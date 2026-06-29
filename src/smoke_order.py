"""
Order-path smoke test (MANUAL ONLY — never scheduled).

Deterministically places a tiny 1-share SPY bracket order, regardless of any
signal, so we can prove the live order path end to end: submit_bracket_order
is accepted by the account, the fill is logged, and end_of_day reconciles it.

The row is tagged strategy='smoke_test' so it is trivial to delete afterward
(DELETE FROM algo_trade_log WHERE strategy='smoke_test'). This must NOT pollute
the real track record — clean it up after verifying.

Usage (workflow_dispatch only): pick `smoke_order`. Then run `end_of_day` to
force-close + reconcile, verify, and delete the smoke rows.
"""
import sys
from datetime import datetime
import pytz

from src.alpaca_client import (
    get_data_client, get_trading_client,
    fetch_intraday_bars, get_account_equity, submit_bracket_order,
)
from src.trade_logger import log_trade, init_tables
from config import settings

TICKER = "SPY"
QTY = 1
BRACKET_PCT = 0.01  # take-profit +1%, stop -1% (comfortably valid, tiny size)


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    today = now.date()
    print(f"[SMOKE ORDER] {now.strftime('%Y-%m-%d %H:%M ET')} — order-path test (1 share {TICKER})")

    trading_client = get_trading_client()
    data_client = get_data_client()
    equity = get_account_equity(trading_client)
    print(f"  Account equity: ${equity:,.2f}")

    bars = fetch_intraday_bars(TICKER, today, data_client=data_client)
    if bars.empty:
        print("  No intraday bars; cannot price the test order. Aborting.")
        sys.exit(1)
    price = float(bars.iloc[-1]["close"])
    tp = round(price * (1 + BRACKET_PCT), 2)
    sl = round(price * (1 - BRACKET_PCT), 2)
    print(f"  Latest {TICKER} ~${price:.2f} | bracket: TP {tp} / SL {sl}")

    try:
        order = submit_bracket_order(TICKER, "buy", QTY, tp, sl, trading_client=trading_client)
        print(f"  ORDER SUBMITTED: {order.id} | status {order.status}")
    except Exception as e:
        print(f"  ORDER FAILED: {e}")
        sys.exit(1)

    # Log it like a real entry so end_of_day reconciliation has a row to resolve.
    try:
        init_tables()
        log_trade(
            trade_date=str(today), ticker=TICKER, direction="long",
            entry_price=price, stop_price=sl, target_price=tp, shares=QTY,
            entry_time=str(now), exit_reason="open", equity_before=equity,
            strategy="smoke_test", mode="paper" if settings.ALPACA_PAPER else "live",
        )
        print("  Logged smoke_test row to algo_trade_log.")
    except Exception as e:
        print(f"  Trade-log error (non-fatal): {e}")

    print("  Next: run end_of_day to force-close + reconcile, verify, then DELETE the smoke_test rows.")


if __name__ == "__main__":
    main()
