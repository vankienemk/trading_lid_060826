#!/usr/bin/env python3
"""Generate score-bucket and walk-forward reports from frozen artifacts.

Usage:
    python scripts/generate_reports.py

Outputs:
    - reports/figures/score_bucket_performance.png
    - reports/score_bucket_report.md
    - reports/score_bucket_report.json
    - reports/walk_forward_report.md
    - reports/walk_forward_report.json
    - reports/figures/walk_forward_folds.png
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.data.loader import read_parquet
from src.modeling.train import walk_forward_validation
from src.scoring.rule_score import assign_score_bucket, compute_bucket_report
from src.visualization.score_chart import plot_score_bucket_performance


def generate_score_bucket_report(
    labeled_df: pd.DataFrame,
    config: dict,
    output_dir: str = "reports",
) -> dict:
    """Generate score-bucket performance report from labeled data."""
    # Compute bucket report
    bucket_df = compute_bucket_report(labeled_df)

    # Save JSON
    json_path = os.path.join(output_dir, "score_bucket_report.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    bucket_df.to_json(json_path, orient="records", indent=2)

    # Generate figure
    fig_path = os.path.join(output_dir, "figures", "score_bucket_performance.png")
    os.makedirs(os.path.dirname(fig_path), exist_ok=True)
    try:
        actual_fig_path = plot_score_bucket_performance(
            labeled_df, output_path=fig_path, config=config
        )
    except Exception as e:
        print(f"Warning: Could not generate figure: {e}")
        actual_fig_path = None

    # Generate markdown report
    md_lines = [
        "# Score Bucket Performance Report",
        "",
        f"**Generated**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Dataset**: `data/processed/labeled_events.parquet`",
        f"**Total events**: {len(labeled_df)}",
        f"**Config**: `configs/baseline.yaml` (scoring.weights + score_buckets)",
        "",
        "## Bucket Table",
        "",
        "| Bucket | Events | Win Rate | Loss Rate | Ambiguous | Avg MFE (R) | Avg MAE (R) | Avg Net (R) | Profit Factor |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for _, row in bucket_df.iterrows():
        bucket = row["bucket"]
        pf_str = f"{row['profit_factor']:.4f}" if row["profit_factor"] is not None else "N/A"
        md_lines.append(
            f"| {bucket} | {int(row['event_count'])} | "
            f"{row['win_rate']*100:.2f}% | {row['loss_rate']*100:.2f}% | "
            f"{row['ambiguous_rate']*100:.2f}% | "
            f"{row['avg_mfe_r']:.4f} | {row['avg_mae_r']:.4f} | "
            f"{row['avg_net_result_r']:.4f} | {pf_str} |"
        )

    md_lines.extend([
        "",
        "## Score Distribution",
        "",
        f"- Mean rule_score: {labeled_df['rule_score'].mean():.2f}",
        f"- Std rule_score: {labeled_df['rule_score'].std():.2f}",
        f"- Min: {labeled_df['rule_score'].min():.2f}, Max: {labeled_df['rule_score'].max():.2f}",
        "",
        "## Outcome Distribution",
        "",
    ])

    outcome_counts = labeled_df["outcome_2r_h16"].value_counts()
    for outcome, count in outcome_counts.items():
        pct = count / len(labeled_df) * 100
        md_lines.append(f"- **{outcome}**: {count} ({pct:.1f}%)")

    md_lines.extend([
        "",
        "## Figure",
        "",
        f"`reports/figures/score_bucket_performance.png` — win rate, profit factor,",
        "avg net result, and event distribution by score bucket.",
        "",
        "## Notes",
        "",
        "- Rule score range: 0-100 (guide §19)",
        "- Buckets: 0-39 / 40-49 / 50-59 / 60-69 / 70-79 / 80-100",
        "- Costs included in net_result_r (guide §17)",
        "- Small sample size per bucket — interpret cautiously",
    ])

    md_path = os.path.join(output_dir, "score_bucket_report.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    return {
        "json_path": os.path.abspath(json_path),
        "md_path": os.path.abspath(md_path),
        "fig_path": os.path.abspath(actual_fig_path) if actual_fig_path else None,
        "n_events": len(labeled_df),
        "n_buckets": len(bucket_df),
    }


def generate_walk_forward_report(
    labeled_df: pd.DataFrame,
    config: dict,
    output_dir: str = "reports",
) -> dict:
    """Generate walk-forward validation report."""
    # Prepare features and labels
    from src.modeling.train import prepare_features_and_labels

    X, y, feature_names = prepare_features_and_labels(labeled_df)

    # Run walk-forward validation
    wf_result = walk_forward_validation(X, y, config)

    # Save JSON
    json_path = os.path.join(output_dir, "walk_forward_report.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    # Convert fold results for JSON serialization
    serializable_result = {
        "n_folds": wf_result["n_folds"],
        "aggregate": wf_result["aggregate"],
        "folds": [],
    }

    for fold in wf_result["folds"]:
        fold_copy = {k: v for k, v in fold.items() if k != "error"}
        # Convert numpy types to Python types
        for k, v in fold_copy.items():
            if isinstance(v, (np.integer,)):
                fold_copy[k] = int(v)
            elif isinstance(v, (np.floating,)):
                fold_copy[k] = float(v)
            elif isinstance(v, np.ndarray):
                fold_copy[k] = v.tolist()
        serializable_result["folds"].append(fold_copy)

    with open(json_path, "w") as f:
        json.dump(serializable_result, f, indent=2, default=str)

    # Generate markdown report
    md_lines = [
        "# Walk-Forward Validation Report",
        "",
        f"**Generated**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Dataset**: `data/processed/labeled_events.parquet` ({len(labeled_df)} events)",
        f"**Features**: {len(feature_names)} causal features (no look-ahead)",
        f"**Config**: `configs/baseline.yaml` (splitting + model sections)",
        f"**Seed**: 42 (frozen per baseline v1.1.0-freeze)",
        "",
        "## Aggregate Metrics (Out-of-Sample)",
        "",
        "| Metric | Mean | Std | Min | Max | N Folds |",
        "|---|---|---|---|---|---|",
    ]

    agg = wf_result.get("aggregate", {})
    for metric_name, stats in sorted(agg.items()):
        if isinstance(stats, dict):
            md_lines.append(
                f"| {metric_name} | {stats.get('mean', 'N/A'):.4f} | "
                f"{stats.get('std', 'N/A'):.4f} | "
                f"{stats.get('min', 'N/A'):.4f} | "
                f"{stats.get('max', 'N/A'):.4f} | "
                f"{stats.get('n_folds', 'N/A')} |"
            )

    md_lines.extend([
        "",
        "## Per-Fold Details",
        "",
    ])

    for fold in wf_result.get("folds", []):
        fold_idx = fold.get("fold", "?")
        train_size = fold.get("train_size", "?")
        val_size = fold.get("val_size", "?")
        model = fold.get("model", "?")
        pr_auc = fold.get("pr_auc", float("nan"))
        roc_auc = fold.get("roc_auc", float("nan"))
        brier = fold.get("brier_score", float("nan"))
        f1 = fold.get("f1", float("nan"))

        md_lines.append(
            f"### Fold {fold_idx}\n"
            f"- **Train size**: {train_size} events\n"
            f"- **Validation size**: {val_size} events\n"
            f"- **Best model**: {model}\n"
            f"- **PR-AUC**: {pr_auc:.4f}\n"
            f"- **ROC-AUC**: {roc_auc:.4f}\n"
            f"- **Brier Score**: {brier:.4f}\n"
            f"- **F1**: {f1:.4f}\n"
        )

    md_lines.extend([
        "",
        "## Figure",
        "",
        "`reports/figures/walk_forward_folds.png` — metrics across folds.",
        "",
        "## Notes",
        "",
        "- Expanding window: each fold trains on all prior data + step expansion",
        "- Purge + embargo applied between train and validation (embargo_bars=32)",
        "- OOS metrics computed on validation set for each fold",
        "- Stability assessed via mean/std of metrics across folds",
    ])

    md_path = os.path.join(output_dir, "walk_forward_report.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    # Generate figure (metrics vs fold)
    fig_path = os.path.join(output_dir, "figures", "walk_forward_folds.png")
    os.makedirs(os.path.dirname(fig_path), exist_ok=True)
    _plot_walk_forward_metrics(wf_result, fig_path)

    return {
        "json_path": os.path.abspath(json_path),
        "md_path": os.path.abspath(md_path),
        "fig_path": os.path.abspath(fig_path),
        "n_folds": wf_result["n_folds"],
    }


def _plot_walk_forward_metrics(wf_result: dict, output_path: str) -> str:
    """Plot walk-forward metrics across folds."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return ""

    folds = wf_result.get("folds", [])
    if not folds:
        return ""

    fold_indices = [f.get("fold", i) for i, f in enumerate(folds)]
    pr_aucs = [f.get("pr_auc", 0) for f in folds]
    roc_aucs = [f.get("roc_auc", 0) for f in folds]
    briers = [f.get("brier_score", 0) for f in folds]
    f1s = [f.get("f1", 0) for f in folds]

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle("Walk-Forward Validation Metrics by Fold", fontsize=14, fontweight="bold")

    # PR-AUC
    ax1 = axes[0, 0]
    ax1.plot(fold_indices, pr_aucs, "o-", color="steelblue", markersize=8)
    ax1.set_xlabel("Fold")
    ax1.set_ylabel("PR-AUC")
    ax1.set_title("Precision-Recall AUC")
    ax1.grid(True, alpha=0.3)

    # ROC-AUC
    ax2 = axes[0, 1]
    ax2.plot(fold_indices, roc_aucs, "s-", color="coral", markersize=8)
    ax2.set_xlabel("Fold")
    ax2.set_ylabel("ROC-AUC")
    ax2.set_title("ROC AUC")
    ax2.grid(True, alpha=0.3)

    # Brier Score
    ax3 = axes[1, 0]
    ax3.plot(fold_indices, briers, "^-", color="green", markersize=8)
    ax3.set_xlabel("Fold")
    ax3.set_ylabel("Brier Score")
    ax3.set_title("Brier Score (lower is better)")
    ax3.grid(True, alpha=0.3)

    # F1 Score
    ax4 = axes[1, 1]
    ax4.plot(fold_indices, f1s, "D-", color="purple", markersize=8)
    ax4.set_xlabel("Fold")
    ax4.set_ylabel("F1 Score")
    ax4.set_title("F1 Score")
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return os.path.abspath(output_path)


