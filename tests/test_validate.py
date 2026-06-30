"""Tests for the statistical validation module (pure numpy, no network)."""
import numpy as np

from src.validate import (
    _norm_cdf, _norm_ppf, annualized_sharpe, bootstrap_ci,
    sign_permutation_p, deflated_sharpe, validate_trades,
)


def test_norm_cdf_ppf_roundtrip():
    assert abs(_norm_cdf(0.0) - 0.5) < 1e-9
    assert abs(_norm_ppf(0.975) - 1.959964) < 1e-3
    assert abs(_norm_cdf(1.959964) - 0.975) < 1e-3


def test_sharpe_zero_for_flat():
    assert annualized_sharpe(np.zeros(10)) == 0.0


def test_bootstrap_positive_edge_ci_above_zero():
    rng = np.random.default_rng(1)
    rets = rng.normal(0.001, 0.002, 200)         # strong positive mean vs vol
    b = bootstrap_ci(rets, n=500)
    assert b["sharpe_ci"][0] > 0
    assert b["p_sharpe_le_0"] < 0.10


def test_permutation_significant_for_real_edge():
    rng = np.random.default_rng(2)
    rets = rng.normal(0.001, 0.002, 200)
    assert sign_permutation_p(rets, n=1000) < 0.05


def test_permutation_not_significant_for_noise():
    rng = np.random.default_rng(3)
    rets = rng.normal(0.0, 0.002, 200)           # zero-mean: no edge
    assert sign_permutation_p(rets, n=1000) > 0.05


def test_deflated_sharpe_penalizes_more_trials():
    rng = np.random.default_rng(4)
    rets = rng.normal(0.0005, 0.003, 150)
    d1 = deflated_sharpe(rets, n_trials=1)
    d50 = deflated_sharpe(rets, n_trials=50)
    assert d1["deflated_sr_prob"] >= d50["deflated_sr_prob"]   # more trials -> lower confidence
    assert d50["sr0_annual"] > d1["sr0_annual"]                # haircut threshold grows


def test_validate_bundle_runs():
    rng = np.random.default_rng(5)
    pnls = list(rng.normal(50, 100, 60))
    dates = [f"2026-01-{(i % 27) + 1:02d}" for i in range(60)]
    v = validate_trades(pnls, dates, 10000.0, n_trials=12)
    assert v["n_days"] >= 1
    assert v["verdict"] in ("ROBUST", "NOT CONVINCING")
