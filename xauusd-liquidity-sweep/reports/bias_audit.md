# Bias Audit Report — QA / Bias Auditor (Agent 7)

Project: XAUUSD Liquidity Sweep Detection & Scoring (M15)
Owner: QA_Auditor (Agent 7). Independent of detector/feature/label/model code.
This is the final bias audit, superseding the t5/t11/t15/t18 review rounds and
covering the whole pipeline, **including the frozen artifacts** (t21).
Last updated: 2026-09-04 (team v2 t3) — W4 fully resolved; baseline **re-frozen as
v1.2.0 with honest metrics**; see §8.8 and `reports/freeze_status_v1.2.0.md`.

## ⛔ CRITICAL FINDING (t21) — label leakage in the frozen model.pkl

The frozen baseline `artifacts/models/model.pkl` was trained on **45 features,
of which 19 are future-information (post-entry) columns** that leak the label
into the model. This **overrides the earlier "no leakage" conclusion of t15/t18**,
which audited only the feature-build modules, not the train-time feature
selection. The model's reported metrics (test ROC-AUC 0.98, PR-AUC 0.876;
walk-forward OOS PR-AUC mean 0.968, Brier 0.0177) are therefore **inflated and
invalid** as an out-of-sample estimate.

See §8 "Label-leakage audit (t21)" for the full list, root cause, and the
follow-on discovery that the attempted whitelist fix is itself broken (registry
path bug) and still leaks `bars_held`.

## Final verdict (bias/leakage)

**Feature-build modules (data → levels → detector → confirmation → features →
labeling → scoring): causal.** No look-ahead in the per-bar/event feature build.

**Model training / frozen artifact: LEAKED (CRITICAL).** The frozen model must
be regenerated after the whitelist fix is corrected; the current baseline must
not be considered a valid result until then.

## Definition of Done status

`reports/verification_checklist.json` — see it for per-item status. The leakage
finding re-opened the freeze (v1.1.0 invalid) and is now **RESOLVED**: with the
v1.2.0 re-freeze (2026-09-04, team v2 t1+t2) `model.pkl` carries only the 30
registry features, all artifacts were regenerated with train-only imputation and
md5-frozen — DoD validly met (§8.8, `reports/freeze_status_v1.2.0.md`).

## 1. Audit method

Guide §25.2 truncation invariance (full-frame vs truncated-prefix outputs must
be byte-identical at rows `<= cut`), run independently of module owners on
synthetic frames and the real 99,692-bar XAUUSD M15 frame, plus a positive
control proving the harness detects a centered-window leak.

## 2. Per-module audit

| Module | Task | Causality verdict | Key evidence |
|--------|------|-------------------|--------------|
| Data (loader/validator/resampler) | t2 | PASS | UTC normalization, unique monotonic index, gap report; resampler closed-HTF + previous-day causal |
| ATR / rolling levels | t3 | PASS | rolling stats, `shift(1)`, no `center=True`, prefix-invariant |
| Sweep detector + dedup | t4 | PASS | causal flags; cooldown/run-grouping default `group_rule="first"` (F1) |
| Swing/equal/prev-day/H1 levels + registry | t8 | PASS | `known_at` correct per type; `level_state_at_bar` uses bars `<= bar_pos` only |
| Confirmation + entry schedule | t9 | PASS | run-first-bar anchor; break test reads bars `[anchor..k-1]`; entry = open of candle after decision |
| Feature pipeline | t10 | PASS | level features via `level_state_at_bar`; closed H1/H4 + prior completed day; session flags pure timestamp; no outcome/mfe/mae columns |
| Labeling (triple barrier + costs) | t6 | PASS | entry/stop/target causal; same-bar policy; no access past dataset end; cost_r = 2*(half_spread+slippage)/risk + commission |
| Rule scoring | t13 | PASS | derived from causal event features only |
| Modeling (split + train) | t14 | PASS (split/calib) | time split (no random), purge+embargo, banned-feature drop; see §6 for walk-forward gap |

## 3. Bias dimensions (guide §5.8) — all clear

- **Look-ahead** — no centered windows, no negative shift, no full-dataset
  percentile/scaling; all rolling stats causal; `known_at` gating enforced.
- **Data leakage / label contamination** — feature matrix carries no
  outcome/exit/mfe/mae/future_return columns; `train.py` additionally drops
  `BANNED_FEATURES` and identity/entry/exit columns before fitting.
- **Train/test overlap** — chronological split + purge (horizon reaches into
  val) + embargo (gap 32 bars); calibration fit on validation only; test
  evaluated once.
