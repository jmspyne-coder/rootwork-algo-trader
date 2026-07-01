# Validation Status & Reviewer Packet

Audit of the ORB reviewer-prep plan as of 2026-07-01. Honest accounting: what is
done, what is blocked and why, and the exact commands to finish the blocked
items. No numbers here are fabricated; blocked means blocked.

## Status table

| Item | Status | Evidence / Notes |
|---|---|---|
| Slippage cliff (0-20 bps) | Done | `slippage_cliff.csv` (extended to 40 bps), `slippage_cliff_summary.md`. Kill levels: SPY ~22.5 bps, QQQ ~28 bps. |
| Block bootstrap + lag-1 autocorr | Done | `autocorrelation_check.md`, `block_bootstrap.csv`. Lag-1: SPY 0.10, QQQ 0.06 (low; block CI ~ IID). |
| DSR sensitivity (N=18/30/50/100) | Done | `dsr_sensitivity.csv`. QQQ 0.92 -> 0.78; SPY 0.25 -> 0.09. |
| Segmentation: direction / day-of-week / range width | Done | `trade_segmentation.md`. |
| Segmentation: gap size at open | Blocked (data) | Needs prior close + day open per trade, not in the committed trade files. |
| Entry timing 5-min vs 15-min | Blocked (data) | `entry_timing_comparison.md`. Needs a 15-min ORB re-run over intraday bars. |
| PBO / CSCV | Blocked (data) | `pbo_cscv.md`. ORB sweep did not persist per-config returns; the gap sweep now does. |
| REVIEWER_BRIEF.md | Done | Repo root. Real numbers, 10 sections. |
| REVIEWER_SPEC.md | Done | Repo root. |
| Kill switch (-10% peak equity) | Done | `MAX_DRAWDOWN_PCT=0.10`, sticky latch in `src/risk_manager.py` (`can_trade`, `simulate_risk_controls`). Plus 3% daily stop, 50% floor, manual kill, consec-loss pause. |
| Position sizing (compound + $5K sim) | Done | `get_effective_equity` + `PAPER_SIMULATED_EQUITY` (`src/alpaca_client.py`, threaded through execute/pre/EOD/monitor). |
| GitHub Actions crons + paper logging | Partial | Workflow `Trading Schedule` is ACTIVE and runs succeed (secrets valid in Actions). All runs so far are manual dispatches; `algo_trade_log` has 0 rows, `algo_run_log` 5, `algo_daily_summary` 1. No market-hours scheduled trade yet. |

## B1 gate (slippage cliff)

Directive gate: "if edge dies at 7 bps, stop and report." It does not.

| bps | 3 | 5 | 7 | 10 |
|---|---|---|---|---|
| SPY net Sharpe | 2.65 | 2.37 | 2.10 | 1.69 |
| QQQ net Sharpe | 3.81 | 3.51 | 3.21 | 2.75 |

Edge is intact at 7 bps -> PROCEED.

## Why the blocked items cannot be run from here

The Alpaca API keys in the local `.env` are deauthorized (account + data both
401). Verified this turn that the alpaca MCP tool also returns 401 on both SIP
and IEX. So there is no market-data channel available in this environment;
running the backtests would only produce an auth error. GitHub Actions is a
separate story: its repo secrets ARE valid (recent runs on 2026-06-30 succeeded),
so the blocked items can be produced there or after refreshing the local keys.

## Exact commands to close the gaps (once a data key works)

```
# Entry timing 5m vs 15m (B6)
python -m src.backtest --ticker QQQ --or-minutes 15 --start 2024-01-01 --end 2026-06-01
python -m src.backtest --ticker SPY --or-minutes 15 --start 2024-01-01 --end 2026-06-01
#   then rerun scripts/validation_suite.py on both windows and fill entry_timing_comparison.md

# Gap-size segmentation (B5 remainder): fetch daily bars, compute
# (open-prev_close)/prev_close per date, join to results/trades_*.csv by date.

# PBO/CSCV (B3): run the gap sweep (it persists per-config returns), then CSCV S=8
python -m src.param_sweep_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01
#   -> validation/gap_sweep_returns.csv feeds CSCV

# Gap-Fill v1 backtest (separate strategy, pending)
python -m src.backtest_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01
```

## Reviewer packet — file map

Everything the external reviewer needs, by question:

- `REVIEWER_SPEC.md` — who to look for (outreach).
- `REVIEWER_BRIEF.md` — the one-pager: summary, results, slippage cliff, DSR
  sensitivity, TastyTrading comparison, honest gaps, the four questions.
- `results/trades_SPY.csv`, `results/trades_QQQ.csv` — per-trade backtest (Q1).
- `validation/slippage_cliff.csv` + summary — execution/slippage (Q3).
- `validation/dsr_sensitivity.csv`, `autocorrelation_check.md`,
  `block_bootstrap.csv` — robustness (Q2).
- `validation/trade_segmentation.md`, `pbo_cscv.md`,
  `entry_timing_comparison.md` — leak finder + the two open gaps (Q1, Q2).
- `scripts/validation_suite.py` — reproduces every computed figure (Q2).
- `config/settings.py`, `src/orb_signal.py`, `src/risk_manager.py`,
  `src/backtest.py` — parameters, signal, risk, performance (all questions).

The four questions for the reviewer are in `REVIEWER_BRIEF.md` Section 9:
1. Is the edge real, or small-sample noise?
2. Would you redo the deflated-Sharpe trial count (est. 18) or the IID bootstrap?
3. Does 3-7 bps slippage hold for QQQ opening-range market orders at 09:40 ET?
4. What would you need to see before risking capital?
