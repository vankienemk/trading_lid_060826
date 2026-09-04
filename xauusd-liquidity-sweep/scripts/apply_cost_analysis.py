#!/usr/bin/env python3
"""Apply a realistic per-trade cost (~0.05R round-trip) to the frozen 974-event pool.

Gate analysis (t8): which buckets keep PF > 1 AFTER cost, and does baseline
expectancy stay positive? No re-training, no model/detector/baseline writes —
only reads frozen artifacts and writes NEW files under reports/analysis/.

Method
------
1. Load frozen events (974; 973 net-valid; cost_r = 0 placeholder => net_result_r
   IS gross).  Post-cost net per position = net_result_r - COST_R, COST_R = 0.05
   (round-trip spread + slippage + commission in R units; symmetric long/short).
   Assumption source: reports/freeze_status_v1.2.0.md (decision #4: real cost
   ~0.05R/trade), reports/top_prob_subset_analysis.md "Cost Considerations"
   (spread+slippage ~0.05R). A measured per-event cost run was deferred (T16),
   so the flat 0.05R assumption is stated explicitly and a PF breakeven-cost
   table quantifies headroom at other cost levels.
2. Groups: whole pool (973), rule bucket 40-49 (rule_score in [40,50), frozen
   score_bucket_report.json), top-prob OOS subsets >=0.2/0.25/0.4 (frozen
   top_prob_subset_analysis protocol: 5-fold CV OOS probabilities from the clean
   30-feature model with train-80% median imputation, probabilities NOT re-frozen,
   only regenerated deterministically for subset selection), level_type groups,
   direction x h1_trend 2x2 cells, trend composites (sweep_cung_chieu_trend_h1 =
   catch-the-knife: SHORT&up | LONG&down; sweep_nguoc_trend: LONG&up | SHORT&down)
   and composite x level_type crosses (mirror of trend_context_analysis).
3. Statistics per group pre- and post-cost: n, n_net, profit factor (PF),
   avg net R (expectancy), P(net>0), each with a 95% CI — Wilson for the
   proportion P(net>0), deterministic percentile bootstrap (seeded, B=3000) for
   PF and expectancy (no closed-form CI exists for a ratio of sums).
4. PARITY ANCHORS (mandatory, same script): pre-cost numbers must match the
   frozen values — whole PF 1.0279 / avg +0.014R / P(net>0) 0.4224 (n_net 973);
   top-prob OOS PF 1.1653/1.3045/1.2784 @ 0.2/0.25/0.4 (n_net 360/223/32) vs
   reports/top_prob_subset_analysis.json; rule 40-49 PF 1.23 / avg 0.0919
   (n=188) vs reports/score_bucket_report.json. Any mismatch => exit code != 0.
5. Frozen-baseline guard: md5 of the 13 frozen artifacts is compared against
   reports/integration/t1_rerun_md5_snapshot.json (0 mismatch required). This
   script never writes any frozen path.
6. Determinism: fixed seeds; two consecutive runs produce identical stdout,
   identical JSON and identical markdown (byte-identical => stable md5).

Usage (run from project root xauusd-liquidity-sweep/):
    .venv/bin/python scripts/apply_cost_analysis.py [--cost 0.05]

Outputs (NEW files only):
    reports/analysis/cost_0_05r_analysis.json
    reports/analysis/cost_0_05r_analysis.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.data.loader import read_parquet  # noqa: E402
from src.modeling.train import prepare_features_and_labels  # noqa: E402

COST_DEFAULT = 0.05
SEED = 42
BOOT_B = 3000
Z = 1.96
SNAPSHOT = "reports/integration/t1_rerun_md5_snapshot.json"

# Parity anchors (frozen / freeze_status_v1.2.0 + score_bucket_report.json)
WHOLE_PF_ANCHOR = 1.0279
WHOLE_AVG_ANCHOR = 0.0140
WHOLE_NPR_ANCHOR = 0.4224
WHOLE_N_NET = 973

TOP_PROB_ANCHORS = {  # threshold -> (expected PF, expected n_net)
    0.2: (1.1653, 360),
    0.25: (1.3045, 223),
    0.4: (1.2784, 32),
}

RULE_ANCHORS = {"n": 188, "pf": 1.23, "avg_net_r": 0.0919}  # score_bucket 40-49


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def wilson_ci(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson 95% score interval for a binomial proportion."""
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (float(centre - half), float(centre + half))


