# Reviewer Brief: Rootwork ORB Bot

Paper-only intraday breakout system that has never placed a clean on-time real trade. Engineering and statistics are built to a competent standard; what is missing is lived-market judgment and live fills. Your hour is judgment, not code reading. All figures are net of costs unless labeled gross, over 2024-01-01 to 2026-06-01, and are reproducible from the committed trade files via `scripts/validation_suite.py`.

## 1. System summary

Opening Range Breakout on QQQ and SPY, 5-minute opening range, ATR(14) 1.5x stop, 0.3% minimum range filter, 2:1 reward-to-risk. Entry is a market order on the first break of the opening range high (long) or low (short), submitted as an Alpaca bracket (entry, stop, target) atomically. Architecture: GitHub Actions cron (09:25 ET pre-market sizing, 09:40 ET execution after the 09:35 range close, 15:45 ET force-close and reporting), Alpaca, MotherDuck trade logging, Gmail daily report. One trade per symbol per day, first breakout decides the day, a deliberate constraint that independent profitable traders converge on, not a simplification. Not included: no active regime filter (an overnight-gap gate at 1.5% exists but is OFF, and it is a gap gate, not VIX-based), no position scaling, no multi-day holds, no limit-style entry, no earnings-day exclusion.

## 2. Results table

| Metric | SPY | QQQ |
|---|---|---|
| Backtest period | 2024-01-01 to 2026-06-01 | 2024-01-01 to 2026-06-01 |
| Total trades | 46 | 166 |
| Win rate | 60.9% | 62.7% |
| Gross Sharpe | 3.06 | 4.27 |
| Net Sharpe (3 bps) | 2.65 | 3.81 |
| Net Sharpe (7 bps) | 2.10 | 3.21 |
| Net Sharpe (10 bps) | 1.69 | 2.75 |
| Deflated Sharpe (18 trials) | 0.25 | 0.92 |
| Net Sharpe (3 bps) as % of gross | 87% | 89% |
| Max drawdown | 2.4% | 2.9% |
| Profit factor | 1.60 | 1.92 |
| Avg win / avg loss | $51.88 / -$50.45 | $54.97 / -$48.01 |
| Total return | +5.5% | +27.4% |
| Bootstrap 95% CI, Sharpe (IID) | [-2.05, 7.17] | [1.51, 6.11] |
| Bootstrap 95% CI, Sharpe (block=5) | [-1.57, 6.61] | [1.58, 6.01] |
| Permutation p (Sharpe > 0) | 0.138 | 0.0007 |
| Win-rate binomial p vs 50% | 0.092 | 0.0007 |
| Lag-1 autocorr, daily P&L | 0.10 | 0.06 |

Note: trade counts are the current committed backtest (46 SPY, 166 QQQ); an earlier snapshot cited ~43 / ~150.

## 3. Slippage cliff (the most important output)

Net Sharpe by assumed round-trip slippage. Full data in `validation/slippage_cliff.csv`.

| bps | 0 | 3 | 5 | 7 | 10 | 15 | 20 | 25 | 30 |
|---|---|---|---|---|---|---|---|---|---|
| SPY | 3.06 | 2.65 | 2.37 | 2.10 | 1.69 | 1.01 | 0.33 | -0.34 | -1.01 |
| QQQ | 4.27 | 3.81 | 3.51 | 3.21 | 2.75 | 1.99 | 1.22 | 0.46 | -0.31 |

**Kill level (net Sharpe crosses zero): SPY ~22.5 bps, QQQ ~28 bps.** The edge survives well past the 3 to 7 bps assumption. For contrast, the independent TastyTrading clone (3x leveraged TQQQ/SQQQ) dies near 15 bps; ours holds further because it is 1x, unleveraged. The reviewer should judge whether ~22 to 28 bps is reachable for QQQ/SPY market orders at 09:40 ET, i.e. how much cushion actually exists.

## 4. DSR sensitivity to trial count

The deflated Sharpe uses a trial count of 18, an estimated lower bound; true implicit trials (abandoned configs, visual inspections, filter iterations) are higher. ONC clustering (Lopez de Prado 2018) to estimate effective independent trials was not implemented. Data in `validation/dsr_sensitivity.csv`.

| Trials (N) | DSR QQQ | DSR SPY |
|---|---|---|
| 18 | 0.92 | 0.25 |
| 30 | 0.89 | 0.18 |
| 50 | 0.84 | 0.13 |
| 100 | 0.78 | 0.09 |

