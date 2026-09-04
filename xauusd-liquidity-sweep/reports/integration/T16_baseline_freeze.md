# Baseline Freeze — XAUUSD Liquidity Sweep System (t16)

> ⚠️ **SUPERSEDED — do not read the metrics below as current.**
> This v1.1.0 freeze was trained on **45 features incl. 19 future-information
> columns (label leakage, T21)**; every model metric in this file
> (PR-AUC 0.876 / ROC-AUC 0.981 / WF 0.960–0.968 / F1 0.845) is **invalid**.
> The baseline was re-frozen as **v1.2.0 with honest metrics**
> (30 registry features, test PR-AUC 0.2275 / ROC-AUC 0.6041; WF OOS PR-AUC
> mean 0.3552; artifacts md5-frozen) on 2026-09-04 — see
> `reports/freeze_status_v1.2.0.md`. Event/strategy facts below (974 events,
> costs=0 placeholder config, seed 42, group_rule=first) remain valid.

**Baseline version (superseded):** `v1.1.0-freeze` · Date: 2026-09-02 · Owner: Agent 0 (Project Lead)

## Pipeline wiring (all four pipelines active)

| Pipeline | CLI | Status | Output |
|---|---|---|---|
| 1 — Data audit | `xauusd-audit` | ✅ | `data/processed/xauusd_m15.parquet` + `reports/data_quality/xauusd_m15_quality.json` |
| 2 — Events | `xauusd-events` | ✅ | `data/processed/events.parquet` (3,772 events, 974 confirmed; `group_rule=first` causal, F1) |
| 3 — Labeled dataset | `xauusd-dataset` | ✅ | `artifacts/datasets/liquidity_sweep_events.parquet` (974 confirmed-labeled, 94 cols) |
| 4 — Train | `xauusd-train` | ✅ | `artifacts/models/{model,calibrator}.pkl` + `reports/metrics/test_metrics.json` |

## Final metrics (strategy B — entry after confirmation, costs included)

- Labeled events: **974** (primary `outcome_2r_h16`: sl 448 / time 348 / tp 177 / ambiguous 1)
- Net expectancy (net_result_r, after costs): **+0.014 R** overall; mean win rate 18.2% at 2R
- Test set (195 events, single final evaluation, rule 30.5/30.6): best = **random_forest**
  - PR-AUC **0.876** · ROC-AUC 0.981 · Accuracy 0.944 · Precision 0.833 · Recall 0.857 · F1 0.845 · Brier 0.041
  - Walk-forward OOS (2 folds): PR-AUC mean **0.960** (W1 closed by t17/t18)
- Rule-score buckets: 0–39 → −0.0045R (783 ev); 40–49 → **+0.0919R** (188 ev); monotonic signal present
- A/B Phase 5 (cost-free, pre-scoring): A 3,774 trades −0.043R vs B 974 trades −0.016R → **B frozen as official baseline**

## Quality gates passed (t1+t15+t18+t19)

- ruff lint ✅ · mypy src ✅ (44 files, incl. sklearn/catboost/lightgbm overrides) 
- pytest full suite ✅ (359+ passed at t15; integration additions green) · no-lookahead suite ✅ (18 tests, marker)
- Bias audit (t15): PASS with W1 walk-forward **closed** (t17+t18 verified), W3 mypy stubs **fixed** (t19), W2 RF n_jobs non-determinism accepted + documented (t19 seed policy: `random_state=42`; differences ~1e-16)

**N-findings disposition (t16, condition F3):**
- **N1** (equal-level price/envelope frozen at emission; later touches grow `touch_count` only) — **ACCEPTED**: the envelope is the *formation* view at emission time; later touches beyond the envelope are intentionally not retro-fitted, keeping `known_at`/price causal. Lifetime aggregation would be a feature-group change out of baseline scope (future work).
- **N2** (structure_break inlines swing fractal max_age=400 vs registry 200) — **ACCEPTED**: N2 affects only the confirmation *structure_break* trigger; baseline number of confirmed events (974) uses the configured path; duplication is a maintenance concern tracked for Phase 8+ cleanup, not a bias defect (confirmed by t11 audit).
- **N3** (tz-naive confirmation_time when zero confirm) — **MITIGATED** (feature pipeline defensive tz localization; no impact on real path).
- Schema conformance: event table == `schema.EVENT_COLUMNS` (+ §6 confirmations); dataset == §7/§8/§9 columns

