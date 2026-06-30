"""
Opening Range Breakout (ORB) Signal Generator.

Pure signal logic — no execution, no side effects.
Takes candle data, returns a signal dict or None.
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from config import settings


@dataclass
class ORBSignal:
    """Represents a trading signal from the ORB strategy."""
    direction: str          # "long" or "short"
    entry_price: float      # breakout price (ORH or ORL)
    stop_price: float       # stop-loss level
    target_price: float     # take-profit level
    or_high: float          # opening range high
    or_low: float           # opening range low
    or_midline: float       # midline of opening range
    range_width: float      # absolute width
    range_pct: float        # width as % of midline
    atr: float | None       # ATR if available
    timestamp: str          # when signal was generated
    # ─── v2 confirmation-filter telemetry (defaults keep v1 construction valid) ───
    vwap_at_entry: float | None = None    # session VWAP at the breakout bar
    rvol_at_entry: float | None = None    # relative volume on the breakout bar
    candle_strength: float | None = None  # directional close position in [0,1]
    filters_passed: str = ""              # comma-separated enabled filters that passed


def calculate_atr(daily_bars: pd.DataFrame, period: int = 14) -> float:
    """Calculate Average True Range from daily OHLC bars."""
    if len(daily_bars) < period + 1:
        return None
    high = daily_bars["high"].values
    low = daily_bars["low"].values
    close = daily_bars["close"].values
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    atr = pd.Series(tr).rolling(window=period).mean().iloc[-1]
    return float(atr)


def compute_opening_range(
    intraday_bars: pd.DataFrame,
    or_minutes: int = None,
) -> dict | None:
    """
    Compute the opening range from intraday 1-min bars.
    Returns dict with or_high, or_low, or_midline, range_width, range_pct.
    """
    or_minutes = or_minutes or settings.OPENING_RANGE_MINUTES

    # Filter to market hours
    market_bars = intraday_bars.between_time("09:30", "15:59")
    if market_bars.empty:
        return None

    # Opening range = first N minutes
    open_time = market_bars.index[0]
    or_end = open_time + pd.Timedelta(minutes=or_minutes)
    or_bars = market_bars[market_bars.index < or_end]

    # Require the FULL opening range. A partial range (e.g. data still arriving
    # on a lagging live feed) computes a different OR than the backtest and can
    # trigger a trade the backtest never took, so skip until all bars are in.
    if len(or_bars) < or_minutes:
        return None

    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    or_midline = (or_high + or_low) / 2
    range_width = or_high - or_low
    range_pct = range_width / or_midline if or_midline > 0 else 0

    return {
        "or_high": or_high,
        "or_low": or_low,
        "or_midline": or_midline,
        "range_width": range_width,
        "range_pct": range_pct,
        "or_end": or_end,
    }


# ─── Confirmation Filters (v2) ────────────────────────────────────────
# These confirm the QUALITY of the first breakout bar. They never change
# which bar is the first breakout, nor the entry/stop/target levels — they
# only decide whether that breakout is taken. With all filters disabled the
# function returns the first breakout unconditionally, identical to v1.

def compute_session_vwap(market_bars: pd.DataFrame) -> pd.Series:
    """
    Cumulative intraday VWAP (typical-price weighted), reset per call.
    `market_bars` is assumed to be a single session, so a plain cumsum is
    the daily reset. Returns a Series aligned to market_bars.index.
    """
    typical = (market_bars["high"] + market_bars["low"] + market_bars["close"]) / 3.0
    cum_vol = market_bars["volume"].cumsum()
    cum_pv = (typical * market_bars["volume"]).cumsum()
    # Guard against zero cumulative volume (no trades yet) -> NaN, not div-by-zero.
    return cum_pv / cum_vol.where(cum_vol > 0, np.nan)


def compute_rvol(market_bars: pd.DataFrame, idx, lookback: int) -> float | None:
    """
    Relative volume on the bar at `idx`: its volume / mean volume of up to
    `lookback` immediately-prior bars in the session. None if not computable.
    """
    try:
        pos = market_bars.index.get_loc(idx)
    except KeyError:
        return None
    if not isinstance(pos, int) or pos == 0:
        return None
    prior = market_bars["volume"].iloc[max(0, pos - lookback):pos]
    if len(prior) == 0:
        return None
    avg = float(prior.mean())
    if avg <= 0:
        return None
    return float(market_bars["volume"].iloc[pos] / avg)


def compute_candle_strength(row, direction: str) -> float | None:
    """
    Directional close position within the bar's range, in [0, 1].
    long  -> (close - low) / (high - low)   (1.0 = closed at the high)
    short -> (high - close) / (high - low)  (1.0 = closed at the low)
    None for a zero-range bar (strength undefined).
    """
    rng = row["high"] - row["low"]
    if rng <= 0:
        return None
    if direction == "long":
        return float((row["close"] - row["low"]) / rng)
    return float((row["high"] - row["close"]) / rng)


def evaluate_filters(
    direction: str,
    entry_price: float,
    row,
    idx,
    vwap_series: pd.Series,
    market_bars: pd.DataFrame,
    use_vwap: bool,
    use_rvol: bool,
    rvol_threshold: float,
    rvol_lookback: int,
    use_candle: bool,
    candle_pct: float,
) -> tuple[bool, dict]:
    """
    Evaluate the three confirmation filters on the breakout bar.

    Returns (all_enabled_passed, telemetry) where telemetry always carries the
    measured values (vwap/rvol/candle_strength) plus the comma-separated list
    of enabled filters that passed — even when a filter is disabled, so the
    trade log captures the context for later analysis.
    A filter whose value can't be computed FAILS when enabled (conservative).
    """
    vwap_val = None
    if idx in vwap_series.index:
        v = vwap_series.loc[idx]
        vwap_val = float(v) if pd.notna(v) else None
    rvol_val = compute_rvol(market_bars, idx, rvol_lookback)
    candle_val = compute_candle_strength(row, direction)

    passed = []
    ok = True

    if use_vwap:
        if vwap_val is None:
            ok = False
        elif direction == "long" and entry_price > vwap_val:
            passed.append("vwap")
        elif direction == "short" and entry_price < vwap_val:
            passed.append("vwap")
        else:
            ok = False

    if use_rvol:
        if rvol_val is not None and rvol_val >= rvol_threshold:
            passed.append("rvol")
        else:
            ok = False

    if use_candle:
        # close must sit in the top (long) / bottom (short) `candle_pct` of the bar
        if candle_val is not None and candle_val >= (1.0 - candle_pct):
            passed.append("candle_strength")
        else:
            ok = False

    telemetry = {
        "vwap_at_entry": vwap_val,
        "rvol_at_entry": rvol_val,
        "candle_strength": candle_val,
        "filters_passed": ",".join(passed),
    }
    return ok, telemetry


def generate_signal(
    intraday_bars: pd.DataFrame,
    atr: float | None = None,
    or_minutes: int = None,
    rr_ratio: float = None,
    stop_mode: str = None,
    min_range_pct: float = None,
    filter_vwap: bool = None,
    filter_rvol: bool = None,
    rvol_threshold: float = None,
    filter_candle: bool = None,
    candle_pct: float = None,
    entry_cutoff: str | None = None,
    prev_close: float | None = None,
    filter_regime: bool = None,
    regime_gap_max: float = None,
    breakout_confirm: str = None,
) -> ORBSignal | None:
    """
    Core signal generator. Scans post-opening-range bars for breakout.

    Returns an ORBSignal if a breakout occurs AND passes all enabled
    confirmation filters, None otherwise.
    Only considers the FIRST breakout of the day (max 1 signal per session):
    if that breakout fails an enabled filter, no trade is taken for the day.

    Backward compatibility: with filter_vwap/filter_rvol/filter_candle all
    False, the filters never gate, so output is identical to v1.
    """
    or_minutes = or_minutes or settings.OPENING_RANGE_MINUTES
    rr_ratio = rr_ratio or settings.REWARD_RISK_RATIO
    stop_mode = stop_mode or settings.STOP_MODE
    min_range_pct = min_range_pct or settings.MIN_RANGE_PCT
    # Filter toggles/params: None -> fall back to config (default OFF; see settings.py).
    filter_vwap = settings.FILTER_VWAP_ENABLED if filter_vwap is None else filter_vwap
    filter_rvol = settings.FILTER_RVOL_ENABLED if filter_rvol is None else filter_rvol
    rvol_threshold = settings.FILTER_RVOL_THRESHOLD if rvol_threshold is None else rvol_threshold
    filter_candle = settings.FILTER_CANDLE_STRENGTH_ENABLED if filter_candle is None else filter_candle
    candle_pct = settings.FILTER_CANDLE_STRENGTH_PCT if candle_pct is None else candle_pct
    filter_regime = settings.FILTER_REGIME_GAP_ENABLED if filter_regime is None else filter_regime
    regime_gap_max = settings.FILTER_REGIME_GAP_MAX_PCT if regime_gap_max is None else regime_gap_max
    breakout_confirm = (breakout_confirm or settings.BREAKOUT_CONFIRM or "wick").lower()

    # Step 1: compute opening range
    orng = compute_opening_range(intraday_bars, or_minutes)
    if orng is None:
        return None

    # Step 2: filter — skip if range is too narrow (false breakout territory)
    if orng["range_pct"] < min_range_pct:
        return None

    # Regime gate: skip chaotic overnight-gap days (optional, needs prev_close).
    market_bars = intraday_bars.between_time("09:30", "15:44")
    if filter_regime and prev_close and not market_bars.empty:
        today_open = float(market_bars.iloc[0]["open"])
        gap = (today_open - prev_close) / prev_close if prev_close else 0.0
        if abs(gap) > regime_gap_max:
            return None

    # Step 3: scan post-OR bars for first breakout
    post_or = market_bars[market_bars.index >= orng["or_end"]]

    # Session VWAP base (full session up to force-close cutoff), computed once.
    vwap_series = compute_session_vwap(market_bars)

    # Live runs once near 09:40 and only sees breakouts up to that point. When an
    # entry_cutoff is given, stop scanning past it so the backtest takes the same
    # early breakouts the live bot can actually catch (not an all-day first
    # breakout). None = scan the whole session (for analysis tools).
    cutoff_t = pd.to_datetime(entry_cutoff).time() if entry_cutoff else None

    for idx, row in post_or.iterrows():
        if cutoff_t is not None and idx.time() > cutoff_t:
            break
        if breakout_confirm == "close":
            # require the bar to CLOSE beyond the opening range (fewer false breakouts)
            is_long = row["close"] > orng["or_high"]
            is_short = row["close"] < orng["or_low"]
        else:
            is_long = row["high"] > orng["or_high"]
            is_short = row["low"] < orng["or_low"]
        if not (is_long or is_short):
            continue

        # First breakout decides the day. Long takes priority on outside bars
        # (matches v1 evaluation order).
        direction = "long" if is_long else "short"
        if direction == "long":
            entry = orng["or_high"]
            if stop_mode == "atr" and atr is not None:
                stop = entry - (atr * settings.ATR_STOP_MULTIPLIER)
            else:
                stop = orng["or_midline"]
            risk = entry - stop
            target = entry + (risk * rr_ratio)
        else:
            entry = orng["or_low"]
            if stop_mode == "atr" and atr is not None:
                stop = entry + (atr * settings.ATR_STOP_MULTIPLIER)
            else:
                stop = orng["or_midline"]
            risk = stop - entry
            target = entry - (risk * rr_ratio)

        # Confirmation gate on the first breakout bar.
        passed, telemetry = evaluate_filters(
            direction, entry, row, idx, vwap_series, market_bars,
            use_vwap=filter_vwap,
            use_rvol=filter_rvol,
            rvol_threshold=rvol_threshold,
            rvol_lookback=settings.FILTER_RVOL_LOOKBACK,
            use_candle=filter_candle,
            candle_pct=candle_pct,
        )
        if not passed:
            return None  # unconfirmed first breakout -> no trade today

        return ORBSignal(
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            or_high=orng["or_high"],
            or_low=orng["or_low"],
            or_midline=orng["or_midline"],
            range_width=orng["range_width"],
            range_pct=orng["range_pct"],
            atr=atr,
            timestamp=str(idx),
            vwap_at_entry=telemetry["vwap_at_entry"],
            rvol_at_entry=telemetry["rvol_at_entry"],
            candle_strength=telemetry["candle_strength"],
            filters_passed=telemetry["filters_passed"],
        )

    return None


def simulate_trade(
    signal: ORBSignal,
    intraday_bars: pd.DataFrame,
) -> dict:
    """
    Simulate a trade outcome for backtesting.
    Walks forward through bars after entry to determine if target or stop hit first.
    Returns trade result dict.
    """
    # Cap the walk at the force-close time: the live bot flattens at 15:45, so
    # giving backtest trades until ~16:00 to hit target/stop overstates results.
    day = intraday_bars.between_time("09:30", settings.FORCE_CLOSE_TIME)
    post_entry = day[day.index >= pd.Timestamp(signal.timestamp)]

    for idx, row in post_entry.iterrows():
        if signal.direction == "long":
            # Check stop first (conservative — assume worst case)
            if row["low"] <= signal.stop_price:
                pnl = signal.stop_price - signal.entry_price
                return _trade_result(signal, idx, signal.stop_price, pnl, "stop")
            if row["high"] >= signal.target_price:
                pnl = signal.target_price - signal.entry_price
                return _trade_result(signal, idx, signal.target_price, pnl, "target")
        else:  # short
            if row["high"] >= signal.stop_price:
                pnl = signal.entry_price - signal.stop_price
                return _trade_result(signal, idx, signal.stop_price, pnl, "stop")
            if row["low"] <= signal.target_price:
                pnl = signal.entry_price - signal.target_price
                return _trade_result(signal, idx, signal.target_price, pnl, "target")

    # EOD force close at last available price
    if not post_entry.empty:
        last_price = float(post_entry.iloc[-1]["close"])
        if signal.direction == "long":
            pnl = last_price - signal.entry_price
        else:
            pnl = signal.entry_price - last_price
        return _trade_result(signal, post_entry.index[-1], last_price, pnl, "eod_close")

    return _trade_result(signal, signal.timestamp, signal.entry_price, 0, "no_data")


def _trade_result(signal: ORBSignal, exit_time, exit_price, pnl, exit_reason) -> dict:
    return {
        "direction": signal.direction,
        "entry_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "exit_price": exit_price,
        "exit_time": str(exit_time),
        "entry_time": signal.timestamp,
        "pnl_per_share": round(pnl, 4),
        "exit_reason": exit_reason,
        "or_high": signal.or_high,
        "or_low": signal.or_low,
        "range_pct": round(signal.range_pct, 6),
        "atr": signal.atr,
        # v2 confirmation-filter telemetry (carried through for logging/analysis)
        "vwap_at_entry": signal.vwap_at_entry,
        "rvol_at_entry": signal.rvol_at_entry,
        "candle_strength": signal.candle_strength,
        "filters_passed": signal.filters_passed,
    }
