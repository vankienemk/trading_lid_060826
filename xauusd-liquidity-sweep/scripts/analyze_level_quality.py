#!/usr/bin/env python3
"""Quantitative chart-review hypothesis analysis (t4, ML_Engineer).

Reads ONLY frozen artifacts:
    data/processed/labeled_events.parquet        (974 confirmed events)
    reports/manual_review_v2.csv                 (review-round-2 pool, 160 rows)
    reports/event_charts_v2/<group>/<event_id>.png (chart folder membership)

Writes (deterministic, no timestamps, no RNG):
    reports/analysis/level_type_quality.{json,md}
    reports/analysis/trend_context_analysis.{json,md}
    reports/analysis/stop_buffer_regime.{json,md}

Analyses:
  1. level_type_quality  — 974 events split by level_type (equal/swing/prev_day;
     rolling n=0 in the frozen confirmed set — see notes) with win_rate (hit 2R),
     P(net_result_r>0), avg_net_r, PF from the REAL net_result_r column, and
     Wilson 95% CIs.  Auxiliary table: review-round-2 pool by level_type
     (confirmed rolling vs level-sweep equal/swing/prev_day) so the user's
     rolling-vs-structural chart hypothesis is directly testable where rolling
     rows exist.
  2. trend_context_analysis — 2x2 (trade direction x h1_trend at event time,
     causal) plus composite groups: sweep_cung_chieu_trend_h1 (catch-the-knife
     cluster: short in uptrend / long in downtrend — chart cases LVL-000561,
     LVL-002241) vs sweep_nguoc_trend; crossed with level_type.
  3. stop_buffer_regime — for losing trades (net_result_r<0): MAE_R
     (mae_r_h16) mean/median/quantiles by volatility_regime low vs high
     (+normal), Mann-Whitney U low-vs-high, stop-vs-time exit mix, absolute
     0.10*ATR buffer width by regime; chart case LVL-003144 cross-check.

Deterministic by construction: no wall-clock, no randomness, stable sorts,
fixed rounding; identical JSON text between runs.

Usage:
    .venv/bin/python scripts/analyze_level_quality.py
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELED = os.path.join(ROOT, "data/processed/labeled_events.parquet")
REVIEW_CSV = os.path.join(ROOT, "reports/manual_review_v2.csv")
CHARTS = os.path.join(ROOT, "reports/event_charts_v2")
OUT_DIR = os.path.join(ROOT, "reports/analysis")
Z = 1.959963984540054  # 95% normal quantile

_NET_RE = re.compile(r"net_result_r=(-?[\d.]+|nan)")
_OUT_RE = re.compile(r"primary_outcome=(\w+)")
_LVL_RE = re.compile(r"level_id=([^;]+);")
_HELD_RE = re.compile(r"bars_held=([\d.]+)")


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


def net_stats(net: pd.Series) -> dict:
    """Net-based stats on the REAL net_result_r values (NaN excluded)."""
    v = net.dropna().astype(float)
    n = int(len(v))
    if n == 0:
        return {
            "n_net_valid": 0,
            "net_positive_rate": None,
            "avg_net_r": None,
            "profit_factor": None,
        }
    pos = v[v > 0].sum()
    neg = abs(v[v < 0].sum())
    return {
        "n_net_valid": n,
        "net_positive_rate": round(float((v > 0).mean()), 6),
        "avg_net_r": round(float(v.mean()), 6),
        "profit_factor": round(float(pos / neg), 6) if neg > 0 else None,
    }


def group_stats(df: pd.DataFrame) -> dict:
    """One row of the standard table for a group of events."""
    n = int(len(df))
    tp = int((df["outcome_2r_h16"] == "tp").sum())
    sl = int((df["outcome_2r_h16"] == "sl").sum())
    ti = int((df["outcome_2r_h16"] == "time").sum())
    amb = int((df["outcome_2r_h16"] == "ambiguous").sum())
    resolved = tp + sl + ti
    ns = net_stats(df["net_result_r"])
    return {
        "n": n,
        "n_long": int((df["direction"] == "long").sum()),
        "n_short": int((df["direction"] == "short").sum()),
        "n_resolved": resolved,
        "n_ambiguous": amb,
        "n_tp": tp,
        "n_sl": sl,
        "n_time": ti,
        "win_rate": wilson_ci(tp, resolved) if resolved else None,
        "net": ns,
    }


# ---------------------------------------------------------------------------
# load frozen inputs
# ---------------------------------------------------------------------------

lab = pd.read_parquet(LABELED)
assert len(lab) == 974, f"expected 974 frozen events, got {len(lab)}"
assert lab["net_result_r"].notna().sum() == 973, "expected 973 net-valid"

# review pool (round 2)
rev = pd.read_csv(REVIEW_CSV)
rev["net_r"] = rev["notes"].str.extract(_NET_RE)[0].replace("nan", np.nan).astype(float)
rev["prim_out"] = rev["notes"].str.extract(_OUT_RE)[0]
rev["lvl_id"] = rev["notes"].str.extract(_LVL_RE)[0]
rev["bars_held_n"] = rev["notes"].str.extract(_HELD_RE)[0].astype(float)
rev["source"] = rev["source"].astype(str)

CHART_GROUPS: dict[str, str] = {}
for grp in ("winners", "losers", "time", "ambiguous"):
    d = os.path.join(CHARTS, grp)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".png"):
                CHART_GROUPS[f[:-4]] = grp
rev["chart_group"] = rev["event_id"].map(CHART_GROUPS).fillna("(not charted)")

CHARTS_6 = {
    "LVL-020791": "win",
    "LVL-023344": "win",
    "LVL-025112": "win",
    "LVL-000561": "loss",
    "LVL-002241": "loss",
    "LVL-003144": "loss",
}

# ---------------------------------------------------------------------------
# 1. level_type quality
# ---------------------------------------------------------------------------

lt_order = ["rolling", "swing", "equal", "prev_day"]
lt_rows: list[dict] = []
for lt in lt_order:
    sub = lab[lab["level_type"] == lt]
    base = {"level_type": lt}
    if len(sub) == 0:
        base.update(group_stats(sub))
        lt_rows.append(base)
        continue
    g = group_stats(sub)
    base.update(g)
    lt_rows.append(base)

assert sum(r["n"] for r in lt_rows) == 974
assert sum(r["net"]["n_net_valid"] for r in lt_rows) == 973

# auxiliary: review pool by level_type (confirmed rows are labelled rolling;
# level_sweep rows carry equal/swing/prev_day) — where the chart hypothesis
# "structural > rolling" can be tested on rows that exist
rev_valid = rev.dropna(subset=["net_r"]).copy()
rev_lt_rows: list[dict] = []
for lt in sorted(rev_valid["level_type"].dropna().unique()):
    sub = rev_valid[rev_valid["level_type"] == lt]
    n = len(sub)
    wins = int((sub["net_r"] > 0).sum())
    rec = {
        "level_type": str(lt),
        "source_pool": "confirmed" if (sub["source"] == "confirmed").all() else
                       ("level_sweep" if (sub["source"] == "level_sweep").all() else "mixed"),
        "n": n,
        "win_rate_net": wilson_ci(wins, n),
        "avg_net_r": round(float(sub["net_r"].mean()), 6),
    }
    pos = sub.loc[sub["net_r"] > 0, "net_r"].sum()
    neg = abs(sub.loc[sub["net_r"] < 0, "net_r"].sum())
    rec["profit_factor"] = round(float(pos / neg), 6) if neg > 0 else None
    rec["n_ambiguous_net_nan"] = int(rev["net_r"].isna().sum())
    rev_lt_rows.append(rec)
rev_lt_rows.sort(key=lambda r: r["level_type"])

chart_cases: list[dict] = []
for cid, expect in sorted(CHARTS_6.items()):
    row = rev[rev["event_id"] == cid]
    if len(row) == 0:
        chart_cases.append({"event_id": cid, "expected": expect,
                            "found_in_review_pool": False})
        continue
    r = row.iloc[0]
    netv = r["net_r"]
    outcome = "win" if (pd.notna(netv) and netv > 0) else (
        "loss" if (pd.notna(netv) and netv < 0) else "ambiguous/nan")
    chart_cases.append({
        "event_id": cid,
        "expected": expect,
        "found_in_review_pool": True,
        "source": r["source"],
        "level_type": str(r["level_type"]),
        "level_id": str(r["lvl_id"]) if pd.notna(r["lvl_id"]) else None,
        "primary_outcome": str(r["prim_out"]) if pd.notna(r["prim_out"]) else None,
        "net_result_r": None if pd.isna(netv) else float(netv),
        "chart_group": str(r["chart_group"]),
        "match": (outcome == expect),
    })

level_type_out = {
    "dataset": "data/processed/labeled_events.parquet (frozen, 974 confirmed events)",
    "note": ("level_type column carries the registry structural level matched to "
             "each confirmed event (baseline sweeps a rolling level_id but the "
             "feature pipeline attributes every confirmed event to a structural "
             "registry level). 'rolling' therefore has n=0 in the frozen 974; the "
             "rolling-vs-structural comparison is evaluated on the review-round-2 "
             "pool (manual_review_v2.csv) where confirmed rows are labelled "
             "'rolling'."),
    "n_total": 974,
    "n_net_valid": 973,
    "rows": lt_rows,
    "review_pool": {
        "source": "reports/manual_review_v2.csv (160 reviewed, net parseable 150)",
        "rows": rev_lt_rows,
        "note": ("win_rate here = P(net_result_r>0) on review-pool rows (chart "
                 "ground truth from event_charts_v2 folder membership); costs "
                 "already included in net_result_r."),
    },
    "chart_cases": chart_cases,
}

# ---------------------------------------------------------------------------
# 2. trend context
# ---------------------------------------------------------------------------

# direction long = bullish sweep (price thrust DOWN through a low, then long)
# direction short = bearish sweep (price thrust UP through a high, then short)
# h1_trend = sign of H1 EMA slope at event time (causal): +1 up / -1 down
lab["h1_trend_int"] = lab["h1_trend"].astype(int)
# Group A "sweep cùng chiều trend H1 mạnh" (catch-the-knife cluster): the sweep
# THRUST runs in the same direction as the H1 trend (short: up-thrust through a
# high while H1 is up; long: down-thrust through a low while H1 is down) and the
# trade fades that thrust => knife. Chart loss cases LVL-000561 (short @
# prev_day_high in a strong rally, H1 up) and LVL-002241 (long @ equal_low in a
# downtrend, H1 down) belong here.
# Group B "sweep ngược trend / trend yếu": the sweep thrust counters the H1
# trend (buying the dip in an uptrend / selling the pop in a downtrend).
lab["trend_group"] = np.where(
    ((lab["direction"] == "short") & (lab["h1_trend_int"] == 1))
    | ((lab["direction"] == "long") & (lab["h1_trend_int"] == -1)),
    "sweep_cung_chieu_trend_h1",
    "sweep_nguoc_trend",
)
assert lab["trend_group"].value_counts().sum() == 974
assert lab.groupby("trend_group")["net_result_r"].apply(lambda s: s.notna().sum()).sum() == 973

cell_rows: list[dict] = []
for direction in ("long", "short"):
    for ht in (-1, 1):
        sub = lab[(lab["direction"] == direction) & (lab["h1_trend_int"] == ht)]
        rec = group_stats(sub)
        rec.update({"direction": direction, "h1_trend": int(ht)})
        cell_rows.append(rec)

grp_rows: list[dict] = []
for gname in ("sweep_cung_chieu_trend_h1", "sweep_nguoc_trend"):
    sub = lab[lab["trend_group"] == gname]
    rec = group_stats(sub)
    rec["trend_group"] = gname
    # cross with level_type
    cross = []
    for lt in lt_order:
        s2 = sub[sub["level_type"] == lt]
        c = group_stats(s2)
        c["level_type"] = lt
        cross.append(c)
    rec["cross_level_type"] = cross
    grp_rows.append(rec)

# net-based PF per trend group using only net-valid rows (already in group_stats)
trend_out = {
    "dataset": "data/processed/labeled_events.parquet (frozen, 974)",
    "definitions": {
        "direction": "trade direction: long = bullish sweep of a low; short = "
                     "bearish sweep of a high (schema.F2 reconciliation)",
        "h1_trend": "sign of H1 EMA(50) slope at the event bar (causal, "
                    "feature_pipeline/htf.py) : +1 up / -1 down",
        "sweep_cung_chieu_trend_h1": (
            "catch-the-knife cluster (task group 'sweep cùng chiều trend H1'): "
            "SHORT while H1 uptrend (bearish sweep = up-thrust through a high "
            "during an H1 up move) OR LONG while H1 downtrend (bullish sweep = "
            "down-thrust through a low during an H1 down move). The trade fades "
            "the H1-trend thrust at the swept extreme => knife. Chart loss cases "
            "LVL-000561 (short @ prev_day_high in strong rally) and LVL-002241 "
            "(long @ equal_low in downtrend) fall in this group."),
        "sweep_nguoc_trend": (
            "sweep ngược trend / trend yếu: LONG while H1 uptrend (buying a "
            "down-thrust dip in an up trend) OR SHORT while H1 downtrend "
            "(selling an up-thrust pop in a down trend) — the sweep thrust "
            "counters the H1 trend; trade follows the H1 trend."),
        "h1_trend_is_binary_sign": True,
        "h1_trend_magnitude_feature": ("frozen dataset has no trend-strength "
                                       "magnitude column; groups split on the "
                                       "sign only"),
    },
    "n_total": 974,
    "n_net_valid": 973,
    "cells_direction_x_h1trend": cell_rows,
    "groups": grp_rows,
}

# ---------------------------------------------------------------------------
# 3. stop buffer by volatility regime (losing trades)
# ---------------------------------------------------------------------------

losers = lab[lab["net_result_r"] < 0].copy()
assert len(losers) == 562

regime_order = ["low", "normal", "high"]
mae_rows: list[dict] = []
for rg in regime_order:
    sub = losers[losers["volatility_regime"] == rg]
    if len(sub) == 0:
        continue
    mae = sub["mae_r_h16"].astype(float)
    stop_n = int((sub["exit_reason"] == "stop").sum())
    time_n = int((sub["exit_reason"] == "time").sum())
    mae_rows.append({
        "volatility_regime": rg,
        "n_losers": int(len(sub)),
        "n_stop_exit": stop_n,
        "n_time_exit": time_n,
        "stop_share": round(stop_n / len(sub), 6),
        "mae_r_h16_mean": round(float(mae.mean()), 6),
        "mae_r_h16_median": round(float(mae.median()), 6),
        "mae_r_h16_p25": round(float(mae.quantile(0.25)), 6),
        "mae_r_h16_p75": round(float(mae.quantile(0.75)), 6),
        "mae_r_h16_p90": round(float(mae.quantile(0.90)), 6),
        "p_mae_gt_1": wilson_ci(int((mae > 1.0).sum()), len(sub)),
        "p_mae_gt_1_5": wilson_ci(int((mae > 1.5).sum()), len(sub)),
        # absolute stop-buffer width 0.10*ATR at entry (price units)
        "buffer_width_price": round(0.10 * float(sub["atr"].median()), 6),
        "buffer_width_pct_of_risk": round(
            100.0 * float((0.10 * sub["atr"] / sub["risk_price"]).median()), 6),
    })

# Mann-Whitney U low vs high on loser MAE_R
mae_low = losers.loc[losers["volatility_regime"] == "low", "mae_r_h16"].astype(float)
mae_high = losers.loc[losers["volatility_regime"] == "high", "mae_r_h16"].astype(float)
u_stat, u_p = stats.mannwhitneyu(mae_low, mae_high, alternative="two-sided")

# overall loser MAE_R distribution context (all regimes)
stop_out = {
    "dataset": "data/processed/labeled_events.parquet (frozen, 974)",
    "scope": ("losing trades = net_result_r < 0 (562: 448 stop + 114 time). "
              "MAE_R uses the frozen mae_r_h16 column (max adverse excursion in "
              "R units over the 16-bar horizon)."),
    "stop_placement_rule": ("triple_barrier.compute_trade_levels: stop = swept "
                            "extreme +/- 0.10*ATR (stop_buffer_atr=0.10); risk = "
                            "|entry - stop|; target = +/- 2R."),
    "hypothesis": ("user chart case LVL-003144 (swing_low): stop buffer 0.10 ATR "
                   "too tight in the LOW volatility regime (small ATR -> buffer "
                   "and risk are tiny in price terms; retest of the swept extreme "
                   "stops the trade before the 2R target)."),
    "n_losers_total": int(len(losers)),
    "rows": mae_rows,
    "mann_whitney_low_vs_high": {
        "u_stat": float(u_stat),
        "p_value_two_sided": float(u_p),
        "interpretation": ("p<0.05 => loser MAE_R distribution differs between "
                           "low and high volatility regimes"),
    },
    "all_regime_loser_mae": {
        "mean": round(float(losers["mae_r_h16"].mean()), 6),
        "median": round(float(losers["mae_r_h16"].median()), 6),
        "p25": round(float(losers["mae_r_h16"].quantile(0.25)), 6),
        "p75": round(float(losers["mae_r_h16"].quantile(0.75)), 6),
    },
    "chart_case_LVL_003144": (
        next((c for c in chart_cases if c["event_id"] == "LVL-003144"), None)),
}

# ---------------------------------------------------------------------------
# persist JSON (deterministic: sort_keys, no timestamps)
# ---------------------------------------------------------------------------

os.makedirs(OUT_DIR, exist_ok=True)
json_files = {
    "level_type_quality.json": level_type_out,
    "trend_context_analysis.json": trend_out,
    "stop_buffer_regime.json": stop_out,
}
for name, obj in json_files.items():
    with open(os.path.join(OUT_DIR, name), "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------

def ci_str(c: dict | None, suffix: str = "") -> str:
    if c is None:
        return "N/A"
    return (f"{c['point']:.4f} [{c['ci95_low']:.4f}, {c['ci95_high']:.4f}]"
            + (f" (k={c['k']}, n={c['n']})" if suffix == "full" else ""))


md1 = [
    "# Level-Type Quality Analysis (974 frozen events)",
    "",
    "**Dataset**: `data/processed/labeled_events.parquet` (974 confirmed events, "
    "973 net-valid). Metrics from the REAL `net_result_r` (costs included).",
    "",
    "> **Note on `rolling`**: in the frozen 974 every confirmed event carries a "
    "structural registry `level_type` (`equal`/`swing`/`prev_day`) because the "
    "baseline sweeps a rolling `level_id` and the feature pipeline attributes "
    "each event to the matched structural level; `rolling` n=0 in this table. "
    "The rolling-vs-structural comparison is tested on the review-round-2 pool "
    "below, where confirmed rows exist labelled `rolling`.",
    "",
    "| level_type | n | long/short | win_rate (hit 2R) [95% CI] | "
    "P(net>0) [95% CI] | avg_net_r | PF (net) |",
    "|---|---|---|---|---|---|---|",
]
for r in lt_rows:
    w = r["win_rate"]
    n_ = r["net"]
    w_s = (f"{w['point']:.4f} [{w['ci95_low']:.4f}, {w['ci95_high']:.4f}]"
           if w else "N/A")
    p_s = (f"{n_['net_positive_rate']:.4f} "
           f"[{wilson_ci(int(n_['net_positive_rate'] * n_['n_net_valid']), n_['n_net_valid'])['ci95_low']:.4f}, "
           f"{wilson_ci(int(n_['net_positive_rate'] * n_['n_net_valid']), n_['n_net_valid'])['ci95_high']:.4f}]"
           if n_["net_positive_rate"] is not None else "N/A")
    md1.append(
        f"| {r['level_type']} | {r['n']} | {r['n_long']}/{r['n_short']} | "
        f"{w_s} | {p_s} | {_fmt(n_['avg_net_r'])} | {_fmt(n_['profit_factor'])} |"
    )
md1 += [
    "",
    "## Review-round-2 pool (rolling vs structural — chart ground truth)",
    "",
    "Source: `reports/manual_review_v2.csv` (160 reviewed rows; confirmed pool "
    "labelled `rolling`, level-sweep pool `equal`/`swing`/`prev_day`). "
    "win_rate = P(net>0) with Wilson 95% CI.",
    "",
    "| level_type | source pool | n | P(net>0) [95% CI] | avg_net_r | PF |",
    "|---|---|---|---|---|---|",
]
for r in rev_lt_rows:
    w = r["win_rate_net"]
    w_s = (f"{w['point']:.4f} [{w['ci95_low']:.4f}, {w['ci95_high']:.4f}]"
           if w else "N/A")
    md1.append(
        f"| {r['level_type']} | {r['source_pool']} | {r['n']} | {w_s} | "
        f"{_fmt(r['avg_net_r'])} | {_fmt(r['profit_factor'])} |"
    )
md1 += [
    "",
    "## Chart case cross-check (user's 6 observations)",
    "",
    "| case | level_type | level_id | outcome | net_result_r | chart group | match |",
    "|---|---|---|---|---|---|---|",
]
for c in chart_cases:
    if not c.get("found_in_review_pool"):
        md1.append(f"| {c['event_id']} | — | — | — | — | — | not in pool |")
        continue
    md1.append(
        f"| {c['event_id']} | {c['level_type']} | {c['level_id']} | "
        f"{c['primary_outcome']} | {_fmt(c['net_result_r'])} | "
        f"{c['chart_group']} | {'PASS' if c['match'] else 'MISMATCH'} |"
    )

with open(os.path.join(OUT_DIR, "level_type_quality.md"), "w") as f:
    f.write("\n".join(md1) + "\n")

md2 = [
    "# Trend-Context Analysis (direction x H1 trend at event time, causal)",
    "",
    f"**Dataset**: frozen 974 events (973 net-valid). `h1_trend` = sign of "
    f"H1 EMA(50) slope at event bar (+1 up / -1 down; binary sign only).",
    "",
    "## 2x2: trade direction x H1 trend",
    "",
    "| direction | h1_trend | n | win_rate [95% CI] | P(net>0) | avg_net_r | PF |",
    "|---|---|---|---|---|---|---|",
]
for r in cell_rows:
    w = r["win_rate"]
    w_s = (f"{w['point']:.4f} [{w['ci95_low']:.4f}, {w['ci95_high']:.4f}]"
           if w else "N/A")
    n_ = r["net"]
    p_s = (f"{n_['net_positive_rate']:.4f}" if n_["net_positive_rate"] is not None
           else "N/A")
    md2.append(
        f"| {r['direction']} | {r['h1_trend']:+d} | {r['n']} | {w_s} | "
        f"{p_s} | {_fmt(n_['avg_net_r'])} | {_fmt(n_['profit_factor'])} |"
    )
md2 += [
    "",
    "## Composite groups",
    "",
    "- **sweep_cung_chieu_trend_h1** = catch-the-knife cluster (task group "
    "'sweep cùng chiều trend H1 mạnh'): SHORT while H1 uptrend (bearish sweep = "
    "up-thrust through a high during an H1 up move) **or** LONG while H1 "
    "downtrend (bullish sweep = down-thrust through a low during an H1 down "
    "move) — the trade fades the H1-trend thrust => knife. Chart cases "
    "LVL-000561 (short @ prev_day_high in a strong rally) and LVL-002241 "
    "(long @ equal_low in a downtrend) belong here.",
    "- **sweep_nguoc_trend** = LONG while H1 uptrend (buying a dip in an up "
    "trend) **or** SHORT while H1 downtrend (selling a pop in a down trend) — "
    "the sweep thrust counters the H1 trend; the trade follows the H1 trend.",
    "",
    "| group | n | win_rate (hit 2R) [95% CI] | P(net>0) | avg_net_r | PF |",
    "|---|---|---|---|---|---|",
]
for r in grp_rows:
    w = r["win_rate"]
    w_s = (f"{w['point']:.4f} [{w['ci95_low']:.4f}, {w['ci95_high']:.4f}]"
           if w else "N/A")
    n_ = r["net"]
    p_s = (f"{n_['net_positive_rate']:.4f} [{wilson_ci(int(n_['net_positive_rate'] * n_['n_net_valid']), n_['n_net_valid'])['ci95_low']:.4f}, "
           f"{wilson_ci(int(n_['net_positive_rate'] * n_['n_net_valid']), n_['n_net_valid'])['ci95_high']:.4f}]"
           if n_["net_positive_rate"] is not None else "N/A")
    md2.append(
        f"| {r['trend_group']} | {r['n']} | {w_s} | {p_s} | "
        f"{_fmt(n_['avg_net_r'])} | {_fmt(n_['profit_factor'])} |"
    )
md2 += [
    "",
    "## Cross: group x level_type",
    "",
    "| group | level_type | n | win_rate | P(net>0) | avg_net_r | PF |",
    "|---|---|---|---|---|---|---|",
]
for r in grp_rows:
    for c in r["cross_level_type"]:
        if c["n"] == 0:
            md2.append(f"| {r['trend_group']} | {c['level_type']} | 0 | — | — | — | — |")
            continue
        w = c["win_rate"]
        w_s = (f"{w['point']:.4f} [{w['ci95_low']:.4f}, {w['ci95_high']:.4f}]"
               if w else "N/A")
        n_ = c["net"]
        p_s = (f"{n_['net_positive_rate']:.4f}" if n_["net_positive_rate"] is not None
               else "N/A")
        md2.append(
            f"| {r['trend_group']} | {c['level_type']} | {c['n']} | {w_s} | "
            f"{p_s} | {_fmt(n_['avg_net_r'])} | {_fmt(n_['profit_factor'])} |"
        )

with open(os.path.join(OUT_DIR, "trend_context_analysis.md"), "w") as f:
    f.write("\n".join(md2) + "\n")

md3 = [
    "# Stop-Buffer vs Volatility Regime (losing trades, frozen 974)",
    "",
    "**Scope**: losing trades = `net_result_r < 0` (562: 448 stop + 114 time). "
    "MAE_R = frozen `mae_r_h16` (max adverse excursion in R).",
    "",
    "**Stop rule** (`triple_barrier.compute_trade_levels`): "
    "stop = swept extreme ± 0.10·ATR (`stop_buffer_atr=0.10`); "
    "risk = |entry − stop|; target = ±2R.",
    "",
    "**Hypothesis (chart case LVL-003144, swing_low)**: in the LOW volatility "
    "regime the 0.10·ATR buffer (and the whole risk) is tiny in price terms, so "
    "a normal retest of the swept extreme stops the trade before the 2R target.",
    "",
    "| regime | n losers | stop/time | MAE_R mean | median | p25 | p75 | p90 | "
    "P(MAE>1R) | P(MAE>1.5R) | 0.1·ATR buffer (price) | buffer % risk |",
    "|---|---|---|---|---|---|---|---|---|---|---|---|",
]
for r in mae_rows:
    p1 = r["p_mae_gt_1"]
    p15 = r["p_mae_gt_1_5"]
    p1_s = (f"{p1['point']:.3f} [{p1['ci95_low']:.3f},{p1['ci95_high']:.3f}]"
            if p1 else "N/A")
    p15_s = (f"{p15['point']:.3f} [{p15['ci95_low']:.3f},{p15['ci95_high']:.3f}]"
             if p15 else "N/A")
    md3.append(
        f"| {r['volatility_regime']} | {r['n_losers']} | {r['n_stop_exit']}/"
        f"{r['n_time_exit']} | {r['mae_r_h16_mean']:.3f} | "
        f"{r['mae_r_h16_median']:.3f} | {r['mae_r_h16_p25']:.3f} | "
        f"{r['mae_r_h16_p75']:.3f} | {r['mae_r_h16_p90']:.3f} | {p1_s} | "
        f"{p15_s} | {r['buffer_width_price']:.4f} | "
        f"{r['buffer_width_pct_of_risk']:.1f}% |"
    )
md3 += [
    "",
    f"**Mann-Whitney U (low vs high loser MAE_R)**: U={u_stat:.1f}, "
    f"p={u_p:.4g} (two-sided) — {'significant difference' if u_p < 0.05 else 'no significant difference'}.",
    "",
    "## Interpretation",
    "",
    "- `mae_r_h16` counts the full OHLC range of the exit bar (the loop breaks "
    "only after the stop/target bar is processed), so for a stop-exit loser "
    "`MAE_R - 1` = the intrabar wick beyond the 0.10-ATR stop level before the "
    "bar resolved; it does NOT include post-exit bars.",
    "- Low-regime losers are stopped far more often (stop share "
    f"{mae_rows[0]['n_stop_exit']}/{mae_rows[0]['n_losers']} = "
    f"{mae_rows[0]['stop_share']:.1%}) than high-regime losers "
    f"({mae_rows[-1]['n_stop_exit']}/{mae_rows[-1]['n_losers']} = "
    f"{mae_rows[-1]['stop_share']:.1%}), and their stop-bar pierce beyond the "
    f"stop is much deeper (median MAE_R {mae_rows[0]['mae_r_h16_median']:.2f}R "
    f"vs {mae_rows[-1]['mae_r_h16_median']:.2f}R => ~"
    f"{mae_rows[0]['mae_r_h16_median'] - 1:.2f}R vs ~"
    f"{mae_rows[-1]['mae_r_h16_median'] - 1:.2f}R beyond the stop). "
    "Consistent with the LVL-003144 observation: in the low-ATR regime the "
    "0.10-ATR stop buffer at the swept extreme is routinely violated by retest "
    "wicks, stopping trades that a wider buffer (or same-bar fill at the stop "
    "level) might have let reach the 2R target.",
    "- `buffer % risk` = 0.10·ATR as a share of the initial risk "
    f"(median {mae_rows[0]['buffer_width_pct_of_risk']:.1f}% low-regime losers "
    f"vs {mae_rows[-1]['buffer_width_pct_of_risk']:.1f}% high-regime): the stop "
    "sits very close to the swept extreme in every regime.",
    "- Chart case LVL-003144 cross-check is embedded in "
    "`reports/analysis/level_type_quality.json` (chart_cases) and "
    "`reports/analysis/stop_buffer_regime.json`.",
]
with open(os.path.join(OUT_DIR, "stop_buffer_regime.md"), "w") as f:
    f.write("\n".join(md3) + "\n")

# ---------------------------------------------------------------------------
# console summary + integrity checks
# ---------------------------------------------------------------------------

print("=" * 64)
print("Level-Type / Trend-Context / Stop-Buffer analysis (t4)")
print("=" * 64)
print(f"[ok] loaded {len(lab)} frozen events ({lab['net_result_r'].notna().sum()} net-valid)")
for r in lt_rows:
    print(f"  level_type={r['level_type']:<8} n={r['n']:<4} "
          f"win={r['win_rate']['point'] if r['win_rate'] else None} "
          f"P(net>0)={r['net']['net_positive_rate']} PF={r['net']['profit_factor']}")
print("[ok] level_type n sums:", sum(r["n"] for r in lt_rows),
      "| net-valid sums:", sum(r["net"]["n_net_valid"] for r in lt_rows))
for r in grp_rows:
    print(f"  trend_group={r['trend_group']:<14} n={r['n']:<4} "
          f"win={r['win_rate']['point'] if r['win_rate'] else None} "
          f"PF={r['net']['profit_factor']}")
print("[ok] trend group n sums:", sum(r["n"] for r in grp_rows),
      "| net-valid sums:", sum(r["net"]["n_net_valid"] for r in grp_rows))
for r in mae_rows:
    print(f"  regime={r['volatility_regime']:<6} n_losers={r['n_losers']:<4} "
          f"MAE median={r['mae_r_h16_median']} mean={r['mae_r_h16_mean']}")
print(f"[ok] Mann-Whitney low-vs-high U={u_stat:.1f} p={u_p:.4g}")
print("[ok] chart cases:")
for c in chart_cases:
    print(f"  {c['event_id']}: expected={c['expected']} "
          f"outcome={'win' if c.get('net_result_r') and c['net_result_r'] > 0 else ('loss' if c.get('net_result_r') and c['net_result_r'] < 0 else 'n/a')} "
          f"match={c.get('match')}")
for name in json_files:
    p = os.path.join(OUT_DIR, name)
    print(f"[ok] wrote {os.path.relpath(p, ROOT)} ({os.path.getsize(p)} bytes)")
print("[ok] wrote reports/analysis/*.md")
print("DONE")
