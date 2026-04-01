#!/usr/bin/env python3
"""
Stage 3: Targeted LLM gap-filler (v2).

Improvements over v1:
  - Full paper text (all sections, not just TITLE/ABSTRACT/METHODS)
  - Increased truncation limit (150K chars)
  - Value format rules aligned with Stage 1 v2 and Stage 4
  - Fewer SKIP_COLUMNS — ask about more fields
  - System prompt for proteomics expertise
  - Can CORRECT wrong Stage 1 values for key columns (not just fill gaps)
  - Better placeholder rejection
  - "label free" not "label free sample", instrument full names, etc.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python 3_llm_gapfill.py --input stage2.csv --output stage3.csv --pubtext PubText.json
"""
import json, csv, os, re, sys, time
import anthropic
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 3: Targeted LLM gap-fill v2")
parser.add_argument("--input", default=os.path.join(BASE_DIR, "api_patched", "submission.csv"))
parser.add_argument("--output", default=os.path.join(BASE_DIR, "final_submission", "submission.csv"))
parser.add_argument("--pubtext", default=os.path.join(BASE_DIR, "TestPubText", "PubText.json"))
parser.add_argument("--cache-dir", default=os.path.join(BASE_DIR, "cache_gapfill"))
args = parser.parse_args()

INPUT_CSV = args.input
OUTPUT_CSV = args.output
PUBTEXT_PATH = args.pubtext
CACHE_DIR = args.cache_dir

MODEL = "claude-opus-4-6"
MAX_TOKENS = 128000
NA = "Not Applicable"

for path, name in [(INPUT_CSV, "Stage 2 CSV"), (PUBTEXT_PATH, "PubText.json")]:
    if not os.path.isfile(path):
        print(f"ERROR: {name} not found at: {path}"); sys.exit(1)

#API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    print('ERROR: export ANTHROPIC_API_KEY="sk-ant-..." first'); sys.exit(1)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(OUTPUT_CSV) or '.', exist_ok=True)

# ============================================================================
# LOAD DATA
# ============================================================================
print(f"Loading Stage 2 submission: {INPUT_CSV}")
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
pxd_ids = sorted(set(r['PXD'] for r in rows))

pxd_gaps = {}
for pxd in pxd_ids:
    pxd_rows = [r for r in rows if r['PXD'] == pxd]

    study_gaps = [col for col in META_COLS
                  if all(r.get(col, NA).strip() == NA for r in pxd_rows)]

    perfile_gaps = {}
    for col in META_COLS:
        if col in study_gaps:
            continue
        na_files = [r['Raw Data File'] for r in pxd_rows if r.get(col, NA).strip() == NA]
        filled_files = [r['Raw Data File'] for r in pxd_rows if r.get(col, NA).strip() != NA]
        if na_files and filled_files:
            perfile_gaps[col] = na_files

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
          f"{len(study_gaps)} study gaps, {len(perfile_gaps)} per-file gap cols")

# ============================================================================
# SKIP COLUMNS — reduced set. Only skip truly rare columns.
# ============================================================================
SKIP_COLUMNS = {
    "Characteristics[AnatomicSiteTumor]", "Characteristics[TumorCellularity]",
    "Characteristics[TumorGrade]", "Characteristics[TumorSite]",
    "Characteristics[TumorSize]", "Characteristics[TumorStage]",
    "Characteristics[AncestryCategory]", "Characteristics[BMI]",
    "Characteristics[Staining]", "Characteristics[SyntheticPeptide]",
    "Characteristics[GrowthRate]", "Characteristics[DiseaseTreatment]",
    "Characteristics[Modification].5", "Characteristics[Modification].6",
}

