"""Duplicate-event removal and cooldown for sweep events."""
from __future__ import annotations
import numpy as np
import pandas as pd

_GROUP_RULES = ("first", "deepest_penetration", "strongest_reclaim")


def apply_cooldown(signal, bars: int = 4) -> np.ndarray:
    if bars < 0:
        raise ValueError(f"apply_cooldown: bars must be >= 0, got {bars}")
    sig = np.asarray(signal, dtype=bool)
    accepted = np.zeros(len(sig), dtype=bool)
    last_event = -10**9
    for position, flag in enumerate(sig):
        if flag and position - last_event > bars:
            accepted[position] = True
            last_event = position
    return accepted


def _validate_candidates(events: pd.DataFrame) -> pd.DataFrame:
    required = ["position", "direction", "level_id", "penetration_atr", "reclaim_atr"]
    missing = [c for c in required if c not in events.columns]
    if missing:
        raise ValueError(f"candidate events are missing columns {missing}")
    if len(events) == 0:
        return events.copy()
    if not pd.api.types.is_integer_dtype(events["position"]):
        raise TypeError("candidate events 'position' must be an integer dtype")
    if events["position"].duplicated().any():
        dup = events.duplicated(subset=["position", "direction"], keep=False)
        if dup.any():
            raise ValueError("candidate events contain duplicate (position, direction) rows")
    return events.sort_values("position", kind="stable").reset_index(drop=True)


def group_candidate_events(events: pd.DataFrame, rule: str = "first") -> pd.DataFrame:
    if rule not in _GROUP_RULES:
        raise ValueError(f"group_candidate_events: rule must be one of {_GROUP_RULES}, got {rule!r}")
    df = _validate_candidates(events)
    if df.empty:
        return df
    new_run = (
        (df["direction"] != df["direction"].shift())
        | (df["level_id"] != df["level_id"].shift())
        | df["position"].diff().gt(1)
    ).fillna(True)
    df["_run"] = new_run.cumsum()
    picks: list[pd.DataFrame] = []
    for _, run in df.groupby("_run", sort=False):
        if rule == "first":
            pick = run.iloc[[0]]
        elif rule == "deepest_penetration":
            winner = run["penetration_atr"].idxmax()
            pick = run.loc[[winner]]
        else:
            winner = run["reclaim_atr"].idxmax()
            pick = run.loc[[winner]]
        picks.append(pick)
    out = pd.concat(picks, ignore_index=True).drop(columns=["_run"])
    return out.sort_values("position", kind="stable").reset_index(drop=True)


def select_deduplicated_events(events: pd.DataFrame, cooldown_bars: int = 4, group_rule: str = "first") -> pd.DataFrame:
    grouped = group_candidate_events(events, rule=group_rule)
    if grouped.empty:
        return grouped
    kept: list[pd.DataFrame] = []
    for direction in grouped["direction"].unique():
        sub = grouped[grouped["direction"] == direction].sort_values("position")
        span = int(sub["position"].max()) + 1
        signal = np.zeros(span, dtype=bool)
        signal[sub["position"].to_numpy()] = True
        accepted = apply_cooldown(signal, bars=cooldown_bars)
        kept.append(sub[accepted[sub["position"].to_numpy()]])
    out = pd.concat(kept, ignore_index=True) if kept else grouped.iloc[0:0]
    return out.sort_values("position", kind="stable").reset_index(drop=True)