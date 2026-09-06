"""
signal_engine.py — V2 Signal Engine for Paper Trading

Architecture
============
This module implements a forward-looking signal engine that scans each new
M15 candle, runs the V2 sweep detection pipeline, applies confirmation,
builds 32 features, computes rule_score + calibrated model probability,
and outputs SignalCandidate records for the execution layer.

Key design decisions
--------------------
- **Causality**: every signal is generated using only data available up to
  the *current* bar's close.  Sweep detection, HTF context, volume z-scores,
  and ATR percentiles all use shift(1) or rolling windows that exclude the
  "current" bar where appropriate.
- **Frozen model**: the trained LogisticRegression (L2, C=1.0, 30 features)
  and its isotonic calibrator are loaded once at startup.
  **Never retrain** — this is a forward-only inference engine.
- **Config-driven**: parameters are read from v2_frozen.yaml at startup;
  every sweep, confirmation, entry/stop/target parameter matches the frozen
  V2 spec.

Classes
-------
SignalCandidate:
    Frozen dataclass with all fields the execution layer needs.

Functions
---------
create_check_new_bar(symbol, mcp_client) -> callable
    Returns a `check_new_bar` closure bound to the symbol and MCP client.
    Call per new M15 candle to get list[SignalCandidate].
"""

from __future__ import annotations

import os
import joblib
import pickle
import sys
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Ensure paper_trading/src is importable
# ---------------------------------------------------------------------------
_PT_SRC = Path(__file__).resolve().parent / "src"
if str(_PT_SRC) not in sys.path:
    sys.path.insert(0, str(_PT_SRC.parent))  # paper_trading dir

from src.events.sweep_detector_v2 import (
    detect_sweeps_v2,
    _candidate_rows_v2,
    V2_MAX_PENETRATION_ATR,
)
from src.events.confirmation import (
    attach_confirmations,
    run_anchor_position,
    run_opposite_extreme_at,
)
from src.features.feature_pipeline import build_event_features
from src.features.registry import SIGNAL_FEATURE_NAMES
from src.liquidity.level_registry import build_liquidity_levels
from src.scoring.rule_score import compute_rule_scores

logger = logging.getLogger("signal_engine")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KOWN_AT = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

#: Paths (configurable via env; defaults relative to this file's project root)
_PROJECT_ROOT = Path(__file__).resolve().parent  # paper_trading/
_FROZEN_CONFIG = _PROJECT_ROOT.parent / "xauusd-liquidity-sweep" / "pipeline_v2" / "configs" / "v2_frozen.yaml"
_MODEL_DIR = _PROJECT_ROOT.parent / "xauusd-liquidity-sweep" / "pipeline_v2" / "artifacts" / "models"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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
# Model loader
# ---------------------------------------------------------------------------


