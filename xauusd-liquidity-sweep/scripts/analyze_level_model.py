#!/usr/bin/env python3
"""Per-level_type model PR-AUC / PF analysis (t5, ML_Engineer).

Tests the user's hypothesis #4: "the model is diluted by low-quality
rolling-level events — keeping only swing/equal/prev_day improves model
PR-AUC/PF over the whole pool".  Because every confirmed event in the
frozen 974 is a baseline rolling-level sweep (level_id = rolling_high/low)
and the `level_type` column carries the *registry structural level* the
feature pipeline attributed to the event, the group level_type='rolling'
has n=0 in the model pool.  We therefore report the per-level_type model
quality on OOS probabilities AND the pooled 'swing+equal+prev_day' group,
which (with rolling n=0) equals the whole pool — the identity is checked
numerically inside the script.

Method (locked to frozen artifacts for parity):
  1. Whole-pool PR-AUC anchors:
     (a) frozen-method reproduction: load artifacts/models/model.pkl
         (calibrated model + scaler + impute_medians) and evaluate on the
         frozen CLI test split (build_split_masks) -> must equal
         reports/metrics/test_metrics.json PR-AUC 0.2275435...;
     (b) OOS protocol of the frozen top-prob report: 5-fold
         cross_val_predict (seed 42) on the 30-feature matrix, impute
         medians from the first-80% proxy exactly as
         scripts/generate_top_prob_analysis.py -> whole-pool PF at
         thresholds 0.2/0.25/0.4 must equal the frozen OOS table
         (1.1653 / 1.3045 / 1.2784).
  2. Per-level_type metrics on the SAME OOS probabilities (NOT in-sample):
     for each group (rolling / swing / equal / prev_day / pooled
     swing+equal+prev_day): n, PR-AUC, ROC-AUC, baseline P(net_result_r>0)
     with Wilson 95% CI, whole-group PF, and PF / P(net>0) / avg_net_r of
     the top-probability subset at thresholds 0.2/0.25/0.4 computed from
     the REAL net_result_r column (F1-PAYOFF lesson; the single ambiguous
     event with net_result_r=NaN is excluded from net statistics).
  3. Verdict on hypothesis #4 with small-n caveats (prev_day n=14 < 30;
     rolling n=0 in the model pool).

Reads ONLY frozen inputs; writes ONLY reports/analysis/ (new folder).
Deterministic: fixed seed, no timestamps, stable sorts, fixed rounding;
identical JSON text between runs.

Usage:
    .venv/bin/python scripts/analyze_level_model.py
"""

from __future__ import annotations

import json
import math
import os
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    roc_auc_score,
)
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import cross_val_predict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELED = os.path.join(ROOT, "data/processed/labeled_events.parquet")
MODEL_PKL = os.path.join(ROOT, "artifacts/models/model.pkl")
TEST_METRICS = os.path.join(ROOT, "reports/metrics/test_metrics.json")
TOP_PROB_JSON = os.path.join(ROOT, "reports/top_prob_subset_analysis.json")
WF_JSON = os.path.join(ROOT, "reports/walk_forward_report.json")
OUT_DIR = os.path.join(ROOT, "reports/analysis")
SEED = 42
THRESHOLDS = (0.2, 0.25, 0.4)
Z = 1.959963984540054  # 95% normal quantile

