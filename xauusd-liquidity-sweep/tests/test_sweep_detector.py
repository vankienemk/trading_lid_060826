"""Unit tests for the baseline sweep detector and deduplication.

The tests use synthetic OHLC frames with exactly known levels (flat channel +
hand-placed sweep candles) so every metric can be verified by hand, plus a
reproducible random-walk frame for the causality proofs.  The no-look-ahead
proof mirrors ``tests/test_levels.py``: the *prefix invariant* — the value (or
flag) computed on the full frame at row ``t`` must equal the value recomputed
on the prefix ``df.iloc[:t+1]``.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.events.deduplication import (
    apply_cooldown,
    deduplicate_events,
    group_candidate_events,
    select_deduplicated_events,
)
from src.events.sweep_detector import (
    BEARISH,
    BULLISH,
    build_sweep_events,
    detect_sweeps,
    sweep_event_schema_columns,
)

RAW_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "raw",
    "XAUUSD_M15_202206020915_202608272245.csv",
)

LOOKBACK = 20
WARMUP = LOOKBACK  # no rolling level exists before this many candles


# ---------------------------------------------------------------------------
# Synthetic frame builders
# ---------------------------------------------------------------------------

def _synthetic(n: int = 60, seed: int = 21) -> pd.DataFrame:
    """Reproducible random-walk OHLC frame used for causality proofs."""
    rng = np.random.default_rng(seed)
    close = 1800 + np.cumsum(rng.normal(0.0, 1.0, n))
    spread = np.abs(rng.normal(0.8, 0.3, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close + spread, "low": close - spread, "close": close},
        index=idx,
    )


def _channel(n: int = 24) -> pd.DataFrame:
    """Flat channel: high=101.0, low=100.0, open=close=100.5 (TR=1, level=100.0)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": 100.5, "high": 101.0, "low": 100.0, "close": 100.5}, index=idx
    )


def _set_bar(df: pd.DataFrame, i: int, o: float, h: float, lo: float, c: float) -> None:
    df.loc[df.index[i], ["open", "high", "low", "close"]] = (o, h, lo, c)


def _cluster_frame(second_cluster: int | None = None) -> pd.DataFrame:
    """Synthetic sweep-cluster scenario.

    Bars 0..20: flat channel (rolling low 100.0, ATR 1.0).
    Bars 21..23: three consecutive bullish sweeps of the rolling low (run 1,
    deepest penetration at bar 22).
    Bars 24..27: recovery (no sweep).
    With ``second_cluster=28`` bars 28..29 sweep again (run 2, representative
    bar 28; 28 - 22 = 6 > 4 cooldown bars -> accepted).
    With ``second_cluster=25`` bars 25..26 sweep again (representative bar 25;
    25 - 22 = 3 <= 4 -> suppressed by the cooldown).
    """
    df = _channel(n=30)
    _set_bar(df, 21, 100.6, 101.0, 99.8, 100.8)
    _set_bar(df, 22, 100.1, 100.8, 99.4, 99.9)
    _set_bar(df, 23, 100.4, 100.6, 99.0, 99.7)
    for i in range(24, 28):
        _set_bar(df, i, 100.0, 100.2, 99.9, 100.0)
    if second_cluster == 28:
        _set_bar(df, 28, 100.1, 100.7, 98.7, 99.5)
        _set_bar(df, 29, 100.0, 100.6, 98.4, 99.2)
    elif second_cluster == 25:
        _set_bar(df, 25, 100.1, 100.2, 98.6, 99.5)
        _set_bar(df, 26, 99.9, 100.1, 98.3, 99.2)
    return df


# ---------------------------------------------------------------------------
# Hand-computed sweep detection (guide section 10)
# ---------------------------------------------------------------------------

