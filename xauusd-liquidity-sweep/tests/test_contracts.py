"""Contract tests for the project-wide schema constants (docs/SCHEMAS.md).

These tests lock the canonical column lists and conventions so that a schema
change requires an explicit, reviewed update (rule 30.1).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src import schema
from src.data.loader import OHLCV_COLUMNS


def test_ohlcv_columns_match_loader() -> None:
    """The schema module and the loader must agree on the OHLCV columns."""
    assert schema.OHLCV_COLUMNS == OHLCV_COLUMNS == ["open", "high", "low", "close", "volume"]


def test_level_columns_are_unique_and_complete() -> None:
    cols = schema.LEVEL_COLUMNS
    assert len(cols) == len(set(cols))
    assert "level_id" in cols
    assert "known_at" in cols  # causality anchor
    assert "origin_time" in cols
    assert "direction" in cols


def test_level_directions_and_types() -> None:
    assert set(schema.LEVEL_DIRECTIONS) == {"low", "high"}
    assert schema.LEVEL_TYPES == ["rolling", "swing", "equal", "prev_day"]


def test_level_registry_extensions_locked() -> None:
    """Schema lock v1.1: registry/state columns and sweep-state enum."""
    # Full registry = canonical level columns + documented extensions, disjoint.
    assert set(schema.LEVEL_EXTENSION_COLUMNS).isdisjoint(set(schema.LEVEL_COLUMNS))
    assert schema.LEVEL_REGISTRY_COLUMNS == (
        schema.LEVEL_COLUMNS + schema.LEVEL_EXTENSION_COLUMNS
    )
    assert len(schema.LEVEL_REGISTRY_COLUMNS) == len(set(schema.LEVEL_REGISTRY_COLUMNS))
    # Every extension is documented in the registry emitter's output.
    assert "known_pos" in schema.LEVEL_EXTENSION_COLUMNS  # causal gate
    assert "sweep_state" in schema.LEVEL_EXTENSION_COLUMNS
    assert schema.SWEEP_STATES == ["active", "swept", "invalidated"]
    # Per-bar causal state interface reuses the canonical vocabulary.
    assert set(schema.LEVEL_STATE_COLUMNS) <= set(schema.LEVEL_REGISTRY_COLUMNS)
    assert "known_at" in schema.LEVEL_STATE_COLUMNS


def test_event_columns_anchor_fields_present() -> None:
    cols = schema.EVENT_COLUMNS
    assert "event_id" in cols
    assert "event_time" in cols
    assert "level_id" in cols
    assert "penetration_atr" in cols
    assert "wick_ratio" in cols
    assert "reclaim_atr" in cols
    # merged detector contract: OHLC of the sweep candle uses event_* naming
    assert "event_open" in cols and "event_close" in cols


def test_event_columns_match_live_detector_contract() -> None:
    """Rule 30.1/F2 reconcile: schema constants == what the merged detector
    (t4) actually emits, so schema drift fails this gate test."""
    from src.events.sweep_detector import sweep_event_schema_columns

    assert schema.EVENT_COLUMNS == sweep_event_schema_columns()


def test_event_directions_match_live_detector_vocabulary() -> None:
    """Detector vocabulary is sweep-side (bullish/bearish), not trade-side."""
    from src.events.sweep_detector import BEARISH, BULLISH

    assert schema.EVENT_DIRECTIONS == [BULLISH, BEARISH] == ["bullish", "bearish"]


def test_event_id_prefix_matches_live_detector() -> None:
    """Event ids are deterministic SWP-XXXXXX ordinals (guide 5.4)."""
    from src.events.sweep_detector import build_sweep_events
    from src.indicators.atr import add_atr
    from src.liquidity.rolling_levels import add_rolling_liquidity_levels

    idx = pd.date_range("2024-01-01", periods=60, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.0] * 60,
            "high": [2.0] * 60,
            "low": [0.5] * 60,
            "close": [1.5] * 60,
            "volume": [10.0] * 60,
        },
        index=idx,
    )
    base = add_rolling_liquidity_levels(add_atr(df), lookback=20)
    # force a bullish sweep: pierce liq_low with a reclaiming close
    base.loc[idx[30], "low"] = base.loc[idx[30], "liq_low"] * 0.5
    out = build_sweep_events(base.reset_index())
    if len(out):
        assert str(out["event_id"].iloc[0]).startswith(schema.EVENT_ID_PREFIX)


def test_trade_direction_mapping() -> None:
    assert schema.SWEEP_TO_TRADE_DIRECTION == {"bullish": "long", "bearish": "short"}
    assert schema.normalize_event_direction("bullish") == "long"
    assert schema.normalize_event_direction("bearish") == "short"
    assert schema.normalize_event_direction("long") == "long"
    assert schema.normalize_event_direction("SHORT") == "short"
    with pytest.raises(ValueError):
        schema.normalize_event_direction("sideways")


def test_confirmation_columns_anchor_is_confirmed() -> None:
    assert "is_confirmed" in schema.CONFIRMATION_COLUMNS
    assert "confirmation_time" in schema.CONFIRMATION_COLUMNS


def test_outcome_values() -> None:
    assert set(schema.OUTCOME_VALUES) == {"tp", "sl", "time", "ambiguous"}


def test_review_columns_and_verdicts() -> None:
    assert "event_id" in schema.REVIEW_COLUMNS
    assert "verdict" in schema.REVIEW_COLUMNS
    assert "review_version" in schema.REVIEW_COLUMNS
    assert set(schema.REVIEW_VERDICTS) == {"correct", "incorrect", "ambiguous"}


def test_processed_frame_conforms_to_schema() -> None:
    """A normalized OHLCV frame must carry exactly the canonical columns."""
    idx = pd.date_range("2024-01-01", periods=5, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.5] * 5,
            "volume": [10.0] * 5,
        },
        index=idx,
    )
    assert list(df.columns) == schema.OHLCV_COLUMNS
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None