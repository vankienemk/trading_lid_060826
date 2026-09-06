"""Causal event-level feature pipeline for signal engine."""
from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd

from src.indicators.atr import add_atr, atr_percentile_causal, volatility_regime_causal
from src.indicators.volume import volume_percentile_causal, volume_zscore
from src.liquidity.level_registry import level_state_at_bar
from src.features.htf import build_htf_context
from src.features.registry import SIGNAL_FEATURE_NAMES
from src.features.sessions import session_flags

_VALID_LEVEL_TYPES = ("rolling", "swing", "equal", "prev_day")
_LEVEL_MATCH_MAX_ATR = 2.0


class FeaturePipelineError(ValueError):
    pass


def _per_bar_context(candles: pd.DataFrame, config: dict[str, Any]) -> dict[str, pd.Series]:
    ind = config.get("indicators", {}) if config else {}
    atr_period = int(ind.get("atr_period", 14))
    vol_win = int(ind.get("volume_zscore_window", 50))

    atr_frame = add_atr(candles, period=atr_period)
    atr = atr_frame["atr"]
    atr_pct = atr_percentile_causal(atr, window=200)
    vol_regime = volatility_regime_causal(atr_pct)
    vol_z = volume_zscore(candles["volume"], window=vol_win)
    vol_pct = volume_percentile_causal(candles["volume"], window=200)

    htf = build_htf_context(candles)
    return {
        "atr": atr,
        "atr_percentile": atr_pct,
        "volatility_regime": vol_regime,
        "volume_zscore": vol_z,
        "volume_percentile": vol_pct,
        "h1_trend": htf["h1_trend"],
        "prev_day_high": htf["prev_day_high"],
        "prev_day_low": htf["prev_day_low"],
    }


def _time_features(ts: pd.Timestamp, config: dict[str, Any] | None = None) -> dict[str, Any]:
    hour_utc = int(ts.hour)
    d = int(ts.dayofweek)
    day_of_week = d if d < 5 else d - 5
    flags = session_flags(hour_utc, config)
    return {
        "hour_utc": hour_utc,
        "day_of_week": day_of_week,
        "session_asia": flags.get("session_asia", False),
        "session_london": flags.get("session_london", False),
        "session_new_york": flags.get("session_new_york", False),
    }


def _rolling_level_default() -> dict[str, Any]:
    return {
        "level_type": "rolling",
        "level_age_bars": np.nan,
        "level_touch_count": np.nan,
        "bars_since_last_touch": np.nan,
        "equal_level_dispersion_atr": np.nan,
        "level_is_previous_day_high_low": False,
        "level_is_h1_swing": False,
        "level_already_partially_swept": False,
    }


def _level_features(levels: pd.DataFrame, df: pd.DataFrame, atr: pd.Series,
                    bar_pos: int, level_id: str, level_price: float, direction: str) -> dict[str, Any]:
    side = "low" if direction == "bullish" else "high"
    atr_value = float(atr.iloc[bar_pos]) if np.isfinite(atr.iloc[bar_pos]) else np.nan

    if len(levels) == 0:
        return _rolling_level_default()

    known = levels[levels["known_pos"] <= bar_pos]
    if known.empty:
        return _rolling_level_default()

    same_side = known[known["direction"] == side]
    if same_side.empty:
        return _rolling_level_default()

    diffs = (same_side["price"].astype(float) - float(level_price)).abs()
    nearest_idx = diffs.idxmin()
    if np.isfinite(atr_value) and atr_value > 0 and diffs.min() > _LEVEL_MATCH_MAX_ATR * atr_value:
        return _rolling_level_default()

    lrow = same_side.loc[[nearest_idx]]
    seen = lrow.iloc[0]
    is_h1 = bool(seen.get("is_h1", False))
    disp = seen.get("equal_dispersion_atr")
    dispersion = float(disp) if disp is not None and np.isfinite(float(disp)) else np.nan

    level_state = level_state_at_bar(lrow, df, atr, bar_pos)
    if level_state.empty:
        return _rolling_level_default()
    row = level_state.iloc[0]

    pre_swept = False
    if bar_pos > 0:
        pre_state = level_state_at_bar(lrow, df, atr, bar_pos - 1)
        if not pre_state.empty:
            pre_swept = pre_state.iloc[0]["sweep_state"] != "active"

    return {
        "level_type": str(row["level_type"]),
        "level_age_bars": float(row["age_bars"]),
        "level_touch_count": float(row["touch_count"]),
        "bars_since_last_touch": float(row["bars_since_last_touch"]),
        "equal_level_dispersion_atr": dispersion,
        "level_is_previous_day_high_low": bool(row["level_type"] == "prev_day"),
        "level_is_h1_swing": is_h1,
        "level_already_partially_swept": pre_swept,
    }


def _safe_div(num: float, den: float) -> float:
    den = float(den)
    if not np.isfinite(den) or den == 0.0:
        return np.nan
    return float(num) / den


