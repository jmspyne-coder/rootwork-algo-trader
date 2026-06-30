"""
ORB Backtesting Engine.

Runs the full strategy over historical data with risk controls applied.
Can be run locally or via GitHub Actions.

Usage:
    python -m src.backtest --ticker TQQQ --start 2024-01-01 --end 2026-06-01
    python -m src.backtest --ticker TQQQ --start 2024-01-01 --end 2026-06-01 --or-minutes 15
"""
import argparse
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from src.orb_signal import generate_signal, simulate_trade, calculate_atr
from src.risk_manager import simulate_risk_controls
from src.alpaca_client import get_data_client, fetch_multi_day_intraday, fetch_daily_bars
from config import settings


def run_backtest(
    ticker: str,
    start: str,
    end: str,
    initial_capital: float = None,
    or_minutes: int = None,
    rr_ratio: float = None,
    stop_mode: str = None,
    filter_vwap: bool = None,
    filter_rvol: bool = None,
    rvol_threshold: float = None,
    filter_candle: bool = None,
    candle_pct: float = None,
    entry_cutoff: str = None,
    capital_cap_frac: float = None,
    filter_regime: bool = None,
    regime_gap_max: float = None,
    breakout_confirm: str = None,
) -> dict:
    """
    Full backtest pipeline:
    1. Fetch intraday + daily data from Alpaca
    2. Run ORB signal generation per day
    3. Simulate trades
    4. Apply risk controls
    5. Return performance summary

    Filter args (filter_vwap/filter_rvol/filter_candle and their params) are
    passed straight through to generate_signal(); None means "use config
    default". Set all three to False to reproduce v1 (baseline) behavior.
    """
    initial_capital = initial_capital or settings.BACKTEST_INITIAL_CAPITAL
    or_minutes = or_minutes or settings.OPENING_RANGE_MINUTES
    rr_ratio = rr_ratio or settings.REWARD_RISK_RATIO
    stop_mode = stop_mode or settings.STOP_MODE
    entry_cutoff = entry_cutoff if entry_cutoff is not None else settings.BACKTEST_ENTRY_CUTOFF
    capital_cap_frac = (capital_cap_frac if capital_cap_frac is not None
                        else settings.BACKTEST_CAPITAL_CAP_FRAC)

    print(f"Backtesting {ticker} from {start} to {end}")
    print(f"  ORB window: {or_minutes} min | R:R = {rr_ratio} | Stop: {stop_mode}")
    print(f"  Filters: vwap={filter_vwap} rvol={filter_rvol} candle={filter_candle}")
    print(f"  Live-fidelity: first breakout by {entry_cutoff} ET | notional cap {capital_cap_frac:.0%} equity/position")
    print(f"  Initial capital: ${initial_capital:,.0f}")
    print(f"  Fetching data from Alpaca...")

    data_client = get_data_client()

    # Fetch daily bars for ATR
    daily_start = (datetime.fromisoformat(start) - timedelta(days=30)).strftime("%Y-%m-%d")
    daily_bars = fetch_daily_bars(ticker, daily_start, end, data_client)
    print(f"  Daily bars: {len(daily_bars)} rows")

    # Fetch intraday bars
    intraday_bars = fetch_multi_day_intraday(ticker, start, end, data_client)
    print(f"  Intraday bars: {len(intraday_bars)} rows")

    if intraday_bars.empty:
        print("  ERROR: No intraday data returned.")
        return {"error": "No data"}

    # Group by trading day
    intraday_bars["date"] = intraday_bars.index.date
    trading_days = sorted(intraday_bars["date"].unique())
    print(f"  Trading days: {len(trading_days)}")

    # Run strategy per day
    raw_trades = []
    for day in trading_days:
        day_bars = intraday_bars[intraday_bars["date"] == day].copy()
        day_bars = day_bars.drop(columns=["date"])

        # Calculate ATR + prior close from daily bars strictly before this day.
        daily_up_to = daily_bars[daily_bars.index.date < day]
        atr = calculate_atr(daily_up_to, settings.ATR_PERIOD)
        prev_close = float(daily_up_to.iloc[-1]["close"]) if len(daily_up_to) else None

        # Generate signal
        signal = generate_signal(
            day_bars,
            atr=atr,
            or_minutes=or_minutes,
            rr_ratio=rr_ratio,
            stop_mode=stop_mode,
            filter_vwap=filter_vwap,
            filter_rvol=filter_rvol,
            rvol_threshold=rvol_threshold,
            filter_candle=filter_candle,
            candle_pct=candle_pct,
            entry_cutoff=entry_cutoff,
            prev_close=prev_close,
            filter_regime=filter_regime,
            regime_gap_max=regime_gap_max,
            breakout_confirm=breakout_confirm,
        )
        if signal is None:
            continue

        # Simulate trade outcome
        result = simulate_trade(signal, day_bars)
        result["date"] = str(day)
        raw_trades.append(result)

    print(f"  Raw signals: {len(raw_trades)}")

    if not raw_trades:
        print("  No trades generated.")
        return {"error": "No trades"}

    # Apply risk controls (live-identical capital-capped sizing)
    executed_trades = simulate_risk_controls(raw_trades, initial_capital, capital_cap_frac=capital_cap_frac)
    print(f"  Executed (post-risk): {len(executed_trades)}")

    # Calculate performance
    summary = calculate_performance(executed_trades, initial_capital, trading_days)
    summary["parameters"] = {
        "ticker": ticker,
        "start": start,
        "end": end,
        "or_minutes": or_minutes,
        "rr_ratio": rr_ratio,
        "stop_mode": stop_mode,
        "initial_capital": initial_capital,
        "filter_vwap": filter_vwap,
        "filter_rvol": filter_rvol,
        "rvol_threshold": rvol_threshold,
        "filter_candle": filter_candle,
        "candle_pct": candle_pct,
        "entry_cutoff": entry_cutoff,
        "capital_cap_frac": capital_cap_frac,
        "filter_regime": filter_regime,
        "regime_gap_max": regime_gap_max,
        "breakout_confirm": breakout_confirm,
    }
    summary["trades"] = executed_trades

    return summary


