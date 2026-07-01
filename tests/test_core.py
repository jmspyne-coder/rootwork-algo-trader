"""Unit tests for the safety core and the pure logic the live bot depends on.

These cover the exact failure modes the audit surfaced: round-trip pairing,
per-trade P&L from real fills, the cross-day risk derivation, the daily-loss
halt, capital-capped sizing, opening-range completeness, and the live
freshness/forming-bar guards.
"""
import pandas as pd
import pytz
from datetime import datetime

from src.reconcile import reconstruct_round_trips, match_exits
from src.risk_manager import (
    compute_history_state, can_trade, calculate_position_size, RiskState,
    consec_loss_pause_active, is_scale_anomaly,
)
from src.alpaca_client import verify_bracket_legs, to_effective_equity
from src.orb_signal import compute_opening_range, generate_signal
from src.execute_orb import _age_min, _drop_forming_bar


# ─── reconcile ────────────────────────────────────────────────────────
def test_round_trip_long_target():
    fills = [
        {"side": "buy", "price": 100.0, "qty": 10, "time": "t1", "type": "market"},
        {"side": "sell", "price": 102.0, "qty": 10, "time": "t2", "type": "limit"},
    ]
    trips = reconstruct_round_trips(fills)
    assert len(trips) == 1
    assert trips[0]["direction"] == "long"
    assert trips[0]["exit_reason"] == "target"
    assert trips[0]["entry_price"] == 100.0 and trips[0]["exit_price"] == 102.0


def test_round_trip_short_stop():
    fills = [
        {"side": "sell", "price": 50.0, "qty": 5, "time": "t1", "type": "market"},
        {"side": "buy", "price": 51.0, "qty": 5, "time": "t2", "type": "stop"},
    ]
    trips = reconstruct_round_trips(fills)
    assert trips[0]["direction"] == "short" and trips[0]["exit_reason"] == "stop"


def test_match_exits_uses_fill_price_and_qty():
    rows = [{"trade_id": "x", "direction": "long", "shares": 10, "equity_before": 1000.0}]
    trips = [{"entry_price": 100.0, "exit_price": 103.0, "qty": 10,
              "exit_time": "t", "exit_reason": "target"}]
    upd = match_exits(rows, trips)
    assert upd[0]["entry_price"] == 100.0
    assert upd[0]["trade_pnl"] == 30.0          # (103 - 100) * 10
    assert upd[0]["equity_after"] == 1030.0


# ─── risk ─────────────────────────────────────────────────────────────
def test_history_peak_and_streak():
    hist = [
        {"summary_date": "d1", "daily_pnl": 100, "equity_end": 10100},
        {"summary_date": "d2", "daily_pnl": -50, "equity_end": 10050},
        {"summary_date": "d3", "daily_pnl": -50, "equity_end": 10000},
    ]
    peak, consec = compute_history_state(hist)
    assert peak == 10100 and consec == 2


def test_flat_day_resets_streak():
    hist = [
        {"summary_date": "d1", "daily_pnl": -50, "equity_end": 9950},
        {"summary_date": "d2", "daily_pnl": 0.0, "equity_end": 9950},
    ]
    _, consec = compute_history_state(hist)
    assert consec == 0


def test_can_trade_daily_loss_halt():
    st = RiskState(peak_equity=10000, current_equity=9500, daily_starting_equity=10000,
                   daily_pnl=-500, consecutive_losses=0, trades_today=0,
                   is_halted=False, halt_reason=None)  # -5% > 4% limit
    ok, reason = can_trade(st)
    assert not ok and "Daily loss" in reason


def test_can_trade_ok():
    st = RiskState(peak_equity=10000, current_equity=10000, daily_starting_equity=10000,
                   daily_pnl=0.0, consecutive_losses=0, trades_today=0,
                   is_halted=False, halt_reason=None)
    ok, _ = can_trade(st)
    assert ok


def test_can_trade_blocked_by_manual_kill():
    # The manual kill switch surfaces as a pre-set is_halted state; can_trade
    # must refuse regardless of P&L.
    st = RiskState(peak_equity=10000, current_equity=10000, daily_starting_equity=10000,
                   daily_pnl=0.0, consecutive_losses=0, trades_today=0,
                   is_halted=True, halt_reason="manual_kill")
    ok, reason = can_trade(st)
    assert not ok and "manual_kill" in reason


