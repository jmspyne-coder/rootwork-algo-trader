"""
ORB Execution — Runs ~9:40 AM ET via an external trigger (Windows task / Mac).

Loops over the configured symbols (settings.TICKERS, e.g. SPY + QQQ). For each:
  1. Pull today's intraday bars (the still-forming bar is dropped)
  2. Compute opening range + breakout signal (v1 + candle filter)
  3. Size the position (risk % of equity, capital split across symbols)
  4. Submit a bracket order (entry + stop + target) and log it

Guard order: scheduled DST guard -> wall-clock entry window -> market-calendar
gate (skip holidays/half-days) -> account risk pre-check -> per-symbol
idempotency + data/signal freshness. A deterministic client_order_id makes a
re-run (or a second trigger) idempotent at the broker. Every run writes a
heartbeat to algo_run_log so a no-signal day is distinguishable from a never-ran
day (the silent failure the health check watches for).
"""
import sys
from datetime import datetime, timedelta
import pandas as pd
import pytz

from src.alpaca_client import (
    get_data_client, get_trading_client,
    fetch_intraday_bars, fetch_daily_bars,
    get_account_equity, submit_bracket_order,
    has_order_today, count_todays_orders, get_market_session_today,
)
from src.orb_signal import generate_signal, calculate_atr
from src.risk_manager import (
    load_risk_state, can_trade, calculate_position_size, save_risk_state,
)
from src.trade_logger import log_trade, init_tables, log_run
from src.notifications import (
    notify_trade_entry, notify_no_signal, notify_risk_halt, send_notification,
)
from config import settings