def _bootstrap_ci(net: np.ndarray, stat: str, seed: int, b: int = BOOT_B):
    """Deterministic percentile bootstrap CI (2.5, 97.5) for 'pf' or 'mean'.

    Infinities (bootstrap draw with no losing trade => PF undefined) are
    dropped; if fewer than 100 finite draws remain the CI is None.
    """
    n = net.size
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(b, n))
    s = net[idx]
    if stat == "pf":
        pos = np.maximum(s, 0.0).sum(axis=1)
        neg = (-np.minimum(s, 0.0)).sum(axis=1)
        vals = np.full(pos.shape, np.inf)
        ok = neg > 0.0
        vals[ok] = pos[ok] / neg[ok]
    else:  # mean
        vals = s.mean(axis=1)
    vals = vals[np.isfinite(vals)]
    if vals.size < 100:
        return None
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def stat_block(net: np.ndarray, seed_base: int) -> dict:
    """One statistics block (pre- or post-cost) over a net-R array."""
    net = np.asarray(net, dtype=float)
    n_net = int(net.size)
    if n_net == 0:
        return {"n_net": 0}
    pos_sum = float(net[net > 0].sum())
    neg_sum = float(abs(net[net < 0].sum()))
    pf = pos_sum / neg_sum if neg_sum > 0 else None
    avg = float(net.mean())
    n_pos = int((net > 0).sum())
    rate = n_pos / n_net
    lo, hi = wilson_ci(n_pos, n_net)
    pf_ci = _bootstrap_ci(net, "pf", seed_base)
    avg_ci = _bootstrap_ci(net, "mean", seed_base + 1)
    r = lambda v: None if v is None else round(float(v), 4)  # noqa: E731
    r2 = lambda v: None if v is None else round(float(v), 4)  # noqa: E731
    return {
        "n_net": n_net,
        "gross_profit_r": r2(pos_sum),
        "gross_loss_r": r2(neg_sum),
        "profit_factor": r(pf),
        "pf_ci95": None if pf_ci is None else [r(pf_ci[0]), r(pf_ci[1])],
        "avg_net_r": r(avg),
        "avg_net_r_ci95": None if avg_ci is None else [r(avg_ci[0]), r(avg_ci[1])],
        "net_positive_rate": r(rate),
        "net_positive_rate_wilson_ci95": [r(lo), r(hi)],
    }


def pf_at_cost(net: np.ndarray, cost: float) -> float | None:
    n = net - cost
    pos = float(n[n > 0].sum())
    neg = float(abs(n[n < 0].sum()))
    return pos / neg if neg > 0 else None


