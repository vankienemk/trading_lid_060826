"""Rolling high/low liquidity levels."""
from __future__ import annotations
import pandas as pd


def _validate_frame(df: pd.DataFrame, func: str, lookback: int) -> None:
    missing = [c for c in ("high", "low") if c not in df.columns]
    if missing:
        raise ValueError(f"{func}: missing required columns {missing}")
    if len(df) == 0:
        raise ValueError(f"{func}: empty DataFrame")
    if not df.index.is_monotonic_increasing:
        raise ValueError(
            f"{func}: index must be chronologically sorted (monotonic "
            "increasing); an unsorted frame breaks causal (no-look-ahead) "
            "semantics"
        )
    non_numeric = [c for c in ("high", "low") if not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric:
        raise TypeError(f"{func}: columns {non_numeric} must be numeric")
    if lookback < 1:
        raise ValueError(f"{func}: lookback must be >= 1, got {lookback}")


def rolling_level_at(df: pd.DataFrame, lookback: int = 20) -> tuple[pd.Series, pd.Series]:
    _validate_frame(df, "rolling_level_at", lookback)
    liq_low = df["low"].shift(1).rolling(lookback).min()
    liq_high = df["high"].shift(1).rolling(lookback).max()
    liq_low.name = "liq_low"
    liq_high.name = "liq_high"
    return liq_low, liq_high