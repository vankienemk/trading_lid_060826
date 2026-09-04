"""Unit tests for Agent 5 labeling: triple barrier, excursions, costs, builder.

Covers the guide's mandatory test list (section 25.1/25.3): long triple
barrier, short triple barrier, same-bar ambiguity, MFE/MAE, costs, invalid
risk, ATR warm-up NaN, candle range 0, events near the dataset end, and the
wide/long dataset builders.  Every scenario uses hand-placed OHLC candles so
entry/stop/target/risk and every barrier ordering can be verified by hand.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src import schema
from src.labeling.excursions import (
    excursion_column_names,
    measure_excursions,
    measure_excursions_arrays,
)
from src.labeling.outcome_builder import (
    build_event_labels,
    build_trade_records,
    cost_in_r,
    format_reward,
    gross_result_in_r,
    labeling_defaults,
    outcome_column,
    resolve_labeling_config,
    target_column,
    wide_schema_columns,
)
from src.labeling.triple_barrier import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    EXIT_REASON_AMBIGUOUS,
    EXIT_REASON_STOP,
    EXIT_REASON_TARGET,
    EXIT_REASON_TIME,
    OUTCOME_TO_TOKEN,
    TOKEN_AMBIGUOUS,
    compute_trade_levels,
    label_event,
    label_event_arrays,
    label_long_event,
    label_short_event,
    outcome_to_token,
    token_to_outcome,
)

PARQUET = os.path.join(
    os.path.dirname(__file__), "..", "data", "processed", "xauusd_m15.parquet"
)


# ---------------------------------------------------------------------------
# Synthetic helpers
# ---------------------------------------------------------------------------

def _base(n: int = 44) -> pd.DataFrame:
    """Flat channel o=100 h=101 l=99 c=100 with a constant ATR column of 1.0."""
    idx = pd.date_range("2024-01-02", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=idx
    )
    df["atr"] = 1.0
    return df


def _set(df: pd.DataFrame, i: int, o: float, h: float, lo: float, c: float) -> None:
    row = df.index[i]
    df.loc[row, "open"] = o
    df.loc[row, "high"] = h
    df.loc[row, "low"] = lo
    df.loc[row, "close"] = c


def _long_tp_frame() -> pd.DataFrame:
    """Sweep at bar 20; bar 22 hits the 2R target before any stop."""
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)  # bullish sweep (extreme low 99.5)
    _set(df, 21, 100.0, 100.5, 99.8, 100.3)  # entry bar (open 100.0)
    _set(df, 22, 100.2, 101.3, 100.1, 101.0)  # high 101.3 >= target 101.2
    return df


def _short_tp_frame() -> pd.DataFrame:
    """Bearish sweep at bar 20; bar 22 hits the 2R target before any stop."""
    df = _base(44)
    _set(df, 20, 100.0, 100.5, 99.2, 99.4)  # bearish sweep (extreme high 100.5)
    _set(df, 21, 99.4, 100.1, 99.0, 99.6)  # entry bar (open 99.4)
    _set(df, 22, 99.3, 100.1, 96.8, 97.4)  # low 96.8 <= target 97.0
    return df


# ---------------------------------------------------------------------------
# Long triple barrier — TP first / SL first / time / same bar
# ---------------------------------------------------------------------------

def test_long_target_first_hand_computed():
    df = _long_tp_frame()
    r = label_long_event(df, 20)
    assert r is not None
    # entry = open of bar 21; stop = low[20] - 0.10*ATR; risk; 2R target
    assert r["entry"] == pytest.approx(100.0)
    assert r["stop"] == pytest.approx(99.5 - 0.10 * 1.0)
    assert r["risk"] == pytest.approx(0.6)
    assert r["target"] == pytest.approx(100.0 + 2.0 * 0.6)
    assert r["entry_pos"] == 21
    assert r["exit_pos"] == 22
    assert r["bars_held"] == 2
    assert r["outcome"] == 1
    assert r["exit_reason"] == EXIT_REASON_TARGET
    assert not r["ambiguous"]
    assert r["exit_price"] == pytest.approx(101.2)
    assert r["exit_time"] == df.index[22]
    # MFE/MAE include the full range of the exit candle (guide 16.2 order)
    assert r["mfe_r"] == pytest.approx((101.3 - 100.0) / 0.6)
    assert r["mae_r"] == pytest.approx((100.0 - 99.8) / 0.6)


def test_long_stop_first():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    _set(df, 21, 100.0, 100.5, 99.8, 100.3)
    _set(df, 22, 100.1, 100.9, 99.3, 99.6)  # low 99.3 <= stop 99.4
    r = label_long_event(df, 20)
    assert r is not None
    assert r["outcome"] == 0
    assert r["exit_reason"] == EXIT_REASON_STOP
    assert r["exit_pos"] == 22
    assert r["bars_held"] == 2
    assert r["exit_price"] == pytest.approx(99.4)


def test_long_time_barrier_defaults_to_mark_to_market():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    # bars 21..24 stay between stop 99.4 and 2R target 101.2
    for i, (h, lo, c) in enumerate(
        [(100.6, 99.6, 100.4), (100.5, 99.5, 100.2), (100.6, 99.6, 100.3), (100.8, 99.7, 100.5)],
        start=21,
    ):
        _set(df, i, 100.0, h, lo, c)
    r = label_long_event(df, 20, horizon=4)
    assert r is not None
    assert r["outcome"] == -1
    assert r["exit_reason"] == EXIT_REASON_TIME
    assert not r["ambiguous"]
    assert r["exit_pos"] == 24
    assert r["bars_held"] == 4
    assert r["exit_time"] == df.index[24]
    # mark_to_market: exit at close of the last horizon candle
    assert r["exit_price"] == pytest.approx(100.5)
    assert r["mfe_r"] == pytest.approx((100.8 - 100.0) / 0.6)
    assert r["mae_r"] == pytest.approx((100.0 - 99.5) / 0.6)


def test_long_time_barrier_zero_result_exits_at_entry():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    for i, (h, lo) in enumerate(
        [(100.6, 99.6), (100.5, 99.5), (100.6, 99.6), (100.8, 99.7)], start=21
    ):
        _set(df, i, 100.0, h, lo, 100.3)
    r = label_long_event(df, 20, horizon=4, time_barrier_result="zero")
    assert r is not None
    assert r["outcome"] == -1
    assert r["exit_reason"] == EXIT_REASON_TIME
    assert r["exit_price"] == pytest.approx(100.0)  # entry price


def test_long_same_bar_ambiguous_nan():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    # bar 21: low <= stop 99.4 AND high >= target 101.2 in the same candle
    _set(df, 21, 100.0, 101.4, 99.3, 100.0)
    r = label_long_event(df, 20)
    assert r is not None
    assert np.isnan(r["outcome"])
    assert r["exit_reason"] == EXIT_REASON_AMBIGUOUS
    assert r["ambiguous"]
    assert r["exit_pos"] == 21
    assert r["bars_held"] == 1
    assert np.isnan(r["exit_price"])
    assert r["exit_time"] == df.index[21]


def test_long_same_bar_conservative_stop_first():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    _set(df, 21, 100.0, 101.4, 99.3, 100.0)
    r = label_long_event(df, 20, same_bar_policy="conservative")
    assert r is not None
    assert r["outcome"] == 0
    assert r["exit_reason"] == EXIT_REASON_STOP
    assert r["exit_price"] == pytest.approx(99.4)


def test_long_same_bar_optimistic_target_first():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    _set(df, 21, 100.0, 101.4, 99.3, 100.0)
    r = label_long_event(df, 20, same_bar_policy="optimistic")
    assert r is not None
    assert r["outcome"] == 1
    assert r["exit_reason"] == EXIT_REASON_TARGET
    assert r["exit_price"] == pytest.approx(101.2)


# ---------------------------------------------------------------------------
# Short triple barrier (mirror)
# ---------------------------------------------------------------------------

def test_short_target_first_hand_computed():
    df = _short_tp_frame()
    r = label_short_event(df, 20)
    assert r is not None
    # entry = open of bar 21; stop = high[20] + 0.10*ATR
    assert r["entry"] == pytest.approx(99.4)
    assert r["stop"] == pytest.approx(100.5 + 0.10 * 1.0)
    assert r["risk"] == pytest.approx(1.2)
    assert r["target"] == pytest.approx(99.4 - 2.0 * 1.2)
    assert r["entry_pos"] == 21
    assert r["exit_pos"] == 22
    assert r["bars_held"] == 2
    assert r["outcome"] == 1
    assert r["exit_reason"] == EXIT_REASON_TARGET
    assert r["exit_price"] == pytest.approx(97.0)
    assert r["exit_time"] == df.index[22]
    assert r["mfe_r"] == pytest.approx((99.4 - 96.8) / 1.2)
    assert r["mae_r"] == pytest.approx((100.1 - 99.4) / 1.2)


def test_short_stop_first():
    df = _base(44)
    _set(df, 20, 100.0, 100.5, 99.2, 99.4)
    _set(df, 21, 99.4, 100.1, 99.0, 99.6)
    _set(df, 22, 99.3, 100.7, 98.2, 100.1)  # high 100.7 >= stop 100.6
    r = label_short_event(df, 20)
    assert r is not None
    assert r["outcome"] == 0
    assert r["exit_reason"] == EXIT_REASON_STOP
    assert r["exit_pos"] == 22
    assert r["exit_price"] == pytest.approx(100.6)


def test_short_same_bar_ambiguous_nan():
    df = _base(44)
    _set(df, 20, 100.0, 100.5, 99.2, 99.4)
    # bar 21: high >= stop 100.6 AND low <= target 97.0 in the same candle
    _set(df, 21, 99.4, 100.8, 96.5, 99.0)
    r = label_short_event(df, 20)
    assert r is not None
    assert np.isnan(r["outcome"])
    assert r["exit_reason"] == EXIT_REASON_AMBIGUOUS
    assert r["ambiguous"]
    assert r["bars_held"] == 1


def test_short_same_bar_conservative_stop_first():
    df = _base(44)
    _set(df, 20, 100.0, 100.5, 99.2, 99.4)
    _set(df, 21, 99.4, 100.8, 96.5, 99.0)
    r = label_short_event(df, 20, same_bar_policy="conservative")
    assert r is not None
    assert r["outcome"] == 0
    assert r["exit_reason"] == EXIT_REASON_STOP
    assert r["exit_price"] == pytest.approx(100.6)


def test_short_same_bar_optimistic_target_first():
    df = _base(44)
    _set(df, 20, 100.0, 100.5, 99.2, 99.4)
    _set(df, 21, 99.4, 100.8, 96.5, 99.0)
    r = label_short_event(df, 20, same_bar_policy="optimistic")
    assert r is not None
    assert r["outcome"] == 1
    assert r["exit_reason"] == EXIT_REASON_TARGET
    assert r["exit_price"] == pytest.approx(97.0)


# ---------------------------------------------------------------------------
# Reward multiples and entry/stop/risk geometry
# ---------------------------------------------------------------------------

def test_reward_target_prices():
    """Targets at 1R/1.5R/2R from one entry/stop pair."""
    df = _long_tp_frame()
    entry = 100.0
    stop = 99.4
    risk = entry - stop
    for reward in (1.0, 1.5, 2.0):
        r = label_long_event(df, 20, reward_r=reward)
        assert r is not None
        assert r["target"] == pytest.approx(entry + reward * risk)
        assert r["risk"] == pytest.approx(risk)


def test_compute_trade_levels_long_and_short():
    long_levels = compute_trade_levels(DIRECTION_LONG, 100.0, 99.5, 1.0, 2.0, 0.10)
    assert long_levels["stop"] == pytest.approx(99.4)
    assert long_levels["risk"] == pytest.approx(0.6)
    assert long_levels["target"] == pytest.approx(101.2)
    short_levels = compute_trade_levels(DIRECTION_SHORT, 99.4, 100.5, 1.0, 2.0, 0.10)
    assert short_levels["stop"] == pytest.approx(100.6)
    assert short_levels["risk"] == pytest.approx(1.2)
    assert short_levels["target"] == pytest.approx(97.0)


# ---------------------------------------------------------------------------
# Invalid events (guide 14.4 / 25.3)
# ---------------------------------------------------------------------------

def test_risk_zero_or_negative_returns_none():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.6, 100.6)  # stop = 99.5
    _set(df, 21, 99.4, 100.0, 99.2, 99.6)  # entry 99.4 < stop -> risk < 0
    assert label_long_event(df, 20) is None
    _set(df, 21, 99.5, 100.0, 99.2, 99.6)  # entry == stop -> risk == 0
    assert label_long_event(df, 20) is None


def test_atr_nan_warmup_returns_none():
    df = _base(44)
    df.loc[df.index[20], "atr"] = np.nan
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    assert label_long_event(df, 20) is None


def test_event_at_last_candle_returns_none():
    df = _base(44)
    assert label_long_event(df, len(df) - 1) is None  # no entry bar after
    assert label_short_event(df, len(df) - 1) is None


def test_no_access_beyond_dataset_end():
    df = _base(44)
    # event at len-2: entry = last candle; horizon truncated to the frame end
    _set(df, 42, 100.0, 100.8, 99.5, 100.6)
    _set(df, 43, 100.0, 100.6, 99.6, 100.3)
    r = label_long_event(df, 42, horizon=32)
    assert r is not None
    assert r["entry_pos"] == 43
    assert r["exit_pos"] == 43  # clamped: no access past len-1
    assert r["exit_time"] == df.index[43]
    assert r["outcome"] == -1  # time barrier on truncated window
    assert r["bars_held"] == 1


def test_zero_range_candle_is_labeled_consistently():
    """Range-0 (doji) candles never trigger barriers but do not crash."""
    df = _base(44)
    for i in range(21, 44):
        _set(df, i, 100.0, 100.0, 100.0, 100.0)  # all bars flat at entry price
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    r = label_long_event(df, 20, horizon=4)
    assert r is not None
    assert r["outcome"] == -1
    assert r["exit_reason"] == EXIT_REASON_TIME


# ---------------------------------------------------------------------------
# Frame validation
# ---------------------------------------------------------------------------

def test_frame_validation_errors():
    df = _base(10)
    with pytest.raises(ValueError, match="monotonic"):
        label_long_event(df.sample(frac=1.0, random_state=0), 2)
    with pytest.raises(ValueError, match="empty"):
        label_long_event(pd.DataFrame(columns=["open", "high", "low", "close", "atr"]), 0)
    with pytest.raises(ValueError, match="atr"):
        label_long_event(df.drop(columns=["atr"]), 2)
    with pytest.raises(TypeError, match="numeric"):
        bad = df.copy()
        bad["close"] = "nan"
        label_long_event(bad, 2)
    with pytest.raises(ValueError, match="event_pos"):
        label_long_event(df, -1)
    with pytest.raises(ValueError, match="horizon"):
        label_long_event(df, 2, horizon=0)
    with pytest.raises(ValueError, match="same_bar_policy"):
        label_long_event(df, 2, same_bar_policy="unknown")
    with pytest.raises(ValueError, match="time_barrier_result"):
        label_long_event(df, 2, time_barrier_result="unknown")
    with pytest.raises(ValueError, match="entry_pos"):
        label_long_event(df, 2, entry_pos=1)
    with pytest.raises(ValueError, match="direction"):
        label_event(df, 2, "sideways")


def test_input_not_mutated():
    df = _long_tp_frame()
    original = df.copy()
    label_long_event(df, 20)
    label_short_event(_short_tp_frame(), 20)
    measure_excursions(df, 21, 4, DIRECTION_LONG, 100.0, 0.6)
    pd.testing.assert_frame_equal(df, original)


def test_label_wrappers_match_core():
    df = _long_tp_frame()
    long_res = label_event(df, 20, DIRECTION_LONG)
    assert long_res is not None
    assert long_res == label_long_event(df, 20)
    short_df = _short_tp_frame()
    short_res = label_event(short_df, 20, DIRECTION_SHORT)
    assert short_res is not None
    assert short_res == label_short_event(short_df, 20)
    assert long_res["outcome"] == 1
    assert short_res["outcome"] == 1


# ---------------------------------------------------------------------------
# Numeric label <-> canonical token mapping
# ---------------------------------------------------------------------------

def test_token_mapping_bijection_matches_schema():
    tokens = set(OUTCOME_TO_TOKEN.values()) | {TOKEN_AMBIGUOUS}
    assert tokens == set(schema.OUTCOME_VALUES)
    for code, token in OUTCOME_TO_TOKEN.items():
        assert outcome_to_token(code) == token
        assert token_to_outcome(token) == code
    assert outcome_to_token(float("nan")) == TOKEN_AMBIGUOUS
    assert np.isnan(token_to_outcome(TOKEN_AMBIGUOUS))
    with pytest.raises(ValueError, match="unknown"):
        outcome_to_token(42.0)
    with pytest.raises(ValueError, match="unknown"):
        token_to_outcome("bogus")


# ---------------------------------------------------------------------------
# Fixed-window excursions (guide 15)
# ---------------------------------------------------------------------------

def test_excursions_long_hand_computed():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    for i, (h, lo, c) in enumerate(
        [(100.6, 99.6, 100.4), (100.5, 99.5, 100.2), (100.6, 99.6, 100.3), (100.8, 99.7, 100.5)],
        start=21,
    ):
        _set(df, i, 100.0, h, lo, c)
    e = measure_excursions(df, 21, 4, DIRECTION_LONG, entry_price=100.0, risk=0.6)
    assert e is not None
    assert e["n_bars"] == 4
    assert not e["window_truncated"]
    assert e["mfe"] == pytest.approx(0.8)
    assert e["mae"] == pytest.approx(0.5)
    assert e["mfe_r"] == pytest.approx(0.8 / 0.6)
    assert e["mae_r"] == pytest.approx(0.5 / 0.6)
    assert e["close_return"] == pytest.approx(0.5)
    assert e["close_return_r"] == pytest.approx(0.5 / 0.6)
    assert e["max_close_return_r"] == pytest.approx(0.5 / 0.6)
    assert e["min_close_return_r"] == pytest.approx(0.2 / 0.6)


def test_excursions_short_hand_computed():
    df = _base(44)
    _set(df, 20, 100.0, 100.5, 99.2, 99.4)
    for i, (h, lo, c) in enumerate(
        [(100.1, 99.0, 99.6), (100.1, 96.8, 97.4), (100.2, 99.0, 99.1), (100.5, 98.9, 99.2)],
        start=21,
    ):
        _set(df, i, 99.4, h, lo, c)
    e = measure_excursions(df, 21, 4, DIRECTION_SHORT, entry_price=99.4, risk=1.2)
    assert e is not None
    assert e["mfe"] == pytest.approx(99.4 - 96.8)
    assert e["mfe_r"] == pytest.approx(2.6 / 1.2)
    assert e["mae"] == pytest.approx(100.5 - 99.4)
    assert e["mae_r"] == pytest.approx(1.1 / 1.2)
    assert e["close_return_r"] == pytest.approx(0.2 / 1.2)
    assert e["max_close_return_r"] == pytest.approx(2.0 / 1.2)
    assert e["min_close_return_r"] == pytest.approx(-0.2 / 1.2)


def test_excursions_truncated_near_end():
    df = _base(44)
    # entry_pos = 42 leaves only 2 bars of a horizon-32 window
    e = measure_excursions(df, 42, 32, DIRECTION_LONG, entry_price=100.0, risk=1.0)
    assert e is not None
    assert e["n_bars"] == 2
    assert e["window_truncated"]
    assert e["end_pos"] == 43


def test_excursions_invalid_risk_returns_none():
    df = _base(44)
    assert measure_excursions(df, 21, 4, DIRECTION_LONG, 100.0, 0.0) is None
    assert measure_excursions(df, 21, 4, DIRECTION_LONG, 100.0, np.nan) is None


def test_array_fast_path_matches_frame_api():
    df = _long_tp_frame()
    arr = measure_excursions_arrays(
        highs=df["high"].to_numpy(),
        lows=df["low"].to_numpy(),
        closes=df["close"].to_numpy(),
        entry_pos=21,
        horizon=4,
        direction=DIRECTION_LONG,
        entry_price=100.0,
        risk=0.6,
    )
    frame = measure_excursions(df, 21, 4, DIRECTION_LONG, 100.0, 0.6)
    assert arr is not None and frame is not None
    for key in ("mfe_r", "mae_r", "close_return_r", "max_close_return_r", "min_close_return_r"):
        assert arr[key] == pytest.approx(frame[key])
    sim = label_event_arrays(
        highs=df["high"].to_numpy(),
        lows=df["low"].to_numpy(),
        opens=df["open"].to_numpy(),
        closes=df["close"].to_numpy(),
        atr=df["atr"].to_numpy(),
        times=df.index,
        event_pos=20,
        direction=DIRECTION_LONG,
        entry_pos=21,
    )
    assert sim == label_event(df, 20, DIRECTION_LONG)


def test_excursion_column_names():
    assert excursion_column_names(4) == [
        "mfe_r_h4",
        "mae_r_h4",
        "close_return_r_h4",
        "max_close_return_r_h4",
        "min_close_return_r_h4",
    ]


# ---------------------------------------------------------------------------
# Costs (guide 17)
# ---------------------------------------------------------------------------

def test_cost_accounting_hand_computed():
    # long 2R fill: entry 100, target 101.2, risk 0.6 -> gross +2R
    risk = 0.6
    assert gross_result_in_r(DIRECTION_LONG, 100.0, 101.2, risk) == pytest.approx(2.0)
    assert gross_result_in_r(DIRECTION_LONG, 100.0, 99.4, risk) == pytest.approx(-1.0)
    assert gross_result_in_r(DIRECTION_SHORT, 99.4, 97.0, 1.2) == pytest.approx(2.0)
    assert gross_result_in_r(DIRECTION_SHORT, 99.4, 100.6, 1.2) == pytest.approx(-1.0)
    # costs: 2 * (half_spread + slippage) / risk + commission_r
    cost = cost_in_r(risk=0.6, half_spread_price=0.03, slippage_price=0.01, commission_r=0.02)
    assert cost == pytest.approx(2.0 * 0.04 / 0.6 + 0.02)
    # gross - cost == net
    assert 2.0 - cost == pytest.approx(2.0 - (2.0 * 0.04 / 0.6 + 0.02))
    assert np.isnan(gross_result_in_r(DIRECTION_LONG, 100.0, np.nan, 0.6))


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def test_labeling_defaults():
    cfg = labeling_defaults()
    assert cfg["horizons"] == [4, 8, 16, 32]
    assert cfg["reward_r_values"] == [1.0, 1.5, 2.0]
    assert cfg["same_bar_policy"] == "ambiguous"
    assert cfg["primary_reward_r"] == 2.0
    assert cfg["primary_horizon"] == 16
    assert cfg["stop_buffer_atr"] == 0.10


def test_resolve_labeling_config_nested_and_flat():
    nested = {
        "labeling": {"horizons": [8, 16], "same_bar_policy": "conservative"},
        "costs": {"half_spread_price": 0.01},
        "stop": {"buffer_atr": 0.2},
    }
    cfg = resolve_labeling_config(nested)
    assert cfg["horizons"] == [8, 16]
    assert cfg["reward_r_values"] == [1.0, 1.5, 2.0]  # default preserved
    assert cfg["same_bar_policy"] == "conservative"
    assert cfg["half_spread_price"] == 0.01
    assert cfg["stop_buffer_atr"] == 0.2
    assert resolve_labeling_config(None) == labeling_defaults()


@pytest.mark.parametrize(
    "bad",
    [
        {"labeling": {"horizons": []}},
        {"labeling": {"horizons": [0]}},
        {"labeling": {"horizons": "abc"}},
        {"labeling": {"reward_r_values": []}},
        {"labeling": {"reward_r_values": [0.0]}},
        {"labeling": {"same_bar_policy": "nope"}},
        {"labeling": {"time_barrier_result": "nope"}},
        {"labeling": {"primary_reward_r": 3.0}},  # not in reward values
        {"labeling": {"primary_horizon": 3}},  # not in horizons
        {"costs": {"half_spread_price": -1}},
        {"costs": {"commission_r": "x"}},
        {"stop": {"buffer_atr": -0.1}},
        {"entry": {"mode": "whenever"}},
    ],
)
def test_resolve_labeling_config_rejects_invalid(bad):
    with pytest.raises(ValueError):
        resolve_labeling_config(bad)


def test_column_name_helpers():
    assert format_reward(1.0) == "1"
    assert format_reward(1.5) == "1_5"
    assert format_reward(2.0) == "2"
    assert outcome_column(2.0, 16) == "outcome_2r_h16"
    assert outcome_column(1.5, 4) == "outcome_1_5r_h4"
    assert target_column(1.0) == "target_1r"
    cols = wide_schema_columns(None)
    assert cols[0] == "event_id"
    assert "outcome_2r_h16" in cols
    assert "mfe_r_h8" in cols
    assert "net_result_r" in cols
    assert len(cols) == len(set(cols))


def test_entry_spread_price_keys_rejected_when_nonzero():
    """entry.spread_price is MT5 points, never read by the cost model: a
    nonzero value must fail loudly instead of silently understating costs."""
    with pytest.raises(ValueError, match="not read by the labeling cost model"):
        resolve_labeling_config({"entry": {"spread_price": 25.0}})


@pytest.mark.parametrize("key", ["spread_price", "slippage_price"])
def test_entry_price_keys_nonzero_rejected(key):
    with pytest.raises(ValueError, match="not read by the labeling cost model"):
        resolve_labeling_config({"entry": {key: 25.0}})
    # zero values (baseline.yaml shape) are fine and keep defaults
    cfg = resolve_labeling_config({"entry": {key: 0.0}})
    assert cfg["half_spread_price"] == 0.0
    assert cfg["slippage_price"] == 0.0


def test_costs_keys_are_price_units():
    """costs.*_price are PRICE units: cost_r scales linearly with risk."""
    cfg = resolve_labeling_config({"costs": {"half_spread_price": 0.15}})
    assert cfg["half_spread_price"] == 0.15
    # half spread of 0.15 on a 1.0-risk trade -> 2*0.15/1.0 = 0.30R
    assert cost_in_r(1.0, cfg["half_spread_price"], 0.0, 0.0) == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# Outcome builder — wide labeled dataset
# ---------------------------------------------------------------------------

def _event_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_build_event_labels_long_tp():
    df = _long_tp_frame()
    events = _event_frame(
        [
            {
                "event_id": "long_1",
                "event_time": df.index[20],
                "direction": "bullish",
                "level_id": "rolling_low",
                "level_price": 100.0,
            }
        ]
    )
    out = build_event_labels(df, events)
    assert list(out.columns) == wide_schema_columns(None)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["event_id"] == "long_1"
    assert row["direction"] == "long"  # normalized
    assert row["level_id"] == "rolling_low"
    assert row["level_price"] == pytest.approx(100.0)
    assert row["atr"] == pytest.approx(1.0)
    assert row["entry_time"] == df.index[21]
    assert row["entry_price"] == pytest.approx(100.0)
    assert row["stop_price"] == pytest.approx(99.4)
    assert row["risk_price"] == pytest.approx(0.6)
    assert row["target_1r"] == pytest.approx(100.6)
    assert row["target_1_5r"] == pytest.approx(100.9)
    assert row["target_2r"] == pytest.approx(101.2)
    # outcome tokens across combos: target reached at bar 22 inside h4
    for horizon in (4, 8, 16, 32):
        assert row[f"outcome_1r_h{horizon}"] == "tp"
        assert row[f"outcome_1_5r_h{horizon}"] == "tp"
        assert row[f"outcome_2r_h{horizon}"] == "tp"
    # primary (2R, h16) exit + results
    assert row["exit_reason"] == EXIT_REASON_TARGET
    assert row["exit_price"] == pytest.approx(101.2)
    assert row["exit_time"] == df.index[22]
    assert row["bars_held"] == 2
    assert row["bars_to_target"] == 2
    assert np.isnan(row["bars_to_stop"])
    assert not row["ambiguous"]
    assert row["gross_result_r"] == pytest.approx(2.0)
    assert row["cost_r"] == pytest.approx(0.0)
    assert row["net_result_r"] == pytest.approx(2.0)
    # windowed excursion for the primary horizon
    assert row["mfe_r_h16"] == pytest.approx((101.3 - 100.0) / 0.6)
    assert out.attrs["labeling_summary"]["n_valid"] == 1
    assert out.attrs["labeling_summary"]["n_invalid"] == 0


def test_build_event_labels_short_tp():
    df = _short_tp_frame()
    events = _event_frame(
        [
            {
                "event_id": "short_1",
                "event_time": df.index[20],
                "direction": "bearish",
                "level_id": "rolling_high",
                "level_price": 100.0,
            }
        ]
    )
    out = build_event_labels(df, events)
    row = out.iloc[0]
    assert row["direction"] == "short"
    assert row["entry_price"] == pytest.approx(99.4)
    assert row["stop_price"] == pytest.approx(100.6)
    assert row["risk_price"] == pytest.approx(1.2)
    assert row["target_2r"] == pytest.approx(97.0)
    assert row["outcome_2r_h16"] == "tp"
    assert row["exit_reason"] == EXIT_REASON_TARGET
    assert row["net_result_r"] == pytest.approx(2.0)


def test_build_event_labels_drops_invalid_and_reports():
    df = _base(44)
    # tp event: sweep at 12, target hit at bar 14
    _set(df, 12, 100.0, 100.8, 99.5, 100.6)
    _set(df, 13, 100.0, 100.6, 99.8, 100.4)
    _set(df, 14, 100.2, 101.3, 100.1, 101.0)
    # sl event: sweep at 20; the default entry bar 21 (low 99) stops out
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    events = _event_frame(
        [
            {"event_id": "tp_1", "event_time": df.index[12], "direction": "long"},
            {"event_id": "sl_1", "event_time": df.index[20], "direction": "long"},
            {"event_id": "bad_1", "event_time": df.index[43], "direction": "long"},  # no next bar
        ]
    )
    out = build_event_labels(df, events)
    assert len(out) == 2  # bad_1 dropped
    assert list(out["event_id"]) == ["tp_1", "sl_1"]
    assert out.iloc[0]["outcome_2r_h16"] == "tp"
    assert out.iloc[1]["outcome_2r_h16"] == "sl"
    assert out.iloc[1]["exit_reason"] == EXIT_REASON_STOP
    assert out.iloc[1]["net_result_r"] == pytest.approx(-1.0)
    invalid = out.attrs["labeling_invalid"]
    assert len(invalid) == 1
    assert invalid.iloc[0]["event_id"] == "bad_1"
    assert invalid.iloc[0]["reason"] == "no_next_bar"
    assert out.attrs["labeling_summary"]["n_invalid"] == 1


def test_build_event_labels_ambiguous_policy_and_flag():
    df = _base(44)
    # both barriers hit inside bar 21
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    _set(df, 21, 100.0, 101.4, 99.3, 100.0)
    events = _event_frame(
        [{"event_id": "amb_1", "event_time": df.index[20], "direction": "long"}]
    )
    out = build_event_labels(df, events)
    row = out.iloc[0]
    assert row["outcome_2r_h16"] == TOKEN_AMBIGUOUS
    assert row["exit_reason"] == EXIT_REASON_AMBIGUOUS
    assert row["ambiguous"]
    assert np.isnan(row["gross_result_r"])
    assert np.isnan(row["net_result_r"])
    assert np.isnan(row["bars_to_target"])
    assert np.isnan(row["bars_to_stop"])
    assert out.attrs["labeling_summary"]["n_ambiguous_primary"] == 1

    # conservative policy resolves the same event to a stop
    out_c = build_event_labels(df, events, {"labeling": {"same_bar_policy": "conservative"}})
    row_c = out_c.iloc[0]
    assert row_c["outcome_2r_h16"] == "sl"
    assert row_c["exit_reason"] == EXIT_REASON_STOP
    assert row_c["net_result_r"] == pytest.approx(-1.0)
    assert out_c.attrs["labeling_summary"]["n_ambiguous_primary"] == 0


def test_build_event_labels_time_barrier_mark_to_market_equals_close_return():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    # bars 21..43 stay between stop 99.4 and the 1R target 100.6: pure time exit
    for i in range(21, 44):
        _set(df, i, 100.0, 100.5, 99.5, 100.4)
    events = _event_frame(
        [{"event_id": "time_1", "event_time": df.index[20], "direction": "long"}]
    )
    out = build_event_labels(df, events)
    row = out.iloc[0]
    assert row["outcome_2r_h16"] == "time"
    assert row["exit_reason"] == EXIT_REASON_TIME
    assert row["bars_held"] == 16
    assert np.isnan(row["bars_to_target"])
    assert row["exit_time"] == df.index[36]
    assert row["exit_price"] == pytest.approx(100.4)
    assert row["net_result_r"] == pytest.approx((100.4 - 100.0) / 0.6)
    # ... which equals the fixed-window close return of h16
    assert row["net_result_r"] == pytest.approx(row["close_return_r_h16"])


def test_build_event_labels_costs_apply():
    df = _long_tp_frame()
    events = _event_frame(
        [{"event_id": "cost_1", "event_time": df.index[20], "direction": "long"}]
    )
    cfg = {
        "costs": {"half_spread_price": 0.03, "slippage_price": 0.01, "commission_r": 0.02}
    }
    out = build_event_labels(df, events, cfg)
    row = out.iloc[0]
    expected_cost = 2.0 * 0.04 / 0.6 + 0.02
    assert row["gross_result_r"] == pytest.approx(2.0)
    assert row["cost_r"] == pytest.approx(expected_cost)
    assert row["net_result_r"] == pytest.approx(2.0 - expected_cost)


def test_build_event_labels_confirmation_entry_mode():
    df = _long_tp_frame()
    # confirm at bar 22 -> entry at open of bar 23
    events = _event_frame(
        [
            {
                "event_id": "conf_1",
                "event_time": df.index[20],
                "direction": "long",
                "is_confirmed": True,
                "confirmation_time": df.index[22],
            },
            {
                "event_id": "unconf_1",
                "event_time": df.index[20],
                "direction": "long",
                "is_confirmed": False,
                "confirmation_time": pd.NaT,
            },
        ]
    )
    cfg = {"entry": {"mode": "next_open_after_confirmation"}}
    out = build_event_labels(df, events, cfg)
    assert list(out["event_id"]) == ["conf_1"]
    row = out.iloc[0]
    assert row["entry_time"] == df.index[23]
    assert row["entry_price"] == pytest.approx(df["open"].iloc[23])
    assert out.attrs["labeling_invalid"].iloc[0]["reason"] == "no_confirmation"


def test_build_event_labels_confirmation_mode_requires_columns():
    df = _long_tp_frame()
    events = _event_frame([{"event_id": "x", "event_time": df.index[20], "direction": "long"}])
    with pytest.raises(ValueError, match="confirmation"):
        build_event_labels(df, events, {"entry": {"mode": "next_open_after_confirmation"}})


def test_build_event_labels_missing_event_time_raises():
    df = _long_tp_frame()
    missing = df.index[20] + pd.Timedelta(minutes=7)  # off the 15-min grid
    events = _event_frame([{"event_id": "x", "event_time": missing, "direction": "long"}])
    with pytest.raises(ValueError, match="event_time"):
        build_event_labels(df, events)


def test_build_event_labels_promotes_timestamp_column():
    """Processed parquet stores timestamps as a column on a RangeIndex."""
    df = _long_tp_frame().reset_index(names="timestamp")
    events = _event_frame(
        [
            {
                "event_id": "promo_1",
                "event_time": df["timestamp"].iloc[20],
                "direction": "long",
            }
        ]
    )
    out = build_event_labels(df, events)
    assert len(out) == 1
    assert out.iloc[0]["entry_time"] == df["timestamp"].iloc[21]


def test_build_event_labels_auto_atr_and_inputs_immutable():
    df = _long_tp_frame().drop(columns=["atr"])
    events = _event_frame(
        [{"event_id": "auto_1", "event_time": df.index[20], "direction": "long"}]
    )
    candles_before = df.copy()
    events_before = events.copy()
    out = build_event_labels(df, events, {"indicators": {"atr_period": 2}})
    pd.testing.assert_frame_equal(df, candles_before)
    pd.testing.assert_frame_equal(events, events_before)
    assert out.iloc[0]["atr"] > 0  # auto-computed ATR at the sweep candle


def test_build_event_labels_unsorted_candles_raise():
    df = _long_tp_frame().sample(frac=1.0, random_state=7)
    events = _event_frame(
        [{"event_id": "x", "event_time": df.index[20], "direction": "long"}]
    )
    with pytest.raises(ValueError, match="monotonic"):
        build_event_labels(df, events)


# ---------------------------------------------------------------------------
# Outcome builder — long trade records (guide 5.6 schema)
# ---------------------------------------------------------------------------

def test_build_trade_records_long_form():
    expected_cols = [
        "event_id",
        "event_time",
        "direction",
        "reward_r",
        "horizon",
        "entry_time",
        "entry_price",
        "stop_price",
        "target_price",
        "risk_price",
        "outcome",
        "exit_time",
        "exit_price",
        "exit_reason",
        "mfe_r",
        "mae_r",
        "bars_held",
        "net_result_r",
        "ambiguous",
    ]
    long_df = _long_tp_frame()
    long_events = _event_frame(
        [{"event_id": "long_1", "event_time": long_df.index[20], "direction": "bullish"}]
    )
    recs = build_trade_records(long_df, long_events)
    assert len(recs) == 1 * 3 * 4  # events x rewards x horizons
    assert list(recs.columns) == expected_cols
    primary = recs[
        (recs["event_id"] == "long_1") & (recs["reward_r"] == 2.0) & (recs["horizon"] == 16)
    ]
    assert len(primary) == 1
    row = primary.iloc[0]
    assert row["direction"] == "long"
    assert row["outcome"] == "tp"
    assert row["exit_reason"] == EXIT_REASON_TARGET
    assert row["exit_price"] == pytest.approx(101.2)
    assert row["net_result_r"] == pytest.approx(2.0)
    assert row["mfe_r"] == pytest.approx((101.3 - 100.0) / 0.6)
    # every record of a given (reward,horizon) references its own target price
    r15 = recs[(recs["event_id"] == "long_1") & (recs["reward_r"] == 1.5)].iloc[0]
    assert r15["target_price"] == pytest.approx(100.9)

    short_df = _short_tp_frame()
    short_events = _event_frame(
        [{"event_id": "short_1", "event_time": short_df.index[20], "direction": "bearish"}]
    )
    short_recs = build_trade_records(short_df, short_events)
    assert len(short_recs) == 12
    short = short_recs[
        (short_recs["reward_r"] == 2.0) & (short_recs["horizon"] == 16)
    ].iloc[0]
    assert short["direction"] == "short"
    assert short["outcome"] == "tp"
    assert short["exit_price"] == pytest.approx(97.0)


def test_build_trade_records_ambiguous_has_nan_net():
    df = _base(44)
    _set(df, 20, 100.0, 100.8, 99.5, 100.6)
    _set(df, 21, 100.0, 101.4, 99.3, 100.0)
    events = _event_frame(
        [{"event_id": "amb_1", "event_time": df.index[20], "direction": "long"}]
    )
    recs = build_trade_records(df, events)
    assert len(recs) == 12
    assert (recs["outcome"] == TOKEN_AMBIGUOUS).all()
    assert recs["net_result_r"].isna().all()
    assert recs["ambiguous"].all()


def test_build_event_labels_wide_and_trade_records_agree_on_primary():
    df = _long_tp_frame()
    events = _event_frame(
        [{"event_id": "a", "event_time": df.index[20], "direction": "long"}]
    )
    wide = build_event_labels(df, events).iloc[0]
    records = build_trade_records(df, events)
    rec = records[
        (records["reward_r"] == 2.0) & (records["horizon"] == 16)
    ].iloc[0]
    assert wide["outcome_2r_h16"] == rec["outcome"]
    assert wide["exit_time"] == rec["exit_time"]
    assert wide["exit_reason"] == rec["exit_reason"]
    assert wide["bars_held"] == rec["bars_held"]
    assert wide["net_result_r"] == pytest.approx(rec["net_result_r"])
    assert bool(wide["ambiguous"]) == bool(rec["ambiguous"])


# ---------------------------------------------------------------------------
# Real-data smoke (skips when the processed parquet is absent)
# ---------------------------------------------------------------------------

def test_real_data_labeling_smoke():
    if not os.path.exists(PARQUET):
        pytest.skip("processed parquet not present")
    from src.events.sweep_detector import build_sweep_events

    # The parquet artifact stores timestamps in a column (RangeIndex index);
    # promote it to the canonical DatetimeIndex like the pipeline does.
    candles = pd.read_parquet(PARQUET).set_index("timestamp").sort_index()
    # QA finding F1: run-representative dedup (deepest_penetration) picks a
    # hindsight bar mid-run.  Labeling must be fed the causal rule so
    # event_time == first sweep bar of the run (decision bar is real-time).
    events = build_sweep_events(candles, group_rule="first")
    assert len(events) > 0

    labeled = build_event_labels(candles, events)
    assert len(labeled) > 0
    assert list(labeled.columns) == wide_schema_columns(None)
    assert labeled["event_id"].is_unique
    assert labeled["event_time"].is_monotonic_increasing
    assert set(labeled["direction"]) <= {"long", "short"}
    token_values = set(schema.OUTCOME_VALUES)
    for col in ("outcome_1r_h16", "outcome_1_5r_h16", "outcome_2r_h16"):
        assert set(labeled[col]) <= token_values
    # sample events of both directions exist
    assert labeled["direction"].nunique() == 2
    # invalid events (no next bar etc.) are reported, not silently dropped
    assert "labeling_invalid" in labeled.attrs
    assert labeled.attrs["labeling_summary"]["n_valid"] == len(labeled)

    records = build_trade_records(candles, events)
    assert len(records) == len(labeled) * 3 * 4
    assert records["net_result_r"].notna().sum() > 0
