#!/usr/bin/env python3
"""Tách XAUUSD Full theo 2 giai đoạn:
   - 2018 → 2022-06 (OOS thật — chưa từng khám phá)
   - 2022-06 → 2026 (đã dùng để khám phá nguoc_trend, max_pen, R:R)
   Tính PF, CI, breakeven cho từng giai đoạn riêng với V2 pipeline.

Usage:
    cd xauusd-liquidity-sweep/
    python pipeline_v2/scripts/analysis/phase_split_analysis.py
"""

from __future__ import annotations

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
SPLIT_DATE = "2022-06-01"  # Phase 1 frozen dataset starts 2022-06
XAUUSD_RAW = os.path.join(PROJECT, "data", "raw", "XAUUSD_M15_201801020900_202609032245.csv")
OUT_DIR = os.path.join(PROJECT, "pipeline_v2", "reports", "analysis", "cross_asset")


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


def analyze_period(df, label):
    """Run V2 pipeline on df subset and return metrics."""
    events = build_sweep_events_v2(
        df, min_penetration_atr=0.05, max_penetration_atr=MAX_PEN,
        min_wick_ratio=0.35, min_reclaim_atr=0.0,
        cooldown_bars=4, group_rule="first",
        v2_nguoc_trend=NGUOC_TREND, v2_target_r=TARGET_R,
    )
    n_events = len(events)
    print(f"  [{label}] V2 events: {n_events}")
    if events.empty:
        return {"label": label, "error": "No V2 events", "n_bars": len(df),
                "n_events_raw": 0}

    cfg_conf = {
        "confirmation": {"enabled": True, "max_wait_bars": 3,
                         "require_break_sweep_extreme": True,
                         "min_body_ratio": 0.60, "min_range_atr": 0.80},
        "entry": {"mode": "next_open_after_confirmation"},
    }
    confirmed = attach_confirmations(df, events, cfg_conf)
    n_confirmed = int(confirmed["is_confirmed"].sum()) if not confirmed.empty else 0
    print(f"  [{label}] Confirmed: {n_confirmed}/{len(confirmed)}")
    if confirmed.empty or n_confirmed == 0:
        return {"label": label, "error": "No confirmed",
                "n_bars": len(df), "n_events_raw": n_events,
                "events_per_1k_bars": _r(n_events / len(df) * 1000)}

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
    print(f"  [{label}] Valid: {n_valid}")
    if labeled.empty:
        return {"label": label, "error": "No valid labeled",
                "n_bars": len(df), "n_events_raw": n_events,
                "n_confirmed": n_confirmed}

    nr = labeled["net_result_r"].to_numpy(float)
    outcome = labeled["outcome_2r_h16"].to_numpy(object)
    direction = labeled["direction"].to_numpy(object)
    cost_r = labeled["cost_r"].to_numpy(float)

    v = ~np.isnan(nr)
    nr_v = nr[v]; outcome_v = outcome[v]; cost_v = cost_r[v]; dir_v = direction[v]
    n_net = int(v.sum())
    print(f"  [{label}] Net valid: {n_net}")
    if n_net == 0:
        return {"label": label, "error": "No net-valid",
                "n_bars": len(df), "n_events_raw": n_events,
                "n_confirmed": n_confirmed, "n_valid": n_valid}

    n_long = int((dir_v == "long").sum())
    n_short = int((dir_v == "short").sum())
    n_tp = int((outcome_v == "tp").sum())
    n_sl = int((outcome_v == "sl").sum())
    n_time = int((outcome_v == "time").sum())

    # Base 2R
    base_pre = sb(nr_v, SEED + 100)
    base_post = sb(nr_v - COST_BASE, SEED + 101)
    be_base = be(nr_v)

    # R:R 3.0
    nr_rr = rr_transform(nr_v, outcome_v, cost_v, TARGET_R)
    nr_rr_post = nr_rr - COST_BASE
    rr_pre = sb(nr_rr, SEED + 200)
    rr_post = sb(nr_rr_post, SEED + 201)
    be_rr = be(nr_rr)

    return {
        "label": label,
        "n_bars": len(df),
        "date_range": f"{df.index.min()} → {df.index.max()}",
        "n_events_raw": n_events,
        "n_confirmed": n_confirmed,
        "n_valid": n_valid,
        "n_net": n_net,
        "events_per_1k_bars": _r(n_events / len(df) * 1000),
        "direction_split": {"long": n_long, "short": n_short},
        "outcome_breakdown": {"tp": n_tp, "sl": n_sl, "time_out": n_time},
        "base_2R_h16": {
            "PF_pre_cost": base_pre["profit_factor"],
            "PF_post_0.05R": base_post["profit_factor"],
            "PF_CI95_pre": base_pre["pf_ci95"],
            "PF_CI95_post": base_post["pf_ci95"],
            "breakeven_cost": be_base,
            "avg_net_r": base_pre["avg_net_r"],
            "net_positive_rate": base_pre["net_positive_rate"],
        },
        "RR3_h16": {
            "PF_pre_cost": rr_pre["profit_factor"],
            "PF_post_0.05R": rr_post["profit_factor"],
            "PF_CI95_pre": rr_pre["pf_ci95"],
            "PF_CI95_post": rr_post["pf_ci95"],
            "breakeven_cost": be_rr,
            "avg_net_r": rr_pre["avg_net_r"],
            "net_positive_rate": rr_pre["net_positive_rate"],
        },
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 60)
    print("Loading XAUUSD raw data...")
    raw = load_ohlcv(XAUUSD_RAW)
    print(f"  Raw rows: {len(raw)}")
    df = normalize_ohlcv(raw, volume_kind="tick", timezone="UTC", utc_offset_hours="auto")
    print(f"  Normalized bars: {len(df)} ({df.index.min()} → {df.index.max()})")
    df = add_atr(df, period=14)

    # Split
    split_ts = pd.Timestamp(SPLIT_DATE, tz=df.index.tz)
    df_before = df[df.index < split_ts].copy()
    df_after = df[df.index >= split_ts].copy()
    print(f"\n  Before {SPLIT_DATE}: {len(df_before)} bars")
    print(f"  After  {SPLIT_DATE}: {len(df_after)} bars")

    print("\n" + "=" * 60)
    print("Analyzing Period 1: 2018 → 2022-06 (OOS — pure unseen)")
    r1 = analyze_period(df_before, "2018_to_202206")

    print("\n" + "=" * 60)
    print("Analyzing Period 2: 2022-06 → 2026 (discovery period)")
    r2 = analyze_period(df_after, "202206_to_2026")

    # Load full run results from existing JSON for comparison
    full_json_path = os.path.join(OUT_DIR, "xauusd_full_analysis.json")
    full = None
    if os.path.exists(full_json_path):
        with open(full_json_path) as f:
            full = json.load(f)

    # Build output
    result = {
        "analysis": "XAUUSD time-split: OOS vs discovery period",
        "split_date": SPLIT_DATE,
        "periods": {
            "2018_to_202206_OOS": r1,
            "202206_to_2026_discovery": r2,
            "full_2018_to_2026": {
                "label": "Full 2018→2026",
                "n_bars": len(df),
                "source": full_json_path if full else "Not found — run cross_asset_analysis.py first",
            },
        },
        "full_run_comparison": full.get("performance_r3_h16_RR3", None) if full else None,
    }

    # Write JSON
    out_json = os.path.join(OUT_DIR, "xauusd_phase_split.json")
    with open(out_json, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nWrote: {out_json}")

    # Generate summary
    print("\n" + "=" * 60)
    print("PHASE SPLIT SUMMARY (V2 R:R 3.0:1 @0.05R)")
    print("=" * 60)

    periods = [
        ("OOS: 2018→2022-06", r1),
        ("Discovery: 2022-06→2026", r2),
    ]
    for label, r in periods:
        if "error" in r:
            print(f"\n{label}: ERROR — {r['error']}")
            continue
        rr = r["RR3_h16"]
        print(f"\n{label}")
        print(f"  Bars: {r['n_bars']:,}")
        print(f"  Net valid trades: {r['n_net']}")
        print(f"  PF pre-cost @3R: {rr['PF_pre_cost']}   CI95: {rr['PF_CI95_pre']}")
        print(f"  PF @0.05R:       {rr['PF_post_0.05R']}   CI95: {rr['PF_CI95_post']}")
        print(f"  Breakeven cost:  {rr['breakeven_cost']}R")
        print(f"  Avg net R:       {rr['avg_net_r']}")
        print(f"  Net positive rate: {rr['net_positive_rate']}")
        print(f"  Events/1k bars:  {r['events_per_1k_bars']}")

    if full:
        f3 = full["performance_r3_h16_RR3"]
        print(f"\nFull (baseline comparison):")
        print(f"  PF @0.05R: {f3['post_cost_0.05R']['profit_factor']}   "
              f"CI95: {f3['post_cost_0.05R']['pf_ci95']}")
        print(f"  Breakeven: {f3['breakeven_cost']}R")

    print("\n" + "=" * 60)
    print("Done. Full results at:")
    print(f"  {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())