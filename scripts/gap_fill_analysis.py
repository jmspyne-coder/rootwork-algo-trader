"""
Gap-Fill validation against the spec's success criteria.

Reads the gap-fill trade export and the committed ORB trades, and reports:
  - slippage cliff (net Sharpe by bps) + kill level
  - DSR at N=18, 100, and the combined trial count (ORB 18 + gap sweep 729)
  - correlation of gap-fill daily returns vs ORB daily returns
Writes validation/gap_fill_results.md and copies the trades to results/.

    python scripts/gap_fill_analysis.py
"""
import csv
import os
import sys
import shutil

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import validate as V

CAP = 10000.0
GAP_CSV = "backtest_gap_QQQ_2024-01-01_2026-06-01.csv"
ORB_CSV = "results/trades_QQQ.csv"
BPS = [0, 3, 5, 7, 10, 15, 20, 25, 30]


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def net_daily_returns(rows, bps):
    by_day = {}
    for r in rows:
        net = float(r["gross_pnl"]) - (bps / 1e4) * float(r["entry_price"]) * float(r["shares"])
        by_day[r["date"]] = by_day.get(r["date"], 0.0) + net / CAP
    return by_day


def sharpe_from_daily(by_day):
    dr = np.array(list(by_day.values()))
    if len(dr) < 2 or np.std(dr, ddof=1) == 0:
        return 0.0
    return float(np.mean(dr) / np.std(dr, ddof=1) * np.sqrt(252))


def kill_level(curve):
    items = sorted(curve.items())
    for i in range(1, len(items)):
        (b0, s0), (b1, s1) = items[i - 1], items[i]
        if s0 > 0 >= s1:
            return round(b0 + (b1 - b0) * (s0 / (s0 - s1)), 1)
    return None


def main():
    gap = load(GAP_CSV)
    orb = load(ORB_CSV)

    # slippage cliff
    curve = {bps: round(sharpe_from_daily(net_daily_returns(gap, bps)), 2) for bps in BPS}
    kill = kill_level(curve)

    # DSR at 3 bps
    g3 = net_daily_returns(gap, 3.0)
    rets = np.array([g3[d] for d in sorted(g3)])
    dsr = {nt: round(V.deflated_sharpe(rets, nt)["deflated_sr_prob"], 4) for nt in (18, 100, 747)}

    # correlation with ORB (align by date, missing = 0 flat day)
    o3 = net_daily_returns(orb, 3.0)
    dates = sorted(set(g3) | set(o3))
    gv = np.array([g3.get(d, 0.0) for d in dates])
    ov = np.array([o3.get(d, 0.0) for d in dates])
    corr = float(np.corrcoef(gv, ov)[0, 1]) if len(dates) > 2 else float("nan")
    overlap = len(set(g3) & set(o3))

    shutil.copyfile(GAP_CSV, "results/trades_gap_QQQ.csv")

    net3 = curve[3]
    crit = {
        "Net Sharpe >= 1.5 @3bps": (net3 >= 1.5, f"{net3:.2f}"),
        "Trade count >= 30": (len(gap) >= 30, str(len(gap))),
        "Correlation w/ ORB < 0.3": (abs(corr) < 0.3, f"{corr:.2f}"),
        "Slippage kill >= 10 bps": ((kill or 99) >= 10, f"{kill} bps" if kill else ">30 bps"),
        "DSR survives @combined N=747": (dsr[747] > 0.5, f"{dsr[747]:.2f}"),
    }

    lines = ["# Gap-Fill v1 — Validation Results (QQQ)\n",
             "Default params (ATR 1.0x stop, 2:1 RR, gap band 0.3%-1.5%, fade), "
             "net of 3 bps, 2024-01-01..2026-06-01. `results/trades_gap_QQQ.csv`.\n",
             f"\n- Trades: {len(gap)} | net Sharpe @3bps: {net3:.2f} | "
             f"correlation with ORB: {corr:.2f} (overlap {overlap} shared days)\n",
             "\n## Slippage cliff (net Sharpe)\n",
             "| bps | " + " | ".join(str(b) for b in BPS) + " |",
             "|---|" + "|".join("---" for _ in BPS) + "|",
             "| QQQ gap-fill | " + " | ".join(f"{curve[b]:.2f}" for b in BPS) + " |",
             f"\nKill level: {('~%.1f bps' % kill) if kill else '> 30 bps'}.\n",
             "\n## Deflated Sharpe by trial count\n",
             "| N trials | 18 | 100 | 747 (combined ORB+gap sweep) |",
             "|---|---|---|---|",
             f"| DSR | {dsr[18]:.2f} | {dsr[100]:.2f} | {dsr[747]:.2f} |\n",
             "\n## Success criteria (spec Section 10)\n",
             "| Criterion | Result | Pass |", "|---|---|---|"]
    for name, (ok, val) in crit.items():
        lines.append(f"| {name} | {val} | {'YES' if ok else 'NO'} |")
    n_pass = sum(1 for ok, _ in crit.values() if ok)
    dsr_ok = crit["DSR survives @combined N=747"][0]
    lines.append(f"\n**{n_pass}/{len(crit)} criteria pass** (plus max drawdown 4.4% <= 5%). "
                 "But the deflated-Sharpe test is the decisive anti-overfitting gate, and it "
                 "FAILS: DSR is 0.30 at N=18 (a fair a-priori count) and collapses to 0.03 at "
                 "N=747. Even before the harsh combined haircut, a 0.30 probability that the true "
                 "Sharpe > 0 is weak (ORB QQQ is 0.92 at N=18). The N=747 figure overcounts "
                 "independent trials (the 729 sweep configs are highly correlated; ONC clustering "
                 "would give far fewer), so 0.03 is a floor, not the true number, but even the "
                 "fair read is not convincing.\n")
    # The DSR gate is decisive per the spec; do not proceed on the count alone.
    verdict = ("PROCEED to paper" if (n_pass == len(crit)) else
               "HOLD — strong diversification, but the edge is statistically fragile")
    lines.append(f"\n**Verdict: {verdict}.**\n")
    lines.append("Read: gap-fill is a genuinely uncorrelated (r = -0.01), slippage-robust "
                 "(kill 13.8 bps) second stream, which is exactly what we wanted structurally. "
                 "But its per-trade edge is thin (mostly EOD closes, few target hits) and does "
                 "not survive deflation the way ORB does. Recommendation: do NOT deploy on the "
                 "default config alone. Run the 729-config sweep + PBO/CSCV, and consider adding "
                 "SPY for more samples, before revisiting. It is a promising diversifier, not a "
                 "proven edge.")

    with open("validation/gap_fill_results.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"gap-fill: {len(gap)} trades, net@3 {net3:.2f}, kill {kill}, "
          f"corr {corr:.2f}, DSR(18/100/747) {dsr[18]}/{dsr[100]}/{dsr[747]}")
    for name, (ok, val) in crit.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val}")
    print(f"Verdict: {verdict}")


if __name__ == "__main__":
    main()
