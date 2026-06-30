"""
Pre-Market Setup — Runs at 9:25 AM ET via GitHub Actions.

1. Fetch account equity from Alpaca
2. Calculate ATR for position sizing
3. Reset daily risk state
4. Log starting conditions
"""
import sys
from datetime import datetime
import pytz

from src.alpaca_client import (
    get_trading_client, get_account_equity, fetch_daily_bars, get_data_client,
    get_market_session_today,
)
from src.orb_signal import calculate_atr
from src.risk_manager import load_risk_state, reset_daily_state, save_risk_state, can_trade
from src.notifications import send_notification, notify_risk_halt
from src.trade_logger import log_run
from config import settings


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    print(f"[PRE-MARKET] {now.strftime('%Y-%m-%d %H:%M ET')}")
    from src.timeguard import ensure_et_window
    ensure_et_window("09:15", "09:59", "PRE-MARKET")  # intended 09:25 ET

    # 1. Get current equity
    try:
        trading_client = get_trading_client()
        equity = get_account_equity(trading_client)
        print(f"  Account equity: ${equity:,.2f}")
    except Exception as e:
        print(f"  ERROR fetching account: {e}")
        sys.exit(1)

    # 1a. Market-calendar gate: skip cleanly on weekends/holidays.
    try:
        session = get_market_session_today(trading_client)
    except Exception as e:
        print(f"  Calendar check failed ({e}); proceeding.")
        session = {"date": "unknown"}
    if session is None:
        print("  Market closed today — skipping pre-market.")
        log_run("pre_market", "closed_market", et_hhmm=now.strftime("%H:%M"))
        sys.exit(0)

    # 1b. Safety: clear any stray/leftover open orders before the session
    # (e.g. an after-hours test order Alpaca queued for the open).
    try:
        from src.alpaca_client import cancel_all_orders
        canceled = cancel_all_orders(trading_client)
        if canceled:
            print(f"  Canceled {len(canceled)} stray open order(s).")
    except Exception as e:
        print(f"  Stray-order cancel error (non-fatal): {e}")

    # 2. Calculate ATR per symbol (for the notification; sizing happens at execute)
    from datetime import timedelta
    atrs = {}
    try:
        data_client = get_data_client()
        end = now.strftime("%Y-%m-%d")
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        for tk in settings.TICKERS:
            atrs[tk] = calculate_atr(fetch_daily_bars(tk, start, end, data_client, feed=settings.ALPACA_DATA_FEED), settings.ATR_PERIOD)
    except Exception as e:
        print(f"  ATR calculation error: {e}")
    atr_str = " | ".join(f"{tk} ATR {a:.2f}" if a else f"{tk} ATR N/A" for tk, a in atrs.items()) or "ATR N/A"
    print(f"  {atr_str}")

    # 3. Reset daily risk state
    state = load_risk_state(equity)
    state = reset_daily_state(state, equity)
    save_risk_state(state)

    # 4. Check if we're allowed to trade today
    allowed, reason = can_trade(state)
    if not allowed:
        notify_risk_halt(reason)
        print(f"  HALTED: {reason}")
        log_run("pre_market", "halted", et_hhmm=now.strftime("%H:%M"), detail=reason)
        sys.exit(0)  # exit 0 so Actions doesn't show failure

    # 5. Notify
    send_notification(
        f"*PRE-MARKET* | {now.strftime('%Y-%m-%d')}\n"
        f"Symbols: `{', '.join(settings.TICKERS)}` | Equity: ${equity:,.2f}\n"
        f"{atr_str} | ORB window: {settings.OPENING_RANGE_MINUTES} min\n"
        f"Risk/trade: {settings.RISK_PER_TRADE_PCT:.1%} = ${equity * settings.RISK_PER_TRADE_PCT:,.0f} each",
        ":sunrise:",
    )
    log_run("pre_market", "ok", et_hhmm=now.strftime("%H:%M"), detail=f"equity ${equity:,.0f}")
    print("  Pre-market setup complete.")


if __name__ == "__main__":
    main()