def _drop_forming_bar(intraday: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop the still-forming current-minute bar so a partial candle (whose OHLC
    is not final) can never be the breakout / candle-strength bar."""
    if intraday is None or intraday.empty:
        return intraday
    cutoff = now.replace(second=0, microsecond=0)
    return intraday[intraday.index < cutoff]


def _age_min(ts, now: datetime) -> float | None:
    """Age in minutes of an ET timestamp (str or Timestamp) relative to now."""
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("US/Eastern")
        return (pd.Timestamp(now) - t).total_seconds() / 60.0
    except Exception:
        return None


def trade_symbol(ticker, equity, capital_cap, data_client, trading_client, today, now) -> dict:
    """Run the ORB flow for one symbol. Returns {placed, status, detail}."""
    bypass = settings.DRY_RUN or settings.FORCE_ENTRY

    # Idempotency: any non-cancelled order today means we already acted. Fail
    # CLOSED if the check itself errors — never risk a double entry on a blip.
    try:
        if has_order_today(ticker, trading_client):
            print(f"  [{ticker}] already has an order today — skipping (re-run guard).")
            return {"placed": 0, "status": "already", "detail": "order exists today"}
    except Exception as e:
        print(f"  [{ticker}] idempotency check failed — SKIPPING (fail-closed): {e}")
        return {"placed": 0, "status": "idem_error", "detail": str(e)}

    # Fetch today's intraday bars, then drop the still-forming bar.
    try:
        intraday = fetch_intraday_bars(ticker, today, data_client=data_client)
    except Exception as e:
        print(f"  [{ticker}] ERROR fetching intraday: {e}")
        return {"placed": 0, "status": "no_data", "detail": f"fetch error: {e}"}
    intraday = _drop_forming_bar(intraday, now)
    if intraday is None or intraday.empty:
        notify_no_signal(ticker, str(today), "No intraday data available yet")
        print(f"  [{ticker}] no intraday data.")
        return {"placed": 0, "status": "no_data", "detail": "empty intraday"}

    # Data freshness: reject a stale/lagged frame (e.g. prior-session bars on a
    # holiday, or a badly delayed feed) instead of trading on it.
    if not bypass:
        age = _age_min(intraday.index[-1], now)
        if age is not None and age > settings.DATA_MAX_AGE_MIN:
            notify_no_signal(ticker, str(today), f"data stale ({age:.0f} min old) — skipping")
            print(f"  [{ticker}] data stale (latest bar {age:.0f} min old) — skipping.")
            return {"placed": 0, "status": "stale_data", "detail": f"{age:.0f}min old"}

    # ATR for the stop. Exclude today's partial daily bar (would deflate ATR ->
    # too-tight stop -> oversize) and use SIP so it matches the backtest's ATR.
    try:
        start_daily = (now - timedelta(days=40)).strftime("%Y-%m-%d")
        end_daily = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        daily = fetch_daily_bars(ticker, start_daily, end_daily, data_client, feed="sip")
        atr = calculate_atr(daily, settings.ATR_PERIOD)
    except Exception as e:
        print(f"  [{ticker}] ATR fetch failed: {e}")
        atr = None
    if atr is None:
        print(f"  [{ticker}] WARNING: ATR unavailable — stop falls back to OR midline (not the validated ATR stop).")

    # Signal (v1 + candle filter via config defaults).
    signal = generate_signal(intraday, atr=atr)
    if signal is None:
        notify_no_signal(ticker, str(today))
        print(f"  [{ticker}] no ORB signal.")
        return {"placed": 0, "status": "no_signal", "detail": ""}

    # Signal freshness: only enter if the breakout bar is recent. Immune to WHY
    # a run is late and to data lag — this is the real anti-chase guard.
    if not bypass:
        age = _age_min(signal.timestamp, now)
        if age is not None and age > settings.SIGNAL_MAX_AGE_MIN:
            print(f"  [{ticker}] breakout bar {age:.0f} min old (> {settings.SIGNAL_MAX_AGE_MIN}) — skipping stale signal.")
            send_notification(f"*SKIPPED (stale signal)* `{ticker}` breakout {age:.0f}m old; not chasing.", ":zzz:")
            return {"placed": 0, "status": "skipped_stale", "detail": f"signal {age:.0f}min old"}

    # Size (risk % of equity, capped at this symbol's share of capital). Surface
    # when the capital cap binds — that means the position is under-risked vs the
    # validated (uncapped) backtest sizing.
    risk_shares = calculate_position_size(equity, signal.entry_price, signal.stop_price)
    shares = calculate_position_size(equity, signal.entry_price, signal.stop_price, capital_cap=capital_cap)
    if shares <= 0:
        notify_no_signal(ticker, str(today), "Position size = 0 (stop too wide or capital too low)")
        print(f"  [{ticker}] position size 0 — skipping.")
        return {"placed": 0, "status": "size_zero", "detail": ""}
    if shares < risk_shares:
        print(f"  [{ticker}] NOTE: capital cap binds — {shares}sh vs risk-sized {risk_shares}sh (under-risked vs backtest).")

    print(f"  [{ticker}] {signal.direction.upper()} {shares}sh @ ${signal.entry_price:.2f} | "
          f"stop ${signal.stop_price:.2f} | tgt ${signal.target_price:.2f} | "
          f"notional ${shares * signal.entry_price:,.0f}")

    # Dry run: prove the full path without touching the account.
    if settings.DRY_RUN:
        print(f"  [{ticker}] DRY RUN — would {'BUY' if signal.direction == 'long' else 'SELL'} "
              f"{shares} sh. No order placed, nothing logged.")
        return {"placed": 0, "status": "dry_run", "detail": f"{shares}sh @ {signal.entry_price:.2f}"}

    # Submit the bracket with a deterministic client_order_id so a re-run or a
    # second trigger cannot double-enter (the broker rejects the duplicate id).
    coid = f"orb-{ticker}-{today}"
    try:
        order = submit_bracket_order(
            ticker=ticker,
            side="buy" if signal.direction == "long" else "sell",
            qty=shares,
            take_profit_price=signal.target_price,
            stop_loss_price=signal.stop_price,
            trading_client=trading_client,
            client_order_id=coid,
        )
        print(f"  [{ticker}] order {order.id} ({order.status})")
        notify_trade_entry(ticker, signal.direction, shares,
                           signal.entry_price, signal.stop_price, signal.target_price)
    except Exception as e:
        msg = str(e)
        if "client_order_id" in msg.lower() or "duplicate" in msg.lower():
            print(f"  [{ticker}] duplicate order id ({coid}) — already entered today, skipping.")
            return {"placed": 0, "status": "already", "detail": "duplicate client_order_id"}
        print(f"  [{ticker}] ORDER ERROR: {e}")
        send_notification(f"*ORDER ERROR* `{ticker}`: {e}", ":rotating_light:")
        return {"placed": 0, "status": "order_error", "detail": msg}

    # Log the entry. If this fails the order is still live — alert loudly so the
    # book gets reconciled by hand rather than silently desyncing.
    try:
        log_trade(
            trade_date=str(today), ticker=ticker, direction=signal.direction,
            entry_price=signal.entry_price, stop_price=signal.stop_price,
            target_price=signal.target_price, shares=shares, entry_time=signal.timestamp,
            exit_reason="open", or_high=signal.or_high, or_low=signal.or_low,
            range_pct=signal.range_pct, atr=signal.atr, equity_before=equity,
            vwap_at_entry=signal.vwap_at_entry, rvol_at_entry=signal.rvol_at_entry,
            candle_strength=signal.candle_strength, filters_passed=signal.filters_passed,
            strategy="orb_v2", mode="paper" if settings.ALPACA_PAPER else "live",
        )
        print(f"  [{ticker}] logged to algo_trade_log.")
    except Exception as e:
        print(f"  [{ticker}] TRADE-LOG ERROR (order IS live): {e}")
        send_notification(
            f"*LOG FAILURE* `{ticker}` order placed (coid {coid}) but NOT logged: {e}. "
            f"Reconcile manually — the book is out of sync.", ":rotating_light:")
    return {"placed": 1, "status": "entered", "detail": f"{shares}sh @ {signal.entry_price:.2f}"}


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    today = now.date()
    tickers = settings.TICKERS
    hhmm = now.strftime("%H:%M")
    print(f"[EXECUTE ORB] {now.strftime('%Y-%m-%d %H:%M ET')} | symbols: {', '.join(tickers)}")

    from src.timeguard import ensure_et_window
    ensure_et_window("09:36", "10:14", "EXECUTE ORB")  # scheduled-run DST guard only

    def heartbeat(outcome, detail=""):
        try:
            log_run("execute_orb", outcome, et_hhmm=hhmm, detail=detail)
        except Exception as e:
            print(f"  [run-log] could not write heartbeat ({outcome}): {e}")

    # Wall-clock freshness backstop — applies to ALL triggers. A late/stale run
    # skips (and SAYS SO) rather than chasing. DRY_RUN/FORCE_ENTRY override.
    if (not settings.DRY_RUN and not settings.FORCE_ENTRY
            and not (settings.ENTRY_WINDOW_START <= hhmm <= settings.ENTRY_WINDOW_END)):
        print(f"  ET {hhmm} outside entry window {settings.ENTRY_WINDOW_START}-"
              f"{settings.ENTRY_WINDOW_END} — skipping (stale run).")
        send_notification(
            f"*SKIPPED (late run)* execute_orb ran at ET {hhmm}, outside "
            f"{settings.ENTRY_WINDOW_START}-{settings.ENTRY_WINDOW_END}. No trade.", ":zzz:")
        heartbeat("skipped_stale", f"run at {hhmm} outside window")
        sys.exit(0)

    trading_client = get_trading_client()

    # Market-calendar gate: never trade a holiday/half-day. Fail OPEN on a
    # calendar API error (the data-freshness guard still blocks stale holiday data).
    try:
        session = get_market_session_today(trading_client)
    except Exception as e:
        print(f"  Calendar check failed ({e}); proceeding — data-freshness guard still applies.")
        session = {"date": str(today), "close": "16:00", "is_half_day": False}
    if session is None:
        print("  Market closed today (weekend/holiday) — no trading.")
        heartbeat("closed_market", "market closed today")
        sys.exit(0)
    if session.get("is_half_day"):
        print(f"  Half-day session (close {session['close']}) — skipping to stay in-distribution with the backtest.")
        send_notification(f"*SKIPPED* half-day session (close {session['close']}). No trade.", ":calendar:")
        heartbeat("skipped_halfday", f"half-day close {session['close']}")
        sys.exit(0)

    # Equity + account-level risk pre-check.
    try:
        equity = get_account_equity(trading_client)
    except Exception as e:
        print(f"  ERROR fetching equity: {e}")
        send_notification(f"*EXECUTE ERROR* could not fetch account equity: {e}", ":rotating_light:")
        heartbeat("error", f"equity fetch: {e}")
        sys.exit(1)

    state = load_risk_state(equity)
    # Live daily P&L vs the prior-close baseline (the cached value is stale; this
    # is what makes the daily-loss halt actually able to fire intraday).
    state.daily_pnl = equity - state.daily_starting_equity
    allowed, reason = can_trade(state)
    if not allowed:
        notify_risk_halt(reason)
        print(f"  HALTED: {reason}")
        heartbeat("halted", reason)
        sys.exit(0)

    init_tables()  # once, off the per-symbol hot path
    data_client = get_data_client()
    capital_cap = equity / max(len(tickers), 1)

    # Account-wide trade cap from BROKER truth (robust to un-persisted state and
    # to two triggers both firing), falling back to the cached counter.
    try:
        existing = count_todays_orders(trading_client)
    except Exception as e:
        print(f"  trade-count check failed ({e}); using cached counter {state.trades_today}.")
        existing = state.trades_today

    placed = 0
    results = []
    for tk in tickers:
        if existing + placed >= settings.MAX_TRADES_PER_DAY:
            print(f"  Account trade cap reached ({settings.MAX_TRADES_PER_DAY}) — stopping.")
            break
        # Re-check halts each iteration (a halt can engage between symbols).
        allowed, reason = can_trade(state)
        if not allowed:
            print(f"  Risk halt mid-loop: {reason} — stopping.")
            notify_risk_halt(reason)
            break
        res = trade_symbol(tk, equity, capital_cap, data_client, trading_client, today, now)
        results.append((tk, res))
        placed += res["placed"]

    state.trades_today = existing + placed
    save_risk_state(state)  # unconditional — keep the counter honest

    statuses = [r["status"] for _, r in results]
    if placed > 0:
        outcome = "entered"
    elif "dry_run" in statuses:
        outcome = "dry_run"
    elif "skipped_stale" in statuses or "stale_data" in statuses:
        outcome = "skipped_stale"
    elif any(s in ("order_error", "idem_error") for s in statuses):
        outcome = "error"
    elif "no_data" in statuses:
        outcome = "no_data"
    else:
        outcome = "no_signal"
    detail = "; ".join(
        f"{tk}:{r['status']}" + (f"({r['detail']})" if r["detail"] else "")
        for tk, r in results
    )
    heartbeat(outcome, detail)
    print(f"  Done — {placed} order(s) placed across {len(tickers)} symbol(s). [{outcome}]")


if __name__ == "__main__":
    main()