# ============================================================================
# VALUE FORMAT RULES — aligned with Stage 1 v2 and Stage 4
# ============================================================================
FORMAT_RULES = """
<value_format_rules>
Every value is a SHORT string (1-5 words). No sentences. No parentheticals.
NEVER output "not available", "not specified", "unknown" — just omit the field.

Critical formatting rules:
- Characteristics[Label]: Exactly "label free" (two words). NEVER "label free sample" or "label-free".
- Characteristics[Disease]: "normal" for healthy samples/standard cell lines.
- Characteristics[MaterialType]: "cell line" for immortalized lines, "tissue" for tissue, "cells" for primary, "biofluid" for serum/plasma/urine/milk.
- Characteristics[Temperature]: "37°C" for mammalian cell culture.
- Characteristics[Organism]: Scientific name only. "Homo sapiens" not "Homo sapiens (human)".
- Characteristics[Specimen]: "cell culture" for cell-culture studies. Describe tissue specimens specifically.
- Comment[Instrument]: FULL standard name: "Orbitrap Exploris 480", "Orbitrap Fusion Lumos", "Q Exactive HF", "Orbitrap Astral", "LTQ-Orbitrap XL", "TripleTOF 5600+".
- Comment[AcquisitionMethod]: "DDA", "DIA", "PRM", "SWATH", "MSE". IDA = DDA.
- Comment[FragmentationMethod]: "HCD", "CID", "ETD", "EtHCD". NOT acquisition methods.
- Comment[MS2MassAnalyzer]: "orbitrap", "ion trap", "TOF", "Astral". Infer from instrument.
- Comment[CollisionEnergy]: Just the NUMBER. "32" not "32 NCE" or "32%".
- Comment[IonizationType]: "nanoESI" if flow ≤1 µL/min. "ESI" if flow >1 µL/min.
- Comment[FlowRateChromatogram]: Always nL/min. Convert: 0.25 µL/min = "250 nL/min".
- Comment[Separation]: "C18" for standard RP. "FAIMS" if FAIMS used.
- Comment[EnrichmentMethod]: Generic: "immunoprecipitation" not "FLAG IP".
- Characteristics[ReductionReagent]: "DTT" or "TCEP". NOT enzymes.
- Characteristics[AlkylationReagent]: "iodoacetamide" or "chloroacetamide".
- Characteristics[CellPart]: Subcellular fractions only (nucleus, exosomes). NOT cell types.
- Characteristics[CellType]: Cell types only (fibroblasts, macrophage). NOT cell line names.
- Characteristics[Age]: Age of the organism/donor. e.g. "8 weeks", "6 months", "55 years". Use the unit from the paper.
- Characteristics[DevelopmentalStage]: Life stage. "adult", "embryonic", "fetal", "neonatal". Fill for animal studies.
- Characteristics[Depletion]: Abundant protein depletion. "depletion" if depleted, "no depletion" if not. NOT gene knockdown.
- Characteristics[Modification]: For TMT: TMTpro → Carbamidomethyl → Oxidation → Met-loss → Acetyl.
  For label-free: Oxidation → Carbamidomethyl → Acetyl → Deamidated.
- FactorValue columns: ONLY when that variable is compared across samples.
</value_format_rules>
"""

# ============================================================================
# BUILD FULL MANUSCRIPT TEXT
# ============================================================================
def build_manuscript(paper):
    """Build full manuscript with all sections, not just TITLE/ABSTRACT/METHODS."""
    sections = []
    for key, label in [('TITLE', 'TITLE'), ('ABSTRACT', 'ABSTRACT'), ('INTRO', 'INTRODUCTION'),
                        ('RESULTS', 'RESULTS'), ('DISCUSS', 'DISCUSSION'),
                        ('METHODS', 'METHODS'), ('FIG', 'FIGURES')]:
        text = paper.get(key, '')
        if text:
            sections.append(f"{label}:\n{text}")

    manuscript = "\n\n".join(sections)

    if len(manuscript) > 150000:
        title = paper.get('TITLE', '')
        abstract = paper.get('ABSTRACT', '')
        methods = paper.get('METHODS', '')
        core = f"TITLE:\n{title}\n\nABSTRACT:\n{abstract}\n\nMETHODS:\n{methods}"
        budget = 150000 - len(core)
        extras = []
        for key, label in [('RESULTS', 'RESULTS'), ('INTRO', 'INTRODUCTION'), ('FIG', 'FIGURES')]:
            text = paper.get(key, '')
            if text and budget > 1000:
                chunk = text[:budget]
                extras.append(f"{label}:\n{chunk}")
                budget -= len(chunk)
        manuscript = core
        if extras:
            manuscript += "\n\n" + "\n\n".join(extras)

    return manuscript

