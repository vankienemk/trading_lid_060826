"""Causal-behavior unit tests for the liquidity level stack (Phases 1 + 4).

Rolling levels (t3): the no-look-ahead proof is the *prefix invariant* — for
every row ``t`` the level computed on the full frame equals the level computed
on the prefix ``df.iloc[:t+1]``; a current-candle-exclusion test verifies the
level-building candle is never the candle being measured.

Swing / equal / previous-day / registry (t8): every level must carry
``known_at`` (swing: ``t + right_bars``; equal: the completing touch; previous
day: first bar of the next day; H1: first M15 bar after the confirming H1
candle closes) and must never be *usable* before it.  Per-bar causal state is
verified through ``level_state_at_bar`` against registry recomputation on
truncated prefixes.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.indicators.atr import add_atr
from src.liquidity.equal_levels import detect_equal_levels
from src.liquidity.level_registry import (
    build_liquidity_levels,
    detect_previous_day_levels,
    level_state_at_bar,
)
from src.liquidity.rolling_levels import (
    add_rolling_liquidity_levels,
    rolling_level_at,
)
from src.liquidity.swing_levels import detect_h1_swing_levels, detect_swing_levels
from src.schema import (
    LEVEL_COLUMNS,
    LEVEL_DIRECTIONS,
    LEVEL_REGISTRY_COLUMNS,
    LEVEL_STATE_COLUMNS,
    LEVEL_STATUSES,
    LEVEL_TYPES,
)

RAW_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "raw",
    "XAUUSD_M15_202206020915_202608272245.csv",
)


def _synthetic(n: int = 60, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1800 + np.cumsum(rng.normal(0.0, 1.0, n))
    spread = np.abs(rng.normal(0.8, 0.3, n))
    return pd.DataFrame(
        {"open": close, "high": close + spread, "low": close - spread, "close": close}
    )


def _utc_frame(ohlc: dict) -> pd.DataFrame:
    """Build a tz-aware UTC-indexed OHLC frame from column arrays."""
    n = len(next(iter(ohlc.values())))
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(ohlc, index=idx)


def _frame_with_atr(n: int = 60, seed: int = 5) -> tuple[pd.DataFrame, pd.Series]:
    df = _synthetic(n=n, seed=seed)
    df.index = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    atr = add_atr(df, period=14)["atr"]
    return df, atr


def _prefix_invariant(lookback: int, col: str, df: pd.DataFrame, atol: float = 1e-12) -> None:
    """Assert full-frame level[t] == last row of prefix frame level."""
    full = add_rolling_liquidity_levels(df, lookback=lookback)
    for t in range(len(df)):
        prefix = add_rolling_liquidity_levels(df.iloc[: t + 1], lookback=lookback)
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
# Correctness of the level definition
# ---------------------------------------------------------------------------

def test_rolling_levels_hand_computed():
    """liq_low[t] = min(low[t-lookback:t]); liq_high[t] = max(high[t-lookback:t])."""
    df = _synthetic(n=30)
    out = add_rolling_liquidity_levels(df, lookback=5)
    for t in range(5, 30):
        assert np.isclose(out["liq_low"].iloc[t], df["low"].iloc[t - 5 : t].min())
        assert np.isclose(out["liq_high"].iloc[t], df["high"].iloc[t - 5 : t].max())


def test_current_candle_never_participates():
    """The extreme at candle t must not move the level at candle t."""
    n = 40
    close = np.linspace(100.0, 140.0, n)
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
    )
    # Candle 25 spikes far outside both prior ranges (new low AND new high).
    df.loc[25, "low"] = 0.0
    df.loc[25, "high"] = 300.0

    out = add_rolling_liquidity_levels(df, lookback=20)

    # Row 25 must NOT see its own extremes: level = extrema of low[25-20:25].
    assert np.isclose(out["liq_low"].iloc[25], df["low"].iloc[5:25].min())
    assert out["liq_low"].iloc[25] > df["low"].iloc[25]
    assert np.isclose(out["liq_high"].iloc[25], df["high"].iloc[5:25].max())
    assert out["liq_high"].iloc[25] < df["high"].iloc[25]
    # Row 26's window is [6:26], which DOES include the spike at index 25.
    assert np.isclose(out["liq_low"].iloc[26], 0.0)
    assert np.isclose(out["liq_high"].iloc[26], 300.0)


def test_no_center_and_no_missing_shift():
    """Compare against a center=True / no-shift leak: ours must differ."""
    lows = np.linspace(100.0, 50.0, 60)  # strictly decreasing lows
    df = pd.DataFrame(
        {
            "open": lows,
            "high": lows + 1.0,
            "low": lows,
            "close": lows,
        }
    )
    out = add_rolling_liquidity_levels(df, lookback=20)
    leaking = df["low"].rolling(20, center=True).min()  # includes future lows

    for t in (30, 40, 50):
        assert out["liq_low"].iloc[t] > leaking.iloc[t], (
            f"row {t}: implementation matches a center=True leak "
            "(future lows pulled the level down)"
        )
    assert not np.array_equal(
        out["liq_low"].iloc[20:].to_numpy(), leaking.iloc[20:].to_numpy()
    )


def test_nan_warmup_first_lookback_rows():
    """A level needs a full window of prior candles: first lookback rows are NaN."""
    df = _synthetic(n=30)
    out = add_rolling_liquidity_levels(df, lookback=20)
    assert out["liq_low"].iloc[:20].isna().all()
    assert out["liq_low"].iloc[20:].notna().all()
    assert out["liq_high"].iloc[:20].isna().all()
    assert out["liq_high"].iloc[20:].notna().all()


def test_liq_high_above_liq_low():
    df = _synthetic(n=50)
    out = add_rolling_liquidity_levels(df, lookback=20)
    finite = out["liq_high"].notna() & out["liq_low"].notna()
    assert (out.loc[finite, "liq_high"] >= out.loc[finite, "liq_low"]).all()


# ---------------------------------------------------------------------------
# Causality (the module's highest-risk property)
# ---------------------------------------------------------------------------

def test_rolling_levels_causal_prefix_invariant():
    df = _synthetic(n=45)
    for col in ("liq_low", "liq_high"):
        _prefix_invariant(20, col, df)


def test_rolling_levels_causal_prefix_invariant_small_lookback():
    """Invariant holds for non-default lookback too."""
    df = _synthetic(n=30)
    for col in ("liq_low", "liq_high"):
        _prefix_invariant(3, col, df)


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

def test_rolling_level_at_matches_add():
    df = _synthetic(n=40)
    out = add_rolling_liquidity_levels(df, lookback=20)
    lo, hi = rolling_level_at(df, lookback=20)
    pd.testing.assert_series_equal(out["liq_low"], lo)
    pd.testing.assert_series_equal(out["liq_high"], hi)


def test_input_not_mutated():
    df = _synthetic(n=30)
    original = df.copy()
    add_rolling_liquidity_levels(df, lookback=20)
    pd.testing.assert_frame_equal(df, original)


def test_validation_errors():
    df = _synthetic(n=30)
    with pytest.raises(ValueError, match="lookback"):
        add_rolling_liquidity_levels(df, lookback=0)
    with pytest.raises(ValueError, match="low"):
        add_rolling_liquidity_levels(df.drop(columns=["low"]), lookback=20)
    with pytest.raises(ValueError, match="empty"):
        add_rolling_liquidity_levels(pd.DataFrame(columns=["high", "low"]))
    shuffled = df.sample(frac=1.0, random_state=0)
    with pytest.raises(ValueError, match="monotonic"):
        add_rolling_liquidity_levels(shuffled, lookback=20)


def test_returns_pure_float_levels():
    """Levels must be true numbers market data can be compared against."""
    df = _synthetic(n=30)
    out = add_rolling_liquidity_levels(df, lookback=5)
    assert pd.api.types.is_float_dtype(out["liq_low"])
    assert pd.api.types.is_float_dtype(out["liq_high"])


# ---------------------------------------------------------------------------
# Real-data smoke (skips when the raw export is absent)
# ---------------------------------------------------------------------------

def test_real_data_rolling_levels_smoke():
    if not os.path.exists(RAW_CSV):
        pytest.skip("raw MT5 CSV not present")
    from src.data.loader import load_ohlcv, normalize_ohlcv

    df = normalize_ohlcv(load_ohlcv(RAW_CSV))
    out = add_rolling_liquidity_levels(df, lookback=20)
    assert len(out) == len(df)
    assert out["liq_low"].iloc[:20].isna().all()
    assert out["liq_low"].iloc[20:].notna().all()
    assert out["liq_high"].iloc[20:].notna().all()
    assert (out.loc[out["liq_high"].notna(), "liq_high"] >=
            out.loc[out["liq_low"].notna(), "liq_low"]).all()
    # Sanity: the rolling channel width is plausible for XAUUSD M15.
    width = (out["liq_high"] - out["liq_low"]).dropna()
    assert width.gt(0).all()
    assert width.quantile(0.99) < 200.0


# ---------------------------------------------------------------------------
# Swing levels (guide §9.2): known_at = t + right_bars
# ---------------------------------------------------------------------------

def _swing_frame() -> pd.DataFrame:
    """One strict swing low at bar 5 (price 90) and swing high at bar 7 (130)."""
    lows = [105, 104, 103, 102, 101, 90, 102, 103, 104, 105, 106, 107]
    highs = [120] * 12
    highs[7] = 130.0
    return _utc_frame({"open": highs, "high": highs, "low": lows, "close": lows})


def test_swing_low_known_at_is_t_plus_right_bars():
    df = _swing_frame()
    out = detect_swing_levels(df, left_bars=3, right_bars=3)
    lows = out[out["direction"] == "low"]
    assert len(lows) == 1
    row = lows.iloc[0]
    assert row["level_id"] == "swing_low_5"
    assert row["level_type"] == "swing"
    assert np.isclose(row["price"], 90.0)
    assert row["origin_pos"] == 5
    assert row["known_pos"] == 8
    assert row["known_at"] == df.index[8]
    assert row["origin_time"] == df.index[5]
    assert row["status"] == "active"


def test_swing_high_detected():
    df = _swing_frame()
    out = detect_swing_levels(df, left_bars=3, right_bars=3)
    highs = out[out["direction"] == "high"]
    assert len(highs) == 1
    assert highs.iloc[0]["level_id"] == "swing_high_7"
    assert np.isclose(highs.iloc[0]["price"], 130.0)
    assert highs.iloc[0]["known_pos"] == 10


def test_swing_requires_strict_fractal():
    """Equal neighbours must NOT confirm a swing (guide: 'lower than')."""
    lows = [105, 104, 103, 96, 95, 95, 96, 103, 104, 105, 106, 107]
    highs = [120] * 12
    df = _utc_frame({"open": highs, "high": highs, "low": lows, "close": lows})
    out = detect_swing_levels(df, left_bars=3, right_bars=3)
    # low[4] == low[5] == 95 -> no strict pivot anywhere near.
    assert out[out["direction"] == "low"].empty


def test_swing_level_not_usable_before_known():
    df = _swing_frame()
    atr = add_atr(df, period=2)["atr"]
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 2},
            "liquidity": {
                "swing": {"enabled": True, "left_bars": 3, "right_bars": 3},
                "equal_levels": {"enabled": False},
                "h1": {"enabled": False},
                "prev_day": {"enabled": False},
            },
        },
    )
    # Bar 7: swing_low_5 (known_pos 8) must not exist yet.
    before = level_state_at_bar(registry, df, atr, 7)
    assert "swing_low_5" not in set(before["level_id"])
    # Bar 8: it appears, with its origin touch already counted.
    at = level_state_at_bar(registry, df, atr, 8)
    row = at[at["level_id"] == "swing_low_5"].iloc[0]
    assert row["touch_count"] == 1
    assert row["sweep_state"] == "active"
    assert row["status"] == "active"


def test_swing_detector_truncation_invariant():
    """Levels known within a prefix are identical on full vs prefix frames."""
    df = _swing_frame()
    full = detect_swing_levels(df, left_bars=3, right_bars=3)
    prefix = detect_swing_levels(df.iloc[:10], left_bars=3, right_bars=3)
    full_known = full[full["known_pos"] <= 9]
    pd.testing.assert_frame_equal(
        prefix.reset_index(drop=True), full_known.reset_index(drop=True)
    )


def test_swing_validation():
    df = _swing_frame()
    with pytest.raises(ValueError, match="left_bars"):
        detect_swing_levels(df, left_bars=0)
    with pytest.raises(ValueError, match="right_bars"):
        detect_swing_levels(df, right_bars=0)
    with pytest.raises(ValueError, match="monotonic"):
        detect_swing_levels(df.sample(frac=1.0, random_state=0))
    with pytest.raises(ValueError, match="low"):
        detect_swing_levels(df.drop(columns=["low"]))


# ---------------------------------------------------------------------------
# Equal highs/lows (guide §9.3): tolerance_atr=0.10, min_touches=2
# ---------------------------------------------------------------------------

def _equal_frame() -> pd.DataFrame:
    # Base prices spaced > tolerance (2.0 > 0.10*ATR=1.0) so that ONLY the
    # crafted touches at bars 25/30/35 can form clusters.
    n = 50
    lows = np.array([108.0 + 2.0 * i for i in range(n)], dtype="float64")
    highs = lows + 2.0
    lows[25] = 99.8  # touch 1 of an equal low pool
    highs[25] = 100.2
    lows[30] = 100.0  # touch 2 -> completes the low cluster
    highs[30] = 113.4  # far from 112 -> NO equal-high pool
    lows[35] = 99.9   # later touch (registry counting)
    highs[35] = 114.9  # far from 113.4 -> no high cluster either
    return _utc_frame({"open": lows, "high": highs, "low": lows, "close": lows})


def _constant_atr(n: int, value: float = 10.0) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.Series(value, index=idx, dtype="float64")


def test_equal_low_cluster_detected_causally():
    df = _equal_frame()
    out = detect_equal_levels(df, tolerance_atr=0.10, min_touches=2, atr=_constant_atr(50))
    lows = out[out["direction"] == "low"]
    assert len(lows) == 1
    row = lows.iloc[0]
    assert row["level_id"] == "equal_low_25"
    assert row["level_type"] == "equal"
    assert np.isclose(row["price_min"], 99.8)
    assert np.isclose(row["price_max"], 100.0)
    assert np.isclose(row["price"], 99.9)
    assert row["origin_pos"] == 25                  # first touch (was a bug: active[0])
    assert row["known_pos"] == 30                   # completing touch
    assert row["known_at"] == df.index[30]          # known_at = last touch
    assert row["touch_count"] == 2
    assert row["first_touch_time"] == df.index[25]
    assert row["last_touch_time"] == df.index[30]


def test_equal_high_cluster_detected():
    n = 50
    highs = np.array([105.0 + 2.0 * i for i in range(n)], dtype="float64")
    lows = highs - 2.0  # spaced the same way -> no accidental low clusters
    highs[10] = 300.4   # pair sits far above the ramp max (203) -> unique pool
    highs[11] = 300.5   # within tol of 300.4 -> equal-high pool
    lows[10] = 290.0    # far from base lows AND from each other (gap 5 > tol)
    lows[11] = 295.0
    df = _utc_frame({"open": highs, "high": highs, "low": lows, "close": highs})
    out = detect_equal_levels(df, tolerance_atr=0.10, min_touches=2, atr=_constant_atr(n))
    highs_out = out[out["direction"] == "high"]
    assert len(highs_out) == 1
    assert highs_out.iloc[0]["level_id"] == "equal_high_10"


def test_equal_requires_min_touches():
    # Only ONE touch exists -> no level, even with min_touches=2.
    n = 50
    lows = np.array([108.0 + 2.0 * i for i in range(n)], dtype="float64")
    highs = lows + 2.0
    lows[25] = 99.8
    highs[25] = 100.2
    df = _utc_frame({"open": lows, "high": highs, "low": lows, "close": lows})
    out = detect_equal_levels(
        df, tolerance_atr=0.10, min_touches=2, max_age_bars=200,
        atr=_constant_atr(n),
    )
    assert out.empty
    # Two touches within tolerance DO form a level (positive control).
    lows[30] = 100.0
    highs[30] = 100.4
    df2 = _utc_frame({"open": lows, "high": highs, "low": lows, "close": lows})
    out2 = detect_equal_levels(
        df2, tolerance_atr=0.10, min_touches=2, max_age_bars=200,
        atr=_constant_atr(n),
    )
    assert len(out2[out2["direction"] == "low"]) == 1


def test_equal_tolerance_gating():
    n = 50
    lows = np.array([108.0 + 2.0 * i for i in range(n)], dtype="float64")
    highs = lows + 2.0
    lows[25] = 99.8
    highs[25] = 100.2
    lows[30] = 100.0
    highs[30] = 100.4
    df = _utc_frame({"open": lows, "high": highs, "low": lows, "close": lows})
    # tolerance 0.01 * ATR(10) = 0.1 < |99.8 - 100.0| = 0.2 -> no cluster.
    tight = detect_equal_levels(
        df, tolerance_atr=0.01, min_touches=2, max_age_bars=200,
        atr=_constant_atr(n),
    )
    assert tight.empty
    # baseline tolerance 0.10 * ATR(10) = 1.0 -> cluster forms.
    loose = detect_equal_levels(
        df, tolerance_atr=0.10, min_touches=2, max_age_bars=200,
        atr=_constant_atr(n),
    )
    assert len(loose[loose["direction"] == "low"]) == 1


def test_equal_cluster_expires_by_age():
    n = 80
    lows = np.array([108.0 + 2.0 * i for i in range(n)], dtype="float64")
    highs = lows + 2.0
    lows[10] = 99.8
    highs[10] = 100.2
    lows[25] = 100.0   # 15 bars later, max_age_bars=10 -> cluster expired
    highs[25] = 100.4
    df = _utc_frame({"open": lows, "high": highs, "low": lows, "close": lows})
    out = detect_equal_levels(
        df, tolerance_atr=0.10, min_touches=2, max_age_bars=10,
        atr=_constant_atr(n),
    )
    assert out.empty


def test_equal_requires_atr_series():
    df = _equal_frame()
    with pytest.raises(ValueError, match="ATR"):
        detect_equal_levels(df, tolerance_atr=0.10, min_touches=2)


def test_equal_detector_truncation_invariant():
    df = _equal_frame()
    atr_full = _constant_atr(50)
    full = detect_equal_levels(df, tolerance_atr=0.10, min_touches=2, atr=atr_full)
    # Prefix of 34 bars: cluster completes at bar 30 -> present in both.
    prefix = detect_equal_levels(df.iloc[:34], tolerance_atr=0.10, min_touches=2,
                                 atr=atr_full.iloc[:34])
    full_low = full[full["direction"] == "low"]
    prefix_low = prefix[prefix["direction"] == "low"]
    assert len(full_low) == 1
    assert len(prefix_low) == 1
    assert prefix_low.iloc[0]["level_id"] == full_low.iloc[0]["level_id"]
    assert prefix_low.iloc[0]["known_pos"] == full_low.iloc[0]["known_pos"]
    # A prefix that ends before the completing touch must see nothing.
    early = detect_equal_levels(df.iloc[:28], tolerance_atr=0.10, min_touches=2,
                                atr=atr_full.iloc[:28])
    assert early.empty


def test_equal_third_touch_grows_touch_count_in_registry():
    # Build a clean frame with constant ATR for exact tolerance control.
    df = _equal_frame()
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 14},
            "liquidity": {
                "swing": {"enabled": False},
                "h1": {"enabled": False},
                "prev_day": {"enabled": False},
                "equal_levels": {
                    "enabled": True, "tolerance_atr": 0.10,
                    "min_touches": 2, "max_age_bars": 200,
                },
            },
        },
    )
    eq = registry[registry["level_id"] == "equal_low_25"]
    assert len(eq) == 1
    row = eq.iloc[0]
    assert row["level_id"] == "equal_low_25"
    # Touches at 25, 30 (cluster) + 35 (later) = 3, last at bar 35.
    assert row["touch_count"] == 3
    assert row["last_touch_time"] == df.index[35]
    assert row["sweep_state"] == "active"
    # known_at stays at the completing touch (usable from then on).
    assert row["known_at"] == df.index[30]


# ---------------------------------------------------------------------------
# Previous-day levels (Phase 4)
# ---------------------------------------------------------------------------

def _prev_day_frame() -> pd.DataFrame:
    """Two days of trading; day 2 pierces day 1's low."""
    idx = pd.DatetimeIndex(
        [
            "2026-01-01 00:15", "2026-01-01 00:30",     # day 1 (2 bars)
            "2026-01-02 00:15", "2026-01-02 00:30", "2026-01-02 00:45",  # day 2
            "2026-01-05 00:15", "2026-01-05 00:30",     # day 3 (after weekend)
        ],
        tz="UTC",
    )
    highs = [100.5, 100.2, 101.0, 101.5, 101.2, 103.0, 102.5]
    lows = [99.5, 99.8, 99.0, 99.4, 99.6, 102.0, 101.8]
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 102.5, 102.0]
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes}, index=idx)
    return df


