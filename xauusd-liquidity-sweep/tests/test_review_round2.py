# mypy: ignore-errors
# plot tests import matplotlib (no stubs); same
# rationale as src/visualization/event_chart.py.

"""Unit tests for human review round 2 (task t12, Phase 5b).

Synthetic candles/levels verify: the registry->review-row builder
(detector_pass FP probe), deterministic stratified sampling of the two pools
(confirmed + swing/equal/prev-day), the round-2 verdict incl. confirmation
delay and detector_pass logic, and the versioned CSV columns (round-1 file is
never touched).  No heavy full-frame registry build is needed here.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from src.visualization.review_round2 import (  # noqa: E402
    REVIEW2_CSV_COLUMNS,
    REVIEW_VERSION,
    assess_v2,
    build_level_sweep_rows,
    sample_round2,
    write_review2_csv,
)


def _candles(n: int = 420) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(5)
    close = 100 + np.cumsum(rng.normal(0.0, 0.2, n))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": 100.0,
        },
        index=idx,
    )
    df["atr"] = 0.5
    return df


def _confirmed_pool(n: int = 260) -> pd.DataFrame:
    candles = _candles()
    rows = []
    tokens = ["tp", "sl", "time", "ambiguous"]
    for i in range(n):
        pos = 60 + i  # keep every event inside the 420-bar frame
        rows.append(
            {
                "event_id": f"SWP-{i:06d}",
                "event_time": candles.index[pos],
                "direction": "long" if i % 2 == 0 else "short",
                "level_id": "rolling_low",
                "level_price": 100.0,
                "penetration_atr": 0.2,
                "wick_ratio": 0.6,
                "reclaim_atr": 0.3,
                "outcome_2r_h16": tokens[i % 4],
                "exit_reason": tokens[i % 4],
                "is_confirmed": True,
                "confirmation_delay_bars": 1 + i % 3,
            }
        )
    return pd.DataFrame(rows)


def _level_rows(n: int = 200) -> pd.DataFrame:
    candles = _candles()
    rows = []
    ltypes = ["swing", "equal", "prev_day"]
    for i in range(n):
        pos = 70 + i  # keep every event inside the 420-bar frame
        rows.append(
            {
                "event_id": f"LVL-{i:06d}",
                "event_time": candles.index[pos],
                "direction": "long" if i % 2 == 0 else "short",
                "level_id": f"{ltypes[i % 3]}_low_{i}",
                "level_type": ltypes[i % 3],
                "level_price": 100.0,
                "penetration_atr": 0.3,
                "wick_ratio": 0.55,
                "reclaim_atr": 0.2,
                "detector_pass": i % 2 == 0,
            }
        )
    return pd.DataFrame(rows)


def test_build_level_sweep_rows_metrics_and_ids():
    candles = _candles(200)
    # three registry rows: swing low swept deep, equal high swept shallow,
    # prev_day never penetrated (first_swept_at NaT -> dropped)
    registry = pd.DataFrame(
        [
            {
                "level_id": "swing_low_1", "level_type": "swing", "direction": "low",
                "price": 100.0, "known_at": candles.index[40],
                "first_swept_at": candles.index[60], "is_h1": False,
            },
            {
                "level_id": "equal_high_1", "level_type": "equal", "direction": "high",
                "price": 101.0, "known_at": candles.index[40],
                "first_swept_at": candles.index[80], "is_h1": False,
            },
            {
                "level_id": "prev_low_1", "level_type": "prev_day", "direction": "low",
                "price": 99.0, "known_at": candles.index[40],
                "first_swept_at": pd.NaT, "is_h1": False,
            },
        ]
    )
    out = build_level_sweep_rows(candles, registry)
    assert len(out) == 2
    assert out["event_id"].is_unique
    assert set(out["level_type"]) == {"swing", "equal"}
    assert set(out.columns) >= {
        "event_id", "event_time", "direction", "level_id", "level_type",
        "level_price", "penetration_atr", "wick_ratio", "reclaim_atr",
        "detector_pass",
    }
    assert out["event_time"].is_monotonic_increasing


def test_sample_round2_deterministic_and_stratified():
    candles = _candles()
    confirmed = _confirmed_pool()
    levels = _level_rows()
    a = sample_round2(candles, confirmed, levels, n=100, seed=11)
    b = sample_round2(candles, confirmed, levels, n=100, seed=11)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 100
    assert a["event_id"].is_unique
    assert set(a["event_id"].str[:3]) == {"SWP", "LVL"}
    n_lvl = int((a["event_id"].str.startswith("LVL-")).sum())
    assert 0 < n_lvl < 100
    # both directions always represented
    assert a["direction"].nunique() == 2
    # confirmed sub-sample keeps all primary outcome tokens
    conf = a[a["event_id"].str.startswith("SWP-")]
    assert set(conf["outcome_2r_h16"]) == {"tp", "sl", "time", "ambiguous"}


def test_assess_v2_verdicts_and_delay():
    base = {
        "event_id": "LVL-000001",
        "direction": "long",
        "source": "level_sweep",
        "level_type": "swing",
        "outcome_2r_h16": "sl",
        "exit_reason": "stop",
        "wick_ratio": 0.7,
        "penetration_atr": 0.3,
        "reclaim_atr": 0.4,
        "detector_pass": True,
        "is_confirmed": False,
    }
    ok = assess_v2({**base})
    assert ok["verdict"] == "correct"
    assert ok["review_version"] == REVIEW_VERSION
    assert ok["review_id"] == "R2-LVL-000001"
    assert ok["source"] == "level_sweep"

    # new-level penetration that would fail the baseline rules -> FP candidate
    fp = assess_v2({**base, "detector_pass": False, "wick_ratio": 0.2})
    assert fp["verdict"] == "incorrect"
    assert fp["detector_correct"] == "no"

    # confirmed event with slow confirmation is noted, verdict stays from sweep
    conf = assess_v2(
        {
            **base, "source": "confirmed", "level_type": "rolling",
            "is_confirmed": True, "confirmation_delay_bars": 3.0,
            "confirmation_type": "close_break",
        }
    )
    assert conf["verdict"] == "correct"
    assert "delay AT max_wait" in conf["notes"]

    amb = assess_v2({**base, "outcome_2r_h16": "ambiguous", "exit_reason": "ambiguous"})
    assert amb["verdict"] == "ambiguous"


def test_review2_csv_columns_roundtrip(tmp_path):
    rows = [
        assess_v2(
            {
                "event_id": "LVL-000001", "direction": "short", "source": "level_sweep",
                "level_type": "equal", "outcome_2r_h16": "time", "exit_reason": "time",
                "wick_ratio": 0.6, "penetration_atr": 0.2, "reclaim_atr": 0.2,
                "detector_pass": True, "is_confirmed": False,
            }
        ),
        assess_v2(
            {
                "event_id": "SWP-000001", "direction": "long", "source": "confirmed",
                "level_type": "rolling", "outcome_2r_h16": "tp", "exit_reason": "target",
                "wick_ratio": 0.6, "penetration_atr": 0.25, "reclaim_atr": 0.3,
                "detector_pass": True, "is_confirmed": True,
                "confirmation_delay_bars": 1.0, "confirmation_type": "displacement",
            }
        ),
    ]
    path = write_review2_csv(rows, f"{tmp_path}/manual_review_v2.csv")
    df = pd.read_csv(path)
    assert list(df.columns) == REVIEW2_CSV_COLUMNS
    assert df["review_version"].nunique() == 1
    assert df["review_id"].is_unique
    assert set(df["verdict"]) <= {"correct", "incorrect", "ambiguous"}
    assert df["source"].tolist() == ["level_sweep", "confirmed"]
    # round-1 file untouched (we only ever write *_v2 names here)
    assert not os.path.exists(f"{tmp_path}/manual_review.csv")
