"""Unit and contract tests for the causal feature pipeline (Agent 4, task t10)."""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.events.confirmation import attach_confirmations
from src.events.sweep_detector import build_sweep_events
from src.features.feature_pipeline import (
    FeaturePipelineError,
    build_event_features,
    session_flags_from_hour,
)
from src.features.registry import (
    FeatureRegistryError,
    load_features,
    parse_feature_list,
    registered_feature_names,
)
from src.features.sessions import session_flags, session_specs
from src.indicators.atr import add_atr
from src.liquidity.level_registry import build_liquidity_levels

_REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "configs", "features.yaml"
)
_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "artifacts", "feature_schemas", "features.json"
)

#: Every label / outcome column that must never appear as a model feature.
BANNED = [
    "outcome",
    "exit_time",
    "exit_price",
    "mfe_r",
    "mae_r",
    "bars_to_target",
    "bars_to_stop",
    "future_return",
]


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


def _fixture(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build (candles, registry, confirmed-events) on a synthetic frame."""
    df = _synthetic_ohlcv(600)
    events = build_sweep_events(df, group_rule="first")
    reg = build_liquidity_levels(df, cfg)
    conf = attach_confirmations(df, events, cfg)
    return df, reg, conf


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_registry_has_expected_feature_count() -> None:
    feats = load_features()
    assert len(feats) == 32
    assert len(set(f["name"] for f in feats)) == 32


def test_registry_all_features_are_causal() -> None:
    for f in load_features():
        assert f["uses_future_data"] is False
        assert f["available_at"] in ("event_time", "confirmation_time")


def test_registry_has_no_banned_label_column() -> None:
    names = registered_feature_names()
    for banned in BANNED:
        assert banned not in names


def test_feature_schema_json_matches_registry() -> None:
    assert os.path.exists(_SCHEMA_PATH)
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    feats = load_features()
    assert payload["feature_count"] == len(feats)
    json_names = [f["name"] for f in payload["features"]]
    assert json_names == [f["name"] for f in feats]
    for f in payload["features"]:
        assert f["uses_future_data"] is False


def test_registry_rejects_duplicate_name() -> None:
    registry = {
        "features": [
            {
                "name": "a",
                "group": "g",
                "description": "a",
                "dtype": "float64",
                "available_at": "event_time",
                "uses_future_data": False,
                "expected_range": [0, 1],
            },
            {
                "name": "a",
                "group": "g",
                "description": "a",
                "dtype": "float64",
                "available_at": "event_time",
                "uses_future_data": False,
                "expected_range": [0, 1],
            },
        ]
    }
    with pytest.raises(FeatureRegistryError, match="duplicate"):
        parse_feature_list(registry["features"])


def test_registry_rejects_banned_column() -> None:
    with pytest.raises(FeatureRegistryError, match="banned"):
        parse_feature_list(
            [
                {
                    "name": "mfe_r",
                    "group": "g",
                    "description": "x",
                    "dtype": "float64",
                    "available_at": "event_time",
                    "uses_future_data": False,
                    "expected_range": [0, 1],
                }
            ]
        )


def test_registry_rejects_lookahead_feature() -> None:
    with pytest.raises(FeatureRegistryError, match="uses_future_data"):
        parse_feature_list(
            [
                {
                    "name": "lookahead_x",
                    "group": "g",
                    "description": "x",
                    "dtype": "float64",
                    "available_at": "event_time",
                    "uses_future_data": True,
                    "expected_range": [0, 1],
                }
            ]
        )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def test_session_specs_parse_defaults() -> None:
    specs = session_specs({})
    assert set(specs) == {"asia", "london", "new_york"}


def test_session_hours_from_config() -> None:
    cfg = {
        "sessions": {
            "timezone": "UTC",
            "asia": [0, 8],
            "london": [7, 16],
            "new_york": [12, 21],
        }
    }
    assert session_flags(3, cfg)["session_asia"] is True
    assert session_flags(3, cfg)["session_london"] is False
    assert session_flags(10, cfg)["session_london"] is True
    assert session_flags(10, cfg)["session_asia"] is False
    assert session_flags(18, cfg)["session_new_york"] is True
    assert session_flags(23, cfg)["session_new_york"] is False
    assert session_flags(10, cfg)["session_overlap"] == 1


def test_session_wrap_midnight() -> None:
    cfg = {"sessions": {"asia": [20, 4]}}
    assert session_flags(22, cfg)["session_asia"] is True
    assert session_flags(2, cfg)["session_asia"] is True
    assert session_flags(12, cfg)["session_asia"] is False


def test_session_flags_from_hour_registered_only() -> None:
    flags = session_flags_from_hour(3, {})
    assert set(flags) == {"session_asia", "session_london", "session_new_york"}
    assert flags["session_asia"] is True


# ---------------------------------------------------------------------------
# Feature-matrix contract
# ---------------------------------------------------------------------------


def test_output_columns_match_registry() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    expected = ["event_id", "event_time", *registered_feature_names()]
    assert list(feats.columns) == expected


def test_output_has_no_label_or_outcome_column() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    for banned in BANNED:
        assert banned not in feats.columns


def test_one_row_per_event_and_unique_ids() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    assert len(feats) == len(conf)
    assert feats["event_id"].is_unique
    assert feats["event_id"].tolist() == conf["event_id"].tolist()


def test_confirmation_features_are_nan_for_unconfirmed() -> None:
    """With strict confirmation, synthetic events are all unconfirmed."""
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    conf_names = [
        "confirmation_delay_bars",
        "confirmation_range_atr",
        "confirmation_body_ratio",
        "confirmation_volume_zscore",
    ]
    assert conf["is_confirmed"].sum() == 0  # baseline thresholds: no confirms
    for col in conf_names:
        assert feats[col].isna().all()


def test_confirmation_features_populated_when_confirmed() -> None:
    """Permissive confirmation yields confirmed events with finite features."""
    cfg = load_config("baseline.yaml")
    df = _synthetic_ohlcv(600)
    events = build_sweep_events(df, group_rule="first")
    reg = build_liquidity_levels(df, cfg)
    conf = attach_confirmations(
        df,
        events,
        cfg,
        min_body_ratio=0.0,
        min_range_atr=0.0,
        require_break_sweep_extreme=False,
    )
    assert conf["is_confirmed"].sum() > 0
    feats = build_event_features(df, reg, conf, cfg)
    confirmed = feats[feats["confirmation_delay_bars"].notna()]
    assert len(confirmed) > 0
    assert confirmed["confirmation_delay_bars"].ge(1).all()
    assert confirmed["confirmation_range_atr"].notna().all()
    assert confirmed["confirmation_body_ratio"].notna().all()


def test_empty_events_return_empty_schema() -> None:
    cfg = load_config("baseline.yaml")
    df = _synthetic_ohlcv(600)
    reg = build_liquidity_levels(df, cfg)
    empty = pd.DataFrame(
        columns=[
            "event_id",
            "event_time",
            "direction",
            "level_id",
            "level_price",
            "event_open",
            "event_high",
            "event_low",
            "event_close",
            "penetration_atr",
            "wick_ratio",
            "reclaim_atr",
        ]
    )
    feats = build_event_features(df, reg, empty, cfg)
    assert feats.empty
    assert list(feats.columns)[:2] == ["event_id", "event_time"]
    assert len(feats.columns) == 2 + len(registered_feature_names())


# ---------------------------------------------------------------------------
# Hand-computed candle features
# ---------------------------------------------------------------------------


def test_candle_features_hand_computed() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    atr_frame = add_atr(df.iloc[: len(df)], period=cfg["indicators"]["atr_period"])
    first = conf.iloc[0]
    bar_pos = df.index.get_indexer([first["event_time"]])[0]
    atr = float(atr_frame["atr"].iloc[bar_pos])
    rng = first["event_high"] - first["event_low"]
    body = abs(first["event_close"] - first["event_open"])
    assert feats.loc[0, "range_atr"] == pytest.approx(rng / atr, rel=1e-6)
    assert feats.loc[0, "body_ratio"] == pytest.approx(body / rng, rel=1e-6)
    assert feats.loc[0, "close_location"] == pytest.approx(
        (first["event_close"] - first["event_low"]) / rng, rel=1e-6
    )
    assert feats.loc[0, "wick_ratio"] == pytest.approx(first["wick_ratio"], rel=1e-6)
    assert feats.loc[0, "penetration_atr"] == pytest.approx(
        first["penetration_atr"], rel=1e-6
    )


def test_session_and_hour_features_match_event_time() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    first = conf.iloc[0]
    ts = first["event_time"]
    assert feats.loc[0, "hour_utc"] == ts.hour
    assert feats.loc[0, "day_of_week"] == (
        ts.dayofweek if ts.dayofweek < 5 else ts.dayofweek - 5
    )


def test_time_features_are_causal_pure_function() -> None:
    """A session flag depends only on the event's own timestamp."""
    flags1 = session_flags_from_hour(5, {})
    flags2 = session_flags_from_hour(5, {})
    assert flags1 == flags2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unsorted_candles_rejected() -> None:
    cfg = load_config("baseline.yaml")
    df = _synthetic_ohlcv(120)
    df = df.sample(frac=1.0, random_state=0)
    # Registry and events content are irrelevant: the unsorted index must raise.
    empty_reg = pd.DataFrame()
    events = pd.DataFrame({"event_id": [], "event_time": [], "direction": []})
    with pytest.raises(FeaturePipelineError, match="chronologically"):
        build_event_features(df, empty_reg, events, cfg)


def test_missing_event_columns_rejected() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, _ = _fixture(cfg)
    bad = pd.DataFrame(
        {"event_id": ["x"], "event_time": [df.index[40]], "level_price": [1.0]}
    )
    with pytest.raises(FeaturePipelineError, match="required"):
        build_event_features(df, reg, bad, cfg)


def test_event_time_not_in_candles_rejected() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, _ = _fixture(cfg)
    bad = pd.DataFrame(
        {
            "event_id": ["x"],
            "event_time": [pd.Timestamp("2000-01-01 00:00:00+00:00")],
            "direction": ["bullish"],
            "level_id": ["rolling_low"],
            "level_price": [1.0],
            "event_open": [1.0],
            "event_high": [1.0],
            "event_low": [1.0],
            "event_close": [1.0],
            "penetration_atr": [0.1],
            "wick_ratio": [0.5],
            "reclaim_atr": [0.1],
        }
    )
    with pytest.raises(FeaturePipelineError, match="not present"):
        build_event_features(df, reg, bad, cfg)


# ---------------------------------------------------------------------------
# Level feature resolution
# ---------------------------------------------------------------------------


def test_level_features_resolved_from_registry() -> None:
    cfg = load_config("baseline.yaml")
    df, reg, conf = _fixture(cfg)
    feats = build_event_features(df, reg, conf, cfg)
    # With a populated registry, most events attribute to a real level type.
    assert feats["level_type"].isin(["rolling", "swing", "equal", "prev_day"]).all()
    assert (feats["level_type"] != "rolling").any()


def test_level_features_rolling_default_without_registry() -> None:
    cfg = load_config("baseline.yaml")
    df, _, conf = _fixture(cfg)
    empty = pd.DataFrame(columns=build_liquidity_levels(df, cfg).columns)
    feats = build_event_features(df, empty, conf, cfg)
    assert (feats["level_type"] == "rolling").all()
    assert feats["level_age_bars"].isna().all()
    assert feats["level_touch_count"].isna().all()
    assert not feats["level_already_partially_swept"].any()
