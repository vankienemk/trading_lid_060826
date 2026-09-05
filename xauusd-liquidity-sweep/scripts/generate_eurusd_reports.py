#!/usr/bin/env python3
"""Generate EURUSD score-bucket and walk-forward reports using v2 pipeline outputs.

This is a custom wrapper that reads the EURUSD dataset from the v2 pipeline
artifacts and calls the reporting functions with correct paths.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.config import load_config
from src.data.loader import read_parquet
from scripts.generate_reports import generate_score_bucket_report, generate_walk_forward_report


def main():
    print("=" * 60)
    print("EURUSD Report Generation using V2 Pipeline")
    print("=" * 60)

    # Load EURUSD config
    cfg = load_config("baseline.yaml", ["eurusd_override.yaml"])
    print(f"\nConfig loaded: symbol={cfg['project']['symbol']}")

    # Load EURUSD dataset
    labeled_path = os.path.join(PROJECT_ROOT, "pipeline_v2", "artifacts", "datasets", "liquidity_sweep_events_eurusd.parquet")
    print(f"Loading labeled data from: {labeled_path}")
    labeled_df = read_parquet(labeled_path)
    print(f"Loaded {len(labeled_df)} events with {len(labeled_df.columns)} columns")

    # Verify required columns
    required = ["rule_score", "outcome_2r_h16", "net_result_r"]
    missing = [c for c in required if c not in labeled_df.columns]
    if missing:
        print(f"ERROR: Missing required columns: {missing}")
        sys.exit(1)

    # Output directory
    output_dir = os.path.join(PROJECT_ROOT, "pipeline_v2", "reports", "metrics")
    os.makedirs(output_dir, exist_ok=True)

    # Generate score-bucket report
    print("\n--- Generating Score-Bucket Report ---")
    sb_result = generate_score_bucket_report(labeled_df, cfg, output_dir=output_dir)
    print(f"  JSON: {sb_result['json_path']}")
    print(f"  Markdown: {sb_result['md_path']}")
    print(f"  Figure: {sb_result['fig_path']}")
    print(f"  Events: {sb_result['n_events']}, Buckets: {sb_result['n_buckets']}")

    # Generate walk-forward report
    print("\n--- Generating Walk-Forward Report ---")
    wf_result = generate_walk_forward_report(labeled_df, cfg, output_dir=output_dir)
    print(f"  JSON: {wf_result['json_path']}")
    print(f"  Markdown: {wf_result['md_path']}")
    print(f"  Figure: {wf_result['fig_path']}")
    print(f"  Folds: {wf_result['n_folds']}")

    # Save overall summary
    summary = {
        "symbol": "EURUSD",
        "n_events": len(labeled_df),
        "score_bucket": {
            "json_path": sb_result["json_path"],
            "md_path": sb_result["md_path"],
            "n_events": sb_result["n_events"],
            "n_buckets": sb_result["n_buckets"],
        },
        "walk_forward": {
            "json_path": wf_result["json_path"],
            "md_path": wf_result["md_path"],
            "n_folds": wf_result["n_folds"],
        },
    }
    summary_path = os.path.join(output_dir, "eurusd_report_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Summary: {summary_path}")

    print("\n" + "=" * 60)
    print("EURUSD reports generated successfully!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())