## Freeze decisions

1. **Baseline strategy = B (confirmed events)** — better win rate + expectancy, fewer trades, causal.
2. **`sweep.group_rule: "first"`** is the frozen default (config-driven, QA F1); deepest_penetration /
   strongest_reclaim remain opt-in experiment-only.
3. **Direction vocabulary** frozen at sweep-side (`bullish`/`bearish`) in the event table, with
   `normalize_event_direction` as the canonical bridge to trade-side (`long`/`short`).
4. **Cost model frozen** (half-spread/slippage/commission per guide §17; `net_result_r` is the
   cost-adjusted headline metric). Current config costs are zero-cost placeholders in the demo;
   a non-zero cost config is required before paper trading (Phase 8).
5. Known honest finding: with current zero-cost config the confirmed baseline is *slightly positive*
   (+0.014R); with realistic costs it may go negative — paper trading must re-measure with real
   spread values (spread column exists in the raw export).

## Final deliverables (guide §33) — all present

```text
data/processed/xauusd_m15.parquet            ✅
artifacts/datasets/liquidity_sweep_events.parquet ✅ (974 events)
artifacts/models/model.pkl, calibrator.pkl   ✅
artifacts/feature_schemas/features.json      ✅ (32 features, all causal)
reports/data_quality.json                    ✅ (+ canonical copy under reports/data_quality/)
reports/manual_review.csv (v1) + _v2.csv     ✅ (160 + 160 rows)
reports/bias_audit.md                        ✅
reports/metrics/test_metrics.json            ✅
reports/figures/calibration_curve.png        ✅
reports/figures/equity_curve_r.png           ✅
reports/figures/score_bucket_performance.png ✅
reports/event_charts/ (v1+v2)                ✅
reports/verification_checklist.json          ✅ (t15: 29 pass / 1 fixed / 5 resolved by t16)
```

## Reproducibility

All four pipelines read the merged config (`configs/baseline.yaml` + overrides),
`project.random_seed: 42`, deterministic event ids (`SWP-000000..`), deterministic
walk-forward folds and sampling seeds (`review seeds` documented in t7/t12). Run order:
`xauusd-audit && xauusd-events && xauusd-dataset && xauusd-train` (each ~1–15 min on the
99,692-bar dataset; registry/features steps dominate).

### Seed policy & W2 (RF non-determinism, documented acceptance)

`RandomForestClassifier(n_jobs=-1)` (config `model.random_forest`) shows
~1e-16-level non-determinism across fits on the same data (predictions
identical, metrics reproducible to 4 decimals; W2, low). Documented policy:

- **Frozen default**: `random_state=42` + `n_jobs=-1` — reproducible to 4
  decimal places (adequate for the baseline report).
- **Bit-exact alternative** (for strict re-runs / audit): set
  `model.random_forest.n_jobs: 1` in an override config → fits become
  bit-exact on the same machine (documented by t19).
- All other stages (LR, calibration, split, walk-forward folds) are fully
  deterministic under `random_seed=42`.

## Integration fixes applied in t16

- Wire `xauusd-dataset` (Pipeline 3) and `xauusd-train` (Pipeline 4) from stubs to real.
- `build_events_cli` reads `cfg["sweep"]["group_rule"]` and passes it to `build_sweep_events`
  (F1 config-driven closure, verified by `tests/test_build_events_cli.py`).
- **PR-AUC sign bug fixed** in `src/modeling/train.py::_compute_pr_auc` (negative values from
  trapezoid over decreasing recall → `average_precision_score`; reported to Agent 6, t14 module).
- `docs/SCHEMAS.md` §11/§5 reconciled (events path, data-quality path, model artifacts).
- New: `src/visualization/equity_curve.py` + `reports/figures/equity_curve_r.png` (§33 item
  previously missing), `reports/score_bucket_report.md` (real data), `tests/test_pipeline_clis.py`.