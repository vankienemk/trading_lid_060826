#!/usr/bin/env python3
"""Cost sensitivity 0.01R-0.10R × all subsets + breakeven map.
    Sweep cost from 0.00 to 0.10R step 0.01R on:
     - whole pool
     - rule 40-49
     - top-prob OOS ≥0.25 (5-fold CV)
     - trend composites (cung_chieu / nguoc)
     - level_type groups (equal/swing/prev_day)
    Plus: exact breakeven cost (PF=1) per group.

    Parity: cost=0 MUST match frozen (whole PF 1.0279, top-prob PF 1.1653/1.3045/1.2784,
    rule 40-49 PF 1.23/avg 0.0919).

Outputs:
    reports/analysis/phase1/cost_sensitivity_map.json
    reports/analysis/phase1/cost_sensitivity_map.md
"""

from __future__ import annotations
import hashlib, json, os, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from src.data.loader import read_parquet
from src.modeling.train import prepare_features_and_labels

SEED = 42; BOOT_B = 3000; Z = 1.96
OD = os.path.join(ROOT, "reports", "analysis", "phase1")

# Parity anchors
WPF_A = 1.0279; WAVG_A = 0.0140; WNPR_A = 0.4224; WNN = 973
TP_A = {0.2: (1.1653, 360), 0.25: (1.3045, 223), 0.4: (1.2784, 32)}
RULE_A = {"n": 188, "pf": 1.23, "avg": 0.0919}

def _r(v, nd=4): return None if v is None else round(float(v), nd)

def wc(k, n):
    if n == 0: return None, None
    p = k / n; d = 1.0 + Z*Z/n
    c = (p + Z*Z/(2*n))/d; h = Z * np.sqrt(p*(1-p)/n + Z*Z/(4*n*n))/d
    return float(c-h), float(c+h)

def bci(net, seed):
    r = np.random.default_rng(seed); i = r.integers(0, net.size, size=(BOOT_B, net.size))
    s = net[i]; pos = np.maximum(s, 0).sum(1); neg = (-np.minimum(s, 0)).sum(1)
    v = np.full(pos.shape, np.inf); ok = neg > 0; v[ok] = pos[ok] / neg[ok]
    v = v[np.isfinite(v)]
    if v.size < 100: return None
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))

def sb(net, seed):
    net = np.asarray(net, float); n = int(net.size)
    if n == 0: return {"n_net": 0}
    ps = float(net[net > 0].sum()); ns = float(abs(net[net < 0].sum()))
    pf = ps / ns if ns > 0 else None; avg = float(net.mean())
    np_ = int((net > 0).sum()); lo, hi = wc(np_, n)
    pfc = bci(net, seed); avc = bci(net, seed + 1)
    return {"n_net": n, "profit_factor": _r(pf),
        "pf_ci95": None if pfc is None else [_r(pfc[0]), _r(pfc[1])],
        "avg_net_r": _r(avg),
        "avg_net_r_ci95": None if avc is None else [_r(avc[0]), _r(avc[1])],
        "net_positive_rate": _r(np_ / n),
        "net_positive_rate_wilson_ci95": [_r(lo), _r(hi)]}

def pfc_(net, c):
    n = net - c; p = float(n[n > 0].sum()); nv = float(abs(n[n < 0].sum()))
    return p / nv if nv > 0 else None

def be(net):
    pc = pp = None; c = 0.0
    while c <= 0.6 + 1e-9:
        p = pfc_(net, c)
        if p is not None and p <= 1.0:
            if pc is not None and pp is not None and pp > 1.0:
                t = (pp - 1) / (pp - p); return _r(pc + t * (c - pc))
            return _r(c)
        if p is not None: pc, pp = c, p
        c += 0.0005
    return None

