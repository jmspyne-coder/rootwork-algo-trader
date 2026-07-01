"""
Risk Management Module.

Enforces all risk controls before and during trading.
This is the module that keeps you from blowing up.
"""
from dataclasses import dataclass
from datetime import datetime
import pytz
from config import settings
from src.costs import round_trip_cost_per_share


@dataclass
class RiskState:
    """Tracks risk state across the trading session."""
    peak_equity: float
    current_equity: float
    daily_starting_equity: float
    daily_pnl: float
    consecutive_losses: int
    trades_today: int
    is_halted: bool
    halt_reason: str | None

    @property
    def current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    @property
    def daily_loss_pct(self) -> float:
        if self.daily_starting_equity <= 0:
            return 0
        return -self.daily_pnl / self.daily_starting_equity if self.daily_pnl < 0 else 0


def _mode() -> str:
    return "paper" if settings.ALPACA_PAPER else "live"


def _today_et() -> str:
    return datetime.now(pytz.timezone("US/Eastern")).date().isoformat()


def compute_history_state(history: list[dict]) -> tuple[float, int]:
    """Derive cross-day risk quantities from closed-day history (oldest first).

    Returns (peak_equity, consecutive_losing_days):
    - peak_equity: the highest end-of-day equity ever recorded.
    - consecutive_losing_days: the trailing run of days with negative P&L.

    A flat or winning day (pnl >= 0) breaks the streak. This is what kills
    the old deadlock: after a consecutive-loss halt, the halted day trades
    nothing, lands flat, and the streak resets, so the halt is a one-day
    cooldown rather than a permanent lock.
    """
    peak = 0.0
    for h in history:
        eq = h.get("equity_end")
        if eq is not None and eq > peak:
            peak = eq
    consec = 0
    for h in reversed(history):
        pnl = h.get("daily_pnl")
        if pnl is not None and pnl < 0:
            consec += 1
        else:
            break
    return peak, consec


def is_scale_anomaly(peak_equity: float, current_equity: float, threshold: float) -> bool:
    """True if the peak-vs-current gap is too large to be a real trading loss.

    Real drawdown is bounded by the 10% max-drawdown halt (trading stops there),
    so an implied drawdown past `threshold` (default 60%) is not a loss — it is
    data contamination: a scale change (e.g. a $100k paper row vs $5k simulated
    equity), a bad cached row, or an un-modeled deposit/withdrawal. The caller
    resets the peak instead of false-halting on it. See load_risk_state.
    """
    if peak_equity <= 0 or current_equity <= 0:
        return False
    return (peak_equity - current_equity) / peak_equity > threshold


def consec_loss_pause_active(history: list[dict], pause_days: int) -> bool:
    """True if a consecutive-loss halt occurred within the last `pause_days`
    trading days, so trading should stay paused.

    When the streak halt fires on day D, EOD records that day's summary with
    halt_reason 'consecutive_losses'. This keeps trading paused for the next
    `pause_days` sessions: while any of the last `pause_days` history rows still
    carries that reason, the pause holds, then it auto-clears.
    """
    if pause_days <= 0 or not history:
        return False
    recent = history[-pause_days:]
    return any(h.get("halt_reason") == "consecutive_losses" for h in recent)


def _fetch_state_with_retry(mode: str, attempts: int = 3):
    """Read history + cache from MotherDuck with a bounded retry, so a single
    transient blip does not burn the whole trading day by failing closed."""
    import time
    last = None
    for i in range(attempts):
        try:
            from src.trade_logger import fetch_daily_history, read_risk_cache
            return fetch_daily_history(mode), read_risk_cache(mode)
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(0.75 * (i + 1))
    raise last


