"""Unit tests for sweep confirmation detection (guide §11, Phase 5).

The scenarios use synthetic OHLC frames with exactly known sweep bars and
hand-placed confirmation candles so every metric (body ratio, range/ATR,
delay, strength) is verified by hand.  Causality is locked two ways: run
anchoring (confirmation anchored at the run's *first* bar — QA finding F1) and
truncation invariance of the events(``group_rule="first"``) + confirmation
table, mirroring ``tests/test_no_lookahead.py``.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src import schema
from src.events.confirmation import (
    attach_confirmations,
    build_entry_schedule,
    compare_entry_strategies,
    normalize_direction,
    run_anchor_position,
    structure_level_at,
)
from src.events.sweep_detector import (
    BEARISH,
    BULLISH,
    build_sweep_events,
    detect_sweeps,
)

RAW_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "raw",
    "XAUUSD_M15_202206020915_202608272245.csv",
)

EXPECTED_COLUMNS = [
    *schema.CONFIRMATION_COLUMNS,
    "confirmation_type",
    "confirmation_strength",
]


# ---------------------------------------------------------------------------
# Synthetic frame builders
# ---------------------------------------------------------------------------

def _channel(n: int = 30, mid: float = 100.5, half: float = 0.5) -> pd.DataFrame:
    """Flat channel: high=mid+half, low=mid-half, open=close=mid (TR=half*2)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": mid, "high": mid + half, "low": mid - half, "close": mid,
            "volume": 10.0,
        },
        index=idx,
    )


def _set_bar(df: pd.DataFrame, i: int, o: float, h: float, lo: float, c: float) -> None:
    df.iloc[i] = (o, h, lo, c, 10.0)


def _events(df: pd.DataFrame, group_rule: str = "first") -> pd.DataFrame:
    """Causal (truncation-safe) events for hand-built scenarios by default."""
    return build_sweep_events(df, group_rule=group_rule)


def _synthetic(n: int = 600, seed: int = 9) -> pd.DataFrame:
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


def _confirming_frame(n_segments: int = 12) -> pd.DataFrame:
    """Deterministic frame with guaranteed confirmations (for locking tests).

    Each 26-bar segment contains exactly one bullish sweep (segment bar 20,
    piercing the causal rolling low) that is confirmed on the very next bar
    (segment bar 21: full-body close above the sweep high).  The channel drifts
    0.2 lower per segment so every sweep pierces the current causal level, and
    segment spacing (26) exceeds both the 20-bar level lookback and the 4-bar
    dedup cooldown, so each segment yields exactly one confirmed event with
    ``confirmation_delay_bars == 1``.
    """
    rows = []
    for s in range(n_segments):
        base = 100.0 - 0.2 * s  # channel low of this segment
        for j in range(26):
            if j < 20:      # flat channel
                o, h, lo, c = base + 0.5, base + 1.0, base, base + 0.5
            elif j == 20:   # sweep: low <- base-0.4, close reclaims
                o, h, lo, c = base + 0.6, base + 1.1, base - 0.4, base + 0.65
            elif j == 21:   # confirmation: close far above the sweep high
                o, h, lo, c = base + 0.7, base + 1.8, base + 0.4, base + 1.6
            else:           # recovery
                o, h, lo, c = base + 0.5, base + 1.0, base, base + 0.5
            rows.append((o, h, lo, c, 10.0))
    idx = pd.date_range("2024-01-01", periods=n_segments * 26, freq="15min", tz="UTC")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


# ---------------------------------------------------------------------------
# Baseline confirmation (guide 11.2)
# ---------------------------------------------------------------------------

