"""Volume indicators (all causal)."""
from __future__ import annotations
import numpy as np
import pandas as pd


def volume_zscore(volume: pd.Series, window: int = 50) -> pd.Series:
    """Rolling z-score of volume over the *past* ``window`` values."""
    past_mean = volume.shift(1).rolling(window).mean()
    past_std = volume.shift(1).rolling(window).std()
    return (volume - past_mean) / past_std.replace(0, np.nan)


def volume_percentile_causal(volume: pd.Series, window: int = 200) -> pd.Series:
    """Causal rolling percentile of volume."""
    return volume.rolling(window).rank(pct=True)