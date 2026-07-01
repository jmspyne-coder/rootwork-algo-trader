"""
Gap-size segmentation (closes the B5 remainder).

The committed trade files lack the prior close / day open, so gap-size buckets
needed market data. With a working key we fetch daily bars, compute the
overnight gap per date, join to results/trades_{SPY,QQQ}.csv, and report
net-of-3bps stats by |gap| bucket. Appends a section to trade_segmentation.md.

    python scripts/gap_size_segmentation.py
"""
import csv
import os
import sys
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.alpaca_client import get_data_client, fetch_daily_bars

CAP = 10000.0
FILES = {"SPY": "results/trades_SPY.csv", "QQQ": "results/trades_QQQ.csv"}
START, END = "2024-01-01", "2026-06-01"
BUCKETS = [("<0.3%", 0.0, 0.003), ("0.3-0.7%", 0.003, 0.007), (">0.7%", 0.007, 9.0)]


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def net_pnl(r, bps=3.0):
    return float(r["gross_pnl"]) - (bps / 1e4) * float(r["entry_price"]) * float(r["shares"])


def stats(rows):
    pnls = [net_pnl(r) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    by_day = {}
    for r, p in zip(rows, pnls):
        by_day[r["date"]] = by_day.get(r["date"], 0.0) + p
    dr = list(by_day.values())
    sd = np.std(dr, ddof=1) if len(dr) > 1 else 0
    sharpe = (np.mean(dr) / sd * np.sqrt(252)) if sd > 0 else 0.0
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) else float("inf")
    return len(rows), (len(wins) / len(rows) if rows else 0), sharpe, pf


def gap_by_date(ticker, client):
    daily = fetch_daily_bars(ticker, (datetime.fromisoformat(START) - timedelta(days=10)).strftime("%Y-%m-%d"),
                             END, client)
    gaps = {}
    prev_close = None
    for ts, row in daily.iterrows():
        d = str(ts.date())
        if prev_close:
            gaps[d] = (float(row["open"]) - prev_close) / prev_close
        prev_close = float(row["close"])
    return gaps


def main():
    client = get_data_client()
    lines = ["\n## Gap size at open (net 3 bps) — computed from daily bars\n"]
    print("Gap-size segmentation:")
    for tk, path in FILES.items():
        rows = load(path)
        gaps = gap_by_date(tk, client)
        for r in rows:
            r["_gap"] = abs(gaps.get(r["date"], 0.0))
        lines.append(f"\n### {tk} ({len(rows)} trades)\n")
        lines.append("| gap bucket | trades | win% | Sharpe | PF |")
        lines.append("|---|---|---|---|---|")
        print(f"  {tk}:")
        for name, lo, hi in BUCKETS:
            sub = [r for r in rows if lo <= r["_gap"] < hi]
            if not sub:
                lines.append(f"| {name} | 0 | - | - | - |")
                print(f"    {name}: 0 trades")
                continue
            n, wr, sh, pf = stats(sub)
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            lines.append(f"| {name} | {n} | {wr:.0%} | {sh:.2f} | {pf_s} |")
            print(f"    {name}: {n} trades, win {wr:.0%}, Sharpe {sh:.2f}, PF {pf_s}")

    with open("validation/trade_segmentation.md", "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("Appended gap-size section to validation/trade_segmentation.md")


if __name__ == "__main__":
    main()