def test_bullish_close_break_hand_computed():
    """Sweep at 21, confirmation candle at 22: close 101.3 > high[21]=101.0."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # bullish sweep
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)  # confirmation candle
    out = attach_confirmations(df, _events(df))

    row = out.iloc[0]
    assert bool(row["is_confirmed"])
    assert row["confirmation_time"] == df.index[22]
    assert row["confirmation_delay_bars"] == 1
    assert row["confirmation_type"] == "close_break"
    assert np.isclose(row["confirmation_close"], 101.3)
    assert np.isclose(row["confirmation_body_ratio"], 0.6 / 0.9)  # 0.6667
    # TR[21]=1.2, TR[22]=0.9 -> ATR[22] = (12*1.0 + 1.2 + 0.9)/14
    atr22 = (12 * 1.0 + 1.2 + 0.9) / 14.0
    assert np.isclose(row["confirmation_range_atr"], 0.9 / atr22)
    assert np.isclose(row["confirmation_volume"], 10.0)
    # strength = 0.5*body + 0.5*min(range_atr/2, 1)
    expected_strength = 0.5 * (0.6 / 0.9) + 0.5 * min(0.9 / atr22 / 2.0, 1.0)
    assert np.isclose(row["confirmation_strength"], expected_strength)


def test_bearish_close_break_hand_computed():
    """Mirror: close 100.6 < low[21]=101.2 with body/range filters met."""
    df = _channel(mid=101.5)
    _set_bar(df, 21, 101.6, 102.2, 101.2, 101.4)  # bearish sweep
    _set_bar(df, 22, 101.3, 101.4, 100.5, 100.6)  # confirmation candle
    out = attach_confirmations(df, _events(df))

    row = out.iloc[0]
    assert bool(row["is_confirmed"])
    assert row["direction"] == BEARISH
    assert row["confirmation_time"] == df.index[22]
    assert row["confirmation_delay_bars"] == 1
    assert row["confirmation_type"] == "close_break"
    assert np.isclose(row["confirmation_body_ratio"], 0.7 / 0.9)  # 0.7778
    atr22 = (13 * 1.0 + 0.9) / 14.0  # TR[0..20]=1 (sweep TR=1), TR[22]=0.9
    assert np.isclose(row["confirmation_range_atr"], 0.9 / atr22)


def test_no_confirmation_within_window():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # sweep, then flat recovery
    out = attach_confirmations(df, _events(df))
    row = out.iloc[0]
    assert not bool(row["is_confirmed"])
    assert pd.isna(row["confirmation_time"])
    assert pd.isna(row["confirmation_delay_bars"])
    assert pd.isna(row["confirmation_type"])
    assert pd.isna(row["confirmation_strength"])


def test_confirmation_delay_counts_second_candle_when_first_fails():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # sweep
    _set_bar(df, 22, 100.9, 101.2, 100.4, 101.05)  # body 0.1875 -> fails
    _set_bar(df, 23, 100.7, 101.5, 100.6, 101.3)  # qualifies -> delay 2
    out = attach_confirmations(df, _events(df))
    row = out.iloc[0]
    assert bool(row["is_confirmed"])
    assert row["confirmation_time"] == df.index[23]
    assert row["confirmation_delay_bars"] == 2


def test_max_wait_bars_respected():
    """A candle qualifying at anchor+4 is outside the 3-bar window."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # sweep
    _set_bar(df, 25, 100.7, 101.5, 100.6, 101.3)  # qualifies only at +4
    out = attach_confirmations(df, _events(df))
    assert not bool(out.iloc[0]["is_confirmed"])


