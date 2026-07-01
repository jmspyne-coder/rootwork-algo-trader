# Trade Segmentation / Leak Finder (B5)

Net-of-3bps stats sliced by attributes available in the committed trade files. Sharpe within a thin segment is directional only.

## SPY (46 trades)

**By direction**

| direction | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| long | 21 | 71% | 4.98 | 2.7 |
| short | 25 | 52% | 0.54 | 1.09 |

**By day of week**

| day | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| Monday | 13 | 62% | -4.07 | 0.49 |
| Tuesday | 6 | 33% | 3.45 | 2.25 |
| Wednesday | 6 | 33% | 2.50 | 1.57 |
| Thursday | 8 | 75% | 8.21 | 3.39 |
| Friday | 13 | 77% | 12.16 | 6.36 |

**By opening-range width** (range_pct; <0.3% empty by the 0.3% filter)

| width | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| 0.3-0.6% | 43 | 58% | 1.06 | 1.19 |
| 0.6-1.0% | 3 | 100% | 15.25 | inf |

## QQQ (166 trades)

**By direction**

| direction | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| long | 84 | 70% | 5.07 | 2.35 |
| short | 82 | 55% | 3.06 | 1.68 |

**By day of week**

| day | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| Monday | 34 | 59% | 1.47 | 1.27 |
| Tuesday | 33 | 52% | 2.69 | 1.58 |
| Wednesday | 24 | 75% | 4.98 | 2.72 |
| Thursday | 36 | 61% | 3.66 | 1.89 |
| Friday | 39 | 69% | 6.87 | 2.79 |

**By opening-range width** (range_pct; <0.3% empty by the 0.3% filter)

| width | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| 0.3-0.6% | 152 | 62% | 3.86 | 1.93 |
| 0.6-1.0% | 13 | 69% | 3.15 | 1.72 |
| >1.0% | 1 | 100% | 0.00 | inf |

**Gap size at open:** computed below (`scripts/gap_size_segmentation.py`), which
fetches daily bars and joins the overnight gap to each trade date.

## Gap size at open (net 3 bps) — computed from daily bars


### SPY (46 trades)

| gap bucket | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| <0.3% | 9 | 56% | 0.26 | 1.04 |
| 0.3-0.7% | 11 | 55% | 3.23 | 1.97 |
| >0.7% | 26 | 65% | 2.89 | 1.61 |

### QQQ (166 trades)

| gap bucket | trades | win% | Sharpe | PF |
|---|---|---|---|---|
| <0.3% | 45 | 64% | 3.51 | 1.99 |
| 0.3-0.7% | 52 | 62% | 5.24 | 2.33 |
| >0.7% | 69 | 62% | 3.10 | 1.66 |
