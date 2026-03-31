#!/usr/bin/env python3
"""
Stage 4: Minimal targeted fixes for bogus values and inconsistencies.

Only fixes values that are WRONG (wrong column, wrong data type) or
inconsistent (same thing spelled two ways). Does NOT normalize vocabulary
or change formatting — those changes hurt Kaggle score.

Pipeline:
  Stage 1: process_improved.py      → broad LLM extraction
  Stage 2: fetch_all_metadata.py    → API gap-fill
  Stage 3: llm_gapfill.py           → targeted LLM re-read
  Stage 4: postprocess_fixes.py     → remove bogus values, fix inconsistencies (THIS)

Usage:
    # Test (defaults):
    python postprocess_fixes.py

    # Training:
    python postprocess_fixes.py \
        --input error_analysis/predictions_gapfill.csv \
        --output error_analysis/predictions_final.csv
"""
import csv, os, sys, argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 4: Targeted bogus-value fixes")
parser.add_argument("--input", default=os.path.join(BASE_DIR, "final_submission", "submission.csv"),
                    help="Input CSV (Stage 3 output)")
parser.add_argument("--output", default=os.path.join(BASE_DIR, "final_submission", "submission_fixed.csv"),
                    help="Output CSV path")
args = parser.parse_args()

INPUT_CSV = args.input
OUTPUT_CSV = args.output
OUTPUT_DIR = os.path.dirname(OUTPUT_CSV)

os.makedirs(OUTPUT_DIR, exist_ok=True)

if not os.path.isfile(INPUT_CSV):
    print(f"ERROR: {INPUT_CSV} not found")
    sys.exit(1)

NA = "Not Applicable"

