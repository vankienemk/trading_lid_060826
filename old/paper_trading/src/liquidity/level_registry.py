"""Simplified liquidity-level registry for paper trading (rolling + prev_day)."""
from __future__ import annotations
from typing import cast
import numpy as np
import pandas as pd

from src.indicators.atr import add_atr
from src.data.resampler import previous_day_high_low
from src.schema import LEVEL_STATE_COLUMNS


def _validate_frame(df: pd.DataFrame, func: str) -> None:
    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"{func}: missing required columns {missing}")
    if len(df) == 0:
        raise ValueError(f"{func}: empty DataFrame")
    if not df.index.is_monotonic_increasing:
        raise ValueError(f"{func}: index must be chronologically sorted")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError(f"{func}: index must be a DatetimeIndex")


def detect_previous_day_levels(df: pd.DataFrame, max_age_bars: int = 200) -> pd.DataFrame:
    """Simple prev-day high/low level detection used in sweep detection."""
    _validate_frame(df, "detect_previous_day_levels")
    ts = df.index.to_numpy()
    n = len(df)
    day = cast(pd.DatetimeIndex, df.index).normalize()

    daily_high = df["high"].groupby(day).max()
    daily_low = df["low"].groupby(day).min()
    days = np.asarray(daily_high.index)

    rows: list[dict] = []
    for k in range(1, len(days)):
        day_start = days[k]
        first_pos = int(np.searchsorted(ts, day_start, side="left"))
        if first_pos >= n:
            break
        origin_pos = first_pos - 1
        prev_day = days[k - 1]
        prev_high = float(daily_high[prev_day])
        prev_low = float(daily_low[prev_day])
        rows.append({
            "level_id": f"prev_day_high_{origin_pos}",
            "level_type": "prev_day",
            "direction": "high",
            "price": prev_high,
            "price_min": prev_high,
            "price_max": prev_high,
            "origin_pos": origin_pos,
            "origin_time": ts[origin_pos],
            "known_at": ts[first_pos],
            "status": "active",
            "touch_count": 0,
            "first_touch_time": pd.NaT,
            "last_touch_time": pd.NaT,
            "known_pos": first_pos,
            "is_h1": False,
            "max_age_bars": int(max_age_bars),
        })
        rows.append({
            "level_id": f"prev_day_low_{origin_pos}",
            "level_type": "prev_day",
            "direction": "low",
            "price": prev_low,
            "price_min": prev_low,
            "price_max": prev_low,
            "origin_pos": origin_pos,
            "origin_time": ts[origin_pos],
            "known_at": ts[first_pos],
            "status": "active",
            "touch_count": 0,
            "first_touch_time": pd.NaT,
            "last_touch_time": pd.NaT,
            "known_pos": first_pos,
            "is_h1": False,
            "max_age_bars": int(max_age_bars),
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("known_at").reset_index(drop=True)
    return out


def build_liquidity_levels(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    _validate_frame(df, "build_liquidity_levels")
    indicators = config.get("indicators", {}) if config else {}
    liq = config.get("liquidity", {}) if config else {}
    atr_period = int(indicators.get("atr_period", 14))
    atr = add_atr(df, atr_period)["atr"]

    prev = liq.get("prev_day", {})
    parts = []
    if prev.get("enabled", True):
        parts.append(detect_previous_day_levels(df, max_age_bars=int(prev.get("max_age_bars", 200))))

    if not parts:
        return pd.DataFrame()
    combined = pd.concat(parts, ignore_index=True)
    return combined


def level_state_at_bar(registry: pd.DataFrame, df: pd.DataFrame, atr: pd.Series, bar_pos: int) -> pd.DataFrame:
    if not isinstance(bar_pos, (int, np.integer)) or bar_pos < 0:
        raise ValueError(f"level_state_at_bar: bar_pos must be >= 0, got {bar_pos}")
    if bar_pos >= len(df):
        raise ValueError(f"level_state_at_bar: bar_pos {bar_pos} out of range")
    if len(atr) != len(df):
        raise ValueError("level_state_at_bar: ATR length mismatch")

    low = df["low"].to_numpy()
    high = df["high"].to_numpy()
    atr_arr = atr.to_numpy(dtype="float64")
    ts = df.index.to_numpy()

    rows: list[dict] = []
    for _, level in registry.iterrows():
        direction = level["direction"]
        price = float(level["price"])
        tol_atr = float(level.get("touch_tolerance_atr", 0.0))
        known_pos = int(level["known_pos"])
        origin_pos = int(level["origin_pos"])
        max_age = int(level["max_age_bars"])
        level_type = level["level_type"]

        if known_pos > bar_pos:
            continue

        count_from = known_pos if level_type == "prev_day" else origin_pos
        if count_from <= bar_pos:
            sl = slice(count_from, bar_pos + 1)
            if direction == "low":
                mask = low[sl] <= price + tol_atr * atr_arr[sl]
            else:
                mask = high[sl] >= price - tol_atr * atr_arr[sl]
            touch_count = int(np.sum(mask))
        else:
            touch_count = 0

        sl = slice(known_pos, bar_pos + 1)
        if direction == "low":
            smask = low[sl] < price - tol_atr * atr_arr[sl]
        else:
            smask = high[sl] > price + tol_atr * atr_arr[sl]
        sweep_positions = np.flatnonzero(smask) + known_pos

        age = int(bar_pos - origin_pos)
        first_sweep = int(sweep_positions[0]) if len(sweep_positions) else None

        if first_sweep is not None:
            sweep_state, status = "swept", "expired"
        elif age > max_age:
            sweep_state, status = "invalidated", "expired"
        else:
            sweep_state, status = "active", "active"

        rows.append({
            "level_id": level["level_id"],
            "level_type": level_type,
            "direction": direction,
            "price": price,
            "known_at": level["known_at"],
            "touch_count": touch_count,
            "last_touch_time": pd.NaT,
            "age_bars": age,
            "bars_since_last_touch": np.nan,
            "sweep_state": sweep_state,
            "status": status,
            "first_swept_at": ts[first_sweep] if first_sweep is not None else pd.NaT,
            "invalidated_at": pd.NaT,
        })
    return pd.DataFrame(rows, columns=LEVEL_STATE_COLUMNS)