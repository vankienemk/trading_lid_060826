"""Authoritative no-look-ahead (truncation-invariance) suite — guide section 25.2.

Owned by Agent 7 (QA / Bias Auditor).  This is the project-wide causality gate:
it is *the* test every stage's per-bar feature pipeline must pass before merge
(guide section 5.5 requires it on every Agent 4 change; INTERFACES.md rule 1
points here).

Method (guide 25.2)
--------------------
1. Run the pipeline on all data up to ``T``.
2. Re-run it on data truncated at ``t < T``.
3. Compare values at rows ``<= t``.
4. They must be identical — :func:`pandas.testing.assert_frame_equal`.

If any value at row ``i`` changes when future rows are appended, that column
leaks.  A *positive-control* test
(:func:`test_positive_control_centered_window_is_detected_as_leak`) proves the
harness actually detects a known leak, so a green run is meaningful rather
than vacuous.

Scope today (Sprint 1)
----------------------
The causal per-bar modules that exist at t5 are covered: ATR (t3), rolling
liquidity levels (t3) and the baseline sweep detector + deduplication (t4).
As later stages land, extend ``CAUSAL_COLUMNS`` (features, t10) and extend
``_feature_pipeline`` with ``build_event_features`` / label pipelines.

Known nuance (documented behaviour, not a hidden leak)
------------------------------------------------------
The *event table* deduplication uses ``deepest_penetration`` grouping by
default (guide section 12).  That rule picks the strongest bar of a *run* of
consecutive sweep candles, so a run's representative ``event_time`` depends on
the whole run — a *within-run* look-ahead.  The per-bar sweep flags and metrics
remain strictly causal; only the run *representative* is affected.  This is
captured by :func:`test_deepest_penetration_representative_can_be_a_later_bar`
and must be handled downstream (t6 labeling / t9 confirmation): decisions must
anchor to the run's first bar, or ``group_rule="first"`` must be used wherever
strict causality of the event anchor is required.
:func:`test_event_table_is_causal_with_first_rule` proves the causal
alternative is truncation-invariant.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.data.loader import read_parquet
from src.events.confirmation import attach_confirmations, build_entry_schedule
from src.events.deduplication import group_candidate_events
from src.events.sweep_detector import build_sweep_events, detect_sweeps
from src.indicators.atr import add_atr
from src.liquidity.level_registry import build_liquidity_levels, level_state_at_bar
from src.liquidity.rolling_levels import add_rolling_liquidity_levels

pytestmark = pytest.mark.no_lookahead

#: Every causal per-bar column currently produced by the project pipeline.
#: Extended by later phases (feature pipeline, t10) — the new columns must keep
#: the truncation-invariance property or they will fail here.
CAUSAL_COLUMNS = [
    "true_range",           # ATR (t3)
    "atr",
    "liq_low",              # rolling liquidity levels (t3)
    "liq_high",
    "lower_wick",           # sweep metrics (t4)
    "upper_wick",
    "lower_wick_ratio",
    "upper_wick_ratio",
    "low_pen_atr",
    "high_pen_atr",
    "low_reclaim_atr",
    "high_reclaim_atr",
    "sweep_long",           # sweep flags (t4)
    "sweep_short",
]

_REAL_PARQUET = os.path.join(
    os.path.dirname(__file__), "..", "data", "processed", "xauusd_m15.parquet"
)


def _synthetic_ohlcv(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """Reproducible random-walk OHLC frame (valid, sorted, UTC-indexed)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
    high = close + rng.uniform(0.05, 0.5, n)
    low = close - rng.uniform(0.05, 0.5, n)
    open_ = close + rng.normal(0, 0.1, n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )
    df.loc[df["high"] < df[["open", "close"]].max(axis=1), "high"] = df[
        ["open", "close"]
    ].max(axis=1)
    df.loc[df["low"] > df[["open", "close"]].min(axis=1), "low"] = df[
        ["open", "close"]
    ].min(axis=1)
    return df


def _feature_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """The causal per-bar pipeline currently owned by the project.

    ``detect_sweeps`` runs ATR -> rolling levels -> sweep metrics -> flags, so
    it returns every causal per-bar column in a single call.  Later phases add
    ``build_event_features`` here (one event per row, strictly causal).
    """
    return detect_sweeps(df)


def _assert_truncation_invariant(
    df: pd.DataFrame, columns: list[str], cuts: list[int]
) -> None:
    """Assert ``_feature_pipeline`` prefix-invariance at every cut point."""
    full = _feature_pipeline(df)
    for cut in cuts:
        partial = _feature_pipeline(df.iloc[:cut])
        pd.testing.assert_frame_equal(
            full.iloc[:cut][columns],
            partial[columns],
            check_dtype=False,
        )


