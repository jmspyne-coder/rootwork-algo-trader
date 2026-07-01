"""
Pre-open failsafe (runs in GitHub Actions ~09:05 ET, before pre_market/execute).

Runs where every credential is valid (Alpaca keys, MotherDuck token, Gmail all
come from repo secrets), verifies the whole chain, and EMAILS a green/red verdict
with ~35 min of lead time before the 09:40 execute — so a dead key or a blocked
state is caught early enough to fix, not discovered at the open.

It only VERIFIES and ALERTS: no order, no state mutation. The scale-anomaly guard
in load_risk_state already auto-heals a contaminated false-halt at runtime, so
this focuses on the things a human must fix (revoked keys, an unexpected halt).

Triggered by the Windows task RootworkAlgo-failsafe (gh workflow_dispatch) and/or
its own CI cron. Exits 0 always (a red verdict is an email, not a failed job).
"""
import sys
from datetime import datetime

import pytz

from config import settings


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    hhmm = now.strftime("%H:%M")
    mode = "paper" if settings.ALPACA_PAPER else "live"
    print(f"[PRE-OPEN FAILSAFE] {now:%Y-%m-%d} {hhmm} ET | {mode}")

    from src.timeguard import ensure_et_window
    ensure_et_window("08:30", "09:39", "PRE-OPEN FAILSAFE")  # scheduled-run DST guard

    from src.alpaca_client import get_trading_client, get_effective_equity, get_market_session_today
    from src.risk_manager import load_risk_state, can_trade
    from src.notifications import send_email, send_notification
    from src.trade_logger import log_run

    reds, greens = [], []

    # Trading day? Skip quietly on weekends/holidays (no noise).
    tc = get_trading_client()
    try:
        session = get_market_session_today(tc)
    except Exception as e:
        session = {"date": str(now.date())}
        reds.append(f"calendar check failed: {e}")
    if session is None:
        print("  Market closed today — failsafe stands down.")
        try:
            log_run("preopen_failsafe", "closed_market", et_hhmm=hhmm, mode=mode)
        except Exception:
            pass
        sys.exit(0)
    if session and session.get("is_half_day"):
        greens.append("half-day session (bot will skip execute by design)")

    # 1. Keys authenticate + effective equity is on the ~$5k scale.
    equity = None
    try:
        equity = get_effective_equity(tc, mode)
        if equity and equity > 0:
            greens.append(f"keys OK, effective equity ${equity:,.2f}")
        else:
            reds.append(f"effective equity non-positive ({equity})")
    except Exception as e:
        reds.append(f"could not fetch equity (keys?): {str(e)[:120]}")

    # 2. Risk state allows trading (guard auto-heals contamination; report if not).
    if equity and equity > 0:
        try:
            state = load_risk_state(equity, mode)
            state.daily_pnl = equity - state.daily_starting_equity
            allowed, reason = can_trade(state)
            if allowed:
                greens.append("risk state clears can_trade (not halted)")
            else:
                reds.append(f"trading blocked by risk state: {reason}")
        except Exception as e:
            reds.append(f"risk state check errored: {str(e)[:120]}")

    verdict = "GREEN" if not reds else "RED"
    print(f"  VERDICT: {verdict}")
    for g in greens:
        print(f"    OK  {g}")
    for r in reds:
        print(f"    XX  {r}")

    if verdict == "GREEN":
        subject = f"✅ Pre-open failsafe GREEN — cleared for the {now:%m/%d} open"
        body_lines = "".join(f"<li>{g}</li>" for g in greens)
        send_email(subject, f"""<div style="font-family:sans-serif;padding:20px;background:#1a1a1a;color:#e5e5e5;border-radius:8px;">
        <h2 style="color:#22c55e;">✅ Cleared for the open</h2>
        <p>All pre-open checks passed ~35 min before the 09:40 execute.</p>
        <ul>{body_lines}</ul>
        <p style="color:#a0a0a0;">Pre-open failsafe · {mode} · {hhmm} ET</p></div>""")
        send_notification(f"*PRE-OPEN GREEN* {now:%m/%d} — cleared for the open.", ":white_check_mark:")
    else:
        subject = f"⛔ Pre-open failsafe RED — FIX BEFORE 09:40 ET ({now:%m/%d})"
        red_lines = "".join(f"<li>{r}</li>" for r in reds)
        send_email(subject, f"""<div style="font-family:sans-serif;padding:20px;background:#1a1a1a;color:#e5e5e5;border-radius:8px;">
        <h2 style="color:#ef4444;">⛔ NOT cleared — action needed before 09:40 ET</h2>
        <p>The pre-open failsafe found blocking issues ~35 min before execute. Fix now:</p>
        <ul style="color:#fca5a5;">{red_lines}</ul>
        <p><strong>Likely fixes:</strong> a keys error means the Alpaca GitHub secrets are
        revoked — regenerate and update both <code>.env</code> and repo secrets. A risk-state
        block means review the halt reason (manual_kill/max_drawdown need a manual resume).</p>
        <p style="color:#a0a0a0;">Pre-open failsafe · {mode} · {hhmm} ET</p></div>""")
        send_notification(f"*PRE-OPEN RED* {now:%m/%d} — FIX BEFORE 09:40: {'; '.join(reds)}", ":rotating_light:")

    try:
        log_run("preopen_failsafe", verdict.lower(), et_hhmm=hhmm, detail="; ".join(reds) or "ok", mode=mode)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
