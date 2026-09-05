"""V2 liquidity-sweep detector: nguoc_trend + max_pen <= 0.20 + configurable R:R."""
from __future__ import annotations
import numpy as np
import pandas as pd

from src.events.sweep_detector import (
    add_sweep_features,
    _candidate_rows,
    _EVENT_COLUMNS,
    BULLISH,
    BEARISH,
)
from src.events.deduplication import select_deduplicated_events
from src.features.htf import build_htf_context

V2_MAX_PENETRATION_ATR = 0.20
V2_DEFAULT_TARGET_R = 2.0

_V2_EVENT_COLUMNS = list(_EVENT_COLUMNS) + [
    "h1_trend",
    "v2_target_r",
]


def detect_sweeps_v2(
    df: pd.DataFrame,
    atr_period: int = 14,
    level_lookback: int = 20,
    min_penetration_atr: float = 0.05,
    max_penetration_atr: float = V2_MAX_PENETRATION_ATR,
    min_wick_ratio: float = 0.35,
    min_reclaim_atr: float = 0.0,
    v2_nguoc_trend: bool = True,
) -> pd.DataFrame:
    if max_penetration_atr < min_penetration_atr:
        raise ValueError(
            f"detect_sweeps_v2: max_penetration_atr ({max_penetration_atr}) must be "
            f">= min_penetration_atr ({min_penetration_atr})"
        )

    out = add_sweep_features(df, atr_period=atr_period, level_lookback=level_lookback)
    htf = build_htf_context(df)
    out["h1_trend"] = htf["h1_trend"]

    out["sweep_long"] = (
        out["liq_low"].notna()
        & out["atr"].notna()
        & (out["low"] < out["liq_low"])
        & (out["close"] > out["liq_low"])
        & (out["low_pen_atr"] >= min_penetration_atr)
        & (out["low_pen_atr"] <= max_penetration_atr)
        & (out["lower_wick_ratio"] >= min_wick_ratio)
        & (out["low_reclaim_atr"] >= min_reclaim_atr)
    )
    out["sweep_short"] = (
        out["liq_high"].notna()
        & out["atr"].notna()
        & (out["high"] > out["liq_high"])
        & (out["close"] < out["liq_high"])
        & (out["high_pen_atr"] >= min_penetration_atr)
        & (out["high_pen_atr"] <= max_penetration_atr)
        & (out["upper_wick_ratio"] >= min_wick_ratio)
        & (out["high_reclaim_atr"] >= min_reclaim_atr)
    )

    if v2_nguoc_trend:
        trend_ok = out["h1_trend"].notna()
        long_nguoc = out["sweep_long"] & (out["h1_trend"] == 1.0)
        short_nguoc = out["sweep_short"] & (out["h1_trend"] == -1.0)
        out["sweep_v2_long"] = trend_ok & long_nguoc
        out["sweep_v2_short"] = trend_ok & short_nguoc
    else:
        out["sweep_v2_long"] = out["sweep_long"].copy()
        out["sweep_v2_short"] = out["sweep_short"].copy()

    return out


def _candidate_rows_v2(out: pd.DataFrame) -> pd.DataFrame:
    flags = out["sweep_v2_long"].fillna(False) | out["sweep_v2_short"].fillna(False)
    positions = np.flatnonzero(flags.to_numpy())
    rows = []
    for pos in positions:
        if bool(out["sweep_v2_long"].iloc[pos]):
            rows.append(_candidate_row_v2(out, pos, BULLISH))
        if bool(out["sweep_v2_short"].iloc[pos]):
            rows.append(_candidate_row_v2(out, pos, BEARISH))
    return pd.DataFrame(rows, columns=["position"] + _V2_EVENT_COLUMNS[1:])