def test_prev_day_levels_known_at_next_day():
    df = _prev_day_frame()
    out = detect_previous_day_levels(df, max_age_bars=200)
    assert len(out) == 4  # day2 (2) + day3 (2); day1 has no previous day
    # Day-2 levels reference day 1: origin = last bar of day 1 (pos 1),
    # known = first bar of day 2 (pos 2).
    high_row = out[out["level_id"] == "prev_day_high_1"].iloc[0]
    assert np.isclose(high_row["price"], 100.5)
    assert high_row["origin_pos"] == 1
    assert high_row["known_pos"] == 2
    assert high_row["known_at"] == df.index[2]
    low_row = out[out["level_id"] == "prev_day_low_1"].iloc[0]
    assert np.isclose(low_row["price"], 99.5)
    # Day-3 levels: previous trading day is day 2 (Friday -> Monday works).
    monday_high = out[out["level_id"] == "prev_day_high_4"].iloc[0]
    assert np.isclose(monday_high["price"], 101.5)
    assert monday_high["origin_pos"] == 4
    assert monday_high["known_pos"] == 5


def test_prev_day_levels_in_registry_with_sweep():
    df = _prev_day_frame()
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 2},
            "liquidity": {
                "swing": {"enabled": False},
                "h1": {"enabled": False},
                "equal_levels": {"enabled": False},
                "prev_day": {"enabled": True, "max_age_bars": 200},
            },
        },
    )
    assert set(registry["level_type"].unique()) == {"prev_day"}
    # Day 2 pierces day 1's low (99.0 < 99.5) from bar 3 -> swept.
    low_row = registry[registry["level_id"] == "prev_day_low_1"].iloc[0]
    # Touches at bars 2 (99.0) and 3 (99.4); bar 2 already penetrates 99.5.
    assert low_row["touch_count"] == 2
    assert low_row["last_touch_time"] == df.index[3]
    assert low_row["sweep_state"] == "swept"
    assert low_row["status"] == "expired"
    assert low_row["first_swept_at"] == df.index[2]


