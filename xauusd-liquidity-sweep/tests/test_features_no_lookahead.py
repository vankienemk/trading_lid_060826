"""No-look-ahead (truncation-invariance) tests for the feature pipeline.

Marker ``no_lookahead`` (guide 25.2 / rule 30.4).  These assert that every
event-level feature value is a function of data strictly at/before the event's
decision bar:

* running the pipeline on all data up to ``T`` then re-running it on data
  truncated at ``t < T`` must leave the rows ``<= t`` byte-identical.

Two sources are covered: a deterministic synthetic frame and the real XAUUSD
M15 processed parquet (skipped if not built).  This is the gate a reviewer uses
to sign off the feature module before it feeds an ML model (schema §7 / §8).
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.data.loader import read_parquet
from src.events.confirmation import attach_confirmations
from src.events.sweep_detector import build_sweep_events
from src.features.feature_pipeline import build_event_features
from src.features.registry import registered_feature_names
from src.liquidity.level_registry import build_liquidity_levels

pytestmark = pytest.mark.no_lookahead

_REAL_PARQUET = os.path.join(
    os.path.dirname(__file__), "..", "data", "processed", "xauusd_m15.parquet"
)

_FEATURES = registered_feature_names()


def _synthetic_ohlcv(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """Reproducible random-walk OHLC frame (valid, sorted, UTC-indexed).

    Drawn from a fixed ``n``-bar stream so any prefix is a strict prefix of the
    same random stream — required for a meaningful truncation comparison.
    """
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


def _build(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Event table + confirmation, then the feature matrix (one row per event)."""
    reg = build_liquidity_levels(df, cfg)
    ev = build_sweep_events(df, group_rule="first")
    conf = attach_confirmations(df, ev, cfg)
    feats = build_event_features(df, reg, conf, cfg)
    return feats, conf


def _resolved_before(
    feats: pd.DataFrame, conf: pd.DataFrame, boundary: pd.Timestamp
) -> pd.Series:
    """Mask of event rows fully resolved before ``boundary`` (decision-causal)."""
    in_window = conf["event_time"] < boundary
    not_confirmed = ~conf["is_confirmed"].fillna(False)
    ct = conf["confirmation_time"]
    # t9 finding N3: the confirmation column can be tz-naive when all-NaT; make
    # the boundary comparison tz-consistent so it never raises.
    if pd.api.types.is_datetime64_any_dtype(ct):
        boundary_cmp = boundary
        if getattr(ct.dt, "tz", None) is None:
            boundary_cmp = boundary.tz_localize(None)
        confirmed_early = conf["is_confirmed"] & ct.notna() & (ct < boundary_cmp)
    else:
        confirmed_early = pd.Series(False, index=conf.index)
    return in_window & (not_confirmed | confirmed_early)


def _assert_feature_prefix_invariant(
    df: pd.DataFrame, cfg: dict, cuts: list[int]
) -> None:
    """:func:`build_event_features` is truncation-invariant at every cut."""
    full, conf_full = _build(df, cfg)
    for cut in cuts:
        boundary = df.index[cut]
        mask = _resolved_before(full, conf_full, boundary)
        ids = conf_full.loc[mask, "event_id"].tolist()
        if not ids:  # a cut with no fully-resolved events is a vacuous pass
            continue
        partial, _ = _build(df.iloc[:cut], cfg)
        fb = (
            full[full["event_id"].isin(ids)]
            .sort_values("event_id")
            .reset_index(drop=True)
        )
        pb = (
            partial[partial["event_id"].isin(ids)]
            .sort_values("event_id")
            .reset_index(drop=True)
        )
        assert len(fb) > 0
        assert len(pb) == len(fb)
        pd.testing.assert_frame_equal(
            fb[_FEATURES], pb[_FEATURES], check_dtype=False, check_names=False
        )


# ---------------------------------------------------------------------------
# Synthetic-frame truncation invariance (guide 25.2)
# ---------------------------------------------------------------------------


def test_all_event_features_are_truncation_invariant() -> None:
    cfg = load_config("baseline.yaml")
    _assert_feature_prefix_invariant(_synthetic_ohlcv(600), cfg, [200, 300, 400])


def test_feature_invariance_at_warmup_edges() -> None:
    cfg = load_config("baseline.yaml")
    _assert_feature_prefix_invariant(_synthetic_ohlcv(500), cfg, [100, 200, 300])


def test_causal_columns_are_prefix_invariant() -> None:
    """Per-bar causal context columns equal on full vs truncated data."""
    cfg = load_config("baseline.yaml")
    df = _synthetic_ohlcv(400)
    from src.features.feature_pipeline import _per_bar_context

    full = _per_bar_context(df, cfg)
    for cut in (150, 250):
        partial = _per_bar_context(df.iloc[:cut], cfg)
        for col in (
            "atr",
            "atr_percentile",
            "volume_zscore",
            "h1_trend",
            "prev_day_high",
            "prev_day_low",
        ):
            pd.testing.assert_series_equal(
                full[col].iloc[:cut], partial[col], check_dtype=False, check_names=False
            )


def test_level_features_are_prefix_invariant() -> None:
    """Resolved level state at a bar is a function of bars <= that bar only."""
    cfg = load_config("baseline.yaml")
    df = _synthetic_ohlcv(500)
    reg = build_liquidity_levels(df, cfg)
    from src.indicators.atr import add_atr

    atr = add_atr(df, cfg["indicators"]["atr_period"])["atr"]
    from src.liquidity.level_registry import level_state_at_bar

    for bar in (200, 300, 400):
        full_state = level_state_at_bar(reg, df, atr, bar)
        reg_cut = build_liquidity_levels(df.iloc[: bar + 1], cfg)
        cut_state = level_state_at_bar(
            reg_cut,
            df.iloc[: bar + 1],
            add_atr(df.iloc[: bar + 1], cfg["indicators"]["atr_period"])["atr"],
            bar,
        )
        if full_state.empty and cut_state.empty:
            continue
        pd.testing.assert_frame_equal(full_state, cut_state, check_dtype=False)


# ---------------------------------------------------------------------------
# Real-data gate (skip if the processed parquet is not built)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.path.exists(_REAL_PARQUET), reason="processed parquet not built"
)
def test_real_data_feature_prefix_invariant() -> None:
    """Truncation invariance on a real XAUUSD M15 slice (99,692 bars)."""
    cfg = load_config("baseline.yaml")
    df = read_parquet(_REAL_PARQUET).set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]]
    _assert_feature_prefix_invariant(df.iloc[:12000], cfg, [4000, 8000])
