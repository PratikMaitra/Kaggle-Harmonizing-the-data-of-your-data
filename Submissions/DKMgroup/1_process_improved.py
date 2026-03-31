#!/usr/bin/env python3
"""
SDRF extraction v2 — improved prompt that fixes baseline gaps.
Uses the baseline's category definitions but relaxes verbatim-only constraint,
explicitly asks for Modifications, Treatments, Compounds, counts, etc.

Usage:
    # Test (defaults):
    export ANTHROPIC_API_KEY="sk-ant-..."
    python process_improved.py

    # Training:
    python process_improved.py \
        --pubtext "Train PubText/Train PubText/PubText.json" \
        --scaffold TrainSampleSubmission.csv \
        --output error_analysis/predictions.csv \
        --cache-dir cache_improved_train
"""
import json, csv, os, re, sys, time
import anthropic

# ============================================================================
# PATHS — argparse with defaults for test submission
# ============================================================================
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 1: LLM extraction of SDRF metadata")
parser.add_argument("--pubtext", default=os.path.join(BASE_DIR, "Test PubText", "Test PubText", "PubText.json"),
                    help="Path to PubText.json")
parser.add_argument("--scaffold", default=os.path.join(BASE_DIR, "SampleSubmission.csv"),
                    help="Path to SampleSubmission.csv (scaffold with ID, PXD, Raw Data File)")
parser.add_argument("--output", default=os.path.join(BASE_DIR, "improved_claude", "submission.csv"),
                    help="Output CSV path")
parser.add_argument("--cache-dir", default=os.path.join(BASE_DIR, "cache_improved"),
                    help="Cache directory for LLM responses")
args = parser.parse_args()

PUBTEXT_PATH = args.pubtext
SAMPLE_SUB_PATH = args.scaffold
OUTPUT_PATH = args.output
OUTPUT_DIR = os.path.dirname(OUTPUT_PATH)
CACHE_DIR = args.cache_dir

#MODEL = "claude-sonnet-4-20250514"
MODEL = "claude-opus-4-6"
MAX_TOKENS = 128000

# ============================================================================
# VERIFY INPUTS
# ============================================================================
for path, name in [
    (PUBTEXT_PATH, "PubText.json"),
    (SAMPLE_SUB_PATH, "SampleSubmission.csv"),
]:
    if not os.path.isfile(path):
        print(f"ERROR: {name} not found at: {path}")
        sys.exit(1)

### PLEASE USE CLAUDE API KEY FOR REPLICATING OUR PIPELINE SUBMISSION ###
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
#API_KEY=""
if not API_KEY:
    print('ERROR: export ANTHROPIC_API_KEY="sk-ant-..." first')
    sys.exit(1)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================================
