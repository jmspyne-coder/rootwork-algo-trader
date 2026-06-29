"""
Walk-forward / out-of-sample validation for the ORB strategy.

The backtest headline (Sharpe 2.58, ~2.16 net of costs) is an IN-SAMPLE
number: it was measured on the same 2024-2026 data the params were chosen
on. This module asks the harder questions:

  1. TIME STABILITY   Is the edge consistent across sub-periods, or is it
                      carried by one lucky stretch? Fixed v1 params, net
                      of costs, reported per half-year.

  2. WALK-FORWARD     If you let a small grid optimizer pick params on a
                      rolling in-sample window and then trade them on the
                      next unseen window, do they generalize? Reports the
                      in-sample-best vs out-of-sample gap (the overfit
                      tax) and whether walk-forward selection beats just
                      holding the fixed v1 default.

  3. FILTER SCREEN    Does any single v2 filter (or a loosened one) beat
                      the v1 baseline net of costs over the full history?
                      This is the question left open when we defaulted the
                      filters off.

Everything is reported NET OF COSTS (the cost model from src/costs.py,
default ~3 bps round trip). Small-sample caveat: v1 fires ~60 trades over
2.5 years, so per-window samples are thin. Treat single-window numbers as
directional and weight the stitched out-of-sample aggregate.

Usage:
    python -m src.walkforward --ticker SPY --start 2024-01-01 --end 2026-06-01
"""
import argparse
import copy
from datetime import timedelta

import numpy as np
import pandas as pd

from config import settings
from src.orb_signal import generate_signal, simulate_trade, calculate_atr
from src.risk_manager import simulate_risk_controls
from src.alpaca_client import get_data_client, fetch_multi_day_intraday, fetch_daily_bars
from src.backtest import calculate_performance

CAP = settings.BACKTEST_INITIAL_CAPITAL
MIN_IS_TRADES = 8  # don't let the optimizer pick a combo it barely saw

DEFAULT_V1 = {"or_minutes": 5, "rr_ratio": 2.0, "stop_mode": "atr", "atr_mult": 1.5, "min_range": 0.003}

# Param grid for the walk-forward optimizer. Deliberately small: the more
# combos you let it choose from, the more it overfits the in-sample window.
PARAM_GRID = [
    {"or_minutes": o, "rr_ratio": r, "stop_mode": "atr", "atr_mult": a, "min_range": 0.003}
    for o in (5, 15, 30) for r in (1.5, 2.0, 3.0) for a in (1.0, 1.5, 2.0)
]

# Single-filter screen, all on top of the fixed v1 params.
FILTER_TESTS = [
    ("baseline (none)",  {}),
    ("vwap only",        {"vwap": True}),
    ("rvol >= 1.5",      {"rvol": True, "rvol_thr": 1.5}),
    ("rvol >= 1.2",      {"rvol": True, "rvol_thr": 1.2}),
    ("candle top 30%",   {"candle": True, "candle_pct": 0.3}),
    ("candle top 50%",   {"candle": True, "candle_pct": 0.5}),
]


# ─── signal generation ────────────────────────────────────────────────

def precompute(intraday, daily):
    """Group intraday bars by day and precompute per-day ATR once."""
    intraday = intraday.copy()
    intraday["date"] = intraday.index.date
    day_groups = {d: g.drop(columns=["date"]) for d, g in intraday.groupby("date")}
    atr_by_day = {d: calculate_atr(daily[daily.index.date < d], settings.ATR_PERIOD)
                  for d in day_groups}
    return day_groups, atr_by_day


def build_signals(day_groups, atr_by_day, p):
    """Generate the trade list for one param set over the full history."""
    settings.ATR_STOP_MULTIPLIER = p["atr_mult"]  # generate_signal reads this from settings
    out = []
    for d, db in day_groups.items():
        sig = generate_signal(
            db, atr=atr_by_day[d],
            or_minutes=p["or_minutes"], rr_ratio=p["rr_ratio"],
            stop_mode=p["stop_mode"], min_range_pct=p["min_range"],
            filter_vwap=p.get("vwap", False),
            filter_rvol=p.get("rvol", False), rvol_threshold=p.get("rvol_thr"),
            filter_candle=p.get("candle", False), candle_pct=p.get("candle_pct"),
        )
        if sig is None:
            continue
        r = simulate_trade(sig, db)
        r["date"] = str(d)
        out.append(r)
    return out


def evaluate(trades):
    """Net-of-cost performance for a list of trades (chronological)."""
    if not trades:
        return None
    executed = simulate_risk_controls(copy.deepcopy(trades), CAP)
    days = sorted({t["date"] for t in trades})
    s = calculate_performance(executed, CAP, days)
    return None if "error" in s else s