# ============================================================================
# LOAD
# ============================================================================
print(f"Loading: {INPUT_CSV}")
rows = []
with open(INPUT_CSV, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    COLUMNS = reader.fieldnames
    rows = list(reader)

META_COLS = [c for c in COLUMNS if c not in ("ID", "PXD", "Raw Data File", "Usage")]
fixes = 0

def fix(row, col, new_val, reason):
    global fixes
    old = row[col]
    row[col] = new_val
    fixes += 1
    return old

# ============================================================================
# FIX 1: ReductionReagent = "trypsin" → NA (trypsin is an enzyme)
# ============================================================================
for row in rows:
    val = row.get("Characteristics[ReductionReagent]", NA)
    if val.lower() == "trypsin":
        fix(row, "Characteristics[ReductionReagent]", NA, "trypsin is not a reductant")

# ============================================================================
# FIX 2: CellType = cell line name → NA (cell lines belong in CellLine)
# ============================================================================
CELL_LINES = {"hek293t", "hek293", "hek-293", "hela", "mrc5", "mrc-5",
              "a549", "u2os", "k562", "jurkat", "mcf7", "mcf-7",
              "hepg2", "sh-sy5y", "hl-60", "thp-1", "293t"}
for row in rows:
    val = row.get("Characteristics[CellType]", NA)
    if val.lower().replace(" ", "") in CELL_LINES or val.replace(" ", "").lower() in CELL_LINES:
        fix(row, "Characteristics[CellType]", NA, f"'{val}' is a cell line, not a cell type")

# ============================================================================
# FIX 3: Temperature = "raw" → NA (not a temperature)
# ============================================================================
for row in rows:
    val = row.get("Characteristics[Temperature]", NA)
    if val.lower() in ("raw", "room temperature", "rt", "ambient"):
        fix(row, "Characteristics[Temperature]", NA, f"'{val}' is not a specific temperature")

# ============================================================================
# FIX 4: FragmentMassTolerance — remove bogus values
#   - "15.9949 Da" = oxidation mass
#   - "445.120025 Da" = lock mass
#   - "FWHM" values = resolution, not tolerance
# ============================================================================
for row in rows:
    val = row.get("Comment[FragmentMassTolerance]", NA)
    if val == NA:
        continue
    val_lower = val.lower()
    if "fwhm" in val_lower:
        fix(row, "Comment[FragmentMassTolerance]", NA, "FWHM is resolution, not tolerance")
    elif "15.9949" in val:
        fix(row, "Comment[FragmentMassTolerance]", NA, "15.9949 is oxidation mass")
    elif "445.12" in val:
        fix(row, "Comment[FragmentMassTolerance]", NA, "445.12 is lock mass")

# ============================================================================
# FIX 5: PrecursorMassTolerance — remove FWHM values (resolution, not tolerance)
# ============================================================================
for row in rows:
    val = row.get("Comment[PrecursorMassTolerance]", NA)
    if val == NA:
        continue
    if "fwhm" in val.lower():
        fix(row, "Comment[PrecursorMassTolerance]", NA, "FWHM is resolution, not tolerance")

# ============================================================================
# FIX 6: FragmentationMethod = "IDA" → NA (IDA is acquisition method, not fragmentation)
# ============================================================================
for row in rows:
    val = row.get("Comment[FragmentationMethod]", NA)
    if val.upper() == "IDA":
        fix(row, "Comment[FragmentationMethod]", NA, "IDA is acquisition method")

# ============================================================================
# FIX 7: CellPart = cell type values → NA
#   "bone marrow-derived macrophages" is a cell type, not a subcellular part
# ============================================================================
CELL_TYPE_WORDS = {"macrophages", "macrophage", "monocytes", "neurons", "fibroblasts",
                   "lymphocytes", "neutrophils", "stem cells", "t cells", "b cells"}
for row in rows:
    val = row.get("Characteristics[CellPart]", NA)
    if val == NA:
        continue
    val_lower = val.lower()
    if any(w in val_lower for w in CELL_TYPE_WORDS):
        fix(row, "Characteristics[CellPart]", NA, f"'{val}' is a cell type, not subcellular part")

# ============================================================================
# FIX 8: AlkylationReagent = "not specified" → NA
# ============================================================================
for row in rows:
    val = row.get("Characteristics[AlkylationReagent]", NA)
    if val.lower() in ("not specified", "not available", "none", "n/a", "unknown"):
        fix(row, "Characteristics[AlkylationReagent]", NA, "placeholder, not a real value")

# ============================================================================
# FIX 9: Disease inconsistencies — normalize duplicates
# ============================================================================
DISEASE_NORMALIZE = {
    "alzheimer disease": "Alzheimer's disease",
    "alzheimer's disease": "Alzheimer's disease",
    "osteoarthritis": "Osteoarthritis",
}
for row in rows:
    val = row.get("Characteristics[Disease]", NA)
    if val == NA:
        continue
    if val.lower() in DISEASE_NORMALIZE:
        new_val = DISEASE_NORMALIZE[val.lower()]
        if new_val != val:
            fix(row, "Characteristics[Disease]", new_val, f"normalize '{val}'")

# ============================================================================
# FIX 10: ReductionReagent — "dithiothreitol" → "DTT" (same chemical)
# ============================================================================
for row in rows:
    val = row.get("Characteristics[ReductionReagent]", NA)
    if val.lower() == "dithiothreitol":
        fix(row, "Characteristics[ReductionReagent]", "DTT", "normalize to abbreviation")

# ============================================================================
# FIX 11: Organism — remove parenthetical "(human)", "(mouse)" etc.
#   "Homo sapiens (human)" → "Homo sapiens"
# ============================================================================
import re
for row in rows:
    val = row.get("Characteristics[Organism]", NA)
    if val == NA:
        continue
    cleaned = re.sub(r'\s*\(.*?\)', '', val).strip()
    if cleaned != val:
        fix(row, "Characteristics[Organism]", cleaned, f"remove parenthetical from '{val}'")

# ============================================================================
# FIX 12: FactorValue[Disease] — normalize same as Characteristics[Disease]
# ============================================================================
FACTOR_DISEASE_NORMALIZE = {
    "alzheimer disease": "Alzheimer's disease",
    "alzheimer's disease": "Alzheimer's disease",
    "osteoarthritis": "Osteoarthritis",
}
for row in rows:
    val = row.get("FactorValue[Disease]", NA)
    if val == NA:
        continue
    if val.lower() in FACTOR_DISEASE_NORMALIZE:
        new_val = FACTOR_DISEASE_NORMALIZE[val.lower()]
        if new_val != val:
            fix(row, "FactorValue[Disease]", new_val, f"normalize FactorValue disease '{val}'")

# ============================================================================
# FIX 13: ConcentrationOfCompound — normalize unit notation "uM" → "µM"
# ============================================================================
for row in rows:
    val = row.get("Characteristics[ConcentrationOfCompound]", NA)
    if val == NA:
        continue
    # Replace plain ASCII "uM" with proper "µM" (but not inside words like "uMol")
    normalized = re.sub(r'(\d)\s*uM\b', r'\1 µM', val)
    if normalized != val:
        fix(row, "Characteristics[ConcentrationOfCompound]", normalized,
            f"normalize unit '{val}' → '{normalized}'")

# ============================================================================
# FIX 14: FractionIdentifier — normalize zero-padded numbers to plain integers
#   "01" → "1", "09" → "9" (within same PXD, keep consistent)
# ============================================================================
for row in rows:
    val = row.get("Comment[FractionIdentifier]", NA)
    if val == NA:
        continue
    # Only strip leading zeros from purely numeric values like "01", "09"
    if val.isdigit() and val.startswith("0") and len(val) > 1:
        fix(row, "Comment[FractionIdentifier]", str(int(val)),
            f"remove zero-padding from fraction '{val}'")

# ============================================================================
# FIX 15: Placeholder → NA for multiple columns
#   "not available", "unknown", "not specified" are not real values
# ============================================================================
PLACEHOLDER_COLS = [
    "Characteristics[Age]",
    "Characteristics[Sex]",
    "Characteristics[ReductionReagent]",
    "Characteristics[Modification].1",
    "Characteristics[Modification].2",
    "Characteristics[Modification].3",
    "Characteristics[Modification].4",
    "Comment[GradientTime]",
    "Comment[CollisionEnergy]",
    "Comment[NumberOfMissedCleavages]",
]
PLACEHOLDER_VALS = {"not available", "not specified", "unknown", "none", "n/a", "na"}

for row in rows:
    for col in PLACEHOLDER_COLS:
        val = row.get(col, NA)
        if val == NA:
            continue
        if val.lower().strip() in PLACEHOLDER_VALS:
            fix(row, col, NA, f"placeholder '{val}' → NA in {col}")

# ============================================================================
# FIX 16: CellType — normalize "epithelial" → "epithelial cell"
# ============================================================================
for row in rows:
    val = row.get("Characteristics[CellType]", NA)
    if val.strip().lower() == "epithelial":
        fix(row, "Characteristics[CellType]", "epithelial cell",
            "normalize 'epithelial' → 'epithelial cell'")

# ============================================================================
# FIX 17: OrganismPart — normalize capitalization to lowercase
#   "Cell culture" → "cell culture", "Bone marrow" → "bone marrow", etc.
# ============================================================================
ORGANISM_PART_NORMALIZE = {
    "cell culture": "cell culture",
    "bone marrow": "bone marrow",
    "epithelial cell": "epithelial cell",
    "malignant cell": "malignant cell",
}
for row in rows:
    val = row.get("Characteristics[OrganismPart]", NA)
    if val == NA:
        continue
    lowered = val.lower()
    if lowered in ORGANISM_PART_NORMALIZE and val != ORGANISM_PART_NORMALIZE[lowered]:
        fix(row, "Characteristics[OrganismPart]", ORGANISM_PART_NORMALIZE[lowered],
            f"normalize casing '{val}'")

# ============================================================================
# FIX 18: Global placeholder strings → NA across ALL columns
#   "not available", "not specified" etc. score zero — must be Not Applicable
# ============================================================================
GLOBAL_PLACEHOLDERS = {"not available", "not specified", "unknown", "none", "n/a", "na",
                       "not determined", "missing", "unspecified", "nd"}
for row in rows:
    for col in META_COLS:
        val = row.get(col, NA)
        if val != NA and val.lower().strip() in GLOBAL_PLACEHOLDERS:
            fix(row, col, NA, f"placeholder '{val}' → NA in {col}")

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
print(f"Fixes applied: {fixes}")
print(f"Total filled:  {filled}/{total} ({100*filled/total:.1f}%)")
print()
for pxd in sorted(set(r['PXD'] for r in rows)):
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%)")