- **Selection bias** — events are the full deduplicated sweep population; no
  post-hoc filtering beyond warm-up/invalid-risk gates.
- **Same-bar assumption** — triple barrier checks TP/SL in candle order with
  stop-priority (conservative) and an explicit `same_bar_policy`
  ambiguous/conservative/optimistic.
- **Trading costs** — full cost model present (`half_spread_price`,
  `slippage_price`, `commission_r`) with `net_result_r = gross - cost`; legacy
  `entry.spread/slippage` keys rejected loudly if nonzero.

## 4. Findings ledger

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| F1 | medium | **CLOSED** | default `group_rule="deepest_penetration"` within-run look-ahead → default now `first` (code + config `sweep.group_rule: first` + validation + downstream) |
| F2 | medium | **CLOSED** | event schema drift → `src/schema.py` reconciled (sweep-side `bullish/bearish` + trade-side `long/short` + `normalize_event_direction`), anti-drift contract test |
| N1 | low | **RESOLVED (t16, accepted)** | equal-level `price`/`price_min`/`price_max` frozen at emission; later touches grow `touch_count` only — accepted: formation view at `known_at`, causal; retro-fitting would be out-of-baseline-scope |
| N2 | low | **RESOLVED (t16, accepted)** | `structure_break` re-implements the swing fractal inline (max_age 400) instead of the t8 registry (200) — accepted: affects only the structure_break trigger, not the 974-event baseline; maintenance item for Phase 8+ |
| N3 | low | MITIGATED | `confirmation_time` tz-naive when zero events confirm; feature pipeline adds defensive tz localization |
| W1 | **high** | **CLOSED (t17 + t18)** | Walk-forward validation implemented (`walk_forward_folds` + `walk_forward_validation`, expanding folds + purge/embargo + per-fold OOS + aggregate stability); QA-verified in t18, DoD valid_2 now `pass` |
| W2 | low | OPEN | `RandomForestClassifier(n_jobs=-1)` shows ~1e-16 non-determinism across fits (predictions identical, metrics reproducible to 4 decimals); `n_jobs=1` would give bit-exact reproducibility |
| W3 | medium | **CLOSED (t19)** | `mypy src` failed on sklearn/catboost/lightgbm stubs → `[tool.mypy.overrides]` extended in t19; gate passes |
| W4 | **critical** | **RESOLVED — baseline re-frozen v1.2.0 with honest metrics (t21 verify; t22/t24 fix; team v2 t1 rerun + t2 QA + t3 freeze)** | v1.1.0 `model.pkl` was trained on 45 features incl. 19 future-information columns (mfe/mae/close_return/max/min_close_return + bars_held) — label leakage; frozen metrics invalid (ROC 0.981/PR 0.876/WF 0.968). Whitelist fix (t22/t24) → artifact uses only registry features (30 numeric; `atr`/`risk_price`/`bars_held` gone — verified §8.6). Re-freeze was blocked pending t24 metrics review; **now unblocked**: honest-but-weak metrics are the accepted truthful picture, not underfit-by-bug. Team v2 verified + re-froze: F3-WF/CLI wire train-only `impute_medians` (train.py L655-668; train_model.py L105-110), F1-PAYOFF uses real `net_result_r` (n=973 net-valid), F1-RACE guarded `--force`; `xauusd-train` rc=0, pytest 376 passed, WF report regenerated (5 folds, OOS PR-AUC mean 0.3552), md5 snapshot `reports/integration/t1_rerun_md5_snapshot.json` stable (13 artifacts, QA diff rỗng). Freeze status + honest metrics: `reports/freeze_status_v1.2.0.md` |

## 5. Residual integration risks (t16) — status after t16

1. **Registry wiring** — **RESOLVED (t16)**: feature pipeline gates swing/equal/
   prev-day/H1 levels on `known_at` via `level_state_at_bar` (`_level_features`,
   causal `known_pos <= bar_pos`); events.parquet consumed by Pipeline 3 with
   the registry attached. Note: the *detector* still sweeps rolling levels
   (frozen baseline design, reviewed rounds 1–2); registry levels enrich
   features, not detection.
2. **Pipeline 3/4 stubs** — **RESOLVED (t16)**: `build_dataset_cli` /
   `train_model_cli` wired end-to-end; all artifacts produced (event dataset,
   `model.pkl`, `calibrator.pkl`, `test_metrics.json`, calibration curve,
   score-bucket figure) — see `reports/integration/T16_baseline_freeze.md`.
