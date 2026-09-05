# Freeze Status — v2.0.0

**Ngày freeze:** 2026-09-04  
**Pipeline:** V2 (nguoc_trend + max_pen≤0.20 + R:R configurable)  
**Baseline gốc:** v1.2.0 (frozen dataset 974 events, 13 md5 spec)  

---

## V2 Artifact Checksums

### Data Files

| File | MD5 | Status |
|---|---|---|
| `pipeline_v2/data/events_v2.parquet` | `e175e1fbe02e5d99c915b4908ad3861a` | ✅ |
| `pipeline_v2/data/labeled_events_v2.parquet` | `da92633ea99b73e0fe6dc07a2417b026` | ✅ |
| `pipeline_v2/artifacts/datasets/liquidity_sweep_events_v2.parquet` | `da92633ea99b73e0fe6dc07a2417b026` | ✅ |

### Model Files

| File | MD5 | Status |
|---|---|---|
| `pipeline_v2/artifacts/models/model.pkl` | `c46f53b4ae745f357aadb3c53ac33148` | ✅ |
| `pipeline_v2/artifacts/models/calibrator.pkl` | `324d939106fc5172a302b5d68b81f9d1` | ✅ |

### Metric Reports

| File | MD5 | Status |
|---|---|---|
| `pipeline_v2/reports/metrics/test_metrics.json` | `b7c79fdb51edb4a02588360a87e67352` | ✅ |
| `pipeline_v2/reports/metrics/walk_forward_report.json` | `e70cefb297c5664791afc9d2be303c6b` | ✅ |
| `pipeline_v2/reports/metrics/top_prob_subset_analysis.json` | `f1ae70b4fe8ce6bd289de7773e2bcb41` | ✅ |
| `pipeline_v2/reports/metrics/score_bucket_report.json` | `a1b92f87d6bb12300b907721a44b7893` | ✅ |
| `pipeline_v2/reports/metrics/v2_pipeline_summary.json` | `ced7b7f867bd5bed857c82d445fc0ca7` | ✅ |
| `pipeline_v2/reports/analysis/v2_analysis.json` | `b8ef3351a819add8829147a65672f66e` | ✅ |

### Deterministic Verification

| File | Byte-identical runs 1&2 | Status |
|---|---|---|
| events_v2.parquet | ✅ match | ✅ |
| liquidity_sweep_events_v2.parquet | ✅ match | ✅ |
| labeled_events_v2.parquet | ✅ match | ✅ |
| model.pkl | ✅ match | ✅ |
| calibrator.pkl | ✅ match | ✅ |
| test_metrics.json | ✅ match | ✅ |
| walk_forward_report.json | ❌ non-det (CV shuffle) | ⚠️ same issue as v1 baseline |
| top_prob_subset_analysis.json | ✅ match | ✅ |
| score_bucket_report.json | ✅ match | ✅ |

---

## V1 Frozen Baseline Integrity

The v2 pipeline run (t12) was designed to preserve v1.2.0 frozen artifacts by using a `.baseline_backup` restore mechanism in `step_top_prob` and writing v2 outputs to isolated `pipeline_v2/` paths. However, some top-level `reports/` files were overwritten during the run.

| Frozen v1.2.0 File | Expected MD5 | Actual MD5 | Match |
|---|---|---|---|
| artifacts/models/model.pkl | `69d65883bef616ebe23993478629fe45` | `69d65883bef616ebe23993478629fe45` | ✅ |
| artifacts/models/calibrator.pkl | `ca1d7e61b4a47f376e4c0d0a6a136615` | `ca1d7e61b4a47f376e4c0d0a6a136615` | ✅ |
| reports/metrics/test_metrics.json | `3087b9e3639df95f2b5eb4d033175c4a` | `3087b9e3639df95f2b5eb4d033175c4a` | ✅ |
| data/processed/labeled_events.parquet | `fac22cea82ef7d0df646a9106239a70c` | `fac22cea82ef7d0df646a9106239a70c` | ✅ |
| artifacts/datasets/liquidity_sweep_events.parquet | `fac22cea82ef7d0df646a9106239a70c` | `fac22cea82ef7d0df646a9106239a70c` | ✅ |
| reports/figures/score_bucket_performance.png | `94beafe7766a7c6c2038f415954cb54d` | `94beafe7766a7c6c2038f415954cb54d` | ✅ |
| reports/score_bucket_report.json | `a1b92f87d6bb12300b907721a44b7893` | `a1b92f87d6bb12300b907721a44b7893` | ✅ |
| reports/walk_forward_report.json | `59e67682b1e626d5f8f6ecf6b3810e94` | `9f0a1a28ae424320d513ca393f75b0d0` | ❌ |
| reports/walk_forward_report.md | `2a1e726fb314bc8176c9ffae3478371a` | `7479f0f7f10fd76aceba8008c9511da2` | ❌ |
| reports/figures/walk_forward_folds.png | `cc82fd73a0f14161b262286d9e00d2af` | `5b45082a3910137ae8e215aaed8c3339` | ❌ |
| reports/top_prob_subset_analysis.json | `d360daa483c8fd10caaffbae49730fac` | **MISSING** | ❌ |
| reports/top_prob_subset_analysis.md | `0ef590b8204425422b2255bb03623b9b` | **MISSING** | ❌ |
| reports/score_bucket_report.md | `6d0ce7062c38d1c07be70a667ed1137a` | `03d35324f651a4b074b26d9934129e7c` | ❌ |

**Summary:** 7/13 frozen md5 intact (core model + data files). 6/13 changed or missing (top-level report files overwritten by v2 pipeline `step_top_prob` runner). The `.baseline_backup` restore mechanism did not fully protect report files.

---

## Run Metadata

| Field | Value |
|---|---|
| Total bars processed | 99,692 |
| V2 raw events | 462 |
| V2 confirmed events | 121 |
| V2 reduction vs v1.2.0 | 87.6% (from 974 to 121) |
| Target R:R | 2.0 (configurable via --target-r) |
| CLI entry | `--v2` flag on `run_pipeline_v2.py` |
| Filter stack | nguoc_trend (H1) + max_penetration_atr ≤ 0.20 + score filtering |