# ---------------------------------------------------------------------------
# Core truncation-invariance tests (guide 25.2)
# ---------------------------------------------------------------------------

def test_all_causal_columns_are_truncation_invariant() -> None:
    """Every per-bar causal column is identical on full vs truncated data."""
    _assert_truncation_invariant(_synthetic_ohlcv(600), CAUSAL_COLUMNS, [200, 300, 400])


def test_atr_is_truncation_invariant() -> None:
    _assert_truncation_invariant(_synthetic_ohlcv(400), ["true_range", "atr"], [150, 250])


def test_rolling_levels_are_truncation_invariant() -> None:
    _assert_truncation_invariant(_synthetic_ohlcv(400), ["liq_low", "liq_high"], [150, 250])


def test_sweep_metrics_are_truncation_invariant() -> None:
    _assert_truncation_invariant(
        _synthetic_ohlcv(400),
        [
            "lower_wick", "upper_wick",
            "lower_wick_ratio", "upper_wick_ratio",
            "low_pen_atr", "high_pen_atr",
            "low_reclaim_atr", "high_reclaim_atr",
        ],
        [150, 250],
    )


def test_sweep_flags_are_truncation_invariant() -> None:
    _assert_truncation_invariant(
        _synthetic_ohlcv(400), ["sweep_long", "sweep_short"], [150, 250]
    )


def test_truncation_invariant_at_warmup_boundary() -> None:
    """Cut points that land exactly on warm-up edges are the sharpest case."""
    _assert_truncation_invariant(
        _synthetic_ohlcv(300),
        CAUSAL_COLUMNS,
        cuts=[13, 14, 15, 19, 20, 21],  # ATR(14) and level(20) warm-up edges
    )


def test_positive_control_centered_window_is_detected_as_leak() -> None:
    """The harness has power: a centered (look-ahead) window must *fail*.

    A ``center=True`` rolling max at row ``t`` uses future rows, so a value at
    ``t`` changes once later rows are appended.  If this assertion ever passes,
    the truncation method itself is broken and the whole suite is vacuous.
    """

    def centered(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["lookahead_max"] = out["high"].rolling(20, center=True).max()
        return out

    df = _synthetic_ohlcv(200)
    cut = 120
    full = centered(df)
    partial = centered(df.iloc[:cut])
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(
            full.iloc[:cut][["lookahead_max"]],
            partial[["lookahead_max"]],
            check_dtype=False,
        )


# ---------------------------------------------------------------------------
# Warm-up and current-candle exclusion (guide 3.1 / 8 / 9.1)
# ---------------------------------------------------------------------------

def test_rolling_level_warmup_is_nan() -> None:
    """No level exists until a full lookback window of prior candles is seen."""
    df = add_rolling_liquidity_levels(_synthetic_ohlcv(), lookback=20)
    assert df["liq_low"].iloc[:20].isna().all()
    assert df["liq_low"].iloc[20:].notna().all()
    assert df["liq_high"].iloc[:20].isna().all()
    assert df["liq_high"].iloc[20:].notna().all()


def test_atr_warmup_is_nan() -> None:
    """ATR needs a full ``period`` window; first ``period-1`` rows are NaN."""
    df = add_atr(_synthetic_ohlcv(), period=14)
    assert df["atr"].iloc[:13].isna().all()
    assert df["atr"].iloc[13:].notna().all()


def test_rolling_level_excludes_current_candle() -> None:
    """The level at t uses rows [t-lookback, t-1] only (shift(1) semantics)."""
    df = _synthetic_ohlcv()
    levels = add_rolling_liquidity_levels(df, lookback=20)
    for t in range(25, len(df), 97):
        window_high = df["high"].iloc[t - 20 : t].max()
        window_low = df["low"].iloc[t - 20 : t].min()
        assert levels["liq_high"].iloc[t] == window_high
        assert levels["liq_low"].iloc[t] == window_low


def test_unsorted_index_is_rejected_not_silently_leaked() -> None:
    """A shuffled frame must raise, never silently leak future data into a level."""
    df = _synthetic_ohlcv(100)
    shuffled = df.sample(frac=1.0, random_state=0)
    with pytest.raises(ValueError):
        add_rolling_liquidity_levels(shuffled, lookback=20)
    with pytest.raises(ValueError):
        add_atr(shuffled, period=14)
    with pytest.raises(ValueError):
        detect_sweeps(shuffled)


# ---------------------------------------------------------------------------
# Event-table causality (deduplication) — guide section 12
# ---------------------------------------------------------------------------

def test_event_table_is_causal_with_first_rule() -> None:
    """With ``group_rule="first"`` the deduplicated event table is truncation-invariant.

    ``first`` + cooldown only read past bars, so events before the cut are
    byte-identical whether or not future bars are present.
    """
    df = _synthetic_ohlcv(600)
    cut = 300
    boundary = df.index[cut]
    full = build_sweep_events(df, group_rule="first")
    partial = build_sweep_events(df.iloc[:cut], group_rule="first")

    full_before = full[full["event_time"] < boundary].reset_index(drop=True)
    partial_before = partial[partial["event_time"] < boundary].reset_index(drop=True)
    assert len(full_before) > 0  # not a vacuous empty-table pass
    pd.testing.assert_frame_equal(full_before, partial_before)


def test_deepest_penetration_representative_can_be_a_later_bar() -> None:
    """Documented within-run look-ahead of the default grouping rule (guide 12).

    Two consecutive bullish candidates against the same level form one run;
    ``deepest_penetration`` picks the bar with the larger penetration — the
    *second* (later) bar here.  The run representative therefore depends on a
    future bar of the run even though every per-bar flag is causal.  Downstream
    (t6/t9) must not treat the representative's timestamp as knowable at the
    run's first bar; the causal alternative is ``group_rule="first"``.
    """
    candidates = pd.DataFrame(
        {
            "position": [10, 11],
            "direction": ["bullish", "bullish"],
            "level_id": ["rolling_low", "rolling_low"],
            "penetration_atr": [0.10, 0.30],
            "reclaim_atr": [0.05, 0.02],
        }
    )
    picked = group_candidate_events(candidates, rule="deepest_penetration")
    assert int(picked["position"].iloc[0]) == 11  # representative = later bar

    first = group_candidate_events(candidates, rule="first")
    assert int(first["position"].iloc[0]) == 10  # causal anchor = first bar


# ---------------------------------------------------------------------------
# Real-data gate (skip if the processed parquet is not built)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.path.exists(_REAL_PARQUET), reason="processed parquet not built"
)
def test_real_data_prefix_invariant() -> None:
    """Truncation invariance on the real XAUUSD M15 frame (99,692 bars)."""
    df = read_parquet(_REAL_PARQUET).set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]]
    full = _feature_pipeline(df)
    for cut in (2000, 20000, 60000):
        partial = _feature_pipeline(df.iloc[:cut])
        pd.testing.assert_frame_equal(
            full.iloc[:cut][CAUSAL_COLUMNS],
            partial[CAUSAL_COLUMNS],
            check_dtype=False,
        )


