"""
Manual kill switch — the human-in-the-loop stop.

Two operator actions, run by hand (GitHub Actions workflow_dispatch or locally):

    python -m src.killswitch halt      # stop all trading until re-authorized
    python -m src.killswitch resume    # re-authorize trading

`halt` sets a STICKY manual halt in the risk-state cache (halt_reason
"manual_kill"). Unlike the daily-loss stop, it does NOT auto-clear overnight
(see src/risk_manager.load_risk_state), so nothing trades on any day until
`resume` clears it. execute_orb's can_trade() refuses to trade while it is set.

`resume` clears ONLY the manual halt. A daily-loss or max-drawdown halt is left
untouched (those clear on their own schedule / after review).
"""
import sys

from config import settings
from src.risk_manager import load_risk_state, save_risk_state
from src.notifications import notify_manual_halt, notify_manual_resume, send_notification


def _equity_or_none():
    try:
        from src.alpaca_client import get_account_equity
        return get_account_equity()
    except Exception as e:
        print(f"  [killswitch] could not fetch equity ({e}).")
        return None


def halt():
    """Engage the sticky manual halt."""
    equity = _equity_or_none()
    # load_risk_state fails closed (is_halted) when equity is unknown; pass a
    # positive placeholder so we can still WRITE the manual halt, but prefer the
    # real equity when we have it.
    state = load_risk_state(equity if equity and equity > 0 else 1.0)
    state.is_halted = True
    state.halt_reason = "manual_kill"
    if equity and equity > 0:
        state.current_equity = equity
    save_risk_state(state)
    print("  [killswitch] MANUAL HALT engaged (sticky). Trading stays stopped until resume.")
    notify_manual_halt(equity or 0.0)


# Sticky halts the operator clears by explicitly re-authorizing (this action).
_OPERATOR_CLEARABLE = ("manual_kill", "daily_floor_breach")


def resume():
    """Clear an operator-review halt (manual kill or catastrophic daily-floor
    breach). Leaves other halts (routine daily loss, max drawdown) alone."""
    equity = _equity_or_none()
    state = load_risk_state(equity if equity and equity > 0 else 1.0)
    if state.is_halted and state.halt_reason in _OPERATOR_CLEARABLE:
        cleared = state.halt_reason
        state.is_halted = False
        state.halt_reason = None
        save_risk_state(state)
        print(f"  [killswitch] Cleared '{cleared}' halt. Trading re-authorized.")
        notify_manual_resume(equity or 0.0)
    elif state.is_halted:
        # Halted for some OTHER reason — do not silently override it.
        print(f"  [killswitch] Halt reason '{state.halt_reason}' is not operator-clearable. Left in place.")
        send_notification(
            f"*RESUME IGNORED* halt reason is '{state.halt_reason}'. Not overriding "
            f"a routine daily-loss or drawdown halt (those clear on their own).", ":warning:")
    else:
        print("  [killswitch] Nothing to resume — trading is not halted.")
        send_notification("*RESUME* trading was not halted; nothing to do.", ":information_source:")


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if action in ("halt", "stop", "halt_now", "kill"):
        halt()
    elif action in ("resume", "arm", "authorize", "resume_trading"):
        resume()
    else:
        print("Usage: python -m src.killswitch [halt|resume]")
        sys.exit(2)


if __name__ == "__main__":
    main()
