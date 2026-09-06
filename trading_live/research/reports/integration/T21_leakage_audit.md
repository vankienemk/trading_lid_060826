# t21 — Formal Leakage Audit: frozen model.pkl (supersedes t15 "no leakage")

**Auditor:** Project_Lead (Agent 0) · Date: 2026-09-03 · Verdict: **CRITICAL FINDING (leakage), now remediated; re-freeze COMPLETED 2026-09-04 as v1.2.0 with honest metrics (team v2 t1+t2+t3) — this audit is historical record.**

> ✅ **UPDATE (2026-09-04, t3):** the re-freeze blocked at §6/§7 is no longer
> blocked. Pipeline re-run rc=0, WF report regenerated with train-only
> `impute_medians` (F3-WF/F3-CLI), top-prob uses real `net_result_r`
> (F1-PAYOFF, n=973), pytest 376 passed, md5 frozen (13 artifacts, stable over
> re-runs). QA (t2) verdict PASS. Baseline **v1.2.0** frozen with honest
> metrics: test PR-AUC 0.2275 / ROC-AUC 0.6041; WF OOS PR-AUC mean 0.3552.
> Freeze status: `reports/freeze_status_v1.2.0.md`; md5 snapshot:
> `reports/integration/t1_rerun_md5_snapshot.json`.

## 1. Confirmed leak scope (vs captain's preliminary evidence)

`artifacts/models/model.pkl` (frozen at t16, generated 2026-09-02): `feature_names` =
**45 features** vs registry `features.json` = **32**. Diff = **21 non-registry columns**:

- **19 future-information (post-entry) columns — true label leakage:**
  `mfe_r_h4/h8/h32` (3), `mae_r_h4/h8/h32` (3), `close_return_r_h4/h8/h16/h32` (4),
  `max_close_return_r_h4/h8/h16/h32` (4), `min_close_return_r_h4/h8/h16/h32` (4),
  `bars_held` (1).
- **2 non-leak but whitelist-violating:** `atr`, `risk_price` (entry-time derivable —
  must still be excluded from model features).

Captain's preliminary count (13) under-estimated: exact audit = **19 future columns + 2
non-registry = 21**. All frozen-baseline metrics (test ROC-AUC 0.981 / PR-AUC 0.876;
walk-forward OOS PR-AUC mean 0.968) are **inflated and invalid**.

## 2. Root cause (train-time selection, not feature build)

`src/modeling/train.py::prepare_features_and_labels` used a blacklist +
"keep all remaining numeric columns" strategy; `BANNED_FEATURES` exact-match only
caught `mfe_r_h16`/`mae_r_h16`; horizon variants + `bars_held` slipped through.
Additionally 8 legitimate registry features (categorical/bool) were dropped
(`select_dtypes(include=[np.number])`).

## 3. Why the no-lookahead suite (23 tests) missed it — CONFIRMED GAP

`tests/test_no_lookahead.py` + `tests/test_features_no_lookahead.py` verify
**truncation-invariance of the feature BUILD** (t10, `build_event_features` — correct:
32 causal features). They never test **train-time feature SELECTION**
(`prepare_features_and_labels`, t14) against the merged labeled dataset (which carries
the future label columns). No test asserted `model.feature_names ⊆ registry`.

**Anti-regression tests added (t21)** in `tests/test_modeling.py`:
- `test_prepare_returns_exactly_registry_32` — selection must equal registry set exactly.
- `test_banned_prefixes_cover_all_excursion_variants` — prefix list catches all horizon variants.
- `test_serialized_model_features_are_registry_only` — shipped `model.pkl` must never
  contain non-registry features (fails on a leaked artifact).

## 4. Purge/embargo — CONFIRMED enforced

`build_split_masks` and `walk_forward_folds` read `splitting.embargo_bars=32` +
`purge=true`, apply `apply_purge` (label-window horizon) + `apply_embargo` (32-bar gap).
Verified in code and fold arithmetic. Not the leak vector.

## 5. 974 vs 875 arithmetic — CONFIRMED, not a leak

Single split: train 584 / val 195 / test 195 (60/20/20). Walk-forward last fold (t20):
train 778 + val 97 = 875. Difference of 99 = **67 events after last fold's val window
(val_end 907 < 974) + 32 embargo gap (val_start 810 − train_end 778)**. Purge drops
nothing extra (train_clean == raw). Expected expanding-window behavior.

## 6. Current state (2026-09-03, post t22/t24 parallel work)

Independent re-verification of the *current* artifact:
- `model.pkl` regenerated → **30 features, 0 non-registry** (registry subset;
  `level_type`/`volatility_regime` categorical excluded by design — documented §8.6).
- Current `prepare_features_and_labels` returns **exactly 32 registry features**
  (whitelist active; registry-path bug from §8.4 is fixed in current code).
- 23/23 modeling tests green (incl. 3 new anti-regression).
- **But** regenerated `test_metrics.json` are honest-but-poor:
  `pr_auc 0.228, roc_auc 0.605, precision 0.0, recall 0.0, f1 0.0` — without leaked
  features the model underfits. **Agent 6 (t24) must review/fix before any re-freeze.**

## 7. Required before re-freeze

> ✅ **All completed 2026-09-04 (team v2 t1+t2+t3)** — see update banner above and
> `reports/freeze_status_v1.2.0.md`.

1. t24: review degraded metrics (possibly underfit / threshold / class-imbalance), retrain
   if needed, regenerate all artifacts + walk-forward report.
2. QA re-audit artifacts (`feature_names == registry`); update `verification_checklist.json`.
3. Keep W2 seed-policy note; freeze version bump (v1.2.0) documenting honest metrics.

## Files

- `reports/bias_audit.md` §8.1–§8.7 (leak list, root cause, gap, current state, maths)
- `tests/test_modeling.py` (3 new anti-regression tests)
- `reports/integration/T21_leakage_audit.md` (this file)