def test_prev_day_first_day_has_no_levels():
    df = _prev_day_frame()
    out = detect_previous_day_levels(df.iloc[:2], max_age_bars=200)
    assert out.empty


# ---------------------------------------------------------------------------
# H1 swing levels (Phase 4): closed candles only
# ---------------------------------------------------------------------------

def _h1_frame(partial_low: float = 1.0) -> pd.DataFrame:
    """8 hours of M15; closed H1 candle 03:00 is a strict swing low (price 90).

    Closed H1 candles: 00:00->102, 01:00->101, 02:00->100, 03:00->90,
    04:00->101, 05:00->102, 06:00->103.  The final (partial) 07:00 candle has
    lows ``partial_low`` (1.0 by default) and must never contribute a level.
    """
    n = 32
    idx = pd.date_range("2026-01-01 00:00", periods=n, freq="15min", tz="UTC")
    lows = np.concatenate(
        [
            [102.0] * 4, [101.0] * 4, [100.0] * 4, [95.0, 90.0, 92.0, 91.0],
            [101.0] * 4, [102.0] * 4, [103.0] * 4, [partial_low] * 4,
        ]
    )
    highs = lows + 2.0
    return pd.DataFrame(
        {"open": lows, "high": highs, "low": lows, "close": lows}, index=idx
    )


