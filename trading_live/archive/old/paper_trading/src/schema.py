"""Schema constants for the signal engine."""
from __future__ import annotations

EVENT_COLUMNS = [
    "event_id", "event_time", "direction", "level_id", "level_price",
    "event_open", "event_high", "event_low", "event_close",
    "penetration_atr", "wick_ratio", "reclaim_atr",
]

CONFIRMATION_COLUMNS = [
    "is_confirmed", "confirmation_time", "confirmation_delay_bars",
    "confirmation_close", "confirmation_range_atr", "confirmation_body_ratio",
    "confirmation_volume",
]

LEVEL_COLUMNS = [
    "level_id", "level_type", "direction", "price", "price_min", "price_max",
    "origin_pos", "origin_time", "known_at", "status",
    "touch_count", "first_touch_time", "last_touch_time",
]

LEVEL_EXTENSION_COLUMNS = [
    "known_pos", "is_h1", "max_age_bars", "touch_tolerance_atr",
    "touch_positions", "equal_dispersion_atr", "formation_atr_tolerance",
    "age_bars", "bars_since_last_touch", "sweep_state",
    "first_swept_at", "invalidated_at",
]

LEVEL_REGISTRY_COLUMNS = LEVEL_COLUMNS + LEVEL_EXTENSION_COLUMNS

LEVEL_STATE_COLUMNS = [
    "level_id", "level_type", "direction", "price", "known_at",
    "touch_count", "last_touch_time", "age_bars", "bars_since_last_touch",
    "sweep_state", "status", "first_swept_at", "invalidated_at",
]


def normalize_event_direction(direction: str) -> str:
    """Map bullish/bearish -> long/short and pass long/short through."""
    d = str(direction).lower().strip()
    if d in ("bullish", "long"):
        return "long"
    if d in ("bearish", "short"):
        return "short"
    raise ValueError(f"Unknown direction: {direction!r}")


__all__ = [
    "EVENT_COLUMNS", "CONFIRMATION_COLUMNS",
    "LEVEL_COLUMNS", "LEVEL_EXTENSION_COLUMNS",
    "LEVEL_REGISTRY_COLUMNS", "LEVEL_STATE_COLUMNS",
    "normalize_event_direction",
]