def in_range(trades, start_str, end_str):
    return [t for t in trades if start_str <= t["date"] < end_str]


# ─── analyses ─────────────────────────────────────────────────────────

def period_stability(v1_trades, start, end, n_buckets=5):
    span = (pd.Timestamp(end) - pd.Timestamp(start)) / n_buckets
    print(f"\n{'='*72}\n  1. TIME STABILITY  (fixed v1, net of costs, {n_buckets} buckets)\n{'='*72}")
    print(f"  {'period':<26}{'trades':>7}{'win%':>8}{'Sharpe':>9}{'return':>9}{'maxDD':>8}")
    positive = 0
    for i in range(n_buckets):
        b0 = pd.Timestamp(start) + span * i
        b1 = pd.Timestamp(start) + span * (i + 1)
        sub = in_range(v1_trades, b0.strftime("%Y-%m-%d"), b1.strftime("%Y-%m-%d"))
        s = evaluate(sub)
        lab = f"{b0.date()}..{b1.date()}"
        if s is None:
            print(f"  {lab:<26}{0:>7}{'-':>8}{'-':>9}{'-':>9}{'-':>8}")
            continue
        if s["total_return"] > 0:
            positive += 1
        print(f"  {lab:<26}{s['total_trades']:>7}{s['win_rate']:>8.1%}"
              f"{s['sharpe_ratio']:>9.2f}{s['total_return']:>9.1%}{s['max_drawdown']:>8.1%}")
    print(f"\n  -> {positive}/{n_buckets} buckets net-positive.")


def walk_forward(signals_by_key, start, end, train_m=12, test_m=3, step_m=3):
    print(f"\n{'='*72}\n  2. WALK-FORWARD  (train {train_m}m / test {test_m}m, net of costs)\n{'='*72}")
    print(f"  grid = {len(PARAM_GRID)} combos; optimizer picks best in-sample net Sharpe per window")
    s_ts, e_ts = pd.Timestamp(start), pd.Timestamp(end)
    windows = []
    i = 0
    while True:
        tr_s = s_ts + pd.DateOffset(months=i * step_m)
        tr_e = tr_s + pd.DateOffset(months=train_m)
        te_e = tr_e + pd.DateOffset(months=test_m)
        if te_e > e_ts:
            break
        windows.append((tr_s, tr_e, te_e))
        i += 1

    print(f"\n  {'OOS window':<26}{'chosen params':>22}{'IS Shrp':>9}{'OOS Shrp':>9}")
    wf_oos, v1_oos = [], []
    is_best_sharpes, oos_sharpes = [], []
    for tr_s, tr_e, te_e in windows:
        a, b, c = tr_s.strftime("%Y-%m-%d"), tr_e.strftime("%Y-%m-%d"), te_e.strftime("%Y-%m-%d")
        best_key, best_sharpe = None, -1e9
        for key, trades in signals_by_key.items():
            s = evaluate(in_range(trades, a, b))
            if s is None or s["total_trades"] < MIN_IS_TRADES:
                continue
            if s["sharpe_ratio"] > best_sharpe:
                best_sharpe, best_key = s["sharpe_ratio"], key
        if best_key is None:
            best_key, best_sharpe = tuple(DEFAULT_V1[k] for k in ("or_minutes", "rr_ratio", "atr_mult")), float("nan")
        # OOS for the chosen combo
        chosen = signals_by_key[best_key]
        oos = in_range(chosen, b, c)
        wf_oos.extend(oos)
        v1_oos.extend(in_range(signals_by_key[V1_KEY], b, c))
        s_oos = evaluate(oos)
        oos_sh = s_oos["sharpe_ratio"] if s_oos else float("nan")
        is_best_sharpes.append(best_sharpe)
        oos_sharpes.append(oos_sh)
        pstr = f"o{best_key[0]} r{best_key[1]} a{best_key[2]}"
        print(f"  {b}..{c:<14}{pstr:>22}{best_sharpe:>9.2f}{oos_sh:>9.2f}")

    wf = evaluate(wf_oos)
    v1 = evaluate(v1_oos)
    print(f"\n  Stitched out-of-sample ({len(windows)} windows):")
    print(f"  {'approach':<26}{'trades':>7}{'win%':>8}{'Sharpe':>9}{'return':>9}{'maxDD':>8}")
    for lab, s in (("walk-forward selected", wf), ("fixed v1 default", v1)):
        if s is None:
            print(f"  {lab:<26}  no trades")
            continue
        print(f"  {lab:<26}{s['total_trades']:>7}{s['win_rate']:>8.1%}"
              f"{s['sharpe_ratio']:>9.2f}{s['total_return']:>9.1%}{s['max_drawdown']:>8.1%}")
    mean_is = np.nanmean(is_best_sharpes)
    mean_oos = np.nanmean(oos_sharpes)
    print(f"\n  Overfit tax: mean in-sample-best Sharpe {mean_is:.2f} vs mean OOS Sharpe {mean_oos:.2f}"
          f"  (gap {mean_is - mean_oos:.2f})")


