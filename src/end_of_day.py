"""
End of Day Cleanup — Runs ~15:45 ET via the external trigger.

1. Skip cleanly if the market was closed today (no false summary).
2. Force-close any open positions and wait for the fills to settle.
3. Reconcile per-trade outcomes from the day's fills (writes realized exits).
4. Compute the daily summary FROM the reconciled trade log (truthful per-trade
   counts), with the P&L baseline anchored to the prior session's close.
5. Persist the summary, update risk state, send Slack + email, write a heartbeat.

Reconciliation runs BEFORE the summary is computed, so the numbers reflect real
round trips, not Alpaca order legs.
"""
import sys
import time
from datetime import datetime
import pytz

from src.alpaca_client import (
    get_trading_client, get_account_equity,
    get_open_positions, close_all_positions, get_market_session_today,
)
from src.risk_manager import load_risk_state, save_risk_state
from src.trade_logger import (
    log_daily_summary, init_tables, log_run,
    get_prior_equity_end, get_daily_trade_stats, get_todays_trades,
)
from src.notifications import (
    notify_daily_summary, notify_trade_exit, send_daily_email, send_notification,
)
from config import settings


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    today_str = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    mode = "paper" if settings.ALPACA_PAPER else "live"
    print(f"[END OF DAY] {now.strftime('%Y-%m-%d %H:%M ET')}")
    from src.timeguard import ensure_et_window
    ensure_et_window("15:41", "16:14", "END OF DAY")

    def heartbeat(outcome, detail=""):
        try:
            log_run("end_of_day", outcome, et_hhmm=hhmm, detail=detail)
        except Exception as e:
            print(f"  [run-log] could not write heartbeat ({outcome}): {e}")

    trading_client = get_trading_client()

    # Skip cleanly on a non-trading day — never write a false flat summary that
    # would corrupt tomorrow's baseline/streak. Fail open on a calendar blip.
    try:
        session = get_market_session_today(trading_client)
    except Exception as e:
        print(f"  Calendar check failed ({e}); proceeding.")
        session = {"date": today_str}
    if session is None:
        print("  Market closed today — nothing to settle.")
        heartbeat("closed_market", "market closed today")
        sys.exit(0)

    # 1. Force-close any open positions.
    positions = get_open_positions(trading_client)
    if positions:
        print(f"  Open positions: {len(positions)} — force closing...")
        for pos in positions:
            pnl = float(pos.unrealized_pl)
            print(f"    {pos.symbol}: {pos.qty} shares, unrealized P&L: ${pnl:+,.2f}")
            notify_trade_exit(pos.symbol, "long" if int(pos.qty) > 0 else "short",
                              pnl, "eod_force_close", float(pos.market_value))
        close_all_positions(trading_client)
        print("  All positions closed.")
        # Let the closing fills settle before reconciliation tries to pair them.
        for _ in range(15):
            time.sleep(2)
            if not get_open_positions(trading_client):
                break
        time.sleep(3)
    else:
        print("  No open positions.")

    # 2. Final equity.
    try:
        equity = get_account_equity(trading_client)
    except Exception as e:
        print(f"  ERROR fetching equity: {e}")
        send_notification(f"*EOD ERROR* could not fetch equity: {e}", ":rotating_light:")
        heartbeat("error", f"equity fetch: {e}")
        sys.exit(1)
    print(f"  Final equity: ${equity:,.2f}")

    # 3. Risk state. If state/equity is unavailable, do NOT write a false flat
    # summary (it would erase a real streak and corrupt tomorrow's baseline).
    state = load_risk_state(equity)
    if state.halt_reason in ("risk_state_unavailable", "equity_unavailable"):
        print(f"  State unavailable ({state.halt_reason}) — skipping summary write.")
        send_notification(
            f"*EOD WARNING* state unavailable ({state.halt_reason}); daily summary NOT written.",
            ":rotating_light:")
        heartbeat("error", state.halt_reason)
        sys.exit(0)

    # 4. Reconcile per-trade outcomes FIRST (writes realized exits onto open rows),
    # so the summary below reflects real round trips, not Alpaca order legs.
    try:
        init_tables()
        from src.reconcile import reconcile_today
        for tk in settings.TICKERS:
            res = reconcile_today(today_str, tk, mode, trading_client)
            print(f"  [{tk}] reconciled {res['reconciled']}/{res['open_rows']} "
                  f"({res['round_trips']} round trips).")
    except Exception as e:
        print(f"  Trade reconciliation error (non-fatal): {e}")

    # 5. Daily stats FROM the reconciled trade log. P&L baseline = prior session
    # close, independent of whether pre_market ran.
    stats = get_daily_trade_stats(today_str, mode)
    trades_taken, wins, losses = stats["trades"], stats["wins"], stats["losses"]
    realized_pnl = stats["realized_pnl"]

    prior_end = get_prior_equity_end(today_str, mode)
    starting_equity = prior_end if prior_end is not None else (state.daily_starting_equity or equity)
    daily_pnl = equity - starting_equity
    if abs(daily_pnl - realized_pnl) > max(1.0, 0.0005 * equity):
        print(f"  NOTE: account P&L ${daily_pnl:+,.2f} vs realized round-trip "
              f"${realized_pnl:+,.2f} diverge (unreconciled fills, fees, or deposits?).")

    # Update + persist risk state.
    state.current_equity = equity
    state.daily_starting_equity = starting_equity
    state.daily_pnl = daily_pnl
    if equity > state.peak_equity:
        state.peak_equity = equity
    drawdown = state.current_drawdown_pct
    save_risk_state(state)

    # 6. Write the summary (always on a trading day, so the baseline chain holds).
    try:
        log_daily_summary(
            summary_date=today_str, ticker=",".join(settings.TICKERS),
            trades_taken=trades_taken, wins=wins, losses=losses,
            daily_pnl=round(daily_pnl, 2), equity_start=starting_equity, equity_end=equity,
            max_drawdown_pct=round(drawdown, 4), consecutive_losses=state.consecutive_losses,
            was_halted=state.is_halted, halt_reason=state.halt_reason,
            strategy="orb_v2", mode=mode,
        )
        print("  Daily summary logged to MotherDuck.")
    except Exception as e:
        print(f"  MotherDuck summary write error: {e}")
        send_notification(f"*EOD WARNING* summary write failed: {e}", ":rotating_light:")

    # 7. Notify + email (email shows reconciled round trips, not just force-closed).
    trades = get_todays_trades(today_str, mode)
    notify_daily_summary(today_str, trades_taken, wins, losses, daily_pnl, equity, drawdown)
    send_daily_email(
        date=today_str, ticker=",".join(settings.TICKERS),
        trades_taken=trades_taken, wins=wins, losses=losses,
        daily_pnl=round(daily_pnl, 2), equity_start=starting_equity, equity_end=equity,
        drawdown_pct=drawdown, trades=trades,
        was_halted=state.is_halted, halt_reason=state.halt_reason, mode=mode,
    )

    heartbeat("ok", f"{trades_taken} trades, P&L ${daily_pnl:+.2f}")
    print(f"  Daily P&L: ${daily_pnl:+,.2f} | Drawdown: {drawdown:.1%}")

    # Shadow A/B: record what each variant (per ticker, regime gate off/on) would
    # have done today on the real data — a live, zero-risk tandem comparison.
    # Best-effort: never let it affect the real EOD.
    try:
        from src.shadow import run_shadow
        n = run_shadow(today_str, mode=mode)
        print(f"  Shadow A/B: logged {n} variant row(s).")
    except Exception as e:
        print(f"  Shadow A/B logging failed (non-fatal): {e}")

    print("  End of day complete.")


if __name__ == "__main__":
    main()
