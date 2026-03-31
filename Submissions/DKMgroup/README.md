# SDRF-Proteomics Metadata Extraction

## Approach

We extract SDRF-Proteomics metadata from scientific manuscripts using a four-stage pipeline that combines large language models with structured proteomics database APIs.

**Stage 1 — LLM Supervised broad extraction (`1_process_improved.py`):** Claude Opus 4.6 reads each manuscript (title, abstract, methods) alongside the list of raw filenames and extracts as many SDRF columns as possible in a single pass, using a structured prompt that enforces short vocabulary-consistent values and systematic filename parsing for per-file metadata such as bait protein, biological replicate, fraction identifier, and treatment condition.

**Stage 2 — Distantly Supervised metadata gap-fill using API (`2_fetch_all_metadata.py`):** Five public proteomics APIs (PRIDE Archive v2, ProteomeCentral PROXI, PeptideAtlas PROXI, jPOST PROXI, ProteomeXchange GetDataset) are queried for each PXD accession; organism, instrument, quantification method, sample attributes, and protocol text are extracted and used to fill any cells left empty by Stage 1 without overwriting existing values.

**Stage 3 — LLM Supervised re-fill (`3_llm_gapfill.py`):** Claude Opus 4.6 performs a second, focused pass over the manuscript for each PXD, receiving a gap analysis (which columns are still NA and for which files) so it can concentrate on the remaining missing values rather than re-extracting everything.

**Stage 4 — Rule Based post-processing (`4_postprocess_fixes.py`):** Deterministic post-processing corrects systematic LLM errors and vocabulary mismatches: placeholder strings are cleared, known wrong values are fixed (e.g. `"label free"` → `"label free sample"`, reporter constructs removed from GeneticModification, HCD+ion trap → orbitrap), and cross-column contradictions are resolved (e.g. MaterialType=biofluid contradicting CellLine presence).

## Requirements

```
anthropic>=0.40.0
```

All other dependencies (`csv`, `json`, `re`, `urllib`) are Python standard library.

## Usage

```bash
# Install dependency
pip install anthropic

# Set API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Run full pipeline with defaults (test set)
python pipeline.py

# Run with explicit paths
python pipeline.py \
    --pubtext  "Test PubText/Test PubText/PubText.json" \
    --scaffold SampleSubmission.csv \
    --output   submission_fixed.csv

# Skip stages (e.g. rerun only hard rules after editing script 4)
python pipeline.py --skip-stage 1 --skip-stage 2 --skip-stage 3
```

## File structure

```
pipeline.py                  # Orchestrator — runs all 4 stages
1_process_improved.py        # Stage 1: LLM broad extraction
2_fetch_all_metadata.py      # Stage 2: Public API gap-fill
3_llm_gapfill.py             # Stage 3: LLM targeted re-fill
4_postprocess_fixes.py       # Stage 4: Hard-coded rule fixes
SampleSubmission.csv         # Scaffold (provided by competition)
Test PubText/                # Manuscript text (provided by competition)
submission_fixed.csv         # Final output
```

## Notes

- Each LLM stage caches responses per PXD under `pipeline_work/cache_stage*/`. Delete a cache file to force re-extraction for a specific dataset.
- Stage 1 uses `claude-opus-4-6` with no fallback model. If the model refuses a request (e.g. for biosafety-sensitive content), Stage 2 fills the gap from public APIs.
- Stage 2 requires no API key and makes no LLM calls.
- Stage 3 only calls the LLM for PXDs that still have NA cells after Stages 1–2.
- Stage 4 is deterministic and runs in under a second.
