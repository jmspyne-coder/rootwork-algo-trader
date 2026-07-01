"""
ORB sweep that persists per-config daily returns, so a direct ORB PBO/CSCV can
run (closing the gap flagged in validation/pbo_cscv.md: the original ORB sweeps
never saved per-config return series).

Grid (QQQ, the shipped edge): or_minutes x rr x atr_stop_mult x candle_filter
= 3 x 3 x 3 x 2 = 54 configs. Each config's entry cutoff tracks its opening-range
window (OR end + 6 min), matching live cadence. Writes, in the same schema the
gap sweep + scripts/cscv.py expect:
  validation/orb_sweep_results.csv   (config_id, params, trades, sharpe, ...)
  validation/orb_sweep_returns.csv   (config_id, date, daily_return)

    python scripts/orb_pbo_sweep.py --ticker QQQ --start 2024-01-01 --end 2026-06-01
    python scripts/cscv.py --returns validation/orb_sweep_returns.csv \
        --results validation/orb_sweep_results.csv --segments 8
"""
import argparse
import csv
import itertools
import os
import sys
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings
from src.orb_signal import generate_signal, simulate_trade, calculate_atr
from src.risk_manager import simulate_risk_controls
from src.backtest import calculate_performance
from src.alpaca_client import get_data_client, fetch_multi_day_intraday, fetch_daily_bars

CAP, CAP_FRAC, OUT = 10000.0, 0.5, "validation"
GRID = {
    "or_minutes": [5, 15, 30],
    "rr_ratio": [1.5, 2.0, 3.0],
    "atr_mult": [1.0, 1.5, 2.0],
    "candle": [False, True],
}


def cutoff_for(or_minutes):
    end = datetime(2000, 1, 1, 9, 30) + timedelta(minutes=or_minutes + 6)
    return end.strftime("%H:%M")


def prep(ticker, start, end, client):
    daily_start = (datetime.fromisoformat(start) - timedelta(days=30)).strftime("%Y-%m-%d")
    daily = fetch_daily_bars(ticker, daily_start, end, client)
    intra = fetch_multi_day_intraday(ticker, start, end, client)
    intra = intra.copy()
    intra["date"] = intra.index.date
    groups, atr_by_day = {}, {}
    for d, g in intra.groupby("date"):
        groups[d] = g.drop(columns=["date"])
        atr_by_day[d] = calculate_atr(daily[daily.index.date < d], settings.ATR_PERIOD)
    return groups, atr_by_day


def run_one(groups, atr_by_day, cfg):
    orig_mult = settings.ATR_STOP_MULTIPLIER
    raw = []
    try:
        settings.ATR_STOP_MULTIPLIER = cfg["atr_mult"]
        cutoff = cutoff_for(cfg["or_minutes"])
        for d, db in groups.items():
            sig = generate_signal(
                db, atr=atr_by_day[d], or_minutes=cfg["or_minutes"], rr_ratio=cfg["rr_ratio"],
                stop_mode="atr", min_range_pct=0.003,
                filter_vwap=False, filter_rvol=False,
                filter_candle=cfg["candle"], candle_pct=0.5,
                entry_cutoff=cutoff,
            )
            if sig is None:
                continue
            r = simulate_trade(sig, db)
            r["date"] = str(d)
            raw.append(r)
    finally:
        settings.ATR_STOP_MULTIPLIER = orig_mult
    if not raw:
        return {"total_trades": 0}, []
    executed = simulate_risk_controls(raw, CAP, capital_cap_frac=CAP_FRAC)
    perf = calculate_performance(executed, CAP, [t["date"] for t in raw])
    by_day = {}
    for t in executed:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["trade_pnl"] / CAP
    return perf, sorted(by_day.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="QQQ")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-06-01")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    client = get_data_client()
    print(f"Fetching {a.ticker} {a.start}..{a.end} once...")
    groups, atr_by_day = prep(a.ticker, a.start, a.end, client)

    keys = list(GRID.keys())
    combos = list(itertools.product(*(GRID[k] for k in keys)))
    print(f"Running {len(combos)} ORB configs...")
    rows, returns_rows = [], []
    for i, combo in enumerate(combos):
        cfg = dict(zip(keys, combo))
        cid = f"o{i:03d}"
        perf, daily = run_one(groups, atr_by_day, cfg)
        rows.append({"config_id": cid, **cfg,
                     "total_trades": perf.get("total_trades", 0),
                     "win_rate": perf.get("win_rate", 0),
                     "sharpe_ratio": perf.get("sharpe_ratio", 0),
                     "max_drawdown": perf.get("max_drawdown", 0),
                     "total_return": perf.get("total_return", 0)})
        for d, ret in daily:
            returns_rows.append({"config_id": cid, "date": d, "daily_return": ret})

    with open(f"{OUT}/orb_sweep_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    with open(f"{OUT}/orb_sweep_returns.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["config_id", "date", "daily_return"])
        w.writeheader(); w.writerows(returns_rows)
    credible = sum(1 for r in rows if r["total_trades"] >= 30)
    print(f"Wrote {OUT}/orb_sweep_results.csv + orb_sweep_returns.csv "
          f"({len(combos)} configs, {credible} with >=30 trades).")


if __name__ == "__main__":
    main()
