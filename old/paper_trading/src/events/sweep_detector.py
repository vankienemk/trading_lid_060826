"""Baseline liquidity-sweep detector."""
from __future__ import annotations
import numpy as np
import pandas as pd

from src.indicators.atr import add_atr
from src.liquidity.rolling_levels import rolling_level_at

BULLISH = "bullish"
BEARISH = "bearish"

_EVENT_COLUMNS = [
    "event_id", "event_time", "direction", "level_id", "level_price",
    "event_open", "event_high", "event_low", "event_close",
    "penetration_atr", "wick_ratio", "reclaim_atr",
]


def add_sweep_features(df: pd.DataFrame, atr_period: int = 14, level_lookback: int = 20) -> pd.DataFrame:
    if atr_period < 1:
        raise ValueError(f"add_sweep_features: atr_period must be >= 1, got {atr_period}")
    if level_lookback < 1:
        raise ValueError(f"add_sweep_features: level_lookback must be >= 1, got {level_lookback}")

    out = add_atr(df, period=atr_period)
    liq_low, liq_high = rolling_level_at(out, lookback=level_lookback)
    out["liq_low"] = liq_low
    out["liq_high"] = liq_high

    candle_range = (out["high"] - out["low"]).replace(0, np.nan)
    lower_body = out[["open", "close"]].min(axis=1)
    upper_body = out[["open", "close"]].max(axis=1)

    out["lower_wick"] = lower_body - out["low"]
    out["upper_wick"] = out["high"] - upper_body
    out["lower_wick_ratio"] = out["lower_wick"] / candle_range
    out["upper_wick_ratio"] = out["upper_wick"] / candle_range

    out["low_pen_atr"] = (out["liq_low"] - out["low"]) / out["atr"]
    out["high_pen_atr"] = (out["high"] - out["liq_high"]) / out["atr"]
    out["low_reclaim_atr"] = (out["close"] - out["liq_low"]) / out["atr"]
    out["high_reclaim_atr"] = (out["liq_high"] - out["close"]) / out["atr"]
    return out


def detect_sweeps(df: pd.DataFrame, atr_period: int = 14, level_lookback: int = 20,
                  min_penetration_atr: float = 0.05, max_penetration_atr: float = 0.50,
                  min_wick_ratio: float = 0.35, min_reclaim_atr: float = 0.0) -> pd.DataFrame:
    if max_penetration_atr < min_penetration_atr:
        raise ValueError(f"detect_sweeps: max_penetration_atr ({max_penetration_atr}) must be >= min_penetration_atr ({min_penetration_atr})")
    if min_penetration_atr < 0.0:
        raise ValueError(f"detect_sweeps: min_penetration_atr must be >= 0, got {min_penetration_atr}")
    if not 0.0 <= min_wick_ratio <= 1.0:
        raise ValueError(f"detect_sweeps: min_wick_ratio must be in [0, 1], got {min_wick_ratio}")

    out = add_sweep_features(df, atr_period=atr_period, level_lookback=level_lookback)

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
    return out


def _candidate_rows(out: pd.DataFrame) -> pd.DataFrame:
    flags = out["sweep_long"].fillna(False) | out["sweep_short"].fillna(False)
    positions = np.flatnonzero(flags.to_numpy())
    rows = []
    for pos in positions:
        if bool(out["sweep_long"].iloc[pos]):
            rows.append(_candidate_row(out, pos, BULLISH))
        if bool(out["sweep_short"].iloc[pos]):
            rows.append(_candidate_row(out, pos, BEARISH))
    return pd.DataFrame(rows, columns=["position", *_EVENT_COLUMNS[1:]])


def _candidate_row(out: pd.DataFrame, pos: int, direction: str) -> dict:
    if direction == BULLISH:
        level_id, level_price = "rolling_low", out["liq_low"].iloc[pos]
        penetration = out["low_pen_atr"].iloc[pos]
        wick = out["lower_wick_ratio"].iloc[pos]
        reclaim = out["low_reclaim_atr"].iloc[pos]
    else:
        level_id, level_price = "rolling_high", out["liq_high"].iloc[pos]
        penetration = out["high_pen_atr"].iloc[pos]
        wick = out["upper_wick_ratio"].iloc[pos]
        reclaim = out["high_reclaim_atr"].iloc[pos]
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
    }