# Validation Status & Reviewer Packet

Audit of the ORB reviewer-prep plan as of 2026-07-01. Honest accounting. As of
this date the Alpaca keys were restored, so every previously data-blocked item
has been run — nothing here is fabricated or pending on access.

## Status table

| Item | Status | Evidence / Notes |
|---|---|---|
| Slippage cliff (0-20 bps) | Done | `slippage_cliff.csv` (extended to 40 bps), `slippage_cliff_summary.md`. Kill levels: SPY ~22.5 bps, QQQ ~28 bps. |
| Block bootstrap + lag-1 autocorr | Done | `autocorrelation_check.md`, `block_bootstrap.csv`. Lag-1: SPY 0.10, QQQ 0.06 (low; block CI ~ IID). |
| DSR sensitivity (N=18/30/50/100) | Done | `dsr_sensitivity.csv`. QQQ 0.92 -> 0.78; SPY 0.25 -> 0.09. |
| Segmentation: direction / day-of-week / range width | Done | `trade_segmentation.md`. |
| Segmentation: gap size at open | Done | `trade_segmentation.md` (gap-size section) via `scripts/gap_size_segmentation.py`. SPY <0.3%-gap trades near-flat (Sharpe 0.26); QQQ strongest in 0.3-0.7% band (5.24). |
| Entry timing 5-min vs 15-min | Done | `entry_timing_comparison.md`, `entry_timing_cliff.csv` (`scripts/entry_timing_analysis.py`). Decisive: 15-min has NO edge (QQQ gross -0.34, SPY -1.11) vs 5-min positive. Keep 5-min. |
| PBO / CSCV | Done | `pbo_cscv.md` (`scripts/cscv.py`) on the Gap-Fill 729-config sweep. PBO 0.429, degradation slope -1.12, mean OOS Sharpe of the pick -0.19 -> the sweep winner is overfit. Direct ORB PBO still needs the ORB sweep to persist returns (documented). |
| Gap-Fill v1 (separate strategy) | Backtested, HOLD | Two angles agree: `gap_fill_results.md` (default config: net Sharpe 1.92, r=-0.01 with ORB, slippage-robust, but weak DSR) and `pbo_cscv.md` (sweep winner overfit). Not deployed. |
| REVIEWER_BRIEF.md | Done | Repo root. Real numbers, 10 sections. |
| REVIEWER_SPEC.md | Done | Repo root. |
| Kill switch (-10% peak equity) | Done | `MAX_DRAWDOWN_PCT=0.10`, sticky latch in `src/risk_manager.py` (`can_trade`, `simulate_risk_controls`). Plus 3% daily stop, 50% floor, manual kill, consec-loss pause. |
| Position sizing (compound + $5K sim) | Done | `get_effective_equity` + `PAPER_SIMULATED_EQUITY` (`src/alpaca_client.py`, threaded through execute/pre/EOD/monitor). |
| GitHub Actions crons + paper logging | Partial | Workflow `Trading Schedule` ACTIVE, runs succeed. All runs so far are manual dispatches; `algo_trade_log` had 0 rows at audit time. No market-hours scheduled trade yet. |

## B1 gate (slippage cliff)

Directive gate: "if edge dies at 7 bps, stop and report." It does not.

| bps | 3 | 5 | 7 | 10 |
|---|---|---|---|---|
| SPY net Sharpe | 2.65 | 2.37 | 2.10 | 1.69 |
| QQQ net Sharpe | 3.81 | 3.51 | 3.21 | 2.75 |

Edge is intact at 7 bps -> PROCEED. (These are the committed trade set; a fresh
2026-07-01 SIP fetch gives slightly lower counts, QQQ 150 / SPY 43, a data-
snapshot difference — see `entry_timing_comparison.md`.)

## Reproduction

All previously-blocked items were run 2026-07-01 with restored keys:

```
# Entry timing 5m vs 15m (B6)
python -m src.backtest --ticker QQQ --or-minutes 15 --start 2024-01-01 --end 2026-06-01
python -m src.backtest --ticker SPY --or-minutes 15 --start 2024-01-01 --end 2026-06-01
python scripts/entry_timing_analysis.py

# Gap-size segmentation (B5 remainder)
python scripts/gap_size_segmentation.py

# Gap-Fill backtest (default config) + success-criteria analysis
python -m src.backtest_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01
python scripts/gap_fill_analysis.py

# Gap-Fill 729-config sweep (persists per-config returns) + PBO/CSCV
python -m src.param_sweep_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01
python scripts/cscv.py --segments 8
```

## Reviewer packet — file map

- `REVIEWER_SPEC.md` — who to look for (outreach).
- `REVIEWER_BRIEF.md` — the one-pager: summary, results, slippage cliff, DSR
  sensitivity, TastyTrading comparison, honest gaps, the four questions.
- `results/trades_SPY.csv`, `results/trades_QQQ.csv` — ORB per-trade backtest (Q1).
- `validation/slippage_cliff.csv` + summary — execution/slippage (Q3).
- `validation/dsr_sensitivity.csv`, `autocorrelation_check.md`,
  `block_bootstrap.csv` — robustness (Q2).
- `validation/trade_segmentation.md` — leak finder incl. gap-size (Q1).
- `validation/entry_timing_comparison.md`, `entry_timing_cliff.csv` — 5m vs 15m (Q3).
- `validation/pbo_cscv.md` — overfitting test on the gap sweep (Q2).
- `validation/gap_fill_results.md`, `results/trades_gap_QQQ.csv` — Gap-Fill v1
  default-config validation (separate candidate strategy, HOLD).
- `scripts/validation_suite.py`, `scripts/cscv.py`, `scripts/gap_fill_analysis.py`,
  `scripts/gap_size_segmentation.py`, `scripts/entry_timing_analysis.py` —
  reproduce every figure (Q2).
- `config/settings.py`, `src/orb_signal.py`, `src/risk_manager.py`,
  `src/backtest.py` — parameters, signal, risk, performance (all questions).

The four questions for the reviewer are in `REVIEWER_BRIEF.md` Section 9:
1. Is the edge real, or small-sample noise?
2. Would you redo the deflated-Sharpe trial count (est. 18) or the IID bootstrap?
3. Does 3-7 bps slippage hold for QQQ opening-range market orders at 09:40 ET?
4. What would you need to see before risking capital?
