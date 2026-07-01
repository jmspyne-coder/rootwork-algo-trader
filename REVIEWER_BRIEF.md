# Reviewer Brief: Rootwork ORB Bot

Paper-only intraday breakout system that has never placed a clean on-time real trade. Engineering and statistics are built to a competent standard. What is missing is lived-market judgment and live fills. Your hour is judgment, not code reading. Figures are net of costs unless labeled gross, over 2024-01-01 to 2026-06-01, reproducible via `scripts/compute_review_metrics.py`.

## 1. System summary

Opening Range Breakout on QQQ and SPY, 5-minute opening range, ATR(14) 1.5x stop, 0.3% minimum range filter, 2:1 reward-to-risk. Entry is a market order on the first break of the opening range high (long) or low (short), sent as an Alpaca bracket (stop plus target) atomically. Architecture: GitHub Actions cron (09:25 ET pre-market sizing, 09:40 ET execution after the 09:35 range close, 15:45 ET force-close and reporting), Alpaca paper, MotherDuck logging, Gmail daily report, plus an independent 09:58 ET watchdog. One trade per symbol per day, first breakout decides the day. Not included: no active regime filter, no position scaling, no multi-day holds, no limit-style entry, no earnings-day exclusion.

## 2. Results table

| Metric | SPY | QQQ |
|---|---|---|
| Backtest period | 2024-01-01 to 2026-06-01 | 2024-01-01 to 2026-06-01 |
| Total trades | 46 | 166 |
| Win rate | 60.9% | 62.7% |
| Gross Sharpe | 3.06 | 4.27 |
| Net Sharpe (3 bps round trip) | 2.65 | 3.81 |
| Net Sharpe (7 bps round trip) | 2.10 | 3.21 |
| Net Sharpe (10 bps stress) | 1.69 | 2.75 |
| Deflated Sharpe, P(true SR>0), 18 trials | 0.25 | 0.92 |
| Net Sharpe (3 bps) as % of gross | 87% | 89% |
| Max drawdown | 2.4% | 2.9% |
| Profit factor | 1.60 | 1.92 |
| Avg win / avg loss | $51.88 / -$50.45 | $54.97 / -$48.01 |
| Total return | +5.5% | +27.4% |
| Win-rate binomial p vs 50% | 0.092 | 0.0007 |
| Lag-1 autocorr (daily P&L) | 0.10 | 0.06 |
| IID bootstrap 95% CI on Sharpe | [-2.05, 7.17] | [1.51, 6.11] |
| Block bootstrap 95% CI (block=5) | [-1.57, 6.61] | [1.58, 6.01] |
| Permutation p (Sharpe > 0) | 0.138 | 0.0007 |

Read: QQQ looks like a real edge that survives the multiple-testing haircut, holds a net Sharpe of 2.75 even at a punitive 10 bps, and keeps a CI clear of zero under both bootstraps. SPY does not. Its CI spans zero under both methods, its permutation p and its win-rate binomial p are not significant, and its deflated Sharpe collapses as trial count rises. Trade counts are the current committed backtest (46 SPY, 166 QQQ); an earlier snapshot cited roughly 43 and 150.

**Deflated Sharpe sensitivity to trial count** (the weak link; see caveats):

| Trials | 18 | 30 | 50 | 100 |
|---|---|---|---|---|
| SPY | 0.25 | 0.18 | 0.13 | 0.09 |
| QQQ | 0.92 | 0.89 | 0.84 | 0.78 |

QQQ retains more than 70% survival probability even at 100 assumed trials. SPY is a multiple-testing artifact under any honest trial count.

**Serial correlation.** Observed lag-1 autocorrelation of daily P&L is low (0.10 SPY, 0.06 QQQ), so the moving-block bootstrap (block = 5 days, Kunsch 1989) does not materially widen the CI here, contrary to the usual IID warning. The IID concern is real in principle but empirically small on this data. QQQ's block CI still excludes zero.

## 3. What was validated, and how

