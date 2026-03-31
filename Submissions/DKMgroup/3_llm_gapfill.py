#!/usr/bin/env python3
"""
Stage 3: Targeted LLM gap-filler.
Reads the Stage 2 (API-patched) submission, identifies which columns are still
"Not Applicable" for each PXD, then sends a FOCUSED prompt to Claude asking it
to re-read the manuscript and fill ONLY the missing columns.

Pipeline:
  Stage 1: process_improved.py     → broad LLM extraction
  Stage 2: fetch_all_metadata.py   → API gap-fill
  Stage 3: llm_gapfill.py (THIS)   → targeted LLM re-read for remaining gaps

Usage:
    # Test (defaults):
    export ANTHROPIC_API_KEY="sk-ant-..."
    python llm_gapfill.py

    # Training:
    python llm_gapfill.py \
        --input error_analysis/predictions_api_patched.csv \
        --output error_analysis/predictions_gapfill.csv \
        --pubtext "Train PubText/Train PubText/PubText.json" \
        --cache-dir cache_gapfill_train
"""
import json, csv, os, re, sys, time
import anthropic

# ============================================================================
# PATHS
# ============================================================================
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 3: Targeted LLM gap-fill")
parser.add_argument("--input", default=os.path.join(BASE_DIR, "api_patched", "submission.csv"),
                    help="Input CSV (Stage 2 output)")
parser.add_argument("--output", default=os.path.join(BASE_DIR, "final_submission", "submission.csv"),
                    help="Output CSV path")
parser.add_argument("--pubtext", default=os.path.join(BASE_DIR, "Test PubText", "Test PubText", "PubText.json"),
                    help="Path to PubText.json")
parser.add_argument("--cache-dir", default=os.path.join(BASE_DIR, "cache_gapfill"),
                    help="Cache directory for LLM responses")
args = parser.parse_args()

INPUT_CSV = args.input
OUTPUT_CSV = args.output
OUTPUT_DIR = os.path.dirname(OUTPUT_CSV)
PUBTEXT_PATH = args.pubtext
CACHE_DIR = args.cache_dir

#MODEL = "claude-sonnet-4-20250514"
MODEL = "claude-opus-4-6"
MAX_TOKENS = 128000
NA = "Not Applicable"

# ============================================================================
# VERIFY INPUTS
# ============================================================================
for path, name in [
    (INPUT_CSV, "Stage 2 submission CSV"),
    (PUBTEXT_PATH, "PubText.json"),
]:
    if not os.path.isfile(path):
        print(f"ERROR: {name} not found at: {path}")
        sys.exit(1)

### PLEASE USE CLAUDE API KEY FOR REPLICATING OUR SUBMISSION ###
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
#API_KEY = ""
if not API_KEY:
    print('ERROR: export ANTHROPIC_API_KEY="sk-ant-..." first')
    sys.exit(1)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================================