def test_scale_anomaly_detects_contamination():
    # The exact 2026-07-01 contamination: $100k peak vs $5.1k effective equity.
    assert is_scale_anomaly(100101.0, 5101.0, 0.60) is True
    # A real max-drawdown-sized gap (~10%) is NOT flagged.
    assert is_scale_anomaly(10000.0, 9000.0, 0.60) is False
    # Right at a normal small loss.
    assert is_scale_anomaly(5200.0, 5100.0, 0.60) is False
    # Degenerate inputs are safe.
    assert is_scale_anomaly(0.0, 5000.0, 0.60) is False
    assert is_scale_anomaly(100000.0, 0.0, 0.60) is False


def test_can_trade_blocked_by_daily_floor():
    st = RiskState(peak_equity=10000, current_equity=5000, daily_starting_equity=10000,
                   daily_pnl=-5000, consecutive_losses=0, trades_today=0,
                   is_halted=True, halt_reason="daily_floor_breach")
    ok, reason = can_trade(st)
    assert not ok and "daily_floor_breach" in reason


# ─── consecutive-loss pause (2 trading days) ─────────────────────────
def test_consec_loss_pause_active_window():
    # A consecutive_losses halt on the last day keeps the pause active.
    hist = [
        {"summary_date": "d1", "halt_reason": None},
        {"summary_date": "d2", "halt_reason": "consecutive_losses"},
    ]
    assert consec_loss_pause_active(hist, pause_days=2) is True
    # Two clean days after the halt -> pause has expired.
    hist2 = hist + [
        {"summary_date": "d3", "halt_reason": "consec_loss_pause"},
        {"summary_date": "d4", "halt_reason": None},
    ]
    assert consec_loss_pause_active(hist2, pause_days=2) is False
    assert consec_loss_pause_active([], pause_days=2) is False
    assert consec_loss_pause_active(hist, pause_days=0) is False


# ─── simulated paper equity ──────────────────────────────────────────
def test_to_effective_equity():
    # Live: real equity passes through unchanged.
    assert to_effective_equity(4823.0, "live") == 4823.0
    # Paper: pre-funded ~$100k maps to the $5k simulated start and compounds.
    assert to_effective_equity(100000.0, "paper") == 5000.0
    assert to_effective_equity(100500.0, "paper") == 5500.0   # +$500 paper P&L
    assert to_effective_equity(99000.0, "paper") == 4000.0     # -$1000 paper P&L


# ─── orphaned-leg guard ──────────────────────────────────────────────
class _FakeOrder:
    def __init__(self, legs):
        self.legs = legs


def test_verify_bracket_legs():
    ok, _ = verify_bracket_legs(_FakeOrder(["tp", "sl"]))
    assert ok
    bad, detail = verify_bracket_legs(_FakeOrder([]))
    assert not bad and "UNPROTECTED" in detail
    none_legs, _ = verify_bracket_legs(_FakeOrder(None))
    assert not none_legs


# ─── paper-trading slippage tracker ──────────────────────────────────
def test_realized_slippage_bps():
    from src.paper_stats import realized_slippage_bps, summarize
    # Long filled 10 bps above the intended breakout = 10 bps cost.
    assert round(realized_slippage_bps("long", 100.0, 100.10), 1) == 10.0
    # Short filled below intended = positive cost; price improvement = negative.
    assert round(realized_slippage_bps("short", 100.0, 99.90), 1) == 10.0
    assert round(realized_slippage_bps("long", 100.0, 99.95), 1) == -5.0
    assert realized_slippage_bps("long", 0, 100) is None
    s = summarize([
        {"direction": "long", "or_high": 100.0, "or_low": 99.0, "entry_price": 100.10,
         "trade_pnl": 20.0, "trade_date": "d1"},
        {"direction": "short", "or_high": 51.0, "or_low": 50.0, "entry_price": 49.95,
         "trade_pnl": -10.0, "trade_date": "d2"},
    ])
    assert s["trades"] == 2 and s["days_traded"] == 2
    assert s["cum_pnl"] == 10.0 and s["avg_slippage_bps"] == 10.0


