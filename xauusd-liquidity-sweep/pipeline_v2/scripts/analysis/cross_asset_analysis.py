#!/usr/bin/env python3
"""Cross-asset V2 analysis: apply V2 pipeline (nguoc_trend + max_pen≤0.20 + R:R 3.0)
to XAUUSD full (2018→2026) and EURUSD full (2018→2026).

Usage:
    cd xauusd-liquidity-sweep/
    python pipeline_v2/scripts/analysis/cross_asset_analysis.py

Outputs:
    pipeline_v2/reports/analysis/cross_asset/cross_asset_comparison.{json,md}
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

PROJECT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT)

from src.data.loader import load_ohlcv, normalize_ohlcv
from src.indicators.atr import add_atr
from pipeline_v2.src.events.sweep_detector_v2 import (
    build_sweep_events_v2, detect_sweeps_v2,
)
from src.events.confirmation import attach_confirmations
from src.labeling.outcome_builder import build_event_labels

SEED = 42
BOOT_B = 3000
Z = 1.96
COST_BASE = 0.05
TARGET_R = 3.0
NGUOC_TREND = True
MAX_PEN = 0.20
OUT_DIR = os.path.join(PROJECT, "pipeline_v2", "reports", "analysis", "cross_asset")

XAUUSD_RAW = os.path.join(PROJECT, "data", "raw", "XAUUSD_M15_201801020900_202609032245.csv")
EURUSD_RAW = os.path.join(PROJECT, "data", "raw", "EURUSD_M15_201801020000_202609040000.csv")


def _r(v, nd=4):
    return None if v is None else round(float(v), nd)


def wc(k, n):
    if n == 0:
        return None, None
    p = k / n
    d = 1.0 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * np.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return float(c - h), float(c + h)


def bci(net, seed):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, net.size, size=(BOOT_B, net.size))
    s = net[idx]
    pos = np.maximum(s, 0).sum(1)
    neg = (-np.minimum(s, 0)).sum(1)
    v = np.full(pos.shape, np.inf)
    ok = neg > 0
    v[ok] = pos[ok] / neg[ok]
    v = v[np.isfinite(v)]
    if v.size < 100:
        return None
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))


def sb(net, seed):
    net = np.asarray(net, float)
    n = int(net.size)
    if n == 0:
        return {"n_net": 0}
    ps = float(net[net > 0].sum())
    ns = float(abs(net[net < 0].sum()))
    pf = ps / ns if ns > 0 else None
    avg = float(net.mean())
    np_ = int((net > 0).sum())
    lo, hi = wc(np_, n)
    pfc = bci(net, seed)
    return {
        "n_net": n,
        "profit_factor": _r(pf),
        "pf_ci95": None if pfc is None else [_r(pfc[0]), _r(pfc[1])],
        "avg_net_r": _r(avg),
        "net_positive_rate": _r(np_ / n),
        "net_positive_rate_wilson_ci95": [_r(lo), _r(hi)],
    }


def pfc_(net, c):
    n = net - c
    p = float(n[n > 0].sum())
    nv = float(abs(n[n < 0].sum()))
    return p / nv if nv > 0 else None


def be(net):
    pc = pp = None
    c = 0.0
    while c <= 1.0 + 1e-9:
        p = pfc_(net, c)
        if p is not None and p <= 1.0:
            if pc is not None and pp is not None and pp > 1.0:
                t = (pp - 1) / (pp - p)
                return _r(pc + t * (c - pc))
            return _r(c)
        if p is not None:
            pc, pp = c, p
        c += 0.0005
    return None


def rr_transform(nr_arr, outcome_arr, cost_r_arr, rr_ratio):
    is_tp = outcome_arr == "tp"
    if len(nr_arr) == 0:
        return np.array([], dtype=float)
    gross = nr_arr + cost_r_arr
    new_net = nr_arr.copy()
    new_net[is_tp] = gross[is_tp] * (rr_ratio / 2.0) - cost_r_arr[is_tp]
    return new_net


def load_and_process(raw_path, symbol):
    raw = load_ohlcv(raw_path)
    n = len(raw)
    print(f"  {symbol}: {n} raw rows")
    df = normalize_ohlcv(raw, volume_kind="tick", timezone="UTC", utc_offset_hours="auto")
    print(f"  {symbol}: {len(df)} normalized bars ({df.index.min()} → {df.index.max()})")
    df = add_atr(df, period=14)
    return df


def analyze_symbol(df, symbol):
    print(f"\n  --- {symbol} V2 Analysis ---")

    # 1. V2 sweep detection
    events = build_sweep_events_v2(
        df, min_penetration_atr=0.05, max_penetration_atr=MAX_PEN,
        min_wick_ratio=0.35, min_reclaim_atr=0.0,
        cooldown_bars=4, group_rule="first",
        v2_nguoc_trend=NGUOC_TREND, v2_target_r=TARGET_R,
    )
    n_events = len(events)
    print(f"  [1] V2 events: {n_events}")
    if events.empty:
        return {"symbol": symbol, "error": "No V2 events", "n_events_raw": 0}

    # 2. Confirmations
    cfg_conf = {
        "confirmation": {"enabled": True, "max_wait_bars": 3,
                         "require_break_sweep_extreme": True,
                         "min_body_ratio": 0.60, "min_range_atr": 0.80},
        "entry": {"mode": "next_open_after_confirmation"},
    }
    confirmed = attach_confirmations(df, events, cfg_conf)
    n_confirmed = int(confirmed["is_confirmed"].sum()) if not confirmed.empty else 0
    print(f"  [2] Confirmed: {n_confirmed}/{len(confirmed)}")
    if confirmed.empty or n_confirmed == 0:
        return {"symbol": symbol, "error": "No confirmed", "n_events_raw": n_events,
                "n_events_confirmed": 0}

    # 3. Labeling
    labeling_cfg = {
        "labeling": {"horizons": [16], "reward_r_values": [2.0],
                      "same_bar_policy": "ambiguous",
                      "time_barrier_result": "mark_to_market",
                      "primary_reward_r": 2.0, "primary_horizon": 16},
        "costs": {"half_spread_price": 0.0, "slippage_price": 0.0, "commission_r": 0.0},
        "stop": {"buffer_atr": 0.10},
        "entry": {"mode": "next_open_after_confirmation"},
        "indicators": {"atr_period": 14},
    }
    labeled = build_event_labels(df, confirmed, labeling_cfg)
    n_valid = len(labeled)
    n_invalid = labeled.attrs.get("labeling_summary", {}).get("n_invalid", 0)
    print(f"  [3] Valid: {n_valid}, Invalid: {n_invalid}")
    if labeled.empty:
        return {"symbol": symbol, "error": "No valid labeled",
                "n_events_raw": n_events, "n_events_confirmed": n_confirmed}

    # 4. Arrays
    nr = labeled["net_result_r"].to_numpy(float)
    outcome = labeled["outcome_2r_h16"].to_numpy(object)
    direction = labeled["direction"].to_numpy(object)
    cost_r = labeled["cost_r"].to_numpy(float)

    v = ~np.isnan(nr)
    nr_v = nr[v]; outcome_v = outcome[v]; cost_v = cost_r[v]; dir_v = direction[v]
    n_net = int(v.sum())
    print(f"  [4] Net valid: {n_net}")
    if n_net == 0:
        return {"symbol": symbol, "error": "No net-valid",
                "n_events_raw": n_events, "n_events_confirmed": n_confirmed, "n_valid": n_valid}

    n_long = int((dir_v == "long").sum())
    n_short = int((dir_v == "short").sum())

    # 5. Bar-level filter stack
    out = detect_sweeps_v2(df, atr_period=14, level_lookback=20,
                           min_penetration_atr=0.05, max_penetration_atr=MAX_PEN,
                           min_wick_ratio=0.35, min_reclaim_atr=0.0,
                           v2_nguoc_trend=NGUOC_TREND)
    bs = int((out["sweep_long"].fillna(False) | out["sweep_short"].fillna(False)).sum())
    v2s = int((out["sweep_v2_long"].fillna(False) | out["sweep_v2_short"].fillna(False)).sum())
    h1b = int(out["h1_trend"].notna().sum())

    # 6. Performance base 2R
    base_pre = sb(nr_v, SEED + 100)
    base_post = sb(nr_v - COST_BASE, SEED + 101)
    be_base = be(nr_v)

    # 7. R:R 3.0
    nr_rr = rr_transform(nr_v, outcome_v, cost_v, TARGET_R)
    nr_rr_post = nr_rr - COST_BASE
    rr_pre = sb(nr_rr, SEED + 200)
    rr_post = sb(nr_rr_post, SEED + 201)
    be_rr = be(nr_rr)

    n_tp = int((outcome_v == "tp").sum())
    n_sl = int((outcome_v == "sl").sum())
    n_time = int((outcome_v == "time").sum())

    result = {
        "symbol": symbol,
        "metadata": {"bars": len(df),
                      "date_range": f"{df.index.min()} → {df.index.max()}",
                      "v2_nguoc_trend": NGUOC_TREND,
                      "v2_max_penetration_atr": MAX_PEN,
                      "v2_target_r": TARGET_R, "cost_base_r": COST_BASE,
                      "entry_mode": "next_open_after_confirmation",
                      "group_rule": "first"},
        "filter_stack": {"h1_trend_bars_available": h1b,
                          "baseline_sweep_bars": bs,
                          "v2_nguoc_trend_bars": v2s,
                          "confirmed_events": n_confirmed,
                          "labeled_valid": n_valid,
                          "net_valid": n_net,
                          "direction_split": {"long": n_long, "short": n_short}},
        "outcome_breakdown_2R_h16": {"tp": n_tp, "sl": n_sl, "time_out": n_time,
                                      "ambiguous": n_net - n_tp - n_sl - n_time},
        "performance_base_2R_h16": {"n_confirmed": n_confirmed, "n_valid": n_valid,
                                     "n_net_valid": n_net,
                                     "pre_cost": base_pre,
                                     "post_cost_0.05R": base_post,
                                     "breakeven_cost": be_base},
        "performance_r3_h16_RR3": {"n_net": n_net,
                                    "pre_cost": rr_pre,
                                    "post_cost_0.05R": rr_post,
                                    "breakeven_cost": be_rr},
    }
    print(f"  Results: base PF={base_pre['profit_factor']}, "
          f"RR{TARGET_R}:1 PF post-cost={rr_post['profit_factor']}")
    return result


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out_json = os.path.join(OUT_DIR, "cross_asset_comparison.json")
    out_md = os.path.join(OUT_DIR, "cross_asset_comparison.md")

    print("=" * 60)
    print("  Loading XAUUSD...")
    xd = load_and_process(XAUUSD_RAW, "XAUUSD")
    print("  Loading EURUSD...")
    ed = load_and_process(EURUSD_RAW, "EURUSD")

    print("\n" + "=" * 60)
    xr = analyze_symbol(xd, "XAUUSD")
    er = analyze_symbol(ed, "EURUSD")

    # Write per-asset JSON+MD (task deliverables requirement)
    xauusd_json = os.path.join(OUT_DIR, "xauusd_full_analysis.json")
    xauusd_md = os.path.join(OUT_DIR, "xauusd_full_analysis.md")
    eurusd_json = os.path.join(OUT_DIR, "eurusd_analysis.json")
    eurusd_md = os.path.join(OUT_DIR, "eurusd_analysis.md")

    with open(xauusd_json, "w") as f:
        json.dump(xr, f, indent=2)
    print(f"Wrote: {xauusd_json}")
    with open(eurusd_json, "w") as f:
        json.dump(er, f, indent=2)
    print(f"Wrote: {eurusd_json}")

    # Per-asset MD summaries
    for r_, mdfile in [(xr, xauusd_md), (er, eurusd_md)]:
        r = r_; sym = r["symbol"]
        lines = [f"# {sym} V2 Analysis\n\n",
                 f"**Bars**: {r['metadata']['bars']:,} ({r['metadata']['date_range']})\n\n",
                 f"## Filter Stack\n\n",
                 f"| Layer | Count |\n|---|---|\n"]
        fs = r["filter_stack"]
        lines.append(f"| H1 trend bars | {fs['h1_trend_bars_available']} |\n")
        lines.append(f"| Baseline sweeps | {fs['baseline_sweep_bars']} |\n")
        lines.append(f"| V2 nguoc_trend | {fs['v2_nguoc_trend_bars']} |\n")
        lines.append(f"| Confirmed events | {fs['confirmed_events']} |\n")
        lines.append(f"| Labeled valid | {fs['labeled_valid']} |\n")
        lines.append(f"| Net valid | {fs['net_valid']} |\n\n")
        lines.append(f"## Performance R:R 3.0:1\n\n")
        pr3 = r["performance_r3_h16_RR3"]
        lines.append(f"- **PF pre-cost**: {pr3['pre_cost']['profit_factor']}\n")
        lines.append(f"- **PF @0.05R**: {pr3['post_cost_0.05R']['profit_factor']}\n")
        lines.append(f"- **Breakeven cost**: {pr3['breakeven_cost']}R\n")
        lines.append(f"- **Avg net R**: {pr3['pre_cost']['avg_net_r']}R\n")
        lines.append(f"- **Net positive rate**: {pr3['pre_cost']['net_positive_rate']}\n")
        with open(mdfile, "w") as f:
            f.write("".join(lines))
        print(f"Wrote: {mdfile}")

    # Build combined comparison
    comparison = {
        "generated": pd.Timestamp.now().isoformat(),
        "project_root": PROJECT,
        "assets": [xr, er],
        "comparison": {},
    }

    if len([r for r in [xr, er] if "error" not in r]) == 2:
        # Both succeeded — build comparison dict
        for key in ["metadata", "filter_stack", "outcome_breakdown_2R_h16",
                     "performance_base_2R_h16", "performance_r3_h16_RR3"]:
            comparison["comparison"][key] = {"XAUUSD": xr.get(key), "EURUSD": er.get(key)}

        # Additional derived comparisons
        xs = xr["filter_stack"]; es = er["filter_stack"]
        comparison["comparison"]["derived"] = {
            "baseline_sweep_rate_per_1000_bars": {
                "XAUUSD": _r(xs["baseline_sweep_bars"] / xr["metadata"]["bars"] * 1000),
                "EURUSD": _r(es["baseline_sweep_bars"] / er["metadata"]["bars"] * 1000),
            },
            "v2_nguoc_trend_rate_per_1000_bars": {
                "XAUUSD": _r(xs["v2_nguoc_trend_bars"] / xr["metadata"]["bars"] * 1000),
                "EURUSD": _r(es["v2_nguoc_trend_bars"] / er["metadata"]["bars"] * 1000),
            },
            "confirmed_rate_per_1000_bars": {
                "XAUUSD": _r(xs["confirmed_events"] / xr["metadata"]["bars"] * 1000),
                "EURUSD": _r(es["confirmed_events"] / er["metadata"]["bars"] * 1000),
            },
            "net_valid_rate_per_1000_bars": {
                "XAUUSD": _r(xs["net_valid"] / xr["metadata"]["bars"] * 1000),
                "EURUSD": _r(es["net_valid"] / er["metadata"]["bars"] * 1000),
            },
        }

        # Generalization verdict
        xpf = xr["performance_r3_h16_RR3"]["post_cost_0.05R"]["profit_factor"]
        epf = er["performance_r3_h16_RR3"]["post_cost_0.05R"]["profit_factor"]
        xbe = xr["performance_r3_h16_RR3"]["breakeven_cost"]
        ebe = er["performance_r3_h16_RR3"]["breakeven_cost"]
        xsample = xr["filter_stack"]["net_valid"]
        esample = er["filter_stack"]["net_valid"]

        verdict_parts = []
        if xpf is not None and xpf > 1.0:
            verdict_parts.append(f"XAUUSD V2 PF={xpf} > 1.0 @ {COST_BASE}R cost")
        else:
            verdict_parts.append(f"XAUUSD V2 PF={xpf} ≤ 1.0")
        if epf is not None and epf > 1.0:
            verdict_parts.append(f"EURUSD V2 PF={epf} > 1.0 @ {COST_BASE}R cost")
        else:
            verdict_parts.append(f"EURUSD V2 PF={epf} ≤ 1.0")
        if xbe is not None and ebe is not None:
            verdict_parts.append(f"Breakeven: XAUUSD {xbe}R, EURUSD {ebe}R")
        verdict_parts.append(f"Sample: XAUUSD n={xsample}, EURUSD n={esample}")
        if xpf is not None and epf is not None and xpf > 1.0 and epf > 1.0:
            verdict_parts.append("V2 generalizes across both assets (PF>1 on both)")
        else:
            verdict_parts.append("V2 does NOT fully generalize (PF≤1 on at least one asset)")

        comparison["comparison"]["verdict"] = "; ".join(verdict_parts)

    # Write JSON
    with open(out_json, "w") as f:
        json.dump(comparison, f, indent=2)
    print(f"\nWrote JSON: {out_json}")

    # Write Markdown
    md = []
    md.append("# Cross-Asset V2 Comparison Report\n\n")
    md.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n\n")
    md.append(f"## Dataset Overview\n\n")
    md.append(f"| Asset | Bars | Date Range |\n")
    md.append(f"|---|---|---|\n")
    md.append(f"| XAUUSD | {xr['metadata']['bars']:,} | {xr['metadata']['date_range']} |\n")
    md.append(f"| EURUSD | {er['metadata']['bars']:,} | {er['metadata']['date_range']} |\n\n")

    md.append("## V2 Pipeline Configuration\n\n")
    md.append(f"- **nguoc_trend**: {NGUOC_TREND}\n")
    md.append(f"- **max_penetration_atr**: {MAX_PEN}\n")
    md.append(f"- **Target R:R**: {TARGET_R}:1\n")
    md.append(f"- **Cost base**: {COST_BASE}R/trade\n")
    md.append(f"- **Entry mode**: next_open_after_confirmation\n")
    md.append(f"- **Group rule**: first\n\n")

    md.append("## Filter Stack Progression\n\n")
    for r_ in [xr, er]:
        fs = r_["filter_stack"]
        tot = r_["metadata"]["bars"]
        md.append(f"### {r_['symbol']}\n\n")
        md.append(f"| Layer | Count | Per 1000 bars |\n")
        md.append(f"|---|---|---|\n")
        md.append(f"| Baseline sweeps (bars) | {fs['baseline_sweep_bars']:,} | {_r(fs['baseline_sweep_bars']/tot*1000)} |\n")
        md.append(f"| + nguoc_trend filter | {fs['v2_nguoc_trend_bars']:,} | {_r(fs['v2_nguoc_trend_bars']/tot*1000)} |\n")
        md.append(f"| Confirmed events | {fs['confirmed_events']:,} | {_r(fs['confirmed_events']/tot*1000)} |\n")
        md.append(f"| Labeled valid | {fs['labeled_valid']:,} | {_r(fs['labeled_valid']/tot*1000)} |\n")
        md.append(f"| Net valid | {fs['net_valid']:,} | {_r(fs['net_valid']/tot*1000)} |\n\n")

    md.append("## Performance: Base 2R:16H (no R:R adjust)\n\n")
    md.append("| Metric | XAUUSD | EURUSD |\n")
    md.append("|---|---|---|\n")
    def row(label, xv, ev):
        return f"| {label} | {xv} | {ev} |\n"
    xb = xr["performance_base_2R_h16"]; eb = er["performance_base_2R_h16"]
    md.append(row("Net valid n", xb["n_net_valid"], eb["n_net_valid"]))
    md.append(row("PF pre-cost", xb["pre_cost"]["profit_factor"], eb["pre_cost"]["profit_factor"]))
    md.append(row("PF @0.05R cost", xb["post_cost_0.05R"]["profit_factor"], eb["post_cost_0.05R"]["profit_factor"]))
    md.append(row("Breakeven cost (R)", xb["breakeven_cost"], eb["breakeven_cost"]))
    md.append(row("Net positive rate", xb["pre_cost"]["net_positive_rate"], eb["pre_cost"]["net_positive_rate"]))
    xbci = xb["pre_cost"].get("pf_ci95")
    ebci = eb["pre_cost"].get("pf_ci95")
    md.append(row("PF 95% CI", f"[{xbci[0]}, {xbci[1]}]" if xbci else "N/A",
                   f"[{ebci[0]}, {ebci[1]}]" if ebci else "N/A"))
    md.append(row("Avg net R", xb["pre_cost"]["avg_net_r"], eb["pre_cost"]["avg_net_r"]))
    md.append("\n")

    md.append("## Performance: R:R 3.0:1 Transform\n\n")
    xrr = xr["performance_r3_h16_RR3"]; err_ = er["performance_r3_h16_RR3"]
    md.append("| Metric | XAUUSD | EURUSD |\n")
    md.append("|---|---|---|\n")
    md.append(row("Net valid n", xrr["n_net"], err_["n_net"]))
    md.append(row("PF pre-cost", xrr["pre_cost"]["profit_factor"], err_["pre_cost"]["profit_factor"]))
    md.append(row("PF @0.05R cost", xrr["post_cost_0.05R"]["profit_factor"], err_["post_cost_0.05R"]["profit_factor"]))
    md.append(row("Breakeven cost (R)", xrr["breakeven_cost"], err_["breakeven_cost"]))
    md.append(row("Avg net R (pre-cost)", xrr["pre_cost"]["avg_net_r"], err_["pre_cost"]["avg_net_r"]))
    md.append(row("Net positive rate", xrr["pre_cost"]["net_positive_rate"], err_["pre_cost"]["net_positive_rate"]))
    xrci = xrr["pre_cost"].get("pf_ci95")
    erci = err_["pre_cost"].get("pf_ci95")
    md.append(row("PF 95% CI", f"[{xrci[0]}, {xrci[1]}]" if xrci else "N/A",
                   f"[{erci[0]}, {erci[1]}]" if erci else "N/A"))
    md.append("\n")

    md.append("## Outcome Breakdown (2R:16H)\n\n")
    md.append("| Outcome | XAUUSD | EURUSD |\n")
    md.append("|---|---|---|\n")
    xo = xr["outcome_breakdown_2R_h16"]; eo = er["outcome_breakdown_2R_h16"]
    for lb in ["tp", "sl", "time_out", "ambiguous"]:
        md.append(row(lb, xo[lb], eo[lb]))
    md.append("\n")

    md.append("## Filter Stack Effectiveness (bars/events)\n\n")
    for r_ in [xr, er]:
        fs = r_["filter_stack"]
        tot = r_["metadata"]["bars"]
        md.append(f"### {r_['symbol']}\n\n")
        md.append(f"- **Total bars**: {tot:,}\n")
        md.append(f"- **Sweep bars (baseline)**: {fs['baseline_sweep_bars']:,} "
                  f"({_r(fs['baseline_sweep_bars']/tot*100)}% of all bars)\n")
        md.append(f"- **V2 nguoc_trend bars**: {fs['v2_nguoc_trend_bars']:,} "
                  f"({_r(fs['v2_nguoc_trend_bars']/tot*100)}%)\n")
        md.append(f"- **Confirmed events**: {fs['confirmed_events']:,}\n")
        md.append(f"- **Net valid trades**: {fs['net_valid']:,}\n")
        md.append(f"- **Reduction factor (bars→net valid)**: "
                  f"{_r(tot / fs['net_valid']) if fs['net_valid'] > 0 else 'N/A'}:1\n\n")

    md.append("## Generalization Verdict\n\n")
    if "verdict" in comparison["comparison"]:
        md.append(f"{comparison['comparison']['verdict']}\n\n")
    md.append("### Criteria\n\n")
    md.append("1. **PF > 1.0 @0.05R cost** on both assets\n")
    md.append("2. **Breakeven cost ≥ 0.05R** on both assets\n")
    md.append("3. **Sample size ≥ 10 trades** per asset\n")
    md.append("4. **Filter stack reduces event count** — indicating non-random selection\n")
    md.append("\n")

    # Final verdict
    passes = []
    fails = []
    for r_ in [xr, er]:
        pf_v = r_["performance_r3_h16_RR3"]["post_cost_0.05R"]["profit_factor"]
        be_v = r_["performance_r3_h16_RR3"]["breakeven_cost"]
        ns = r_["filter_stack"]["net_valid"]
        if pf_v is not None and pf_v > 1.0:
            passes.append(f"{r_['symbol']}: PF={pf_v} > 1.0 ✓")
        else:
            fails.append(f"{r_['symbol']}: PF={pf_v} ≤ 1.0 ✗")
        if be_v is not None and be_v >= COST_BASE:
            passes.append(f"{r_['symbol']}: breakeven {be_v}R >= {COST_BASE}R ✓")
        else:
            fails.append(f"{r_['symbol']}: breakeven {be_v}R < {COST_BASE}R ✗")
        if ns >= 10:
            passes.append(f"{r_['symbol']}: sample n={ns} >= 10 ✓")
        else:
            fails.append(f"{r_['symbol']}: sample n={ns} < 10 ✗")

    for r_ in [xr, er]:
        fs = r_["filter_stack"]
        if fs["net_valid"] > 0 and fs["net_valid"] < fs["baseline_sweep_bars"]:
            passes.append(f"{r_['symbol']}: filter stack reduces events ✓")
        else:
            fails.append(f"{r_['symbol']}: filter stack does not reduce ✗")

    md.append("### Pass/Fail Summary\n\n")
    for p in passes:
        md.append(f"- ✅ {p}\n")
    for f in fails:
        md.append(f"- ❌ {f}\n")
    md.append("\n")

    n_pass = len(passes); n_fail = len(fails)
    if n_fail == 0:
        md.append(f"### ✅ FINAL: V2 Generalizes Across Both Assets\n\n")
        md.append(f"All {n_pass}/{n_pass} criteria pass. The V2 pipeline "
                  f"(nguoc_trend + max_pen≤{MAX_PEN} + R:R {TARGET_R}:1) is effective "
                  f"on both XAUUSD and EURUSD.\n\n")
    elif n_pass >= n_fail:
        md.append(f"### ⚠️ PARTIAL: V2 Shows Promise but Limited\n\n")
        md.append(f"{n_pass}/{n_pass + n_fail} criteria pass. "
                  f"V2 works on one asset but may need per-asset tuning.\n\n")
    else:
        md.append(f"### ❌ NEGATIVE: V2 Does Not Generalize\n\n")
        md.append(f"Only {n_pass}/{n_pass + n_fail} criteria pass. "
                  f"V2 parameters are specific to XAUUSD.\n\n")

    md.append("---\n")
    md.append("*Analysis performed with deterministic seed=42, 3000 bootstrap resamples.*\n")

    with open(out_md, "w") as f:
        f.write("".join(md))
    print(f"Wrote MD: {out_md}")

    summary = comparison["comparison"].get("verdict", "See full report")
    print(f"\n=== VERDICT ===\n{summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())