- Parameter search: a v1 grid of 27 combinations (3 opening-range windows x 3 reward-risk ratios x 3 ATR-stop multipliers, `src/walkforward.py`), then a v2 filter A/B of 3 base configs x 4 filter stacks (`src/param_sweep.py`), ranked by Sharpe with a minimum trade-count floor.
- Cost model: slippage plus half-spread per leg plus commission (`src/costs.py`), reported at 3, 7, and 10 bps round trip. The edge clears a net Sharpe of 1.5 at 3 bps for both names and stays above 1.0 at 7 bps, and QQQ stays above 2.7 even at the 10 bps stress case.
- Deflated Sharpe: Bailey and Lopez de Prado (2014), `src/validate.py`. Headline uses a trial count of 18 (a lower bound) with an approximated trial variance. Sensitivity computed at 18, 30, 50, 100 (table above). We did not implement ONC clustering (Lopez de Prado 2018) to estimate the effective number of independent trials.
- PBO / CSCV (Bailey et al. 2017): not computed. The per-trial return series from the sweep were not preserved, only the winning config's trades. PBO is the standard companion to DSR and its absence is a gap.
- Bootstrap: IID resample of daily returns plus a moving-block variant (block = 5), 10,000 iterations each, 90% and 95% CIs, plus lag-1 autocorrelation of daily P&L.
- Permutation: sign-flip test on daily returns, 10,000 iterations, one-sided.
- ORB edge status: reported alive through 2024 to 2026 across independent backtests (Options Cafe 0DTE SPY, BreakOrb, Edgeful), with the 5-minute window consistently beating 15-minute. BreakOrb's walk-forward survival was about 0.5% of configurations, so parameter sensitivity, not the class of edge, is the primary risk.
- Walk-forward: run previously (`src/walkforward.py`). A rolling optimizer overfit hard (mean in-sample-best Sharpe about 3.5 versus out-of-sample about 0.1), so params are held fixed. The candle filter held out-of-sample on a true holdout (Sharpe 3.9 to 5.7). No standing walk-forward output file exists; it prints to stdout.
- Regime gate: an overnight-gap gate at 1.5% (today's open versus prior close). Not swept, a fixed value, and left OFF by default.
- v2 filters (VWAP, RVOL, candle): VWAP was inert, RVOL was toxic (cut roughly 60 trades to about 3 with negative Sharpe), candle helped and is the only one enabled. All three ANDed are incompatible with one-signal-per-day gating.

## 4. What we could not verify

**Data or time gated:** zero real-money trades, so the live edge is untested; real slippage versus the assumption is unmeasured; forward persistence is unknown; IEX (live) versus SIP (backtest) divergence on the opening range is not quantified.

**Slippage may be optimistic.** 3 bps is realistic for a QQQ market order mid-session, but entries fire at 09:40 into a fresh breakout. Invesco (QQQ's own issuer) recommends avoiding the first 30 minutes because spreads are wider while liquidity providers reprice, and a directional market order into momentum invites adverse selection, exactly when makers widen. Real slippage likely sits at the upper end of 3 to 7 bps or beyond, which is why the 10 bps stress case is in the table. QQQ holds a net Sharpe of 2.75 at 10 bps; SPY falls to 1.69.

**Sample size.** SPY at 46 trades is below the credibility floor (practitioner consensus is 100-plus for reliable metrics), with a win-rate binomial p of 0.09. Treat SPY as supporting evidence for the ORB edge, not an independently validated configuration. QQQ at 166 trades (binomial p 0.0007) meets minimum thresholds but remains a small sample.

**Statistical shortcuts:** deflated Sharpe uses an approximated trial variance and a trial count of 18 that is a lower bound (true implicit trials, including abandoned configs and visual inspections, are higher; ONC clustering not run); no PBO was computed because per-trial series were not saved; the bootstrap is IID, mitigated by the block variant but not replaced by PBO; the 1.5% regime threshold was not swept; new variants got full-sample stats, not a rolling walk-forward.

**Judgment we cannot supply:** whether ORB is a decayed or crowded edge now; whether QQQ is structural or a 2024 to 2025 regime artifact; whether market orders fill near the backtest level on fast breakouts; what counts as enough evidence to risk capital.

**Deferred armor (not built):** limit-style entry, absolute notional cap, kill-switch and error-streak breaker, orphan-position detection, per-symbol filter config, live leak-finder and dashboard. GitHub Actions cold-start latency is also non-deterministic (a 09:40 entry depends on the runner spinning up in time); a dedicated VPS or Raspberry Pi cron would give deterministic timing.

**Structural limitation:** this was built by an AI coding assistant (Claude) working with a domain expert in energy infrastructure, not trading. Claude verified engineering and statistical method, not market truth, and worked from summaries of the original research (Reddit post images), not the primary source. The builder has textbook knowledge, not scar tissue.

## 5. The four questions for you

> **Q1, Is the edge real?** 166 QQQ trades, net Sharpe retains about 90% of gross. 46 SPY trades with a lower and statistically weak result. Enough to risk money, or do you want more history?
>
> **Q2, Stats shortcuts.** Deflated Sharpe trial count is estimated at 18, and the bootstrap is IID (ignores serial correlation). Would you redo either? What would you use instead?
>
> **Q3, Execution realism.** Market-order entry with 3 to 7 bps slippage on QQQ at the opening range. Does that hold for real fills, or am I too optimistic?
>
> **Q4, Go or no-go.** What would you need to see before you would risk capital on this system?

## 6. File map

- `results/trades_SPY.csv`, `results/trades_QQQ.csv`: per-trade backtest results, the headline numbers. (Q1)
- `results/backtest_validation.json`: IID and block bootstrap CIs, permutation and binomial p-values, deflated Sharpe at 18/30/50/100 trials, lag-1 autocorrelation, and 4 cost tiers (gross, 3, 7, 10 bps). (Q1, Q2, Q3)
- `scripts/compute_review_metrics.py`: reproduces every figure above from the committed trade files. (Q1, Q2)
- `src/validate.py`: bootstrap, permutation, deflated Sharpe implementations. (Q2)
- `src/walkforward.py`: out-of-sample and time-stability checks; run it for the walk-forward output. (Q1, Q2)
- `src/param_sweep.py`: the filter A/B matrix. (Q1)
- `config/settings.py`: all strategy parameters in one place. (Q3, Q4)
- `src/orb_signal.py`: signal generation and trade simulation. (Q3)
- `src/risk_manager.py`: position sizing, stops, circuit breakers. (Q4)
- `src/backtest.py`: performance and Sharpe computation. (Q1, Q3)
