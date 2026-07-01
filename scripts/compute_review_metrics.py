"""
Recompute the Reviewer Brief metrics from the committed per-trade backtest files.

Reads results/trades_{SPY,QQQ}.csv (the executed trade lists exported by
src/backtest.py at the default 3 bps round-trip cost, active config: 5m ORB,
ATR 1.5x stop, 0.3% min range, 2:1 RR, candle top-50% filter ON, per-symbol
notional cap = 1/N = 50%). Each row carries the cost-free gross_pnl plus
entry_price and shares, so net P&L at any cost tier is exact:

    net = gross_pnl - (round_trip_bps / 1e4) * entry_price * shares
    round_trip_bps = 2 * slippage_bps + spread_bps   (see src/costs.py)

Metrics at each tier are computed on the SAME executed set, matching how
src/backtest.calculate_performance reports gross vs net side by side.

Validation (bootstrap CIs, sign-permutation p-value, deflated Sharpe) runs on
the net-3bps daily returns using the functions in src/validate.py. Writes
results/backtest_validation.json.

    python scripts/compute_review_metrics.py
"""
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import validate as V

CAP = 10000.0
FILES = {"SPY": "results/trades_SPY.csv", "QQQ": "results/trades_QQQ.csv"}
BPS_TIERS = {"gross": 0.0, "net_3bps": 3.0, "net_7bps": 7.0, "net_10bps": 10.0}


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def net_pnls(rows, bps):
    return [float(r["gross_pnl"]) - (bps / 1e4) * float(r["entry_price"]) * float(r["shares"])
            for r in rows]


def curve_stats(pnls, dates, cap=CAP):
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity, peak, max_dd = cap, cap, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0)
    by_day = {}
    for d, p in zip(dates, pnls):
        by_day[d] = by_day.get(d, 0.0) + p
    dr = list(by_day.values())
    sd = np.std(dr, ddof=1) if len(dr) > 1 else 0
    sharpe = (np.mean(dr) / sd * np.sqrt(252)) if sd > 0 else 0.0
    return {
        "trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0,
        "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) else float("inf"),
        "sharpe": round(float(sharpe), 2),
        "max_drawdown": round(max_dd, 4),
        "total_return": round((equity - cap) / cap, 4),
    }


def bootstrap(rets, n=10000, ci=95, seed=7):
    """IID bootstrap: resample individual daily returns with replacement."""
    rng = np.random.default_rng(seed)
    k = len(rets)
    sh = np.array([V.annualized_sharpe(rets[rng.integers(0, k, k)]) for _ in range(n)])
    lo = (100 - ci) / 2
    return {"method": "iid", "ci_pct": ci, "n": n, "median": round(float(np.median(sh)), 2),
            "lo": round(float(np.percentile(sh, lo)), 2),
            "hi": round(float(np.percentile(sh, 100 - lo)), 2),
            "p_sharpe_le_0": round(float(np.mean(sh <= 0)), 4)}


def block_bootstrap(rets, block=5, n=10000, ci=95, seed=7):
    """Moving block bootstrap (Kunsch 1989): resample contiguous blocks of
    `block` days to preserve short-range serial correlation the IID bootstrap
    destroys. Concatenate blocks to length k, then compute the Sharpe."""
    rng = np.random.default_rng(seed)
    k = len(rets)
    if k < block:
        return {"method": "moving_block", "block": block, "note": "series shorter than block"}
    n_starts = k - block + 1
    n_blocks = int(np.ceil(k / block))
    sh = np.empty(n)
    for i in range(n):
        starts = rng.integers(0, n_starts, n_blocks)
        sample = np.concatenate([rets[s:s + block] for s in starts])[:k]
        sh[i] = V.annualized_sharpe(sample)
    lo = (100 - ci) / 2
    return {"method": "moving_block", "block": block, "ci_pct": ci, "n": n,
            "median": round(float(np.median(sh)), 2),
            "lo": round(float(np.percentile(sh, lo)), 2),
            "hi": round(float(np.percentile(sh, 100 - lo)), 2),
            "p_sharpe_le_0": round(float(np.mean(sh <= 0)), 4)}


def lag1_autocorr(rets):
    r = np.asarray(rets, dtype=float)
    if len(r) < 3:
        return None
    r = r - r.mean()
    denom = float(np.sum(r * r))
    if denom == 0:
        return None
    return round(float(np.sum(r[:-1] * r[1:]) / denom), 4)


def binom_one_sided_p(wins, n, p0=0.5):
    """Exact one-sided binomial p-value: P(X >= wins | Binom(n, 0.5))."""
    return round(sum(math.comb(n, k) for k in range(wins, n + 1)) * (p0 ** n), 6)


def main():
    out = {"meta": {
        "source": "results/trades_{SPY,QQQ}.csv (real backtest runs), recomputed at 3 cost tiers",
        "period": "2024-01-01 to 2026-06-01 (SIP historical, 1-min bars)",
        "capital": CAP,
        "config": "5m ORB, ATR 1.5x stop, 0.3% min range, 2:1 RR, candle top-50% ON, per-symbol notional cap 50%",
        "cost_model": "round_trip_bps = 2*slippage + spread; CSVs generated at 3 bps",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }, "tickers": {}}

    for tk, path in FILES.items():
        rows = load(path)
        dates = [r["date"] for r in rows]
        tiers = {t: curve_stats(net_pnls(rows, bps), dates) for t, bps in BPS_TIERS.items()}
        rets = V.daily_returns(net_pnls(rows, 3.0), dates, CAP)
        gross_sh, net3_sh = tiers["gross"]["sharpe"], tiers["net_3bps"]["sharpe"]
        wins = sum(1 for p in net_pnls(rows, 3.0) if p > 0)
        out["tickers"][tk] = {
            "trades": tiers["net_3bps"]["trades"],
            "win_rate": tiers["net_3bps"]["win_rate"],
            "binomial_p_winrate_vs_50pct": binom_one_sided_p(wins, tiers["net_3bps"]["trades"]),
            "gross_sharpe": gross_sh,
            "net_sharpe_3bps": net3_sh,
            "net_sharpe_7bps": tiers["net_7bps"]["sharpe"],
            "net_sharpe_10bps": tiers["net_10bps"]["sharpe"],
            "net3_sharpe_pct_of_gross": round(net3_sh / gross_sh, 4) if gross_sh else None,
            "max_drawdown_net3": tiers["net_3bps"]["max_drawdown"],
            "profit_factor_net3": tiers["net_3bps"]["profit_factor"],
            "avg_win_net3": tiers["net_3bps"]["avg_win"],
            "avg_loss_net3": tiers["net_3bps"]["avg_loss"],
            "total_return_net3": tiers["net_3bps"]["total_return"],
            "lag1_autocorr_daily_pnl": lag1_autocorr(rets),
            "bootstrap_iid_90ci_sharpe": bootstrap(rets, ci=90),
            "bootstrap_iid_95ci_sharpe": bootstrap(rets, ci=95),
            "bootstrap_block5_95ci_sharpe": block_bootstrap(rets, block=5, ci=95),
            "permutation_p": round(V.sign_permutation_p(rets, n=10000), 4),
            "deflated_sharpe_prob_by_trials": {
                str(nt): round(V.deflated_sharpe(rets, nt)["deflated_sr_prob"], 4)
                for nt in (18, 30, 50, 100)
            },
            "per_tier": tiers,
        }

    with open("results/backtest_validation.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps(out["tickers"], indent=2, default=str))


if __name__ == "__main__":
    main()
