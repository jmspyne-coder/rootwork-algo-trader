"""
Config A/B + statistical validation in one run.

Runs the validated baseline against the new algorithmic variants (overnight-gap
regime gate, close-confirmed breakout, and the combination) on the SAME data,
net of costs at live-fidelity sizing + entry cutoff, and validates each with the
deflated-Sharpe / bootstrap-CI / permutation tests. One dispatch per ticker
gives the whole algorithmic picture instead of a dozen separate runs.

A variant is only worth adopting if it IMPROVES the net Sharpe AND survives the
deflated-Sharpe haircut (it is not just another overfit knob).

CLI: python -m src.abtest --ticker QQQ --start 2024-01-01 --end 2026-06-01 --trials 18
"""
import argparse

from config import settings
from src.backtest import run_backtest
from src.validate import validate_trades

VARIANTS = [
    ("baseline (v1+candle)", {}),
    ("+ regime gate", {"filter_regime": True}),
    ("close-confirmed", {"breakout_confirm": "close"}),
    ("regime + close", {"filter_regime": True, "breakout_confirm": "close"}),
]


def main():
    ap = argparse.ArgumentParser(description="ORB config A/B + statistical validation")
    ap.add_argument("--ticker", default=settings.TICKER)
    ap.add_argument("--start", default=settings.BACKTEST_START)
    ap.add_argument("--end", default=settings.BACKTEST_END)
    ap.add_argument("--capital", type=float, default=settings.BACKTEST_INITIAL_CAPITAL)
    ap.add_argument("--trials", type=int, default=18,
                    help="config variants tried (deflated-Sharpe multiple-testing haircut)")
    args = ap.parse_args()

    results = []
    for name, kw in VARIANTS:
        print(f"\n----- variant: {name} ({kw or 'config defaults'}) -----")
        s = run_backtest(ticker=args.ticker, start=args.start, end=args.end,
                         initial_capital=args.capital, **kw)
        if "error" in s or not s.get("trades"):
            print(f"  (no trades: {s.get('error', 'none')})")
            results.append((name, None, None))
            continue
        trades = s["trades"]
        v = validate_trades([t["trade_pnl"] for t in trades],
                            [t.get("date", str(t.get("entry_time", ""))[:10]) for t in trades],
                            args.capital, n_trials=args.trials)
        results.append((name, s, v))

    print(f"\n{'='*96}")
    print(f"  A/B + VALIDATION: {args.ticker}  {args.start}..{args.end}  "
          f"(net of costs, {args.trials}-trial haircut)")
    print(f"{'='*96}")
    hdr = f"  {'variant':<22}{'trades':>7}{'win':>6}{'netSharpe':>11}{'netRet':>9}{'maxDD':>7}{'SharpeCI':>16}{'permP':>8}{'DSR':>7}"
    print(hdr)
    print(f"  {'-'*94}")
    for name, s, v in results:
        if s is None:
            print(f"  {name:<22}{'-':>7}")
            continue
        ci = v["bootstrap"]["sharpe_ci"]
        line = (f"  {name:<22}{s['total_trades']:>7}{s['win_rate']:>6.0%}"
                f"{s['sharpe_ratio']:>11.2f}{s['total_return']:>9.1%}{s['max_drawdown']:>7.1%}"
                f"{('['+format(ci[0],'.1f')+','+format(ci[1],'.1f')+']'):>16}"
                f"{v['permutation_p']:>8.3f}{v['deflated']['deflated_sr_prob']:>7.0%}")
        print(line)
    print(f"  {'-'*94}")
    print("  Adopt a variant only if netSharpe improves AND DSR stays high (survives the haircut).")
    print(f"{'='*96}")


if __name__ == "__main__":
    main()
