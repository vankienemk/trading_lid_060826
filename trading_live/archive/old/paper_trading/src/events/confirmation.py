"""Confirmation detection for sweep events (guide sections 11, 13.8)."""
from __future__ import annotations
import numpy as np
import pandas as pd

from src.schema import normalize_event_direction, CONFIRMATION_COLUMNS
from src.events.sweep_detector import detect_sweeps

DEFAULT_MAX_WAIT_BARS = 3
DEFAULT_MIN_BODY_RATIO = 0.60
DEFAULT_MIN_RANGE_ATR = 0.80
DEFAULT_REQUIRE_BREAK_SWEEP_EXTREME = True
DEFAULT_BUFFER_ATR = 0.10

_LONG, _SHORT = "long", "short"


def _sweep_flags(candles: pd.DataFrame, config: dict) -> pd.DataFrame:
    ind = config.get("indicators", {})
    liq = config.get("liquidity", {})
    swp = config.get("sweep", {})
    return detect_sweeps(
        candles,
        atr_period=ind.get("atr_period", 14),
        level_lookback=liq.get("rolling_lookback", 20),
        min_penetration_atr=swp.get("min_penetration_atr", 0.05),
        max_penetration_atr=swp.get("max_penetration_atr", 0.50),
        min_wick_ratio=swp.get("min_wick_ratio", 0.35),
        min_reclaim_atr=swp.get("min_reclaim_atr", 0.0),
    )


def run_anchor_position(flags: pd.DataFrame, pos: int, direction: str) -> int:
    col = "sweep_long" if normalize_event_direction(direction) == _LONG else "sweep_short"
    flags_np = flags[col].fillna(False).to_numpy()
    start = int(pos)
    while start > 0 and bool(flags_np[start - 1]):
        start -= 1
    return start


def run_extreme_at(candles: pd.DataFrame, first_bar: int, k: int, direction: str) -> float:
    if normalize_event_direction(direction) == _LONG:
        return float(candles["high"].iloc[first_bar : k + 1].max())
    return float(candles["low"].iloc[first_bar : k + 1].min())


def run_opposite_extreme_at(candles: pd.DataFrame, first_bar: int, k: int, direction: str) -> float:
    if normalize_event_direction(direction) == _LONG:
        return float(candles["low"].iloc[first_bar : k + 1].min())
    return float(candles["high"].iloc[first_bar : k + 1].max())


