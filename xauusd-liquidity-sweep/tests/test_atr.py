"""Causal-behavior unit tests for ``src.indicators.atr``.

The no-look-ahead proof used here is the *prefix invariant*: for every row
``t``, the value computed on the full frame must equal the value computed on
the prefix ``df.iloc[:t+1]``.  If a computation ever used future rows, the two
would diverge somewhere.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.indicators.atr import (
    add_atr,
    atr_percentile_causal,
    volatility_regime_causal,
)

RAW_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "raw",
    "XAUUSD_M15_202206020915_202608272245.csv",
)


def _synthetic(n: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1800 + np.cumsum(rng.normal(0.0, 1.0, n))
    spread = np.abs(rng.normal(0.8, 0.3, n))
    high = close + spread
    low = close - spread
    open_ = close - rng.normal(0.0, 0.2, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def _prefix_invariant(builder, col, df, atol: float = 1e-12) -> None:
    """Assert ``builder(df)``[col][t] == last row of ``builder(df[:t+1])``[col]."""
    full = builder(df)
    for t in range(len(df)):
        prefix = builder(df.iloc[: t + 1])
        a, b = full[col].iloc[t], prefix[col].iloc[-1]
        if pd.isna(a) or pd.isna(b):
            assert pd.isna(a) and pd.isna(b), (
                f"{col}[{t}]: NaN mismatch (full={a!r}, prefix={b!r})"
            )
        else:
            assert np.isclose(a, b, atol=atol), (
                f"{col}[{t}]: full={a!r} but prefix recompute={b!r} "
                "-- future data leaked"
            )


# ---------------------------------------------------------------------------
# add_atr
# ---------------------------------------------------------------------------

def test_add_atr_matches_hand_computed():
    """Guide section 8 formulas verified by hand on a tiny frame."""
    df = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
            "high": [11.0, 12.0, 13.0, 14.0, 15.0, 16.0],
            "low": [9.0, 10.0, 11.0, 12.0, 13.0, 14.0],
            "close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        }
    )
    out = add_atr(df, period=2)

    # True range: row 0 has no previous close -> high-low only.
    expected_tr = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    np.testing.assert_allclose(out["true_range"], expected_tr)

    # ATR: rolling mean of TR over 2 bars; first row NaN (window not full).
    assert pd.isna(out["atr"].iloc[0])
    np.testing.assert_allclose(out["atr"].iloc[1:], [2.0, 2.0, 2.0, 2.0, 2.0])


def test_true_range_uses_previous_close_only():
    """TR[t] must use close[t-1], never close[t] or close[t+1]."""
    df = pd.DataFrame(
        {
            "open": [10.0, 10.0, 10.0],
            "high": [10.0, 13.0, 10.0],
            "low": [10.0, 10.0, 10.0],
            "close": [10.0, 10.0, 10.0],
        }
    )
    out = add_atr(df, period=2)
    # Row 1: gap vs previous close (10) -> |13-10| = 3 dominates.
    assert np.isclose(out["true_range"].iloc[1], 3.0)
    # Row 2: gap vs close[1]=10 -> |10-10| = 0, range = 0.
    assert np.isclose(out["true_range"].iloc[2], 0.0)


def test_atr_nan_warmup_no_future_fill():
    """First period-1 rows are NaN; no warm-start value uses future data."""
    df = _synthetic()
    out = add_atr(df, period=14)
    assert out["atr"].iloc[:13].isna().all()
    assert out["atr"].iloc[13:].notna().all()


def test_add_atr_causal_prefix_invariant():
    """The core no-look-ahead proof for true_range and atr."""
    df = _synthetic(n=45)
    for col in ("true_range", "atr"):
        _prefix_invariant(add_atr, col, df)


def test_add_atr_does_not_mutate_input():
    df = _synthetic(n=30)
    original = df.copy()
    add_atr(df, period=14)
    pd.testing.assert_frame_equal(df, original)


def test_add_atr_period_validation():
    with pytest.raises(ValueError, match="period"):
        add_atr(_synthetic(), period=0)


def test_add_atr_requires_columns():
    df = _synthetic().drop(columns=["close"])
    with pytest.raises(ValueError, match="close"):
        add_atr(df)


def test_add_atr_empty_frame():
    with pytest.raises(ValueError, match="empty"):
        add_atr(pd.DataFrame(columns=["open", "high", "low", "close"]))


def test_add_atr_unsorted_index_raises():
    """A shuffled frame must fail loudly instead of leaking data."""
    df = _synthetic().sample(frac=1.0, random_state=1)
    with pytest.raises(ValueError, match="monotonic"):
        add_atr(df)


# ---------------------------------------------------------------------------
# atr_percentile_causal / volatility_regime_causal
# ---------------------------------------------------------------------------

def test_atr_percentile_causal_prefix_invariant():
    df = _synthetic(n=45)

    def builder(x: pd.DataFrame) -> pd.DataFrame:
        o = add_atr(x, period=14)
        o["atr_pct"] = atr_percentile_causal(o["atr"], window=20)
        return o

    _prefix_invariant(builder, "atr_pct", df)


def test_atr_percentile_causal_window_behavior():
    """Percentile at t ranks over the trailing window including t."""
    atr = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    pct = atr_percentile_causal(atr, window=4)
    # Warmup: first 3 rows NaN.
    assert pct.iloc[:3].isna().all()
    # Window [3,4,5,6] -> 6.0 is the max -> pct 1.0.
    assert np.isclose(pct.iloc[5], 1.0)
    # Window [5,6,7,8] -> 8.0 is the max.
    assert np.isclose(pct.iloc[7], 1.0)
    # 4.0 within [1..4] is the max of its window as well.
    assert np.isclose(pct.iloc[3], 1.0)


def test_volatility_regime_causal_mapping():
    pct = pd.Series([0.10, 0.50, 0.90, np.nan])
    regime = volatility_regime_causal(pct, low=0.33, high=0.67)
    assert regime.iloc[0] == "low"
    assert regime.iloc[1] == "normal"
    assert regime.iloc[2] == "high"
    assert pd.isna(regime.iloc[3])


# ---------------------------------------------------------------------------
# Real-data smoke (skips when the raw export is absent)
# ---------------------------------------------------------------------------

def test_real_data_atr_smoke():
    if not os.path.exists(RAW_CSV):
        pytest.skip("raw MT5 CSV not present")
    from src.data.loader import load_ohlcv, normalize_ohlcv

    df = normalize_ohlcv(load_ohlcv(RAW_CSV))
    out = add_atr(df, period=14)
    assert len(out) == len(df)
    assert out["atr"].iloc[:13].isna().all()
    assert out["atr"].iloc[13:].notna().all()
    # True range is by definition non-negative; dead candles may make it 0,
    # but real XAUUSD activity keeps the average clearly positive.
    assert out["true_range"].dropna().ge(0).all()
    assert out["atr"].dropna().ge(0).all()
    assert out["true_range"].dropna().mean() > 0
    assert out["atr"].dropna().mean() > 0
    assert np.isclose(
        out["true_range"].iloc[0],
        df["high"].iloc[0] - df["low"].iloc[0],
    )