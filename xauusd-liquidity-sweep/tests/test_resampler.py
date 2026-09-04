"""Unit tests for causal resampling utilities (src/data/resampler.py).

The guide (sections 3.1 and 13.7) requires that higher-timeframe context only
ever uses *closed* candles: an H1/H4 candle closing at time ``c`` must not be
visible to an M15 row at ``c`` — only from the next M15 bar onward.  These
tests pin that behavior, including the truncated-vs-full prefix invariance
(no-lookahead check, guide section 25.2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.resampler import (
    closed_higher_timeframe_merge,
    previous_day_high_low,
    resample_ohlcv,
)

_MAX_BARS = 2000


def make_m15(n: int = 96 * 3, start: str = "2023-01-02 01:00") -> pd.DataFrame:
    """A continuous M15 frame (UTC index) of ``n`` bars.

    Generation is prefix-stable: a fixed stream of ``_MAX_BARS`` is drawn
    once and sliced, so ``make_m15(n)`` and ``make_m15(m)`` with ``n < m``
    share identical first ``n`` rows.  (numpy's ``Generator`` is not
    size-prefix-stable, so drawing per-call would break the truncation
    no-lookahead tests.)
    """
    assert n <= _MAX_BARS
    idx = pd.date_range(start, periods=_MAX_BARS, freq="15min", tz="UTC")
    rng = np.random.default_rng(7)
    open_ = 2000.0 + np.cumsum(rng.normal(0, 0.5, _MAX_BARS))
    close = open_ + rng.normal(0, 0.3, _MAX_BARS)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.2, _MAX_BARS))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.2, _MAX_BARS))
    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100, 2000, _MAX_BARS).astype("float64"),
        },
        index=idx,
    )
    return df.iloc[:n]


# --------------------------------------------------------------------------
# resample_ohlcv
# --------------------------------------------------------------------------


def test_resample_ohlcv_aggregates_correctly():
    df = make_m15(96)  # 1 day of M15 -> 24 H1 bars
    h1 = resample_ohlcv(df.iloc[:24], "1h")  # 2 H1 bars over 6 hours... use 24 bars
    h1 = resample_ohlcv(df, "1h")
    assert len(h1) == 24
    assert h1.index.freq is None or h1.index.freq is not None
    # First H1 bar: open = first M15 open, high = max, close = last.
    first = df.iloc[:4]
    assert h1.iloc[0]["open"] == first["open"].iloc[0]
    assert h1.iloc[0]["high"] == first["high"].max()
    assert h1.iloc[0]["low"] == first["low"].min()
    assert h1.iloc[0]["close"] == first["close"].iloc[-1]
    assert h1.iloc[0]["volume"] == first["volume"].sum()


def test_resample_ohlcv_keeps_partial_last_bin_as_own_row():
    df = make_m15(98)
    h1 = resample_ohlcv(df, "1h")
    # 98 bars = 24 full H1 bins (96 bars) + 2 leftover M15 bars.  pandas
    # resample keeps the partial bin as its own row; it must NOT be merged
    # into another bin (no look-ahead across bins).
    assert len(h1) == 25
    # The last bin only aggregates the 2 leftover bars.
    assert h1.iloc[-1]["volume"] == df.iloc[96:]["volume"].sum()
    assert h1.iloc[-1]["open"] == df.iloc[96]["open"]


def test_resample_ohlcv_respects_weekend_gaps():
    # M15 data with a weekend gap: a synthetic H1 aggregate must never mix
    # Friday and Monday data into one candle.  pandas groups by bin, so the
    # Friday 23:45 row stays in its own (partial) bin.
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2023-01-06 23:45", tz="UTC"),
            pd.Timestamp("2023-01-09 01:00", tz="UTC"),
            pd.Timestamp("2023-01-09 01:15", tz="UTC"),
        ]
    )
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0, 2.0],
            "high": [2.0, 3.0, 3.0],
            "low": [0.5, 1.5, 1.5],
            "close": [1.5, 2.5, 2.5],
            "volume": [10.0, 20.0, 20.0],
        },
        index=idx,
    )
    h1 = resample_ohlcv(df, "1h")
    # Two bins: Friday 23:00 (partial, 1 row) and Monday 01:00 (2 rows).
    assert len(h1) == 2
    # The Monday candle must not contain Friday data.
    mon = h1.loc[pd.Timestamp("2023-01-09 01:00", tz="UTC")]
    assert mon["open"] == 2.0 and mon["close"] == 2.5 and mon["volume"] == 40.0


# --------------------------------------------------------------------------
# closed_higher_timeframe_merge — causality
# --------------------------------------------------------------------------


def test_higher_tf_values_are_only_visible_after_close():
    df = make_m15(240)  # 10 hours
    merged = closed_higher_timeframe_merge(df, "1h", "h1")

    # The first H1 bar closes at 02:00 (bars 01:00..01:45).  Its value must
    # NOT be visible in the M15 row at 02:00 -> only from 02:15.
    h1_close = pd.Timestamp("2023-01-02 02:00", tz="UTC")
    last_m15_in_h1 = pd.Timestamp("2023-01-02 01:45", tz="UTC")
    row_at_close = merged.loc[h1_close]
    assert pd.isna(row_at_close["h1_close"])

    row_after = merged.loc[pd.Timestamp("2023-01-02 02:15", tz="UTC")]
    assert not pd.isna(row_after["h1_close"])
    # The H1 candle closing at 02:00 aggregates M15 bars 01:00..01:45, so its
    # close equals the M15 close at 01:45 (not the 02:00 bar).
    assert row_after["h1_close"] == df.loc[last_m15_in_h1]["close"]


def test_higher_tf_merge_prefix_invariance_no_lookahead():
    """Guide 25.2: features at t must be identical when computed on a
    truncated frame vs the full frame.

    The truncated input is a *slice of the same parent frame*
    (``partial = full.iloc[:N]``), so any difference can only come from the
    merge logic leaking future data — never from fixture mismatch.
    """
    parent = make_m15(480)
    merged_full = closed_higher_timeframe_merge(parent, "1h", "h1")

    truncated = parent.iloc[:240]
    merged_partial = closed_higher_timeframe_merge(truncated, "1h", "h1")

    cols = ["h1_open", "h1_high", "h1_low", "h1_close", "h1_volume"]
    pd.testing.assert_frame_equal(
        merged_full.iloc[:240][cols],
        merged_partial[cols],
        check_dtype=False,
    )


def test_higher_tf_merge_no_future_within_forming_h1():
    """A further causality probe: after an H1 candle closes, its value is
    frozen.  It must not change when later candles (same H1 bin) are added —
    i.e. the merge never re-reads a forming bin's final aggregates."""
    parent = make_m15(240)
    closed_at_0200 = closed_higher_timeframe_merge(parent, "1h", "h1")
    # Freeze the value visible at 02:15 (the first closed H1 candle).
    frozen = closed_at_0200.loc[pd.Timestamp("2023-01-02 02:15", tz="UTC"), "h1_close"]

    # Extend the frame by 2 more hours; the merge of the first 2 hours of
    # bars must not change at all.
    extended = make_m15(336)
    merged_ext = closed_higher_timeframe_merge(extended, "1h", "h1")
    same = merged_ext.loc[pd.Timestamp("2023-01-02 02:15", tz="UTC"), "h1_close"]
    assert same == frozen


