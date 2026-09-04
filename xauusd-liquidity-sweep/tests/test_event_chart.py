# mypy: ignore-errors
# src/visualization/event_chart.py for the same rationale.

"""Tests for round-1 event charts and manual review tooling (task t7).

Synthetic candles/events verify the deterministic sampler (guide 24: 100-200
diverse events), the first-pass screening heuristics, chart rendering, and the
grouped export + manual_review.csv round trip — without any real-data
dependency.  The real-data smoke reproduces the actual reports/ artifacts when
the processed parquet is present.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from src import schema

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from src.visualization.event_chart import (  # noqa: E402
    OUTCOME_GROUP_DIRS,
    REVIEW_CSV_COLUMNS,
    REVIEW_VERSION,
    assess_event,
    plot_event_chart,
    render_event_charts,
    run_round1_review,
    sample_review_events,
)

PARQUET = os.path.join(
    os.path.dirname(__file__), "..", "data", "processed", "xauusd_m15.parquet"
)

TOKENS = ["tp", "sl", "time", "ambiguous"]


def _candles(n: int = 420, start: str = "2024-01-01") -> pd.DataFrame:
    """Smooth synthetic candles with a datetime index and rolling levels."""
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0.0, 0.2, n))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + np.abs(rng.normal(0.3, 0.1, n)),
            "low": close - np.abs(rng.normal(0.3, 0.1, n)),
            "close": close,
        },
        index=idx,
    )
    df["liq_low"] = df["low"].shift(1).rolling(20).min()
    df["liq_high"] = df["high"].shift(1).rolling(20).max()
    return df


def _wide_event(candles: pd.DataFrame, pos: int, event_id: str, direction: str,
                outcome: str, metrics: tuple[float, float, float]) -> pd.Series:
    entry_pos = pos + 1
    return pd.Series(
        {
            "event_id": event_id,
            "event_time": candles.index[pos],
            "direction": direction,
            "level_id": "rolling_low" if direction == "long" else "rolling_high",
            "level_price": float(candles["close"].iloc[pos]),
            "atr": 0.4,
            "entry_time": candles.index[entry_pos],
            "entry_price": float(candles["open"].iloc[entry_pos]),
            "stop_price": float(candles["open"].iloc[entry_pos]) - 0.5,
            "risk_price": 0.5,
            "target_1r": float(candles["open"].iloc[entry_pos]) + 0.5,
            "target_1_5r": float(candles["open"].iloc[entry_pos]) + 0.75,
            "target_2r": float(candles["open"].iloc[entry_pos]) + 1.0,
            "outcome_2r_h16": outcome,
            "exit_time": candles.index[min(entry_pos + 5, len(candles) - 1)],
            "exit_price": float(candles["close"].iloc[min(entry_pos + 5, len(candles) - 1)]),
            "exit_reason": {"tp": "target", "sl": "stop", "time": "time", "ambiguous": "ambiguous"}[outcome],
            "bars_held": 6,
            "ambiguous": outcome == "ambiguous",
            "net_result_r": 0.0 if outcome == "ambiguous" else 1.0,
            "mfe_r_h16": 1.2,
            "mae_r_h16": 0.4,
            "penetration_atr": metrics[0],
            "wick_ratio": metrics[1],
            "reclaim_atr": metrics[2],
        }
    )


def _labeled_and_events(candles: pd.DataFrame, k: int = 160) -> tuple[pd.DataFrame, pd.DataFrame]:
    """k events at even spacing covering all outcomes and both directions."""
    start_pos = 60
    step = 2
    rows = []
    for i in range(k):
        pos = start_pos + i * step
        direction = "long" if i % 2 == 0 else "short"
        outcome = TOKENS[i % len(TOKENS)]
        pen = 0.05 + 0.4 * (i % 5) / 4.0
        wick = 0.35 + 0.55 * ((i // 5) % 5) / 4.0
        rows.append(_wide_event(candles, pos, f"E{i:04d}", direction, outcome, (pen, wick, 0.2)))
    labeled = pd.DataFrame(rows)
    events = labeled[
        ["event_id", "event_time", "direction", "level_id", "level_price",
         "penetration_atr", "wick_ratio", "reclaim_atr"]
    ].copy()
    return labeled, events


# ---------------------------------------------------------------------------
# Sampler (guide 24)
# ---------------------------------------------------------------------------

def test_sampler_deterministic_and_diverse():
    candles = _candles()
    labeled, _ = _labeled_and_events(candles)
    a = sample_review_events(candles, labeled, n=140, seed=42)
    b = sample_review_events(candles, labeled, n=140, seed=42)
    pd.testing.assert_frame_equal(a, b)
    assert 100 <= len(a) <= 140
    assert a["event_id"].is_unique
    assert set(a["outcome_2r_h16"]) <= set(TOKENS)
    # both directions present when the pool has them
    assert a["direction"].nunique() == 2
    # ambiguous events were all absorbed into the sample (small pool)
    assert set(a["outcome_2r_h16"]) == set(TOKENS)


def test_sampler_rejects_bad_inputs():
    candles = _candles()
    labeled, _ = _labeled_and_events(candles, k=120)
    with pytest.raises(ValueError, match="n must be in"):
        sample_review_events(candles, labeled, n=50)
    bad = labeled.rename(columns={"outcome_2r_h16": "outcome_x"})
    with pytest.raises(ValueError, match="outcome column"):
        sample_review_events(candles, bad, n=110)


def test_sampler_requires_complete_windows():
    candles = _candles(n=300)
    labeled, _ = _labeled_and_events(candles, k=80)  # too few eligible -> <100
    with pytest.raises(ValueError, match="eligible"):
        sample_review_events(candles, labeled, n=120)


# ---------------------------------------------------------------------------
# Screening heuristics
# ---------------------------------------------------------------------------

def _base_review_row(**overrides: object) -> dict[str, object]:
    row = {
        "event_id": "E0001",
        "direction": "long",
        "level_id": "rolling_low",
        "outcome_2r_h16": "tp",
        "exit_reason": "target",
        "wick_ratio": 0.6,
        "penetration_atr": 0.25,
        "reclaim_atr": 0.2,
        "bars_held": 4,
        "net_result_r": 2.0,
    }
    row.update(overrides)
    return row


def test_assess_textbook_correct():
    out = assess_event(_base_review_row())
    assert out["verdict"] == "correct"
    assert out["detector_correct"] == "yes"
    assert out["sweep_clear"] == "yes"
    assert out["confirmation_clear"] == "n/a"
    assert out["review_version"] == REVIEW_VERSION
    assert out["review_id"] == "R1-E0001"
    assert out["reviewer"] == "agent_5"
    assert 0 <= out["reviewer_score"] <= 100


def test_assess_marginal_incorrect():
    out = assess_event(_base_review_row(wick_ratio=0.30, penetration_atr=0.30))
    assert out["verdict"] == "incorrect"
    assert out["detector_correct"] == "no"
    out2 = assess_event(_base_review_row(wick_ratio=0.6, penetration_atr=0.05))
    assert out2["verdict"] == "incorrect"


def test_assess_same_bar_and_missing_metrics_ambiguous():
    out = assess_event(_base_review_row(outcome_2r_h16="ambiguous", exit_reason="ambiguous"))
    assert out["verdict"] == "ambiguous"
    assert out["detector_correct"] == "maybe"
    out2 = assess_event(_base_review_row(wick_ratio=float("nan"), penetration_atr=float("nan")))
    assert out2["verdict"] == "ambiguous"


# ---------------------------------------------------------------------------
# Chart + round-1 orchestration
# ---------------------------------------------------------------------------

def test_plot_event_chart_returns_figure():
    candles = _candles()
    labeled, _ = _labeled_and_events(candles, k=160)
    row = labeled.iloc[0]
    fig = plot_event_chart(candles, row)
    assert fig is not None
    assert len(fig.axes) == 1
    from matplotlib.figure import Figure

    assert isinstance(fig, Figure)


def test_render_event_charts_groups_and_csv(tmp_path):
    candles = _candles()
    labeled, events = _labeled_and_events(candles, k=160)
    out_dir = str(tmp_path)
    sample, paths = render_event_charts(candles, labeled, out_dir, n=120, seed=7)
    assert len(paths) == len(sample) == 120
    assert len(paths) == len(set(paths))
    for p in paths:
        assert os.path.exists(p)
        assert p.startswith(f"{out_dir}/event_charts/")
    groups = {p.split("/event_charts/")[1].split("/")[0] for p in paths}
    assert groups == set(OUTCOME_GROUP_DIRS.values())

    result = run_round1_review(candles, events, labeled, out_dir, n=120, seed=7)
    assert result["n_reviewed"] == 120
    assert result["n_charts"] == 120
    assert os.path.exists(f"{out_dir}/manual_review.csv")
    assert os.path.exists(f"{out_dir}/manual_review_summary.md")
    assert os.path.exists(f"{out_dir}/manual_review_summary.json")
    stats = result["stats"]
    assert stats["n_reviewed"] == 120
    assert set(stats["verdicts"]) <= {"correct", "incorrect", "ambiguous"}


def test_manual_review_csv_schema(tmp_path):
    candles = _candles()
    labeled, events = _labeled_and_events(candles, k=160)
    result = run_round1_review(candles, events, labeled, str(tmp_path), n=120, seed=1)
    df = pd.read_csv(result["csv_path"])
    # canonical schema columns (docs/SCHEMAS.md §10) are present
    for col in schema.REVIEW_COLUMNS:
        assert col in df.columns
    assert set(df["verdict"]) <= set(schema.REVIEW_VERDICTS)
    assert df["review_version"].nunique() == 1
    assert df["review_id"].is_unique
    assert list(df.columns[: len(REVIEW_CSV_COLUMNS)]) == REVIEW_CSV_COLUMNS


# ---------------------------------------------------------------------------
# Real-data smoke (reproduces reports/ artifacts when parquet is present)
# ---------------------------------------------------------------------------

def test_real_data_round1_smoke(tmp_path):
    if not os.path.exists(PARQUET):
        pytest.skip("processed parquet not present")
    from src.visualization.event_chart import generate_round1_artifacts

    result = generate_round1_artifacts(PARQUET, out_dir=str(tmp_path), n=100, seed=42)
    assert result["n_reviewed"] == 100
    assert result["n_charts"] == 100
    assert result["n_labeled"] > 1000
    stats = result["stats"]
    assert stats["n_reviewed"] == 100
    assert stats["by_direction"].keys() <= {"long", "short"}
    # CSV written with the merged sweep metrics present
    df = pd.read_csv(f"{tmp_path}/manual_review.csv")
    assert len(df) == 100
    assert df["notes"].str.contains("wick_ratio").all()