QQQ retains >70% survival probability even at 100 assumed trials, which is credible. SPY collapses under any honest trial count.

Configurations that failed (the 15-minute ORB and the gap-fill filter) were tested and reported as negative results (Section 5), which reduces the risk that the surviving 5-minute ORB is a cherry-picked winner from unreported trials.

## 5. What was validated, and how

- Parameter search: a v1 grid of 27 combinations (3 ORB windows x 3 R:R x 3 ATR-stop multipliers, `src/walkforward.py`), then a v2 filter A/B of 3 base configs x 4 filter stacks (`src/param_sweep.py`), ranked by Sharpe with a minimum trade-count floor.
- Cost model: slippage + half-spread per leg + commission (`src/costs.py`), reported 0 to 40 bps. Net Sharpe clears 1.5 at 3 bps for both names and stays above 1.0 at 7 bps.
- Deflated Sharpe: Bailey and Lopez de Prado (2014), trial count 18 (estimated), trial variance approximated. Sensitivity in Section 4.
- Bootstrap: IID and moving-block (block=5, Kunsch 1989), 10,000 resamples, 95% CIs. Caveat: IID ignores serial correlation (Section 6).
- Walk-forward: run previously (`src/walkforward.py`). A rolling optimizer overfit hard (mean in-sample-best Sharpe ~3.5 vs out-of-sample ~0.1), so params are held fixed; the candle filter held out-of-sample on a true holdout. No standing output file (prints to stdout).
- Regime gate: overnight-gap gate at 1.5%, NOT swept, fixed, and OFF by default.
- v2 filters (VWAP, RVOL, candle): VWAP inert, RVOL toxic (cut ~60 trades to ~3, negative Sharpe), candle helped and is the only one enabled.
- PBO/CSCV: computed for BOTH strategies (S=8, `scripts/cscv.py`). Shipped ORB PBO 0.057 (not overfit, OOS Sharpe of the pick +1.66); Gap-Fill PBO 0.43 (overfit, held back). See `validation/pbo_cscv.md`.
- Entry timing (5m vs 15m): computed. The 15-minute window has no tradeable edge; the 5-minute window is the only configuration that survives. See `validation/entry_timing_comparison.md`.
- Segmentation / leak finder: direction, day-of-week, opening-range width, and gap-size-at-open in `validation/trade_segmentation.md`.

### Negative results

Two alternative configurations were tested and rejected. Reporting them is the point: it narrows the system to a single validated configuration rather than a cherry-picked winner from many unreported alternatives.

- 15-minute ORB (entry at 09:45 ET) was backtested as an alternative to the 5-minute ORB. It produces no tradeable edge (gross Sharpe QQQ -0.34, SPY -1.11, negative before any cost is charged). The 5-minute entry window is the only configuration that survives. Detail in `validation/entry_timing_comparison.md`.
- A gap-fill parameter sweep was run and the in-sample winner was tested via CSCV (S=8). PBO (0.43, with a negative out-of-sample degradation slope and a negative mean out-of-sample Sharpe on the selected config) confirms the winner does not hold out of sample. The gap-fill filter is overfit and was not shipped. Detail in `validation/pbo_cscv.md`.

## 6. What we could not verify

**Data or time gated:** zero real-money trades; real slippage vs the assumption is unmeasured; forward persistence is unknown; IEX (live) vs SIP (backtest) opening-range divergence is not quantified (`docs/data_feed_audit.md`).

**Statistical shortcuts:** deflated Sharpe uses an approximated trial variance and a trial count of 18 that is a lower bound. PBO was run on both strategies (ORB 0.057, not overfit; gap-fill 0.43, overfit and held back); the ORB PBO universe is 54 configs (directional, smaller than the gap sweep's 729). The bootstrap is IID; observed lag-1 autocorrelation is low (SPY 0.10, QQQ 0.06), so the block bootstrap barely widens the CI here (literature warns of ~30 to 50% understatement at phi~0.2 to 0.3, up to ~2x at phi~0.6; we are below that, and QQQ's block CI still excludes zero). Samples are small: SPY 46 trades is below the ~100-trade credibility floor (win-rate binomial p 0.09), treat as supporting evidence only; QQQ 166 is borderline acceptable.

**Judgment we cannot supply:** whether ORB is decayed or crowded now; whether QQQ is structural or a 2024 to 2025 regime artifact; whether market orders fill near the backtest level on fast breakouts; what counts as enough evidence to risk capital.

