"""Resampling utilities for higher-timeframe context features."""
from __future__ import annotations
import pandas as pd


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    cols = [c for c in agg if c in df.columns]
    return df[cols].resample(rule).agg({c: agg[c] for c in cols}).dropna(subset=["close"])


def closed_higher_timeframe_merge(
    df: pd.DataFrame,
    rule: str,
    prefix: str,
    columns: tuple = ("open", "high", "low", "close", "volume"),
) -> pd.DataFrame:
    ht = resample_ohlcv(df, rule)
    ht.index = ht.index + pd.Timedelta(rule)
    out = df.copy()
    for col in columns:
        if col not in ht.columns:
            continue
        series = ht[col]
        mapped = series.reindex(out.index, method="ffill")
        out[f"{prefix}_{col}"] = mapped.shift(1)
    return out


def previous_day_high_low(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = out.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("previous_day_high_low requires a DatetimeIndex frame")
    day = idx.normalize()

    daily = pd.DataFrame(
        {
            "high": out["high"].groupby(day).max(),
            "low": out["low"].groupby(day).min(),
        }
    )
    prev = daily.shift(1)

    day_values = pd.Series(day, index=out.index)
    out["prev_day_high"] = prev["high"].reindex(day_values.to_numpy()).to_numpy()
    out["prev_day_low"] = prev["low"].reindex(day_values.to_numpy()).to_numpy()
    return out