# ─── intraday risk monitor (daily-loss bumper) ───────────────────────
def test_risk_monitor_classify():
    from src.risk_monitor import classify
    warn, stop, floor = 0.0225, 0.03, 0.50
    assert classify(-300, 10000, warn, stop, floor) == "stop"    # exactly at 3%
    assert classify(-350, 10000, warn, stop, floor) == "stop"    # past 3%, below floor
    assert classify(-4999, 10000, warn, stop, floor) == "stop"   # just under the 50% floor
    assert classify(-5000, 10000, warn, stop, floor) == "floor"  # exactly at 50% floor
    assert classify(-8000, 10000, warn, stop, floor) == "floor"  # catastrophic
    assert classify(-250, 10000, warn, stop, floor) == "warn"    # 2.5%, past the 2.25% warn
    assert classify(-225, 10000, warn, stop, floor) == "warn"    # exactly at 2.25%
    assert classify(-100, 10000, warn, stop, floor) == "ok"      # 1%
    assert classify(50, 10000, warn, stop, floor) == "ok"        # up on the day
    assert classify(-300, 0, warn, stop, floor) == "unknown"     # no usable baseline


def test_position_size_cap_binds():
    uncapped = calculate_position_size(100000, 600.0, 598.5)            # stop dist 1.5
    capped = calculate_position_size(100000, 600.0, 598.5, capital_cap=50000)
    assert capped < uncapped
    assert capped == int(50000 / 600.0)


def test_position_size_zero_on_bad_stop():
    assert calculate_position_size(100000, 600.0, 600.0) == 0           # zero stop distance


# ─── signal ───────────────────────────────────────────────────────────
def _bars(rows, start="2026-06-30 09:30"):
    idx = pd.date_range(start=start, periods=len(rows), freq="1min", tz="US/Eastern")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def test_opening_range_requires_full_window():
    four = _bars([[100, 100.5, 99.5, 100, 1000]] * 4)
    assert compute_opening_range(four, or_minutes=5) is None            # partial -> skip
    five = _bars([[100, 100.5, 99.5, 100, 1000]] * 5)
    rng = compute_opening_range(five, or_minutes=5)
    assert rng is not None and rng["or_high"] == 100.5 and rng["or_low"] == 99.5


def test_generate_signal_long_breakout():
    rows = [[100, 100.2, 99.8, 100, 1000]] * 5 + [[100.2, 101.0, 100.2, 100.9, 3000]]
    sig = generate_signal(_bars(rows), atr=1.0, min_range_pct=0.0, filter_candle=False)
    assert sig is not None
    assert sig.direction == "long"
    assert sig.entry_price == 100.2                                     # OR high


def test_regime_gate_skips_big_overnight_gap():
    rows = [[100, 100.2, 99.8, 100, 1000]] * 5 + [[100.2, 101.0, 100.2, 100.9, 3000]]
    df = _bars(rows)                                                    # session open = 100
    # prev close 90 -> +11% gap -> skipped when regime gate on
    assert generate_signal(df, atr=1.0, min_range_pct=0.0, filter_candle=False,
                           prev_close=90.0, filter_regime=True, regime_gap_max=0.015) is None
    # prev close 99.6 -> 0.4% gap -> trades
    assert generate_signal(df, atr=1.0, min_range_pct=0.0, filter_candle=False,
                           prev_close=99.6, filter_regime=True, regime_gap_max=0.015) is not None


def test_breakout_confirm_close_vs_wick():
    # breakout bar wicks to 101 (> OR high 100.2) but CLOSES at 100.0 (inside OR)
    rows = [[100, 100.2, 99.8, 100, 1000]] * 5 + [[100.1, 101.0, 100.0, 100.0, 3000]]
    df = _bars(rows)
    assert generate_signal(df, atr=1.0, min_range_pct=0.0, filter_candle=False,
                           breakout_confirm="wick") is not None       # wick penetration counts
    assert generate_signal(df, atr=1.0, min_range_pct=0.0, filter_candle=False,
                           breakout_confirm="close") is None          # close inside -> no trade


