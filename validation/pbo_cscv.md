# PBO / CSCV (B3)

**Status: COMPUTED (2026-07-01)** for BOTH the shipped 5-minute ORB and the
candidate Gap-Fill. Real data, over 2024-01-01 to 2026-06-01. Method: CSCV
(Bailey, Borwein, Lopez de Prado, Zhu 2017), `scripts/cscv.py`.

## Headline: the shipped ORB is NOT overfit; Gap-Fill IS

| strategy | configs (>=30 trades) | PBO | degradation slope | mean OOS Sharpe of pick | verdict |
|---|---|---|---|---|---|
| **5-minute ORB (shipped)** | 54 | **0.057** | -0.55 | **+1.66** | holds up |
| Gap-Fill (candidate) | 567 of 729 | 0.429 | -1.12 | -0.19 | overfit, held back |

The direct ORB PBO closes the gap this file previously flagged. ORB PBO of 0.057
means only a ~6% chance the in-sample-best config underperforms the out-of-sample
median, and the selected config keeps a positive OOS Sharpe (+1.66 from +2.10 IS).
That is a strong, independent complement to the ORB's deflated Sharpe
(`dsr_sensitivity.csv`) and bootstrap CIs (`block_bootstrap.csv`): three separate
overfitting lenses now agree the ORB edge is real. Gap-Fill fails the same test
(PBO 0.43, negative OOS), which is why it is not deployed.

## ORB CSCV detail (S=8)

Universe: `scripts/orb_pbo_sweep.py` — QQQ, 54 configs (or_minutes x reward-risk x
ATR-stop-mult x candle on/off), each with its per-config daily return series
persisted to `validation/orb_sweep_returns.csv`. 475 trading days, 70 splits.

| metric | value | reading |
|---|---|---|
| PBO | 0.057 | very low; IS winners generalize |
| Median logit(lambda) | +2.10 | positive: IS-best ranks high OOS |
| Degradation slope | -0.55 | mildly negative but PBO stays low |
| P(OOS Sharpe of pick < 0) | 0.043 | winner is almost never an OOS loser |
| Mean IS / OOS Sharpe of pick | 2.10 / 1.66 | ~79% of edge retained OOS |

Caveat: 54 configs is a smaller universe than the gap sweep's 729, so the ORB PBO
is directional rather than exhaustive; it deliberately includes the known-losing
15/30-minute windows so the selection test is honest. The signal is clear
regardless of that, and stable with the other two overfitting measures.

## Gap-Fill CSCV detail (S = 8, as specified)

CSCV per Bailey, Borwein, Lopez de Prado, Zhu (2017): split the aligned daily
return matrix into S=8 disjoint time segments, take every C(8,4)=70 way of
assigning half to in-sample and half to out-of-sample, pick the highest-IS-Sharpe
config on each split, and measure its OOS rank.

| metric | value | reading |
|---|---|---|
| Configs (>= 30 trades) | 567 of 729 | credible universe |
| Trading days | 279 | union of days any config traded |
| Splits evaluated | 70 | C(8,4) |
| **PBO** | **0.429** | not low; near the caution line |
| Degradation slope (OOS on IS) | **-1.12** | negative: IS strength predicts OOS weakness |
| Mean IS Sharpe of the pick | 2.33 | what the sweep would sell you |
| Mean OOS Sharpe of the pick | **-0.19** | what you would actually get |
| P(OOS Sharpe of pick < 0) | 0.50 | coin-flip whether the winner is a loser |

## Verdict: overfit, do not trust the sweep winner

PBO of 0.429 is not in the comfortable zone. The engineer's own bands put <0.3 as
manageable and >0.5 as a red flag; 0.429 sits in the uncomfortable middle, and the
supporting diagnostics resolve the ambiguity against the strategy:

- The **degradation slope is negative (-1.12).** In a healthy strategy the
  in-sample-better configs stay better out of sample (positive slope). Here the
  relationship inverts: chasing the best backtest Sharpe actively selects for
  worse live performance. That is the signature of fitting noise.
- The **mean OOS Sharpe of the selected config is negative (-0.19).** The sweep
  hands you a config with a ~2.3 IS Sharpe; out of sample that same config
  averages a small loss. The headline Gap-Fill winner (g504: stop 0.75x, 2.5:1
  RR, both directions, IS Sharpe 3.73) is exactly the kind of pick this catches.
- Half the splits put the IS winner underwater OOS.

Read together: the Gap-Fill sweep's apparent 3.7 Sharpe is substantially a
selection artifact. Picking the in-sample optimum does not buy positive
out-of-sample expectancy.

## Robustness across segment counts

The headline is S=8. The conclusion does not depend on that choice.

| S | splits | PBO | degradation slope | mean OOS Sharpe of pick |
|---|---|---|---|---|
| 6 | 20 | 0.55 | -0.45 | -0.22 |
| 8 | 70 | 0.43 | -1.12 | -0.19 |
| 10 | 252 | 0.51 | -0.92 | -0.15 |
| 12 | 924 | 0.46 | -0.88 | -0.10 |

PBO stays in the 0.43 to 0.55 band, the degradation slope stays negative
throughout, and the selected config's mean OOS Sharpe stays negative throughout.
The overfitting signal is stable, not an artifact of the segment count.

## What this means for deployment

Gap-Fill is not ready to trade on the strength of its sweep. Before it gets
capital it needs either a walk-forward that holds up on a true out-of-sample
period, or a structural reason to prefer one config a priori rather than by
Sharpe ranking. This does not touch the ORB, which is the shipped strategy and is
governed by its own DSR and bootstrap evidence. If anything it validates the
decision to ship the ORB and hold Gap-Fill back.

## The ORB gap is now closed

The direct ORB PBO (headline above) was produced by `scripts/orb_pbo_sweep.py`,
which sweeps 54 ORB configs and persists each one's daily return series to
`validation/orb_sweep_returns.csv`, then `scripts/cscv.py` runs on it. The CSCV
code is strategy-agnostic (it takes `--returns`/`--results`); only the input
changes. To widen the ORB universe further, extend the grid in
`orb_pbo_sweep.py`.

## Reproduction

```
# ORB (shipped): sweep persists per-config returns (54 configs), then CSCV
python scripts/orb_pbo_sweep.py --ticker QQQ --start 2024-01-01 --end 2026-06-01
python scripts/cscv.py --returns validation/orb_sweep_returns.csv --results validation/orb_sweep_results.csv --segments 8

# Gap-Fill: sweep persists per-config daily returns (729 configs)
python -m src.param_sweep_gap --ticker QQQ --start 2024-01-01 --end 2026-06-01

# 2. CSCV / PBO, S=8 (implementation in scripts/cscv.py; core loop ~50 lines)
python scripts/cscv.py --segments 8
```

Inputs consumed: `validation/gap_sweep_returns.csv` (matrix) and
`validation/gap_sweep_results.csv` (the >= 30-trade credible-universe filter).