# ---------------------------------------------------------------------------
# Phase 4/5 modules (t8 swing/equal/prev-day/H1 levels; t9 confirmation)
# ---------------------------------------------------------------------------

def _registry_config() -> dict:
    """Minimal baseline config for :func:`build_liquidity_levels` (all types on)."""
    return {
        "indicators": {"atr_period": 14},
        "liquidity": {
            "swing": {"enabled": True, "left_bars": 3, "right_bars": 3, "max_age_bars": 200},
            "h1": {"enabled": True, "left_bars": 3, "right_bars": 3, "max_age_bars": 200},
            "equal_levels": {
                "enabled": True, "tolerance_atr": 0.10, "min_touches": 2,
                "max_age_bars": 200,
            },
            "prev_day": {"enabled": True, "max_age_bars": 200},
        },
    }


#: Level identity (causal) columns — exclude the final-bar lifecycle snapshot.
_LEVEL_IDENTITY_COLUMNS = [
    "level_id", "level_type", "direction", "price",
    "origin_pos", "origin_time", "known_pos", "known_at",
]


def test_registry_level_identity_is_truncation_invariant() -> None:
    """Level identity is prefix-invariant across swing/equal/prev-day/H1.

    ``build_liquidity_levels`` snapshots lifecycle (``touch_count`` /
    ``sweep_state``) *as of the last bar*, so only the level *identity* — id,
    price, origin and the causal usability gate ``known_pos``/``known_at`` —
    must be identical on a truncated prefix.  The per-bar causal state is
    covered by :func:`test_level_state_at_bar_is_truncation_invariant`.
    """
    df = _synthetic_ohlcv(600)
    cut = 300
    full = build_liquidity_levels(df, _registry_config())
    partial = build_liquidity_levels(df.iloc[:cut], _registry_config())

    f = (
        full[full["known_pos"] < cut][_LEVEL_IDENTITY_COLUMNS]
        .sort_values("level_id")
        .reset_index(drop=True)
    )
    p = (
        partial[partial["known_pos"] < cut][_LEVEL_IDENTITY_COLUMNS]
        .sort_values("level_id")
        .reset_index(drop=True)
    )
    assert len(f) > 0  # all four level types emit on a 600-bar frame
    pd.testing.assert_frame_equal(f, p)