def _curve_stats(pnls: list, dates: list, initial_capital: float) -> dict:
    """Performance bundle for one chronological P&L series.

    Rebuilds the equity curve from this series so max drawdown is measured
    on the same series (gross or net), not borrowed from the other.
    """
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    scratches = [p for p in pnls if p == 0]

    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    total_return = (equity - initial_capital) / initial_capital if initial_capital else 0

    daily_pnl = {}
    for d, p in zip(dates, pnls):
        daily_pnl[d] = daily_pnl.get(d, 0) + p
    daily_returns = list(daily_pnl.values())
    if len(daily_returns) > 1 and np.std(daily_returns) > 0:
        sharpe = (np.mean(daily_returns) / np.std(daily_returns)) * np.sqrt(252)
    else:
        sharpe = 0

    return {
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        "total_pnl": round(sum(pnls), 2),
        "total_return": round(total_return, 4),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else float("inf"),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 4),
        "final_equity": round(equity, 2),
    }


def calculate_performance(trades: list, initial_capital: float, trading_days: list) -> dict:
    """Summary statistics, reported net of costs (primary) and gross.

    Net is the honest headline: it is what the equity curve actually did
    after slippage, spread, and commission. Gross is kept alongside so the
    cost drag is visible. With costs disabled the two are identical.
    """
    if not trades:
        return {"error": "No trades to analyze"}

    dates = [t.get("date", t.get("entry_time", "")[:10]) for t in trades]
    net_pnls = [t["trade_pnl"] for t in trades]
    # gross_pnl/cost exist when costs are wired in; fall back gracefully.
    gross_pnls = [t.get("gross_pnl", t["trade_pnl"]) for t in trades]
    costs = [t.get("cost", 0.0) for t in trades]

    net = _curve_stats(net_pnls, dates, initial_capital)
    gross = _curve_stats(gross_pnls, dates, initial_capital)

    exit_reasons = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    total_costs = round(sum(costs), 2)
    cost_drag = round(total_costs / gross["total_pnl"], 4) if gross["total_pnl"] else None

    return {
        "total_trades": len(net_pnls),
        # primary metrics, net of costs
        "wins": net["wins"],
        "losses": net["losses"],
        "scratches": net["scratches"],
        "win_rate": net["win_rate"],
        "total_pnl": net["total_pnl"],
        "total_return": net["total_return"],
        "avg_win": net["avg_win"],
        "avg_loss": net["avg_loss"],
        "profit_factor": net["profit_factor"],
        "sharpe_ratio": net["sharpe_ratio"],
        "max_drawdown": net["max_drawdown"],
        "final_equity": net["final_equity"],
        "initial_capital": initial_capital,
        "trading_days": len(trading_days),
        "exit_reasons": exit_reasons,
        # cost accounting
        "total_costs": total_costs,
        "cost_drag_pct": cost_drag,  # total costs / gross P&L
        # gross (cost-free) bundle for side-by-side comparison
        "gross": gross,
    }


