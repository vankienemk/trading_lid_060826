"""Smoke tests for Pipeline 3/4 CLIs (``xauusd-dataset`` / ``xauusd-train``).

These verify the integration wiring fails cleanly on missing inputs. The full
E2E artifact generation is covered by the real integration run (background
job) and schema-conformance checks in tests/test_contracts.py.
"""

from __future__ import annotations

import pytest

from src.pipelines.build_dataset import build_dataset_cli
from src.pipelines.train_model import train_model_cli


def test_dataset_cli_missing_config_fails_cleanly() -> None:
    assert build_dataset_cli(["--config", "does_not_exist.yaml"]) == 1


def test_dataset_cli_missing_processed_data_fails_cleanly(tmp_path) -> None:
    """Override the processed-OHLCV path to a file that does not exist."""
    override = tmp_path / "override.yaml"
    override.write_text(
        "data:\n"
        f"  output_path: {tmp_path / 'missing.parquet'}\n"
        f"  events_path: {tmp_path / 'events_missing.parquet'}\n",
        encoding="utf-8",
    )
    rc = build_dataset_cli(
        [
            "--config",
            "baseline.yaml",
            "--override",
            str(override),
            "--output",
            str(tmp_path / "out.parquet"),
        ]
    )
    assert rc == 1


def test_train_cli_missing_config_fails_cleanly() -> None:
    assert train_model_cli(["--config", "nope.yaml"]) == 1


def test_train_cli_without_dataset_reports_error(tmp_path) -> None:
    rc = train_model_cli(
        [
            "--config",
            "baseline.yaml",
            "--dataset",
            str(tmp_path / "missing.parquet"),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1


def test_train_cli_invalid_override_fails_cleanly(tmp_path) -> None:
    """A bad override (invalid group_rule) must be rejected by config schema."""
    override = tmp_path / "override.yaml"
    override.write_text("sweep:\n  group_rule: banana\n", encoding="utf-8")
    rc = train_model_cli(
        [
            "--config",
            "baseline.yaml",
            "--override",
            str(override),
            "--dataset",
            str(tmp_path / "missing.parquet"),
        ]
    )
    assert rc == 1


if __name__ == "__main__":
    pytest.main([__file__, "-q"])