def _load_model_and_calibrator(
    model_path: str | Path | None = None,
    calibrator_path: str | Path | None = None,
):
    """Load the frozen logistic regression model and its isotonic calibrator.

    Returns (model, calibrator) — both sklearn CalibratedClassifierCV objects.
    Structure::
        model.estimator — LogisticRegression (30 features, L2, C=1.0)
        model.calibrated_classifiers_[0].calibrators — [IsotonicRegression]
    The calibrator.pkl is a *separate* CalibratedClassifierCV fit with the
    same base estimator but potentially on a different holdout set (used for
    probability calibration without retraining the base model).
    """
    mpath = str(model_path or _MODEL_DIR / "model.pkl")
    cpath = str(calibrator_path or _MODEL_DIR / "calibrator.pkl")

    logger.info("Loading model from %s", mpath)
    raw = joblib.load(mpath)
    # model.pkl is a dict with keys: model, scaler, feature_names, impute_medians
    if isinstance(raw, dict):
        model = raw["model"]
        logger.info("Loaded model from dict wrapper — using raw['model']")
    else:
        model = raw

    logger.info("Loading calibrator from %s", cpath)
    calibrator = joblib.load(cpath)

    # Validate expected structure
    base = getattr(model, "estimator", None)
    if base is None:
        raise ValueError("model.pkl has no .estimator — not a CalibratedClassifierCV")
    n_features = getattr(base, "n_features_in_", 0)
    logger.info("Model expects %d features (LogisticRegression L2 C=1.0)", n_features)

    return model, calibrator


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config(config_path: str | Path | None = None) -> dict:
    """Load the frozen V2 configuration YAML."""
    cpath = str(config_path or _FROZEN_CONFIG)
    logger.info("Loading config from %s", cpath)
    with open(cpath, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        raise ValueError(f"Empty or invalid config at {cpath}")
    return cfg


# ---------------------------------------------------------------------------
# Signal engine — per-bar scanning
# ---------------------------------------------------------------------------


def _compute_model_prob(
    features_df: pd.DataFrame,
    model: Any,
    calibrator: Any,
) -> pd.Series:
    """Compute calibrated probability of the positive (win) class.

    Uses the models loaded from model.pkl / calibrator.pkl.
    The logistic regression inside CalibratedClassifierCV is the base;
    the isotonic calibrator provides the calibrated probabilities.

    The feature set is exactly the 30 numeric features expected by
    the model (minus level_type / volatility_regime which are handled
    separately by the pipeline).
    """
    if features_df.empty:
        return pd.Series(dtype="float64")

    # Ensure features are in the right order and only numeric cols
    available = [c for c in SIGNAL_FEATURE_NAMES if c in features_df.columns]
    X = features_df[available].astype("float64").fillna(0.0).values

    # Model predict_proba: returns [[prob_class0, prob_class1]]
    try:
        proba = model.predict_proba(X)
    except Exception:
        proba = calibrator.predict_proba(X)

    # Positive class (win/success) is class index 1
    if proba.shape[1] >= 2:
        return pd.Series(proba[:, 1], index=features_df.index)
    return pd.Series(proba[:, 0], index=features_df.index)


# ---------------------------------------------------------------------------
# Core per-bar scanning
# ---------------------------------------------------------------------------


def _check_new_bar(
    symbol: str,
    mcp_client: Any,
    config: dict,
    model: Any,
    calibrator: Any,
    levels_cache: dict | None = None,
) -> list[SignalCandidate]:
    """Core per-bar scanning function.

    Steps:
    1. Fetch latest M15 candles from MCP (get_chart_history).
    2. Run V2 sweep detection + V2 filters.
    3. Apply confirmation (wait up to 3 bars, min_body_ratio >= 0.60, min_range_atr >= 0.80).
    4. Build 32-feature matrix.
    5. Compute rule_score + model probability combined_score.
    6. Compute entry = next_open_after_confirmation.
       Stop = entry - stop_buffer_atr * ATR (for long).
       Target = entry + reward_r * ATR (with reward_r=3.0 from config).
    7. Return list[SignalCandidate].
    """
    if levels_cache is None:
        levels_cache = {}

    # --- 1. Fetch candles ---
    candles_raw = mcp_client.get_chart_history(symbol)
    if candles_raw is None or len(candles_raw) == 0:
        logger.warning("No candles returned for %s", symbol)
        return []
    candles = candles_raw.rename(columns=str.lower).copy()
    required_cols = {"open", "high", "low", "close"}
    if not required_cols.issubset(candles.columns):
        raise ValueError(
            f"Missing required columns in MCP data for {symbol}: "
            f"{required_cols - set(candles.columns)}"
        )
    if not isinstance(candles.index, pd.DatetimeIndex):
        candles.index = pd.to_datetime(candles.index)
    if not candles.index.is_monotonic_increasing:
        candles = candles.sort_index()
    if "volume" not in candles.columns:
        candles["volume"] = 0

    # --- 2. V2 Sweep detection ---
    swp_cfg = config.get("sweep", {})
    ind_cfg = config.get("indicators", {})
    liq_cfg = config.get("liquidity", {})
    atr_period = ind_cfg.get("atr_period", 14)
    level_lookback = liq_cfg.get("rolling_lookback", 20)
    min_pen = swp_cfg.get("min_penetration_atr", 0.05)
    max_pen = V2_MAX_PENETRATION_ATR
    min_wick = swp_cfg.get("min_wick_ratio", 0.35)
    v2_trend = swp_cfg.get("v2_nguoc_trend", True)
    cooldown = swp_cfg.get("cooldown_bars", 4)
    group_rule = swp_cfg.get("group_rule", "first")

    sweep_out = detect_sweeps_v2(
        candles,
        atr_period=atr_period,
        level_lookback=level_lookback,
        min_penetration_atr=min_pen,
        max_penetration_atr=max_pen,
        min_wick_ratio=min_wick,
        v2_nguoc_trend=v2_trend,
    )
    candidates = _candidate_rows_v2(sweep_out)
    if candidates.empty:
        return []

    # --- 3. Deduplicate ---
    from src.events.deduplication import select_deduplicated_events

    deduped = select_deduplicated_events(
        candidates,
        cooldown_bars=cooldown,
        group_rule=group_rule,
    )
    if deduped.empty:
        return []
    deduped = deduped.sort_values(["event_time", "direction"], kind="stable")
    deduped["event_id"] = [f"{symbol}-V2-{i:06d}" for i in range(len(deduped))]
    deduped["v2_target_r"] = swp_cfg.get("v2_target_r", 3.0)

    # --- 4. Apply confirmation ---
    conf_cfg = config.get("confirmation", {})
    confirmed = attach_confirmations(
        candles,
        deduped,
        config,
        max_wait_bars=conf_cfg.get("max_wait_bars", 3),
        min_body_ratio=conf_cfg.get("min_body_ratio", 0.60),
        min_range_atr=conf_cfg.get("min_range_atr", 0.80),
        require_break_sweep_extreme=conf_cfg.get("require_break_sweep_extreme", True),
    )
    confirmed_events = confirmed[confirmed["is_confirmed"] == True].copy()
    if confirmed_events.empty:
        return []

    # --- 5. Build feature matrix ---
    levels = levels_cache.get(symbol)
    if levels is None:
        levels = build_liquidity_levels(candles, config)
        levels_cache[symbol] = levels
    features_df = build_event_features(candles, levels, confirmed_events, config)
    if features_df.empty:
        return []

    # --- 6. Compute scores ---
    scored = compute_rule_scores(confirmed_events, config, levels)
    rule_scores = dict(zip(scored["event_id"], scored["rule_score"]))
    model_probs = _compute_model_prob(features_df, model, calibrator)
    model_prob_dict = dict(zip(features_df["event_id"], model_probs))

    # --- 7. Compute entry / stop / target ---
    stop_cfg = config.get("stop", {})
    buffer_atr = float(stop_cfg.get("buffer_atr", 0.10))
    reward_r = float(swp_cfg.get("v2_target_r", 3.0))
    atr_series = sweep_out["atr"]

    signal_candidates: list[SignalCandidate] = []
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

        signal_candidates.append(
            SignalCandidate(
                symbol=symbol,
                direction=direction,
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
                    "config_frozen_at": KOWN_AT,
                    "level_id": str(ev.get("level_id", "")),
                    "level_price": float(ev.get("level_price", 0)),
                },
            )
        )
    return signal_candidates