def breakeven_cost_pf(net: np.ndarray, lo: float = 0.0, hi: float = 0.6,
                      step: float = 0.0005) -> float | None:
    """Smallest cost at which PF <= 1 (linear interpolated). PF(c) is
    non-increasing in c because every position shifts by the same -c."""
    prev_c, prev_pf = None, None
    c = lo
    while c <= hi + 1e-9:
        p = pf_at_cost(net, c)
        if p is None:  # no losing trades at this cost -> PF infinite
            prev_c, prev_pf = c, None
            c += step
            continue
        if p <= 1.0:
            if prev_c is not None and prev_pf is not None and prev_pf > 1.0:
                # linear interpolation between (prev_c, prev_pf) and (c, p)
                t = (prev_pf - 1.0) / (prev_pf - p)
                return round(prev_c + t * (c - prev_c), 4)
            return round(c, 4)
        prev_c, prev_pf = c, p
        c += step
    return None  # still PF>1 beyond hi


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cost", type=float, default=COST_DEFAULT,
                    help=f"per-trade round-trip cost in R (default {COST_DEFAULT})")
    args = ap.parse_args(argv)
    cost_r = float(args.cost)

    out_json = os.path.join(ROOT, "reports/analysis/cost_0_05r_analysis.json")
    out_md = os.path.join(ROOT, "reports/analysis/cost_0_05r_analysis.md")
    # deterministic per-block bootstrap seeds (order-independent of dicts)
    BOOT_BASE = 20260904
    print("=" * 70)
    print("Cost analysis: apply %.2fR round-trip cost to frozen 974-event pool" % cost_r)
    print("=" * 70)

    # --- load data exactly like the frozen top-prob generator -------------
    df = read_parquet(os.path.join(ROOT, "data/processed/labeled_events.parquet"))
    X, y, feature_names = prepare_features_and_labels(df)
    if len(X) != 974:
        raise SystemExit(f"FATAL: expected 974 events, got {len(X)}")
    np.random.seed(SEED)
    base = df.loc[X.index]  # alignment identical to frozen generator
    net_r = base["net_result_r"].to_numpy(dtype=float)
    n_nan = int(np.isnan(net_r).sum())

    # OOS probabilities via frozen top-prob protocol (no re-train of model.pkl)
    n_train = int(len(X) * 0.8)
    impute_medians = {}
    for col in X.columns:
        if X.iloc[:n_train][col].isna().any():
            impute_medians[col] = float(X.iloc[:n_train][col].median())
    X_imputed = X.fillna(impute_medians)
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000, random_state=SEED)),
    ])
    probas_oos = cross_val_predict(pipe, X_imputed, y, cv=5,
                                   method="predict_proba")[:, 1]
    print(f"events={len(X)}  net-valid={int((~np.isnan(net_r)).sum())}  "
          f"net_NaN_excluded={n_nan}  baseline_win_rate(hit 2R)={float(y.mean()):.4f}  "
          f"baseline_P(net>0)={float((net_r[~np.isnan(net_r)] > 0).mean()):.4f}")

    rule_score = base["rule_score"].to_numpy(dtype=float)
    level_type = base["level_type"].to_numpy(dtype=object)
    h1_trend = base["h1_trend"].to_numpy(dtype=float)
    direction = base["direction"].to_numpy(dtype=object)
    is_long = direction == "long"
    is_short = direction == "short"
    up = h1_trend > 0
    down = h1_trend < 0

    masks: list[tuple[str, str, np.ndarray]] = []
    masks.append(("core", "whole_pool", np.ones(len(X), dtype=bool)))
    for th in (0.2, 0.25, 0.4):
        masks.append(("top_prob", f"top_prob_ge_{th:g}", probas_oos >= th))
    masks.append(("rule", "rule_40_49", (rule_score >= 40) & (rule_score < 50)))
    for lt in ("equal", "swing", "prev_day"):
        masks.append(("level_type", f"level_type_{lt}", level_type == lt))
    cell_defs = [
        ("long_h1_down", is_long & down), ("long_h1_up", is_long & up),
        ("short_h1_down", is_short & down), ("short_h1_up", is_short & up),
    ]
    for name, m in cell_defs:
        masks.append(("dir_x_trend", name, m))
    cung = (is_short & up) | (is_long & down)          # catch-the-knife
    nguoc = (is_long & up) | (is_short & down)         # follows H1 trend
    masks.append(("trend_composite", "sweep_cung_chieu_trend_h1", cung))
    masks.append(("trend_composite", "sweep_nguoc_trend", nguoc))
    for cname, cm in (("sweep_cung_chieu_trend_h1", cung),
                      ("sweep_nguoc_trend", nguoc)):
        for lt in ("swing", "equal", "prev_day"):
            masks.append(("cross_trend_x_level_type",
                          f"{cname}_x_{lt}", cm & (level_type == lt)))

    valid = ~np.isnan(net_r)

    # --- parity anchor helpers -------------------------------------------
    def pf4(net: np.ndarray) -> float | None:
        p = net[net > 0].sum()
        l = abs(net[net < 0].sum())
        return float(p / l) if l > 0 else None

    parity = {"checks": [], "pass": True}

    def check(name: str, ok: bool, detail: str) -> None:
        parity["checks"].append({"anchor": name, "pass": bool(ok), "detail": detail})
        parity["pass"] = parity["pass"] and bool(ok)

    # (a) whole-pool anchors (freeze_status_v1.2.0 / t5 pooled)
    wv = net_r[valid]
    w_pf, w_avg, w_npr = pf4(wv), float(wv.mean()), float((wv > 0).mean())
    check("whole_pool_pf_1.0279", abs(w_pf - WHOLE_PF_ANCHOR) < 5e-5,
          f"PF={w_pf:.6f} (anchor {WHOLE_PF_ANCHOR})")
    check("whole_pool_avg_0.014R", abs(w_avg - WHOLE_AVG_ANCHOR) < 1e-3,
          f"avg_net_r={w_avg:.6f} (anchor {WHOLE_AVG_ANCHOR})")
    check("whole_pool_Pnet>0_0.4224", abs(w_npr - WHOLE_NPR_ANCHOR) < 5e-5,
          f"P(net>0)={w_npr:.6f} (anchor {WHOLE_NPR_ANCHOR})")
    check("whole_pool_n_net_973", int(wv.size) == WHOLE_N_NET,
          f"n_net={wv.size}")

    # (b) top-prob OOS vs frozen reports/top_prob_subset_analysis.json
    frozen_tp = json.load(open(os.path.join(ROOT, "reports/top_prob_subset_analysis.json"),
                               encoding="utf-8"))
    oos_rows = {r["threshold"]: r for r in frozen_tp["out_of_sample"]["subsets"]}
    for th, (exp_pf, exp_n) in sorted(TOP_PROB_ANCHORS.items()):
        m = probas_oos >= th
        nv = net_r[m & valid]
        got_pf = round(pf4(nv), 4) if pf4(nv) is not None else None
        fr = oos_rows[th]
        ok_pf = got_pf == round(exp_pf, 4)
        ok_n = int(nv.size) == exp_n
        ok_file = (got_pf == round(float(fr["profit_factor"]), 4)
                   and int(nv.size) == int(fr["n_with_net_result"]))
        check(f"top_prob_ge_{th:g}_PF_{exp_pf}", ok_pf and ok_n and ok_file,
              f"PF={got_pf} n_net={int(nv.size)} (frozen {fr['profit_factor']}/"
              f"{fr['n_with_net_result']})")

    # (c) rule bucket 40-49 vs reports/score_bucket_report.json
    rb = json.load(open(os.path.join(ROOT, "reports/score_bucket_report.json"),
                        encoding="utf-8"))
    row40 = next(r for r in rb if r["bucket"] == "40-49")
    rm = (rule_score >= 40) & (rule_score < 50)
    rv = net_r[rm & valid]
    got = {"n": int(rv.size), "pf": round(pf4(rv), 2), "avg": round(float(rv.mean()), 4)}
    check("rule_40_49_n_188_pf_1.23_avg_0.0919",
          got["n"] == RULE_ANCHORS["n"] == int(row40["event_count"])
          and got["pf"] == round(RULE_ANCHORS["pf"], 2) == round(float(row40["profit_factor"]), 2)
          and got["avg"] == round(RULE_ANCHORS["avg_net_r"], 4) == round(float(row40["avg_net_result_r"]), 4),
          f"n={got['n']} PF={got['pf']} avg_net_r={got['avg']} (frozen {row40['event_count']}/"
          f"{row40['profit_factor']}/{row40['avg_net_result_r']})")
    # (d) rule 40-49 member count == whole bucket counts
    check("rule_40_49_count_matches_bucket_report", got["n"] == 188,
          f"n={got['n']}")

    print("\n--- PARITY (pre-cost vs frozen) ---")
    for c in parity["checks"]:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['anchor']}: {c['detail']}")
    if not parity["pass"]:
        print("FATAL: pre-cost parity mismatch -> refusing to write outputs")
        return 2

    # --- compute blocks ---------------------------------------------------
    blocks: dict[str, dict] = {}
    names: list[str] = []
    for idx, (family, name, m) in enumerate(masks):
        names.append(name)
        sel_net = net_r[m & valid]
        seed_base = BOOT_BASE + 97 * idx
        pre = stat_block(sel_net, seed_base)
        if pre["n_net"] == 0:
            blocks[name] = {"family": family, "n": int(m.sum()),
                            "n_net": 0, "pre": None, "post": None,
                            "breakeven_cost_pf": None}
            continue
        post = stat_block(sel_net - cost_r, seed_base + 1000)
        blocks[name] = {
            "family": family,
            "n": int(m.sum()),
            "n_net": pre["n_net"],
            "pre": pre,
            "post": post,
            "breakeven_cost_pf": breakeven_cost_pf(sel_net),
            "expectancy_breakeven_cost": round(float(sel_net.mean()), 4),
            "pf_at_costs": {f"{c:.2f}": (None if pf_at_cost(sel_net, c) is None
                                         else round(pf_at_cost(sel_net, c), 4))
                            for c in (0.0, 0.03, 0.05, 0.07, 0.10)},
        }

    # --- gate verdict -----------------------------------------------------
    def verdict(name: str) -> dict:
        b = blocks[name]
        pre_pf, post_pf = b["pre"]["profit_factor"], b["post"]["profit_factor"]
        post_lo = b["post"]["pf_ci95"][0] if b["post"]["pf_ci95"] else None
        post_hi = b["post"]["pf_ci95"][1] if b["post"]["pf_ci95"] else None
        return {
            "block": name,
            "n_net": b["n_net"],
            "cost_r": cost_r,
            "pre_pf": pre_pf,
            "post_pf": post_pf,
            "post_pf_ci95": b["post"]["pf_ci95"],
            "post_pf_gt_1_point": post_pf is not None and post_pf > 1.0,
            "post_pf_ci_lb_gt_1": post_lo is not None and post_lo > 1.0,
            "pre_expectancy_r": b["pre"]["avg_net_r"],
            "post_expectancy_r": b["post"]["avg_net_r"],
            "post_expectancy_ci95": b["post"]["avg_net_r_ci95"],
            "post_expectancy_gt_0": b["post"]["avg_net_r"] is not None
                                    and b["post"]["avg_net_r"] > 0.0,
            "breakeven_cost_pf": b["breakeven_cost_pf"],
            "post_pf_at_0_03": b["pf_at_costs"]["0.03"],
            "post_pf_at_0_07": b["pf_at_costs"]["0.07"],
        }

    gate = {
        "cost_assumption": {
            "cost_r": cost_r,
            "definition": "flat round-trip cost in R per position "
                          "(spread + slippage + commission; symmetric long/short)",
            "source": "reports/freeze_status_v1.2.0.md decision #4 (real cost "
                      "~0.05R/trade); reports/top_prob_subset_analysis.md Cost "
                      "Considerations (spread+slippage ~0.05R per trade). "
                      "Measured per-event cost run was deferred (T16, cost=0 "
                      "placeholder in frozen config) -> 0.05R is the stated "
                      "planning assumption; breakeven-cost column quantifies "
                      "headroom.",
        },
        "core_verdicts": {
            name: verdict(name)
            for name in ("whole_pool", "rule_40_49", "top_prob_ge_0.25")
        },
        "all_blocks_verdicts": {name: verdict(name) for name in names},
    }

    # --- frozen md5 guard -------------------------------------------------
    snap = json.load(open(os.path.join(ROOT, SNAPSHOT), encoding="utf-8"))
    md5_results = []
    md5_ok = True
    for f in snap["files"]:
        p = os.path.join(ROOT, f["path"])
        h = md5_file(p)
        ok = h == f["md5"]
        md5_ok = md5_ok and ok
        md5_results.append({"path": f["path"], "expected": f["md5"],
                            "actual": h, "pass": ok})
    print(f"\n--- FROZEN MD5 GUARD: {sum(1 for m in md5_results if m['pass'])}/"
          f"{len(md5_results)} match snapshot ---")
    if not md5_ok:
        for m in md5_results:
            if not m["pass"]:
                print(f"  [FAIL] {m['path']}: {m['actual']} != {m['expected']}")
        return 3

    # ----------------------------------------------------------------------
    output = {
        "dataset": "data/processed/labeled_events.parquet (frozen, 974)",
        "n_total": len(X),
        "n_net_valid": int(valid.sum()),
        "n_net_nan_excluded": n_nan,
        "cost_r": cost_r,
        "cost_assumption": gate["cost_assumption"],
        "probability_source_oos": "5-fold CV OOS probabilities (clean "
                                  "30-feature model, train-80% median "
                                  "imputation; frozen top_prob protocol, "
                                  "regenerated deterministically, no re-train)",
        "ci_method": "P(net>0): Wilson 95%; PF & expectancy: deterministic "
                     "percentile bootstrap 95% (seed fixed, B=3000)",
        "parity_pre_cost": parity,
        "frozen_md5_guard": {"snapshot": SNAPSHOT, "results": md5_results,
                             "all_match": md5_ok},
        "blocks": blocks,
        "gate": gate,
    }

    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(render_md(output))

    # --- stdout summary (deterministic) ----------------------------------
    print("\n--- CORE GATE (cost %.2fR) ---" % cost_r)
    for name in ("whole_pool", "rule_40_49", "top_prob_ge_0.25"):
        v = gate["core_verdicts"][name]
        print(f"  {name:<18} n={v['n_net']:<4} pre_PF={v['pre_pf']:<7} "
              f"post_PF={v['post_pf']} CI={v['post_pf_ci95']} "
              f"expectancy_post={v['post_expectancy_r']}R "
              f"pf_gt1={v['post_pf_gt_1_point']} ci_lb_gt1={v['post_pf_ci_lb_gt_1']} "
              f"breakeven_cost={v['breakeven_cost_pf']}")
    print(f"whole pre PF={gate['core_verdicts']['whole_pool']['pre_pf']} "
          f"expectancy={gate['core_verdicts']['whole_pool']['pre_expectancy_r']}R")
    print(f"\nWrote:\n  {os.path.relpath(out_json, ROOT)}\n  {os.path.relpath(out_md, ROOT)}")
    print("Done!")
    return 0