def test_break_sweep_extreme_can_be_relaxed_by_config():
    """Config flag ``require_break_sweep_extreme=False`` drops the break clause."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # sweep
    # Full-body candle that does NOT close above the sweep high (101.0).
    _set_bar(df, 22, 100.1, 101.0, 100.0, 100.9)
    strict = attach_confirmations(df, _events(df))
    assert not bool(strict.iloc[0]["is_confirmed"])
    relaxed = attach_confirmations(
        df, _events(df), {"confirmation": {"require_break_sweep_extreme": False}}
    )
    assert bool(relaxed.iloc[0]["is_confirmed"])


# ---------------------------------------------------------------------------
# Run anchoring (QA finding F1: decisions anchor on the run's first bar)
# ---------------------------------------------------------------------------

def test_run_anchor_uses_first_bar_not_representative():
    """Cluster run 21..23 with representative 22: confirmation anchors at 21.

    Uses the explicit ``deepest_penetration`` rule so the representative is a
    *later* bar of the run (the F1 scenario); the confirmation window must
    still count from the run's first bar.
    """
    df = _channel(n=30)
    _set_bar(df, 21, 100.6, 101.0, 99.8, 100.8)
    _set_bar(df, 22, 100.1, 100.8, 99.4, 99.9)
    _set_bar(df, 23, 100.4, 100.6, 99.0, 99.7)
    _set_bar(df, 24, 100.5, 101.6, 100.3, 101.4)  # confirms only at +3

    events = build_sweep_events(df, group_rule="deepest_penetration")
    assert df.index.get_loc(events.iloc[0]["event_time"]) == 22
    out = attach_confirmations(df, events)

    row = out.iloc[0]
    assert bool(row["is_confirmed"])
    # Window counted from the run's first bar (21): delay = 24 - 21 = 3.
    assert row["confirmation_time"] == df.index[24]
    assert row["confirmation_delay_bars"] == 3


def test_attach_confirmations_truncation_invariant_with_first_rule():
    """events(group_rule='first') + confirmation: rows confirmed before the cut
    are byte-identical whether or not future bars are present.  Uses the
    engineered frame so the check is non-vacuous (every event confirms)."""
    df = _confirming_frame(12)
    cut = 300
    boundary = df.index[cut]

    full_events = _events(df, group_rule="first")
    assert len(full_events) == 12  # generator contract: not vacuous
    full = attach_confirmations(df, full_events)
    assert int(full["is_confirmed"].sum()) == 12

    partial = attach_confirmations(
        df.iloc[:cut], _events(df.iloc[:cut], group_rule="first")
    )
    full_before = full[full["confirmation_time"] < boundary].reset_index(drop=True)
    partial_before = partial[partial["confirmation_time"] < boundary].reset_index(drop=True)
    assert len(full_before) > 0
    pd.testing.assert_frame_equal(full_before, partial_before)


def test_run_anchor_single_candle_run_equals_event_bar():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    flags = detect_sweeps(df)
    assert run_anchor_position(flags, 21, BULLISH) == 21


# ---------------------------------------------------------------------------
# Confirmation types (Phase 5: displacement, structure break)
# ---------------------------------------------------------------------------

def test_displacement_type():
    """A 1.87 ATR full-body candle qualifies as ``displacement``."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # sweep
    _set_bar(df, 22, 100.7, 102.0, 100.0, 101.95)  # range 2.0, body 1.25
    out = attach_confirmations(df, _events(df), confirmation_types=["displacement"])
    row = out.iloc[0]
    assert bool(row["is_confirmed"])
    assert row["confirmation_type"] == "displacement"
    assert row["confirmation_delay_bars"] == 1
    # TR[22] = 2.0 -> ATR[22] = (12*1.0 + 1.2 + 2.0)/14
    atr22 = (12 * 1.0 + 1.2 + 2.0) / 14.0
    assert np.isclose(row["confirmation_range_atr"], 2.0 / atr22)
    assert np.isclose(row["confirmation_body_ratio"], 1.25 / 2.0)


def test_displacement_requires_strong_range():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)  # range_atr ~0.89 < 1.5
    out = attach_confirmations(df, _events(df), confirmation_types=["displacement"])
    assert not bool(out.iloc[0]["is_confirmed"])


def test_structure_break_type():
    """Close beyond the last confirmed pre-sweep swing high confirms."""
    df = _channel(mid=100.0, half=0.5)  # high 100.5, low 99.5, mid 100.0
    _set_bar(df, 8, 100.0, 101.5, 99.5, 100.2)  # swing high 101.5, known at 11
    _set_bar(df, 21, 100.1, 100.6, 99.2, 100.2)  # bullish sweep vs 99.5
    _set_bar(df, 22, 100.8, 102.1, 100.7, 102.0)  # closes above 101.5

    out = attach_confirmations(df, _events(df), confirmation_types=["structure_break"])
    row = out.iloc[0]
    assert bool(row["is_confirmed"])
    assert row["confirmation_type"] == "structure_break"
    assert np.isclose(row["confirmation_body_ratio"], 1.2 / 1.4)  # 0.8571


def test_structure_break_needs_a_prior_structure_level():
    """No confirmed swing before the sweep -> structure_break cannot fire."""
    df = _channel(mid=100.0, half=0.5)  # no spike: no swing high
    _set_bar(df, 21, 100.1, 100.6, 99.2, 100.2)
    _set_bar(df, 22, 100.8, 102.1, 100.7, 102.0)
    out = attach_confirmations(df, _events(df), confirmation_types=["structure_break"])
    assert not bool(out.iloc[0]["is_confirmed"])
    # ... but a plain close_break confirms the same candle.
    out2 = attach_confirmations(df, _events(df), confirmation_types=["close_break"])
    assert bool(out2.iloc[0]["is_confirmed"])


