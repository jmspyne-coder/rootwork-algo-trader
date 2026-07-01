"""
CSCV / PBO (B3) — Probability of Backtest Overfitting via Combinatorially
Symmetric Cross-Validation (Bailey, Borwein, Lopez de Prado, Zhu, 2017).

Consumes the per-config daily return series persisted by the gap sweep
(validation/gap_sweep_returns.csv: config_id, date, daily_return) and measures
how often the in-sample-best config underperforms the OOS median.

Method:
  1. Build a T x N matrix M (T = union of trading days, N = credible configs).
     A config that did not trade on a day contributes 0 return that day.
  2. Split the T rows, in time order, into S disjoint contiguous segments.
  3. For every way to choose S/2 segments as in-sample (the rest OOS) -> C(S,S/2)
     splits: pick the config with the highest IS Sharpe, find its OOS Sharpe rank
     among all configs, map to relative rank w in (0,1), logit lambda = ln(w/(1-w)).
     The split is "overfit" when w <= 0.5 (IS-best is below the OOS median).
  4. PBO = fraction of splits that are overfit. Also report the OOS-vs-IS
     degradation slope and P(OOS Sharpe of the selected config < 0).

    python scripts/cscv.py --segments 8

Credible universe = configs with >= 30 total trades (the sweep's ranking floor),
read from validation/gap_sweep_results.csv when present.
"""
import argparse
import csv
import itertools
from collections import defaultdict

import numpy as np

MIN_TRADES = 30
ANN = np.sqrt(252)


def sharpe(x):
    x = np.asarray(x, dtype=float)
    if x.size < 2:
        return 0.0
    sd = x.std(ddof=1)
    return float(x.mean() / sd * ANN) if sd > 0 else 0.0


def load_matrix(RETURNS, RESULTS):
    by_cfg = defaultdict(dict)
    dates = set()
    with open(RETURNS) as f:
        for row in csv.DictReader(f):
            by_cfg[row["config_id"]][row["date"]] = float(row["daily_return"])
            dates.add(row["date"])
    # Credible universe: configs meeting the trade-count floor.
    keep = None
    try:
        with open(RESULTS) as f:
            keep = {r["config_id"] for r in csv.DictReader(f)
                    if float(r["total_trades"]) >= MIN_TRADES}
    except FileNotFoundError:
        pass
    cfgs = sorted(c for c in by_cfg if keep is None or c in keep)
    dates = sorted(dates)
    didx = {d: i for i, d in enumerate(dates)}
    M = np.zeros((len(dates), len(cfgs)))
    for j, c in enumerate(cfgs):
        for d, r in by_cfg[c].items():
            M[didx[d], j] = r
    return M, cfgs, dates


def cscv(M, S):
    T, N = M.shape
    # Contiguous, (near-)equal time segments.
    bounds = np.linspace(0, T, S + 1, dtype=int)
    segs = [np.arange(bounds[i], bounds[i + 1]) for i in range(S)]
    lambdas, is_sh, oos_sh_star, overfit = [], [], [], []
    for combo in itertools.combinations(range(S), S // 2):
        is_rows = np.concatenate([segs[i] for i in combo])
        oos_rows = np.concatenate([segs[i] for i in range(S) if i not in combo])
        is_perf = np.array([sharpe(M[is_rows, j]) for j in range(N)])
        oos_perf = np.array([sharpe(M[oos_rows, j]) for j in range(N)])
        n_star = int(np.argmax(is_perf))
        # Relative OOS rank of the IS-best config (1 = worst .. N = best).
        rank = int((oos_perf <= oos_perf[n_star]).sum())
        w = rank / (N + 1)
        w = min(max(w, 1e-6), 1 - 1e-6)
        lambdas.append(np.log(w / (1 - w)))
        is_sh.append(is_perf[n_star])
        oos_sh_star.append(oos_perf[n_star])
        overfit.append(w <= 0.5)
    lambdas = np.array(lambdas)
    is_sh, oos_sh_star = np.array(is_sh), np.array(oos_sh_star)
    slope = float(np.polyfit(is_sh, oos_sh_star, 1)[0]) if len(is_sh) > 1 else float("nan")
    return {
        "n_configs": N, "n_days": T, "segments": S, "n_splits": len(lambdas),
        "pbo": float(np.mean(overfit)),
        "median_logit": float(np.median(lambdas)),
        "degradation_slope": slope,
        "p_oos_selected_below_0": float(np.mean(oos_sh_star < 0)),
        "mean_is_sharpe_selected": float(is_sh.mean()),
        "mean_oos_sharpe_selected": float(oos_sh_star.mean()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=8)
    ap.add_argument("--returns", default="validation/gap_sweep_returns.csv")
    ap.add_argument("--results", default="validation/gap_sweep_results.csv")
    a = ap.parse_args()
    M, cfgs, dates = load_matrix(a.returns, a.results)
    res = cscv(M, a.segments)
    print(f"Configs (>= {MIN_TRADES} trades): {res['n_configs']}")
    print(f"Trading days: {res['n_days']}  |  segments S={res['segments']}  |  "
          f"splits C(S,S/2)={res['n_splits']}")
    print(f"PBO                         : {res['pbo']:.3f}")
    print(f"Median logit(lambda)        : {res['median_logit']:.3f}")
    print(f"Degradation slope (OOS~IS)  : {res['degradation_slope']:.3f}")
    print(f"P(OOS Sharpe of pick < 0)   : {res['p_oos_selected_below_0']:.3f}")
    print(f"Mean IS Sharpe of pick      : {res['mean_is_sharpe_selected']:.2f}")
    print(f"Mean OOS Sharpe of pick     : {res['mean_oos_sharpe_selected']:.2f}")
    return res


if __name__ == "__main__":
    main()
