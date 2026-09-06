# Score Bucket Performance Report

**Generated**: 2026-09-04 22:30
**Dataset**: `data/processed/labeled_events.parquet`
**Total events**: 121
**Config**: `configs/baseline.yaml` (scoring.weights + score_buckets)

## Bucket Table

| Bucket | Events | Win Rate | Loss Rate | Ambiguous | Avg MFE (R) | Avg MAE (R) | Avg Net (R) | Profit Factor |
|---|---|---|---|---|---|---|---|---|
| 0-39 | 94 | 29.79% | 40.43% | 0.00% | 1.9310 | 1.2789 | 0.2600 | 1.5750 |
| 40-49 | 27 | 14.81% | 25.93% | 0.00% | 1.3223 | 0.8895 | 0.1685 | 1.5375 |

## Score Distribution

- Mean rule_score: 34.56
- Std rule_score: 6.63
- Min: 17.00, Max: 47.00

## Outcome Distribution

- **sl**: 45 (37.2%)
- **time**: 44 (36.4%)
- **tp**: 32 (26.4%)

## Figure

`reports/figures/score_bucket_performance.png` — win rate, profit factor,
avg net result, and event distribution by score bucket.

## Notes

- Rule score range: 0-100 (guide §19)
- Buckets: 0-39 / 40-49 / 50-59 / 60-69 / 70-79 / 80-100
- Costs included in net_result_r (guide §17)
- Small sample size per bucket — interpret cautiously