def test_bullish_sweep_hand_computed():
    """Bar 21 of the flat channel: low 99.8 < 100.0, close 100.9 > 100.0."""
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    out = detect_sweeps(df, level_lookback=LOOKBACK)

    assert bool(out["sweep_long"].iloc[21])
    assert not bool(out["sweep_short"].iloc[21])
    # Guide 10.1 metrics, hand-computed.  The sweep bar's own TR is 1.2
    # (high-low), so ATR[21] = (13*1.0 + 1.2)/14, not 1.0.
    atr21 = (13 * 1.0 + 1.2) / 14.0
    assert np.isclose(out["low_pen_atr"].iloc[21], (100.0 - 99.8) / atr21)
    assert np.isclose(
        out["lower_wick_ratio"].iloc[21],
        (min(100.8, 100.9) - 99.8) / (101.0 - 99.8),
    )
    assert np.isclose(out["low_reclaim_atr"].iloc[21], (100.9 - 100.0) / atr21)


def test_bearish_sweep_hand_computed():
    """Mirror frame: high 102.2 > 102.0, close 101.4 < 102.0 (level 102.0)."""
    idx = pd.date_range("2024-01-01", periods=24, freq="15min")
    df = pd.DataFrame(
        {"open": 101.5, "high": 102.0, "low": 101.0, "close": 101.5}, index=idx
    )
    # Same TR=1 / atr=1 structure: hand value below.
    _set_bar(df, 21, 101.6, 102.2, 101.2, 101.4)
    out = detect_sweeps(df, level_lookback=LOOKBACK)

    assert bool(out["sweep_short"].iloc[21])
    assert not bool(out["sweep_long"].iloc[21])
    assert np.isclose(out["high_pen_atr"].iloc[21], (102.2 - 102.0) / 1.0)
    assert np.isclose(
        out["upper_wick_ratio"].iloc[21],
        (102.2 - max(101.6, 101.4)) / (102.2 - 101.2),
    )
    assert np.isclose(out["high_reclaim_atr"].iloc[21], (102.0 - 101.4) / 1.0)


@pytest.mark.parametrize(
    "open_,high,low,close",
    [
        # close below the level -> no reclaim
        (100.8, 101.0, 99.8, 99.9),
        # penetration 1.2 ATR -> too deep (max 0.50)
        (100.8, 101.0, 98.8, 100.9),
        # penetration 0.02 ATR -> too shallow (min 0.05)
        (100.5, 101.0, 99.98, 100.6),
        # lower wick 0.1/1.2 -> ratio 0.083 < 0.35
        (99.9, 101.0, 99.8, 100.05),
        # close exactly on the level (100.0) -> must be strictly above
        (100.8, 101.0, 99.8, 100.0),
        # no pierce at all: low 100.05 stays above the 100.0 level
        (100.6, 101.0, 100.05, 100.9),
    ],
)
def test_non_sweep_bullish_variants_excluded(open_, high, low, close):
    df = _channel()
    _set_bar(df, 21, open_, high, low, close)
    out = detect_sweeps(df, level_lookback=LOOKBACK)
    assert not bool(out["sweep_long"].iloc[21])
    assert not bool(out["sweep_short"].iloc[21])


def test_min_reclaim_atr_filter_applied():
    """A configured minimum reclaim excludes candles that reclaim by too little."""
    # Bar passes every 10.1 condition except reclaim: pen 0.20, wick 0.4,
    # close > level, but reclaim (100.02-100.0)/atr ~= 0.02 ATR.
    df = _channel()
    _set_bar(df, 21, 100.0, 100.3, 99.8, 100.02)
    out = detect_sweeps(df, level_lookback=LOOKBACK, min_reclaim_atr=0.5)
    assert not bool(out["sweep_long"].iloc[21])
    # The defaults (min_reclaim_atr=0.0) accept the same candle.
    out2 = detect_sweeps(df, level_lookback=LOOKBACK)
    assert bool(out2["sweep_long"].iloc[21])


def test_warmup_no_sweeps_before_level_exists():
    """No rolling level before bar ``LOOKBACK``, so no sweep can fire there."""
    df = _channel(n=30)
    # Bar 5 dips far below the channel but its level is still NaN -> no sweep.
    _set_bar(df, 5, 100.5, 100.8, 99.0, 100.6)
    # That dip enters the window, so the level at bar 21 is 99.0 (not 100.0);
    # the sweep candle must dip below it to be detected.
    _set_bar(df, 21, 100.2, 100.8, 98.6, 99.5)
    out = detect_sweeps(df, level_lookback=LOOKBACK)
    assert not out["sweep_long"].iloc[: WARMUP].any()
    assert not out["sweep_short"].iloc[: WARMUP].any()
    assert bool(out["sweep_long"].iloc[21])


