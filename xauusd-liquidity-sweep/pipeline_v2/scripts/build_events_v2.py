#!/usr/bin/env python3
"""V2 pipeline CLI: build events with --v2 flag routing to pipeline_v2 detectors.

    --v2 flag routes to pipeline_v2/src/events/sweep_detector_v2.py
    which applies nguoc_trend filter + max_penetration_atr ≤ 0.20.
    Default (no --v2) produces baseline v1.2.0 frozen output untouched.

    Usage:
        python pipeline_v2/scripts/build_events_v2.py --v2
        python pipeline_v2/scripts/build_events_v2.py  # baseline only
"""

from __future__ import annotations

import argparse
import os
import sys

# The baseline project root is the parent of the parent of this file's directory
# pipeline_v2/scripts/ -> ROOT = xauusd-liquidity-sweep/
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from src.config import load_config, resolve_path, set_global_config
from src.data.loader import run_data_pipeline
from src.events.confirmation import attach_confirmations


def build_events_v2_cli(argv: list[str] | None = None) -> int:
    """``xauusd-events --v2``: v2 event detection with nguoc_trend + max_pen≤0.20.

    Baseline (no --v2): uses the original src.events.sweep_detector (v1.2.0 frozen).
    With --v2: uses pipeline_v2.src.events.sweep_detector_v2 which applies:
      - nguoc_trend filter (trade with H1 trend)
      - max_penetration_atr ≤ 0.20 (from t10 triple intersection analysis)
      - configurable target_r (R:R ratio, default 2.0)

    Outputs:
      baseline: data/processed/events.parquet  (unchanged)
      v2:       pipeline_v2/data/events_v2.parquet
    """
    parser = argparse.ArgumentParser(
        prog="xauusd-events-v2",
        description="Build v2 event table (nguoc_trend + max_pen≤0.20 + configurable R:R).",
    )
    parser.add_argument(
        "--config",
        default="baseline.yaml",
        help="Base config file under configs/ (default: baseline.yaml).",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="Additional config file(s) to deep-merge (repeatable).",
    )
    parser.add_argument(
        "--v2",
        action="store_true",
        help="Use v2 pipeline (nguoc_trend + max_pen≤0.20).",
    )
    parser.add_argument(
        "--target-r",
        type=float,
        default=2.0,
        help="Target R:R ratio for v2 pipeline (default: 2.0, optimal from t10: 3.0).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Event table output path (default: data/processed/events.parquet for baseline, "
        "pipeline_v2/data/events_v2.parquet for v2).",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config, args.override or None)
    set_global_config(cfg)

    try:
        df = run_data_pipeline(cfg)

        if args.v2:
            # V2 pipeline
            from pipeline_v2.src.events.sweep_detector_v2 import build_sweep_events_v2

            events = build_sweep_events_v2(
                df, config=cfg, v2_target_r=args.target_r
            )
            confirmed = attach_confirmations(df, events, cfg)

            # Add v2_target_r to confirmed table (carry through)
            if not confirmed.empty:
                confirmed["v2_target_r"] = args.target_r

            out_path = args.output or os.path.join(
                ROOT, "pipeline_v2", "data", "events_v2.parquet"
            )
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            confirmed.to_parquet(out_path, index=False)

            n_confirmed = int(confirmed["is_confirmed"].sum()) if not confirmed.empty else 0
            print(
                f"[xauusd-events-v2] V2 OK: {len(events)} events "
                f"({n_confirmed} confirmed), target_r={args.target_r}"
            )
            print(f"[xauusd-events-v2] events -> {out_path}")
        else:
            # Baseline v1.2.0 (unchanged)
            from src.events.sweep_detector import build_sweep_events

            events = build_sweep_events(df, config=cfg)
            confirmed = attach_confirmations(df, events, cfg)

            out_path = args.output or resolve_path(cfg, "data/processed/events.parquet")
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            confirmed.to_parquet(out_path, index=False)

            n_confirmed = int(confirmed["is_confirmed"].sum()) if not confirmed.empty else 0
            group_rule = cfg["sweep"].get("group_rule", "first")
            print(
                f"[xauusd-events-v2] Baseline OK: {len(events)} events "
                f"({n_confirmed} confirmed), group_rule={group_rule}"
            )
            print(f"[xauusd-events-v2] events -> {out_path}")

    except Exception as exc:
        print(f"[xauusd-events-v2] ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(build_events_v2_cli())