"""Tests for ML modeling pipeline (split, train, calibration)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.modeling.split import (
    SplitError,
    apply_embargo,
    apply_purge,
    apply_purge_and_embargo,
    build_train_val_test_splits,
    time_split,
    walk_forward_folds,
)
from src.modeling.train import (
    ModelingError,
    prepare_features_and_labels,
)


class TestSplit:
    """Time-based split tests."""

    def test_time_split_with_int(self):
        train, val, test = time_split(1000)
        assert len(train) == 600
        assert len(val) == 200
        assert len(test) == 200
        assert np.all(np.diff(train) == 1)

    def test_time_split_with_array(self):
        positions = np.arange(500)
        train, val, test = time_split(positions)
        assert len(train) == 300
        assert len(val) == 100
        assert len(test) == 100
        assert np.array_equal(train, positions[:300])

    def test_purge_removes_leaking_events(self):
        train_pos = np.array([568, 567, 500])
        mask = apply_purge(train_pos, horizon_bars=32, validation_start_pos=600)
        assert not mask[0]
        assert mask[1]
        assert mask[2]

    def test_embargo_removes_boundary_events(self):
        train_pos = np.array([568, 567, 500])
        mask = apply_embargo(train_pos, validation_start_pos=600, embargo_bars=32)
        assert not mask[0]
        assert mask[1]
        assert mask[2]

    def test_combined_purge_embargo(self):
        train_pos = np.array([568, 567, 500])
        mask = apply_purge_and_embargo(train_pos, 32, 600, 32)
        assert not mask[0]
        assert mask[1]
        assert mask[2]

    def test_build_splits_from_config(self):
        config = {
            "splitting": {
                "train_fraction": 0.6,
                "validation_fraction": 0.2,
                "test_fraction": 0.2,
            }
        }
        splits = build_train_val_test_splits(1000, config)
        assert len(splits["train"]) == 600
        assert len(splits["validation"]) == 200
        assert len(splits["test"]) == 200


class TestWalkForwardFolds:
    """Walk-forward expanding-fold validation tests (guide §22.3)."""

    def test_basic_folds_generated(self):
        folds = walk_forward_folds(
            n_events=1000, initial_train_size=300, step_size=100,
            validation_size=100, embargo_bars=32, horizon_bars=32,
        )
        assert len(folds) > 1
        assert folds[0]["fold"] == 0
        assert folds[0]["train_raw_end"] == 300
        assert folds[1]["train_raw_end"] == 400

    def test_folds_no_overlap(self):
        folds = walk_forward_folds(500, 150, 50, 50, 20, 20)
        val_ranges = [
            (f["val_start"], f["val_start"] + len(f["validation"]))
            for f in folds
        ]
        for i in range(len(val_ranges) - 1):
            assert val_ranges[i][1] <= val_ranges[i + 1][0]

    def test_purge_embargo_applied(self):
        folds = walk_forward_folds(500, 150, 50, 50, 20, 20)
        for fold in folds:
            raw_train_size = fold["train_raw_end"]
            clean_train_size = len(fold["train"])
            assert clean_train_size <= raw_train_size

    def test_train_expands_each_fold(self):
        folds = walk_forward_folds(800, 200, 100, 80, 32, 32)
        raw_ends = [f["train_raw_end"] for f in folds]
        for i in range(len(raw_ends) - 1):
            assert raw_ends[i] < raw_ends[i + 1]

    def test_invalid_params_raise(self):
        with pytest.raises(SplitError):
            walk_forward_folds(0, 100, 50, 50)
        with pytest.raises(SplitError):
            walk_forward_folds(500, 0, 50, 50)
        with pytest.raises(SplitError):
            walk_forward_folds(500, 100, 0, 50)
        with pytest.raises(SplitError):
            walk_forward_folds(500, 100, 50, 0)

    def test_no_folds_when_dataset_too_small(self):
        with pytest.raises(SplitError):
            walk_forward_folds(100, 80, 50, 50, 32, 32)

    def test_reproducible(self):
        folds1 = walk_forward_folds(600, 200, 100, 80, 32, 32)
        folds2 = walk_forward_folds(600, 200, 100, 80, 32, 32)
        assert len(folds1) == len(folds2)
        for f1, f2 in zip(folds1, folds2):
            assert np.array_equal(f1["train"], f2["train"])
            assert np.array_equal(f1["validation"], f2["validation"])


class TestPrepareFeatures:
    """Feature preparation tests."""

    def _get_registry_features(self):
        """Load registry feature names for test data generation."""
        from src.modeling.train import _load_registry_features
        return _load_registry_features()

    def _make_sample_events(self, n=10):
        """Create sample events with ALL 32 registry features + some leakage cols."""
        np.random.seed(42)
        registry = self._get_registry_features()

        # Build data dict with all 32 registry features
        data = {
            "event_id": [f"SWP-{i:06d}" for i in range(n)],
            "event_time": pd.date_range("2022-01-01", periods=n, freq="15min"),
            "direction": ["long", "short"] * (n // 2),
            "outcome_2r_h16": np.random.choice(["tp", "sl", "time"], n),
        }
        # Add all 32 registry features with random values
        for feat_name in registry:
            data[feat_name] = np.random.randn(n)

        # Add leakage columns that should be dropped
        data["net_result_r"] = np.random.randn(n)
        data["mfe_r_h16"] = np.random.randn(n)
        data["mfe_r_h4"] = np.random.randn(n)

        return pd.DataFrame(data)

    def test_prepare_drops_banned_features(self):
        df = self._make_sample_events()
        _, _, features = prepare_features_and_labels(df)
        assert "net_result_r" not in features
        assert "mfe_r_h16" not in features
        assert "outcome_2r_h16" not in features

    def test_prepare_creates_binary_label(self):
        df = self._make_sample_events()
        _, y, _ = prepare_features_and_labels(df)
        assert set(y.unique()).issubset({0, 1})

    def test_prepare_drops_identity_columns(self):
        df = self._make_sample_events()
        _, _, features = prepare_features_and_labels(df)
        assert "event_id" not in features
        assert "event_time" not in features
        assert "direction" not in features

    def test_missing_target_raises(self):
        df = pd.DataFrame({"col1": [1, 2, 3]})
        with pytest.raises(ModelingError, match="Target column"):
            prepare_features_and_labels(df)

    def test_no_features_raises(self):
        df = pd.DataFrame({
            "event_id": ["a", "b"],
            "outcome_2r_h16": ["tp", "sl"],
        })
        with pytest.raises(ModelingError, match="missing from dataset"):
            prepare_features_and_labels(df)

    def test_feature_count_is_32_from_registry(self):
        """Feature set must be from registry whitelist, not all numeric cols."""
        from src.modeling.train import _load_registry_features

        registry = _load_registry_features()
        # Registry MUST load — if it returns empty, the test must fail
        assert len(registry) == 32, (
            f"Registry failed to load: got {len(registry)} features, expected 32. "
            "Check artifacts/feature_schemas/features.json exists."
        )

        # Create a dataframe with ALL 32 registry features + leakage columns
        np.random.seed(42)
        n = 20
        data = {name: np.random.randn(n) for name in registry}
        # Add leakage columns that should NOT appear
        data["mfe_r_h4"] = np.random.randn(n)
        data["mae_r_h32"] = np.random.randn(n)
        data["close_return_r_h16"] = np.random.randn(n)
        data["bars_held"] = np.random.uniform(1, 20, n)
        data["outcome_2r_h16"] = np.random.choice(["tp", "sl"], n)
        df = pd.DataFrame(data)

        _, _, features = prepare_features_and_labels(df)
        # Should have <= 32 features (some may be non-numeric and filtered out)
        assert len(features) <= 32, f"Got {len(features)} features, expected <= 32"
        assert len(features) >= 20, f"Got only {len(features)} features, expected >= 20"
        # Verify no leakage columns
        for leak_col in ["mfe_r_h4", "mae_r_h32", "close_return_r_h16", "bars_held"]:
            assert leak_col not in features, f"Leakage column {leak_col} found in features!"

    def test_prefix_banned_columns_dropped(self):
        """Columns with banned prefixes must be dropped even if numeric."""
        # Use full sample with all 32 registry features + banned columns
        df = self._make_sample_events(n=10)
        _, _, features = prepare_features_and_labels(df)

        # Verify no banned-prefix columns are in features
        for col in features:
            assert not any(col.startswith(p) for p in [
                "mfe_r_", "mae_r_", "close_return_r_", "max_close_return_r_",
                "min_close_return_r_", "outcome_", "target_", "bars_to_",
                "exit_", "gross_", "net_", "cost_", "future_",
            ]), f"Banned column {col} found in features!"

        # Explicitly check known leakage columns are absent
        for leak_col in ["mfe_r_h4", "mfe_r_h16", "net_result_r"]:
            assert leak_col not in features, f"Leakage column {leak_col} still present!"


# ---------------------------------------------------------------------------
# t21 anti-regression: feature SELECTION must equal the 32-feature registry
# (leakage guard — closes the gap the no_lookahead suite cannot see: those
# tests verify truncation-invariance of the feature *build* (t10), not the
# feature *selection* performed at train time (t14/t22)).
# ---------------------------------------------------------------------------


def test_prepare_returns_exactly_registry_32():
    """Selection must return exactly the registry set — no more, no fewer.

    Regression for t21: the old blacklist path let 21 non-registry columns
    through (19 post-entry outcome/excursion columns + atr/risk_price).
    """
    from src.modeling.train import _load_registry_features

    registry = _load_registry_features()
    assert len(registry) == 32

    np.random.seed(7)
    n = 25
    data = {name: np.random.randn(n) for name in registry}
    # post-entry leakage columns (should never appear)
    data["mfe_r_h4"] = np.random.randn(n)
    data["mae_r_h32"] = np.random.randn(n)
    data["close_return_r_h16"] = np.random.randn(n)
    data["max_close_return_r_h8"] = np.random.randn(n)
    data["min_close_return_r_h4"] = np.random.randn(n)
    data["bars_held"] = np.random.uniform(1, 20, n)
    data["atr"] = np.random.randn(n)          # entry-time but non-registry
    data["risk_price"] = np.random.randn(n)   # entry-time but non-registry
    data["outcome_2r_h16"] = np.random.choice(["tp", "sl"], n)
    df = pd.DataFrame(data)

    _, _, features = prepare_features_and_labels(df)
    assert set(features) == set(registry), (
        f"feature selection must equal the 32-feature registry; "
        f"got {len(features)}: {sorted(set(features) - set(registry))}"
    )


def test_banned_prefixes_cover_all_excursion_variants():
    """Prefix banning must catch every horizon variant of outcome columns.

    ``bars_held`` is guarded by the whitelist (registry-only) path rather than
    a prefix; without the whitelist it would leak (t21 finding logged).
    """
    from src.modeling.train import BANNED_PREFIXES

    for col in [
        "mfe_r_h4", "mae_r_h8", "close_return_r_h16", "max_close_return_r_h32",
        "min_close_return_r_h4", "bars_to_target", "net_result_r",
        "cost_r", "exit_price", "outcome_1r_h32", "future_return_r_h8",
    ]:
        assert any(col.startswith(p) for p in BANNED_PREFIXES), f"{col} not banned"


def test_serialized_model_features_are_registry_only(tmp_path):
    """The shipped model artifact must never contain non-registry features."""
    import os
    import pickle

    from src.modeling.train import _load_registry_features

    registry = _load_registry_features()
    model_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "artifacts", "models", "model.pkl",
    )
    if not os.path.exists(model_path):
        pytest.skip("model artifact not yet produced")
    with open(model_path, "rb") as fh:
        pipe = pickle.load(fh)
    feats = set(pipe["feature_names"])
    non_registry = feats - set(registry)
    assert not non_registry, (
        f"model.pkl contains {len(non_registry)} non-registry features "
        f"(leakage): {sorted(non_registry)}"
    )