def test_doji_zero_range_never_sweep():
    """range == 0 -> wick ratio NaN -> comparisons are False."""
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
        index=pd.date_range("2024-01-01", periods=25, freq="15min"),
    )
    out = detect_sweeps(df, level_lookback=LOOKBACK, min_penetration_atr=0.0)
    assert not out["sweep_long"].any()
    assert not out["sweep_short"].any()


# ---------------------------------------------------------------------------
# Deduplication: guide section 12
# ---------------------------------------------------------------------------

def test_apply_cooldown_matches_guide_pseudocode():
    """Exact parity with the guide's loop: accepted where pos - last > bars."""
    signal = np.zeros(16, dtype=bool)
    signal[[3, 5, 9, 10, 15]] = True
    accepted = apply_cooldown(signal, bars=4)
    assert list(np.flatnonzero(accepted)) == [3, 9, 15]


def test_apply_cooldown_bars_semantics():
    assert list(np.flatnonzero(apply_cooldown([True, True], bars=0))) == [0, 1]
    # 4-bar cooldown: positions 0 and 5 (gap 5 > 4) accepted, 4 (gap 4) not.
    sig = np.zeros(9, dtype=bool)
    sig[[0, 4, 5]] = True
    assert list(np.flatnonzero(apply_cooldown(sig, bars=4))) == [0, 5]


def test_apply_cooldown_rejects_negative_bars():
    with pytest.raises(ValueError, match="bars"):
        apply_cooldown([True], bars=-1)


def _cand(positions, direction="bullish", level="rolling_low", pens=None, reclaims=None):
    n = len(positions)
    return pd.DataFrame(
        {
            "position": list(positions),
            "direction": [direction] * n,
            "level_id": [level] * n,
            "penetration_atr": pens if pens is not None else [0.2] * n,
            "reclaim_atr": reclaims if reclaims is not None else [0.5] * n,
        }
    )


def test_group_picks_deepest_penetration_when_opted_in():
    """The retrospective ``deepest_penetration`` rule stays available explicitly."""
    cand = _cand([10, 11, 12, 20, 21], pens=[0.1, 0.4, 0.2, 0.3, 0.15])
    picked = group_candidate_events(cand, rule="deepest_penetration")
    assert list(picked["position"]) == [11, 20]  # runs [10..12] and [20..21]


def test_group_picks_first_bar_by_default():
    """The default run-representative is the run's first (causal) bar."""
    cand = _cand([10, 11, 12, 20, 21], pens=[0.1, 0.4, 0.2, 0.3, 0.15])
    picked = group_candidate_events(cand)
    assert list(picked["position"]) == [10, 20]


def test_group_rule_first_and_strongest_reclaim():
    cand = _cand([10, 11, 12], pens=[0.1, 0.4, 0.2], reclaims=[0.2, 0.5, 0.3])
    assert list(group_candidate_events(cand, rule="first")["position"]) == [10]
    assert list(group_candidate_events(cand, rule="strongest_reclaim")["position"]) == [11]


def test_group_tie_resolves_to_earliest_bar():
    cand = _cand([5, 6], pens=[0.3, 0.3])
    assert list(group_candidate_events(cand)["position"]) == [5]
    assert list(
        group_candidate_events(cand, rule="deepest_penetration")["position"]
    ) == [5]


def test_group_splits_on_direction_change_and_bar_gap():
    cand = pd.DataFrame(
        {
            "position": [10, 11, 12, 20, 30, 31],
            "direction": ["bullish"] * 3 + ["bullish"] + ["bearish"] * 2,
            "level_id": ["rolling_low"] * 4 + ["rolling_high"] * 2,
            "penetration_atr": [0.1, 0.2, 0.15, 0.3, 0.4, 0.1],
            "reclaim_atr": [0.1, 0.2, 0.15, 0.3, 0.4, 0.1],
        }
    )
    picked = group_candidate_events(cand)
    # [10..12] -> 10 (first), [20] -> 20, [30..31] -> 30 (first).
    assert list(picked["position"]) == [10, 20, 30]