def main():
    """Main entry point."""
    print("=" * 60)
    print("Generating Score-Bucket and Walk-Forward Reports")
    print("=" * 60)

    # Load config
    config = load_config("baseline.yaml")
    print(f"\nConfig loaded: random_seed={config['project']['random_seed']}")

    # Load labeled data
    labeled_path = "data/processed/labeled_events.parquet"
    print(f"Loading labeled data from: {labeled_path}")
    labeled_df = read_parquet(labeled_path)
    print(f"Loaded {len(labeled_df)} events with {len(labeled_df.columns)} columns")

    # Verify required columns
    required = ["rule_score", "outcome_2r_h16", "net_result_r"]
    missing = [c for c in required if c not in labeled_df.columns]
    if missing:
        print(f"ERROR: Missing required columns: {missing}")
        sys.exit(1)

    # Generate score-bucket report
    print("\n--- Generating Score-Bucket Report ---")
    sb_result = generate_score_bucket_report(labeled_df, config)
    print(f"  JSON: {sb_result['json_path']}")
    print(f"  Markdown: {sb_result['md_path']}")
    print(f"  Figure: {sb_result['fig_path']}")
    print(f"  Events: {sb_result['n_events']}, Buckets: {sb_result['n_buckets']}")

    # Generate walk-forward report
    print("\n--- Generating Walk-Forward Report ---")
    wf_result = generate_walk_forward_report(labeled_df, config)
    print(f"  JSON: {wf_result['json_path']}")
    print(f"  Markdown: {wf_result['md_path']}")
    print(f"  Figure: {wf_result['fig_path']}")
    print(f"  Folds: {wf_result['n_folds']}")

    print("\n" + "=" * 60)
    print("Reports generated successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