def load_risk_state(equity: float | None = None, mode: str | None = None) -> RiskState:
    """Reconstruct risk state from MotherDuck (the system of record).

    Cross-day quantities (peak equity, consecutive losing days) are derived
    from algo_daily_summary; intraday counters (daily P&L, trades today,
    halt flags) come from the cached algo_risk_state row when it is today's.
    The daily-P&L baseline is anchored to the PRIOR session's close, not to
    current equity, so the daily-loss halt still works when pre_market did not
    run. A max-drawdown halt is a sticky latch that does not auto-clear.

    FAIL CLOSED: if equity is unknown/non-positive or MotherDuck cannot be
    reached (after retries), return a halted state. A risk controller that
    cannot read its own state, or does not know account equity, must not trade.
    """
    mode = mode or _mode()
    eq = equity or 0.0

    # Unknown or non-positive equity == we do not know the account. Do not trade.
    if equity is None or eq <= 0:
        print(f"  [risk] equity unknown/non-positive ({equity}) — FAILING CLOSED (no trading).")
        return RiskState(
            peak_equity=eq, current_equity=eq, daily_starting_equity=eq,
            daily_pnl=0.0, consecutive_losses=0, trades_today=0,
            is_halted=True, halt_reason="equity_unavailable",
        )

    try:
        history, cache = _fetch_state_with_retry(mode)
    except Exception as e:
        print(f"  [risk] MotherDuck unavailable after retries — FAILING CLOSED (no trading): {e}")
        return RiskState(
            peak_equity=eq, current_equity=eq, daily_starting_equity=eq,
            daily_pnl=0.0, consecutive_losses=0, trades_today=0,
            is_halted=True, halt_reason="risk_state_unavailable",
        )

    peak_hist, consec = compute_history_state(history)
    peak_equity = max(peak_hist, eq)
    prior_equity_end = history[-1]["equity_end"] if history else None

    today = _today_et()
    if cache and str(cache.get("as_of_date")) == today:
        daily_starting = cache["daily_starting_equity"] or (prior_equity_end or eq)
        daily_pnl = cache["daily_pnl"] or 0.0
        trades_today = cache["trades_today"] or 0
        is_halted = bool(cache["is_halted"])
        halt_reason = cache["halt_reason"]
    else:
        # No today-dated cache (pre_market did not run, or this is the first run
        # today). Anchor the baseline to the prior session close, NOT to current
        # equity — using current equity would zero out the day's P&L and silently
        # disable the daily-loss halt.
        daily_starting = prior_equity_end if prior_equity_end is not None else eq
        daily_pnl, trades_today = 0.0, 0
        is_halted, halt_reason = False, None

    # Consecutive-loss pause: after a 5-losing-day halt, stay paused for
    # CONSEC_LOSS_PAUSE_DAYS trading days, then auto-resume. Derived from history
    # so it needs no extra state. Does not override a same-day halt already set.
    if not is_halted and consec_loss_pause_active(history, settings.CONSEC_LOSS_PAUSE_DAYS):
        is_halted, halt_reason = True, "consec_loss_pause"

    # Sticky drawdown latch: a max-drawdown halt requires manual review and must
    # NOT auto-clear overnight, so carry it regardless of the cache date.
    if cache and cache.get("halt_reason") == "max_drawdown" and bool(cache.get("is_halted")):
        is_halted, halt_reason = True, "max_drawdown"

    # Sticky MANUAL kill switch: an on-demand halt the operator flips by hand
    # (src/killswitch.py). Like the drawdown latch it persists across days and
    # does NOT auto-clear — only an explicit resume clears it. This is the
    # human-in-the-loop stop: nothing trades until the operator re-authorizes.
    if cache and cache.get("halt_reason") == "manual_kill" and bool(cache.get("is_halted")):
        is_halted, halt_reason = True, "manual_kill"

    # Sticky CATASTROPHIC daily-floor breach: the day's loss hit the absolute
    # floor (default 50% of the day's starting equity). Unlike the routine 3%
    # daily stop (which auto-clears next day), this is a catastrophe latch that
    # requires a manual resume before trading can restart.
    if cache and cache.get("halt_reason") == "daily_floor_breach" and bool(cache.get("is_halted")):
        is_halted, halt_reason = True, "daily_floor_breach"

    # Scale-anomaly guard (runs LAST so it overrides a contamination-caused latch).
    # An implied drawdown past SCALE_ANOMALY_DRAWDOWN_PCT cannot be a real loss
    # (the 10% max-drawdown halt stops trading long before), so it is a stale/scale
    # artifact — e.g. a $100k paper row measured against $5k simulated equity, which
    # is exactly what false-halted the bot on 2026-07-01. Reset the peak to current
    # so it cannot false-halt, and clear a max_drawdown latch that this contamination
    # produced. A genuine max_drawdown latch (~10%) is well under the threshold and
    # is left intact.
    if is_scale_anomaly(peak_equity, eq, settings.SCALE_ANOMALY_DRAWDOWN_PCT):
        print(f"  [risk] SCALE ANOMALY: peak ${peak_equity:,.0f} vs equity ${eq:,.0f} "
              f"(> {settings.SCALE_ANOMALY_DRAWDOWN_PCT:.0%} implied drawdown) — treating as "
              f"data contamination, resetting peak to equity. REVIEW the state tables.")
        peak_equity = eq
        if halt_reason == "max_drawdown":
            is_halted, halt_reason = False, None

    return RiskState(
        peak_equity=peak_equity,
        current_equity=eq,
        daily_starting_equity=daily_starting,
        daily_pnl=daily_pnl,
        consecutive_losses=consec,
        trades_today=trades_today,
        is_halted=is_halted,
        halt_reason=halt_reason,
    )