# LOAD DATA
# ============================================================================
print(f"Loading Stage 2 submission: {INPUT_CSV}")
rows = []
with open(INPUT_CSV, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    COLUMNS = reader.fieldnames
    rows = list(reader)

META_COLS = [c for c in COLUMNS if c not in ("ID", "PXD", "Raw Data File", "Usage")]

print(f"Loading PubText: {PUBTEXT_PATH}")
with open(PUBTEXT_PATH) as f:
    pubtext = json.load(f)

print(f"  {len(rows)} rows, {len(pubtext)} PXDs, {len(META_COLS)} metadata columns")

# ============================================================================
# IDENTIFY GAPS PER PXD
# ============================================================================
# For each PXD, find which columns are still NA across ALL rows for that PXD
# AND which columns are NA for specific files (per-file gaps)
pxd_ids = sorted(set(r['PXD'] for r in rows))

pxd_gaps = {}
for pxd in pxd_ids:
    pxd_rows = [r for r in rows if r['PXD'] == pxd]
    
    # Study-level gaps: columns NA for ALL rows in this PXD
    study_gaps = []
    for col in META_COLS:
        if all(r.get(col, NA).strip() == NA for r in pxd_rows):
            study_gaps.append(col)
    
    # Per-file gaps: columns that are filled for SOME rows but NA for others
    perfile_gaps = {}
    for col in META_COLS:
        if col in study_gaps:
            continue  # already a study gap
        na_files = [r['Raw Data File'] for r in pxd_rows if r.get(col, NA).strip() == NA]
        filled_files = [r['Raw Data File'] for r in pxd_rows if r.get(col, NA).strip() != NA]
        if na_files and filled_files:
            perfile_gaps[col] = na_files
    
    # What's already filled (for context)
    filled_cols = {}
    for col in META_COLS:
        vals = set()
        for r in pxd_rows:
            v = r.get(col, NA).strip()
            if v != NA:
                vals.add(v)
        if vals:
            filled_cols[col] = sorted(vals)
    
    total_cells = len(pxd_rows) * len(META_COLS)
    filled_cells = sum(1 for r in pxd_rows for c in META_COLS if r.get(c, NA).strip() != NA)
    
    pxd_gaps[pxd] = {
        'study_gaps': study_gaps,
        'perfile_gaps': perfile_gaps,
        'filled_cols': filled_cols,
        'n_rows': len(pxd_rows),
        'fill_pct': 100 * filled_cells / total_cells if total_cells else 0,
    }
    
    print(f"  {pxd}: {len(pxd_rows)} rows, {filled_cells}/{total_cells} filled ({pxd_gaps[pxd]['fill_pct']:.1f}%), "
          f"{len(study_gaps)} study-level gaps, {len(perfile_gaps)} per-file gap columns")

# ============================================================================
# COLUMNS WORTH ASKING ABOUT — skip columns that are almost never applicable
# These columns are rarely filled in proteomics SDRF and would just confuse the LLM
# ============================================================================
SKIP_COLUMNS = {
    # Tumor-specific — only for cancer tissue studies
    "Characteristics[AnatomicSiteTumor]", "Characteristics[TumorCellularity]",
    "Characteristics[TumorGrade]", "Characteristics[TumorSite]",
    "Characteristics[TumorSize]", "Characteristics[TumorStage]",
    "Characteristics[OriginSiteDisease]",
    # Rarely used
    "Characteristics[AncestryCategory]", "Characteristics[BMI]",
    "Characteristics[Staining]", "Characteristics[SyntheticPeptide]",
    "Characteristics[GrowthRate]", "Characteristics[PooledSample]",
    "Characteristics[Depletion]", "Characteristics[DevelopmentalStage]",
    "Characteristics[DiseaseTreatment]", "Characteristics[SamplingTime]",
    # Modification slots — Stage 1 handles these well enough
    "Characteristics[Modification].5", "Characteristics[Modification].6",
}

# ============================================================================
# VALUE FORMAT GUIDELINES
# ============================================================================
FORMAT_RULES = """
<value_format_rules>
Every value is a SHORT string (1-5 words). No sentences. No parentheticals.

WRONG: "Homo sapiens (human)" → RIGHT: "Homo sapiens"
WRONG: "Orbitrap Exploris 480" → RIGHT: "Exploris 480"
WRONG: "FLAG immunoprecipitation" → RIGHT: "immunoprecipitation"

Critical constraints:
- Characteristics[Disease]: "normal" for healthy samples/cell lines
- Characteristics[MaterialType]: "cell line" for cell lines, "tissue" for tissue, "cells" for primary cells
- Characteristics[Temperature]: "37°C" for standard cell culture
- Characteristics[Organism]: Scientific name only, no parentheticals
- Comment[AcquisitionMethod]: "DDA", "DIA", "PRM", "SWATH", "MSE"
- Comment[FragmentationMethod]: "HCD", "CID", "ETD", "EtHCD"
- Comment[MS2MassAnalyzer]: Infer from instrument. Orbitrap→"orbitrap", TripleTOF→"TOF", LTQ→"ion trap"
- Comment[Instrument]: SHORT commercial name. "Exploris 480" not "Orbitrap Exploris 480"
- Comment[EnrichmentMethod]: Generic term: "immunoprecipitation" not "FLAG IP"
- Comment[Separation]: "C18", "reversed-phase C18", "FAIMS"
- Comment[FragmentMassTolerance]: e.g. "0.02 Da". NOT lock masses, NOT FWHM.
- Comment[PrecursorMassTolerance]: e.g. "10 ppm". NOT FWHM.
- Characteristics[ReductionReagent]: "DTT" or "TCEP". NOT enzymes.
- Characteristics[CellPart]: Subcellular fractions only. NOT cell types.
- Characteristics[CellType]: Cell types only. NOT cell line names.
- FactorValue columns: ONLY if that variable is compared across samples
</value_format_rules>
"""

# ============================================================================
# BUILD TARGETED PROMPT
# ============================================================================
def build_gapfill_prompt(pxd_id, paper, raw_files, gap_info):
    """Build a focused prompt that tells the LLM exactly which columns need filling."""
    
    title = paper.get('TITLE', '')
    abstract = paper.get('ABSTRACT', '')
    methods = paper.get('METHODS', '')
    
    manuscript = f"TITLE:\n{title}\n\nABSTRACT:\n{abstract}\n\nMETHODS:\n{methods}"
    if len(manuscript) > 60000:
        manuscript = f"TITLE:\n{title}\n\nABSTRACT:\n{abstract}\n\nMETHODS:\n{methods[:40000]}\n[...truncated...]"
    
    # Filter gaps to only the columns worth asking about
    study_gaps = [c for c in gap_info['study_gaps'] if c not in SKIP_COLUMNS]
    perfile_gaps = {c: files for c, files in gap_info['perfile_gaps'].items() if c not in SKIP_COLUMNS}
    
    if not study_gaps and not perfile_gaps:
        return None  # Nothing to ask about
    
    # Build the "what we already know" context
    filled_summary = []
    for col, vals in sorted(gap_info['filled_cols'].items()):
        display_vals = vals[:5]
        if len(vals) > 5:
            display_vals.append(f"... ({len(vals)} total)")
        filled_summary.append(f"  {col}: {', '.join(str(v) for v in display_vals)}")
    filled_block = "\n".join(filled_summary) if filled_summary else "  (none)"
    
    # Build the gaps list
    gaps_block = "STUDY-LEVEL GAPS (NA for ALL files — fill if you can infer from text):\n"
    for col in study_gaps:
        gaps_block += f"  - {col}\n"
    
    if perfile_gaps:
        gaps_block += "\nPER-FILE GAPS (filled for some files, NA for others):\n"
        for col, files in sorted(perfile_gaps.items()):
            gaps_block += f"  - {col}: missing for {len(files)} files\n"
    
    files_block = "\n".join(raw_files)
    
    prompt = f"""<task>
Fill GAPS in SDRF-Proteomics metadata for dataset {pxd_id}.
A first-pass extraction already filled some columns. Your job: re-read the manuscript and fill what is still missing.
Return ONLY valid JSON. No text before or after. No markdown fences.
</task>

<critical_behavior>
You MUST try hard to fill every missing column. Being conservative is a FAILURE.
If you can reasonably infer a value, FILL IT. Common inferences:
- Cell line study with no disease → Characteristics[Disease] = "normal"
- Cell culture → Characteristics[Temperature] = "37°C"
- Cell line used → Characteristics[MaterialType] = "cell line"
- Orbitrap instrument → Comment[MS2MassAnalyzer] = "orbitrap"
- LC-MS with C18 column → Comment[Separation] = "C18"
- Count the raw files → Characteristics[NumberOfSamples]
- Parse filenames for fractions → Comment[FractionIdentifier], Comment[NumberOfFractions]
- Parse filenames for bait proteins → Characteristics[Bait], FactorValue[Bait]
</critical_behavior>

<already_extracted>
{filled_block}
</already_extracted>

<still_missing>
{gaps_block}
</still_missing>

{FORMAT_RULES}

<where_to_look>
- Disease: If healthy organisms or standard cell lines → "normal"
- Sex/Age/Strain: Animal experiment sections
- Instrument, collision energy, tolerances: LC-MS/MS methods paragraphs
- Flow rates, gradient times: LC setup paragraphs
- Enrichment: phospho-enrichment, IP, etc. Use generic "immunoprecipitation"
- Missed cleavages, modifications: Database search parameter sections
- Per-file differences: Parse filenames for replicates, fractions, conditions, bait proteins
- NumberOfSamples: count the raw files listed below
</where_to_look>

<output_format>
JSON object. "_GLOBAL_" for shared values. Per-file keys only for differing values.
Only include NEWLY filled columns. Values must be SHORT strings, no parentheticals.

{{{{
  "_GLOBAL_": {{{{
    "Characteristics[Disease]": "normal",
    "Characteristics[Temperature]": "37°C",
    "Comment[MS2MassAnalyzer]": "orbitrap",
    "Comment[Separation]": "C18",
    "Characteristics[NumberOfSamples]": "80"
  }}}},
  "nsp3.1_rep1_0C.raw": {{{{
    "Characteristics[Bait]": "nsp3.1",
    "FactorValue[Bait]": "nsp3.1",
    "Characteristics[BiologicalReplicate]": "1",
    "Comment[FractionIdentifier]": "0C"
  }}}}
}}}}
</output_format>

<manuscript>
{manuscript}
</manuscript>

<raw_files>
{files_block}
</raw_files>"""
    
    return prompt

# ============================================================================
# API CALL + PARSING (reused from process_improved.py)
# ============================================================================
client = anthropic.Anthropic(api_key=API_KEY)


def call_api(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                msg = stream.get_final_message()
                for block in msg.content:
                    if block.type == "text":
                        return block.text
                return ""
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"      Rate limited, waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"      API error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
    return ""


def parse_response(text, expected_files):
    """Parse LLM JSON response, merge _GLOBAL_ with per-file overrides."""
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1:]
    if text.endswith("```"):
        text = text[:-3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                data = json.loads(m.group())
            except:
                return {}
        else:
            return {}
    if not isinstance(data, dict):
        return {}

    glob = _normalize(data.pop("_GLOBAL_", {}))
    result = {}
    for f in expected_files:
        per_file = {}
        if f in data and isinstance(data[f], dict):
            per_file = data[f]
        else:
            for k in data:
                if k.lower() == f.lower() and isinstance(data[k], dict):
                    per_file = data[k]
                    break
        merged = dict(glob)
        merged.update(_normalize(per_file))
        result[f] = merged
    return result


def _normalize(d):
    """Flatten lists and clean values."""
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            if k.startswith("Characteristics[Modification]") and len(v) > 1:
                out[k] = str(v[0])
                for i, val in enumerate(v[1:], 1):
                    out[f"Characteristics[Modification].{i}"] = str(val)
            else:
                for item in v:
                    if item and str(item).strip():
                        out[k] = str(item).strip()
                        break
        elif v is not None:
            s = str(v).strip()
            if s:
                out[k] = s
    return out


# ============================================================================
# PROCESS EACH PXD
# ============================================================================
print(f"\n{'='*60}")
print("Stage 3: Targeted LLM Gap-Fill")
print(f"{'='*60}\n")

total_new_fills = 0

for i, pxd_id in enumerate(pxd_ids):
    gap_info = pxd_gaps[pxd_id]
    pxd_rows = [r for r in rows if r['PXD'] == pxd_id]
    raw_files = sorted(set(r['Raw Data File'] for r in pxd_rows))
    
    # Check if we have anything worth asking about
    askable_study_gaps = [c for c in gap_info['study_gaps'] if c not in SKIP_COLUMNS]
    askable_perfile_gaps = {c: f for c, f in gap_info['perfile_gaps'].items() if c not in SKIP_COLUMNS}
    
    if not askable_study_gaps and not askable_perfile_gaps:
        print(f"[{i+1}/{len(pxd_ids)}] {pxd_id}: no actionable gaps — skipping")
        continue
    
    # Check cache
    cache_file = os.path.join(CACHE_DIR, f"{pxd_id}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            parsed = json.load(f)
        n = sum(len(v) for v in parsed.values())
        print(f"[{i+1}/{len(pxd_ids)}] {pxd_id}: cached ({n} new fields)")
    else:
        if pxd_id not in pubtext:
            print(f"[{i+1}/{len(pxd_ids)}] {pxd_id}: no manuscript text — skipping")
            continue
        
        prompt = build_gapfill_prompt(pxd_id, pubtext[pxd_id], raw_files, gap_info)
        if prompt is None:
            print(f"[{i+1}/{len(pxd_ids)}] {pxd_id}: no actionable gaps — skipping")
            continue
        
        print(f"[{i+1}/{len(pxd_ids)}] {pxd_id}: {len(askable_study_gaps)} study gaps, "
              f"{len(askable_perfile_gaps)} per-file gap cols, prompt {len(prompt)//1000}K chars — calling API...")
        
        text = call_api(prompt)
        if text:
            parsed = parse_response(text, raw_files)
            with open(cache_file, 'w') as f:
                json.dump(parsed, f, indent=2)
            n = sum(len(v) for v in parsed.values())
            print(f"         extracted {n} new fields across {len(parsed)} files")
        else:
            parsed = {}
            print(f"         FAILED — empty response")
        
        time.sleep(2)
    
    # Merge new fills into submission rows — ONLY fill NA cells
    pxd_fills = 0
    for row in pxd_rows:
        raw_file = row['Raw Data File']
        file_meta = parsed.get(raw_file, {})
        if not file_meta:
            # Try case-insensitive match
            for k, v in parsed.items():
                if k.lower() == raw_file.lower():
                    file_meta = v
                    break
        
        for col, val in file_meta.items():
            if col not in META_COLS:
                continue
            # ONLY fill cells that are still NA — never overwrite Stage 1 or Stage 2 data
            if row.get(col, NA).strip() == NA:
                val_clean = str(val).strip()
                if val_clean and val_clean.lower() not in ("", "none", "null", "n/a", "na", "not applicable"):
                    row[col] = val_clean
                    pxd_fills += 1
    
    total_new_fills += pxd_fills
    if pxd_fills > 0:
        print(f"         → filled {pxd_fills} new cells")

# ============================================================================
# WRITE OUTPUT
# ============================================================================
with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)

# ============================================================================
# STATS
# ============================================================================
filled = sum(1 for r in rows for c in META_COLS if r[c] != NA)
total = len(rows) * len(META_COLS)

print(f"\n{'='*60}")
print(f"DONE — {OUTPUT_CSV}")
print(f"{'='*60}")
print(f"New cells filled by Stage 3: {total_new_fills}")
print(f"Total filled: {filled}/{total} ({100*filled/total:.1f}%)")
print()
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%)")
