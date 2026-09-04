"""Tests for rule-based scoring (guide section 19)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.scoring.rule_score import (
    ScoringError,
    assign_score_bucket,
    compute_bucket_report,
    compute_rule_scores,
)


def _make_sample_events(n: int = 5) -> pd.DataFrame:
    """Create minimal valid event table for testing."""
    data = {
        "event_id": [f"SWP-{i:06d}" for i in range(n)],
        "direction": (["long", "short"] * ((n + 1) // 2))[:n],
        "penetration_atr": [0.15, 0.30, 0.45, 0.10, 0.25][:n],
        "wick_ratio": [0.60, 0.80, 0.40, 0.70, 0.55][:n],
        "reclaim_atr": [0.20, 0.30, 0.05, 0.15, 0.00][:n],
        "is_confirmed": [True, True, False, True, False][:n],
        "confirmation_delay_bars": [2.0, 1.0, np.nan, 3.0, np.nan][:n],
        "confirmation_type": ["close_break", "structure_break", None, "displacement", None][:n],
        "confirmation_strength": [0.6, 0.85, np.nan, 0.5, np.nan][:n],
        "event_open": [100.0, 200.0, 150.0, 180.0, 120.0][:n],
        "event_high": [101.0, 202.0, 152.0, 182.0, 121.0][:n],
        "event_low": [99.0, 198.0, 148.0, 178.0, 119.0][:n],
        "event_close": [100.5, 198.5, 151.5, 178.5, 120.5][:n],
        "level_type": ["rolling", "equal", "swing", "prev_day", "rolling"][:n],
        "touch_count": [1, 4, 2, 1, 0][:n],
        "bars_since_last_touch": [np.nan, 50.0, 20.0, np.nan, np.nan][:n],
        "is_h1": [False, False, True, False, False][:n],
        "level_is_previous_day_high_low": [False, False, False, True, False][:n],
        "h1_trend": ["up", "down", "up", "down", ""][:n],
        "distance_to_previous_day_high_atr": [0.3, 0.8, 0.2, 1.5, 0.4][:n],
        "distance_to_previous_day_low_atr": [1.2, 0.3, 0.5, 0.8, 0.2][:n],
        "session_london": [True, False, True, True, False][:n],
        "session_new_york": [False, True, True, False, True][:n],
        "volume_zscore": [1.5, 2.5, 0.5, 1.2, 0.8][:n],
        "volume_percentile": [0.7, 0.9, 0.4, 0.65, 0.55][:n],
        "volatility_regime": ["normal", "high", "low", "normal", "high"][:n],
        "atr": [1.0, 1.5, 0.8, 1.2, 1.0][:n],
        "range_atr": [1.5, 2.5, 1.0, 1.8, 1.2][:n],
    }
    return pd.DataFrame(data)


class TestRuleScoreValidation:
    """Input validation tests."""

    def test_missing_event_id_raises(self):
        df = pd.DataFrame({"direction": ["long"]})
        with pytest.raises(ScoringError, match="Missing required columns"):
            compute_rule_scores(df, {})

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame(columns=["event_id", "direction"])
        with pytest.raises(ScoringError, match="empty"):
            compute_rule_scores(df, {})


class TestWeightLoading:
    """Weight configuration tests."""

    def test_default_weights(self):
        config: dict[str, Any] = {}
        df = _make_sample_events(1)
        result = compute_rule_scores(df, config)
        assert "rule_score" in result.columns
        assert result["rule_score"].iloc[0] >= 0
        assert result["rule_score"].iloc[0] <= 100

    def test_custom_weights_from_config(self):
        config = {
            "scoring": {
                "weights": {
                    "level": 30,
                    "sweep": 30,
                    "reclaim": 10,
                    "confirmation": 10,
                    "context": 10,
                    "volume": 10,
                }
            }
        }
        df = _make_sample_events(1)
        result = compute_rule_scores(df, config)
        assert "rule_score" in result.columns

    def test_partial_weight_override(self):
        """Missing weight keys should use defaults."""
        config = {"scoring": {"weights": {"level": 25}}}
        df = _make_sample_events(1)
        result = compute_rule_scores(df, config)
        # Should not raise, uses defaults for missing keys
        assert len(result) == 1


class TestComponentScoring:
    """Test individual score components produce reasonable values."""

    def test_all_components_present(self):
        config = load_config("baseline.yaml")
        df = _make_sample_events(3)
        result = compute_rule_scores(df, config)

        expected_cols = [
            "score_level",
            "score_sweep",
            "score_reclaim",
            "score_confirmation",
            "score_context",
            "score_volume",
            "rule_score",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_score_range(self):
        config = load_config("baseline.yaml")
        df = _make_sample_events(5)
        result = compute_rule_scores(df, config)

        # All component scores should be non-negative
        for comp in ["score_level", "score_sweep", "score_reclaim",
                     "score_confirmation", "score_context", "score_volume"]:
            assert (result[comp] >= 0).all(), f"{comp} has negative values"

        # Total score should be 0-100
        assert (result["rule_score"] >= 0).all()
        assert (result["rule_score"] <= 100).all()

    def test_high_quality_event_scores_higher(self):
        """A strong event should score higher than a weak one."""
        config = load_config("baseline.yaml")

        # Strong event: confirmed, good penetration, strong reclaim
        strong = _make_sample_events(1)
        strong.loc[0, "penetration_atr"] = 0.20
        strong.loc[0, "wick_ratio"] = 0.80
        strong.loc[0, "reclaim_atr"] = 0.30
        strong.loc[0, "is_confirmed"] = True
        strong.loc[0, "confirmation_strength"] = 0.9

        # Weak event: unconfirmed, poor metrics
        weak = _make_sample_events(1)
        weak.loc[0, "penetration_atr"] = 0.05
        weak.loc[0, "wick_ratio"] = 0.35
        weak.loc[0, "reclaim_atr"] = 0.0
        weak.loc[0, "is_confirmed"] = False

        strong_score = compute_rule_scores(strong, config)["rule_score"].iloc[0]
        weak_score = compute_rule_scores(weak, config)["rule_score"].iloc[0]

        assert strong_score > weak_score, (
            f"Strong event ({strong_score}) should score higher than "
            f"weak event ({weak_score})"
        )


class TestScoreBuckets:
    """Test bucket assignment and reporting."""

    def test_bucket_assignment(self):
        assert assign_score_bucket(0) == "0-39"
        assert assign_score_bucket(39) == "0-39"
        assert assign_score_bucket(40) == "40-49"
        assert assign_score_bucket(49) == "40-49"
        assert assign_score_bucket(50) == "50-59"
        assert assign_score_bucket(80) == "80-100"
        assert assign_score_bucket(100) == "80-100"

    def test_bucket_report_with_outcomes(self):
        """Bucket report requires outcome columns."""
        config = load_config("baseline.yaml")
        df = _make_sample_events(5)
        scored = compute_rule_scores(df, config)

        # Add fake outcome columns
        scored["outcome_2r_h16"] = ["tp", "sl", "tp", "time", "ambiguous"]
        scored["mfe_r_h16"] = np.random.randn(5)
        scored["mae_r_h16"] = -np.abs(np.random.randn(5))
        scored["net_result_r"] = np.random.randn(5)

        report = compute_bucket_report(scored)
        assert len(report) > 0
        assert "bucket" in report.columns
        assert "event_count" in report.columns
        assert "win_rate" in report.columns
        assert "profit_factor" in report.columns

    def test_bucket_report_without_outcomes_raises(self):
        """Should raise if outcome columns are missing."""
        config = load_config("baseline.yaml")
        df = _make_sample_events(5)
        scored = compute_rule_scores(df, config)

        with pytest.raises(ScoringError, match="missing outcome columns"):
            compute_bucket_report(scored)


class TestIntegrationWithConfig:
    """Integration test using actual baseline config."""

    def test_load_config_and_score(self):
        """Verify scoring works with real baseline.yaml."""
        config = load_config("baseline.yaml")
        assert "scoring" in config
        assert "weights" in config["scoring"]

        df = _make_sample_events(5)
        result = compute_rule_scores(df, config)

        assert len(result) == 5
        assert "rule_score" in result.columns
        # Scores should vary based on input quality
        assert result["rule_score"].std() > 0 or result["rule_score"].nunique() > 1