def save_risk_state(state: RiskState, mode: str | None = None) -> None:
    """Persist the risk-state cache to MotherDuck. Best-effort: the canonical
    state is re-derived on load, so a cache write failure is non-fatal."""
    try:
        from src.trade_logger import write_risk_cache
        write_risk_cache(mode or _mode(), _today_et(), state)
    except Exception as e:
        print(f"  [risk] could not persist risk-state cache (non-fatal): {e}")


def reset_daily_state(state: RiskState, current_equity: float) -> RiskState:
    """Called at start of each trading day."""
    state.daily_starting_equity = current_equity
    state.current_equity = current_equity
    state.daily_pnl = 0.0
    state.trades_today = 0
    # Update peak if we made new highs
    if current_equity > state.peak_equity:
        state.peak_equity = current_equity
    # Clear daily halt (but not drawdown halt)
    if state.halt_reason in ("daily_loss_limit", "consecutive_losses", "max_trades"):
        state.is_halted = False
        state.halt_reason = None
    return state


# ─── Pre-Trade Checks ────────────────────────────────────────────────

def can_trade(state: RiskState) -> tuple[bool, str]:
    """
    Run all pre-trade risk checks. Returns (allowed, reason).
    Call this BEFORE submitting any order.
    """
    if state.is_halted:
        return False, f"Trading halted: {state.halt_reason}"

    if state.trades_today >= settings.MAX_TRADES_PER_DAY:
        state.is_halted = True
        state.halt_reason = "max_trades"
        save_risk_state(state)
        return False, f"Max trades/day reached ({settings.MAX_TRADES_PER_DAY})"

    if state.daily_loss_pct >= settings.MAX_DAILY_LOSS_PCT:
        state.is_halted = True
        state.halt_reason = "daily_loss_limit"
        save_risk_state(state)
        return False, f"Daily loss limit hit ({state.daily_loss_pct:.1%})"

    if state.consecutive_losses >= settings.MAX_CONSECUTIVE_LOSSES:
        state.is_halted = True
        state.halt_reason = "consecutive_losses"
        save_risk_state(state)
        return False, f"Consecutive loss limit ({settings.MAX_CONSECUTIVE_LOSSES})"

    if state.current_drawdown_pct >= settings.MAX_DRAWDOWN_PCT:
        state.is_halted = True
        state.halt_reason = "max_drawdown"
        save_risk_state(state)
        return False, f"Max drawdown hit ({state.current_drawdown_pct:.1%}) — MANUAL REVIEW REQUIRED"

    return True, "OK"


# ─── Position Sizing ─────────────────────────────────────────────────

def calculate_position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float = None,
    capital_cap: float = None,
) -> int:
    """
    ATR-aware position sizing: risk a fixed % of equity per trade.
    Position size = (equity * risk%) / (entry - stop distance)

    capital_cap bounds the notional (buying power) for this position and
    defaults to full equity. For multi-symbol trading pass equity / N so two
    concurrent positions don't each try to claim the whole account.

    Returns number of shares (integer, rounds down).
    """
    risk_pct = risk_pct or settings.RISK_PER_TRADE_PCT
    risk_dollars = equity * risk_pct
    stop_distance = abs(entry_price - stop_price)

    if stop_distance <= 0:
        return 0

    shares = int(risk_dollars / stop_distance)

    # Cap by available capital (buying power) for this position.
    cap = capital_cap if capital_cap is not None else equity
    max_shares_by_capital = int(cap / entry_price)
    shares = min(shares, max_shares_by_capital)

    return max(shares, 0)