# ---------------------------------------------------------------------------
# Factory: create_check_new_bar
# ---------------------------------------------------------------------------


def create_check_new_bar(
    symbol: str,
    mcp_client: Any,
    config_path: str | Path | None = None,
    model_path: str | Path | None = None,
    calibrator_path: str | Path | None = None,
) -> Callable[[], list[SignalCandidate]]:
    """Create a bound ``check_new_bar()`` closure for one symbol.

    Parameters
    ----------
    symbol : str
        Asset symbol (e.g. "XAUUSD", "EURUSD").
    mcp_client : Any
        MCP client object that implements ``get_chart_history(symbol)``
        returning an OHLCV DataFrame with a DatetimeIndex.
    config_path : str or Path, optional
        Path to the frozen v2 config YAML.  Default:
        ``pipeline_v2/configs/v2_frozen.yaml``
    model_path : str or Path, optional
        Path to model.pkl.  Default:
        ``pipeline_v2/artifacts/models/model.pkl``
    calibrator_path : str or Path, optional
        Path to calibrator.pkl.  Default:
        ``pipeline_v2/artifacts/models/calibrator.pkl``

    Returns
    -------
    callable
        A zero-argument function ``check_new_bar() -> list[SignalCandidate]``
        that the caller should invoke on every new M15 candle close.

    Usage
    -----
    >>> check = create_check_new_bar("XAUUSD", mcp_client)
    >>> signals = check()      # after each new bar
    >>> for s in signals:
    ...     send_to_execution(s)
    """
    config = _load_config(config_path)
    model, calibrator = _load_model_and_calibrator(model_path, calibrator_path)
    levels_cache: dict = {}

    def check_new_bar() -> list[SignalCandidate]:
        return _check_new_bar(symbol, mcp_client, config, model, calibrator, levels_cache)

    check_new_bar.config = config
    check_new_bar.model = model
    check_new_bar.calibrator = calibrator
    return check_new_bar