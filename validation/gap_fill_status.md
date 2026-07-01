# Gap-Fill (GAP_FILL v1) — Status

**Status: Phase 1 built. Phase 2 default-config backtest RUN (2026-07-01, keys
restored). Verdict: HOLD.** See `validation/gap_fill_results.md`. QQQ, 117 trades,
net Sharpe 1.92 @3bps, correlation with ORB -0.01 (excellent diversification),
slippage kill 13.8 bps, max DD 4.4% — but the deflated Sharpe is weak (0.30 at
N=18, 0.03 at the combined 747-trial count), so the edge is statistically
fragile and NOT deployed. The 729-config sweep + PBO/CSCV was then run
(`pbo_cscv.md`): PBO 0.429, negative degradation slope, mean OOS Sharpe of the
sweep winner -0.19 — the optimized config is overfit too. Both angles agree:
HOLD. Revisit only with a walk-forward that holds out-of-sample or an a-priori
config, plus an SPY add for samples.

## What is built (Phase 1)

- `src/gap_signal.py` — signal generator (mirror of `orb_signal.py`):
  `detect_gap`, `min_gap_threshold`, `route_strategy` (the three-zone router),
  `generate_gap_fill_signal`, `simulate_gap_fill_trade`. Fades the gap (gap-up →
  short, gap-down → long), ATR x1.0 stop, 2:1 target, EOD force-close. Pure and
  unit-tested (fade direction, entry/stop/target levels, in/out-of-zone,
  direction filter, ATR-required, target/stop/EOD resolution).
- `src/backtest_gap.py` — backtester reusing the SAME `simulate_risk_controls`
  and `calculate_performance` as ORB, so results are directly comparable and
  net-of-cost. CLI mirrors `backtest.py`.
- `src/param_sweep_gap.py` — the 729-config grid (Section 8). Saves the summary
  AND each config's per-day return series (`gap_sweep_returns.csv`) so PBO/CSCV
  can be run later — this closes the gap that blocked PBO for ORB.
- `config/settings.py` — gap-fill params (`GAP_FILL_*`), all env-overridable.

## Why Phase 2 is blocked

The Alpaca API keys in `.env` are deauthorized (account and data both 401), so
the backtester cannot fetch the 2024-2026 bars it needs. Running the code now
would only produce an auth error, not numbers. Producing fake numbers would
defeat the entire review.

## Exact run order once keys are restored

```
# 1. Backtest (default params) — sanity + trade count
python -m src.backtest_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01

# 2. Parameter sweep (729 configs) — writes validation/gap_sweep_results.csv
#    and validation/gap_sweep_returns.csv (DSR trial count = configs evaluated)
python -m src.param_sweep_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01

# 3. Copy the chosen config's trades into results/ (e.g. results/trades_gap_QQQ.csv)
#    then extend scripts/validation_suite.py to include it for the slippage cliff,
#    DSR sensitivity, block bootstrap, and segmentation (same functions as ORB).

# 4. Correlation vs ORB: align daily returns of the gap-fill config and the ORB
#    config by date and compute Pearson r (target r < 0.3).
```

## Success criteria (from the spec — decide GO to paper)

- Net Sharpe >= 1.5 at 3 bps round trip
- Trade count >= 30 over 2024-2026 (else widen the gap band or add SPY)
- Max drawdown <= 5%
- Correlation with ORB returns r < 0.3
- Survives DSR deflation at N = ORB trials + gap-fill configs (18 + up to 729).
  This is a heavy haircut, intentionally: a 729-trial deflation is a real test.
- Slippage cliff kill level >= 10 bps

## Not built yet (Phase 3, gated on a green backtest)

The live routing is deliberately NOT wired: `execute_gap_fill.py` and the
three-zone router in `pre_market.py` (writing `strategy_today` to state) come
only after the backtest clears the success criteria. Wiring an unvalidated
mean-reversion entry into the live order path would be reckless. The pure
`route_strategy()` is ready for that wiring. Note also the open question from
the spec: consecutive-loss counting stays COMBINED across strategies for v1
(already how the shared risk state works).

## Note on the 9:30 timing risk (spec Section 9)

The spec flags open-timing drift and suggests a market-on-open (MOO) order.
Alpaca supports MOO via `TimeInForce.OPG` on a market order (must be submitted
before ~9:28 ET; it fills at the official opening print). This is the right
mechanism for Phase 3 and avoids cron drift, but it means the entry price is the
opening auction print, which the backtest models as the 09:30 bar open — a
fidelity point to verify during the paper period.
