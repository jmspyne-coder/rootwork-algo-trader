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

from src.alpaca_client import get_trading_client, get_account_equity, fetch_daily_bars, get_data_client
from src.orb_signal import calculate_atr
from src.risk_manager import load_risk_state, reset_daily_state, save_risk_state, can_trade
from src.notifications import send_notification, notify_risk_halt
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

    # 2. Calculate ATR
    try:
        data_client = get_data_client()
        end = now.strftime("%Y-%m-%d")
        start = (now - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")
        daily = fetch_daily_bars(settings.TICKER, start, end, data_client)
        atr = calculate_atr(daily, settings.ATR_PERIOD)
        print(f"  ATR({settings.ATR_PERIOD}): {atr:.4f}" if atr else "  ATR: insufficient data")
    except Exception as e:
        print(f"  ATR calculation error: {e}")
        atr = None

    # 3. Reset daily risk state
    state = load_risk_state(equity)
    state = reset_daily_state(state, equity)
    save_risk_state(state)

    # 4. Check if we're allowed to trade today
    allowed, reason = can_trade(state)
    if not allowed:
        notify_risk_halt(reason)
        print(f"  HALTED: {reason}")
        sys.exit(0)  # exit 0 so Actions doesn't show failure

    # 5. Notify
    send_notification(
        f"*PRE-MARKET* | {now.strftime('%Y-%m-%d')}\n"
        f"Ticker: `{settings.TICKER}` | Equity: ${equity:,.2f}\n"
        f"ATR: {f'{atr:.2f}' if atr else 'N/A'} | ORB window: {settings.OPENING_RANGE_MINUTES} min\n"
        f"Risk/trade: {settings.RISK_PER_TRADE_PCT:.1%} = ${equity * settings.RISK_PER_TRADE_PCT:,.0f}",
        ":sunrise:",
    )
    print("  Pre-market setup complete.")


if __name__ == "__main__":
    main()