def test_cooldown_suppresses_close_run_and_allows_distant_run():
    # Run 1 first bar at 21, run 2 first bar at 25: 25 - 21 = 4 <= 4 -> suppressed.
    cand = _cand([21, 22, 23, 25, 26], pens=[0.1, 0.4, 0.2, 0.3, 0.15])
    assert list(select_deduplicated_events(cand, cooldown_bars=4)["position"]) == [21]
    # Run 2 first bar at 28: 28 - 21 = 7 > 4 -> accepted.
    cand2 = _cand([21, 22, 23, 28, 29], pens=[0.1, 0.4, 0.2, 0.3, 0.15])
    assert list(select_deduplicated_events(cand2, cooldown_bars=4)["position"]) == [21, 28]
    # Same scenarios under the explicit retrospective rule: 22 and 22/28.
    assert list(
        select_deduplicated_events(
            cand, cooldown_bars=4, group_rule="deepest_penetration"
        )["position"]
    ) == [22]
    assert list(
        select_deduplicated_events(
            cand2, cooldown_bars=4, group_rule="deepest_penetration"
        )["position"]
    ) == [22, 28]


def test_opposite_directions_do_not_dedup_each_other():
    cand = pd.DataFrame(
        {
            "position": [10, 10, 12],
            "direction": ["bullish", "bearish", "bullish"],
            "level_id": ["rolling_low", "rolling_high", "rolling_low"],
            "penetration_atr": [0.2, 0.2, 0.1],
            "reclaim_atr": [0.5, 0.5, 0.4],
        }
    )
    picked = select_deduplicated_events(cand, cooldown_bars=4)
    assert list(picked["position"]) == [10, 10]


def test_candidate_validation_errors():
    with pytest.raises(ValueError, match="missing columns"):
        select_deduplicated_events(pd.DataFrame({"position": [1]}))
    cand = _cand([1, 1])
    with pytest.raises(ValueError, match="duplicate"):
        select_deduplicated_events(cand)
    cand_bad = _cand([1])
    cand_bad["position"] = cand_bad["position"].astype(float)
    with pytest.raises(TypeError, match="position"):
        select_deduplicated_events(cand_bad)
    with pytest.raises(ValueError, match="rule"):
        group_candidate_events(_cand([1]), rule="unknown")


# ---------------------------------------------------------------------------
# End-to-end: detector + dedup -> event table
# ---------------------------------------------------------------------------

def test_end_to_end_cluster_events_positions():
    """Crafted scenario, causal default: accepted event bars are [21, 28].

    With ``group_rule="first"`` (the system default) each run contributes its
    first bar: run 1 (21..23) -> 21, run 2 (28..29) -> 28, cooldown gap
    28 - 21 = 7 > 4 -> both accepted.
    """
    df = _cluster_frame(second_cluster=28)
    events = build_sweep_events(df, level_lookback=LOOKBACK)
    positions = [df.index.get_loc(t) for t in events["event_time"]]
    assert positions == [21, 28]
    assert list(events["direction"]) == [BULLISH, BULLISH]
    # The cooldown gap between the two accepted events is 7 > 4.
    assert positions[1] - positions[0] == 7 > 4


def test_end_to_end_cluster_deepest_penetration_opt_in():
    """The retrospective ``deepest_penetration`` rule stays available explicitly."""
    df = _cluster_frame(second_cluster=28)
    events = build_sweep_events(df, level_lookback=LOOKBACK, group_rule="deepest_penetration")
    positions = [df.index.get_loc(t) for t in events["event_time"]]
    assert positions == [22, 28]  # 22 = deepest-penetration bar of run 1


def test_end_to_end_close_run_suppressed_by_cooldown():
    """Second cluster 3 bars after run 1's first bar -> only run 1 survives."""
    df = _cluster_frame(second_cluster=25)
    events = build_sweep_events(df, level_lookback=LOOKBACK)
    positions = [df.index.get_loc(t) for t in events["event_time"]]
    # run 1 -> 21 (first), run 2 -> 25, gap 25 - 21 = 4 <= 4 -> suppressed.
    assert positions == [21]