3. N1/N2 — **RESOLVED (t16, accepted with rationale, §4/§6)** before freeze.

## 6. Conditions for baseline freeze

1. W3 — **CLOSED (t19)**: `sklearn`, `catboost`, `lightgbm` added to
   `[tool.mypy.overrides]`; CI gate mypy step passes.
2. t16 wiring — **DONE**: end-to-end pipeline (Pipeline 2/3/4) produces all
   final artifacts; structural levels gated on `known_at` via
   `level_state_at_bar` at the feature stage.
3. N1/N2 — **ACCEPTED (t16)** with rationale in the findings ledger; W2
   documented (n_jobs=-1 noise ~1e-16; reproducibility holds to 4 decimals,
   seed policy `random_state=42`).

## 7. Deliverables touched

- `reports/bias_audit.md` (this file — final full-pipeline audit).
- `reports/verification_checklist.json` (DoD §31 checklist, machine-readable).
- `tests/test_no_lookahead.py` (18 tests, marker `no_lookahead`, extended in t5/t11).

## 8. Label-leakage audit (t21) — CRITICAL

### 8.1 Confirmed leak in the frozen model

`artifacts/models/model.pkl` serializes `feature_names` with **45 features**.
`artifacts/feature_schemas/features.json` locks **32** causal features. The
diff is **21 non-registry columns**:

**19 future-information (post-entry) columns — label leakage:**

| Group | Columns | Count |
|-------|---------|-------|
| Max favorable excursion (R) | `mfe_r_h4`, `mfe_r_h8`, `mfe_r_h32` | 3 |
| Max adverse excursion (R) | `mae_r_h4`, `mae_r_h8`, `mae_r_h32` | 3 |
| Horizon close return (R) | `close_return_r_h4`, `_h8`, `_h16`, `_h32` | 4 |
| Max close return (R) | `max_close_return_r_h4`, `_h8`, `_h16`, `_h32` | 4 |
| Min close return (R) | `min_close_return_r_h4`, `_h8`, `_h16`, `_h32` | 4 |
| Trade duration | `bars_held` | 1 |

(Note: the initial report said "13"; the exact count is **19** future-leak
columns. Two further non-registry columns are *not* leakage but should also be
excluded by the whitelist: `atr` (causal) and `risk_price` (entry-derived).)

These columns are computed *after* entry (label window), so a model using them
sees the outcome before it happens. Every metric reported for the frozen
baseline (`test_metrics.json` ROC-AUC 0.9809, PR-AUC 0.8760;
`walk_forward_report.json` OOS PR-AUC mean 0.9681, Brier 0.0177) is **inflated
and not a valid out-of-sample estimate**.

### 8.2 Root cause

The train-time feature selection in `src/modeling/train.py` used a **blacklist
+ "keep all remaining numeric columns"** approach. `BANNED_FEATURES` was an
exact-match set that only caught `mfe_r_h16`/`mae_r_h16`; the horizon-suffixed
variants (`mfe_r_h4/h8/h32`, `close_return_r_h*`, `max/min_close_return_r_h*`,
`bars_held`) passed through, then `df.select_dtypes(include=[np.number])` kept
every remaining numeric column — including 8 legitimate categorical/bool
registry features were *dropped* (not leakage, but feature loss).

### 8.3 Why the no-lookahead suite missed it (gap)

`tests/test_no_lookahead.py` and `tests/test_features_no_lookahead.py` verify
**truncation invariance of the feature *build*** (`build_event_features`, t10)
— which is correct (32 causal features). They never test the **train-time
feature *selection*** (`prepare_features_and_labels`, t14) against the merged
labeled dataset, which carries the future label columns. No test asserted
`model.feature_names ⊆ registry`.

### 8.4 The attempted fix is itself broken (follow-on discovery)

`prepare_features_and_labels` now has a whitelist path
(`_load_registry_features()` → keep only the 32 registry names), **but the
registry path is wrong**: it joins `dirname(dirname(__file__))` (= `src/`) with
`artifacts/...`, yielding `src/artifacts/feature_schemas/features.json` (does
not exist) instead of the project root `artifacts/feature_schemas/features.json`.
Consequently `_load_registry_features()` returns an **empty set**, the whitelist
is skipped, and the code falls back to a prefix blacklist that still misses
`bars_held` (prefix list has `bars_to_` but not `bars_held`). Verified:
`prepare_features_and_labels(labeled_events.parquet)` today returns **27
features including `bars_held`, `atr`, `risk_price`** — i.e. still leaking.