def test_h1_swing_level_known_at_confirming_candle_close():
    df = _h1_frame()
    out = detect_h1_swing_levels(df, left_bars=3, right_bars=3)
    lows = out[out["direction"] == "low"]
    assert len(lows) == 1
    row = lows.iloc[0]
    assert row["level_id"] == "h1_swing_low_12"       # first M15 bar of 03:00 H1
    assert np.isclose(row["price"], 90.0)
    assert bool(row["is_h1"])
    assert row["origin_pos"] == 12
    # Confirming H1 candle = 03:00 + 3 = 06:00, closes 07:00 -> M15 bar 28.
    assert row["known_pos"] == 28
    assert row["known_at"] == df.index[28]


def test_h1_partial_candle_never_contributes():
    """The forming 07:00 H1 candle (low 1.0) must not create a level."""
    df = _h1_frame()
    out = detect_h1_swing_levels(df, left_bars=3, right_bars=3)
    assert out[out["direction"] == "low"].empty or (out["price"] > 1.0).all()
    assert not (np.asarray(out["price"]) < 2.0).any()


def test_h1_level_not_usable_before_known():
    df = _h1_frame(partial_low=104.0)  # benign partial candle: no touch at 28
    atr = add_atr(df, period=2)["atr"]
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 2},
            "liquidity": {
                "swing": {"enabled": False},
                "equal_levels": {"enabled": False},
                "prev_day": {"enabled": False},
                "h1": {"enabled": True, "left_bars": 3, "right_bars": 3},
            },
        },
    )
    before = level_state_at_bar(registry, df, atr, 27)
    assert "h1_swing_low_12" not in set(before["level_id"])
    at_bar = level_state_at_bar(registry, df, atr, 28)
    row = at_bar[at_bar["level_id"] == "h1_swing_low_12"].iloc[0]
    assert row["touch_count"] == 1  # M15 bar 13 touches the 90.0 pivot
    assert row["sweep_state"] == "active"
    assert row["status"] == "active"