def test_higher_tf_merge_columns_complete():
    df = make_m15(240)
    merged = closed_higher_timeframe_merge(df, "1h", "h1")
    for col in ["h1_open", "h1_high", "h1_low", "h1_close", "h1_volume"]:
        assert col in merged.columns
    # Values must never exceed the candle range they aggregate.
    assert (merged["h1_high"].dropna() >= merged["h1_low"].dropna()).all()


# --------------------------------------------------------------------------
# previous_day_high_low — causality
# --------------------------------------------------------------------------


def test_previous_day_high_low_causal():
    df = make_m15(96)  # one full UTC day (01:00.. next day 00:45 in UTC terms
    # but our convention: 96 bars starting 2023-01-02 01:00 -> covers
    # 2023-01-02 01:00 .. 2023-01-03 00:45).
    out = previous_day_high_low(df)

    day1 = out.index.normalize() == pd.Timestamp("2023-01-02")
    day2 = out.index.normalize() == pd.Timestamp("2023-01-03")

    # Day 2023-01-03 rows must know 2023-01-02's high/low...
    if day2.any():
        assert (out.loc[day2, "prev_day_high"] == df.loc[day1, "high"].max()).all()
    # ...while the very first day has no previous day.
    assert out.loc[day1, "prev_day_high"].isna().all()


def test_previous_day_high_low_prefix_invariance():
    parent = make_m15(192)
    out_full = previous_day_high_low(parent)
    out_partial = previous_day_high_low(parent.iloc[:96])
    cols = ["prev_day_high", "prev_day_low"]
    pd.testing.assert_frame_equal(
        out_full.iloc[:96][cols],
        out_partial[cols],
        check_dtype=False,
    )


def test_previous_day_high_low_first_day_nan():
    """The very first calendar day has no prior day: prev_day cols are NaN,
    and no row is polluted by its own day's aggregates."""
    df = make_m15(192)
    out = previous_day_high_low(df)
    day1 = out.index.normalize() == pd.Timestamp("2023-01-02")
    assert out.loc[day1, "prev_day_high"].isna().all()
    assert out.loc[day1, "prev_day_low"].isna().all()


def test_previous_day_high_low_uses_only_prior_calendar_day():
    """Causality pin with a controlled frame: each day has a distinct,
    clearly separated high band, so the value attached to any row must be
    the *prior* day's max high — never the current row's day."""
    idx = pd.date_range("2023-01-02 01:00", periods=96 * 4, freq="15min", tz="UTC")
    day_no = (idx.normalize() - pd.Timestamp("2023-01-02", tz="UTC")).days
    # Day k: highs in band (k*1000 + 5 .. k*1000 + 14).
    base = day_no * 1000.0
    df = pd.DataFrame(
        {
            "open": base + 500.0,
            "high": base + 5.0 + (np.arange(len(idx)) % 10),
            "low": base - 5.0,
            "close": base + 500.5,
            "volume": 1.0,
        },
        index=idx,
    )
    out = previous_day_high_low(df)
    for i, ts in enumerate(out.index):
        k = (ts.normalize() - pd.Timestamp("2023-01-02", tz="UTC")).days
        if k == 0:
            assert pd.isna(out["prev_day_high"].iloc[i])
        else:
            # Must be prior day's band, never the current day's band.
            assert (k - 1) * 1000 <= out["prev_day_high"].iloc[i] < k * 1000
            assert out["prev_day_high"].iloc[i] == (k - 1) * 1000 + 14.0