# ─── Post-Trade Updates ──────────────────────────────────────────────

def record_trade_result(state: RiskState, pnl: float, equity_after: float) -> RiskState:
    """Update risk state after a trade completes."""
    state.trades_today += 1
    state.daily_pnl += pnl
    state.current_equity = equity_after

    if pnl < 0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0  # reset on any win

    if equity_after > state.peak_equity:
        state.peak_equity = equity_after

    save_risk_state(state)
    return state


# ─── Backtest Risk Simulation ────────────────────────────────────────

def simulate_risk_controls(
    trades: list[dict],
    initial_capital: float,
    capital_cap_frac: float | None = None,
) -> list[dict]:
    """
    Apply risk controls to a list of backtest trades.
    Returns only the trades that would have been taken,
    plus equity curve data.

    Sizing matches LIVE exactly: risk % of equity, capped at each position's
    notional slice (equity * capital_cap_frac). capital_cap_frac defaults to
    1 / N symbols, the same split the live bot uses (no leverage). Uncapped
    risk-only sizing implied unrealistic leverage and inflated returns.
    """
    cap_frac = (capital_cap_frac if capital_cap_frac is not None
                else 1.0 / max(len(settings.TICKERS), 1))
    equity = initial_capital
    peak_equity = initial_capital
    consecutive_losses = 0
    daily_trades = {}
    executed_trades = []

    for trade in trades:
        trade_date = trade.get("entry_time", "")[:10]

        # Max trades per day
        daily_trades[trade_date] = daily_trades.get(trade_date, 0) + 1
        if daily_trades[trade_date] > settings.MAX_TRADES_PER_DAY:
            continue

        # Consecutive loss check
        if consecutive_losses >= settings.MAX_CONSECUTIVE_LOSSES:
            # Skip until next day
            if trade_date == (executed_trades[-1]["entry_time"][:10] if executed_trades else ""):
                continue
            consecutive_losses = 0  # new day, reset

        # Max drawdown check
        drawdown_pct = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        if drawdown_pct >= settings.MAX_DRAWDOWN_PCT:
            break  # full halt

        # Position sizing — identical to live: risk % of equity, capped at the
        # per-symbol notional slice (equity * cap_frac). No leverage.
        stop_dist = abs(trade["entry_price"] - trade["stop_price"])
        if stop_dist <= 0:
            continue
        shares = calculate_position_size(
            equity, trade["entry_price"], trade["stop_price"],
            capital_cap=equity * cap_frac,
        )
        if shares <= 0:
            continue

        # Daily loss check
        day_pnl = sum(
            t["trade_pnl"] for t in executed_trades
            if t["entry_time"][:10] == trade_date
        )
        if day_pnl < 0 and abs(day_pnl / equity) >= settings.MAX_DAILY_LOSS_PCT:
            continue

        # Execute. Round-trip costs are netted out of gross P&L. Net is
        # what drives equity, drawdown, and the win/loss classification:
        # a marginal gross win can flip to a net loss once costs are paid.
        gross_pnl = trade["pnl_per_share"] * shares
        cost = round_trip_cost_per_share(trade["entry_price"]) * shares
        net_pnl = gross_pnl - cost
        equity += net_pnl

        if equity > peak_equity:
            peak_equity = equity

        if net_pnl < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        trade["shares"] = shares
        trade["gross_pnl"] = round(gross_pnl, 2)
        trade["cost"] = round(cost, 2)
        trade["trade_pnl"] = round(net_pnl, 2)
        trade["equity_after"] = round(equity, 2)
        trade["drawdown_pct"] = round(
            (peak_equity - equity) / peak_equity if peak_equity > 0 else 0, 4
        )
        executed_trades.append(trade)

    return executed_trades