def _event_feature_row(event: pd.Series, df: pd.DataFrame, ctx: dict[str, pd.Series],
                       levels: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    pos = df.index.get_indexer([event["event_time"]])
    bar_pos = int(pos[0]) if pos[0] >= 0 else None
    if bar_pos is None:
        raise FeaturePipelineError(f"event_time {event['event_time']} not present in candles index")

    direction = str(event["direction"])
    atr = float(ctx["atr"].iloc[bar_pos]) if np.isfinite(ctx["atr"].iloc[bar_pos]) else np.nan

    e_open, e_high, e_low, e_close = (
        float(event["event_open"]), float(event["event_high"]),
        float(event["event_low"]), float(event["event_close"]),
    )
    rng = e_high - e_low
    body = abs(e_close - e_open)

    row: dict[str, Any] = {}
    # Candle features
    row["range_atr"] = _safe_div(rng, atr)
    row["body_ratio"] = _safe_div(body, rng)
    lower_wick = min(e_open, e_close) - e_low
    upper_wick = e_high - max(e_open, e_close)
    row["lower_wick_ratio"] = _safe_div(lower_wick, rng)
    row["upper_wick_ratio"] = _safe_div(upper_wick, rng)
    row["close_location"] = _safe_div(e_close - e_low, rng)

    # Sweep features
    row["penetration_atr"] = float(event["penetration_atr"])
    row["reclaim_atr"] = float(event["reclaim_atr"])
    row["wick_ratio"] = float(event["wick_ratio"])
    row["sweep_volume_zscore"] = float(ctx["volume_zscore"].iloc[bar_pos]) if np.isfinite(ctx["volume_zscore"].iloc[bar_pos]) else np.nan

    # Level features
    lvl = _level_features(levels, df, ctx["atr"], bar_pos, str(event["level_id"]), float(event["level_price"]), direction)
    row.update(lvl)

    # Volatility
    row["atr_percentile"] = float(ctx["atr_percentile"].iloc[bar_pos]) if np.isfinite(ctx["atr_percentile"].iloc[bar_pos]) else np.nan

    # Volume
    row["volume_zscore"] = float(ctx["volume_zscore"].iloc[bar_pos]) if np.isfinite(ctx["volume_zscore"].iloc[bar_pos]) else np.nan

    # Time/session
    row.update(_time_features(event["event_time"], config))

    # HTF
    h1_trend = ctx["h1_trend"].iloc[bar_pos]
    row["h1_trend"] = float(h1_trend) if np.isfinite(h1_trend) else np.nan
    pdh = ctx["prev_day_high"].iloc[bar_pos]
    pdl = ctx["prev_day_low"].iloc[bar_pos]
    row["distance_to_previous_day_high_atr"] = _safe_div(e_close - float(pdh), atr) if np.isfinite(pdh) else np.nan
    row["distance_to_previous_day_low_atr"] = _safe_div(e_close - float(pdl), atr) if np.isfinite(pdl) else np.nan

    # Confirmation features
    confirmed = bool(event.get("is_confirmed", False))
    if confirmed and pd.notna(event.get("confirmation_time", pd.NaT)):
        row["confirmation_delay_bars"] = float(event["confirmation_delay_bars"]) if pd.notna(event.get("confirmation_delay_bars")) else np.nan
        row["confirmation_range_atr"] = float(event["confirmation_range_atr"]) if pd.notna(event.get("confirmation_range_atr")) else np.nan
        row["confirmation_body_ratio"] = float(event["confirmation_body_ratio"]) if pd.notna(event.get("confirmation_body_ratio")) else np.nan
        conf_ts = event["confirmation_time"]
        if hasattr(conf_ts, "tzinfo") and conf_ts.tzinfo is None and isinstance(df.index, pd.DatetimeIndex):
            conf_ts = conf_ts.tz_localize(df.index.tz)
        conf_bar = df.index.get_indexer([conf_ts])
        cpos = int(conf_bar[0]) if conf_bar[0] >= 0 else None
        if cpos is not None:
            cvol = float(ctx["volume_zscore"].iloc[cpos])
            row["confirmation_volume_zscore"] = cvol if np.isfinite(cvol) else np.nan
        else:
            row["confirmation_volume_zscore"] = np.nan
    else:
        row["confirmation_delay_bars"] = np.nan
        row["confirmation_range_atr"] = np.nan
        row["confirmation_body_ratio"] = np.nan
        row["confirmation_volume_zscore"] = np.nan

    return row


def build_event_features(candles: pd.DataFrame, levels: pd.DataFrame, events: pd.DataFrame,
                         config: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = dict(config or {})
    if events.empty:
        out = pd.DataFrame({"event_id": pd.Series(dtype="object"), "event_time": pd.Series(dtype="object")})
        for name in SIGNAL_FEATURE_NAMES:
            out[name] = pd.Series(dtype="float64")
        return out

    ctx = _per_bar_context(candles, cfg)
    rows: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        feat = _event_feature_row(event, candles, ctx, levels, cfg)
        feat["event_id"] = event["event_id"]
        feat["event_time"] = event["event_time"]
        rows.append(feat)

    out = pd.DataFrame(rows).reset_index(drop=True)
    cols = ["event_id", "event_time"] + SIGNAL_FEATURE_NAMES
    out = out[cols]
    return out


__all__ = ["FeaturePipelineError", "build_event_features"]