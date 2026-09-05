"""Rule-based scoring for liquidity sweep events (guide section 19)."""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd

_DEFAULT_WEIGHTS = {"level": 20, "sweep": 25, "reclaim": 15, "confirmation": 20, "context": 10, "volume": 10}
SCORE_COMPONENTS = list(_DEFAULT_WEIGHTS.keys())
TOTAL_MAX = sum(_DEFAULT_WEIGHTS.values())


class ScoringError(ValueError):
    pass


def _get_weights(config: dict[str, Any]) -> dict[str, int]:
    weights = dict(_DEFAULT_WEIGHTS)
    scoring_cfg = config.get("scoring", {})
    cfg_weights = scoring_cfg.get("weights", {})
    for key in SCORE_COMPONENTS:
        if key in cfg_weights:
            val = cfg_weights[key]
            if isinstance(val, (int, float)) and val >= 0:
                weights[key] = int(val)
    return weights


def _score_level(row: pd.Series, levels_df: pd.DataFrame | None = None) -> float:
    score = 0.0
    level_type = row.get("level_type", "")
    if level_type == "equal":
        score += 8
    touch_count = row.get("level_touch_count", 0)
    if pd.notna(touch_count) and touch_count >= 3:
        score += 4
    if row.get("level_is_h1_swing", False) or row.get("level_is_previous_day_high_low", False):
        score += 5
    bars_since = row.get("bars_since_last_touch", np.nan)
    if pd.isna(bars_since):
        score += 3
    return min(score, 20)


def _score_sweep(row: pd.Series) -> float:
    score = 0.0
    pen_atr = row.get("penetration_atr", 0)
    if pd.notna(pen_atr):
        if 0.10 <= pen_atr <= 0.35:
            score += 8
        elif 0.05 <= pen_atr < 0.10 or 0.35 < pen_atr <= 0.50:
            score += 5
    wick_ratio = row.get("wick_ratio", 0)
    if pd.notna(wick_ratio):
        if wick_ratio >= 0.70:
            score += 7
        elif wick_ratio >= 0.50:
            score += 5
        elif wick_ratio >= 0.35:
            score += 3
    range_atr = row.get("range_atr", np.nan)
    if pd.notna(range_atr):
        if range_atr >= 2.0:
            score += 5
        elif range_atr >= 1.5:
            score += 3
        elif range_atr >= 1.0:
            score += 2
    return min(score, 25)


def _score_reclaim(row: pd.Series) -> float:
    score = 0.0
    reclaim_atr = row.get("reclaim_atr", 0)
    if pd.notna(reclaim_atr) and reclaim_atr > 0:
        score += 5
        if reclaim_atr >= 0.25:
            score += 10
        elif reclaim_atr >= 0.10:
            score += 5
    return min(score, 15)


def _score_confirmation(row: pd.Series) -> float:
    score = 0.0
    is_confirmed = row.get("is_confirmed", False)
    delay = row.get("confirmation_delay_bars", np.nan)
    if is_confirmed and pd.notna(delay):
        if 1 <= delay <= 3:
            score += 5
        elif delay == 0:
            score += 3
    return min(score, 20)


def _score_context(row: pd.Series) -> float:
    score = 0.0
    h1_trend = row.get("h1_trend", np.nan)
    direction = str(row.get("direction", "")).lower()
    if pd.notna(h1_trend):
        if (h1_trend == 1 and "long" in direction) or (h1_trend == -1 and "short" in direction):
            score += 5
        elif h1_trend in (1, -1):
            score += 2
    return min(score, 10)


def _score_volume(row: pd.Series) -> float:
    score = 0.0
    vol_z = row.get("volume_zscore", np.nan)
    if pd.notna(vol_z):
        if vol_z > 2.0:
            score += 5
        elif vol_z > 1.0:
            score += 3
        elif vol_z > 0:
            score += 1
    return min(score, 10)


def compute_rule_scores(events_df: pd.DataFrame, config: dict[str, Any], levels_df: pd.DataFrame | None = None) -> pd.DataFrame:
    _validate_inputs(events_df)
    weights = _get_weights(config)
    total_weight = sum(weights.values())
    df = events_df.copy()

    scores_level, scores_sweep, scores_reclaim = [], [], []
    scores_confirmation, scores_context, scores_volume = [], [], []

    for _, row in df.iterrows():
        scores_level.append(_score_level(row, levels_df))
        scores_sweep.append(_score_sweep(row))
        scores_reclaim.append(_score_reclaim(row))
        scores_confirmation.append(_score_confirmation(row))
        scores_context.append(_score_context(row))
        scores_volume.append(_score_volume(row))

    df["score_level"] = scores_level
    df["score_sweep"] = scores_sweep
    df["score_reclaim"] = scores_reclaim
    df["score_confirmation"] = scores_confirmation
    df["score_context"] = scores_context
    df["score_volume"] = scores_volume

    df["rule_score"] = (
        df["score_level"] * weights["level"] / 20
        + df["score_sweep"] * weights["sweep"] / 25
        + df["score_reclaim"] * weights["reclaim"] / 15
        + df["score_confirmation"] * weights["confirmation"] / 20
        + df["score_context"] * weights["context"] / 10
        + df["score_volume"] * weights["volume"] / 10
    ) * (100 / total_weight)
    df["rule_score"] = df["rule_score"].clip(0, 100).round(2)
    return df


def _validate_inputs(df: pd.DataFrame) -> None:
    required = ["event_id", "direction"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ScoringError(f"Missing required columns: {missing}")
    if len(df) == 0:
        raise ScoringError("Input dataframe is empty")