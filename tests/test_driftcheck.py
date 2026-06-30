"""Tests for the drift-check pure comparison logic."""
from src.driftcheck import realized_slippage_bps, summarize


def test_slippage_long_paid_up():
    assert round(realized_slippage_bps("long", 100.0, 100.5), 1) == 50.0   # filled above level


def test_slippage_short_paid_up():
    assert round(realized_slippage_bps("short", 100.0, 99.5), 1) == 50.0   # filled below level


def test_slippage_improvement_is_negative():
    assert realized_slippage_bps("long", 100.0, 99.9) < 0


def test_summarize_flags_high_slippage_and_winrate_gap():
    rows = [
        {"traded_live": True, "traded_bt": True, "slippage_bps": 30, "pnl_ps_live": -1, "pnl_ps_bt": 1},
        {"traded_live": True, "traded_bt": True, "slippage_bps": 28, "pnl_ps_live": -1, "pnl_ps_bt": 1},
    ]
    s = summarize(rows, modeled_bps=1.5)
    assert s["n_agreed"] == 2
    assert any("slippage" in f for f in s["flags"])
    assert any("win rate" in f for f in s["flags"])


def test_summarize_clean_has_no_flags():
    rows = [
        {"traded_live": True, "traded_bt": True, "slippage_bps": 1.0, "pnl_ps_live": 1, "pnl_ps_bt": 1},
        {"traded_live": True, "traded_bt": True, "slippage_bps": 0.5, "pnl_ps_live": 1, "pnl_ps_bt": -1},
    ]
    s = summarize(rows, modeled_bps=1.5)
    assert s["flags"] == []