def _candidate_row_v2(out: pd.DataFrame, pos: int, direction: str) -> dict:
    if direction == BULLISH:
        level_id = "rolling_low"
        level_price = out["liq_low"].iloc[pos]
        penetration = out["low_pen_atr"].iloc[pos]
        wick = out["lower_wick_ratio"].iloc[pos]
        reclaim = out["low_reclaim_atr"].iloc[pos]
    else:
        level_id = "rolling_high"
        level_price = out["liq_high"].iloc[pos]
        penetration = out["high_pen_atr"].iloc[pos]
        wick = out["upper_wick_ratio"].iloc[pos]
        reclaim = out["high_reclaim_atr"].iloc[pos]

    h1_t = out["h1_trend"].iloc[pos]
    h1_t = int(h1_t) if pd.notna(h1_t) else 0

    return {
        "position": int(pos),
        "event_time": out.index[pos],
        "direction": direction,
        "level_id": level_id,
        "level_price": float(level_price),
        "event_open": float(out["open"].iloc[pos]),
        "event_high": float(out["high"].iloc[pos]),
        "event_low": float(out["low"].iloc[pos]),
        "event_close": float(out["close"].iloc[pos]),
        "penetration_atr": float(penetration),
        "wick_ratio": float(wick),
        "reclaim_atr": float(reclaim),
        "h1_trend": h1_t,
        "v2_target_r": V2_DEFAULT_TARGET_R,
    }


def build_sweep_events_v2(
    df: pd.DataFrame,
    config: dict | None = None,
    *,
    atr_period: int | None = None,
    level_lookback: int | None = None,
    min_penetration_atr: float | None = None,
    max_penetration_atr: float | None = None,
    min_wick_ratio: float | None = None,
    min_reclaim_atr: float | None = None,
    cooldown_bars: int | None = None,
    group_rule: str | None = None,
    v2_nguoc_trend: bool = True,
    v2_target_r: float = V2_DEFAULT_TARGET_R,
) -> pd.DataFrame:
    cfg = config or {}
    ind = cfg.get("indicators", {})
    liq = cfg.get("liquidity", {})
    swp = cfg.get("sweep", {})
    atr_period = ind.get("atr_period", 14) if atr_period is None else atr_period
    level_lookback = liq.get("rolling_lookback", 20) if level_lookback is None else level_lookback
    min_penetration_atr = swp.get("min_penetration_atr", 0.05) if min_penetration_atr is None else min_penetration_atr
    max_penetration_atr = V2_MAX_PENETRATION_ATR if max_penetration_atr is None else max_penetration_atr
    min_wick_ratio = swp.get("min_wick_ratio", 0.35) if min_wick_ratio is None else min_wick_ratio
    min_reclaim_atr = swp.get("min_reclaim_atr", 0.0) if min_reclaim_atr is None else min_reclaim_atr
    cooldown_bars = swp.get("cooldown_bars", 4) if cooldown_bars is None else cooldown_bars
    group_rule = swp.get("group_rule", "first") if group_rule is None else group_rule

    out = detect_sweeps_v2(
        df,
        atr_period=atr_period,
        level_lookback=level_lookback,
        min_penetration_atr=min_penetration_atr,
        max_penetration_atr=max_penetration_atr,
        min_wick_ratio=min_wick_ratio,
        min_reclaim_atr=min_reclaim_atr,
        v2_nguoc_trend=v2_nguoc_trend,
    )
    candidates = _candidate_rows_v2(out)
    if candidates.empty:
        return pd.DataFrame(columns=_V2_EVENT_COLUMNS)

    selected = select_deduplicated_events(candidates, cooldown_bars=cooldown_bars, group_rule=group_rule)
    selected = selected.sort_values(["event_time", "direction"], kind="stable")
    selected["event_id"] = [f"V2-{i:06d}" for i in range(len(selected))]
    selected["v2_target_r"] = v2_target_r
    return selected[_V2_EVENT_COLUMNS].reset_index(drop=True)


__all__ = [
    "detect_sweeps_v2",
    "build_sweep_events_v2",
    "V2_MAX_PENETRATION_ATR",
    "V2_DEFAULT_TARGET_R",
    "_V2_EVENT_COLUMNS",
]