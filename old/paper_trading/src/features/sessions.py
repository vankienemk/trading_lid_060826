"""Trading-session boundaries and hour-based session flags."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

DEFAULT_SESSIONS: dict[str, list[int]] = {
    "asia": [0, 8],
    "london": [7, 16],
    "new_york": [12, 21],
}


@dataclass(frozen=True)
class SessionSpec:
    name: str
    start: int
    end: int

    def contains_hour(self, hour: int) -> bool:
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= hour < self.end
        return hour >= self.start or hour < self.end


def _parse_hours(value: Any, name: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(
            f"sessions.{name}: expected a 2-element [start, end] hour list, "
            f"got {value!r}"
        )
    start, end = int(value[0]), int(value[1])
    if not (0 <= start <= 24 and 0 <= end <= 24):
        raise ValueError(f"sessions.{name}: hours must be in [0, 24], got [{start}, {end}]")
    return [start, end]


def session_specs(config: dict[str, Any] | None = None) -> dict[str, SessionSpec]:
    raw = (config or {}).get("sessions", DEFAULT_SESSIONS)
    if isinstance(raw, dict):
        meta = {"timezone", "tz", "name"}
        merged = {
            **DEFAULT_SESSIONS,
            **{k: v for k, v in raw.items() if k not in meta},
        }
    else:
        merged = dict(DEFAULT_SESSIONS)
    return {
        name: SessionSpec(name, *_parse_hours(hours, name))
        for name, hours in merged.items()
    }


def session_flags(hour_utc: int, config: dict[str, Any] | None = None) -> dict[str, Any]:
    specs = session_specs(config)
    active = [(name, spec) for name, spec in specs.items() if spec.contains_hour(hour_utc)]
    flags: dict[str, Any] = {}
    for name in specs:
        flags[f"session_{name}"] = any(n == name for n, _ in active)
    flags["session_overlap"] = len(active)
    if active:
        earliest_start = min(spec.start for _, spec in active)
        flags["minutes_from_session_open"] = (hour_utc - earliest_start) * 60
    else:
        flags["minutes_from_session_open"] = float("nan")
    return flags


__all__ = ["DEFAULT_SESSIONS", "SessionSpec", "session_flags", "session_specs"]