# ---------------------------------------------------------------------------
# Registry lifecycle: age, touch count, status (active / swept / invalidated)
# ---------------------------------------------------------------------------

def _lifecycle_frame() -> pd.DataFrame:
    """22 bars; swing low at 5 (90). Bar 20 re-touches 90, bar 21 sweeps 89.5."""
    lows = [105, 104, 103, 102, 101, 90, 102, 103, 104, 105, 106, 107,
            108, 108, 108, 108, 108, 108, 108, 108, 90.0, 89.5]
    highs = [120] * 12 + [125] * 10
    highs[7] = 130.0
    return _utc_frame({"open": highs, "high": highs, "low": lows, "close": lows})


def test_registry_tracks_touch_and_sweep_lifecycle():
    df = _lifecycle_frame()
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 2},
            "liquidity": {
                "swing": {"enabled": True, "left_bars": 3, "right_bars": 3,
                          "max_age_bars": 200},
                "equal_levels": {"enabled": False},
                "h1": {"enabled": False},
                "prev_day": {"enabled": False},
            },
        },
    )
    row = registry[registry["level_id"] == "swing_low_5"].iloc[0]
    # Touches: origin (5) + re-touch (20) + sweep bar (21) = 3.
    assert row["touch_count"] == 3
    assert row["last_touch_time"] == df.index[21]
    assert row["sweep_state"] == "swept"
    assert row["status"] == "expired"
    assert row["first_swept_at"] == df.index[21]
    assert row["age_bars"] == 21 - 5