def test_level_state_at_bar_is_truncation_invariant() -> None:
    """The causal per-bar registry interface uses only bars ``<= bar_pos``.

    ``level_state_at_bar`` is the no-look-ahead surface the sweep detector and
    feature stage must consume: each value at ``bar_pos`` must equal the value
    recomputed on the truncated prefix.
    """
    df = _synthetic_ohlcv(600)
    cut = 300
    atr = add_atr(df, 14)["atr"]
    atr_prefix = add_atr(df.iloc[:cut], 14)["atr"]
    full_reg = build_liquidity_levels(df, _registry_config())
    prefix_reg = build_liquidity_levels(df.iloc[:cut], _registry_config())

    for bar_pos in (50, 150, 250, 299):
        state_full = level_state_at_bar(full_reg, df, atr, bar_pos)
        state_prefix = level_state_at_bar(prefix_reg, df.iloc[:cut], atr_prefix, bar_pos)
        a = state_full.sort_values("level_id").reset_index(drop=True)
        b = state_prefix.sort_values("level_id").reset_index(drop=True)
        assert len(a) > 0
        pd.testing.assert_frame_equal(a, b)


def _confirming_frame(n_segments: int = 4) -> pd.DataFrame:
    """Deterministic frame where every segment's sweep confirms on the next bar.

    Each 26-bar segment holds exactly one bullish sweep (bar 20, piercing the
    causal rolling low) confirmed by bar 21 (full-body close above the sweep
    high); the channel drifts 0.2 lower per segment so every sweep pierces its
    own causal level.
    """
    rows = []
    for s in range(n_segments):
        base = 100.0 - 0.2 * s
        for j in range(26):
            if j < 20:
                o, h, lo, c = base + 0.5, base + 1.0, base, base + 0.5
            elif j == 20:  # sweep
                o, h, lo, c = base + 0.6, base + 1.1, base - 0.4, base + 0.65
            elif j == 21:  # confirmation
                o, h, lo, c = base + 0.7, base + 1.8, base + 0.4, base + 1.6
            else:
                o, h, lo, c = base + 0.5, base + 1.0, base, base + 0.5
            rows.append((o, h, lo, c, 10.0))
    idx = pd.date_range(
        "2024-01-01", periods=n_segments * 26, freq="15min", tz="UTC"
    )
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_confirmation_table_is_truncation_invariant() -> None:
    """events(group_rule='first') + confirmation are prefix-invariant (t9 stage).

    Rows whose confirmation candle closes strictly before the cut are
    byte-identical whether or not future bars are present — the confirmation
    scan reads only bars ``<=`` the confirmation candle.
    """
    df = _confirming_frame(12)
    cut = 8 * 26  # boundary between segments 7 and 8
    boundary = df.index[cut]

    full = attach_confirmations(df, build_sweep_events(df, group_rule="first"))
    assert int(full["is_confirmed"].sum()) > 0  # non-vacuous

    partial = attach_confirmations(
        df.iloc[:cut], build_sweep_events(df.iloc[:cut], group_rule="first")
    )
    full_before = full[full["confirmation_time"] < boundary].reset_index(drop=True)
    partial_before = partial[partial["confirmation_time"] < boundary].reset_index(drop=True)
    assert len(full_before) > 0
    pd.testing.assert_frame_equal(full_before, partial_before)


def test_entry_schedule_is_truncation_invariant() -> None:
    """Entry timing (next open after confirmation, guide 11.3) is prefix-invariant."""
    df = _confirming_frame(12)
    cut = 8 * 26
    boundary = df.index[cut]

    full_conf = attach_confirmations(df, build_sweep_events(df, group_rule="first"))
    partial_conf = attach_confirmations(
        df.iloc[:cut], build_sweep_events(df.iloc[:cut], group_rule="first")
    )
    full_sched = build_entry_schedule(df, full_conf, mode="after_confirmation")
    partial_sched = build_entry_schedule(
        df.iloc[:cut], partial_conf, mode="after_confirmation"
    )
    a = full_sched[full_sched["entry_time"] < boundary].reset_index(drop=True)
    b = partial_sched[partial_sched["entry_time"] < boundary].reset_index(drop=True)
    assert len(a) > 0
    pd.testing.assert_frame_equal(a, b)