def test_structure_level_at_returns_most_recent_confirmed_pivot():
    df = _channel(mid=100.0, half=0.5, n=40)
    _set_bar(df, 8, 100.0, 101.5, 99.5, 100.2)   # swing high 101.5
    _set_bar(df, 20, 100.0, 102.0, 99.5, 100.2)  # swing high 102.0 (later)
    # Before bar 12 only the first pivot is known...
    assert np.isclose(structure_level_at(df, 12, BULLISH), 101.5)
    # ...before bar 24 the most recent confirmed pivot is 102.0.
    assert np.isclose(structure_level_at(df, 24, BULLISH), 102.0)


# ---------------------------------------------------------------------------
# Schema / API contract
# ---------------------------------------------------------------------------

def test_confirmation_columns_match_locked_schema_plus_extras():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    out = attach_confirmations(df, _events(df))
    assert list(out.columns[-len(EXPECTED_COLUMNS):]) == EXPECTED_COLUMNS
    for col in schema.CONFIRMATION_COLUMNS:
        assert col in out.columns


def test_inputs_never_mutated():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    ev = _events(df)
    df_copy, ev_copy = df.copy(), ev.copy()
    attach_confirmations(df, ev)
    pd.testing.assert_frame_equal(df, df_copy)
    pd.testing.assert_frame_equal(ev, ev_copy)


def test_empty_events_keep_typed_schema():
    df = _channel()
    ev = pd.DataFrame(columns=["event_id", "event_time", "direction"])
    out = attach_confirmations(df, ev)
    assert out.empty
    assert list(out.columns[-len(EXPECTED_COLUMNS):]) == EXPECTED_COLUMNS
    assert out["is_confirmed"].dtype == bool


def test_direction_vocabulary_long_short_and_invalid():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    ev = _events(df)
    ev_schema = ev.copy()
    ev_schema["direction"] = ev_schema["direction"].map({BULLISH: "long", BEARISH: "short"})
    out = attach_confirmations(df, ev_schema)
    assert bool(out.iloc[0]["is_confirmed"])

    bad = ev.copy()
    bad["direction"] = "sideways"
    with pytest.raises(ValueError, match="direction"):
        attach_confirmations(df, bad)


def test_validation_errors():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    ev = _events(df)
    with pytest.raises(ValueError, match="event_time"):
        attach_confirmations(df, ev.drop(columns=["event_time"]))
    with pytest.raises(ValueError, match="direction"):
        attach_confirmations(df, ev.drop(columns=["direction"]))
    with pytest.raises(ValueError, match="not present"):
        ghost = ev.copy()
        ghost["event_time"] = df.index[21] + pd.Timedelta(days=99)
        attach_confirmations(df, ghost)
    with pytest.raises(ValueError, match="confirmation type"):
        attach_confirmations(df, ev, confirmation_types=["fvg"])
    with pytest.raises(ValueError, match="max_wait_bars"):
        attach_confirmations(df, ev, max_wait_bars=-1)
    with pytest.raises(ValueError, match="min_body_ratio"):
        attach_confirmations(df, ev, min_body_ratio=1.5)
    with pytest.raises(ValueError, match="displacement_min_range_atr"):
        attach_confirmations(df, ev, displacement_min_range_atr=-0.1)
    with pytest.raises(ValueError, match="monotonic"):
        attach_confirmations(df.sample(frac=1.0, random_state=0), ev)


def test_normalize_direction_mapping():
    assert normalize_direction("bullish") == "long"
    assert normalize_direction("long") == "long"
    assert normalize_direction("bearish") == "short"
    assert normalize_direction("SHORT") == "short"
    with pytest.raises(ValueError):
        normalize_direction("up")


# ---------------------------------------------------------------------------
# Entry timing (guide 11.3: never at the confirmation candle's open)
# ---------------------------------------------------------------------------

def test_entry_schedule_after_confirmation_uses_next_open():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)  # confirm at 22
    ev = attach_confirmations(df, _events(df))
    schedule = build_entry_schedule(df, ev, mode="after_confirmation")

    assert len(schedule) == 1
    entry = schedule.iloc[0]
    assert entry["entry_bar"] == 23
    assert entry["entry_time"] == df.index[23]  # open time of candle 23
    assert np.isclose(entry["entry_price"], df["open"].iloc[23])
    assert entry["sweep_anchor_time"] == df.index[21]
    assert entry["confirmation_time"] == df.index[22]


def test_entry_schedule_after_sweep_uses_bar_right_after_anchor():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    ev = attach_confirmations(df, _events(df))
    schedule = build_entry_schedule(df, ev, mode="after_sweep")
    assert schedule.iloc[0]["entry_bar"] == 22
    assert schedule.iloc[0]["entry_time"] == df.index[22]  # open time of 22

    with pytest.raises(ValueError, match="mode"):
        build_entry_schedule(df, _events(df), mode="sideways")