def test_registry_invalidated_by_age():
    df = _swing_frame()  # 12 bars, swing low at 5, nothing sweeps it
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 2},
            "liquidity": {
                "swing": {"enabled": True, "left_bars": 3, "right_bars": 3,
                          "max_age_bars": 3},
                "equal_levels": {"enabled": False},
                "h1": {"enabled": False},
                "prev_day": {"enabled": False},
            },
        },
    )
    row = registry[registry["level_id"] == "swing_low_5"].iloc[0]
    assert row["sweep_state"] == "invalidated"
    assert row["status"] == "expired"
    assert row["invalidated_at"] == df.index[5 + 3 + 1]  # first bar past max_age


def test_level_state_at_bar_is_causal():
    """Per-bar state must equal the state recomputed on a truncated prefix."""
    df, atr = _frame_with_atr(120, seed=9)  # 30h -> two trading days
    config = {
        "indicators": {"atr_period": 14},
        "liquidity": {
            "swing": {"enabled": True, "left_bars": 3, "right_bars": 3},
            "equal_levels": {"enabled": True, "tolerance_atr": 0.10,
                             "min_touches": 2, "max_age_bars": 200},
            "h1": {"enabled": False},
            "prev_day": {"enabled": True, "max_age_bars": 200},
        },
    }
    registry_full = build_liquidity_levels(df, config)

    for t in (30, 60, 100, 119):
        state_full = level_state_at_bar(registry_full, df, atr, t)
        prefix_df = df.iloc[: t + 1]
        prefix_atr = add_atr(prefix_df, period=14)["atr"]
        registry_prefix = build_liquidity_levels(prefix_df, config)
        state_prefix = level_state_at_bar(registry_prefix, prefix_df, prefix_atr, t)
        # Same set of *usable* levels at bar t.
        assert set(state_full["level_id"]) == set(state_prefix["level_id"]), (
            f"level set diverges at bar {t}"
        )
        merged = state_full.merge(
            state_prefix, on="level_id", suffixes=("_full", "_prefix")
        )
        assert len(merged) == len(state_full)
        for col in LEVEL_STATE_COLUMNS:
            if col in ("level_id", "level_type", "direction"):
                continue
            for _, r in merged.iterrows():
                a, b = r[f"{col}_full"], r[f"{col}_prefix"]
                if pd.isna(a) or pd.isna(b):
                    assert pd.isna(a) and pd.isna(b), (
                        f"bar {t} {r['level_id']} {col}: NaN mismatch"
                    )
                elif isinstance(a, (int, float, np.floating)):
                    assert np.isclose(float(a), float(b)), (
                        f"bar {t} {r['level_id']} {col}: {a} != {b} -- leak"
                    )
                else:
                    assert a == b, (
                        f"bar {t} {r['level_id']} {col}: {a} != {b} -- leak"
                    )


