# Top-Probability Subset Analysis

**Model**: Clean (30 numeric features from registry 32; 2 categorical excluded: level_type, volatility_regime)
**Total events**: 974
**Baseline (all events)**: win rate (hit 2R tp) = 0.1817; P(net_result_r > 0) = 0.4224
**Net result source**: real `net_result_r` column (1 ambiguous event excluded from net-based stats)

## In-Sample Analysis (Upper Bound - NOT Live Edge)

**Warning**: In-sample probabilities are optimistically biased. Use OOS results for realistic expectations.

| Threshold | Count | Win Rate | P(net>0) | Avg Net (R) | Profit Factor |
|---|---|---|---|---|---|
| >= 0.1 | 746 | 0.217 | 0.431 | 0.065R | 1.13 |
| >= 0.15 | 557 | 0.250 | 0.410 | 0.070R | 1.13 |
| >= 0.2 | 372 | 0.290 | 0.442 | 0.160R | 1.30 |
| >= 0.25 | 218 | 0.353 | 0.491 | 0.335R | 1.70 |
| >= 0.3 | 127 | 0.402 | 0.512 | 0.425R | 1.93 |
| >= 0.35 | 60 | 0.450 | 0.550 | 0.536R | 2.23 |
| >= 0.4 | 26 | 0.500 | 0.538 | 0.574R | 2.35 |

## Out-of-Sample Analysis (Realistic)

**Source**: 5-fold cross-validation (no leakage)

| Threshold | Count | Win Rate | P(net>0) | Avg Net (R) | Profit Factor |
|---|---|---|---|---|---|
| >= 0.1 | 714 | 0.200 | 0.412 | 0.009R | 1.02 |
| >= 0.15 | 537 | 0.224 | 0.386 | -0.005R | 0.99 |
| >= 0.2 | 361 | 0.258 | 0.422 | 0.090R | 1.17 |
| >= 0.25 | 223 | 0.291 | 0.440 | 0.162R | 1.30 |
| >= 0.3 | 125 | 0.280 | 0.448 | 0.167R | 1.32 |
| >= 0.35 | 69 | 0.304 | 0.420 | 0.130R | 1.23 |
| >= 0.4 | 32 | 0.312 | 0.438 | 0.149R | 1.28 |

## Key Findings

- **OOS (real net_result_r)**: Model filtering achieves PF > 1 at threshold >= 0.3: 125 events (12.8%), win=0.280, P(net>0)=0.448, PF=1.32
- 'Win rate' here = fraction hitting the 2R target (binary label), which differs from P(net_result_r > 0).
- Baseline all 974 events: PF ~1 (+0.014R cost-free, real net_result_r)
- Small sample sizes at high thresholds → results may be noisy (Wilson CI spans baseline for most subsets)
- In-sample results are optimistically biased; trust OOS numbers

## Cost Considerations

- Above metrics are cost-free (gross R)
- With typical costs (spread + slippage ~0.05R per trade), net expectancy decreases
- At th>=0.25 OOS (real net_result_r): small n; after 0.05R cost the edge is marginal
- Model provides ranking value but the edge is small after costs; do NOT trade the ML signal as-is