def test_entry_schedule_drops_unconfirmed_in_confirmation_mode():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)  # no confirmation follows
    ev = attach_confirmations(df, _events(df))
    schedule = build_entry_schedule(df, ev, mode="after_confirmation")
    assert schedule.empty
    assert list(schedule.columns) == [
        "event_id", "direction", "sweep_anchor_time", "is_confirmed",
        "confirmation_time", "entry_bar", "entry_time", "entry_price",
    ]


# ---------------------------------------------------------------------------
# Phase 5: A vs B strategy comparison
# ---------------------------------------------------------------------------

def test_compare_hand_computed_metrics_and_delay():
    """A enters at open[22] (delay 1), B at open[23] (delay 2); both never hit
    the 1R target or the stop in the 32-bar horizon -> mark-to-market."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    ev = attach_confirmations(df, _events(df))
    cmp = compare_entry_strategies(df, ev)

    assert list(cmp.index) == ["after_sweep", "after_confirmation"]
    a, b = cmp.loc["after_sweep"], cmp.loc["after_confirmation"]
    assert a["event_count"] == b["event_count"] == 1
    assert a["avg_entry_delay_bars"] == 1.0
    assert b["avg_entry_delay_bars"] == 2.0

    atr21 = (13 * 1.0 + 1.2) / 14.0
    stop = 99.8 - 0.10 * atr21
    r_a = 100.7 - stop
    r_b = 100.5 - stop
    assert np.isclose(a["avg_stop_distance_atr"], r_a / atr21)
    assert np.isclose(b["avg_stop_distance_atr"], r_b / atr21)
    assert np.isclose(a["expectancy_r"], (100.5 - 100.7) / r_a)
    assert np.isclose(b["expectancy_r"], (100.5 - 100.5) / r_b)
    assert np.isclose(a["mfe_mean_r"], (101.5 - 100.7) / r_a)
    assert np.isclose(a["mae_mean_r"], (100.0 - 100.7) / r_a)
    assert a["win_rate"] == 0.0


def test_compare_win_and_loss_outcomes():
    """One event wins at +1R (same-bar target touch), one loses at -1R."""
    df = _channel(n=30)
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)   # sweep 1
    _set_bar(df, 22, 100.7, 102.0, 100.5, 101.9)  # entry+target touch -> +1R
    _set_bar(df, 26, 100.7, 101.0, 99.3, 100.6)   # sweep 2 (cooldown 5 > 4)
    _set_bar(df, 27, 100.5, 100.8, 98.9, 99.0)    # entry, stop touch -> -1R

    ev = _events(df)  # group_rule="first": both sweeps survive the cooldown
    assert len(ev) == 2
    cmp = compare_entry_strategies(df, attach_confirmations(df, ev))
    a = cmp.loc["after_sweep"]
    assert a["event_count"] == 2
    assert a["win_rate"] == 0.5
    assert np.isclose(a["expectancy_r"], 0.0)


def test_compare_only_confirmed_entries_in_strategy_b():
    df = _channel(n=30)
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)   # confirmed at 22
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    _set_bar(df, 26, 100.7, 101.0, 99.3, 100.6)   # never confirmed
    ev = attach_confirmations(df, _events(df))
    assert sum(ev["is_confirmed"]) == 1
    cmp = compare_entry_strategies(df, ev)
    assert cmp.loc["after_sweep", "event_count"] == 2
    assert cmp.loc["after_confirmation", "event_count"] == 1


def test_compare_validation_errors():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    ev = _events(df)  # no confirmation columns yet
    with pytest.raises(ValueError, match="attach_confirmations first"):
        compare_entry_strategies(df, ev)
    conf = attach_confirmations(df, ev)
    with pytest.raises(ValueError, match=r"stop\.mode"):
        compare_entry_strategies(df, conf, {"stop": {"mode": "atr_multiple"}})
    with pytest.raises(ValueError, match="reward_r"):
        compare_entry_strategies(df, conf, reward_r=0.0)
    with pytest.raises(ValueError, match="horizon_bars"):
        compare_entry_strategies(df, conf, horizon_bars=0)


# ---------------------------------------------------------------------------
# Timezone robustness (tz-aware vs tz-naive must never raise TypeError)
# ---------------------------------------------------------------------------

def test_tz_naive_candles_flow_is_consistent():
    """A tz-naive candles frame produces naive confirmation timestamps and the
    whole flow (attach / entry schedule / A-B compare) runs without TypeError."""
    idx = pd.date_range("2024-01-01", periods=30, freq="15min")
    df = pd.DataFrame(
        {"open": 100.5, "high": 101.0, "low": 100.0, "close": 100.5, "volume": 10.0},
        index=idx,
    )
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    conf = attach_confirmations(df, _events(df))
    row = conf.iloc[0]
    assert bool(row["is_confirmed"])
    assert conf["confirmation_time"].dtype == np.dtype("datetime64[ns]")  # naive
    assert row["confirmation_time"] == idx[22]

    schedule = build_entry_schedule(df, conf, mode="after_confirmation")
    assert schedule.iloc[0]["entry_time"] == idx[23]
    cmp = compare_entry_strategies(df, conf)
    assert cmp.loc["after_confirmation", "avg_entry_delay_bars"] == 2.0


def test_unconfirmed_all_nat_keeps_aware_dtype():
    """Zero confirmations must not degrade confirmation_time to tz-naive."""
    df = _channel(n=30)  # no confirmation follows the sweep at 21
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    conf = attach_confirmations(df, _events(df))
    assert not bool(conf.iloc[0]["is_confirmed"])
    assert conf["confirmation_time"].dtype == df.index.dtype  # tz-aware UTC


def test_mixed_tz_lookup_never_raises_typeerror():
    """A consumer passing naive confirmation_time against an aware index is
    handled: the naive value is read as UTC and resolves to the same bar."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    _set_bar(df, 22, 100.7, 101.5, 100.6, 101.3)
    conf = attach_confirmations(df, _events(df))
    assert conf["confirmation_time"].iloc[0] == df.index[22]

    mixed = conf.copy()
    mixed["confirmation_time"] = conf["confirmation_time"].dt.tz_localize(None)
    mixed["event_time"] = conf["event_time"].dt.tz_localize(None)
    schedule = build_entry_schedule(df, mixed, mode="after_confirmation")
    assert len(schedule) == 1
    assert schedule.iloc[0]["entry_time"] == df.index[23]

    trades = compare_entry_strategies(df, mixed)
    assert "after_confirmation" in trades.index
    assert trades.loc["after_confirmation", "avg_entry_delay_bars"] == 2.0