# ---------------------------------------------------------------------------
# Unified registry: schema contract
# ---------------------------------------------------------------------------

def test_registry_schema_conformance():
    df, _ = _frame_with_atr(80, seed=13)
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 14},
            "liquidity": {
                "swing": {"enabled": True, "left_bars": 3, "right_bars": 3},
                "equal_levels": {"enabled": True, "tolerance_atr": 0.10,
                                 "min_touches": 2, "max_age_bars": 200},
                "h1": {"enabled": True},
                "prev_day": {"enabled": True},
            },
        },
    )
    # Full registry column set == schema lock v1.1 (LEVEL_COLUMNS + extensions).
    assert list(registry.columns) == LEVEL_REGISTRY_COLUMNS
    # Enum containment.
    assert set(registry["level_type"]).issubset(set(LEVEL_TYPES))
    assert set(registry["direction"]).issubset(set(LEVEL_DIRECTIONS))
    assert set(registry["status"]).issubset(set(LEVEL_STATUSES))
    # Id scheme {type}_{direction}_{origin_pos} (h1 rows prefixed h1_).
    for _, row in registry.iterrows():
        lid = row["level_id"]
        assert lid.endswith(f"_{row['direction']}_{row['origin_pos']}")
    assert registry["level_id"].is_unique
    assert registry["known_at"].notna().all()
    assert (registry["known_at"] >= registry["origin_time"]).all()
    # Sorted by known_at.
    assert registry["known_at"].is_monotonic_increasing


