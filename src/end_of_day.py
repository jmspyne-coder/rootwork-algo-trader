"""
End of Day Cleanup — Runs at 3:45 PM ET via GitHub Actions.

1. Force-close any open positions
2. Calculate daily P&L
3. Update risk state
4. Log to MotherDuck
5. Send daily summary email + Slack notification
"""
import sys
from datetime import datetime
import pytz

from src.alpaca_client import (
    get_trading_client, get_account_equity,
    get_open_positions, close_all_positions, get_todays_orders,
)
from src.risk_manager import load_risk_state, record_trade_result, save_risk_state
from src.trade_logger import log_daily_summary, init_tables
from src.notifications import (
    notify_daily_summary, notify_trade_exit, send_daily_email,
)
from config import settings


def main():
    et = pytz.timezone("US/Eastern")
    now = datetime.now(et)
    today_str = now.strftime("%Y-%m-%d")
    print(f"[END OF DAY] {now.strftime('%Y-%m-%d %H:%M ET')}")

    trading_client = get_trading_client()

    # 1. Check for open positions and force-close
    positions = get_open_positions(trading_client)
    positions_data = []
    if positions:
        print(f"  Open positions: {len(positions)} — force closing...")
        for pos in positions:
            pnl = float(pos.unrealized_pl)
            print(f"    {pos.symbol}: {pos.qty} shares, unrealized P&L: ${pnl:+,.2f}")
            positions_data.append({
                "symbol": pos.symbol,
                "qty": str(pos.qty),
                "unrealized_pl": pnl,
            })
            notify_trade_exit(
                pos.symbol,
                "long" if int(pos.qty) > 0 else "short",
                pnl,
                "eod_force_close",
                float(pos.market_value),
            )
        close_all_positions(trading_client)
        print("  All positions closed.")
    else:
        print("  No open positions.")

    # 2. Get final equity
    equity = get_account_equity(trading_client)
    print(f"  Final equity: ${equity:,.2f}")

    # 3. Calculate daily stats
    state = load_risk_state(equity)
    daily_pnl = equity - state.daily_starting_equity
    state.current_equity = equity
    if equity > state.peak_equity:
        state.peak_equity = equity

    # Count today's trades
    todays_orders = get_todays_orders(trading_client)
    filled_count = len([o for o in todays_orders if hasattr(o, 'filled_qty') and o.filled_qty])
    wins = 1 if daily_pnl > 0 else 0
    losses = 1 if daily_pnl < 0 else 0

    drawdown = state.current_drawdown_pct

    save_risk_state(state)

    # 4. Log to MotherDuck
    try:
        init_tables()
        log_daily_summary(
            summary_date=today_str,
            ticker=settings.TICKER,
            trades_taken=filled_count,
            wins=wins,
            losses=losses,
            daily_pnl=round(daily_pnl, 2),
            equity_start=state.daily_starting_equity,
            equity_end=equity,
            max_drawdown_pct=round(drawdown, 4),
            consecutive_losses=state.consecutive_losses,
            was_halted=state.is_halted,
            halt_reason=state.halt_reason,
            mode="paper" if settings.ALPACA_PAPER else "live",
        )
        print("  Daily summary logged to MotherDuck.")
    except Exception as e:
        print(f"  MotherDuck logging error: {e}")

    # 4b. Reconcile per-trade outcomes. Entries were logged at fill time with
    # exit_reason='open'; now that the day is flat, pull the fills and write the
    # realized exit (price, P&L, reason) onto each open row. Best-effort.
    try:
        from src.reconcile import reconcile_today
        mode = "paper" if settings.ALPACA_PAPER else "live"
        res = reconcile_today(today_str, settings.TICKER, mode, trading_client)
        print(f"  Reconciled {res['reconciled']}/{res['open_rows']} open trades "
              f"({res['round_trips']} round trips from fills).")
    except Exception as e:
        print(f"  Trade reconciliation error (non-fatal): {e}")

    # 5. Slack notification
    notify_daily_summary(
        today_str,
        filled_count,
        wins,
        losses,
        daily_pnl,
        equity,
        drawdown,
    )

    # 6. Daily email
    send_daily_email(
        date=today_str,
        ticker=settings.TICKER,
        trades_taken=filled_count,
        wins=wins,
        losses=losses,
        daily_pnl=round(daily_pnl, 2),
        equity_start=state.daily_starting_equity,
        equity_end=equity,
        drawdown_pct=drawdown,
        positions_closed=positions_data,
        was_halted=state.is_halted,
        halt_reason=state.halt_reason,
        mode="paper" if settings.ALPACA_PAPER else "live",
    )

    print(f"  Daily P&L: ${daily_pnl:+,.2f}")
    print(f"  Drawdown: {drawdown:.1%}")
    print("  End of day complete.")


if __name__ == "__main__":
    main()