def test_default_group_rule_is_first():
    """The event feed default is the strictly causal ``first`` rule."""
    df = _cluster_frame(second_cluster=28)
    default = build_sweep_events(df, level_lookback=LOOKBACK)
    first = build_sweep_events(df, level_lookback=LOOKBACK, group_rule="first")
    deep = build_sweep_events(df, level_lookback=LOOKBACK, group_rule="deepest_penetration")
    pd.testing.assert_frame_equal(default, first)
    # Documented difference: deep-pen may pick a later bar of a run (here 22).
    assert list(first["event_time"]) != list(deep["event_time"])


def test_build_sweep_events_reads_config_group_rule():
    """``config["sweep"]["group_rule"]`` drives dedup; explicit arg wins."""
    df = _cluster_frame(second_cluster=28)
    from_config = build_sweep_events(
        df, level_lookback=LOOKBACK, config={"sweep": {"group_rule": "deepest_penetration"}}
    )
    positions = [df.index.get_loc(t) for t in from_config["event_time"]]
    assert positions == [22, 28]

    # Explicit keyword overrides the config value.
    explicit = build_sweep_events(
        df,
        level_lookback=LOOKBACK,
        config={"sweep": {"group_rule": "deepest_penetration"}},
        group_rule="first",
    )
    assert [df.index.get_loc(t) for t in explicit["event_time"]] == [21, 28]

    # Unknown rule surfaces as a ValueError, from config or keyword.
    with pytest.raises(ValueError, match="group_rule"):
        build_sweep_events(df, level_lookback=LOOKBACK, config={"sweep": {"group_rule": "x"}})
    with pytest.raises(ValueError, match="group_rule"):
        build_sweep_events(df, level_lookback=LOOKBACK, group_rule="x")


def test_build_sweep_events_reads_other_params_from_config():
    """ATR/lookback/penetration/cooldown all resolve from the merged config."""
    df = _cluster_frame(second_cluster=28)
    blocking_pen = build_sweep_events(
        df, level_lookback=LOOKBACK, config={"sweep": {"min_penetration_atr": 0.5}}
    )
    # Run penetrations (0.20/0.38/0.37) are all below 0.5 -> no events.
    assert blocking_pen.empty
    default_pen = build_sweep_events(df, level_lookback=LOOKBACK)
    assert not default_pen.empty
    # group_rule + cooldown_bars from config behave identically to keywords.
    cfg_style = build_sweep_events(
        df,
        level_lookback=LOOKBACK,
        config={
            "sweep": {"cooldown_bars": 4, "group_rule": "first"},
        },
    )
    kw_style = build_sweep_events(
        df, level_lookback=LOOKBACK, cooldown_bars=4, group_rule="first"
    )
    pd.testing.assert_frame_equal(cfg_style, kw_style)


def test_run_representative_is_deepest_penetration():
    """Run 1's accepted bar carries the maximum penetration of the run (opt-in rule)."""
    df = _cluster_frame(second_cluster=28)
    out = detect_sweeps(df, level_lookback=LOOKBACK)
    cand_positions = np.flatnonzero(out["sweep_long"].fillna(False).to_numpy())
    assert list(cand_positions) == [21, 22, 23, 28, 29]
    run1 = out["low_pen_atr"].iloc[[21, 22, 23]]
    assert out["low_pen_atr"].iloc[22] == run1.max()
    events = build_sweep_events(
        df, level_lookback=LOOKBACK, group_rule="deepest_penetration"
    )
    ev22 = events.iloc[0]
    assert np.isclose(ev22["penetration_atr"], out["low_pen_atr"].iloc[22])


def test_event_rows_are_genuine_sweep_bars():
    """Every event row matches the causal detector metrics at its bar."""
    df = _cluster_frame(second_cluster=28)
    events = build_sweep_events(df, level_lookback=LOOKBACK)
    out = detect_sweeps(df, level_lookback=LOOKBACK)
    for _, row in events.iterrows():
        pos = df.index.get_loc(row["event_time"])
        assert bool(out["sweep_long"].iloc[pos])
        assert np.isclose(row["level_price"], out["liq_low"].iloc[pos])
        assert np.isclose(row["event_open"], out["open"].iloc[pos])
        assert np.isclose(row["event_close"], out["close"].iloc[pos])
        assert np.isclose(row["penetration_atr"], out["low_pen_atr"].iloc[pos])
        assert np.isclose(row["wick_ratio"], out["lower_wick_ratio"].iloc[pos])
        assert np.isclose(row["reclaim_atr"], out["low_reclaim_atr"].iloc[pos])


