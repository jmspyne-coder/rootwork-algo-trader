# Gap-Fill v1 — Validation Results (QQQ)

Default params (ATR 1.0x stop, 2:1 RR, gap band 0.3%-1.5%, fade), net of 3 bps, 2024-01-01..2026-06-01. `results/trades_gap_QQQ.csv`.


- Trades: 117 | net Sharpe @3bps: 1.92 | correlation with ORB: -0.01 (overlap 35 shared days)


## Slippage cliff (net Sharpe)

| bps | 0 | 3 | 5 | 7 | 10 | 15 | 20 | 25 | 30 |
|---|---|---|---|---|---|---|---|---|---|
| QQQ gap-fill | 2.45 | 1.92 | 1.56 | 1.21 | 0.67 | -0.22 | -1.11 | -1.99 | -2.88 |

Kill level: ~13.8 bps.


## Deflated Sharpe by trial count

| N trials | 18 | 100 | 747 (combined ORB+gap sweep) |
|---|---|---|---|
| DSR | 0.30 | 0.12 | 0.03 |


## Success criteria (spec Section 10)

| Criterion | Result | Pass |
|---|---|---|
| Net Sharpe >= 1.5 @3bps | 1.92 | YES |
| Trade count >= 30 | 117 | YES |
| Correlation w/ ORB < 0.3 | -0.01 | YES |
| Slippage kill >= 10 bps | 13.8 bps | YES |
| DSR survives @combined N=747 | 0.03 | NO |

**4/5 criteria pass** (plus max drawdown 4.4% <= 5%). But the deflated-Sharpe test is the decisive anti-overfitting gate, and it FAILS: DSR is 0.30 at N=18 (a fair a-priori count) and collapses to 0.03 at N=747. Even before the harsh combined haircut, a 0.30 probability that the true Sharpe > 0 is weak (ORB QQQ is 0.92 at N=18). The N=747 figure overcounts independent trials (the 729 sweep configs are highly correlated; ONC clustering would give far fewer), so 0.03 is a floor, not the true number, but even the fair read is not convincing.


**Verdict: HOLD — strong diversification, but the edge is statistically fragile.**

Read: gap-fill is a genuinely uncorrelated (r = -0.01), slippage-robust (kill 13.8 bps) second stream, which is exactly what we wanted structurally. But its per-trade edge is thin (mostly EOD closes, few target hits) and does not survive deflation the way ORB does.

The 729-config sweep + PBO/CSCV was subsequently run (`pbo_cscv.md`) and reaches the same verdict from the other direction: PBO 0.429 with a negative degradation slope (-1.12) and a mean out-of-sample Sharpe of the selected config of -0.19 — picking the sweep's in-sample winner buys negative OOS expectancy. So both the default config (weak DSR) and the optimized config (overfit sweep) point the same way.

Recommendation: do NOT deploy gap-fill. It is a promising, well-diversifying idea, not a proven edge. Revisit only with a walk-forward that holds on a true out-of-sample period, or an a-priori (not Sharpe-ranked) config, and consider adding SPY for more samples. This does not touch the shipped 5-minute ORB.