def attach_confirmations(
    candles: pd.DataFrame,
    events: pd.DataFrame,
    config: dict | None = None,
    *,
    max_wait_bars: int | None = None,
    min_body_ratio: float | None = None,
    min_range_atr: float | None = None,
    require_break_sweep_extreme: bool | None = None,
) -> pd.DataFrame:
    cfg = dict(config or {})
    conf = cfg.get("confirmation", {})
    max_wait = conf.get("max_wait_bars", DEFAULT_MAX_WAIT_BARS) if max_wait_bars is None else max_wait_bars
    body_min = conf.get("min_body_ratio", DEFAULT_MIN_BODY_RATIO) if min_body_ratio is None else min_body_ratio
    range_min = conf.get("min_range_atr", DEFAULT_MIN_RANGE_ATR) if min_range_atr is None else min_range_atr
    req_break = conf.get("require_break_sweep_extreme", DEFAULT_REQUIRE_BREAK_SWEEP_EXTREME) if require_break_sweep_extreme is None else require_break_sweep_extreme
    enabled = conf.get("enabled", True)

    required = ["event_time", "direction"]
    missing = [c for c in required if c not in events.columns]
    if missing:
        raise ValueError(f"attach_confirmations: events table missing required columns {missing}")
    if len(events) == 0:
        out = events.copy()
        ts_dtype = candles.index.dtype if isinstance(candles.index, pd.DatetimeIndex) else "object"
        for col, dtype in [
            ("is_confirmed", "bool"),
            ("confirmation_time", ts_dtype),
            ("confirmation_delay_bars", "float64"),
            ("confirmation_close", "float64"),
            ("confirmation_range_atr", "float64"),
            ("confirmation_body_ratio", "float64"),
            ("confirmation_volume", "float64"),
        ]:
            out[col] = pd.Series(dtype=dtype, index=events.index)
        return out

    flags = _sweep_flags(candles, cfg)
    n = len(candles)

    rows: list[dict] = []
    for _, row in events.iterrows():
        direction = normalize_event_direction(row["direction"])
        pos = int(row.get("bar_index", candles.index.get_indexer([row["event_time"]])[0]))
        anchor = run_anchor_position(flags, pos, direction)

        found_k: int | None = None
        if enabled:
            for k in range(anchor + 1, min(anchor + max_wait + 1, n)):
                candle = candles.iloc[k]
                o, h, lo, c = candle["open"], candle["high"], candle["low"], candle["close"]
                rng = h - lo
                atr_k = flags["atr"].iloc[k]
                if not np.isfinite(atr_k) or rng <= 0:
                    continue
                body_r = abs(c - o) / rng
                range_a = rng / atr_k
                extreme = run_extreme_at(candles, anchor, k - 1, direction)
                broke_extreme = (c > extreme) if direction == _LONG else (c < extreme)
                if (not req_break or broke_extreme) and body_r >= body_min and range_a >= range_min:
                    found_k = k
                    break

        if found_k is None:
            rows.append({
                "is_confirmed": False,
                "confirmation_time": pd.NaT,
                "confirmation_delay_bars": np.nan,
                "confirmation_close": np.nan,
                "confirmation_range_atr": np.nan,
                "confirmation_body_ratio": np.nan,
                "confirmation_volume": np.nan,
            })
        else:
            candle = candles.iloc[found_k]
            rng = candle["high"] - candle["low"]
            body_r = abs(candle["close"] - candle["open"]) / rng
            range_a = rng / flags["atr"].iloc[found_k]
            vol = candle.get("volume", np.nan)
            rows.append({
                "is_confirmed": True,
                "confirmation_time": candles.index[found_k],
                "confirmation_delay_bars": found_k - anchor,
                "confirmation_close": float(candle["close"]),
                "confirmation_range_atr": float(range_a),
                "confirmation_body_ratio": float(body_r),
                "confirmation_volume": float(vol),
            })

    confirm_df = pd.DataFrame(rows, index=events.index)
    if isinstance(candles.index, pd.DatetimeIndex):
        utc_col = pd.to_datetime(confirm_df["confirmation_time"], utc=True)
        tz = candles.index.tz
        confirm_df["confirmation_time"] = (
            utc_col.dt.tz_convert(tz) if tz is not None else utc_col.dt.tz_localize(None)
        )
    out = events.copy()
    for col in CONFIRMATION_COLUMNS:
        out[col] = confirm_df[col]
    return out


def build_entry_schedule(candles: pd.DataFrame, events: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    cfg = dict(config or {})
    costs = cfg.get("entry", {})
    slip = float(costs.get("slippage_price", 0.0))
    spread = float(costs.get("spread_price", 0.0))
    flags = _sweep_flags(candles, cfg)
    n = len(candles)

    rows: list[dict] = []
    for _, row in events.iterrows():
        direction = normalize_event_direction(row["direction"])
        pos = candles.index.get_indexer([row["event_time"]])[0]
        anchor = run_anchor_position(flags, pos, direction)
        is_long = direction == _LONG

        if not bool(row.get("is_confirmed", False)) or pd.isna(row.get("confirmation_time")):
            continue
        conf_bar = candles.index.get_indexer([row["confirmation_time"]])[0]
        entry_bar = conf_bar + 1
        if entry_bar >= n:
            continue
        entry_price = float(candles["open"].iloc[entry_bar])
        entry_price = entry_price + slip + spread if is_long else entry_price - slip - spread
        rows.append({
            "event_id": row.get("event_id", np.nan),
            "direction": direction,
            "sweep_anchor_time": candles.index[anchor],
            "is_confirmed": True,
            "confirmation_time": row["confirmation_time"],
            "entry_bar": int(entry_bar),
            "entry_time": candles.index[entry_bar],
            "entry_price": float(entry_price),
        })
    if not rows:
        return pd.DataFrame(columns=["event_id", "direction", "sweep_anchor_time", "is_confirmed",
                                      "confirmation_time", "entry_bar", "entry_time", "entry_price"])
    return pd.DataFrame(rows)