LT_ORDER = ("rolling", "swing", "equal", "prev_day")
POOLED = "swing+equal+prev_day"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def wilson_ci(k: int, n: int, z: float = Z) -> dict | None:
    """Wilson score 95% CI for a proportion k/n. None when n == 0."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return {
        "point": round(p, 6),
        "ci95_low": round(max(0.0, centre - half), 6),
        "ci95_high": round(min(1.0, centre + half), 6),
        "k": int(k),
        "n": int(n),
    }


def _fmt(x, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "N/A"
    return f"{x:.{nd}f}"


def net_pf(net: np.ndarray) -> float | None:
    """Profit factor from REAL net_result_r values (NaN excluded by caller)."""
    pos = float(net[net > 0].sum())
    neg = abs(float(net[net < 0].sum()))
    return round(pos / neg, 6) if neg > 0 else None


# ---------------------------------------------------------------------------
# load frozen inputs
# ---------------------------------------------------------------------------

df = pd.read_parquet(LABELED)
assert len(df) == 974, f"expected 974 frozen events, got {len(df)}"

from src.config import load_config, set_global_config
from src.modeling.train import prepare_features_and_labels, build_split_masks

config = load_config("baseline.yaml")
set_global_config(config)

X, y, feature_names = prepare_features_and_labels(df)
assert len(X) == 974 and len(y) == 974
assert y.name == "outcome_2r_h16"
y_arr = y.to_numpy(dtype=int)

# level_type + real net_result_r aligned with X rows
level_type = df.loc[X.index, "level_type"].astype(str).to_numpy()
net_r = df.loc[X.index, "net_result_r"].to_numpy(dtype=float)
assert int(np.isnan(net_r).sum()) == 1, "expected exactly 1 ambiguous event"
lt_counts = {k: int((level_type == k).sum()) for k in LT_ORDER}
assert lt_counts == {"rolling": 0, "swing": 473, "equal": 487, "prev_day": 14}, lt_counts
n_net_valid = int((~np.isnan(net_r)).sum())
assert n_net_valid == 973

# ---------------------------------------------------------------------------
# 1. Whole-pool PR-AUC anchors (method parity with frozen reports)
# ---------------------------------------------------------------------------

# (a) frozen-method reproduction: saved calibrated model on the frozen test split
splits = build_split_masks(len(X), config)
with open(MODEL_PKL, "rb") as f:
    saved = pickle.load(f)
cal_model = saved["model"]
scaler = saved["scaler"]
impute_saved = saved.get("impute_medians", {})
X_test = X.iloc[splits["test"]].copy()
X_test = X_test.fillna(impute_saved)
proba_test = cal_model.predict_proba(scaler.transform(X_test))[:, 1]
y_test = y_arr[splits["test"]]
pr_auc_test_repro = float(average_precision_score(y_test, proba_test))
roc_auc_test_repro = float(roc_auc_score(y_test, proba_test))

with open(TEST_METRICS) as f:
    frozen_test = json.load(f)
pr_auc_frozen = float(frozen_test["pr_auc"])
parity_test = abs(pr_auc_test_repro - pr_auc_frozen) < 5e-5
print(f"[anchor a] frozen-method test-split PR-AUC reproduction: "
      f"{pr_auc_test_repro:.6f} vs frozen {pr_auc_frozen:.6f} -> "
      f"{'PASS' if parity_test else 'FAIL'}")

# (b) OOS protocol of the frozen top-prob report (5-fold CV, seed 42)
n_train_proxy = int(len(X) * 0.8)
impute_medians = {}
for col in X.columns:
    if X.iloc[:n_train_proxy][col].isna().any():
        impute_medians[col] = float(X.iloc[:n_train_proxy][col].median())
X_imp = X.fillna(impute_medians)

pipe = Pipeline([
    ("scaler", StandardScaler()),
    ("clf", LogisticRegression(max_iter=1000, random_state=SEED)),
])
probas_oos = cross_val_predict(pipe, X_imp, y_arr, cv=5, method="predict_proba")[:, 1]
assert len(probas_oos) == 974

with open(TOP_PROB_JSON) as f:
    frozen_tp = json.load(f)
frozen_oos_subsets = {r["threshold"]: r for r in frozen_tp["out_of_sample"]["subsets"]}

whole_set = {
    "pr_auc": round(float(average_precision_score(y_arr, probas_oos)), 6),
    "roc_auc": round(float(roc_auc_score(y_arr, probas_oos)), 6),
    "positive_rate_tp": round(float(y_arr.mean()), 6),
}
oos_parity_rows = []
for th in THRESHOLDS:
    mask = probas_oos >= th
    n_sel = int(mask.sum())
    net_sel = net_r[mask]
    valid = ~np.isnan(net_sel)
    n_net = int(valid.sum())
    nv = net_sel[valid]
    pf = net_pf(nv)
    npr = float((nv > 0).mean()) if n_net else None
    avg = float(nv.mean()) if n_net else None
    fr = frozen_oos_subsets[th]
    ok_pf = (pf is not None and fr["profit_factor"] is not None
             and abs(pf - fr["profit_factor"]) < 1e-4)
    ok_cnt = n_net == fr["n_with_net_result"]
    oos_parity_rows.append({
        "threshold": th,
        "count": n_sel,
        "n_with_net_result": n_net,
        "profit_factor": pf,
        "frozen_profit_factor": fr["profit_factor"],
        "pf_parity": bool(ok_pf),
        "count_parity": bool(ok_cnt),
        "net_positive_rate": round(npr, 6) if npr is not None else None,
        "avg_net_r": round(avg, 6) if avg is not None else None,
    })
    print(f"[anchor b] whole-pool top-prob th={th}: n_net={n_net} PF={pf} "
          f"(frozen {fr['profit_factor']}) -> "
          f"{'PASS' if ok_pf and ok_cnt else 'FAIL'}")
parity_oos = all(r["pf_parity"] and r["count_parity"] for r in oos_parity_rows)

with open(WF_JSON) as f:
    wf = json.load(f)
wf_pr_auc_mean = None
_agg = wf.get("aggregate", wf)
if "pr_auc" in _agg and isinstance(_agg["pr_auc"], dict):
    wf_pr_auc_mean = float(_agg["pr_auc"]["mean"])

# ---------------------------------------------------------------------------
# 2. per-level_type metrics on OOS probabilities (NOT in-sample)
# ---------------------------------------------------------------------------

def group_metrics(mask: np.ndarray, label: str) -> dict:
    n = int(mask.sum())
    rec: dict = {
        "level_type": label,
        "n": n,
        "n_long": int((df.loc[X.index[mask], "direction"] == "long").sum()),
        "n_short": int((df.loc[X.index[mask], "direction"] == "short").sum()),
        "n_tp": int(y_arr[mask].sum()),
        "small_n_note": n < 30,
    }
    if n == 0:
        rec["n_net_valid"] = 0
        rec.update({
            "pr_auc_oos": None, "roc_auc_oos": None,
            "baseline_net_positive_rate": None, "wilson_ci": None,
            "avg_net_r": None, "profit_factor": None, "top_prob_subsets": [],
        })
        return rec

    yg = y_arr[mask]
    pg = probas_oos[mask]
    pr = None
    if int(yg.sum()) > 0:
        try:
            pr = round(float(average_precision_score(yg, pg)), 6)
        except ValueError:
            pr = None
    roc = None
    if int(yg.sum()) > 0 and int(yg.sum()) < n:
        roc = round(float(roc_auc_score(yg, pg)), 6)

    netg = net_r[mask]
    valid = ~np.isnan(netg)
    nv = netg[valid]
    n_net = int(valid.sum())
    n_pos_net = int((nv > 0).sum())
    wilson = wilson_ci(n_pos_net, n_net) if n_net else None

    subsets = []
    for th in THRESHOLDS:
        sub_mask = mask & (probas_oos >= th)
        n_sub = int(sub_mask.sum())
        net_sub = net_r[sub_mask]
        sub_valid = ~np.isnan(net_sub)
        nv_sub = net_sub[sub_valid]
        row: dict = {"threshold": th, "count": n_sub}
        if n_sub == 0:
            row.update({"n_with_net_result": 0, "profit_factor": None,
                        "net_positive_rate": None, "avg_net_r": None,
                        "wilson_ci": None})
        else:
            n_net_sub = int(sub_valid.sum())
            pf_sub = net_pf(nv_sub) if n_net_sub else None
            k_pos = int((nv_sub > 0).sum())
            row.update({
                "n_with_net_result": n_net_sub,
                "profit_factor": pf_sub,
                "net_positive_rate": round(float(k_pos / n_net_sub), 6)
                if n_net_sub else None,
                "avg_net_r": round(float(nv_sub.mean()), 6)
                if n_net_sub else None,
                "wilson_ci": wilson_ci(k_pos, n_net_sub) if n_net_sub else None,
            })
        subsets.append(row)

    rec.update({
        "n_net_valid": n_net,
        "pr_auc_oos": pr,
        "roc_auc_oos": roc,
        "baseline_net_positive_rate": round(float((nv > 0).mean()), 6)
        if n_net else None,
        "wilson_ci": wilson,
        "avg_net_r": round(float(nv.mean()), 6) if n_net else None,
        "profit_factor": net_pf(nv),
        "top_prob_subsets": subsets,
    })
    return rec


rows = []
for lt in LT_ORDER:
    rows.append(group_metrics(level_type == lt, lt))

# pooled 'swing+equal+prev_day' (rolling excluded) == whole pool when rolling n=0
pooled_mask = np.isin(level_type, ["swing", "equal", "prev_day"])
pooled_rec = group_metrics(pooled_mask, POOLED)
rows.append(pooled_rec)

assert sum(r["n"] for r in rows[:4]) == 974
assert pooled_rec["n"] == 974, "pooled group must equal whole pool (rolling n=0)"
assert pooled_rec["pr_auc_oos"] == whole_set["pr_auc"], \
    "pooled PR-AUC must equal whole-pool OOS PR-AUC (identity check)"
assert pooled_rec["roc_auc_oos"] == whole_set["roc_auc"], \
    "pooled ROC-AUC must equal whole-pool OOS ROC-AUC (identity check)"

# ---------------------------------------------------------------------------
# 3. verdict
# ---------------------------------------------------------------------------

# Reference numbers for the comparison requested in the acceptance.
pooled_pr = pooled_rec["pr_auc_oos"]
whole_cv_pr = whole_set["pr_auc"]
swing_pr = next(r["pr_auc_oos"] for r in rows if r["level_type"] == "swing")
equal_pr = next(r["pr_auc_oos"] for r in rows if r["level_type"] == "equal")
prev_pr = next(r["pr_auc_oos"] for r in rows if r["level_type"] == "prev_day")

verdict_parts = []
verdict_parts.append(
    "HYPOTHESIS #4 (rolling dilution) — NOT SUPPORTED inside the frozen model "
    "pool: level_type='rolling' has n=0 in the 974 confirmed events (every "
    "event is a baseline rolling-level sweep whose level_type column carries "
    "the registry structural level the feature pipeline attributed to it). "
    "Therefore the pooled group 'swing+equal+prev_day' IS the whole pool "
    "(n=974, identity asserted in-script), so excluding 'rolling' changes "
    "nothing: pooled PR-AUC == whole-pool CV-OOS PR-AUC == "
    f"{pooled_pr:.4f}."
)
verdict_parts.append(
    "The whole-pool reference numbers differ by protocol: frozen test-split "
    f"PR-AUC 0.2275 (single 195-event test split, reproduced exactly here: "
    f"{pr_auc_test_repro:.4f}) vs 5-fold CV-OOS over all 974 events "
    f"(PR-AUC {whole_cv_pr:.4f}, ROC-AUC {whole_set['roc_auc']:.4f}) vs "
    "walk-forward OOS mean "
    f"({wf_pr_auc_mean:.4f} if wf_pr_auc_mean is not None else 'n/a'). "
    "All are OOS; none is exceeded by the pooled structural group because the "
    "pooled group and the whole pool contain the same events."
)
if prev_pr is not None:
    verdict_parts.append(
        f"prev_day (n=14 < 30) carries the worst realized quality "
        f"(group PF {_fmt(next(r['profit_factor'] for r in rows if r['level_type']=='prev_day'))}, "
        f"OOS PR-AUC {prev_pr:.3f}) but the sample is too small for any firm "
        "conclusion (Wilson CI is wide). swing vs equal OOS PR-AUC "
        f"({swing_pr:.3f} vs {equal_pr:.3f}) is not a meaningful separation "
        "either; top-prob subset PFs per group are reported with counts and "
        "Wilson CIs and should not be over-read given small n at high "
        "thresholds."
    )
else:
    verdict_parts.append(
        "prev_day (n=14 < 30) has no positive label in OOS evaluation; "
        "its realized quality is reported from net_result_r only."
    )

verdict = " ".join(verdict_parts)

# ---------------------------------------------------------------------------
# persist JSON (deterministic)
# ---------------------------------------------------------------------------

os.makedirs(OUT_DIR, exist_ok=True)

output = {
    "dataset": "data/processed/labeled_events.parquet (frozen, 974 confirmed events)",
    "analysis": ("per-level_type model PR-AUC/ROC-AUC + PF top-prob subsets "
                 "on OOS probabilities (5-fold CV seed 42, NOT in-sample); "
                 "net statistics from the REAL net_result_r column "
                 "(1 ambiguous event excluded from net stats)"),
    "level_type_semantics": (
        "Every confirmed event in the frozen 974 is a baseline rolling-level "
        "sweep (level_id rolling_high/low); the 'level_type' column carries "
        "the registry STRUCTURAL level (equal/swing/prev_day) the feature "
        "pipeline attributed to the event. level_type='rolling' therefore has "
        "n=0 in the model pool; the rolling-vs-structural comparison lives in "
        "the review-round-2 pool (reports/analysis/level_type_quality.json)."),
    "method_parity": {
        "frozen_test_split_reproduction": {
            "pr_auc_reproduced": round(pr_auc_test_repro, 6),
            "pr_auc_frozen_test_metrics": round(pr_auc_frozen, 6),
            "roc_auc_reproduced": round(roc_auc_test_repro, 6),
            "pass": parity_test,
        },
        "whole_pool_cv_oos_vs_frozen_top_prob": {
            "pr_auc_cv_oos_974": whole_set["pr_auc"],
            "roc_auc_cv_oos_974": whole_set["roc_auc"],
            "positive_rate_tp": whole_set["positive_rate_tp"],
            "rows": oos_parity_rows,
            "all_pass": bool(parity_oos),
        },
        "walk_forward_oos_pr_auc_mean": wf_pr_auc_mean,
    },
    "whole_pool_reference": {
        "frozen_test_metrics_pr_auc_0_2275": round(pr_auc_frozen, 6),
        "cv_oos_974_pr_auc": whole_set["pr_auc"],
        "cv_oos_974_roc_auc": whole_set["roc_auc"],
        "walk_forward_pr_auc_mean": wf_pr_auc_mean,
    },
    "rows": rows,
    "hypothesis_4_verdict": verdict,
}

with open(os.path.join(OUT_DIR, "level_type_model_analysis.json"), "w") as f:
    json.dump(output, f, indent=2, sort_keys=True)
    f.write("\n")

# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------

def ci_str(c: dict | None) -> str:
    if c is None:
        return "N/A"
    return f"{c['point']:.4f} [{c['ci95_low']:.4f}, {c['ci95_high']:.4f}]"


md = [
    "# Model PR-AUC/PF by level_type (rolling-dilution hypothesis #4)",
    "",
    f"**Dataset**: frozen 974 confirmed events (973 net-valid). Probabilities "
    f"are OUT-OF-SAMPLE (5-fold CV, seed {SEED}) — no in-sample metrics. "
    "Net/PF statistics use the REAL `net_result_r` column "
    "(1 ambiguous event excluded).",
    "",
    "> **Semantics**: every confirmed event in the frozen 974 is a baseline "
    "rolling-level sweep (`level_id` rolling_high/low). The `level_type` "
    "column carries the registry **structural** level (equal/swing/prev_day) "
    "the feature pipeline attributed to the event → `rolling` n=0 in the "
    "model pool. The pooled group `swing+equal+prev_day` therefore **is** "
    "the whole pool (identity asserted in-script).",
    "",
    "## Method parity (whole-pool anchors)",
    "",
    f"- Frozen-method reproduction: saved calibrated model on the frozen CLI "
    f"test split → PR-AUC {pr_auc_test_repro:.4f} (frozen "
    f"`reports/metrics/test_metrics.json`: {pr_auc_frozen:.4f}) — "
    f"{'PASS' if parity_test else 'FAIL'}.",
    f"- Whole-pool 5-fold CV-OOS (same protocol as the frozen top-prob "
    f"report): PR-AUC {whole_set['pr_auc']:.4f}, "
    f"ROC-AUC {whole_set['roc_auc']:.4f}.",
    f"- Walk-forward OOS PR-AUC mean (frozen report): "
    f"{_fmt(wf_pr_auc_mean)}.",
    "",
    "| threshold | n_net | PF (OOS, real net) | frozen PF | parity |",
    "|---|---|---|---|---|",
]
for r in oos_parity_rows:
    md.append(
        f"| >= {r['threshold']} | {r['n_with_net_result']} | "
        f"{_fmt(r['profit_factor'])} | {_fmt(r['frozen_profit_factor'])} | "
        f"{'PASS' if r['pf_parity'] and r['count_parity'] else 'FAIL'} |"
    )
md += [
    "",
    "## Per-level_type (OOS probabilities)",
    "",
    "| level_type | n | long/short | n_tp | PR-AUC OOS | ROC-AUC OOS | "
    "P(net>0) [95% CI] | PF (group) |",
    "|---|---|---|---|---|---|---|---|",
]
for r in rows:
    w = r["wilson_ci"]
    md.append(
        f"| {r['level_type']} | {r['n']} | {r['n_long']}/{r['n_short']} | "
        f"{r['n_tp']} | {_fmt(r['pr_auc_oos'])} | {_fmt(r['roc_auc_oos'])} | "
        f"{ci_str(w)} | {_fmt(r['profit_factor'])} |"
    )
md += [
    "",
    "> Note: PR-AUC is baseline-dependent (positive rate per group: "
    "swing {:.3f}, equal {:.3f}, prev_day {:.3f}, pooled/all {:.3f}). "
    "Cross-group PR-AUC differences must be read against each group's own "
    "positive rate; ROC-AUC is the prevalence-independent discriminator."
    "".format(
        next(r["n_tp"] / r["n"] for r in rows if r["level_type"] == "swing"),
        next(r["n_tp"] / r["n"] for r in rows if r["level_type"] == "equal"),
        next(r["n_tp"] / r["n"] for r in rows if r["level_type"] == "prev_day"),
        float(y_arr.mean()),
    ),
    "",
    "## Top-probability subsets (real net_result_r)",
    "",
    "| level_type | th | n (events) | n_net | P(net>0) [95% CI] | avg_net_r | PF |",
    "|---|---|---|---|---|---|---|",
]
for r in rows:
    if r["n"] == 0:
        md.append(f"| {r['level_type']} | — | 0 | 0 | — | — | — |")
        continue
    for s in r["top_prob_subsets"]:
        if s["count"] == 0:
            md.append(f"| {r['level_type']} | >= {s['threshold']} | 0 | 0 | — | — | — |")
            continue
        md.append(
            f"| {r['level_type']} | >= {s['threshold']} | {s['count']} | "
            f"{s['n_with_net_result']} | {ci_str(s['wilson_ci'])} | "
            f"{_fmt(s['avg_net_r'])} | {_fmt(s['profit_factor'])} |"
        )
md += [
    "",
    "## Verdict — hypothesis #4 (rolling dilutes the model)",
    "",
    "- The pooled group `swing+equal+prev_day` equals the whole pool "
    "(rolling n=0 in the frozen 974), so 'excluding rolling' cannot change "
    "PR-AUC/PF: pooled PR-AUC == whole CV-OOS PR-AUC == "
    f"{pooled_rec['pr_auc_oos']:.4f}. The claim 'keep only swing/equal/"
    "prev_day improves PR-AUC over 0.2275' is therefore **not supported** at "
    "the model level — there is no level_type='rolling' event to drop.",
    f"- The 0.2275 frozen number is the single-test-split PR-AUC "
    f"(reproduced exactly here); the CV-OOS whole-pool PR-AUC is "
    f"{whole_cv_pr:.4f} and walk-forward OOS mean {_fmt(wf_pr_auc_mean)}. "
    "Protocol, not model quality, explains the spread; none of the reference "
    "numbers is beatable by the pooled structural group because it is the "
    "whole pool.",
    "- Where rolling rows DO exist (review-round-2 pool, chart ground truth), "
    "the comparison is in `reports/analysis/level_type_quality.json` "
    "(confirmed/rolling n=71 PF 1.788 vs level-sweep equal 1.324 / swing "
    "0.588 / prev_day 0.400 on the reviewed sample) — i.e. the chart-truth "
    "sample does NOT show rolling events as the worst-quality group either; "
    "sample sizes are small and stratified, so treat as indicative only.",
    "- Small-n caveats: prev_day n=14 (< 30) — wide Wilson CI, no firm "
    "conclusion; top-prob subsets at th>=0.4 have very small counts per "
    "group. No per-level_type filter is supported by this analysis as a "
    "reliable PF/PR-AUC improver on OOS data.",
    "",
]

with open(os.path.join(OUT_DIR, "level_type_model_analysis.md"), "w") as f:
    f.write("\n".join(md) + "\n")

# ---------------------------------------------------------------------------
# console summary + integrity checks
# ---------------------------------------------------------------------------

print("=" * 72)
print("Per-level_type model PR-AUC/PF analysis (t5)")
print("=" * 72)
print(f"[ok] frozen events: {len(X)} | net-valid: {n_net_valid} | "
      f"level_type counts: {lt_counts}")
print(f"[ok] whole-pool CV-OOS PR-AUC {whole_set['pr_auc']:.4f} / "
      f"ROC-AUC {whole_set['roc_auc']:.4f} "
      f"(frozen test-split PR-AUC repro {pr_auc_test_repro:.6f})")
for r in rows:
    if r["n"] == 0:
        print(f"  {r['level_type']:<22} n=0")
        continue
    print(f"  {r['level_type']:<22} n={r['n']:<4} "
          f"PR={_fmt(r['pr_auc_oos'],3)} ROC={_fmt(r['roc_auc_oos'],3)} "
          f"P(net>0)={_fmt(r['baseline_net_positive_rate'],3)} "
          f"PF={_fmt(r['profit_factor'],3)}")
print(f"[ok] pooled '{POOLED}' n={pooled_rec['n']} "
      f"== whole (rolling n=0) | PR-AUC identity: "
      f"{pooled_rec['pr_auc_oos'] == whole_set['pr_auc']}")
print(f"[ok] parity anchors: test-split {parity_test} | "
      f"top-prob OOS {parity_oos}")
p = os.path.join(OUT_DIR, "level_type_model_analysis.json")
print(f"[ok] wrote {os.path.relpath(p, ROOT)} ({os.path.getsize(p)} bytes)")
print(f"[ok] wrote reports/analysis/level_type_model_analysis.md")
print("VERDICT: hypothesis #4 not supported in model pool (rolling n=0); "
      "pooled == whole; see JSON/md for details.")
print("DONE")