# SUBMISSION COLUMNS
# ============================================================================
SUBMISSION_COLUMNS = [
    "ID", "PXD", "Raw Data File",
    "Characteristics[Age]", "Characteristics[AlkylationReagent]",
    "Characteristics[AnatomicSiteTumor]", "Characteristics[AncestryCategory]",
    "Characteristics[BMI]", "Characteristics[Bait]",
    "Characteristics[BiologicalReplicate]", "Characteristics[CellLine]",
    "Characteristics[CellPart]", "Characteristics[CellType]",
    "Characteristics[CleavageAgent]", "Characteristics[Compound]",
    "Characteristics[ConcentrationOfCompound]", "Characteristics[Depletion]",
    "Characteristics[DevelopmentalStage]", "Characteristics[DiseaseTreatment]",
    "Characteristics[Disease]", "Characteristics[GeneticModification]",
    "Characteristics[Genotype]", "Characteristics[GrowthRate]",
    "Characteristics[Label]", "Characteristics[MaterialType]",
    "Characteristics[Modification]", "Characteristics[Modification].1",
    "Characteristics[Modification].2", "Characteristics[Modification].3",
    "Characteristics[Modification].4", "Characteristics[Modification].5",
    "Characteristics[Modification].6",
    "Characteristics[NumberOfBiologicalReplicates]",
    "Characteristics[NumberOfSamples]",
    "Characteristics[NumberOfTechnicalReplicates]",
    "Characteristics[OrganismPart]", "Characteristics[Organism]",
    "Characteristics[OriginSiteDisease]", "Characteristics[PooledSample]",
    "Characteristics[ReductionReagent]", "Characteristics[SamplingTime]",
    "Characteristics[Sex]", "Characteristics[Specimen]",
    "Characteristics[SpikedCompound]", "Characteristics[Staining]",
    "Characteristics[Strain]", "Characteristics[SyntheticPeptide]",
    "Characteristics[Temperature]", "Characteristics[Time]",
    "Characteristics[Treatment]", "Characteristics[TumorCellularity]",
    "Characteristics[TumorGrade]", "Characteristics[TumorSite]",
    "Characteristics[TumorSize]", "Characteristics[TumorStage]",
    "Comment[AcquisitionMethod]", "Comment[CollisionEnergy]",
    "Comment[EnrichmentMethod]", "Comment[FlowRateChromatogram]",
    "Comment[FractionIdentifier]", "Comment[FractionationMethod]",
    "Comment[FragmentationMethod]", "Comment[FragmentMassTolerance]",
    "Comment[GradientTime]", "Comment[Instrument]",
    "Comment[IonizationType]", "Comment[MS2MassAnalyzer]",
    "Comment[NumberOfFractions]", "Comment[NumberOfMissedCleavages]",
    "Comment[PrecursorMassTolerance]", "Comment[Separation]",
    "FactorValue[Bait]", "FactorValue[CellPart]",
    "FactorValue[Compound]", "FactorValue[ConcentrationOfCompound].1",
    "FactorValue[Disease]", "FactorValue[FractionIdentifier]",
    "FactorValue[GeneticModification]", "FactorValue[Temperature]",
    "FactorValue[Treatment]", "Usage"
]
META_COLS = [c for c in SUBMISSION_COLUMNS if c not in ("ID", "PXD", "Raw Data File", "Usage")]
NA = "Not Applicable"

# ============================================================================
# LOAD DATA
# ============================================================================
print("Loading data...")
with open(PUBTEXT_PATH) as f:
    pubtext = json.load(f)