`tests/test_modeling.py::test_feature_count_is_32_from_registry` is guarded by
`if registry:` so it **passes vacuously** (skips) while the registry loader is
broken.

### 8.4b Captain-verified holes in the t22 fix — status RESOLVED (t24)

Captain independently ran the code during the t22 fix and found three holes:
1. **Path bug** — `_load_registry_features()` joined `dirname(dirname(__file__))`
   (= `src/`) with `artifacts/...`, yielding a non-existent `src/artifacts/...`,
   returning an empty set → whitelist never activated (falls back to blacklist).
2. **Vacuous test** — `test_feature_count_is_32_from_registry` guarded by `if
   registry:` skipped its asserts when the loader returned empty.
3. **Stale artifacts** — `model.pkl` (45 features) / `test_metrics.json`
   (pr_auc 0.876) had not been regenerated at t22 time.

**Independent re-verification by Agent 0 (t21, 2026-09-03, after t24 landed):**
all three are now fixed; regenerated artifacts verified clean:

| Check | Result |
|---|---|
| `_load_registry_features()` | returns **32** (path = `dirname×3` → project root; raises if missing/≠32) |
| `test_feature_count_is_32_from_registry` | now `assert len(registry)==32` — fails (not skips) when loader is broken |
| `prepare_features_and_labels(labeled_events.parquet)` | returns **30 features, 0 leak** (`atr`/`risk_price`/`bars_held`/mfe/mae/close_return absent) |
| `model.pkl` feature_names | **30, all ⊆ registry** (0 non-registry) |
| `test_metrics.json` | honest: pr_auc 0.228, roc_auc 0.604, precision/recall/f1 0 — **underfit**, not leakage |
| `walk_forward_report.json` | honest: 5 folds, OOS PR-AUC mean **0.347** |

The 30-vs-32 difference is `level_type` + `volatility_regime` (categorical,
excluded by `select_dtypes(include=[np.number])`) — feature loss, not leakage
(t21-documented).

### 8.5 Required fixes (before any re-freeze)

> ✅ Items 1–6 completed across t22/t24 (code) and team v2 t1/t2/t3 (rerun,
> regenerate, re-audit, re-freeze) — see §8.8 / `reports/freeze_status_v1.2.0.md`.

1. Fix `_load_registry_features()` path → project root `artifacts/feature_schemas/features.json`.
2. Make the whitelist mandatory (fail if registry load is empty / != 32) — no
   blacklist fallback that can leak.
3. Add `bars_held`, `risk_price`, `atr` to `BANNED_EXACT` as defense-in-depth.
4. Rewrite `test_feature_count_is_32_from_registry` to fail (not skip) when the
   registry is empty, and add an integration test that runs
   `prepare_features_and_labels` on the real `labeled_events.parquet` and asserts
   the output == exactly the 32 registry features with zero future columns.
5. Regenerate `model.pkl` / `calibrator.pkl` / `test_metrics.json` /
   `walk_forward_report.json` / calibration curve / score-bucket figure with the
   corrected selection, and re-run the walk-forward OOS evaluation.
6. Re-audit the regenerated artifacts (feature_names == registry 32) before the
   baseline freeze is considered valid.

### 8.6 Current state (t21 independent verification, post-t22/t24 retrain)

Independent re-check by Agent 0 (integration) on the *current* artifact
(`artifacts/models/model.pkl`, regenerated after the t22 whitelist fix):

| Check | Result |
|---|---|
| `model.pkl` feature_names count | **30** (registry subset — no non-registry columns; `atr`/`risk_price`/`bars_held`/excursion columns all gone) |
| Non-registry features in model | **0** (verified against `features.json`, 32 names) |
| Registry features missing from model | `level_type`, `volatility_regime` (categorical — dropped by `select_dtypes(include=[np.number])`; design choice documented by t22, not leakage) |
| `prepare_features_and_labels(labeled_events.parquet)` (current code) | **32 registry features exactly** — whitelist path active (`_load_registry_features` returns 32, raises on empty); the §8.4 registry-path bug is fixed in the current code |
| Anti-regression tests (t21, added to `tests/test_modeling.py`) | `test_prepare_returns_exactly_registry_32` ✅, `test_banned_prefixes_cover_all_excursion_variants` ✅, `test_serialized_model_features_are_registry_only` ✅ (23/23 modeling tests green) |