def test_both_sides_sweep_same_bar_produce_two_events():
    """A bar can pierce both levels; events keep distinct ids per direction."""
    idx = pd.date_range("2024-01-01", periods=24, freq="15min")
    df = pd.DataFrame(
        {"open": 101.0, "high": 102.0, "low": 100.0, "close": 101.0}, index=idx
    )
    _set_bar(df, 21, 100.7, 102.2, 99.8, 100.9)
    out = detect_sweeps(df, level_lookback=LOOKBACK)
    assert bool(out["sweep_long"].iloc[21])
    assert bool(out["sweep_short"].iloc[21])

    events = build_sweep_events(df, level_lookback=LOOKBACK)
    assert len(events) == 2
    assert set(events["direction"]) == {BULLISH, BEARISH}
    assert events["event_time"].iloc[0] == events["event_time"].iloc[1] == df.index[21]
    assert events["event_id"].nunique() == 2


def test_event_schema_exact():
    """The event table exposes exactly the guide 5.4 columns, in order."""
    df = _cluster_frame(second_cluster=28)
    events = build_sweep_events(df, level_lookback=LOOKBACK)
    expected = sweep_event_schema_columns()
    assert expected == [
        "event_id", "event_time", "direction", "level_id", "level_price",
        "event_open", "event_high", "event_low", "event_close",
        "penetration_atr", "wick_ratio", "reclaim_atr",
    ]
    assert list(events.columns) == expected
    assert events["event_id"].is_unique
    assert events["event_time"].is_monotonic_increasing
    assert set(events["direction"]) <= {BULLISH, BEARISH}
    assert events["level_id"].iloc[0] == "rolling_low"
    assert pd.api.types.is_float_dtype(events["level_price"])


def test_no_events_empty_table_keeps_schema():
    df = _channel(n=30)  # no bar ever pierces its level
    events = build_sweep_events(df, level_lookback=LOOKBACK)
    assert events.empty
    assert list(events.columns) == sweep_event_schema_columns()


def test_deduplicate_events_alias_matches():
    cand = _cand([21, 22, 23, 28, 29], pens=[0.1, 0.4, 0.2, 0.3, 0.15])
    pd.testing.assert_frame_equal(
        deduplicate_events(cand), select_deduplicated_events(cand)
    )


# ---------------------------------------------------------------------------
# Causality: no look-ahead
# ---------------------------------------------------------------------------

def _prefix_invariant(df: pd.DataFrame, cols, atol: float = 1e-9, **kw) -> None:
    full = detect_sweeps(df, **kw)
    for t in range(len(df)):
        prefix = detect_sweeps(df.iloc[: t + 1], **kw)
        for col in cols:
            a, b = full[col].iloc[t], prefix[col].iloc[-1]
            if type(a) is bool or isinstance(a, (bool, np.bool_)):
                assert bool(a) == bool(b), f"{col}[{t}]: flag mismatch (leak)"
            elif pd.isna(a) or pd.isna(b):
                assert pd.isna(a) and pd.isna(b), (
                    f"{col}[{t}]: NaN mismatch (full={a!r}, prefix={b!r})"
                )
            else:
                assert np.isclose(a, b, atol=atol), (
                    f"{col}[{t}]: full={a!r} prefix={b!r} -- future data leaked"
                )


def test_sweep_flags_causal_prefix_invariant():
    """Flags and every sweep metric at t must equal the prefix recompute at t."""
    df = _synthetic(n=45)
    cols = [
        "sweep_long", "sweep_short", "low_pen_atr", "high_pen_atr",
        "low_reclaim_atr", "high_reclaim_atr", "lower_wick_ratio",
        "upper_wick_ratio",
    ]
    _prefix_invariant(df, cols, level_lookback=LOOKBACK)


