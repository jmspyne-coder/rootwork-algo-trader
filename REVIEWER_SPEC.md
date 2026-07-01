# Reviewer Spec: Rootwork ORB Bot

We are sourcing one experienced systematic trader to stress-test a paper-only intraday breakout system before any real capital is risked. One hour, paid. We supply a one-page brief with validated numbers, honest caveats, and the exact files to look at. Your hour is judgment, not reading code.

## The one non-negotiable

Live systematic trading with real money. Someone who has run automated strategies, watched live diverge from backtest, and felt an edge decay. Everything else is secondary.

## Strong-fit skills, in priority order

1. **Strategy statistics and anti-overfitting.** Small-sample Sharpe inference, multiple-testing corrections (deflated and probabilistic Sharpe, PBO), block bootstrap, sample-size adequacy. Enough to judge whether the numbers are trustworthy.
2. **Intraday microstructure and execution.** Breakout fill behavior, market versus limit orders, realistic slippage on SPY and QQQ at the open, IEX versus SIP data quality.
3. **Backtest methodology.** Look-ahead, survivorship, and leakage detection, in-sample versus out-of-sample discipline, backtest-to-live fidelity.
4. **Risk management for automated systems.** Sizing, halts, cross-symbol allocation, tail risk, sane pre-live limits.
5. **The ORB family specifically.** Whether opening-range breakout is a crowded or decayed edge, and how it behaves across regimes.
6. **Bonus: live bot ops and Alpaca.** Order lifecycle, unattended failure modes.

## Wrong reviewer

A discretionary chart trader, a general software engineer, or another AI. Each shares the builder's blind spots.

## Format

One hour, paid. We provide the brief, the per-trade backtest files, and the validation output up front. We want four answers: is the edge real, would you redo the stats shortcuts, is the execution assumption realistic, and what would you need to see before risking capital.
