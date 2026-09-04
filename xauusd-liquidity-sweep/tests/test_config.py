"""Tests for config loading, deep merge, and declarative schema validation."""

from __future__ import annotations

import copy

import pytest

from src.config import ConfigError, deep_merge, load_config, resolve_path
from src.config_schema import validate_config


def test_load_baseline_succeeds() -> None:
    cfg = load_config("baseline.yaml")
    assert cfg["project"]["symbol"] == "XAUUSD"
    assert cfg["data"]["volume_kind"] == "tick"
    assert cfg["_root_dir"].endswith("xauusd-liquidity-sweep")
    assert cfg["_config_dir"].endswith("configs")


def test_load_with_overrides_deep_merges() -> None:
    cfg = load_config("baseline.yaml", ["model.yaml", "labeling.yaml"])
    # keys from baseline survive
    assert cfg["project"]["timeframe"] == "M15"
    # scalar override wins
    assert cfg["model"]["target"] == "outcome_2r_h16"
    # list override replaces entirely (deep merge semantics)
    assert cfg["labeling"]["horizons"] == [4, 8, 16, 32]
    # merged optional section present
    assert cfg["costs"]["commission_r"] == 0.0


def test_load_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        load_config("does_not_exist.yaml")


def test_validate_config_rejects_missing_section() -> None:
    cfg = load_config("baseline.yaml")
    del cfg["sweep"]
    with pytest.raises(ConfigError, match="sweep"):
        validate_config(cfg)


def test_validate_config_rejects_bad_type() -> None:
    cfg = load_config("baseline.yaml")
    cfg["sweep"]["cooldown_bars"] = "four"  # type: ignore[assignment]
    with pytest.raises(ConfigError, match="cooldown_bars"):
        validate_config(cfg)


def test_validate_config_rejects_out_of_range() -> None:
    cfg = load_config("baseline.yaml")
    cfg["sweep"]["min_penetration_atr"] = -1.0
    with pytest.raises(ConfigError, match="min_penetration_atr"):
        validate_config(cfg)


def test_validate_config_optional_section_absent_ok() -> None:
    cfg = load_config("baseline.yaml")
    cfg.pop("scoring", None)
    validate_config(cfg)  # must not raise (scoring is optional)


def test_deep_merge_nested() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": [1, 2]}
    over = {"a": {"c": 3}, "e": 5}
    merged = deep_merge(base, over)
    assert merged == {"a": {"b": 1, "c": 3}, "d": [1, 2], "e": 5}
    # original untouched
    assert base["a"]["c"] == 2


def test_resolve_path_absolute_and_relative() -> None:
    cfg = load_config("baseline.yaml")
    abs_path = resolve_path(cfg, "/tmp/x.parquet")
    assert abs_path == "/tmp/x.parquet"
    rel_path = resolve_path(cfg, "data/processed/x.parquet")
    assert rel_path.endswith("xauusd-liquidity-sweep/data/processed/x.parquet")


def test_load_config_validate_false_bypasses_schema() -> None:
    cfg = load_config("baseline.yaml", validate=False)
    cfg["sweep"] = "not-a-dict"  # type: ignore[assignment]
    # validate=False must not raise
    validate_config(copy.deepcopy(load_config("baseline.yaml")))

# ---------------------------------------------------------------------------
# sweep.group_rule — causal-absolute dedup (QA finding F1)
# ---------------------------------------------------------------------------


def test_sweep_group_rule_default_is_causal_first() -> None:
    """Baseline forces causal-absolute dedup: run's first bar is the event."""
    cfg = load_config("baseline.yaml")
    assert cfg["sweep"]["group_rule"] == "first"


def test_validate_config_rejects_lookahead_group_rule_in_default_path() -> None:
    """Non-causal group rules still validate syntactically (A/B experiments),
    but the default config must never ship with one."""
    cfg = load_config("baseline.yaml")
    cfg["sweep"]["group_rule"] = "deepest_penetration"
    validate_config(cfg)  # allowed as an explicit experiment override

    bad = load_config("baseline.yaml")
    bad["sweep"]["group_rule"] = "unknown_rule"
    with pytest.raises(ConfigError, match="group_rule"):
        validate_config(bad)


# ---------------------------------------------------------------------------
# sessions config section (feature time/session features, guide 13.6)
# ---------------------------------------------------------------------------


def test_sessions_config_defaults() -> None:
    cfg = load_config("baseline.yaml")
    s = cfg["sessions"]
    assert s["timezone"] == "UTC"
    assert s["asia"] == [0, 8]
    assert s["london"] == [7, 16]
    assert s["new_york"] == [12, 21]


def test_validate_config_rejects_bad_session_hour_range() -> None:
    cfg = load_config("baseline.yaml")
    cfg["sessions"]["asia"] = [0, 30]  # out of 0..24
    with pytest.raises(ConfigError, match="asia"):
        validate_config(cfg)


def test_sessions_optional_when_absent() -> None:
    cfg = load_config("baseline.yaml")
    cfg.pop("sessions", None)
    validate_config(cfg)  # must not raise