# --------------------------------------------------------------------------
# markdown rendering
# --------------------------------------------------------------------------
def fmt(x, nd: int = 4) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def fmt_ci(ci) -> str:
    if not ci:
        return "—"
    return f"[{fmt(ci[0])}, {fmt(ci[1])}]"


def _row(name: str, b: dict, cost_r: float) -> list[str]:
    if b["n_net"] == 0:
        return [name, "0", "—", "—", "—", "—", "—", "—", "—", "—"]
    pre, post = b["pre"], b["post"]
    gt1 = post["profit_factor"] is not None and post["profit_factor"] > 1.0
    return [
        name,
        str(b["n_net"]),
        fmt(pre["profit_factor"], 4),
        f"{fmt(post['profit_factor'], 4)} {fmt_ci(post['pf_ci95'])}",
        fmt(pre["avg_net_r"], 4),
        f"{fmt(post['avg_net_r'], 4)} {fmt_ci(post['avg_net_r_ci95'])}",
        fmt(pre["net_positive_rate"], 4),
        f"{fmt(post['net_positive_rate'], 4)} "
        f"{fmt_ci(post['net_positive_rate_wilson_ci95'])}",
        "YES" if gt1 else "no",
        fmt(b["breakeven_cost_pf"], 4),
    ]


def render_md(o: dict) -> str:
    cost_r = o["cost_r"]
    L: list[str] = []
    A = L.append
    A("# Cost Analysis: ~%.2fR round-trip on the frozen 974-event pool (gate R1/R2)" % cost_r)
    A("")
    A(f"**Dataset**: frozen 974 events, {o['n_net_valid']} net-valid "
      f"({o['n_net_nan_excluded']} ambiguous excluded). `net_result_r` is the "
      f"REAL labeled result with `cost_r=0` placeholder → pre-cost == gross.")
    A("")
    A("## Cost assumption")
    A("")
    A(f"- Flat **{cost_r:.2f}R per position** (round-trip spread + slippage + "
      "commission, symmetric long/short).")
    A("- Source: `reports/freeze_status_v1.2.0.md` decision #4 (real cost "
      "~0.05R/trade); `reports/top_prob_subset_analysis.md` Cost Considerations. "
      "No better measured figure exists (per-event cost run deferred, T16).")
    A("- Post-cost net per position = `net_result_r − %.2fR`." % cost_r)
    A("")
    A("## Parity anchors (pre-cost, same script) — MUST match frozen")
    A("")
    A("| anchor | result | detail |")
    A("|---|---|---|")
    for c in o["parity_pre_cost"]["checks"]:
        A(f"| {c['anchor']} | {'**PASS**' if c['pass'] else '**FAIL**'} | {c['detail']} |")
    A("")
    A("| anchor group | frozen | recomputed |")
    A("|---|---|---|")
    whole = o["blocks"]["whole_pool"]
    A(f"| whole pool | PF 1.0279 · avg +0.014R · P(net>0) 0.4224 · n_net 973 | "
      f"PF {whole['pre']['profit_factor']} · {whole['pre']['avg_net_r']}R · "
      f"{whole['pre']['net_positive_rate']} · {whole['n_net']} |")
    tp = o["blocks"]
    tprow = " · ".join(
        f"@{t}: PF {tp[f'top_prob_ge_{t:g}']['pre']['profit_factor']} "
        f"(n_net {tp[f'top_prob_ge_{t:g}']['n_net']})"
        for t in (0.2, 0.25, 0.4))
    A(f"| top-prob OOS | 1.1653/1.3045/1.2784 @0.2/0.25/0.4 (360/223/32) | {tprow} |")
    r40 = o["blocks"]["rule_40_49"]
    A(f"| rule bucket 40-49 | PF 1.23 · avg 0.0919 · n 188 | PF {r40['pre']['profit_factor']} · "
      f"{r40['pre']['avg_net_r']} · {r40['n']} |")
    A("")
    A("## Gate: does the edge survive {:.2f}R cost?".format(cost_r))
    A("")
    A("PF/avg CI = deterministic percentile bootstrap 95% (B=3000, fixed seed). "
      "P(net>0) CI = Wilson 95%. `breakeven_cost_pf` = cost at which PF falls to 1 "
      "(headroom above 0.05R ⇒ survives; below ⇒ dies at 0.05R).")
    A("")
    A("| block | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | "
      "pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for name in ("whole_pool", "rule_40_49", "top_prob_ge_0.25"):
        A("| " + " | ".join(_row(name, o["blocks"][name], cost_r)) + " |")
    A("")
    A("### Sensitivity PF at other costs (whole / rule 40-49 / top-prob ≥0.25)")
    A("")
    A("| block | 0.00R | 0.03R | 0.05R | 0.07R | 0.10R |")
    A("|---|---|---|---|---|---|")
    for name in ("whole_pool", "rule_40_49", "top_prob_ge_0.25"):
        b = o["blocks"][name]
        A(f"| {name} | " + " | ".join(fmt(b['pf_at_costs'][k], 4)
                                       for k in ("0.00", "0.03", "0.05", "0.07", "0.10"))
          + " |")
    A("")
    A("## level_type groups")
    A("")
    A("| group | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | "
      "pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for name in ("level_type_equal", "level_type_swing", "level_type_prev_day"):
        A("| " + " | ".join(_row(name, o["blocks"][name], cost_r)) + " |")
    A("")
    A("## trend-context: direction × H1 trend (h1_trend = H1 EMA50 slope sign)")
    A("")
    A("| cell | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | "
      "pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for name in ("long_h1_down", "long_h1_up", "short_h1_down", "short_h1_up"):
        A("| " + " | ".join(_row(name, o["blocks"][name], cost_r)) + " |")
    A("")
    A("## trend-context composite clusters")
    A("")
    A("- **sweep_cung_chieu_trend_h1** (catch-the-knife) = SHORT while H1 up ∪ "
      "LONG while H1 down — fades the H1-trend thrust.")
    A("- **sweep_nguoc_trend** = LONG while H1 up ∪ SHORT while H1 down — "
      "sweep counters the H1 trend, trade follows it.")
    A("")
    A("| cluster | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | "
      "pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for name in ("sweep_cung_chieu_trend_h1", "sweep_nguoc_trend"):
        A("| " + " | ".join(_row(name, o["blocks"][name], cost_r)) + " |")
    A("")
    A("## cross: cluster × level_type")
    A("")
    A("| cross | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R | "
      "post P(net>0) | breakeven cost |")
    A("|---|---|---|---|---|---|---|---|")
    for name in o["blocks"]:
        if o["blocks"][name]["family"] != "cross_trend_x_level_type":
            continue
        b = o["blocks"][name]
        if b["n_net"] == 0:
            A(f"| {name} | 0 | — | — | — | — | — | — |")
            continue
        pre, post = b["pre"], b["post"]
        A(f"| {name} | {b['n_net']} | {fmt(pre['profit_factor'])} | "
          f"{fmt(post['profit_factor'])} {fmt_ci(post['pf_ci95'])} | "
          f"{fmt(pre['avg_net_r'])} | {fmt(post['avg_net_r'])} | "
          f"{fmt(post['net_positive_rate'])} "
          f"{fmt_ci(post['net_positive_rate_wilson_ci95'])} | "
          f"{fmt(b['breakeven_cost_pf'])} |")
    A("")
    A("## Gate answer (decision R1/R2)")
    A("")
    g = o["gate"]["core_verdicts"]
    w, r40v, t25 = g["whole_pool"], g["rule_40_49"], g["top_prob_ge_0.25"]
    def bullet(v, name):
        surv = "GIỮ PF>1" if v["post_pf_gt_1_point"] else "RỚT xuống <1"
        robust = " (CI lower bound > 1)" if v["post_pf_ci_lb_gt_1"] else \
                 " (CI vẫn chứa 1 → chỉ point estimate)"
        exp = "dương" if v["post_expectancy_gt_0"] else "âm"
        A(f"- **{name}** (n_net {v['n_net']}): PF {v['pre_pf']} → "
          f"**{v['post_pf']}** sau {v['cost_r']:.2f}R → **{surv}**{robust}; "
          f"expectancy {v['pre_expectancy_r']}R → **{v['post_expectancy_r']}R "
          f"({exp})**; breakeven cost PF = {v['breakeven_cost_pf']}R.")
    A("")
    bullet(w, "Whole pool (baseline)")
    bullet(r40v, "Rule bucket 40-49")
    bullet(t25, "Top-prob OOS ≥0.25")
    A("")
    if w["post_expectancy_r"] is not None and w["post_expectancy_r"] <= 0:
        A("- **Baseline expectancy toàn pool sau cost là ÂM** — xác nhận nhận định "
          "freeze_status v1.2.0 (0.05R biến +0.014R thành âm).")
    if (w["post_pf_gt_1_point"], r40v["post_pf_gt_1_point"],
            t25["post_pf_gt_1_point"]) == (False, False, False):
        A("- Không bucket/subset nào giữ PF>1 sau cost: KHÔNG trade nguyên trạng "
          "(R1: dừng / chỉ research).")
    elif w["post_pf_gt_1_point"]:
        A("- Cả baseline lẫn subset giữ PF>1 point estimate → edge vẫn còn ở mức "
          "gợi ý; kiểm chứng thêm (chi phí đo thật, n lớn hơn) trước khi trade.")
    else:
        A("- Whole pool rớt <1; chỉ subset lọc (rule 40-49 / top-prob ≥0.25) còn "
          "PF>1 point estimate nhưng CI chứa 1 → edge chỉ mang tính gợi ý, "
          "không trade ML nguyên trạng; chỉ dùng làm bộ lọc ranking.")
    A("")
    A("## Frozen-baseline guard")
    A("")
    ok_all = o["frozen_md5_guard"]["all_match"]
    A(f"- md5 snapshot check: {sum(1 for m in o['frozen_md5_guard']['results'] if m['pass'])}/"
      f"{len(o['frozen_md5_guard']['results'])} match "
      f"`{o['frozen_md5_guard']['snapshot']}` → "
      f"{'**0 mismatch — baseline untouched**' if ok_all else '**MISMATCH — see JSON**'}")
    A("")
    A("— End of cost analysis (t8) —")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
