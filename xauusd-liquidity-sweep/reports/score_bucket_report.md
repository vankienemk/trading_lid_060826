# Score Bucket Performance Report

**Generated**: 2026-09-04 16:04
**Dataset**: `data/processed/labeled_events.parquet`
**Total events**: 974
**Config**: `configs/baseline.yaml` (scoring.weights + score_buckets)

## Bucket Table

| Bucket | Events | Win Rate | Loss Rate | Ambiguous | Avg MFE (R) | Avg MAE (R) | Avg Net (R) | Profit Factor |
|---|---|---|---|---|---|---|---|---|
| 0-39 | 783 | 18.90% | 48.66% | 0.13% | 1.4708 | 1.5142 | -0.0045 | 0.9914 |
| 40-49 | 188 | 15.43% | 35.11% | 0.00% | 1.2315 | 1.0364 | 0.0919 | 1.2300 |
| 50-59 | 3 | 0.00% | 33.33% | 0.00% | 1.2582 | 0.6488 | -0.0419 | 0.8744 |

## Score Distribution

- Mean rule_score: 33.45
- Std rule_score: 6.82
- Min: 15.00, Max: 50.00

## Outcome Distribution

- **sl**: 448 (46.0%)
- **time**: 348 (35.7%)
- **tp**: 177 (18.2%)
- **ambiguous**: 1 (0.1%)

## Figure

`reports/figures/score_bucket_performance.png` — win rate, profit factor,
avg net result, and event distribution by score bucket.

## Notes

- Rule score range: 0-100 (guide §19)
- Buckets: 0-39 / 40-49 / 50-59 / 60-69 / 70-79 / 80-100
- Costs included in net_result_r (guide §17)
- Small sample size per bucket — interpret cautiously