def test_registry_contains_all_phase4_types():
    df, _ = _frame_with_atr(120, seed=21)
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 14},
            "liquidity": {
                "swing": {"enabled": True},
                "equal_levels": {"enabled": True, "tolerance_atr": 0.10,
                                 "min_touches": 2, "max_age_bars": 200},
                "h1": {"enabled": True},
                "prev_day": {"enabled": True},
            },
        },
    )
    types = set(registry["level_type"])
    assert {"swing", "equal", "prev_day"} <= types
    assert registry["is_h1"].any()


def test_registry_config_disables_sections():
    df, _ = _frame_with_atr(80, seed=17)
    registry = build_liquidity_levels(
        df,
        {
            "indicators": {"atr_period": 14},
            "liquidity": {
                "swing": {"enabled": False},
                "equal_levels": {"enabled": False},
                "h1": {"enabled": False},
                "prev_day": {"enabled": False},
            },
        },
    )
    assert registry.empty
    assert list(registry.columns) == [
        *LEVEL_COLUMNS, "known_pos", "age_bars", "bars_since_last_touch",
        "sweep_state", "first_swept_at", "invalidated_at", "is_h1",
        "max_age_bars", "touch_tolerance_atr", "equal_dispersion_atr",
        "formation_atr_tolerance", "touch_positions",
    ]


def test_registry_deterministic():
    df, _ = _frame_with_atr(100, seed=31)
    config = {
        "indicators": {"atr_period": 14},
        "liquidity": {
            "swing": {"enabled": True},
            "equal_levels": {"enabled": True, "tolerance_atr": 0.10,
                             "min_touches": 2, "max_age_bars": 200},
            "h1": {"enabled": True},
            "prev_day": {"enabled": True},
        },
    }
    r1 = build_liquidity_levels(df, config)
    r2 = build_liquidity_levels(df, config)
    pd.testing.assert_frame_equal(r1, r2)


def test_registry_validation():
    df, _ = _frame_with_atr(40, seed=41)
    with pytest.raises(ValueError, match="monotonic"):
        build_liquidity_levels(df.sample(frac=1.0, random_state=0), {"liquidity": {}})
    # level_state_at_bar bounds
    atr_full = add_atr(df, 14)["atr"]
    registry = build_liquidity_levels(df, {"liquidity": {}})
    with pytest.raises(ValueError, match="bar_pos"):
        level_state_at_bar(registry, df, atr_full, -1)
    with pytest.raises(ValueError, match="out of range"):
        level_state_at_bar(registry, df, atr_full, len(df))


# ---------------------------------------------------------------------------
# Real-data smoke (Phase 4 stack on the full XAUUSD M15 export)
# ---------------------------------------------------------------------------

def test_real_data_registry_smoke():
    if not os.path.exists(RAW_CSV):
        pytest.skip("raw MT5 CSV not present")
    from src.config import load_config
    from src.data.loader import load_ohlcv, normalize_ohlcv

    df = normalize_ohlcv(load_ohlcv(RAW_CSV))
    cfg = load_config()
    registry = build_liquidity_levels(df, cfg)

    assert len(registry) > 0
    assert registry["known_at"].notna().all()
    assert (registry["known_at"] >= registry["origin_time"]).all()
    assert registry["level_id"].is_unique
    assert set(registry["level_type"]).issubset(set(LEVEL_TYPES))
    assert set(registry["status"]).issubset(set(LEVEL_STATUSES))
    assert registry["known_at"].is_monotonic_increasing

    # Sanity: every source contributes in a 4-year M15 sample.
    counts = registry["level_type"].value_counts()
    for lt in ("swing", "equal", "prev_day"):
        assert counts.get(lt, 0) > 0
    # Spot-check causality on the earliest-known levels (cheap subset:
    # state_at_bar iterates only the passed registry rows).
    atr = add_atr(df, 14)["atr"]
    subset = registry[registry["known_pos"] > 0].iloc[:5]
    for _, row in subset.iterrows():
        kp = int(row["known_pos"])
        before = level_state_at_bar(subset, df, atr, max(kp - 1, 0))
        assert row["level_id"] not in set(before["level_id"]), (
            f"{row['level_id']} usable before known_at"
        )
        at_bar = level_state_at_bar(subset, df, atr, kp)
        assert row["level_id"] in set(at_bar["level_id"]), (
            f"{row['level_id']} missing at known_at"
        )