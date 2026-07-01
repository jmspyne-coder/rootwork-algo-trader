"""
Intraday risk monitor — the daily-loss bumper that acts mid-session.

The rest of the bot only "sees" P&L at entry (~09:40) and at the close (15:45).
Between those, a position is protected only by its own bracket stop. This
monitor runs on a short interval through the trading day and enforces a real
daily-loss ceiling:

  - WARN  at DAILY_LOSS_WARN_PCT (3/4 of the hard stop): email a heads-up only.
  - STOP  at MAX_DAILY_LOSS_PCT: cancel open orders, FLATTEN all positions, and
          halt for the day. Auto-resumes the next trading day.

Fail-SAFE (opposite of the entry path): if equity or the day's baseline cannot
be established, the monitor does NOT flatten (flattening is disruptive and a bad
baseline could trip it wrongly). It alerts instead. The entry path stays fail-
CLOSED; this action path is fail-safe.

Daily P&L baseline is the prior session's close (via load_risk_state), so the
loss is measured against where the account actually started the day.

    python -m src.risk_monitor
"""
import sys
from datetime import datetime

import pytz

from config import settings


def classify(daily_pnl: float, starting_equity: float,
             warn_pct: float, stop_pct: float, floor_pct: float) -> str:
    """Pure decision: 'floor' | 'stop' | 'warn' | 'ok' | 'unknown'.

    'floor' is the catastrophic absolute daily floor (sticky halt, manual resume).
    'stop' is the routine hard daily stop (auto-resumes next day).
    'unknown' when the baseline is unusable (non-positive) — the caller must
    then fail safe and NOT flatten.
    """
    if starting_equity is None or starting_equity <= 0:
        return "unknown"
    loss = (-daily_pnl / starting_equity) if daily_pnl < 0 else 0.0
    if loss >= floor_pct:
        return "floor"
    if loss >= stop_pct:
        return "stop"
    if loss >= warn_pct:
        return "warn"
    return "ok"


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    today = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    mode = "paper" if settings.ALPACA_PAPER else "live"
    print(f"[RISK MONITOR] {now.strftime('%Y-%m-%d %H:%M ET')} | {mode}")

    from src.timeguard import ensure_et_window
    ensure_et_window("10:00", "15:35", "RISK MONITOR")  # scheduled-run DST guard

    from src.alpaca_client import (
        get_trading_client, get_account_equity, get_open_positions,
        close_all_positions, cancel_all_orders, get_market_session_today,
    )
    from src.risk_manager import load_risk_state, save_risk_state
    from src.trade_logger import log_run, get_run_today
    from src.notifications import (
        notify_daily_loss_warning, notify_daily_stop_flattened,
        notify_daily_floor_breach, send_notification,
    )

    def heartbeat(outcome, detail=""):
        try:
            log_run("risk_monitor", outcome, et_hhmm=hhmm, detail=detail, mode=mode)
        except Exception as e:
            print(f"  [run-log] could not write heartbeat ({outcome}): {e}")

    trading_client = get_trading_client()

    # Only monitor on an open session.
    try:
        session = get_market_session_today(trading_client)
    except Exception as e:
        print(f"  Calendar check failed ({e}); proceeding.")
        session = {"date": today}
    if session is None:
        print("  Market closed today — nothing to monitor.")
        heartbeat("closed_market")
        sys.exit(0)

    # If we already stopped the day, stay quiet (positions are already flat).
    prev = None
    try:
        prev = get_run_today("risk_monitor", mode, today)
    except Exception as e:
        print(f"  [run-log] could not read today's monitor state ({e}).")
    if prev and prev.get("outcome") in ("stopped", "floor_breach"):
        print(f"  Already {prev.get('outcome')} today — positions flat, nothing to do.")
        sys.exit(0)

    # Equity + baseline. Fail SAFE on any uncertainty: alert, do not flatten.
    try:
        equity = get_account_equity(trading_client)
    except Exception as e:
        print(f"  ERROR fetching equity: {e}")
        send_notification(f"*RISK MONITOR* could not fetch equity: {e}", ":rotating_light:")
        heartbeat("error", f"equity fetch: {e}")
        sys.exit(1)

    state = load_risk_state(equity)
    if state.halt_reason in ("risk_state_unavailable", "equity_unavailable"):
        print(f"  State unavailable ({state.halt_reason}) — cannot judge P&L, not flattening.")
        send_notification(
            f"*RISK MONITOR* state unavailable ({state.halt_reason}); not acting (fail-safe).",
            ":rotating_light:")
        heartbeat("error", state.halt_reason)
        sys.exit(0)

    starting_equity = state.daily_starting_equity
    daily_pnl = equity - starting_equity
    loss_pct = (-daily_pnl / starting_equity) if (starting_equity and daily_pnl < 0) else 0.0
    decision = classify(daily_pnl, starting_equity, settings.DAILY_LOSS_WARN_PCT,
                        settings.MAX_DAILY_LOSS_PCT, settings.MAX_DAILY_LOSS_ABS_PCT)
    print(f"  Equity {equity:,.2f} vs start {starting_equity:,.2f} | "
          f"P&L ${daily_pnl:+,.2f} ({loss_pct:.2%}) -> {decision}")

    if decision == "unknown":
        print("  Baseline unusable — not acting (fail-safe).")
        send_notification("*RISK MONITOR* no usable daily baseline; not acting.", ":warning:")
        heartbeat("no_baseline", f"start={starting_equity}")
        sys.exit(0)

    if decision in ("stop", "floor"):
        # Cancel working orders (the bracket legs) first, then flatten.
        closed = 0
        try:
            positions = get_open_positions(trading_client)
            closed = len(positions)
        except Exception as e:
            print(f"  Could not list positions ({e}); attempting close anyway.")
        try:
            cancel_all_orders(trading_client)
            close_all_positions(trading_client)
            print(f"  FLATTENED {closed} position(s) and cancelled open orders.")
        except Exception as e:
            print(f"  FLATTEN ERROR: {e}")
            send_notification(f"*RISK MONITOR* flatten FAILED: {e}. CHECK ACCOUNT.", ":rotating_light:")
            heartbeat("flatten_error", str(e))
            sys.exit(1)

        state.daily_pnl = daily_pnl
        state.current_equity = equity
        state.is_halted = True
        if decision == "floor":
            # Catastrophe: STICKY latch, does NOT auto-clear. Manual resume only.
            state.halt_reason = "daily_floor_breach"
            save_risk_state(state)
            notify_daily_floor_breach(daily_pnl, loss_pct, settings.MAX_DAILY_LOSS_ABS_PCT,
                                      equity, closed)
            heartbeat("floor_breach", f"loss {loss_pct:.2%}, flattened {closed}")
        else:
            # Routine hard stop: halt for the day, auto-clears next trading day.
            state.halt_reason = "daily_loss_limit"
            save_risk_state(state)
            notify_daily_stop_flattened(daily_pnl, loss_pct, settings.MAX_DAILY_LOSS_PCT,
                                        equity, closed)
            heartbeat("stopped", f"loss {loss_pct:.2%}, flattened {closed}")
        sys.exit(0)

    if decision == "warn":
        if prev and prev.get("outcome") == "warned":
            print("  Already warned today — staying quiet.")
            sys.exit(0)
        notify_daily_loss_warning(daily_pnl, loss_pct, settings.MAX_DAILY_LOSS_PCT, equity)
        heartbeat("warned", f"loss {loss_pct:.2%}")
        sys.exit(0)

    print("  Within limits — no action.")
    heartbeat("ok", f"pnl ${daily_pnl:+.2f}")


if __name__ == "__main__":
    main()