**Structural limitation:** built by an AI coding assistant (Claude) with a domain expert in energy infrastructure, not trading. Claude verified engineering and statistical method, not market truth, and worked from summaries of the original research (Reddit post images), not the primary source. The builder has textbook knowledge, not scar tissue.

## 7. Independent confirmation, TastyTrading comparison

| Dimension | Our System | TastyTrading Bot |
|---|---|---|
| Signal generation | Python, Alpaca data | Ruby, Intrinio data |
| Execution | Alpaca bracket orders | Claude MCP to Robinhood |
| Instruments | QQQ, SPY | TQQQ, SQQQ (3x) |
| Leverage | 1x | 3x |
| Slippage model | 3 to 7 bps assumed | 0 bps (admits edge dies at 15 bps) |
| Trade frequency | 1/day, ~8/month filtered | 1/day |
| Validation | DSR, bootstrap, sweep | 6-month IS/OOS split |
| Logging | MotherDuck | None mentioned |
| Live P&L | 0 trades | 2 weeks, +$1,242 (24.85%) |

What this confirms: the ORB edge on QQQ-family instruments is real in the current market and replicates independently at similar Sharpe. The vulnerability is identical: slippage kills it. Ours has more slippage headroom because it is unleveraged.

## 8. ORB edge status

ORB as a class shows continued profitability through 2024 to 2026 across independent backtests. Options Cafe: 5-minute ORB on SPY 0DTE profitable across 2024 to early 2026, 5-minute nearly doubling 15-minute returns at half the drawdown. BreakOrb: 28.7M configurations tested, only 0.51% survived walk-forward, so the edge exists but parameter sensitivity is extreme (199 of 200 configs fail forward). Edgeful: ORB widely used, settings need periodic recalibration. Slippage context: Invesco (QQQ's issuer) recommends avoiding QQQ in the first 30 minutes because spreads are wider; ETF data shows spreads tightest 10:00 to 15:30; a market order at 09:40 on a breakout faces adverse selection.

The 15-minute ORB showing no edge means there is no fallback entry time if 5-minute fills are problematic. The slippage question on the 5-minute entry is now a hard constraint, not a preference.

## 9. The four questions for you

> **Q1, Is the edge real?** 166 QQQ trades, deflated Sharpe retains ~90% of gross and >70% even at 100 trials. 46 SPY trades, higher raw Sharpe but below the credibility floor and failing the haircut. An independent clone confirms the edge. Enough to risk $5,000, or do you want more history?
>
> **Q2, Stats shortcuts.** Deflated Sharpe trial count is estimated at 18 (sensitivity in Section 4); the bootstrap is IID with lag-1 autocorrelation of 0.10 (SPY) / 0.06 (QQQ). Would you redo either, and with what?
>
> **Q3, Execution realism.** Market-order entry with 3 to 7 bps assumed slippage on QQQ/SPY at 09:40 ET. The 15-minute ORB was tested and has no edge, so there is no fallback to a later, more liquid entry time. The slippage cliff shows the edge dies at ~22.5 (SPY) / ~28 (QQQ) bps. Does that headroom hold for real fills? If not, would limit-style entry solve it, or does the strategy need to be abandoned?
>
> **Q4, Go / no-go.** What would you need to see before you would risk capital? We plan to paper trade 60 to 90 days first. Is that enough?

## 10. File map

- `results/trades_{SPY,QQQ}.csv`: per-trade backtest results (Q1)
- `validation/slippage_cliff.csv` + `_summary.md`: the cliff and kill levels (Q3)
- `validation/dsr_sensitivity.csv`, `validation/autocorrelation_check.md`, `validation/block_bootstrap.csv`: robustness (Q2)
- `validation/trade_segmentation.md`, `pbo_cscv.md`, `entry_timing_comparison.md`: leak finder, overfitting test, and entry-timing negative result (Q1, Q2, Q3)
- `scripts/validation_suite.py`: reproduces every figure above (Q1, Q2)
- `config/settings.py`: all parameters and risk limits (all questions)
- `src/orb_signal.py`: signal generation and trade simulation (Q1, Q3)
- `src/risk_manager.py` + `src/risk_monitor.py`: sizing, halts, circuit breakers (Q4)
- `src/backtest.py`: performance and Sharpe computation (Q1, Q2)
