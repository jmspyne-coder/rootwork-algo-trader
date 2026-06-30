"""
Morning health-check watchdog — runs ~09:58 ET on an INDEPENDENT clock (a
GitHub Actions cron and/or a Windows task), separate from the trading trigger.

Its whole job is to fire BECAUSE something did not happen: if execute_orb did
not record a run for today after the entry window closed, it raises a loud
"TRIGGER DID NOT FIRE" alarm. A no-signal day (execute ran, found nothing) is
NOT an alarm — that distinction comes from the algo_run_log heartbeat.

Checks, fail-fast:
  1. Market closed today?             -> all clear, no alarm.
  2. Account reachable + equity sane? -> CRITICAL if not.
  3. execute_orb heartbeat present?   -> CRITICAL "TRIGGER DID NOT FIRE" if not.
  4. Classify the outcome for an all-clear, or WARN on a halt/stale-skip/error.
MotherDuck unreadable -> CRITICAL (fail loud, never silent).
"""
import sys
from datetime import datetime
import pytz

from config import settings
from src.notifications import notify_health_alarm
from src.alpaca_client import (
    get_trading_client, get_account_equity, get_market_session_today,
)
from src.trade_logger import get_run_today, get_prior_equity_end


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    today = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    mode = "paper" if settings.ALPACA_PAPER else "live"
    print(f"[HEALTH CHECK] {now.strftime('%Y-%m-%d %H:%M ET')}")

    # 1. Calendar short-circuit — never cry wolf on a weekend/holiday.
    try:
        client = get_trading_client()
        session = get_market_session_today(client)
    except Exception as e:
        notify_health_alarm("CRITICAL", "Account unreachable",
                            f"Health check could not reach Alpaca at {hhmm} ET: {e}. "
                            f"Check API secrets / Alpaca status.")
        sys.exit(0)
    if session is None:
        print("  Market closed today — health check skipped (all clear).")
        sys.exit(0)

    # 2. Account reachable + equity sane.
    try:
        equity = get_account_equity(client)
    except Exception as e:
        notify_health_alarm("CRITICAL", "Account equity unreadable",
                            f"Could not read account equity at {hhmm} ET: {e}.")
        sys.exit(0)
    try:
        prior = get_prior_equity_end(today, mode)
    except Exception:
        prior = None
    if equity <= 0 or (prior and (equity < prior * 0.5 or equity > prior * 5)):
        notify_health_alarm("CRITICAL", "Account equity looks wrong",
                            f"Equity ${equity:,.2f} at {hhmm} ET"
                            + (f" vs prior close ${prior:,.2f}." if prior else "."))

    # 3. Did execute_orb actually run today? The core watchdog check.
    try:
        run = get_run_today("execute_orb", mode, today)
    except Exception as e:
        notify_health_alarm("CRITICAL", "Cannot read run log (MotherDuck down?)",
                            f"Could not read algo_run_log at {hhmm} ET: {e}. "
                            f"Cannot confirm the bot ran — treat as suspect.")
        sys.exit(0)

    if run is None:
        notify_health_alarm(
            "CRITICAL", "TRIGGER DID NOT FIRE",
            f"No execute_orb run recorded for {today} by {hhmm} ET, and the market is "
            f"open today. The bot did not run this morning. Check, in order: (1) the "
            f"trigger machine is on and awake (Windows task / Mac launchd), (2) GitHub "
            f"Actions is enabled, (3) repo secrets. Account equity ${equity:,.2f}.")
        print("  ALARM: no execute_orb heartbeat today.")
        sys.exit(0)

    # 4. Classify the recorded outcome.
    outcome = (run.get("outcome") or "").lower()
    ran_at = run.get("et_hhmm") or "?"
    detail = run.get("detail") or ""
    benign = {"entered", "no_signal", "no_data", "dry_run", "skipped_halfday", "closed_market"}
    if outcome == "entered":
        print(f"  OK — execute_orb entered at ET {ran_at}. {detail}")
    elif outcome in benign:
        print(f"  OK — execute_orb ran at ET {ran_at}, outcome '{outcome}' (no trade). {detail}")
    else:  # halted, error, skipped_stale, idem_error, ...
        notify_health_alarm(
            "WARN", f"Bot ran but did not trade cleanly ({outcome})",
            f"execute_orb at ET {ran_at} on {today}: {outcome}. {detail}. "
            f"Equity ${equity:,.2f}. Review whether this was expected "
            f"(a 'skipped_stale' means the trigger fired too late to enter).")
        print(f"  WARN — outcome '{outcome}' at {ran_at}: {detail}")
    print("  Health check complete.")


if __name__ == "__main__":
    main()
