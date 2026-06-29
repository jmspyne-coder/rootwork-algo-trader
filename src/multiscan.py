"""
Multi-instrument generalization + regime probe for the validated config.

Two disciplined Phase-3 questions, both net of costs and both with a true
holdout split so we are not just re-reading in-sample noise:

  1. GENERALIZATION  Does the SAME fixed config (v1 + candle top-50%, no
     param tuning) show the edge on other liquid ETFs (QQQ, IWM), not just
     SPY? Independent confirmation on multiple symbols is strong evidence the
     edge is real; it also fixes the low-absolute-return weakness, since more
     symbols means more trades.

  2. REGIME PROBE   The leak-finder hinted the edge concentrates in wide
     opening ranges. Does raising the opening-range floor (a volatility-regime
     proxy) help OUT OF SAMPLE, or is it in-sample noise? Tested on SPY across
     a few pre-registered thresholds; adopt only if it holds on the holdout.

Usage:
    python -m src.multiscan --holdout 2025-06-01
"""
import argparse

from config import settings
from src.alpaca_client import get_data_client, fetch_multi_day_intraday, fetch_daily_bars
from src.walkforward import precompute, build_signals, evaluate, in_range, DEFAULT_V1

# The current live config: v1 params + candle-strength filter at top-50%.
LIVE_CONFIG = {**DEFAULT_V1, "candle": True, "candle_pct": 0.5}
INSTRUMENTS = ["SPY", "QQQ", "IWM"]
START, END = "2024-01-01", "2026-06-01"


def _row(label, s):
    if s is None:
        return f"  {label:<26}{'(no trades)':>12}"
    return (f"  {label:<26}{s['total_trades']:>7}{s['win_rate']:>8.0%}"
            f"{s['sharpe_ratio']:>9.2f}{s['total_return']:>9.1%}{s['max_drawdown']:>8.1%}")


def fetch_and_build(ticker, client, config):
    from datetime import timedelta
    import pandas as pd
    daily_start = (pd.Timestamp(START) - timedelta(days=40)).strftime("%Y-%m-%d")
    daily = fetch_daily_bars(ticker, daily_start, END, client)
    intra = fetch_multi_day_intraday(ticker, START, END, client)
    day_groups, atr_by_day = precompute(intra, daily)
    return day_groups, atr_by_day


def main():
    ap = argparse.ArgumentParser(description="Multi-instrument generalization + regime probe")
    ap.add_argument("--holdout", default="2025-06-01", help="holdout split date (>= is out-of-sample)")
    args = ap.parse_args()
    split = args.holdout
    client = get_data_client()

    print(f"Config: {LIVE_CONFIG}")
    print(f"Holdout split: {split} (in-sample < split <= holdout)\n")

    print(f"{'='*70}\n  1. GENERALIZATION — same config across instruments (net of costs)\n{'='*70}")
    print(f"  {'instrument / segment':<26}{'trades':>7}{'win%':>8}{'Sharpe':>9}{'return':>9}{'maxDD':>8}")
    spy_cache = None
    combined_full = []
    for tk in INSTRUMENTS:
        dg, atr = fetch_and_build(tk, client, LIVE_CONFIG)
        if tk == "SPY":
            spy_cache = (dg, atr)
        trades = build_signals(dg, atr, LIVE_CONFIG)
        combined_full.extend(trades)
        print(_row(f"{tk}  full history", evaluate(trades)))
        print(_row(f"{tk}  in-sample", evaluate(in_range(trades, "0000", split))))
        print(_row(f"{tk}  HOLDOUT", evaluate(in_range(trades, split, "9999"))))
        print()

    cf = evaluate(sorted(combined_full, key=lambda t: t["entry_time"]))
    print(_row("ALL 3 pooled (full)", cf))
    print("  (pooled = indicative diversification: more trades, same edge; not a single-account book)")

    print(f"\n{'='*70}\n  2. REGIME PROBE — opening-range floor on SPY (net of costs)\n{'='*70}")
    print("  Higher floor = only trade wider (higher-vol) opening ranges.")
    print(f"  {'min_range / segment':<26}{'trades':>7}{'win%':>8}{'Sharpe':>9}{'return':>9}{'maxDD':>8}")
    dg, atr = spy_cache
    for mr in (0.003, 0.004, 0.005):
        trades = build_signals(dg, atr, {**LIVE_CONFIG, "min_range": mr})
        tag = f"{mr:.1%}"
        print(_row(f"{tag}  full", evaluate(trades)))
        print(_row(f"{tag}  HOLDOUT", evaluate(in_range(trades, split, "9999"))))
        print()


if __name__ == "__main__":
    main()