scaffold = []
scaffold_by_pxd = {}
with open(SAMPLE_SUB_PATH, newline='', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        r = {
            'ID': row['ID'].strip(), 'PXD': row['PXD'].strip(),
            'Raw Data File': row['Raw Data File'].strip(),
            'Usage': row['Usage'].strip()
        }
        scaffold.append(r)
        if r['PXD'] not in scaffold_by_pxd:
            scaffold_by_pxd[r['PXD']] = []
        scaffold_by_pxd[r['PXD']].append(r)

print(f"  {len(pubtext)} PXDs, {len(scaffold)} rows")

# ============================================================================
# THE IMPROVED PROMPT
# ============================================================================
IMPROVED_PROMPT = r"""<task>
Extract SDRF-Proteomics metadata from a scientific manuscript and a list of .raw filenames.
Return a single JSON object. No commentary, no explanation, no markdown fences.
</task>

<critical_behavior>
You MUST fill as many columns as possible. Being conservative and leaving things as NA is a FAILURE.
If you can reasonably infer a value from the text, FILL IT. Only omit if truly inapplicable.

Specific MANDATORY fills:
- Characteristics[Organism]: ALWAYS. Just the scientific name: "Homo sapiens", "Mus musculus". NEVER add parentheticals like "(human)".
- Characteristics[Label]: ALWAYS. "label free" if no labeling described.
- Characteristics[MaterialType]: ALWAYS. "cell line" if cell lines are used, "tissue" if tissue, "cells" for primary cells, "biofluid" for serum/plasma/urine.
- Characteristics[Disease]: ALWAYS. "normal" if no disease context.
- Comment[Instrument]: ALWAYS extract from methods. Use the SHORT commercial name: "Exploris 480" not "Orbitrap Exploris 480", "Q Exactive" not "Thermo Q Exactive".
- Comment[AcquisitionMethod]: ALWAYS. "DDA", "DIA", "PRM", "SWATH", "MSE".
- Comment[MS2MassAnalyzer]: ALWAYS infer from the instrument. Orbitrap instruments → "orbitrap". TripleTOF/QTOF → "TOF". LTQ/Velos (ion trap MS2) → "ion trap". Astral detector → "Astral".
- Characteristics[Temperature]: If cell lines are cultured, use "37°C". Only omit for non-cell-culture studies where temperature is not mentioned.
- Characteristics[NumberOfSamples]: Count total raw files or samples described. e.g. if there are 80 raw files, put "80".
- Comment[Separation]: ALWAYS fill if LC-MS is used. "C18" or "reversed-phase C18" for standard RP columns.
- Comment[FragmentationMethod]: ALWAYS. "HCD", "CID", "ETD", "EtHCD".
- Characteristics[CleavageAgent]: If both trypsin and Lys-C are used together, write "trypsin/Lys-C" (not just "trypsin").
- Characteristics[Modification]: List variable modifications FIRST (Oxidation), then fixed (Carbamidomethyl). Do NOT put labeling reagents (TMT/TMTpro) as the first modification — put them after Oxidation and Carbamidomethyl.
</critical_behavior>

<value_format>
Every value is a SHORT string (1-5 words). No sentences. No parentheticals. No elaboration.

WRONG: "Homo sapiens (human)" → RIGHT: "Homo sapiens"
WRONG: "Orbitrap Exploris 480" → RIGHT: "Exploris 480"  
WRONG: "Higher-energy collisional dissociation" → RIGHT: "HCD"
WRONG: "FLAG immunoprecipitation" → RIGHT: "immunoprecipitation"
WRONG: "collision-induced dissociation at 35%" → RIGHT: "CID"
WRONG: "trypsin (Promega)" → RIGHT: "trypsin"
</value_format>

<categories>
--- Characteristics (sample-level) ---
Characteristics[Age]: e.g. "45 years", "8 weeks old"
Characteristics[AlkylationReagent]: e.g. "iodoacetamide", "chloroacetamide"
Characteristics[AnatomicSiteTumor]: Tumor location
Characteristics[AncestryCategory]: Donor ethnicity
Characteristics[BMI]: e.g. "25.3"
Characteristics[Bait]: Bait protein in AP-MS. e.g. "SRGN", "GFP", "nsp3.1". Parse from filenames if bait names appear there.
Characteristics[BiologicalReplicate]: Plain number: "1", "2", "3"
Characteristics[CellLine]: e.g. "HEK293T", "HeLa", "MRC5"
Characteristics[CellPart]: Subcellular fraction ONLY: "nucleus", "exosomes", "microvesicles". NOT cell types. NOT cell lines.
Characteristics[CellType]: Cell type: "fibroblasts", "macrophages". NOT cell line names like "HEK293T".
Characteristics[CleavageAgent]: e.g. "trypsin", "trypsin/Lys-C", "pepsin"
Characteristics[Compound]: Drug/chemical. e.g. "CBK77", "cidofovir"
Characteristics[ConcentrationOfCompound]: e.g. "25 µM"
Characteristics[Depletion]: Depletion method
Characteristics[DevelopmentalStage]: e.g. "adult", "embryonic"
Characteristics[Disease]: e.g. "Alzheimer's disease", "normal"
Characteristics[DiseaseTreatment]: Treatment of disease
Characteristics[GeneticModification]: e.g. "Tmem9 knockout", "PRNP F198S"
Characteristics[Genotype]: e.g. "wild type", "C57BL/6J"
Characteristics[GrowthRate]: Growth rate
Characteristics[Label]: e.g. "label free", "TMT", "TMTpro 16plex", "SILAC"
Characteristics[MaterialType]: e.g. "tissue", "cell line", "biofluid", "cells"
Characteristics[Modification]: Database search modifications. .1, .2, etc. for multiple.
  Order: Oxidation first, then Carbamidomethyl, then others. NOT TMT/TMTpro first.
  Characteristics[Modification] = "Oxidation"
  Characteristics[Modification].1 = "Carbamidomethyl"
  Characteristics[Modification].2 = "Acetyl" (etc.)
Characteristics[NumberOfBiologicalReplicates]: e.g. "3"
Characteristics[NumberOfSamples]: Total number of samples or raw files. e.g. "80", "24"
Characteristics[NumberOfTechnicalReplicates]: e.g. "2"
Characteristics[Organism]: Scientific name ONLY. "Homo sapiens", "Mus musculus". No parentheticals.
Characteristics[OrganismPart]: e.g. "brain", "liver", "serum"
Characteristics[OriginSiteDisease]: Anatomical site of disease
Characteristics[PooledSample]: e.g. "pooled"
Characteristics[ReductionReagent]: Reducing agent: "DTT", "TCEP". NOT enzymes.
Characteristics[SamplingTime]: Collection timepoint
Characteristics[Sex]: e.g. "male", "female"
Characteristics[Specimen]: e.g. "postmortem brain", "milk serum"
Characteristics[SpikedCompound]: e.g. "iRT peptides"
Characteristics[Staining]: Staining before MS
Characteristics[Strain]: e.g. "Sprague-Dawley", "C57BL/6J"
Characteristics[SyntheticPeptide]: e.g. "yes"
Characteristics[Temperature]: e.g. "37°C". Cell culture → "37°C". NOT "raw", "heated", or conditions.
Characteristics[Time]: e.g. "0sec", "5sec", "30sec"
Characteristics[Treatment]: e.g. "DMSO", "CBK77 25 µM"

--- Comment (technical) ---
Comment[AcquisitionMethod]: "DDA", "DIA", "PRM", "SWATH", "MSE"
Comment[CollisionEnergy]: As stated: "32", "35%", "27 eV"
Comment[EnrichmentMethod]: Use generic term: "immunoprecipitation" (not "FLAG IP" or "anti-FLAG IP")
Comment[FlowRateChromatogram]: e.g. "300 nL/min", "500 nL/min"
Comment[FractionIdentifier]: From filename. Keep as-is. e.g. "0C", "20C", "1", "F01"
Comment[FractionationMethod]: e.g. "SCX", "high-pH RP", "SDS-PAGE", "MudPIT"
Comment[FragmentationMethod]: Abbreviation: "HCD", "CID", "ETD", "EtHCD"
Comment[FragmentMassTolerance]: e.g. "0.02 Da", "0.5 Da". NOT lock masses, NOT modification masses, NOT FWHM.
Comment[GradientTime]: e.g. "120 min", "25 min"
Comment[Instrument]: SHORT commercial name. "Q Exactive HF", "Exploris 480", "Orbitrap Astral", "TripleTOF 5600+"
Comment[IonizationType]: "nanoESI" or "ESI" (nanoESI if nano-LC is used, ESI otherwise)
Comment[MS2MassAnalyzer]: Infer from instrument. "orbitrap", "ion trap", "TOF", "Astral"
Comment[NumberOfFractions]: Total fractions. e.g. "8". If filenames show fraction pattern, count them.
Comment[NumberOfMissedCleavages]: e.g. "2"
Comment[PrecursorMassTolerance]: e.g. "10 ppm", "20 ppm". NOT FWHM resolution values.
Comment[Separation]: "C18", "reversed-phase C18", "FAIMS"

--- FactorValue (ONLY when that variable is compared across samples) ---
FactorValue[Bait]: If different baits are compared
FactorValue[CellPart]: If different compartments are compared
FactorValue[Compound]: If drug vs control is compared
FactorValue[ConcentrationOfCompound].1: If doses are compared
FactorValue[Disease]: If disease states are compared
FactorValue[FractionIdentifier]: If fractions are the study variable
FactorValue[GeneticModification]: If genotypes (WT vs KO) are compared
FactorValue[Temperature]: If temperatures are compared
FactorValue[Treatment]: If treatments are compared

When you set a FactorValue, ALSO set the matching Characteristics column.
</categories>

<filename_parsing>
IMPORTANT: Parse EVERY .raw filename systematically. Filenames encode per-file metadata.

Common patterns:
- Bait/target protein: if filenames contain protein names (nsp3, GFP, SRGN, T9A, T9B), extract as Characteristics[Bait] and FactorValue[Bait]
- Replicate markers: rep1, BR1, _1/_2/_3, _R1/_R2 → BiologicalReplicate (plain number)
- Fraction IDs: F1, frac01, 0C, 20C, 40C, 90C10B → Comment[FractionIdentifier] (keep as-is)
- Timepoints: 0sec, 5sec, 30sec, 24h → Characteristics[Time]
- Conditions: WT, KO, CTRL, DMSO → Treatment or GeneticModification + FactorValue
- Cell lines: HeLa, DU145 → Characteristics[CellLine]
- EV types: Exo, MV → Characteristics[CellPart]

If filenames show a repeating pattern with numbered suffixes, those are likely fractions.
Count distinct fraction IDs → Comment[NumberOfFractions].
</filename_parsing>

<rules>
1. FILL as many columns as possible. Being conservative is worse than being slightly wrong.
2. Characteristics[Organism]: scientific name only, no parentheticals.
3. Comment[Instrument]: short commercial name, no manufacturer prefix.
4. Characteristics[Modification]: Oxidation first, then Carbamidomethyl, then others. Never TMT/label first.
5. Comment[EnrichmentMethod]: use generic "immunoprecipitation" not specific "FLAG IP".
6. CellType ≠ CellLine. CellPart = subcellular fraction only.
7. FragmentMassTolerance = mass tolerance only. PrecursorMassTolerance = mass tolerance only.
8. ReductionReagent = reducing agent only (DTT, TCEP), not enzymes.
9. Parse filenames aggressively for bait, fraction, replicate, and condition info.
</rules>

<output_format>
Single JSON object. "_GLOBAL_" for shared metadata. Per-file keys only for values that differ.
Each value: a SHORT string. Not a list, not a sentence, no parentheticals.

{
  "_GLOBAL_": {
    "Characteristics[Organism]": "Homo sapiens",
    "Characteristics[Disease]": "normal",
    "Characteristics[MaterialType]": "cell line",
    "Characteristics[CellLine]": "HEK293T",
    "Characteristics[CleavageAgent]": "trypsin/Lys-C",
    "Characteristics[Label]": "TMTpro 16plex",
    "Characteristics[Temperature]": "37°C",
    "Characteristics[Modification]": "Oxidation",
    "Characteristics[Modification].1": "Carbamidomethyl",
    "Characteristics[Modification].2": "Acetyl",
    "Characteristics[NumberOfSamples]": "80",
    "Comment[Instrument]": "Exploris 480",
    "Comment[AcquisitionMethod]": "DDA",
    "Comment[FragmentationMethod]": "HCD",
    "Comment[MS2MassAnalyzer]": "orbitrap",
    "Comment[Separation]": "C18",
    "Comment[CollisionEnergy]": "32",
    "Comment[EnrichmentMethod]": "immunoprecipitation",
    "Comment[IonizationType]": "nanoESI",
    "Comment[NumberOfFractions]": "8",
    "Comment[NumberOfMissedCleavages]": "2",
    "Comment[PrecursorMassTolerance]": "20 ppm",
    "Comment[FragmentMassTolerance]": "0.02 Da",
    "Comment[FlowRateChromatogram]": "500 nL/min",
    "Comment[GradientTime]": "130 min",
    "Characteristics[ReductionReagent]": "TCEP",
    "Characteristics[AlkylationReagent]": "chloroacetamide"
  },
  "nsp3.1_rep1_0C.raw": {
    "Characteristics[Bait]": "nsp3.1",
    "FactorValue[Bait]": "nsp3.1",
    "Characteristics[BiologicalReplicate]": "1",
    "Comment[FractionIdentifier]": "0C"
  },
  "GFP_rep1_0C.raw": {
    "Characteristics[Bait]": "GFP",
    "FactorValue[Bait]": "GFP",
    "Characteristics[BiologicalReplicate]": "1",
    "Comment[FractionIdentifier]": "0C"
  }
}
</output_format>"""


def build_prompt(title, abstract, methods, raw_files):
    manuscript = f"TITLE:\n{title}\n\nABSTRACT:\n{abstract}\n\nMETHODS:\n{methods}"
    if len(manuscript) > 60000:
        manuscript = f"TITLE:\n{title}\n\nABSTRACT:\n{abstract}\n\nMETHODS:\n{methods[:40000]}\n[...truncated...]"
    files_block = "\n".join(raw_files)

    return f"""{IMPROVED_PROMPT}

=== MANUSCRIPT_TEXT ===
{manuscript}

=== RAW_FILES ===
{files_block}"""

# ============================================================================
# API + PARSING
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

    glob = normalize(data.pop("_GLOBAL_", {}))
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
        merged.update(normalize(per_file))
        result[f] = merged
    return result


def normalize(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            if k == "Characteristics[Modification]" and len(v) > 1:
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
# PROCESS ALL PXDs
# ============================================================================
all_results = {}

for i, pxd_id in enumerate(sorted(pubtext.keys())):
    paper = pubtext[pxd_id]
    sub_files = sorted(set(r['Raw Data File'] for r in scaffold_by_pxd.get(pxd_id, [])))

    cache_file = os.path.join(CACHE_DIR, f"{pxd_id}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            all_results[pxd_id] = json.load(f)
        n = sum(len(v) for v in all_results[pxd_id].values())
        print(f"[{i+1}/15] {pxd_id}: cached ({n} fields)")
        continue

    prompt = build_prompt(
        paper.get('TITLE', ''),
        paper.get('ABSTRACT', ''),
        paper.get('METHODS', ''),
        sub_files
    )
    print(f"[{i+1}/15] {pxd_id}: {len(sub_files)} files, prompt {len(prompt)//1000}K chars — calling API...")

    text = call_api(prompt)
    if text:
        parsed = parse_response(text, sub_files)
        all_results[pxd_id] = parsed
        with open(cache_file, 'w') as f:
            json.dump(parsed, f, indent=2)
        n = sum(len(v) for v in parsed.values())
        print(f"         extracted {n} fields across {len(parsed)} files")
    else:
        all_results[pxd_id] = {}
        print(f"         FAILED — empty response")

    time.sleep(2)

# ============================================================================
# BUILD submission.csv
# ============================================================================
print("\nBuilding submission.csv...")

output_rows = []
for row in scaffold:
    pxd, raw_file = row['PXD'], row['Raw Data File']
    out = {col: NA for col in SUBMISSION_COLUMNS}
    out['ID'] = row['ID']
    out['PXD'] = pxd
    out['Raw Data File'] = raw_file
    out['Usage'] = row['Usage']

    file_meta = {}
    if pxd in all_results:
        pd = all_results[pxd]
        if raw_file in pd:
            file_meta = pd[raw_file]
        else:
            for k in pd:
                if k.lower() == raw_file.lower():
                    file_meta = pd[k]
                    break

    for col in META_COLS:
        if col in file_meta:
            v = str(file_meta[col]).strip()
            if v and v.lower() not in ("", "none", "null", "n/a", "na", "not applicable"):
                out[col] = v

    output_rows.append(out)

with open(OUTPUT_PATH, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=SUBMISSION_COLUMNS)
    writer.writeheader()
    writer.writerows(output_rows)

# ============================================================================
# STATS
# ============================================================================
filled = sum(1 for r in output_rows for c in META_COLS if r[c] != NA)
total = len(output_rows) * len(META_COLS)
print(f"\n{'='*60}")
print(f"DONE — {OUTPUT_PATH}")
print(f"{'='*60}")
print(f"Rows: {len(output_rows)}  |  Filled: {filled}/{total} ({100*filled/total:.1f}%)")
print()
for pxd in sorted(set(r['PXD'] for r in output_rows)):
    pr = [r for r in output_rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%)")