def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def main():
    os.makedirs(OD, exist_ok=True)
    out_json = os.path.join(OD, "cost_sensitivity_map.json")
    out_md = os.path.join(OD, "cost_sensitivity_map.md")

    # Load
    df = read_parquet(os.path.join(ROOT, "data/processed/labeled_events.parquet"))
    X, y, _ = prepare_features_and_labels(df)
    assert len(X) == 974
    base = df.loc[X.index]
    nr = base["net_result_r"].to_numpy(float)
    rs = base["rule_score"].to_numpy(float)
    dr = base["direction"].to_numpy(object)
    ht = base["h1_trend"].to_numpy(float)
    lt = base["level_type"].to_numpy(object)
    v = ~np.isnan(nr)
    is_long = dr == "long"; is_short = dr == "short"
    up = ht > 0; down = ht < 0

    # OOS probs
    n_tr = int(len(X) * 0.8)
    im = {c: float(X.iloc[:n_tr][c].median()) for c in X.columns if X.iloc[:n_tr][c].isna().any()}
    Xi = X.fillna(im)
    pp = Pipeline([("s", StandardScaler()), ("c", LogisticRegression(max_iter=1000, random_state=SEED))])
    pr = cross_val_predict(pp, Xi, y, cv=5, method="predict_proba")[:, 1]

    # Groups
    cung = (is_short & up) | (is_long & down)
    nguoc = (is_long & up) | (is_short & down)
    groups_def = [
        ("whole_pool", v),
        ("rule_40_49", (rs >= 40) & (rs < 50) & v),
        ("top_prob_ge_0.25", (pr >= 0.25) & v),
        ("top_prob_ge_0.20", (pr >= 0.20) & v),
        ("sweep_cung_chieu_trend_h1", cung & v),
        ("sweep_nguoc_trend", nguoc & v),
        ("level_type_equal", (lt == "equal") & v),
        ("level_type_swing", (lt == "swing") & v),
        ("level_type_prev_day", (lt == "prev_day") & v),
    ]

    cost_levels = [round(0.01 * i, 2) for i in range(0, 11)]
    result = {"cost_levels": cost_levels, "groups": {}, "breakeven_summary": {}}

    for gn, mask in groups_def:
        nn = int(mask.sum())
        if nn == 0: result["groups"][gn] = {"n_net": 0}; continue
        ng = nr[mask]
        pre = sb(ng, SEED)
        post = {}
        for c in cost_levels:
            s = sb(ng - c, SEED + int(c * 100) + 1)
            post[f"{c:.2f}"] = {"cost_r": c, "profit_factor": s["profit_factor"],
                                 "pf_ci95": s["pf_ci95"], "avg_net_r": s["avg_net_r"],
                                 "avg_net_r_ci95": s["avg_net_r_ci95"],
                                 "net_positive_rate": s["net_positive_rate"]}
        result["groups"][gn] = {"n_net": nn, "pre_cost": pre,
            "breakeven_cost_pf": be(ng), "expectancy_breakeven_cost": _r(float(ng.mean())),
            "post_at_costs": post}
        result["breakeven_summary"][gn] = be(ng)

    # Parity check
    wv = nr[v]
    pf_ = float(wv[wv > 0].sum() / abs(wv[wv < 0].sum()))
    par = {"checks": [], "pass": True}
    def ck(n, p, d):
        par["pass"] = par["pass"] and bool(p)
        par["checks"].append({"anchor": n, "pass": bool(p), "detail": d})
    ck("pf_1.0279", abs(pf_ - WPF_A) < 5e-5, f"PF={pf_:.6f}")
    ck("avg_0.014R", abs(wv.mean() - WAVG_A) < 1e-3, f"avg={wv.mean():.6f}")
    ck("Pnet>0_0.4224", abs((wv > 0).mean() - WNPR_A) < 5e-5, f"P={(wv>0).mean():.6f}")
    ck("n_net_973", int(wv.size) == WNN, f"n={wv.size}")
    for th, (ep, en) in sorted(TP_A.items()):
        m = pr >= th; nw = nr[m & v]
        gp = float(nw[nw > 0].sum() / abs(nw[nw < 0].sum())) if (nw[nw < 0].sum() != 0) else None
        ck(f"top_prob_{th:g}_PF", gp is not None and abs(gp - ep) < 5e-3 and int(nw.size) == en,
           f"@ {th}: PF={gp} n={int(nw.size)}")
    rm = (rs >= 40) & (rs < 50); rv = nr[rm & v]
    rgp = float(rv[rv > 0].sum() / abs(rv[rv < 0].sum())) if (rv[rv < 0].sum() != 0) else None
    ck("rule_40_49_PF_1.23_avg_0.0919", rgp is not None and abs(rgp - 1.23) < 0.01 and abs(rv.mean() - 0.0919) < 0.001,
       f"rule 40-49: PF={rgp}, avg={rv.mean():.4f}")

    # md5 guard (frozen)
    try:
        snap = json.load(open(os.path.join(ROOT, "reports/integration/t1_rerun_md5_snapshot.json")))
        md5_results = []; md5_ok = True
        for f in snap["files"]:
            p = os.path.join(ROOT, f["path"])
            h = md5_file(p); ok = h == f["md5"]; md5_ok = md5_ok and ok
            md5_results.append({"path": f["path"], "expected": f["md5"], "actual": h, "pass": ok})
        par["md5_guard"] = {"results": md5_results, "all_match": md5_ok,
                            "n_pass": sum(1 for m in md5_results if m["pass"]),
                            "n_total": len(md5_results)}
    except: pass

    result["parity"] = par

    # Print parity
    print(f"n_cung_chieu={int(cung.sum())} n_nguoc={int(nguoc.sum())} sum={int(cung.sum())+int(nguoc.sum())}")
    print("=== PARITY ===")
    for c in par["checks"]: print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['anchor']}: {c['detail']}")
    if "md5_guard" in par:
        g = par["md5_guard"]
        print(f"  md5 guard: {g['n_pass']}/{g['n_total']} match")

    with open(out_json, "w") as f: json.dump(result, f, indent=2)

    # Markdown
    md = [f"# Cost Sensitivity Map (0.01R-0.10R)\n\n"]
    md.append(f"**Dataset**: frozen 974 events, {int(v.sum())} net-valid.\n\n")
    md.append(f"Cost levels: {cost_levels}\n\n")
    md.append("## Breakeven Summary\n\n| group | breakeven cost (PF=1) | expectancy | n_net |\n|---|---|---|---|\n")
    for gn in result["breakeven_summary"]:
        gd = result["groups"].get(gn, {})
        if gd.get("n_net", 0) == 0: continue
        md.append(f"| {gn} | {gd['breakeven_cost_pf']} | {gd['expectancy_breakeven_cost']} | {gd['n_net']} |\n")
    md.append("\n## Per-Group Cost Sweep\n\n")
    for gn, gd in sorted(result["groups"].items(), key=lambda x: x[1].get("n_net", 0), reverse=True):
        if gd.get("n_net", 0) == 0: continue
        md.append(f"### {gn} (n_net {gd['n_net']})\n\n")
        md.append(f"Pre-cost: PF {gd['pre_cost']['profit_factor']}, avg {gd['pre_cost']['avg_net_r']}R\n\n")
        md.append("| cost | PF | 95% CI | avg R | P(net>0) |\n|---|---|---|---|---|\n")
        for ck_, cv in sorted(gd["post_at_costs"].items(), key=lambda x: float(x[0])):
            ci = cv.get("pf_ci95"); cis = f"[{ci[0]},{ci[1]}]" if ci else "—"
            md.append(f"| {ck_} | {cv['profit_factor']} | {cis} | {cv['avg_net_r']} | {cv['net_positive_rate']} |\n")
        md.append("\n")
    with open(out_md, "w") as f: f.write("".join(md))

    print(f"Wrote {os.path.relpath(out_json, ROOT)} and {os.path.relpath(out_md, ROOT)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
