"""Higher-timeframe context features."""
from __future__ import annotations
import pandas as pd

from src.data.resampler import (
    closed_higher_timeframe_merge,
    previous_day_high_low,
)
from src.indicators.trend import ema_slope_sign


def build_htf_context(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    h1 = closed_higher_timeframe_merge(out, "1h", "h1", columns=("close",))
    h4 = closed_higher_timeframe_merge(out, "4h", "h4", columns=("close",))

    h1_close = h1["h1_close"]
    h1_return = h1_close.pct_change()
    h1_trend = ema_slope_sign(h1_close, period=50, lag=1)

    h4_close = h4["h4_close"]
    h4_trend = ema_slope_sign(h4_close, period=50, lag=1)

    pdh = previous_day_high_low(out)

    return pd.DataFrame(
        {
            "h1_return": h1_return,
            "h1_trend": h1_trend,
            "h4_trend": h4_trend,
            "prev_day_high": pdh["prev_day_high"],
            "prev_day_low": pdh["prev_day_low"],
        },
        index=out.index,
    )


__all__ = ["build_htf_context"]