def test_sweep_metrics_causal_nondefault_params():
    df = _synthetic(n=40, seed=7)
    _prefix_invariant(
        df,
        ["sweep_long", "low_pen_atr", "lower_wick_ratio"],
        level_lookback=5,
        atr_period=8,
    )


def test_unsorted_frame_raises():
    """A shuffled frame must raise instead of silently leaking data."""
    df = _synthetic(n=30)
    shuffled = df.sample(frac=1.0, random_state=0)
    with pytest.raises(ValueError, match="monotonic"):
        detect_sweeps(shuffled, level_lookback=LOOKBACK)
    with pytest.raises(ValueError, match="monotonic"):
        build_sweep_events(shuffled, level_lookback=LOOKBACK)


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------

def test_input_not_mutated():
    df = _channel(n=30)
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    original = df.copy()
    detect_sweeps(df, level_lookback=LOOKBACK)
    build_sweep_events(df, level_lookback=LOOKBACK)
    pd.testing.assert_frame_equal(df, original)


def test_validation_errors():
    df = _channel()
    _set_bar(df, 21, 100.8, 101.0, 99.8, 100.9)
    with pytest.raises(ValueError, match="atr_period"):
        detect_sweeps(df, atr_period=0)
    with pytest.raises(ValueError, match="level_lookback"):
        detect_sweeps(df, level_lookback=0)
    with pytest.raises(ValueError, match="max_penetration_atr"):
        detect_sweeps(df, min_penetration_atr=0.6, max_penetration_atr=0.2)
    with pytest.raises(ValueError, match="min_penetration_atr"):
        detect_sweeps(df, min_penetration_atr=-0.1)
    with pytest.raises(ValueError, match="min_wick_ratio"):
        detect_sweeps(df, min_wick_ratio=1.5)
    with pytest.raises(ValueError, match="empty"):
        detect_sweeps(pd.DataFrame(columns=["open", "high", "low", "close"]))
    with pytest.raises(ValueError, match="low"):
        detect_sweeps(df.drop(columns=["low"]))
    with pytest.raises(TypeError, match="numeric"):
        bad = df.copy()
        bad["close"] = "nan"
        detect_sweeps(bad)


# ---------------------------------------------------------------------------
# Real-data smoke (skips when the raw export is absent)
# ---------------------------------------------------------------------------

def test_real_data_sweep_smoke_and_sampled_no_lookahead():
    if not os.path.exists(RAW_CSV):
        pytest.skip("raw MT5 CSV not present")
    from src.data.loader import load_ohlcv, normalize_ohlcv

    df = normalize_ohlcv(load_ohlcv(RAW_CSV))
    out = detect_sweeps(df, level_lookback=LOOKBACK)
    assert len(out) == len(df)
    assert not out["sweep_long"].iloc[: WARMUP].any()
    assert not out["sweep_short"].iloc[: WARMUP].any()

    events = build_sweep_events(df, level_lookback=LOOKBACK)
    assert len(events) > 0
    assert list(events.columns) == sweep_event_schema_columns()
    assert events["event_id"].is_unique
    assert events["event_time"].is_monotonic_increasing

    # Sampled prefix invariant on real data: full-frame vs prefix recompute.
    rng = np.random.default_rng(123)
    sample = sorted(
        rng.integers(WARMUP + 20, len(df) - 1, size=12).tolist()
    )
    full = detect_sweeps(df, level_lookback=LOOKBACK)
    for t in sample:
        prefix = detect_sweeps(df.iloc[: t + 1], level_lookback=LOOKBACK)
        assert bool(full["sweep_long"].iloc[t]) == bool(
            prefix["sweep_long"].iloc[-1]
        )
        assert bool(full["sweep_short"].iloc[t]) == bool(
            prefix["sweep_short"].iloc[-1]
        )
        for col in ("low_pen_atr", "low_reclaim_atr", "lower_wick_ratio"):
            a, b = full[col].iloc[t], prefix[col].iloc[-1]
            assert np.isclose(a, b, atol=1e-9), f"{col}[{t}] leaked at row {t}"