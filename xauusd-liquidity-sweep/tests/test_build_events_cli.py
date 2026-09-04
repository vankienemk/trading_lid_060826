"""Smoke tests for Pipeline 2 (``xauusd-events`` CLI, build_events_cli).

The CLI must run the whole event flow from the merged config — most
importantly ``sweep.group_rule`` (baseline ``"first"``, causal per QA finding
F1) — and persist an event table with the confirmation columns attached.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.pipelines.build_events import build_events_cli


def _write_mt5_csv(path, n: int = 80) -> None:
    """Write a tiny MT5-format raw CSV with one bullish sweep at bar 40 that is
    confirmed on bar 41 (close 101.3 > sweep high 101.0)."""
    header = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
    rows = [header]
    for i in range(n):
        o = h = lo = c = None
        if i == 40:      # bullish sweep: pierce the rolling low, reclaim
            o, h, lo, c = 100.8, 101.0, 99.8, 100.9
        elif i == 41:    # confirmation candle: close far above the sweep high
            o, h, lo, c = 100.7, 101.5, 100.6, 101.3
        else:            # flat channel
            o, h, lo, c = 100.5, 101.0, 100.0, 100.5
        rows.append(
            f"2024.01.02\t{i * 15 // 60:02d}:{i * 15 % 60:02d}:00\t"
            f"{o}\t{h}\t{lo}\t{c}\t100\t0\t0"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _override_yaml(path, raw_csv, out_parquet) -> None:
    path.write_text(
        "data:\n"
        f"  input_path: {raw_csv}\n"
        f"  output_path: {out_parquet}\n",
        encoding="utf-8",
    )


def test_build_events_cli_runs_causal_default(tmp_path):
    raw = tmp_path / "raw.csv"
    processed = tmp_path / "processed.parquet"
    events_out = tmp_path / "events.parquet"
    override = tmp_path / "override.yaml"
    _write_mt5_csv(raw)
    _override_yaml(override, raw, processed)

    rc = build_events_cli(
        [
            "--config", "baseline.yaml",
            "--override", str(override),
            "--output", str(events_out),
        ]
    )
    assert rc == 0
    assert events_out.exists()

    events = pd.read_parquet(events_out)
    assert len(events) == 1
    row = events.iloc[0]
    assert row["direction"] == "bullish"
    assert bool(row["is_confirmed"])
    # Timestamps come from the normalized frame (broker-local -> UTC offset is
    # applied by the loader); assert against that frame's own index instead of
    # hardcoding an offset.  The processed parquet stores timestamps in a
    # ``timestamp`` column (loader convention).
    processed_df = pd.read_parquet(processed).set_index("timestamp")
    assert row["event_time"] == processed_df.index[40]   # sweep bar
    assert row["confirmation_time"] == processed_df.index[41]  # confirmation bar
    assert row["confirmation_delay_bars"] == 1


def test_build_events_cli_missing_config_fails_cleanly(tmp_path):
    rc = build_events_cli(["--config", "does_not_exist.yaml"])
    assert rc == 1


def test_build_events_cli_honors_override_group_rule(tmp_path, capsys):
    """A config override can opt into the retrospective rule explicitly."""
    raw = tmp_path / "raw.csv"
    processed = tmp_path / "processed.parquet"
    events_out = tmp_path / "events.parquet"
    override = tmp_path / "override.yaml"
    _write_mt5_csv(raw)
    _override_yaml(override, raw, processed)
    override.write_text(
        "sweep:\n  group_rule: deepest_penetration\n"
        "data:\n"
        f"  input_path: {raw}\n"
        f"  output_path: {processed}\n",
        encoding="utf-8",
    )

    rc = build_events_cli(
        [
            "--config", "baseline.yaml",
            "--override", str(override),
            "--output", str(events_out),
        ]
    )
    assert rc == 0
    assert "group_rule=deepest_penetration" in capsys.readouterr().out
    events = pd.read_parquet(events_out)
    assert len(events) == 1  # single-candle run: same representative either way


if __name__ == "__main__":
    pytest.main([__file__, "-q"])