# ─── gap-fill signal + router ─────────────────────────────────────────
from src.gap_signal import (
    detect_gap, min_gap_threshold, route_strategy,
    generate_gap_fill_signal, simulate_gap_fill_trade,
)


def test_detect_gap_and_threshold():
    assert round(detect_gap(100.0, 100.8), 4) == 0.008
    assert detect_gap(0, 100) is None
    # max(0.5*ATR/prevclose, 0.3%) -> ATR component 0.5*1/100 = 0.5% wins over floor
    assert round(min_gap_threshold(1.0, 100.0, 0.5, 0.003), 4) == 0.005
    # tiny ATR -> the 0.3% floor governs
    assert round(min_gap_threshold(0.1, 100.0, 0.5, 0.003), 4) == 0.003


def test_route_strategy_three_zones():
    assert route_strategy(0.02, 0.005, 0.015) == "skip"      # 2% regime event
    assert route_strategy(0.008, 0.005, 0.015) == "gap_fill" # meaningful gap
    assert route_strategy(-0.008, 0.005, 0.015) == "gap_fill"# fade works both ways
    assert route_strategy(0.001, 0.005, 0.015) == "orb"      # normal day
    assert route_strategy(None, 0.005, 0.015) == "orb"       # no prior close


def _gap_session(rows, start="2026-07-01 09:30"):
    idx = pd.date_range(start=start, periods=len(rows), freq="1min", tz="US/Eastern")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def test_gap_up_fades_short_and_hits_target():
    # prev_close 100, open 100.8 (+0.8% gap, in-zone), ATR 1.0 -> short, target 98.8
    bars = _gap_session([
        [100.8, 100.9, 100.5, 100.6, 1000],
        [100.6, 100.7, 99.0, 99.2, 2000],
        [99.2, 99.3, 98.5, 98.7, 2000],   # low 98.5 <= target 98.8
    ])
    sig = generate_gap_fill_signal(bars, atr=1.0, prev_close=100.0)
    assert sig is not None and sig.direction == "short"
    assert sig.entry_price == 100.8 and sig.stop_price == 101.8 and sig.target_price == 98.8
    res = simulate_gap_fill_trade(sig, bars)
    assert res["exit_reason"] == "target" and round(res["pnl_per_share"], 2) == 2.00


def test_gap_down_fades_long():
    bars = _gap_session([[99.2, 99.4, 99.1, 99.3, 1000]])
    sig = generate_gap_fill_signal(bars, atr=1.0, prev_close=100.0)  # -0.8% gap
    assert sig is not None and sig.direction == "long"
    assert sig.entry_price == 99.2 and sig.stop_price == 98.2 and sig.target_price == 101.2


def test_gap_out_of_zone_and_filters():
    normal = _gap_session([[100.1, 100.2, 100.0, 100.1, 1000]])  # +0.1% -> not gap-fill
    assert generate_gap_fill_signal(normal, atr=1.0, prev_close=100.0) is None
    regime = _gap_session([[102.0, 102.1, 101.9, 102.0, 1000]])  # +2% -> skip zone
    assert generate_gap_fill_signal(regime, atr=1.0, prev_close=100.0) is None
    gapup = _gap_session([[100.8, 100.9, 100.5, 100.6, 1000]])
    # direction filter "down" only fades gap-downs, so a gap-up yields nothing
    assert generate_gap_fill_signal(gapup, atr=1.0, prev_close=100.0, direction_filter="down") is None
    # ATR required
    assert generate_gap_fill_signal(gapup, atr=None, prev_close=100.0) is None


# ─── live freshness guards ────────────────────────────────────────────
def test_drop_forming_bar():
    et = pytz.timezone("US/Eastern")
    now = et.localize(datetime(2026, 6, 30, 9, 40, 30))
    df = _bars([[1, 1, 1, 1, 1]] * 12)                                  # 09:30..09:41
    out = _drop_forming_bar(df, now)
    assert out.index[-1].strftime("%H:%M") == "09:39"                   # forming 09:40 dropped


def test_age_min():
    et = pytz.timezone("US/Eastern")
    now = et.localize(datetime(2026, 6, 30, 9, 40, 0))
    age = _age_min("2026-06-30 09:35:00-04:00", now)
    assert 4.9 < age < 5.1
