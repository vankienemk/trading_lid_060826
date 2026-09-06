# Top-Probability Subset Analysis

**Model**: Clean (30 numeric features from registry 32; 2 categorical excluded: level_type, volatility_regime)
**Total events**: 121
**Baseline (all events)**: win rate (hit 2R tp) = 0.2645; P(net_result_r > 0) = 0.4876
**Net result source**: real `net_result_r` column (0 ambiguous event excluded from net-based stats)

## In-Sample Analysis (Upper Bound - NOT Live Edge)

**Warning**: In-sample probabilities are optimistically biased. Use OOS results for realistic expectations.

| Threshold | Count | Win Rate | P(net>0) | Avg Net (R) | Profit Factor |
|---|---|---|---|---|---|
| >= 0.1 | 78 | 0.410 | 0.551 | 0.488R | 2.21 |
| >= 0.15 | 65 | 0.477 | 0.585 | 0.664R | 2.84 |
| >= 0.2 | 55 | 0.527 | 0.636 | 0.808R | 3.57 |
| >= 0.25 | 45 | 0.600 | 0.667 | 0.977R | 4.43 |
| >= 0.3 | 41 | 0.634 | 0.707 | 1.097R | 5.58 |
| >= 0.35 | 35 | 0.686 | 0.771 | 1.284R | 8.74 |
| >= 0.4 | 33 | 0.697 | 0.758 | 1.285R | 8.30 |

## Out-of-Sample Analysis (Realistic)

**Source**: 5-fold cross-validation (no leakage)

| Threshold | Count | Win Rate | P(net>0) | Avg Net (R) | Profit Factor |
|---|---|---|---|---|---|
| >= 0.1 | 79 | 0.354 | 0.519 | 0.400R | 2.01 |
| >= 0.15 | 66 | 0.379 | 0.530 | 0.432R | 2.09 |
| >= 0.2 | 56 | 0.411 | 0.536 | 0.510R | 2.35 |
| >= 0.25 | 52 | 0.385 | 0.500 | 0.434R | 2.07 |
| >= 0.3 | 44 | 0.409 | 0.523 | 0.533R | 2.45 |
| >= 0.35 | 39 | 0.410 | 0.513 | 0.545R | 2.51 |
| >= 0.4 | 35 | 0.457 | 0.571 | 0.698R | 3.23 |

## Key Findings

- **OOS (real net_result_r)**: Model filtering achieves PF > 1 at threshold >= 0.4: 35 events (28.9%), win=0.457, P(net>0)=0.571, PF=3.23
- 'Win rate' here = fraction hitting the 2R target (binary label), which differs from P(net_result_r > 0).
- Baseline all 974 events: PF ~1 (+0.014R cost-free, real net_result_r)
- Small sample sizes at high thresholds → results may be noisy (Wilson CI spans baseline for most subsets)
- In-sample results are optimistically biased; trust OOS numbers

## Cost Considerations

- Above metrics are cost-free (gross R)
- With typical costs (spread + slippage ~0.05R per trade), net expectancy decreases
- At th>=0.25 OOS (real net_result_r): small n; after 0.05R cost the edge is marginal
- Model provides ranking value but the edge is small after costs; do NOT trade the ML signal as-is