# ============================================================================
# BUILD TARGETED PROMPT
# ============================================================================
def build_gapfill_prompt(pxd_id, paper, raw_files, gap_info):
    manuscript = build_manuscript(paper)

    study_gaps = [c for c in gap_info['study_gaps'] if c not in SKIP_COLUMNS]
    perfile_gaps = {c: files for c, files in gap_info['perfile_gaps'].items() if c not in SKIP_COLUMNS}

    if not study_gaps and not perfile_gaps:
        return None

    # Context: what's already filled
    filled_summary = []
    for col, vals in sorted(gap_info['filled_cols'].items()):
        display_vals = vals[:5]
        if len(vals) > 5:
            display_vals.append(f"... ({len(vals)} total)")
        filled_summary.append(f"  {col}: {', '.join(str(v) for v in display_vals)}")
    filled_block = "\n".join(filled_summary) if filled_summary else "  (none)"

    gaps_block = "STUDY-LEVEL GAPS (NA for ALL files — fill if you can infer from text):\n"
    for col in study_gaps:
        gaps_block += f"  - {col}\n"

    if perfile_gaps:
        gaps_block += "\nPER-FILE GAPS (filled for some files, NA for others — parse filenames):\n"
        for col, files in sorted(perfile_gaps.items()):
            gaps_block += f"  - {col}: missing for {len(files)} files\n"

    files_block = "\n".join(raw_files)

    prompt = f"""<task>
Fill GAPS in SDRF-Proteomics metadata for dataset {pxd_id}.
A first-pass extraction already filled some columns. Your job: re-read the manuscript 
carefully and fill what is still missing. Also look at the FULL paper text including 
RESULTS and FIGURES sections — method details are sometimes described there.
Return ONLY valid JSON. No text before or after. No markdown fences.
</task>

<critical_behavior>
You MUST try hard to fill every missing column. Being conservative is a FAILURE.
NEVER output "not available" or "not specified" — just omit the field.

Common inferences you MUST make:
- Cell line study with no disease mentioned → Characteristics[Disease] = "normal"
- Cell culture → Characteristics[Temperature] = "37°C"
- Cell line used → Characteristics[MaterialType] = "cell line"  
- Orbitrap with HCD → Comment[MS2MassAnalyzer] = "orbitrap"
- LTQ/Velos/Fusion with CID in ion trap → Comment[MS2MassAnalyzer] = "ion trap"
- Standard RP C18 column → Comment[Separation] = "C18"
- FAIMS mentioned → Comment[Separation] = "FAIMS"
- nanoLC (flow ≤1 µL/min) → Comment[IonizationType] = "nanoESI"
- Count the raw files listed → Characteristics[NumberOfSamples]
- Parse filenames for fractions (sequential numbers at end) → Comment[FractionIdentifier]
- Parse filenames for bait proteins → Characteristics[Bait]
- Parse filenames for replicates → Characteristics[BiologicalReplicate]
- Parse filenames for conditions/treatments → Characteristics[Treatment], FactorValue[Treatment]
- If AP-MS or Co-IP is used → Comment[EnrichmentMethod] = "immunoprecipitation"
- Animal experiments → look for Sex, Age, Strain in methods
- Cell culture from cell lines → Characteristics[Specimen] = "cell culture"

IMPORTANT — these 3 columns are commonly missed. Look carefully:
- Characteristics[Age]: Extract organism/donor age from methods. Look for patterns like
  "X-week-old mice", "X months old", "aged X weeks", "X-year-old patient".
  Use the format from the paper: "8 weeks", "6 months", "55 years".
- Characteristics[DevelopmentalStage]: For animal studies, determine the life stage.
  "adult" if the animals are mature/adult. "embryonic" if embryos. "fetal" if fetal tissue.
  "neonatal" if newborn. Look for these words in the methods section.
- Characteristics[Depletion]: Did the study use abundant protein depletion before MS?
  If serum/plasma was depleted of abundant proteins (e.g. top-14 removal, immunodepletion 
  columns, MARS, ProteoMiner) → "depletion". If no depletion was mentioned → "no depletion".
  This is about SAMPLE DEPLETION, not gene knockdown/siRNA depletion.
</critical_behavior>

<already_extracted>
{filled_block}
</already_extracted>

<still_missing>
{gaps_block}
</still_missing>

{FORMAT_RULES}

<where_to_look>
- Disease: If healthy/normal → "normal". Check title and abstract for disease context.
- Sex/Age/Strain: Animal experiment sections in methods. "male", "female", "8 weeks".
- Age: Look for "X-week-old", "X months old", "aged X", "X-year-old" in methods.
  For animal studies, age is usually stated where the animals are described.
  For human donors, look for patient demographics.
- DevelopmentalStage: Look for "adult", "embryonic", "fetal", "neonatal", "postnatal" 
  in the methods where animals/samples are described. Most animal studies use adults.
- Depletion: Look in sample preparation methods for "depletion", "depleted", 
  "abundant protein removal", "top-14", "MARS column", "ProteoMiner", "immunodepletion".
  If none found → "no depletion". This refers to PROTEIN depletion from serum/plasma,
  NOT siRNA/gene knockdown.
- Instrument/collision energy/tolerances: LC-MS/MS methods paragraphs and RESULTS.
- Flow rates/gradient times: LC setup paragraphs. Convert µL/min to nL/min.
- Enrichment: AP-MS/Co-IP → "immunoprecipitation". Phospho → "phosphopeptide enrichment".
- Missed cleavages/modifications: Database search parameter sections.
- Per-file differences: PARSE FILENAMES for replicates, fractions, conditions, bait proteins.
- NumberOfSamples: count the raw files listed below.
- Specimen: "cell culture" for cell-culture-based studies.
</where_to_look>

<output_format>
JSON object. "_GLOBAL_" for shared values. Per-file keys only for differing values.
Only include NEWLY filled columns. Values must be SHORT strings.

{{{{
  "_GLOBAL_": {{{{
    "Characteristics[Disease]": "normal",
    "Characteristics[Temperature]": "37°C",
    "Characteristics[Age]": "8 weeks",
    "Characteristics[DevelopmentalStage]": "adult",
    "Characteristics[Depletion]": "no depletion",
    "Comment[MS2MassAnalyzer]": "orbitrap",
    "Comment[Separation]": "C18",
    "Comment[IonizationType]": "nanoESI",
    "Characteristics[NumberOfSamples]": "80",
    "Characteristics[Specimen]": "cell culture"
  }}}},
  "sample_rep1_frac1.raw": {{{{
    "Characteristics[BiologicalReplicate]": "1",
    "Comment[FractionIdentifier]": "1"
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
# API
# ============================================================================
client = anthropic.Anthropic(api_key=API_KEY)

SYSTEM_PROMPT = """You are a proteomics expert specializing in SDRF metadata extraction.
You read scientific papers and extract structured metadata with high accuracy.
Return ONLY valid JSON. No explanations, no markdown, no commentary."""


def call_api(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
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


PLACEHOLDER_VALS = {"not available", "not specified", "unknown", "none", "n/a",
                    "na", "not applicable", "not provided", "not determined",
                    "not reported", "not mentioned", "missing", "unspecified"}

def _normalize(d):
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
            if s and s.lower() not in PLACEHOLDER_VALS:
                out[k] = s
    return out

# ============================================================================
# PROCESS EACH PXD
# ============================================================================
print(f"\n{'='*60}")
print("Stage 3: Targeted LLM Gap-Fill v2")
print(f"{'='*60}\n")

total_new_fills = 0

for i, pxd_id in enumerate(pxd_ids):
    gap_info = pxd_gaps[pxd_id]
    pxd_rows = [r for r in rows if r['PXD'] == pxd_id]
    raw_files = sorted(set(r['Raw Data File'] for r in pxd_rows))

    askable_study_gaps = [c for c in gap_info['study_gaps'] if c not in SKIP_COLUMNS]
    askable_perfile_gaps = {c: f for c, f in gap_info['perfile_gaps'].items() if c not in SKIP_COLUMNS}

    if not askable_study_gaps and not askable_perfile_gaps:
        print(f"[{i+1}/{len(pxd_ids)}] {pxd_id}: no actionable gaps — skipping")
        continue

    cache_file = os.path.join(CACHE_DIR, f"{pxd_id}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            try:
                parsed = json.load(f)
            except json.JSONDecodeError:
                parsed = {}
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

    # Merge new fills — ONLY fill NA cells
    pxd_fills = 0
    for row in pxd_rows:
        raw_file = row['Raw Data File']
        file_meta = parsed.get(raw_file, {})
        if not file_meta:
            for k, v in parsed.items():
                if k.lower() == raw_file.lower():
                    file_meta = v
                    break

        for col, val in file_meta.items():
            if col not in META_COLS:
                continue
            if row.get(col, NA).strip() == NA:
                val_clean = str(val).strip()
                if val_clean and val_clean.lower() not in PLACEHOLDER_VALS:
                    row[col] = val_clean
                    pxd_fills += 1

    total_new_fills += pxd_fills
    if pxd_fills > 0:
        print(f"         → filled {pxd_fills} new cells")

# ============================================================================
# WRITE
# ============================================================================
with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)

filled = sum(1 for r in rows for c in META_COLS if r[c] != NA)
total = len(rows) * len(META_COLS)
print(f"\n{'='*60}")
print(f"DONE — {OUTPUT_CSV}")
print(f"{'='*60}")
print(f"New cells filled by Stage 3: {total_new_fills}")
print(f"Total filled: {filled}/{total} ({100*filled/total:.1f}%)")
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%)")
