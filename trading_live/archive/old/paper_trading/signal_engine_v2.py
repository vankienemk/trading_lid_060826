"""
signal_engine_v2.py — Multi-Symbol Signal Engine for Paper Trading V2

Key changes from signal_engine.py (v1):
1. Reads per-symbol config from configs/symbols/{SYMBOL}.yaml.
2. Validates status field: only "validated" gets a running engine.
3. Loads per-symbol model artifacts from artifacts/models/{SYMBOL}/.
4. Loads per-symbol feature schema from artifacts/feature_schemas/{SYMBOL}/.
5. Maintains separate levels_cache per symbol.
6. Entry=next_open_after_confirmation. Stop=sweep_extreme+buffer_atr*ATR.
   Target=entry+reward_r*(entry-stop).
7. No-lookahead preserved (shift(1) in reused modules).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
import yaml

# Ensure paper_trading/src is importable (src lives under paper_trading/)
_PT_DIR = Path(__file__).resolve().parent.parent / "paper_trading"
_SRC = _PT_DIR / "src"
if str(_PT_DIR) not in sys.path:
    sys.path.insert(0, str(_PT_DIR))

from src.events.sweep_detector_v2 import detect_sweeps_v2, _candidate_rows_v2, V2_MAX_PENETRATION_ATR
from src.events.confirmation import attach_confirmations, run_anchor_position, run_opposite_extreme_at
from src.events.deduplication import select_deduplicated_events
from src.features.feature_pipeline import build_event_features
from src.features.registry import SIGNAL_FEATURE_NAMES
from src.liquidity.level_registry import build_liquidity_levels
from src.scoring.rule_score import compute_rule_scores

logger = logging.getLogger("signal_engine_v2")

# Paths
_PROJECT_ROOT = Path(__file__).resolve().parent
_SYMBOL_CONFIG_DIR = _PROJECT_ROOT.parent / "configs" / "symbols"
_ARTIFACTS_DIR = _PROJECT_ROOT.parent / "artifacts"
_MODELS_BASE = _ARTIFACTS_DIR / "models"
_FEATURE_SCHEMAS_BASE = _ARTIFACTS_DIR / "feature_schemas"
_PIPELINE_V2_MODEL_DIR = (_PROJECT_ROOT.parent / "xauusd-liquidity-sweep"
                          / "pipeline_v2" / "artifacts" / "models")


class SymbolNotValidatedError(ValueError):
    """Symbol status is not 'validated'."""

class SymbolConfigNotFoundError(FileNotFoundError):
    """Per-symbol YAML config does not exist."""

class ModelArtifactNotFoundError(FileNotFoundError):
    """Per-symbol model/calibrator artifact missing."""


@dataclass(frozen=True)
class SignalCandidate:
    """One executable signal produced by the signal engine."""
    symbol: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: datetime
    event_time: datetime
    event_id: str
    rule_score: float
    model_prob: float
    combined_score: float
    penetration_atr: float
    wick_ratio: float
    reclaim_atr: float
    h1_trend: int
    confirmation_delay_bars: float
    confirmation_range_atr: float
    atr_value: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-symbol config loader
# ---------------------------------------------------------------------------

def load_symbol_config(symbol: str) -> dict:
    """Load per-symbol YAML from configs/symbols/{symbol}.yaml."""
    cfg_path = _SYMBOL_CONFIG_DIR / f"{symbol}.yaml"
    if not cfg_path.exists():
        raise SymbolConfigNotFoundError(
            f"Config not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        raise ValueError(f"Empty or invalid config at {cfg_path}")
    return cfg


# ---------------------------------------------------------------------------
# Status validation (spec section 1 - critical safety gate)
# ---------------------------------------------------------------------------

def _validate_status(symbol_cfg: dict, symbol_name: str = "") -> None:
    """Check status field; raise if not 'validated'."""
    status = symbol_cfg.get("status")
    if status is None:
        status = symbol_cfg.get("symbol", {}).get("status")
    if status is None:
        raise SymbolNotValidatedError(
            f"Symbol '{symbol_name}' config has no 'status' field. "
            f"Spec requires status: validated|candidate|rejected.")
    status_str = str(status).lower().strip()
    if status_str != "validated":
        raise SymbolNotValidatedError(
            f"Symbol '{symbol_name}' status is '{status_str}', not 'validated'. "
            f"Signal Engine will NOT run. Only validated symbols go live.")


# ---------------------------------------------------------------------------
# Per-symbol model / calibrator / feature schema loader
# ---------------------------------------------------------------------------

def _load_symbol_model_and_calibrator(symbol: str) -> tuple:
    """Load model + calibrator from artifacts/models/{symbol}/ or fallback."""
    model_dir = _MODELS_BASE / symbol
    model_path = model_dir / "model.pkl"
    calibrator_path = model_dir / "calibrator.pkl"

    if not model_path.exists():
        fallback = _PIPELINE_V2_MODEL_DIR / "model.pkl"
        if fallback.exists():
            logger.warning("Per-symbol model for %s not found - using fallback %s",
                           symbol, fallback)
            model_path = fallback
            calibrator_path = _PIPELINE_V2_MODEL_DIR / "calibrator.pkl"
        else:
            raise ModelArtifactNotFoundError(
                f"Model artifact not found for '{symbol}'. Train via Onboarding Wizard.")

    import joblib
    logger.info("Loading model for %s from %s", symbol, model_path)
    raw = joblib.load(str(model_path))
    model = raw["model"] if isinstance(raw, dict) else raw

    logger.info("Loading calibrator for %s from %s", symbol, calibrator_path)
    calibrator = joblib.load(str(calibrator_path))

    base = getattr(model, "estimator", None)
    if base is None:
        raise ValueError(f"model.pkl for {symbol} has no .estimator")
    n_feat = getattr(base, "n_features_in_", 0)
    logger.info("Model for %s expects %d features", symbol, n_feat)
    return model, calibrator


def _load_symbol_feature_schema(symbol: str) -> Optional[dict]:
    """Load feature schema from artifacts/feature_schemas/{symbol}/features.json."""
    schema_path = _FEATURE_SCHEMAS_BASE / symbol / "features.json"
    if not schema_path.exists():
        return None
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Extract per-symbol pipeline parameters
# ---------------------------------------------------------------------------

def _extract_pipeline_params(symbol_cfg: dict) -> dict:
    """Merge pipeline_overrides with frozen defaults into a flat param dict."""
    ov = symbol_cfg.get("pipeline_overrides", {})
    s = ov.get("sweep", {})
    c = ov.get("confirmation", {})
    i = ov.get("indicators", {})
    l = ov.get("liquidity", {})
    st = ov.get("stop", {})

    return {
        "v2_nguoc_trend": s.get("v2_nguoc_trend", True),
        "v2_target_r": s.get("v2_target_r", 3.0),
        "min_penetration_atr": s.get("min_penetration_atr", 0.05),
        "max_penetration_atr": s.get("max_penetration_atr", 0.20),
        "min_wick_ratio": s.get("min_wick_ratio", 0.35),
        "min_reclaim_atr": s.get("min_reclaim_atr", 0.00),
        "cooldown_bars": s.get("cooldown_bars", 4),
        "group_rule": s.get("group_rule", "first"),
        "max_wait_bars": c.get("max_wait_bars", 3),
        "require_break_sweep_extreme": c.get("require_break_sweep_extreme", True),
        "min_body_ratio": c.get("min_body_ratio", 0.60),
        "min_range_atr": c.get("min_range_atr", 0.80),
        "atr_period": i.get("atr_period", 14),
        "rolling_lookback": l.get("rolling_lookback", 20),
        "buffer_atr": st.get("buffer_atr", 0.10),
    }


# ---------------------------------------------------------------------------
# Model probability helper
# ---------------------------------------------------------------------------

def _compute_model_prob(
    features_df: pd.DataFrame, model: Any, calibrator: Any,
) -> pd.Series:
    """Compute calibrated probability of the positive (win) class."""
    if features_df.empty:
        return pd.Series(dtype="float64")
    available = [c for c in SIGNAL_FEATURE_NAMES if c in features_df.columns]
    X = features_df[available].astype("float64").fillna(0.0).values
    try:
        proba = model.predict_proba(X)
    except Exception:
        proba = calibrator.predict_proba(X)
    if proba.shape[1] >= 2:
        return pd.Series(proba[:, 1], index=features_df.index)
    return pd.Series(proba[:, 0], index=features_df.index)


# ---------------------------------------------------------------------------
# Core per-bar scanning (one symbol)
# ---------------------------------------------------------------------------

def _check_new_bar(
    symbol: str,
    mcp_client: Any,
    pipeline_params: dict,
    model: Any,
    calibrator: Any,
    levels_cache: Optional[dict] = None,
) -> list[SignalCandidate]:
    """Core per-bar scanning for one symbol (no-lookahead enforced)."""
    if levels_cache is None:
        levels_cache = {}

    # 1. Fetch candles
    candles_raw = mcp_client.get_chart_history(symbol)
    if candles_raw is None or (isinstance(candles_raw, pd.DataFrame) and candles_raw.empty):
        logger.warning("No candles returned for %s", symbol)
        return []
    candles = candles_raw.rename(columns=str.lower).copy()
    required = {"open", "high", "low", "close"}
    if not required.issubset(candles.columns):
        raise ValueError(f"Missing columns for {symbol}: {required - set(candles.columns)}")
    if not isinstance(candles.index, pd.DatetimeIndex):
        candles.index = pd.to_datetime(candles.index)
    if not candles.index.is_monotonic_increasing:
        candles = candles.sort_index()
    if "volume" not in candles.columns:
        candles["volume"] = 0

    # 2. V2 Sweep detection (per-symbol params)
    sweep_out = detect_sweeps_v2(
        candles,
        atr_period=pipeline_params["atr_period"],
        level_lookback=pipeline_params["rolling_lookback"],
        min_penetration_atr=pipeline_params["min_penetration_atr"],
        max_penetration_atr=pipeline_params["max_penetration_atr"],
        min_wick_ratio=pipeline_params["min_wick_ratio"],
        v2_nguoc_trend=pipeline_params["v2_nguoc_trend"],
    )
    candidates = _candidate_rows_v2(sweep_out)
    if candidates.empty:
        return []

    # 3. Deduplicate
    deduped = select_deduplicated_events(
        candidates,
        cooldown_bars=pipeline_params["cooldown_bars"],
        group_rule=pipeline_params["group_rule"],
    )
    if deduped.empty:
        return []
    deduped = deduped.sort_values(["event_time", "direction"], kind="stable")
    deduped["event_id"] = [f"{symbol}-V2-{i:06d}" for i in range(len(deduped))]
    deduped["v2_target_r"] = pipeline_params["v2_target_r"]

    # 4. Apply confirmation
    confirmed = attach_confirmations(
        candles, deduped, {},
        max_wait_bars=pipeline_params["max_wait_bars"],
        min_body_ratio=pipeline_params["min_body_ratio"],
        min_range_atr=pipeline_params["min_range_atr"],
        require_break_sweep_extreme=pipeline_params["require_break_sweep_extreme"],
    )
    confirmed_events = confirmed[confirmed["is_confirmed"] == True].copy()
    if confirmed_events.empty:
        return []

    # 5. Build feature matrix (per-symbol levels cache)
    if symbol not in levels_cache:
        levels_cache[symbol] = build_liquidity_levels(candles, {})
    levels = levels_cache[symbol]
    features_df = build_event_features(candles, levels, confirmed_events, {})
    if features_df.empty:
        return []

    # 6. Compute scores
    scored = compute_rule_scores(confirmed_events, {}, levels)
    rule_scores = dict(zip(scored["event_id"], scored["rule_score"]))
    model_probs = _compute_model_prob(features_df, model, calibrator)
    model_prob_dict = dict(zip(features_df["event_id"], model_probs))

    # 7. Compute entry / stop / target
    buffer_atr = pipeline_params["buffer_atr"]
    reward_r = pipeline_params["v2_target_r"]
    atr_series = sweep_out["atr"]

    results: list[SignalCandidate] = []
    for _, ev in confirmed_events.iterrows():
        eid = ev["event_id"]
        direction = "long" if str(ev["direction"]).lower() in ("bullish", "long") else "short"
        is_long = direction == "long"
        bar_pos = candles.index.get_indexer([ev["event_time"]])[0]
        anchor = run_anchor_position(sweep_out, bar_pos, str(ev["direction"]))
        conf_time = ev["confirmation_time"]
        conf_bar = candles.index.get_indexer([conf_time])[0]
        entry_bar = conf_bar + 1
        if entry_bar >= len(candles):
            continue
        entry_price = float(candles["open"].iloc[entry_bar])
        atr_sweep = float(atr_series.iloc[anchor])
        extreme = run_opposite_extreme_at(candles, anchor, anchor, str(ev["direction"]))
        if is_long:
            stop_price = extreme - buffer_atr * atr_sweep
            target_price = entry_price + reward_r * (entry_price - stop_price)
        else:
            stop_price = extreme + buffer_atr * atr_sweep
            target_price = entry_price - reward_r * (stop_price - entry_price)

        rule_score = rule_scores.get(eid, 0.0)
        model_prob = model_prob_dict.get(eid, 0.5)
        combined_score = (rule_score / 100.0 + model_prob) / 2.0

        results.append(SignalCandidate(
            symbol=symbol, direction=direction,
            entry_price=round(entry_price, 5),
            stop_price=round(stop_price, 5),
            target_price=round(target_price, 5),
            entry_time=candles.index[entry_bar].to_pydatetime(),
            event_time=ev["event_time"].to_pydatetime(),
            event_id=eid,
            rule_score=round(rule_score, 2),
            model_prob=round(model_prob, 4),
            combined_score=round(combined_score, 4),
            penetration_atr=round(float(ev["penetration_atr"]), 4),
            wick_ratio=round(float(ev["wick_ratio"]), 4),
            reclaim_atr=round(float(ev["reclaim_atr"]), 4),
            h1_trend=int(ev.get("h1_trend", 0)),
            confirmation_delay_bars=float(ev.get("confirmation_delay_bars", np.nan)),
            confirmation_range_atr=float(ev.get("confirmation_range_atr", np.nan)),
            atr_value=round(atr_sweep, 5),
            metadata={
                "config_source": "per_symbol",
                "level_id": str(ev.get("level_id", "")),
                "level_price": float(ev.get("level_price", 0)),
            },
        ))
    return results


# ---------------------------------------------------------------------------
# Public factory: create_symbol_engine
# ---------------------------------------------------------------------------

def create_symbol_engine(
    symbol: str,
    mcp_client: Any,
    symbol_cfg_override: Optional[dict] = None,
    model_override: Optional[Any] = None,
    calibrator_override: Optional[Any] = None,
) -> Callable[[], list[SignalCandidate]]:
    """Create a per-symbol signal engine closure.

    Flow:
    1. Load per-symbol config from configs/symbols/{symbol}.yaml.
    2. Validate status field (only 'validated' proceeds).
    3. Load per-symbol model + calibrator.
    4. Extract pipeline parameters.
    5. Return a zero-arg callable ``check_new_bar() -> list[SignalCandidate]``.

    Parameters
    ----------
    symbol : str
        Instrument name (e.g. "XAUUSD", "EURUSD").
    mcp_client : Any
        MCP client with get_chart_history(symbol) returning OHLCV DataFrame.
    symbol_cfg_override : dict, optional
        Pre-loaded symbol config (for testing; skips file read).
    model_override : Any, optional
        Pre-loaded model (for testing; skips load).
    calibrator_override : Any, optional
        Pre-loaded calibrator (for testing; skips load).

    Returns
    -------
    callable
        A zero-argument function ``check_new_bar() -> list[SignalCandidate]``.

    Raises
    ------
    SymbolConfigNotFoundError
        If the per-symbol YAML is missing.
    SymbolNotValidatedError
        If the symbol status is not 'validated'.
    ModelArtifactNotFoundError
        If model artifacts are missing (no fallback).

    Usage
    -----
    >>> eng = create_symbol_engine("XAUUSD", mcp_client)
    >>> signals = eng()
    """
    # --- 1. Load per-symbol config ---
    if symbol_cfg_override is not None:
        symbol_cfg = symbol_cfg_override
    else:
        symbol_cfg = load_symbol_config(symbol)

    # --- 2. Validate status ---
    _validate_status(symbol_cfg, symbol)

    logger.info("Symbol '%s' status=validated — creating engine", symbol)

    # --- 3. Load per-symbol model + calibrator ---
    if model_override is not None and calibrator_override is not None:
        model, calibrator = model_override, calibrator_override
    else:
        model, calibrator = _load_symbol_model_and_calibrator(symbol)

    # --- 4. Extract pipeline parameters ---
    pipeline_params = _extract_pipeline_params(symbol_cfg)

    # --- 5. Load feature schema (optional) ---
    feature_schema = _load_symbol_feature_schema(symbol)
    if feature_schema is not None:
        logger.info("Loaded per-symbol feature schema for %s (%d fields)",
                    symbol, len(feature_schema))

    # Per-symbol levels cache (separate from every other symbol)
    levels_cache: dict = {}

    # --- 6. Return bound closure ---
    def check_new_bar() -> list[SignalCandidate]:
        return _check_new_bar(
            symbol, mcp_client, pipeline_params, model, calibrator, levels_cache,
        )

    # Attach metadata for introspection
    check_new_bar.symbol = symbol
    check_new_bar.config = symbol_cfg
    check_new_bar.model = model
    check_new_bar.calibrator = calibrator
    check_new_bar.pipeline_params = pipeline_params
    return check_new_bar