# ---------------------------------------------------------------------------
# Real-data smoke (skips when the raw export is absent)
# ---------------------------------------------------------------------------

def test_real_data_confirmation_and_ab_compare_smoke():
    if not os.path.exists(RAW_CSV):
        pytest.skip("raw MT5 CSV not present")
    from src.data.loader import load_ohlcv, normalize_ohlcv

    df = normalize_ohlcv(load_ohlcv(RAW_CSV))
    events = _events(df, group_rule="first")
    conf = attach_confirmations(df, events)
    assert len(conf) == len(events)
    rate = float(conf["is_confirmed"].mean())
    assert 0.0 < rate < 1.0

    cmp = compare_entry_strategies(df, conf)
    assert "after_sweep" in cmp.index and "after_confirmation" in cmp.index
    assert cmp.loc["after_sweep", "event_count"] >= cmp.loc[
        "after_confirmation", "event_count"
    ]
    assert cmp.loc["after_sweep", "avg_entry_delay_bars"] < cmp.loc[
        "after_confirmation", "avg_entry_delay_bars"
    ]


def test_real_data_confirmation_truncation_sampled():
    """Sampled truncation check on real data (events with group_rule='first')."""
    if not os.path.exists(RAW_CSV):
        pytest.skip("raw MT5 CSV not present")
    from src.data.loader import load_ohlcv, normalize_ohlcv

    df = normalize_ohlcv(load_ohlcv(RAW_CSV))
    full = attach_confirmations(df, _events(df, group_rule="first"))
    for cut in (5000, 20000, 60000):
        boundary = df.index[cut]
        partial = attach_confirmations(
            df.iloc[:cut], _events(df.iloc[:cut], group_rule="first")
        )
        fb = full[full["confirmation_time"] < boundary].reset_index(drop=True)
        pb = partial[partial["confirmation_time"] < boundary].reset_index(drop=True)
        assert len(fb) == len(pb)
        if len(fb):
            pd.testing.assert_frame_equal(fb, pb)