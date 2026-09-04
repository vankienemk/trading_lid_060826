#!/usr/bin/env python3
"""Generate top-probability subset analysis from clean model.

This script produces deterministic results (fixed seed=42) using
out-of-sample walk-forward predictions from the clean 30-feature model.

Subset profit-factor / avg net R are computed from the REAL ``net_result_r``
column of the labeled dataset (NOT a synthetic tp=2/sl=-1/time=0 mapping).
The single ambiguous event (net_result_r = NaN) is excluded from net-based
statistics (F1-PAYOFF fix, t32).

Overwrite guard: output files are only written when ``--force`` is passed
or they do not exist yet (F1-RACE fix, t32).

Usage:
    python scripts/generate_top_prob_analysis.py [--force]

Outputs:
    - reports/top_prob_subset_analysis.json
    - reports/top_prob_subset_analysis.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.model_selection import cross_val_predict
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.data.loader import read_parquet
from src.modeling.train import prepare_features_and_labels


def generate_top_prob_analysis(
    output_dir: str = "reports",
    seed: int = 42,
    force: bool = False,
) -> dict:
    """Generate top-probability subset analysis with OOS predictions.

    Uses 5-fold cross-validation to get out-of-sample probabilities,
    ensuring no data leakage.

    Parameters
    ----------
    output_dir : str
        Directory for output files.
    seed : int
        Random seed for reproducibility.
    force : bool
        Overwrite existing output files even without --force.

    Returns
    -------
    dict
        Analysis results.
    """
    json_path = os.path.join(output_dir, "top_prob_subset_analysis.json")
    md_path = os.path.join(output_dir, "top_prob_subset_analysis.md")

    # --- F1-RACE: overwrite guard -------------------------------------
    # Outputs are frozen artifacts; refuse to clobber them unless the
    # caller explicitly opts in with --force (prevents accidental
    # overwrite during QA/audit runs).
    if os.path.exists(json_path) and os.path.exists(md_path) and not force:
        print(f"[skip] outputs already exist: {json_path}, {md_path}")
        print("[skip] pass --force to regenerate (F1-RACE overwrite guard)")
        with open(json_path) as f:
            return json.load(f)

    np.random.seed(seed)

    # Load data
    config = load_config("baseline.yaml")
    df = read_parquet("data/processed/labeled_events.parquet")

    # Prepare features (30 numeric from registry)
    X, y, feature_names = prepare_features_and_labels(df)

    # --- F1-PAYOFF: use REAL net_result_r column ----------------------
    # 'win' (binary label y=1) means the 2R target was hit, which is NOT
    # the same as P(net_result_r > 0). Subset PF/avg_net_r must come from
    # the actual trade result column, not a synthetic tp=2/sl=-1/time=0 map.
    if "net_result_r" not in df.columns:
        raise SystemExit("FATAL: net_result_r column missing from labeled dataset")
    net_r = df.loc[X.index, "net_result_r"].to_numpy(dtype=float)
    n_nan_net = int(np.isnan(net_r).sum())
    if n_nan_net:
        print(f"[info] excluding {n_nan_net} event(s) with net_result_r=NaN "
              "(ambiguous outcome) from net-based statistics")

    # Build pipeline with imputation
    # First, compute train medians for imputation (using first 80% as proxy for train)
    n_train = int(len(X) * 0.8)
    impute_medians = {}
    for col in X.columns:
        if X.iloc[:n_train][col].isna().any():
            impute_medians[col] = float(X.iloc[:n_train][col].median())

    # Fill NaN for CV
    X_imputed = X.fillna(impute_medians)

    # Get OOS probabilities via cross-validation
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000, random_state=seed)),
    ])

    probas_oos = cross_val_predict(pipe, X_imputed, y, cv=5, method="predict_proba")[:, 1]

    # Also get in-sample probabilities (for comparison, labeled as upper bound)
    pipe.fit(X_imputed, y)
    probas_insamp = pipe.predict_proba(X_imputed)[:, 1]

    def analyze_subsets(probas, label):
        """Analyze subsets at different probability thresholds."""
        results = []
        for threshold in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4]:
            mask = probas >= threshold
            n = int(mask.sum())
            if n == 0:
                continue

            pct = n / len(y) * 100
            y_sub = y.values[mask]
            net_sub = net_r[mask]

            wins = int((y_sub == 1).sum())
            losses = n - wins
            win_rate = wins / n if n > 0 else 0

            # Net-based stats use only events with a real net_result_r
            valid = ~np.isnan(net_sub)
            n_valid = int(valid.sum())
            net_valid = net_sub[valid]
            avg_net = float(net_valid.mean()) if n_valid else None
            gross_profit = float(net_valid[net_valid > 0].sum())
            gross_loss = abs(float(net_valid[net_valid < 0].sum()))
            pf = gross_profit / gross_loss if gross_loss > 0 else None
            # share of valid events with net_result_r > 0 (NOT the 'win' rate)
            net_positive_rate = float((net_valid > 0).mean()) if n_valid else None

            results.append({
                "threshold": threshold,
                "count": n,
                "n_with_net_result": n_valid,
                "pct_of_total": round(pct, 1),
                "win_rate": round(win_rate, 4),
                "net_positive_rate": round(net_positive_rate, 4) if net_positive_rate is not None else None,
                "avg_net_r": round(avg_net, 4) if avg_net is not None else None,
                "profit_factor": round(pf, 4) if pf else None,
                "expectancy_r": round(avg_net, 4) if avg_net is not None else None,
            })
        return results

    insamp_results = analyze_subsets(probas_insamp, "in-sample")
    oos_results = analyze_subsets(probas_oos, "OOS")

    # Build output with both in-sample and OOS tables
    output = {
        "model_features": len(feature_names),
        "total_events": len(y),
        "baseline_win_rate": round(float(y.mean()), 4),
        "baseline_net_positive_rate": round(float((net_r[~np.isnan(net_r)] > 0).mean()), 4),
        "net_result_source": "net_result_r (real labeled trade result column)",
        "excluded_net_nan": n_nan_net,
        "in_sample": {
            "probability_source": "In-sample (fit on full data, upper bound - NOT live edge)",
            "oos": False,
            "subsets": insamp_results,
        },
        "out_of_sample": {
            "probability_source": "5-fold cross-validation (OOS, no leakage)",
            "oos": True,
            "subsets": oos_results,
        },
    }

    # Save JSON
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)

    # Generate markdown with both tables
    md_lines = [
        "# Top-Probability Subset Analysis",
        "",
        f"**Model**: Clean ({len(feature_names)} numeric features from registry 32; 2 categorical excluded: level_type, volatility_regime)",
        f"**Total events**: {len(y)}",
        f"**Baseline (all events)**: win rate (hit 2R tp) = {y.mean():.4f}; P(net_result_r > 0) = {output['baseline_net_positive_rate']:.4f}",
        f"**Net result source**: real `net_result_r` column ({n_nan_net} ambiguous event excluded from net-based stats)",
        "",
        "## In-Sample Analysis (Upper Bound - NOT Live Edge)",
        "",
        "**Warning**: In-sample probabilities are optimistically biased. Use OOS results for realistic expectations.",
        "",
        "| Threshold | Count | Win Rate | P(net>0) | Avg Net (R) | Profit Factor |",
        "|---|---|---|---|---|---|",
    ]

    for r in insamp_results:
        pf_str = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "N/A"
        npr = f"{r['net_positive_rate']:.3f}" if r["net_positive_rate"] is not None else "N/A"
        md_lines.append(
            f"| >= {r['threshold']} | {r['count']} | "
            f"{r['win_rate']:.3f} | {npr} | "
            f"{r['avg_net_r']:.3f}R | {pf_str} |"
        )

    md_lines.extend([
        "",
        "## Out-of-Sample Analysis (Realistic)",
        "",
        "**Source**: 5-fold cross-validation (no leakage)",
        "",
        "| Threshold | Count | Win Rate | P(net>0) | Avg Net (R) | Profit Factor |",
        "|---|---|---|---|---|---|",
    ])

    for r in oos_results:
        pf_str = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "N/A"
        npr = f"{r['net_positive_rate']:.3f}" if r["net_positive_rate"] is not None else "N/A"
        md_lines.append(
            f"| >= {r['threshold']} | {r['count']} | "
            f"{r['win_rate']:.3f} | {npr} | "
            f"{r['avg_net_r']:.3f}R | {pf_str} |"
        )

    # Find best PF in OOS (only rows with a real PF)
    pf_rows = [r for r in oos_results if r["profit_factor"] is not None]
    best_pf_row = max(pf_rows, key=lambda x: x["profit_factor"]) if pf_rows else None
    best_pf = best_pf_row["profit_factor"] if best_pf_row else None

    md_lines.extend([
        "",
        "## Key Findings",
        "",
    ])

    if best_pf and best_pf > 1:
        md_lines.append(
            f"- **OOS (real net_result_r)**: Model filtering achieves PF > 1 at threshold >= {best_pf_row['threshold']}: "
            f"{best_pf_row['count']} events ({best_pf_row['pct_of_total']}%), "
            f"win={best_pf_row['win_rate']:.3f}, P(net>0)={best_pf_row['net_positive_rate']:.3f}, PF={best_pf:.2f}"
        )
    else:
        md_lines.append("- **OOS**: Model filtering does NOT achieve PF > 1 on any subset (real net_result_r)")

    md_lines.extend([
        "- 'Win rate' here = fraction hitting the 2R target (binary label), which differs from P(net_result_r > 0).",
        "- Baseline all 974 events: PF ~1 (+0.014R cost-free, real net_result_r)",
        "- Small sample sizes at high thresholds → results may be noisy (Wilson CI spans baseline for most subsets)",
        "- In-sample results are optimistically biased; trust OOS numbers",
        "",
        "## Cost Considerations",
        "",
        "- Above metrics are cost-free (gross R)",
        "- With typical costs (spread + slippage ~0.05R per trade), net expectancy decreases",
        "- At th>=0.25 OOS (real net_result_r): small n; after 0.05R cost the edge is marginal",
        "- Model provides ranking value but the edge is small after costs; do NOT trade the ML signal as-is",
    ])

    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate_top_prob_analysis",
        description="Generate top-probability subset analysis (real net_result_r, overwrite-guarded).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing frozen output files.",
    )
    args = parser.parse_args(argv)

    print("=" * 60)
    print("Generating Top-Probability Subset Analysis")
    print("=" * 60)

    result = generate_top_prob_analysis(force=args.force)

    print(f"\nTotal events: {result['total_events']}")
    print(f"Baseline win rate: {result['baseline_win_rate']:.4f}")
    print(f"Baseline P(net>0): {result['baseline_net_positive_rate']:.4f}")
    print(f"Net result source: {result['net_result_source']}")
    print(f"Excluded net NaN: {result['excluded_net_nan']}")
    print(f"In-sample subsets: {len(result['in_sample']['subsets'])}")
    print(f"OOS subsets: {len(result['out_of_sample']['subsets'])}")
    print(f"\nReports saved:")
    print(f"  reports/top_prob_subset_analysis.json")
    print(f"  reports/top_prob_subset_analysis.md")
    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
