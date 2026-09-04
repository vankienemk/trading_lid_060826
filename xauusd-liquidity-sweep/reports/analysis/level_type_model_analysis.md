# Model PR-AUC/PF by level_type (rolling-dilution hypothesis #4)

**Dataset**: frozen 974 confirmed events (973 net-valid). Probabilities are OUT-OF-SAMPLE (5-fold CV, seed 42) — no in-sample metrics. Net/PF statistics use the REAL `net_result_r` column (1 ambiguous event excluded).

> **Semantics**: every confirmed event in the frozen 974 is a baseline rolling-level sweep (`level_id` rolling_high/low). The `level_type` column carries the registry **structural** level (equal/swing/prev_day) the feature pipeline attributed to the event → `rolling` n=0 in the model pool. The pooled group `swing+equal+prev_day` therefore **is** the whole pool (identity asserted in-script).

## Method parity (whole-pool anchors)

- Frozen-method reproduction: saved calibrated model on the frozen CLI test split → PR-AUC 0.2275 (frozen `reports/metrics/test_metrics.json`: 0.2275) — PASS.
- Whole-pool 5-fold CV-OOS (same protocol as the frozen top-prob report): PR-AUC 0.2546, ROC-AUC 0.6164.
- Walk-forward OOS PR-AUC mean (frozen report): 0.3552.

| threshold | n_net | PF (OOS, real net) | frozen PF | parity |
|---|---|---|---|---|
| >= 0.2 | 360 | 1.1653 | 1.1653 | PASS |
| >= 0.25 | 223 | 1.3045 | 1.3045 | PASS |
| >= 0.4 | 32 | 1.2784 | 1.2784 | PASS |

## Per-level_type (OOS probabilities)

| level_type | n | long/short | n_tp | PR-AUC OOS | ROC-AUC OOS | P(net>0) [95% CI] | PF (group) |
|---|---|---|---|---|---|---|---|
| rolling | 0 | 0/0 | 0 | N/A | N/A | N/A | N/A |
| swing | 473 | 239/234 | 86 | 0.2510 | 0.6144 | 0.4334 [0.3895, 0.4784] | 1.0788 |
| equal | 487 | 248/239 | 90 | 0.2663 | 0.6155 | 0.4177 [0.3747, 0.4620] | 1.0100 |
| prev_day | 14 | 3/11 | 1 | 0.1000 | 0.3077 | 0.2143 [0.0757, 0.4759] | 0.2783 |
| swing+equal+prev_day | 974 | 490/484 | 177 | 0.2546 | 0.6164 | 0.4224 [0.3917, 0.4537] | 1.0279 |

> Note: PR-AUC is baseline-dependent (positive rate per group: swing 0.182, equal 0.185, prev_day 0.071, pooled/all 0.182). Cross-group PR-AUC differences must be read against each group's own positive rate; ROC-AUC is the prevalence-independent discriminator.

## Top-probability subsets (real net_result_r)

| level_type | th | n (events) | n_net | P(net>0) [95% CI] | avg_net_r | PF |
|---|---|---|---|---|---|---|
| rolling | — | 0 | 0 | — | — | — |
| swing | >= 0.2 | 196 | 196 | 0.4541 [0.3859, 0.5240] | 0.1564 | 1.3113 |
| swing | >= 0.25 | 124 | 124 | 0.4597 [0.3745, 0.5473] | 0.2000 | 1.4030 |
| swing | >= 0.4 | 19 | 19 | 0.4737 [0.2733, 0.6829] | 0.2132 | 1.4452 |
| equal | >= 0.2 | 165 | 164 | 0.3841 [0.3132, 0.4604] | 0.0100 | 1.0169 |
| equal | >= 0.25 | 99 | 99 | 0.4141 [0.3221, 0.5126] | 0.1141 | 1.1982 |
| equal | >= 0.4 | 13 | 13 | 0.3846 [0.1771, 0.6448] | 0.0545 | 1.0886 |
| prev_day | >= 0.2 | 0 | 0 | — | — | — |
| prev_day | >= 0.25 | 0 | 0 | — | — | — |
| prev_day | >= 0.4 | 0 | 0 | — | — | — |
| swing+equal+prev_day | >= 0.2 | 361 | 360 | 0.4222 [0.3723, 0.4738] | 0.0897 | 1.1653 |
| swing+equal+prev_day | >= 0.25 | 223 | 223 | 0.4395 [0.3759, 0.5051] | 0.1619 | 1.3045 |
| swing+equal+prev_day | >= 0.4 | 32 | 32 | 0.4375 [0.2817, 0.6067] | 0.1487 | 1.2784 |

## Verdict — hypothesis #4 (rolling dilutes the model)

- The pooled group `swing+equal+prev_day` equals the whole pool (rolling n=0 in the frozen 974), so 'excluding rolling' cannot change PR-AUC/PF: pooled PR-AUC == whole CV-OOS PR-AUC == 0.2546. The claim 'keep only swing/equal/prev_day improves PR-AUC over 0.2275' is therefore **not supported** at the model level — there is no level_type='rolling' event to drop.
- The 0.2275 frozen number is the single-test-split PR-AUC (reproduced exactly here); the CV-OOS whole-pool PR-AUC is 0.2546 and walk-forward OOS mean 0.3552. Protocol, not model quality, explains the spread; none of the reference numbers is beatable by the pooled structural group because it is the whole pool.
- Where rolling rows DO exist (review-round-2 pool, chart ground truth), the comparison is in `reports/analysis/level_type_quality.json` (confirmed/rolling n=71 PF 1.788 vs level-sweep equal 1.324 / swing 0.588 / prev_day 0.400 on the reviewed sample) — i.e. the chart-truth sample does NOT show rolling events as the worst-quality group either; sample sizes are small and stratified, so treat as indicative only.
- Small-n caveats: prev_day n=14 (< 30) — wide Wilson CI, no firm conclusion; top-prob subsets at th>=0.4 have very small counts per group. No per-level_type filter is supported by this analysis as a reliable PF/PR-AUC improver on OOS data.