**Leakage status: FIXED in the current artifact.** The frozen (stale) model
metrics (ROC-AUC 0.98 / PR-AUC 0.876 / WF-OOS 0.968) are invalid and replaced
by the regenerated test_metrics (`pr_auc 0.23, roc_auc 0.60, precision/recall
0.0` — heavily degraded, which is the *honest* picture without the leaked
columns). Interpretation (t24/t1/t3) is **complete**: the weak discrimination is
the truthful capability of the clean 30-feature model (test PR-AUC 0.2275 ≈
baseline positive_rate 0.1795; ROC-AUC 0.6041), not a repair bug — the model is
ranking-only. **Re-freeze completed 2026-09-04 as baseline v1.2.0** with these
honest metrics and md5-frozen artifacts — §8.8, `reports/freeze_status_v1.2.0.md`.

### 8.7 Split maths verified (captain's item 3/4)

- **Purge/embargo enforced**: `build_split_masks` and `walk_forward_folds`
  read `splitting.embargo_bars=32` and `purge=true`, apply
  `apply_purge` (label window horizon) + `apply_embargo` (32-bar gap) —
  verified in `src/modeling/split.py` and by fold arithmetic below.
- **974-event arithmetic**: single split = train 584 / val 195 / test 195
  (0.60/0.20/0.20). Walk-forward report (t20) last fold = train 778 + val 97 =
  875; the **99-event difference = 67 events after that fold's val window
  (val_end 907 < 974) + 32 embargo gap (val_start 810 − train_end 778)**.
  Purge drops nothing extra on this data (train_clean == raw). This is the
  expected expanding-window behavior, not a leak.

### 8.8 Re-freeze v1.2.0 — RESOLVED (team v2: t1 rerun + t2 QA + t3 freeze, 2026-09-04)

The t24 metric review is complete and the re-freeze (blocked since §8.6/W4) is
**unblocked**. Team v2 re-ran the pipeline and re-froze the baseline as
**v1.2.0** with honest metrics:

**Evidence from t1 (ML_Engineer) and t2 (QA_Auditor, independent):**
- F3-WF: `walk_forward_validation` (train.py L655-668) wires **train-only
  `impute_medians`** into `calibrate_model`/`evaluate_on_test`; 5 folds clean,
  no NaN crash; WF OOS PR-AUC mean **0.3552** (QA re-run 0.3537 — RF noise).
- F3-CLI: `train_model.py` L105-110 wires `impute_medians`; `xauusd-train`
  **rc=0** (t1 + QA rerun).
- F1-PAYOFF: generator uses real **`net_result_r`** (1 ambiguous excluded,
  n=973 net-valid); JSON `net_result_source="net_result_r"` +
  `baseline_net_positive_rate=0.4224`; no synthetic values.
- F1-RACE: probe without `--force` → `[skip]`, md5 unchanged.
- pytest **376 passed** rc=0; strict-check WF report no null/NaN.
- md5: snapshot `reports/integration/t1_rerun_md5_snapshot.json` (13 artifacts)
  stable across re-runs (QA S1/S2 diff rỗng).

**Final honest metrics (replacing the leaked v1.1.0 numbers):**
- Test (195 ev): PR-AUC **0.2275**, ROC-AUC **0.6041**, Brier 0.1561,
  positive_rate 0.1795; precision/recall/F1 0.0.
- Walk-forward OOS (5 folds): PR-AUC mean **0.3552** (0.311–0.394),
  ROC-AUC mean 0.7219, Brier 0.1276, positive_rate 0.1814.
- Top-prob OOS (real net_result_r): PF > 1 tại th0.2/0.25/0.4 (1.1653/1.3045/
  1.2784), n=360/223/32; P(net>0) 42.2–43.9% — Wilson 95% CI mọi subset đều
  chứa baseline 42.24% → gợi ý ranking, chưa phải edge thống kê.

**Interpretation (trung thực):** model sạch phân biệt **YẾU** (test PR-AUC gần
baseline positive_rate, ROC ~0.60) — chỉ dùng **ranking**; subset top-prob OOS
PF>1 nhưng n nhỏ + CI chứa baseline + chi phí thực ~0.05R (config hiện cost=0,
`net_result_r` ≈ gross) triệt tiêu biên → **KHÔNG trade ML nguyên trạng**.
Phân biệt rõ: **'win'** = hit 2R tp = 177/974 = **18.17%** ≠
**P(net_result_r>0)** = **0.4224**.

Full freeze status (artifacts + md5): `reports/freeze_status_v1.2.0.md`.
