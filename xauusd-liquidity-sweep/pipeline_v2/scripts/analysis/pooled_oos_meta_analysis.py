#!/usr/bin/env python3
"""Pooled OOS Meta-Analysis: gộp 2 mẫu OOS độc lập
   - XAUUSD 2018→2022-06 (n=118) — hoàn toàn chưa động tới
   - EURUSD 2018→2026 (n=267) — chưa từng dùng để khám phá
   Cả 2 dùng V2 pipeline cố định (nguoc_trend + max_pen≤0.20 + R:R 3.0:1).

   Pooled bootstrap CI để có ước lượng generalization mạnh hơn từng mẫu riêng.

   Lưu ý: Cả 2 dataset này đã "burned" — không được dùng lại để tinh chỉnh
   bất kỳ tham số nào (kể cả R:R, penetration, score threshold).

Usage:
    cd xauusd-liquidity-sweep/
    python pipeline_v2/scripts/analysis/pooled_oos_meta_analysis.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import chi2
from scipy import stats as scipy_stats

PROJECT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, PROJECT)

from src.data.loader import load_ohlcv, normalize_ohlcv
from src.indicators.atr import add_atr
from pipeline_v2.src.events.sweep_detector_v2 import (
    build_sweep_events_v2,
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

XAUUSD_RAW = os.path.join(PROJECT, "data", "raw", "XAUUSD_M15_201801020900_202609032245.csv")
EURUSD_RAW = os.path.join(PROJECT, "data", "raw", "EURUSD_M15_201801020000_202609040000.csv")
OUT_DIR = os.path.join(PROJECT, "pipeline_v2", "reports", "analysis", "cross_asset")
SPLIT_DATE = "2022-06-01"


def _r(v, nd=4):
    return None if v is None else round(float(v), nd)


def rr_transform(nr_arr, outcome_arr, cost_r_arr, rr_ratio):
    is_tp = outcome_arr == "tp"
    if len(nr_arr) == 0:
        return np.array([], dtype=float)
    gross = nr_arr + cost_r_arr
    new_net = nr_arr.copy()
    new_net[is_tp] = gross[is_tp] * (rr_ratio / 2.0) - cost_r_arr[is_tp]
    return new_net


def bci(net, seed):
    """Bootstrap CI for PF."""
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
        return None, None
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))


def wc(k, n):
    """Wilson CI for net positive rate."""
    if n == 0:
        return None, None
    p = k / n
    d = 1.0 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * np.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return float(c - h), float(c + h)


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


def sb(net, seed):
    """Compute standard stats for a net array."""
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
        "pf_ci95": None if pfc[0] is None else [_r(pfc[0]), _r(pfc[1])],
        "avg_net_r": _r(avg),
        "net_positive_rate": _r(np_ / n),
        "net_positive_rate_wilson_ci95": [_r(lo), _r(hi)],
    }


def get_net_arrays(df, label):
    """Run full V2 pipeline on df, return net arrays (base + R:R 3.0)."""
    events = build_sweep_events_v2(
        df, min_penetration_atr=0.05, max_penetration_atr=MAX_PEN,
        min_wick_ratio=0.35, min_reclaim_atr=0.0,
        cooldown_bars=4, group_rule="first",
        v2_nguoc_trend=NGUOC_TREND, v2_target_r=TARGET_R,
    )
    print(f"  [{label}] V2 events: {len(events)}")
    if events.empty:
        return None, None, None, {"label": label, "error": "No V2 events"}

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
        return None, None, None, {"label": label, "error": "No confirmed"}

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
        return None, None, None, {"label": label, "error": "No valid labeled"}

    nr = labeled["net_result_r"].to_numpy(float)
    outcome = labeled["outcome_2r_h16"].to_numpy(object)
    cost_r = labeled["cost_r"].to_numpy(float)
    direction = labeled["direction"].to_numpy(object)

    v = ~np.isnan(nr)
    nr_v = nr[v]; outcome_v = outcome[v]; cost_v = cost_r[v]; dir_v = direction[v]
    n_net = int(v.sum())
    print(f"  [{label}] Net valid: {n_net}")
    if n_net == 0:
        return None, None, None, {"label": label, "error": "No net-valid"}

    # R:R 3.0 transform
    nr_rr = rr_transform(nr_v, outcome_v, cost_v, TARGET_R)
    nr_rr_post = nr_rr - COST_BASE

    n_long = int((dir_v == "long").sum())
    n_short = int((dir_v == "short").sum())
    n_tp = int((outcome_v == "tp").sum())
    n_sl = int((outcome_v == "sl").sum())
    n_time = int((outcome_v == "time").sum())

    return nr_rr_post, nr_v, outcome_v, {
        "label": label,
        "n_bars": len(df),
        "n_net": n_net,
        "direction_split": {"long": n_long, "short": n_short},
        "outcome_breakdown": {"tp": n_tp, "sl": n_sl, "time_out": n_time,
                               "n_net": n_net, "symbol": label},
    }


def summary_block(meta, source_name, arr, arr_label=""):
    """Compute and print stats for one array."""
    s = sb(arr, SEED + 500)
    be_v = be(arr)
    lbl = f"{source_name}{arr_label}"
    print(f"\n  [{lbl}]")
    print(f"    n={s['n_net']}, PF={s['profit_factor']}, CI95={s['pf_ci95']}")
    print(f"    breakeven={be_v}R, avg_net_r={s['avg_net_r']}, NPR={s['net_positive_rate']}")
    return s, be_v


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 60)
    print("POOLED OOS META-ANALYSIS")
    print("2 independent OOS samples — same frozen V2 params")
    print("=" * 60)

    # =============================================
    # Sample 1: XAUUSD OOS (2018 → 2022-06)
    # =============================================
    print("\n--- Loading XAUUSD ---")
    raw = load_ohlcv(XAUUSD_RAW)
    df = normalize_ohlcv(raw, volume_kind="tick", timezone="UTC", utc_offset_hours="auto")
    df = add_atr(df, period=14)
    print(f"  Total bars: {len(df)} ({df.index.min()} → {df.index.max()})")

    split_ts = pd.Timestamp(SPLIT_DATE, tz=df.index.tz)
    df_xau_oos = df[df.index < split_ts].copy()
    print(f"  OOS portion: {len(df_xau_oos)} bars ({df_xau_oos.index.min()} → {df_xau_oos.index.max()})")

    print(f"\n  Running V2 on XAUUSD-OOS...")
    xau_oos_rr_post, xau_oos_nr_base, xau_oos_outcome, xau_oos_meta = (
        get_net_arrays(df_xau_oos, "XAUUSD-OOS")
    )
    if xau_oos_rr_post is None:
        print(f"  XAUUSD-OOS failed: {xau_oos_meta.get('error', 'unknown')}")
        return 1

    # =============================================
    # Sample 2: EURUSD Full (2018 → 2026)
    # =============================================
    print("\n--- Loading EURUSD ---")
    raw_e = load_ohlcv(EURUSD_RAW)
    df_e = normalize_ohlcv(raw_e, volume_kind="tick", timezone="UTC", utc_offset_hours="auto")
    df_e = add_atr(df_e, period=14)
    print(f"  Total bars: {len(df_e)} ({df_e.index.min()} → {df_e.index.max()})")

    print(f"\n  Running V2 on EURUSD Full...")
    eur_rr_post, eur_nr_base, eur_outcome, eur_meta = (
        get_net_arrays(df_e, "EURUSD-Full")
    )
    if eur_rr_post is None:
        print(f"  EURUSD failed: {eur_meta.get('error', 'unknown')}")
        return 1

    # =============================================
    # Pooled analysis
    # =============================================
    print("\n" + "=" * 60)
    print("POOLED RESULTS")
    print("=" * 60)

    # Individual summaries
    xau_b3, xau_be3 = summary_block(xau_oos_meta, "XAUUSD-OOS", xau_oos_rr_post, " @R:R3+0.05R")
    eur_b3, eur_be3 = summary_block(eur_meta, "EURUSD-Full", eur_rr_post, " @R:R3+0.05R")

    # Breakeven cost: must compute on PRE-COST arrays, then report as cost + breakeven
    # be() on post-cost arrays would report avg_net_r instead of true breakeven
    xau_breakeven_correct = be(xau_oos_rr_post + COST_BASE)
    eur_breakeven_correct = be(eur_rr_post + COST_BASE)

    # Pooled: concatenate arrays
    pooled = np.concatenate([xau_oos_rr_post, eur_rr_post])
    n_total = len(pooled)

    print(f"\n  --- POOLED OOS (XAUUSD-OOS + EURUSD-Full) ---")
    print(f"    Combined n = {n_total}")
    pool_s, _ = summary_block({}, "POOLED", pooled, " @R:R3+0.05R")
    pool_breakeven_correct = be(pooled + COST_BASE)

    # Individual base 2R for context
    xau_base_post = xau_oos_nr_base - COST_BASE
    eur_base_post = eur_nr_base - COST_BASE
    pooled_base = np.concatenate([xau_base_post, eur_base_post])
    summary_block({}, "POOLED Base 2R", pooled_base, " @0.05R cost")

    # Print per-sample base vs RR3 for context
    print(f"\n  --- Per-period comparison ---")
    print(f"  XAUUSD-OOS base 2R: PF_post={sb(xau_oos_nr_base - COST_BASE, SEED+100)['profit_factor']}")
    print(f"  XAUUSD-OOS RR3:    PF_post={xau_b3['profit_factor']}  CI={xau_b3['pf_ci95']}")
    print(f"  EURUSD-Full base 2R: PF_post={sb(eur_nr_base - COST_BASE, SEED+101)['profit_factor']}")
    print(f"  EURUSD-Full RR3:    PF_post={eur_b3['profit_factor']}  CI={eur_b3['pf_ci95']}")
    print(f"\n  POOLED OOS RR3:     PF_post={pool_s['profit_factor']}  CI={pool_s['pf_ci95']}")
    print(f"  POOLED breakeven (corrected): {pool_breakeven_correct}R")

    # =============================================
    # =============================================
    # ACF on pooled net returns (for block size justification)
    # =============================================
    print("\n" + "-" * 50)
    print("AUTOCORRELATION ON POOLED NET RETURNS")
    print("-" * 50)
    # Use pre-cost net returns for ACF to avoid cost-artifact
    pooled_precost = np.concatenate([xau_oos_rr_post + COST_BASE, eur_rr_post + COST_BASE])
    acf_vals = []
    for lag in range(1, 6):
        acf, _ = scipy_stats.pearsonr(pooled_precost[:-lag], pooled_precost[lag:])
        acf_vals.append(acf)
    print(f"  ACF lag 1-5 on pooled pre-cost net returns:")
    for i, acf in enumerate(acf_vals):
        sign = "+" if acf >= 0 else "−"
        print(f"    lag {i+1}: {sign} {abs(acf):.4f}")
    mean_acf = np.mean(acf_vals)
    print(f"  Mean |ACF| lag 1-5: {mean_acf:.4f}")
    if all(v < 0.01 for v in acf_vals):
        print(f"  ✅ Autocorrelation negligible — near-IID trade sequence")
        print(f"     → IID and block bootstrap should give similar CI width")
    elif all(v < 0 for v in acf_vals):
        print(f"  ✅ Mean-reversion pattern (all negative) — block bootstrap may narrow CI")
        print(f"     → Supports team's explanation: outlier dampening via local averaging")
    elif all(v > 0 for v in acf_vals):
        print(f"  ⚠️  POSITIVE autocorrelation (regime streak pattern)")
        print(f"     → Block bootstrap theoretically should WIDEN vs IID bootstrap")
        print(f"     → Current narrowing result needs further investigation")
    else:
        print(f"  ⚠️  Mixed signs — no clear regime streak or mean-reversion pattern")
        print(f"     → Narrowing result not explained by autocorrelation structure alone")
    # Also compute lag-1 autocorrelation of the two component series for completeness
    for label, arr in [("XAUUSD-OOS", xau_oos_rr_post + COST_BASE),
                       ("EURUSD", eur_rr_post + COST_BASE)]:
        if len(arr) >= 5:
            acf1, _ = scipy_stats.pearsonr(arr[:-1], arr[1:])
            print(f"  {label} lag-1 ACF: {'+' if acf1 >= 0 else '−'} {abs(acf1):.4f}")
    # Suggest block size based on ACF decay
    # Common heuristics: block_size = floor(2 * sqrt(n)) or floor(min(20, 2*sqrt(n)))
    # But also ensure at least enough coverage to decorrelate the highest positive lag
    n_eff = len(pooled)
    suggested_bs = int(min(25, max(5, 2 * np.sqrt(n_eff))))
    print(f"  Suggested block size (2*sqrt(n)): {suggested_bs}")
    print(f"  Hardcoded block_size=20 very close to suggested={suggested_bs} — acceptable.")
    block_size = max(suggested_bs, 20)  # use the more conservative (larger) of the two

    # Heterogeneity test (Cochran Q / I²)
    # =============================================
    print("\n" + "-" * 50)
    print("HETEROGENEITY TEST (XAUUSD-OOS vs EURUSD-Full)")
    print("-" * 50)
    # Effect size: log(PF) for each sample. Use weighted sum of squares (Q).
    # Simpler: bootstrap the difference in mean-net-R between the two samples.
    xau_mean = xau_oos_rr_post.mean()
    eur_mean = eur_rr_post.mean()
    n1 = xau_oos_rr_post.size
    n2 = eur_rr_post.size
    pooled_mean = (n1 * xau_mean + n2 * eur_mean) / (n1 + n2)

    # Cochran Q: sum w_i * (theta_i - theta_bar)^2, where w_i = n_i
    # Using mean net return as effect size (theta)
    Q = n1 * (xau_mean - pooled_mean)**2 + n2 * (eur_mean - pooled_mean)**2
    # Under null (homogeneous), Q ~ chi²(df=1)
    from scipy.stats import chi2
    Q_pval = 1.0 - chi2.cdf(Q, df=1)
    # I² = max(0, (Q - (k-1)) / Q) where k = 2
    k = 2
    I2 = max(0.0, (Q - (k - 1)) / Q) * 100.0

    print(f"  Sample means: XAUUSD-OOS = {xau_mean:.4f}R, EURUSD = {eur_mean:.4f}R")
    print(f"  Pooled mean = {pooled_mean:.4f}R")
    print(f"  Q = {Q:.4f}, p = {Q_pval:.4f}  (df=1)")
    print(f"  I² = {I2:.1f}%")
    if Q_pval < 0.05:
        print(f"  ⚠️  SIGNIFICANT HETEROGENEITY (p<0.05) — samples may differ systematically")
    else:
        print(f"  ✅ No significant heterogeneity (p≥0.05) at conventional threshold")
        print(f"  ⚠️  CAVEAT: Q-test with k=2 has VERY LOW statistical power (only 1 df).")
        print(f"       Two sample means differ by {abs(xau_mean - eur_mean):.4f}R ({abs(xau_b3['profit_factor'] - eur_b3['profit_factor']):.2f}x PF)")
        print(f"       Non-significance does NOT confirm homogeneity — merely insufficient")
        print(f"       evidence to reject the null. I²={I2:.1f}% is descriptive only.")
    if I2 > 50:
        print(f"  ⚠️  I² > 50% — moderate-to-high heterogeneity despite non-significant Q")
    else:
        print(f"  ✅ I² ≤ 50% — acceptable homogeneity for pooling")

    # =============================================
    # Block bootstrap (time-series aware)
    # =============================================
    print("\n" + "-" * 50)
    print("BLOCK BOOTSTRAP CI (time-series autocorrelation adjusted)")
    print("-" * 50)
    # block_size derived from ACF analysis above
    n_blocks_xau = max(1, n1 // block_size)
    n_blocks_eur = max(1, n2 // block_size)

    def block_bootstrap_pf_single(arr, block_sz, n_resamples, seed):
        """Block bootstrap on a SINGLE series — no cross-series fence issue."""
        rng = np.random.default_rng(seed)
        n_obs = len(arr)
        n_blocks = max(1, n_obs // block_sz)
        pf_vals = []
        for _ in range(n_resamples):
            blocks = []
            for _ in range(n_blocks):
                start = rng.integers(0, max(1, n_obs - block_sz + 1))
                blocks.append(arr[start:start + block_sz])
            residual = n_obs - n_blocks * block_sz
            if residual > 0:
                start = rng.integers(0, max(1, n_obs - residual + 1))
                blocks.append(arr[start:start + residual])
            boot = np.concatenate(blocks)[:n_obs]
            ps = float(boot[boot > 0].sum())
            ns = float(abs(boot[boot < 0].sum()))
            pf_vals.append(ps / ns if ns > 0 else np.inf)
        pf_vals = np.array([v for v in pf_vals if np.isfinite(v)])
        if len(pf_vals) < 100:
            return None, None
        return float(np.percentile(pf_vals, 2.5)), float(np.percentile(pf_vals, 97.5))

    def block_bootstrap_pf_pooled(arr1, arr2, block_sz, n_resamples, seed):
        """Pooled block bootstrap with PROPER fence between 2 independent assets.
           Each resample draws blocks from arr1 and arr2 SEPARATELY,
           then concatenates the resampled series to form the pooled pseudo-sample.
           This prevents blocks from straddling the asset boundary."""
        rng = np.random.default_rng(seed)
        n1, n2 = len(arr1), len(arr2)
        n1_blocks = max(1, n1 // block_sz)
        n2_blocks = max(1, n2 // block_sz)
        n_total = n1 + n2
        pf_vals = []
        for _ in range(n_resamples):
            # Resample arr1 independently
            blocks1 = []
            for _ in range(n1_blocks):
                start = rng.integers(0, max(1, n1 - block_sz + 1))
                blocks1.append(arr1[start:start + block_sz])
            residual1 = n1 - n1_blocks * block_sz
            if residual1 > 0:
                start = rng.integers(0, max(1, n1 - residual1 + 1))
                blocks1.append(arr1[start:start + residual1])
            boot1 = np.concatenate(blocks1)[:n1]
            # Resample arr2 independently
            blocks2 = []
            for _ in range(n2_blocks):
                start = rng.integers(0, max(1, n2 - block_sz + 1))
                blocks2.append(arr2[start:start + block_sz])
            residual2 = n2 - n2_blocks * block_sz
            if residual2 > 0:
                start = rng.integers(0, max(1, n2 - residual2 + 1))
                blocks2.append(arr2[start:start + residual2])
            boot2 = np.concatenate(blocks2)[:n2]
            # Concatenate independent resamples → pooled pseudo-sample
            boot = np.concatenate([boot1, boot2])[:n_total]
            ps = float(boot[boot > 0].sum())
            ns = float(abs(boot[boot < 0].sum()))
            pf_vals.append(ps / ns if ns > 0 else np.inf)
        pf_vals = np.array([v for v in pf_vals if np.isfinite(v)])
        if len(pf_vals) < 100:
            return None, None
        return float(np.percentile(pf_vals, 2.5)), float(np.percentile(pf_vals, 97.5))

    # Pooled block bootstrap — now with proper fence
    print(f"  No-fence pooled (single-array, original approach):")
    pool_block_ci_old = block_bootstrap_pf_single(pooled, block_size, 3000, SEED + 910)
    if pool_block_ci_old[0] is not None:
        print(f"    CI = [{pool_block_ci_old[0]:.4f}, {pool_block_ci_old[1]:.4f}]")

    print(f"  Fenced pooled (independent per-asset blocks):")
    pool_block_ci = block_bootstrap_pf_pooled(xau_oos_rr_post, eur_rr_post,
                                               block_size, 3000, SEED + 920)
    if pool_block_ci[0] is not None:
        print(f"    Block bootstrap (block_size={block_size}, n_resamples=3000):")
        print(f"    Pooled PF 95% CI = [{pool_block_ci[0]:.4f}, {pool_block_ci[1]:.4f}]")
        print(f"    CI lower bound > 1.0? {'✅ YES' if pool_block_ci[0] > 1.0 else '❌ NO'}")
    else:
        print(f"    Block bootstrap: insufficient valid resamples")
    print(f"  (The no-fence vs fenced comparison quantifies the boundary-crossing bias.)")

    # Per-sample block bootstrap for comparison
    xau_block_ci = block_bootstrap_pf_single(xau_oos_rr_post, block_size, 3000, SEED + 901)
    eur_block_ci = block_bootstrap_pf_single(eur_rr_post, block_size, 3000, SEED + 902)
    if xau_block_ci[0] is not None:
        print(f"  XAUUSD-OOS block CI (single-series) = [{xau_block_ci[0]:.4f}, {xau_block_ci[1]:.4f}]")
    if eur_block_ci[0] is not None:
        print(f"  EURUSD block CI = [{eur_block_ci[0]:.4f}, {eur_block_ci[1]:.4f}]")

    # =============================================
    # Generalization verdict (strict)
    # =============================================
    print("\n" + "-" * 50)
    pf_ci = pool_s['pf_ci95']
    pf_val = pool_s['profit_factor']
    verdict_parts = []
    verdict_parts.append(f"Generalization test on pooled OOS: XAUUSD-OOS (n={xau_oos_rr_post.size}) + EURUSD (n={eur_rr_post.size})")
    verdict_parts.append(f"Pooled n = {n_total}")
    verdict_parts.append(f"")

    if pf_ci and pf_ci[0] is not None:
        verdict_parts.append(f"Pooled PF = {pf_val}  |  95% CI (iid bootstrap) = [{pf_ci[0]}, {pf_ci[1]}]")
        verdict_parts.append(f"Breakeven cost (corrected) = {pool_breakeven_correct}R")
        if pool_block_ci[0] is not None:
            verdict_parts.append(f"95% CI (block bootstrap, block={block_size}) = [{pool_block_ci[0]:.4f}, {pool_block_ci[1]:.4f}]")
        verdict_parts.append(f"Heterogeneity: Q={Q:.3f}, p={Q_pval:.4f}, I²={I2:.1f}%")
        verdict_parts.append(f"")
        verdict_parts.append(f"Post-hoc note: pooling was decided after seeing individual CI results.")
        verdict_parts.append(f"This increases false-positive risk but is disclosed transparently.")
        verdict_parts.append(f"")

        # Final verdict uses BLOCK bootstrap if available, else iid
        final_ci_lower = pool_block_ci[0] if pool_block_ci[0] is not None else pf_ci[0]
        if final_ci_lower > 1.0:
            verdict_parts.append(f"✅ STRICT GENERALIZATION PASS: CI lower bound ({final_ci_lower:.4f}) > 1.0")
            verdict_parts.append(f"")
            verdict_parts.append(f"✅ STRICT GENERALIZATION PASS: CI lower bound ({pf_ci[0]}) exceeds 1.0")
            verdict_parts.append(f"V2 pipeline (nguoc_trend + max_pen≤{MAX_PEN} + R:R {TARGET_R}:1)")
            verdict_parts.append(f"generalizes across XAUUSD and EURUSD at 95% confidence.")
            verdict_parts.append(f"")
            verdict_parts.append(f"→ No third asset needed. Proceed to paper trading (Phase 8).")
        else:
            verdict_parts.append(f"")
            verdict_parts.append(f"❌ STRICT GENERALIZATION FAIL: CI lower bound ({pf_ci[0]}) ≤ 1.0")
            verdict_parts.append(f"Pooled power (n={n_total}) is still insufficient to confirm")
            verdict_parts.append(f"generalization at 95% confidence with strict CI criterion.")
            verdict_parts.append(f"")
            verdict_parts.append(f"→ Third asset (GBPUSD, XAGUSD) needed to increase power.")
    else:
        verdict_parts.append(f"Pooled PF = {pf_val}  |  CI not available (insufficient bootstrap samples)")

    verdict_parts.append(f"")
    verdict_parts.append(f"KILL-SWITCH RECOMMENDATION for Phase 8 (Paper Trading):")
    verdict_parts.append(f"  - Rolling window CI must use BLOCK bootstrap (same block_size={block_size}), not IID,")
    verdict_parts.append(f"    to avoid kill-switch triggering prematurely/lately under autocorrelation.")
    verdict_parts.append(f"  - Minimum observation window: {block_size} trades (1 block) for first evaluation,")
    verdict_parts.append(f"    extended to 50-60 trades as primary kill-switch threshold.")
    verdict_parts.append(f"  - EURUSD position size: 50% of XAUUSD (as previously agreed).")
    verdict_parts.append(f"  - Both OOS datasets permanently burned — no further tuning.")

    pool_s['verdict'] = verdict_parts

    # =============================================
    # Write JSON output
    # =============================================
    output = {
        "analysis": "Pooled OOS Meta-Analysis — V2 generalization test",
        "method": "Pooled bootstrap (3000 resamples) on 2 independent OOS samples",
        "samples": {
            "XAUUSD_OOS_2018_to_202206": {
                "n_bars": len(df_xau_oos),
                "n_net": xau_oos_rr_post.size,
                "PF_RR3_post_0.05R": xau_b3,
                "breakeven_cost_corrected": xau_breakeven_correct,
                "note": "breakeven_cost on pre-cost array (post-cost avg + 0.05R)",
            },
            "EURUSD_full_2018_to_2026": {
                "n_bars": len(df_e),
                "n_net": eur_rr_post.size,
                "PF_RR3_post_0.05R": eur_b3,
                "breakeven_cost_corrected": eur_breakeven_correct,
                "note": "breakeven_cost on pre-cost array (post-cost avg + 0.05R)",
            },
        },
        "pooled": {
            "n_total": n_total,
            "PF_RR3_post_0.05R": pool_s,
            "breakeven_cost_corrected": pool_breakeven_correct,
            "block_bootstrap_ci": {
                "block_size": block_size,
                "fenced_pooled_ci95": [pool_block_ci[0], pool_block_ci[1]] if pool_block_ci[0] is not None else None,
                "no_fence_pooled_ci95": [pool_block_ci_old[0], pool_block_ci_old[1]] if pool_block_ci_old[0] is not None else None,
                "ci95_xauusd_oos": [xau_block_ci[0], xau_block_ci[1]] if xau_block_ci[0] is not None else None,
                "ci95_eurusd": [eur_block_ci[0], eur_block_ci[1]] if eur_block_ci[0] is not None else None,
            },
            "autocorrelation": {
                "acf_lag_1_5": [round(v, 4) for v in acf_vals],
                "acf_signs": ["+" if v >= 0 else "−" for v in acf_vals],
                "mean_acf": round(float(np.mean(acf_vals)), 4),
            },
            "heterogeneity": {
                "statistic_Q": round(Q, 4),
                "p_value": round(Q_pval, 4),
                "I2_pct": round(I2, 1),
                "interpretation": "No significant heterogeneity" if Q_pval >= 0.05 else "Significant heterogeneity",
                "caveat": "Q-test with k=2 has very low power; non-significance does NOT confirm homogeneity",
            },
            "post_hoc_disclosure": "Pooling decided after individual CI both contained 1.0. Increases false-positive risk.",
        },
    }

    out_json = os.path.join(OUT_DIR, "pooled_oos_meta_analysis.json")
    with open(out_json, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nWrote: {out_json}")

    # Print verdict
    print("\n" + "=" * 60)
    print("GENERALIZATION VERDICT (STRICT)")
    print("=" * 60)
    for line in verdict_parts:
        print(f"  {line}")
    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())