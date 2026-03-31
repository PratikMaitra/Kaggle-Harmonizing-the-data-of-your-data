#!/usr/bin/env python3
"""
SDRF-Proteomics Metadata Extraction Pipeline
=============================================
Orchestrates all four stages in sequence.

Usage (all defaults — runs on the test set):
    python pipeline.py

Usage (explicit paths):
    python pipeline.py \
        --pubtext  "Test PubText/Test PubText/PubText.json" \
        --scaffold SampleSubmission.csv \
        --output   submission_fixed.csv

Usage (training set):
    python pipeline.py \
        --pubtext  "Train PubText/Train PubText/PubText_sample.json" \
        --scaffold TrainSampleSubmission.csv \
        --output   train_predictions/submission_fixed.csv

Note: Delete a PXD cache file under cache_stage1/ to force re-extraction for
      that dataset (e.g. to handle a refusal from the LLM on the previous run).
"""
import argparse, os, subprocess, sys, time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Argument parsing ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="Run the full 4-stage SDRF metadata extraction pipeline."
)
parser.add_argument(
    "--pubtext",
    default=os.path.join(BASE_DIR, "Test PubText", "Test PubText", "PubText.json"),
    help="Path to PubText.json (manuscript text for each PXD)",
)
parser.add_argument(
    "--scaffold",
    default=os.path.join(BASE_DIR, "SampleSubmission.csv"),
    help="Path to SampleSubmission.csv (scaffold with ID, PXD, Raw Data File)",
)
parser.add_argument(
    "--output",
    default=os.path.join(BASE_DIR, "submission_fixed.csv"),
    help="Path for the final submission CSV",
)
parser.add_argument(
    "--work-dir",
    default=os.path.join(BASE_DIR, "pipeline_work"),
    help="Working directory for intermediate files and caches",
)
parser.add_argument(
    "--skip-stage",
    type=int,
    action="append",
    default=[],
    metavar="N",
    help="Skip stage N (can be repeated). e.g. --skip-stage 2 --skip-stage 3",
)
args = parser.parse_args()

# ── Validate inputs ───────────────────────────────────────────────────────────
for path, name in [(args.pubtext, "PubText.json"), (args.scaffold, "SampleSubmission.csv")]:
    if not os.path.isfile(path):
        print(f"ERROR: {name} not found at: {path}")
        sys.exit(1)

os.makedirs(args.work_dir, exist_ok=True)
os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

# ── Intermediate file paths ───────────────────────────────────────────────────
stage1_out   = os.path.join(args.work_dir, "stage1_llm",   "submission.csv")
stage2_out   = os.path.join(args.work_dir, "stage2_api",   "submission.csv")
stage3_out   = os.path.join(args.work_dir, "stage3_refill","submission.csv")
stage4_out   = args.output

cache_stage1 = os.path.join(args.work_dir, "cache_stage1")
cache_stage2 = os.path.join(args.work_dir, "cache_stage2")
cache_stage3 = os.path.join(args.work_dir, "cache_stage3")

# ── Helper ────────────────────────────────────────────────────────────────────
def run(stage_num, label, cmd):
    if stage_num in args.skip_stage:
        print(f"\n{'='*60}")
        print(f"Stage {stage_num} SKIPPED ({label})")
        print(f"{'='*60}")
        return
    print(f"\n{'='*60}")
    print(f"Stage {stage_num}: {label}")
    print(f"{'='*60}")
    t0 = time.time()
    result = subprocess.run(cmd, check=True)
    elapsed = time.time() - t0
    print(f"\nStage {stage_num} done in {elapsed/60:.1f} min")

# ── Stage 1: LLM broad extraction ────────────────────────────────────────────
run(1, "LLM broad extraction (claude-opus-4-6)",
    [
        sys.executable, os.path.join(BASE_DIR, "1_process_improved.py"),
        "--pubtext",   args.pubtext,
        "--scaffold",  args.scaffold,
        "--output",    stage1_out,
        "--cache-dir", cache_stage1,
    ]
)

# ── Stage 2: Metadata API gap-fill ───────────────────────────────────────────
run(2, "Metadata API gap-fill (PRIDE / PROXI / ProteomeXchange)",
    [
        sys.executable, os.path.join(BASE_DIR, "2_fetch_all_metadata.py"),
        "--input",     stage1_out,
        "--output",    stage2_out,
        "--cache-dir", cache_stage2,
    ]
)

# ── Stage 3: LLM targeted re-fill ────────────────────────────────────────────
run(3, "LLM targeted re-fill (claude-opus-4-6)",
    [
        sys.executable, os.path.join(BASE_DIR, "3_llm_gapfill.py"),
        "--input",     stage2_out,
        "--output",    stage3_out,
        "--pubtext",   args.pubtext,
        "--cache-dir", cache_stage3,
    ]
)

# ── Stage 4: Hard-coded rule fixes ───────────────────────────────────────────
run(4, "Hard-coded rule fixes",
    [
        sys.executable, os.path.join(BASE_DIR, "4_postprocess_fixes.py"),
        "--input",  stage3_out,
        "--output", stage4_out,
    ]
)

# ── Final summary ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"PIPELINE COMPLETE")
print(f"{'='*60}")
print(f"Final output: {stage4_out}")

import csv
NA = "Not Applicable"
with open(stage4_out, newline='', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
meta_cols = [c for c in rows[0].keys() if c not in ('ID','PXD','Raw Data File','Usage')]
filled = sum(1 for r in rows for c in meta_cols if r.get(c, NA).strip() != NA)
total  = len(rows) * len(meta_cols)
print(f"Rows: {len(rows)} | Fill rate: {filled}/{total} ({100*filled/total:.2f}%)")
print()
for pxd in sorted(set(r['PXD'] for r in rows)):
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in meta_cols if r.get(c, NA).strip() != NA)
    pt = len(pr) * len(meta_cols)
    print(f"  {pxd}: {len(pr):>5} rows  {100*pf/pt:5.1f}%")