def filter_screen(day_groups, atr_by_day):
    print(f"\n{'='*72}\n  3. FILTER SCREEN  (fixed v1 params, full history, net of costs)\n{'='*72}")
    print(f"  {'filter':<20}{'trades':>7}{'win%':>8}{'Sharpe':>9}{'return':>9}{'maxDD':>8}")
    base_sharpe = None
    for name, extra in FILTER_TESTS:
        p = {**DEFAULT_V1, **extra}
        s = evaluate(build_signals(day_groups, atr_by_day, p))
        if s is None:
            print(f"  {name:<20}{0:>7}{'-':>8}{'-':>9}{'-':>9}{'-':>8}")
            continue
        if name.startswith("baseline"):
            base_sharpe = s["sharpe_ratio"]
        flag = ""
        if base_sharpe is not None and not name.startswith("baseline"):
            flag = "  BEATS baseline" if s["sharpe_ratio"] > base_sharpe else ""
        print(f"  {name:<20}{s['total_trades']:>7}{s['win_rate']:>8.1%}"
              f"{s['sharpe_ratio']:>9.2f}{s['total_return']:>9.1%}{s['max_drawdown']:>8.1%}{flag}")


def holdout_filter_check(day_groups, atr_by_day, split="2025-06-01"):
    """Candle filter is the one in-sample winner. Test it on a true holdout:
    designed/measured on data before `split`, judged on data after it."""
    print(f"\n{'='*72}\n  4. CANDLE FILTER OUT-OF-SAMPLE  (holdout split {split}, net of costs)\n{'='*72}")
    print(f"  {'config':<18}{'segment':<22}{'trades':>7}{'win%':>8}{'Sharpe':>9}{'return':>9}")
    for name, extra in [("baseline", {}), ("candle top 50%", {"candle": True, "candle_pct": 0.5})]:
        trades = build_signals(day_groups, atr_by_day, {**DEFAULT_V1, **extra})
        for seg, a, b in [("in-sample < split", "0000", split), ("HOLDOUT >= split", split, "9999")]:
            s = evaluate(in_range(trades, a, b))
            if s is None:
                print(f"  {name:<18}{seg:<22}{0:>7}{'-':>8}{'-':>9}{'-':>9}")
                continue
            print(f"  {name:<18}{seg:<22}{s['total_trades']:>7}{s['win_rate']:>8.1%}"
                  f"{s['sharpe_ratio']:>9.2f}{s['total_return']:>9.1%}")


V1_KEY = (DEFAULT_V1["or_minutes"], DEFAULT_V1["rr_ratio"], DEFAULT_V1["atr_mult"])


def main():
    ap = argparse.ArgumentParser(description="ORB walk-forward / OOS validation")
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-06-01")
    args = ap.parse_args()

    client = get_data_client()
    daily_start = (pd.Timestamp(args.start) - timedelta(days=40)).strftime("%Y-%m-%d")
    print(f"Fetching {args.ticker} {args.start}..{args.end} (once)...")
    daily = fetch_daily_bars(args.ticker, daily_start, args.end, client)
    intra = fetch_multi_day_intraday(args.ticker, args.start, args.end, client)
    print(f"  daily={len(daily)} intraday={len(intra)}")

    day_groups, atr_by_day = precompute(intra, daily)

    # Precompute signals for every grid combo once; key by (or, rr, atr_mult).
    print(f"  generating signals for {len(PARAM_GRID)} param combos...")
    signals_by_key = {}
    for p in PARAM_GRID:
        signals_by_key[(p["or_minutes"], p["rr_ratio"], p["atr_mult"])] = build_signals(day_groups, atr_by_day, p)

    v1_trades = signals_by_key[V1_KEY]
    full = evaluate(v1_trades)
    print(f"\n  Full-history v1 (net of costs): {full['total_trades']} trades, "
          f"win {full['win_rate']:.1%}, Sharpe {full['sharpe_ratio']:.2f}, return {full['total_return']:.1%}")

    period_stability(v1_trades, args.start, args.end)
    walk_forward(signals_by_key, args.start, args.end)
    filter_screen(day_groups, atr_by_day)
    holdout_filter_check(day_groups, atr_by_day)


if __name__ == "__main__":
    main()
