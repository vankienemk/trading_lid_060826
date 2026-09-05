#!/usr/bin/env python3
"""V2 pipeline runner: end-to-end orchestration with --v2 routing.

Usage:
    python pipeline_v2/scripts/run_pipeline_v2.py              # baseline (v1.2.0 frozen)
    python pipeline_v2/scripts/run_pipeline_v2.py --v2         # v2 pipeline
    python pipeline_v2/scripts/run_pipeline_v2.py --v2 --target-r 3.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
V2_ROOT = os.path.join(PROJECT_ROOT, "pipeline_v2")
PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")

V2_PATHS = {
    "events": os.path.join(V2_ROOT, "data", "events_v2.parquet"),
    "artifact_datasets": os.path.join(V2_ROOT, "artifacts", "datasets"),
    "dataset": os.path.join(V2_ROOT, "artifacts", "datasets", "liquidity_sweep_events_v2.parquet"),
    "labeled_events": os.path.join(V2_ROOT, "data", "labeled_events_v2.parquet"),
    "models_dir": os.path.join(V2_ROOT, "artifacts", "models"),
    "model": os.path.join(V2_ROOT, "artifacts", "models", "model.pkl"),
    "calibrator": os.path.join(V2_ROOT, "artifacts", "models", "calibrator.pkl"),
    "metrics_dir": os.path.join(V2_ROOT, "reports", "metrics"),
    "metrics": os.path.join(V2_ROOT, "reports", "metrics", "test_metrics.json"),
    "wf_report": os.path.join(V2_ROOT, "reports", "metrics", "walk_forward_report.json"),
    "top_prob_json": os.path.join(V2_ROOT, "reports", "metrics", "top_prob_subset_analysis.json"),
    "top_prob_md": os.path.join(V2_ROOT, "reports", "metrics", "top_prob_subset_analysis.md"),
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    label = " ".join(cmd[:4]) if len(cmd) > 4 else " ".join(cmd)
    print(f"[run] {label} ...")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        print(f"[FAIL] exit={result.returncode}")
        print(result.stderr[:2000])
    else:
        line = result.stdout.strip().split("\n")[-1] if result.stdout.strip() else ""
        print(f"[OK] {line[:120]}")
    return result


def md5(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def step_events(args):
    if args.skip_events:
        p = V2_PATHS["events"] if args.v2 else "data/processed/events.parquet"
        print(f"[skip-events] {'EXISTS' if os.path.exists(p) else 'MISSING'}: {p}")
        return
    cmd = [PYTHON, "-m", "pipeline_v2.scripts.build_events_v2", "--config", "baseline.yaml"]
    if args.v2:
        cmd += ["--v2", "--target-r", str(args.target_r)]
    r = run(cmd)
    if r.returncode != 0:
        sys.exit(r.returncode)


def step_dataset(args, events_path: str):
    if args.skip_dataset:
        print(f"[skip-dataset] {'EXISTS' if os.path.exists(V2_PATHS['dataset']) else 'MISSING'}: {V2_PATHS['dataset']}")
        return
    cmd = [PYTHON, "-m", "src.pipelines.build_dataset", "--config", "baseline.yaml", "--events", events_path]
    if args.v2:
        os.makedirs(V2_PATHS["artifact_datasets"], exist_ok=True)
        cmd += ["--output", V2_PATHS["dataset"]]
    r = run(cmd)
    if r.returncode != 0:
        sys.exit(r.returncode)
    if args.v2 and os.path.exists(V2_PATHS["dataset"]):
        os.makedirs(os.path.dirname(V2_PATHS["labeled_events"]), exist_ok=True)
        shutil.copy2(V2_PATHS["dataset"], V2_PATHS["labeled_events"])
        print(f"[v2] also -> {V2_PATHS['labeled_events']}")


def step_train(args, dataset_path: str):
    if args.skip_train:
        print(f"[skip-train] {'EXISTS' if os.path.exists(V2_PATHS['model']) else 'MISSING'}: {V2_PATHS['model']}")
        return
    cmd = [PYTHON, "-m", "src.pipelines.train_model", "--config", "baseline.yaml", "--dataset", dataset_path]
    if args.v2:
        os.makedirs(V2_PATHS["models_dir"], exist_ok=True)
        os.makedirs(V2_PATHS["metrics_dir"], exist_ok=True)
        cmd += ["--output-dir", V2_PATHS["models_dir"], "--metrics", V2_PATHS["metrics"]]
    r = run(cmd)
    if r.returncode != 0:
        sys.exit(r.returncode)
    if args.v2:
        wf_src = os.path.join(PROJECT_ROOT, "reports", "walk_forward_report.json")
        if os.path.exists(wf_src):
            shutil.copy2(wf_src, V2_PATHS["wf_report"])
            print(f"[v2] WF -> {V2_PATHS['wf_report']}")
            os.rename(wf_src, os.path.join(PROJECT_ROOT, "reports", "walk_forward_report_v2.json"))


def step_top_prob(args):
    if args.skip_top_prob:
        print("[skip-top-prob]")
        return
    if args.v2:
        labeled_default = os.path.join(PROJECT_ROOT, "data", "processed", "labeled_events.parquet")
        backup = labeled_default + ".baseline_backup"
        if os.path.exists(labeled_default) and not os.path.exists(backup):
            shutil.copy2(labeled_default, backup)
        if os.path.exists(V2_PATHS["labeled_events"]):
            shutil.copy2(V2_PATHS["labeled_events"], labeled_default)
    cmd = [PYTHON, "scripts/generate_top_prob_analysis.py"]
    if args.force:
        cmd.append("--force")
    r = run(cmd)
    if args.v2:
        top_json_src = os.path.join(PROJECT_ROOT, "reports", "top_prob_subset_analysis.json")
        top_md_src = os.path.join(PROJECT_ROOT, "reports", "top_prob_subset_analysis.md")
        if os.path.exists(top_json_src):
            os.makedirs(V2_PATHS["metrics_dir"], exist_ok=True)
            shutil.copy2(top_json_src, V2_PATHS["top_prob_json"])
            os.rename(top_json_src, os.path.join(PROJECT_ROOT, "reports", "top_prob_subset_analysis_v2.json"))
        if os.path.exists(top_md_src):
            shutil.copy2(top_md_src, V2_PATHS["top_prob_md"])
            os.rename(top_md_src, os.path.join(PROJECT_ROOT, "reports", "top_prob_subset_analysis_v2.md"))
        labeled_default = os.path.join(PROJECT_ROOT, "data", "processed", "labeled_events.parquet")
        backup = labeled_default + ".baseline_backup"
        if os.path.exists(backup):
            shutil.copy2(backup, labeled_default)
            os.remove(backup)
    if r.returncode != 0:
        sys.exit(r.returncode)


def step_score_buckets(args):
    if args.skip_score_buckets:
        print("[skip-score-buckets]")
        return
    cmd = [PYTHON, "scripts/generate_reports.py"]
    r = run(cmd)
    if args.v2:
        bucket_json_src = os.path.join(PROJECT_ROOT, "reports", "score_bucket_report.json")
        bucket_md_src = os.path.join(PROJECT_ROOT, "reports", "score_bucket_report.md")
        if os.path.exists(bucket_json_src):
            os.makedirs(V2_PATHS["metrics_dir"], exist_ok=True)
            shutil.copy2(bucket_json_src, os.path.join(V2_PATHS["metrics_dir"], "score_bucket_report.json"))
            print(f"[v2] score_bucket -> {V2_PATHS['metrics_dir']}")
    if r.returncode != 0:
        sys.exit(r.returncode)


FROZEN_MD5 = {
    "artifacts/models/model.pkl": "69d65883bef616ebe23993478629fe45",
    "artifacts/models/calibrator.pkl": "ca1d7e61b4a47f376e4c0d0a6a136615",
    "reports/metrics/test_metrics.json": "3087b9e3639df95f2b5eb4d033175c4a",
    "reports/walk_forward_report.json": "59e67682b1e626d5f8f6ecf6b3810e94",
    "reports/top_prob_subset_analysis.json": "d360daa483c8fd10caaffbae49730fac",
    "reports/score_bucket_report.json": "a1b92f87d6bb12300b907721a44b7893",
    "data/processed/labeled_events.parquet": "fac22cea82ef7d0df646a9106239a70c",
    "artifacts/datasets/liquidity_sweep_events.parquet": "fac22cea82ef7d0df646a9106239a70c",
}


def verify_frozen() -> dict:
    mismatches = {}
    for rel, exp in FROZEN_MD5.items():
        actual = md5(os.path.join(PROJECT_ROOT, rel))
        if actual != exp:
            mismatches[rel] = {"expected": exp, "actual": actual}
    if not mismatches:
        print("[frozen] ALL 8/8 MATCH v1.2.0 spec")
    else:
        print(f"[frozen] {len(mismatches)} MISMATCH(es): {mismatches}")
    return mismatches


def run_baseline(args):
    print("=" * 60)
    print("BASELINE PIPELINE (v1.2.0 frozen)")
    print("=" * 60)
    # Force full re-run for baseline
    ev = os.path.join(PROJECT_ROOT, "data", "processed", "events.parquet")
    ds = os.path.join(PROJECT_ROOT, "artifacts", "datasets", "liquidity_sweep_events.parquet")
    args.skip_events = args.skip_dataset = args.skip_train = args.skip_top_prob = args.skip_score_buckets = False
    step_events(args)
    step_dataset(args, ev)
    step_train(args, ds)
    step_top_prob(args)
    step_score_buckets(args)
    return verify_frozen()


def run_v2(args):
    print("=" * 60)
    print(f"V2 PIPELINE (target_r={args.target_r})")
    print("=" * 60)
    step_events(args)
    step_dataset(args, V2_PATHS["events"])
    step_train(args, V2_PATHS["dataset"])
    step_top_prob(args)
    step_score_buckets(args)

    check = {}
    for name, path in V2_PATHS.items():
        if os.path.exists(path):
            check[name] = {"exists": True, "size": os.path.getsize(path)}
        else:
            check[name] = {"exists": False, "size": 0}
    missing = [k for k, v in check.items() if not v["exists"]]
    print(f"[v2-outputs] {sum(1 for v in check.values() if v['exists'])}/{len(check)} exist")
    if missing:
        print(f"[v2-outputs] MISSING: {missing}")

    wf_pr_auc = test_pr_auc = None
    try:
        with open(V2_PATHS["wf_report"]) as f:
            wf_pr_auc = json.load(f).get("aggregate", {}).get("pr_auc", {}).get("mean", None)
        with open(V2_PATHS["metrics"]) as f:
            test_pr_auc = json.load(f).get("pr_auc", None)
    except Exception:
        pass
    print(f"[v2-metrics] test PR-AUC={test_pr_auc}, WF PR-AUC mean={wf_pr_auc}")

    return {"check": check, "test_pr_auc": test_pr_auc, "wf_pr_auc_mean": wf_pr_auc}


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="run_pipeline_v2")
    p.add_argument("--v2", action="store_true")
    p.add_argument("--target-r", type=float, default=2.0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--skip-events", action="store_true")
    p.add_argument("--skip-dataset", action="store_true")
    p.add_argument("--skip-train", action="store_true")
    p.add_argument("--skip-top-prob", action="store_true")
    p.add_argument("--skip-score-buckets", action="store_true")
    p.add_argument("--skip-baseline-check", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.v2:
        result = run_baseline(args)
        if result:
            print(f"\n[result] BASELINE FAILED: {len(result)} md5 mismatches")
            return 1
        print("\n[result] BASELINE OK — frozen v1.2.0 intact")
        return 0

    if not args.skip_baseline_check:
        bl = run_baseline(args)
        if bl:
            print(f"\n[result] ABORT: baseline frozen integrity violated ({len(bl)} mismatches)")
            return 1
    else:
        print("[skip-baseline-check]")

    result = run_v2(args)
    missing = [k for k, v in result["check"].items() if not v["exists"]]
    if missing:
        print(f"\n[result] V2 PARTIAL: {len(missing)} output(s) missing: {missing}")
    else:
        print("\n[result] V2 PIPELINE COMPLETE — all outputs OK")
    print(f"         test PR-AUC={result['test_pr_auc']}, WF PR-AUC mean={result['wf_pr_auc_mean']}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())