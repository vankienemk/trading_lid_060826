# Walk-Forward Validation Report

**Generated**: 2026-09-04 23:38
**Dataset**: `data/processed/labeled_events.parquet` (974 events)
**Features**: 30 causal features (no look-ahead)
**Config**: `configs/baseline.yaml` (splitting + model sections)
**Seed**: 42 (frozen per baseline v1.1.0-freeze)

## Aggregate Metrics (Out-of-Sample)

| Metric | Mean | Std | Min | Max | N Folds |
|---|---|---|---|---|---|
| accuracy | 0.8268 | 0.0210 | 0.8041 | 0.8660 | 5 |
| brier_score | 0.1276 | 0.0107 | 0.1084 | 0.1410 | 5 |
| f1 | 0.1082 | 0.0749 | 0.0000 | 0.2353 | 5 |
| positive_rate | 0.1814 | 0.0168 | 0.1546 | 0.2062 | 5 |
| pr_auc | 0.3537 | 0.0315 | 0.3031 | 0.3943 | 5 |
| precision | 0.7000 | 0.4000 | 0.0000 | 1.0000 | 5 |
| recall | 0.0595 | 0.0427 | 0.0000 | 0.1333 | 5 |
| roc_auc | 0.7211 | 0.0146 | 0.7015 | 0.7433 | 5 |
| test_size | 97.0000 | 0.0000 | 97.0000 | 97.0000 | 5 |
| train_size | 584.0000 | 137.1787 | 390.0000 | 778.0000 | 5 |
| val_size | 97.0000 | 0.0000 | 97.0000 | 97.0000 | 5 |

## Per-Fold Details

### Fold 0
- **Train size**: 390 events
- **Validation size**: 97 events
- **Best model**: logistic_regression
- **PR-AUC**: 0.3943
- **ROC-AUC**: 0.7433
- **Brier Score**: 0.1288
- **F1**: 0.1053

### Fold 1
- **Train size**: 487 events
- **Validation size**: 97 events
- **Best model**: logistic_regression
- **PR-AUC**: 0.3787
- **ROC-AUC**: 0.7263
- **Brier Score**: 0.1410
- **F1**: 0.0952

### Fold 2
- **Train size**: 584 events
- **Validation size**: 97 events
- **Best model**: logistic_regression
- **PR-AUC**: 0.3488
- **ROC-AUC**: 0.7093
- **Brier Score**: 0.1084
- **F1**: 0.2353

### Fold 3
- **Train size**: 681 events
- **Validation size**: 97 events
- **Best model**: random_forest
- **PR-AUC**: 0.3436
- **ROC-AUC**: 0.7015
- **Brier Score**: 0.1273
- **F1**: 0.1053

### Fold 4
- **Train size**: 778 events
- **Validation size**: 97 events
- **Best model**: random_forest
- **PR-AUC**: 0.3031
- **ROC-AUC**: 0.7250
- **Brier Score**: 0.1323
- **F1**: 0.0000


## Figure

`reports/figures/walk_forward_folds.png` — metrics across folds.

## Notes

- Expanding window: each fold trains on all prior data + step expansion
- Purge + embargo applied between train and validation (embargo_bars=32)
- OOS metrics computed on validation set for each fold
- Stability assessed via mean/std of metrics across folds