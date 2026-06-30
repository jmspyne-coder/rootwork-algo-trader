"""
Statistical validation of a strategy's edge.

A single Sharpe number on a few dozen trades is not evidence — it is one draw
from a noisy distribution, and we tried many config variants to find it. This
module supplies the rigor that separates a real edge from an overfit one:

  - bootstrap_ci:        resampled 90% confidence interval on annualized Sharpe
                         and total return (how stable is the number?).
  - sign_permutation_p:  p-value that the directional edge is not chance
                         (randomly flip daily-return signs; how often does the
                         null beat the observed mean?).
  - deflated_sharpe:     Sharpe haircut for MULTIPLE TESTING (Bailey & Lopez de
                         Prado). Given we tried N config variants, what is the
                         probability the true Sharpe is still > 0? This is the
                         number that kills overfit strategies.

All operate on a chronological list of per-trade P&L (net of costs) + dates,
aggregated to daily returns. numpy only (no scipy); normal CDF/PPF are inline.

CLI:  python -m src.validate --ticker QQQ --start 2024-01-01 --end 2026-06-01 --trials 12
"""
import argparse
from math import sqrt, log, erf, e

import numpy as np

from config import settings

TRADING_DAYS = 252
_EULER = 0.5772156649015329


# ─── Normal CDF / inverse-CDF (no scipy dependency) ──────────────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    if p <= 0.0:
        return -np.inf
    if p >= 1.0:
        return np.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = sqrt(-2 * log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = sqrt(-2 * log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# ─── Core stats ──────────────────────────────────────────────────────
def daily_returns(pnls, dates, initial_capital: float) -> np.ndarray:
    """Aggregate per-trade P&L into one return per trading day (simple return on
    initial capital). One number per day keeps the Sharpe daily-based, matching
    the backtest."""
    by_day = {}
    for p, d in zip(pnls, dates):
        by_day[d] = by_day.get(d, 0.0) + float(p)
    return np.array([by_day[d] / initial_capital for d in sorted(by_day)])


def annualized_sharpe(rets: np.ndarray) -> float:
    rets = np.asarray(rets, dtype=float)
    if len(rets) < 2:
        return 0.0
    sd = np.std(rets, ddof=1)
    if sd == 0:
        return 0.0
    return float(np.mean(rets) / sd * sqrt(TRADING_DAYS))


def bootstrap_ci(rets: np.ndarray, n: int = 2000, seed: int = 7) -> dict:
    """Resample daily returns with replacement; report the 5/95 percentile band
    on annualized Sharpe and on total return, plus the fraction of resamples
    with a non-positive Sharpe (a stability read)."""
    rets = np.asarray(rets, dtype=float)
    if len(rets) < 2:
        return {"sharpe_median": 0.0, "sharpe_ci": (0.0, 0.0),
                "return_median": 0.0, "return_ci": (0.0, 0.0), "p_sharpe_le_0": 1.0}
    rng = np.random.default_rng(seed)
    k = len(rets)
    sharpes = np.empty(n)
    totrets = np.empty(n)
    for i in range(n):
        s = rets[rng.integers(0, k, k)]
        sharpes[i] = annualized_sharpe(s)
        totrets[i] = s.sum()
    return {
        "sharpe_median": float(np.median(sharpes)),
        "sharpe_ci": (float(np.percentile(sharpes, 5)), float(np.percentile(sharpes, 95))),
        "return_median": float(np.median(totrets)),
        "return_ci": (float(np.percentile(totrets, 5)), float(np.percentile(totrets, 95))),
        "p_sharpe_le_0": float(np.mean(sharpes <= 0)),
    }


def sign_permutation_p(rets: np.ndarray, n: int = 5000, seed: int = 7) -> float:
    """One-sided p-value that the mean daily return > 0 is not chance. Null:
    return signs are random (no directional edge). Fraction of sign-flipped
    permutations whose mean >= the observed mean."""
    rets = np.asarray(rets, dtype=float)
    obs = float(np.mean(rets)) if len(rets) else 0.0
    if len(rets) < 2 or obs <= 0:
        return 1.0
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n):
        signs = rng.choice((-1.0, 1.0), size=len(rets))
        if np.mean(rets * signs) >= obs:
            hits += 1
    return (hits + 1) / (n + 1)


def _skew(r: np.ndarray) -> float:
    s = np.std(r)
    return float(np.mean(((r - np.mean(r)) / s) ** 3)) if s > 0 else 0.0


def _kurt(r: np.ndarray) -> float:
    s = np.std(r)
    return float(np.mean(((r - np.mean(r)) / s) ** 4)) if s > 0 else 3.0


def deflated_sharpe(rets: np.ndarray, n_trials: int) -> dict:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado): probability the true
    Sharpe is > 0 after accounting for the number of strategy variants tried.
    Uses the per-day Sharpe, return skew/kurtosis, and an expected-maximum-under-
    -the-null threshold that grows with n_trials. DSR near 1.0 = robust; near
    0.5 or below = likely a multiple-testing artifact."""
    r = np.asarray(rets, dtype=float)
    n = len(r)
    if n < 3:
        return {"deflated_sr_prob": 0.0, "sr0_annual": 0.0, "per_day_sr": 0.0,
                "skew": 0.0, "kurt": 3.0, "n_trials": n_trials}
    sr = annualized_sharpe(r) / sqrt(TRADING_DAYS)   # per-day Sharpe
    sk, ku = _skew(r), _kurt(r)
    # Variance of the Sharpe estimator (proxy for cross-trial variance V).
    var_sr = max((1 - sk * sr + ((ku - 1) / 4.0) * sr * sr) / (n - 1), 1e-12)
    nt = max(int(n_trials), 1)
    if nt == 1:
        sr0 = 0.0
    else:
        sr0 = sqrt(var_sr) * ((1 - _EULER) * _norm_ppf(1 - 1.0 / nt)
                              + _EULER * _norm_ppf(1 - 1.0 / (nt * e)))
    denom = sqrt(max(1 - sk * sr + ((ku - 1) / 4.0) * sr * sr, 1e-12))
    dsr = _norm_cdf((sr - sr0) * sqrt(n - 1) / denom)
    return {
        "deflated_sr_prob": float(dsr),
        "sr0_annual": float(sr0 * sqrt(TRADING_DAYS)),
        "per_day_sr": float(sr),
        "skew": sk, "kurt": ku, "n_trials": nt,
    }


def validate_trades(pnls, dates, initial_capital: float, n_trials: int = 12) -> dict:
    """Bundle the three tests into one verdict over a trade list."""
    rets = daily_returns(pnls, dates, initial_capital)
    sharpe = annualized_sharpe(rets)
    boot = bootstrap_ci(rets)
    p_perm = sign_permutation_p(rets)
    dsr = deflated_sharpe(rets, n_trials)
    # Verdict: real edge if the Sharpe CI stays positive, the permutation p is
    # significant, and the deflated Sharpe survives the multiple-testing haircut.
    robust = (boot["sharpe_ci"][0] > 0 and p_perm < 0.05 and dsr["deflated_sr_prob"] > 0.90)
    return {
        "n_days": int(len(rets)),
        "sharpe": round(sharpe, 2),
        "bootstrap": boot,
        "permutation_p": p_perm,
        "deflated": dsr,
        "verdict": "ROBUST" if robust else "NOT CONVINCING",
    }


def print_validation(label: str, v: dict):
    b = v["bootstrap"]
    d = v["deflated"]
    print(f"\n{'='*64}\n  STATISTICAL VALIDATION: {label}\n{'='*64}")
    print(f"  Trading days:        {v['n_days']}")
    print(f"  Annualized Sharpe:   {v['sharpe']:.2f}")
    print(f"  Sharpe 90% CI:       [{b['sharpe_ci'][0]:.2f}, {b['sharpe_ci'][1]:.2f}]  (median {b['sharpe_median']:.2f})")
    print(f"  P(Sharpe <= 0):      {b['p_sharpe_le_0']:.1%}  (bootstrap resamples that lost the edge)")
    print(f"  Return 90% CI:       [{b['return_ci'][0]:.1%}, {b['return_ci'][1]:.1%}]")
    print(f"  Permutation p-value: {v['permutation_p']:.4f}  (P(mean>0 is chance); want < 0.05)")
    print(f"  Deflated Sharpe:     {d['deflated_sr_prob']:.1%}  P(true SR>0 | {d['n_trials']} trials)  [haircut SR0 {d['sr0_annual']:.2f}]")
    print(f"  Return skew/kurt:    {d['skew']:.2f} / {d['kurt']:.2f}")
    print(f"  VERDICT:             {v['verdict']}")
    print(f"{'='*64}")


def main():
    ap = argparse.ArgumentParser(description="Statistical validation of the ORB edge")
    ap.add_argument("--ticker", default=settings.TICKER)
    ap.add_argument("--start", default=settings.BACKTEST_START)
    ap.add_argument("--end", default=settings.BACKTEST_END)
    ap.add_argument("--capital", type=float, default=settings.BACKTEST_INITIAL_CAPITAL)
    ap.add_argument("--trials", type=int, default=12,
                    help="number of config variants tried (for the deflated-Sharpe haircut)")
    args = ap.parse_args()

    from src.backtest import run_backtest
    summary = run_backtest(ticker=args.ticker, start=args.start, end=args.end,
                           initial_capital=args.capital)
    trades = summary.get("trades")
    if not trades:
        print(f"  No trades to validate: {summary.get('error', 'unknown')}")
        return
    pnls = [t["trade_pnl"] for t in trades]
    dates = [t.get("date", str(t.get("entry_time", ""))[:10]) for t in trades]
    v = validate_trades(pnls, dates, args.capital, n_trials=args.trials)
    print_validation(f"{args.ticker} {args.start}..{args.end} ({len(trades)} trades)", v)


if __name__ == "__main__":
    main()