def print_summary(summary: dict):
    """Pretty-print backtest results."""
    if "error" in summary:
        print(f"\n  Error: {summary['error']}")
        return

    def pf(x):
        return "inf" if x == float("inf") else f"{x:.2f}"

    p = summary.get("parameters", {})
    g = summary.get("gross", {})
    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS: {p.get('ticker', '?')}")
    print(f"  {p.get('start', '?')} → {p.get('end', '?')}")
    print(f"{'='*60}")
    print(f"  Total trades:     {summary['total_trades']}")
    print(f"  Win/Loss/Scratch: {summary['wins']}/{summary['losses']}/{summary['scratches']}  (net)")
    print(f"  {'-'*56}")
    print(f"  {'Metric':<18}{'Gross':>18}{'Net of costs':>18}")
    print(f"  {'Win rate':<18}{g.get('win_rate', 0):>17.1%}{summary['win_rate']:>18.1%}")
    print(f"  {'Profit factor':<18}{pf(g.get('profit_factor', 0)):>18}{pf(summary['profit_factor']):>18}")
    print(f"  {'Sharpe ratio':<18}{g.get('sharpe_ratio', 0):>18.2f}{summary['sharpe_ratio']:>18.2f}")
    print(f"  {'Total P&L':<18}{('$'+format(g.get('total_pnl', 0), ',.2f')):>18}{('$'+format(summary['total_pnl'], ',.2f')):>18}")
    print(f"  {'Total return':<18}{g.get('total_return', 0):>17.1%}{summary['total_return']:>18.1%}")
    print(f"  {'Max drawdown':<18}{g.get('max_drawdown', 0):>17.1%}{summary['max_drawdown']:>18.1%}")
    print(f"  {'Final equity':<18}{('$'+format(g.get('final_equity', 0), ',.2f')):>18}{('$'+format(summary['final_equity'], ',.2f')):>18}")
    print(f"  {'-'*56}")
    drag = summary.get("cost_drag_pct")
    drag_str = f"{drag:.1%} of gross P&L" if drag is not None else "n/a"
    print(f"  Total costs:      ${summary['total_costs']:,.2f}  ({drag_str})")
    print(f"  Avg win/loss:     ${summary['avg_win']:,.2f} / ${summary['avg_loss']:,.2f}  (net)")
    print(f"  Exit reasons:     {summary['exit_reasons']}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="ORB Strategy Backtester")
    parser.add_argument("--ticker", default=settings.TICKER)
    parser.add_argument("--start", default=settings.BACKTEST_START)
    parser.add_argument("--end", default=settings.BACKTEST_END)
    parser.add_argument("--capital", type=float, default=settings.BACKTEST_INITIAL_CAPITAL)
    parser.add_argument("--or-minutes", type=int, default=settings.OPENING_RANGE_MINUTES)
    parser.add_argument("--rr-ratio", type=float, default=settings.REWARD_RISK_RATIO)
    parser.add_argument("--stop-mode", default=settings.STOP_MODE)
    # Filter toggles: --vwap/--no-vwap etc. Omit to use config defaults.
    parser.add_argument("--vwap", action=argparse.BooleanOptionalAction, default=None,
                        help="enable/disable VWAP filter (default: config)")
    parser.add_argument("--rvol", action=argparse.BooleanOptionalAction, default=None,
                        help="enable/disable RVOL filter (default: config)")
    parser.add_argument("--candle", action=argparse.BooleanOptionalAction, default=None,
                        help="enable/disable candle-strength filter (default: config)")
    parser.add_argument("--rvol-threshold", type=float, default=None)
    parser.add_argument("--candle-pct", type=float, default=None)
    parser.add_argument("--entry-cutoff", default=None,
                        help="ET cutoff for the first breakout, e.g. 09:41 (default: config; '' or 23:59 = all-day)")
    parser.add_argument("--cap-frac", type=float, default=None,
                        help="per-position notional cap as fraction of equity (default: 1/N symbols)")
    parser.add_argument("--regime", action=argparse.BooleanOptionalAction, default=None,
                        help="enable/disable the overnight-gap regime gate (default: config)")
    parser.add_argument("--gap-max", type=float, default=None,
                        help="max |overnight gap| before skipping the day, e.g. 0.015 (default: config)")
    parser.add_argument("--breakout-confirm", default=None, choices=["wick", "close"],
                        help="breakout trigger: wick (any penetration) or close (close beyond OR)")
    args = parser.parse_args()

    summary = run_backtest(
        ticker=args.ticker,
        start=args.start,
        end=args.end,
        initial_capital=args.capital,
        or_minutes=args.or_minutes,
        rr_ratio=args.rr_ratio,
        stop_mode=args.stop_mode,
        filter_vwap=args.vwap,
        filter_rvol=args.rvol,
        rvol_threshold=args.rvol_threshold,
        filter_candle=args.candle,
        candle_pct=args.candle_pct,
        entry_cutoff=args.entry_cutoff,
        capital_cap_frac=args.cap_frac,
        filter_regime=args.regime,
        regime_gap_max=args.gap_max,
        breakout_confirm=args.breakout_confirm,
    )
    print_summary(summary)

    # Export trades to CSV
    if summary.get("trades"):
        df = pd.DataFrame(summary["trades"])
        out_path = f"backtest_{args.ticker}_{args.start}_{args.end}.csv"
        df.to_csv(out_path, index=False)
        print(f"\n  Trades exported to: {out_path}")


if __name__ == "__main__":
    main()
