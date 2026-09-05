"""Average True Range (ATR) and causal volatility indicators."""
from __future__ import annotations
import numpy as np
import pandas as pd

_OHLC = ("open", "high", "low", "close")


def _validate_ohlc(df: pd.DataFrame, func: str) -> None:
    missing = [c for c in _OHLC if c not in df.columns]
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
    non_numeric = [c for c in _OHLC if not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric:
        raise TypeError(f"{func}: columns {non_numeric} must be numeric")


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    _validate_ohlc(out, "add_atr")
    if period < 1:
        raise ValueError(f"add_atr: period must be >= 1, got {period}")

    previous_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous_close).abs(),
            (out["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    out["true_range"] = true_range
    out["atr"] = true_range.rolling(period).mean()
    return out


def atr_percentile_causal(atr: pd.Series, window: int = 200) -> pd.Series:
    if window < 1:
        raise ValueError(f"atr_percentile_causal: window must be >= 1, got {window}")
    return atr.rolling(window).rank(pct=True)


def volatility_regime_causal(
    atr_percentile: pd.Series,
    low: float = 0.33,
    high: float = 0.67,
) -> pd.Series:
    out = pd.Series("normal", index=atr_percentile.index, dtype="object")
    out[atr_percentile < low] = "low"
    out[atr_percentile > high] = "high"
    out[atr_percentile.isna()] = np.nan
    return out