#!/usr/bin/env python3
"""
Stage 4: Postprocessing — cleanup + fill rate booster (merged).

Part A: Remove/fix bad values (wrong column, placeholders, inconsistencies)
Part B: Fill missing values using general proteomics knowledge

All rules are paper-agnostic and work on any dataset.

Usage:
    python 4_postprocess_fixes.py --input stage3.csv --output final.csv
"""
import csv, os, sys, re, json, argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 4: Postprocess + fill boost")
parser.add_argument("--input", default=os.path.join(BASE_DIR, "final_submission", "submission.csv"))
parser.add_argument("--output", default=os.path.join(BASE_DIR, "final_submission", "submission_fixed.csv"))
parser.add_argument("--pubtext", default=None, help="Path to PubText.json (optional, enables text-mining rules)")
args = parser.parse_args()

if not os.path.isfile(args.input):
    print(f"ERROR: {args.input} not found"); sys.exit(1)
os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)

NA = "Not Applicable"

print(f"Loading: {args.input}")
with open(args.input, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    COLUMNS = reader.fieldnames
    rows = list(reader)

META_COLS = [c for c in COLUMNS if c not in ("ID", "PXD", "Raw Data File", "Usage")]
fixes = 0

def fix(row, col, val):
    global fixes
    if row.get(col, NA) != val:
        row[col] = val
        fixes += 1

def fill_if_na(row, col, val):
    global fixes
    if col in COLUMNS and row.get(col, NA) == NA:
        row[col] = val
        fixes += 1

pxd_ids = sorted(set(r['PXD'] for r in rows))

# Load PubText if available (for text-mining rules in Part C)
pubtext = {}
if args.pubtext and os.path.isfile(args.pubtext):
    with open(args.pubtext) as f:
        pubtext = json.load(f)
    print(f"PubText loaded: {len(pubtext)} papers")
else:
    # Auto-detect
    for candidate in [
        os.path.join(BASE_DIR, "TestPubText", "PubText.json"),
        os.path.join(BASE_DIR, "Test PubText", "Test PubText", "PubText.json"),
        os.path.join(BASE_DIR, "PubText.json"),
    ]:
        if os.path.isfile(candidate):
            with open(candidate) as f:
                pubtext = json.load(f)
            print(f"PubText auto-detected: {candidate} ({len(pubtext)} papers)")
            break
    if not pubtext:
        print("PubText not found — text-mining rules (Part C) will be skipped")

# ========================================================================
# PART A: CLEANUP — remove/fix bad values
# ========================================================================

# A1: "Text Span" scaffold leftovers → NA
for row in rows:
    for col in META_COLS:
        if row.get(col, "") == "Text Span":
            fix(row, col, NA)

# A2: Global placeholders → NA
PLACEHOLDERS = {
    "not available", "not specified", "unknown", "none", "n/a", "na",
    "not determined", "missing", "unspecified", "nd", "not applicable",
    "not reported", "not provided", "not mentioned", "not described",
    "not stated", "not given", "not indicated", "not shown",
    "no data", "no information", "unavailable",
}
for row in rows:
    for col in META_COLS:
        val = row.get(col, NA)
        if val != NA and val.lower().strip() in PLACEHOLDERS:
            fix(row, col, NA)

# A3: Label normalization — "label free sample" → "label free"
LABEL_MAP = {
    "label free sample": "label free", "label-free sample": "label free",
    "label-free": "label free", "label free quantification": "label free",
    "lfq": "label free", "lf": "label free",
}
for row in rows:
    val = row.get("Characteristics[Label]", NA)
    if val != NA:
        mapped = LABEL_MAP.get(val.lower().strip())
        if mapped:
            fix(row, "Characteristics[Label]", mapped)

# A4: ReductionReagent = enzyme → NA
for row in rows:
    val = row.get("Characteristics[ReductionReagent]", NA)
    if val != NA and val.lower() in ("trypsin", "trypsin/lys-c", "lys-c", "pepsin", "chymotrypsin"):
        fix(row, "Characteristics[ReductionReagent]", NA)

# A5: CellType = cell line name → NA
CELL_LINES = {
    "hek293t", "hek293", "hela", "mrc5", "a549", "u2os", "k562",
    "jurkat", "mcf7", "hepg2", "shsy5y", "hl60", "thp1", "293t",
    "mdamb231", "meljuso", "du145", "pc3", "lncap", "huvec", "raw264.7",
}
for row in rows:
    val = row.get("Characteristics[CellType]", NA)
    if val != NA and val.lower().replace(" ", "").replace("-", "") in CELL_LINES:
        fix(row, "Characteristics[CellType]", NA)

# A6: CellPart = cell type → NA
CELL_TYPE_WORDS = {"macrophages", "macrophage", "monocytes", "neurons", "fibroblasts",
                   "lymphocytes", "neutrophils", "stem cells", "t cells", "b cells",
                   "hepatocytes", "astrocytes", "endothelial cells"}
for row in rows:
    val = row.get("Characteristics[CellPart]", NA)
    if val != NA and any(w in val.lower() for w in CELL_TYPE_WORDS):
        fix(row, "Characteristics[CellPart]", NA)

# A7: FragmentMassTolerance — remove bogus values
for row in rows:
    val = row.get("Comment[FragmentMassTolerance]", NA)
    if val != NA and ("fwhm" in val.lower() or "15.9949" in val or "445.12" in val):
        fix(row, "Comment[FragmentMassTolerance]", NA)

# A8: PrecursorMassTolerance — remove FWHM
for row in rows:
    val = row.get("Comment[PrecursorMassTolerance]", NA)
    if val != NA and "fwhm" in val.lower():
        fix(row, "Comment[PrecursorMassTolerance]", NA)

# A9: FragmentationMethod = acquisition method → NA
for row in rows:
    val = row.get("Comment[FragmentationMethod]", NA)
    if val != NA and val.upper() in ("IDA", "DDA", "DIA", "PRM", "SWATH", "MSE"):
        fix(row, "Comment[FragmentationMethod]", NA)

# A10: Temperature = non-temperature → NA
for row in rows:
    val = row.get("Characteristics[Temperature]", NA)
    if val != NA and val.lower() in ("raw", "room temperature", "rt", "ambient", "heated", "cooled"):
        fix(row, "Characteristics[Temperature]", NA)

# A11: CollisionEnergy = voltage → NA
for row in rows:
    val = row.get("Comment[CollisionEnergy]", NA)
    if val != NA and ("kv" in val.lower() or "kV" in val):
        fix(row, "Comment[CollisionEnergy]", NA)

# A12: Modification = "No PTMs" → NA
for row in rows:
    for col in META_COLS:
        if not col.startswith("Characteristics[Modification]"): continue
        val = row.get(col, NA)
        if val != NA and ("no ptm" in val.lower() or "not included" in val.lower() or val.lower() == "none"):
            fix(row, col, NA)

# A13: BiologicalReplicate = timestamps → NA
for row in rows:
    val = row.get("Characteristics[BiologicalReplicate]", NA)
    if val != NA:
        try:
            if int(val) > 200:
                fix(row, "Characteristics[BiologicalReplicate]", NA)
        except ValueError:
            pass

# A14: ReductionReagent normalization
RED_MAP = {"dithiothreitol": "DTT", "dithiothreitol (dtt)": "DTT",
           "tris(2-carboxyethyl)phosphine": "TCEP", "tcep-hcl": "TCEP"}
for row in rows:
    val = row.get("Characteristics[ReductionReagent]", NA)
    if val != NA and val.lower().strip() in RED_MAP:
        fix(row, "Characteristics[ReductionReagent]", RED_MAP[val.lower().strip()])

# A15: AlkylationReagent normalization
ALK_MAP = {"iaa": "iodoacetamide", "caa": "chloroacetamide", "cam": "chloroacetamide",
           "iodoacetamide (iaa)": "iodoacetamide", "chloroacetamide (caa)": "chloroacetamide"}
for row in rows:
    val = row.get("Characteristics[AlkylationReagent]", NA)
    if val != NA and val.lower().strip() in ALK_MAP:
        fix(row, "Characteristics[AlkylationReagent]", ALK_MAP[val.lower().strip()])

# A16: Organism — remove parentheticals
for row in rows:
    val = row.get("Characteristics[Organism]", NA)
    if val != NA:
        cleaned = re.sub(r'\s*\(.*?\)', '', val).strip()
        if cleaned and cleaned != val:
            fix(row, "Characteristics[Organism]", cleaned)

# A17: CollisionEnergy — strip units
for row in rows:
    val = row.get("Comment[CollisionEnergy]", NA)
    if val != NA:
        cleaned = re.sub(r'\s*(%|NCE|nce|eV|ev|HCD|hcd)\s*$', '', val).strip()
        cleaned = re.sub(r'\s*normalized\s*$', '', cleaned, flags=re.IGNORECASE).strip()
        if cleaned != val:
            fix(row, "Comment[CollisionEnergy]", cleaned)

# A18: FractionIdentifier — strip zero-padding
for row in rows:
    val = row.get("Comment[FractionIdentifier]", NA)
    if val != NA and val.isdigit() and val.startswith("0") and len(val) > 1:
        fix(row, "Comment[FractionIdentifier]", str(int(val)))

# A19: Deamidation → Deamidated
for row in rows:
    for col in META_COLS:
        if col.startswith("Characteristics[Modification]"):
            val = row.get(col, NA)
            if val != NA and val.lower() == "deamidation":
                fix(row, col, "Deamidated")

# A20: Instrument normalization
INST_MAP = {
    "exploris 480": "Orbitrap Exploris 480", "orbitrap exploris 480": "Orbitrap Exploris 480",
    "thermo exploris 480": "Orbitrap Exploris 480",
    "fusion lumos": "Orbitrap Fusion Lumos", "orbitrap fusion lumos tribrid": "Orbitrap Fusion Lumos",
    "ltq orbitrap xl": "LTQ-Orbitrap XL", "ltq-orbitrap xl": "LTQ-Orbitrap XL",
    "tripletof 5600": "TripleTOF 5600+",
    "q exactive hf-x": "Q Exactive HF-X", "q-exactive hf-x": "Q Exactive HF-X",
    "orbitrap astral": "Orbitrap Astral", "thermo orbitrap astral": "Orbitrap Astral",
    "waters synapt xs": "Synapt XS", "zenotof 7600": "Zeno TOF 7600",
    "ab sciex zenotof 7600": "Zeno TOF 7600",
}
for row in rows:
    val = row.get("Comment[Instrument]", NA)
    if val != NA and val.lower().strip() in INST_MAP:
        fix(row, "Comment[Instrument]", INST_MAP[val.lower().strip()])

# A21: CellType "epithelial" → "epithelial cell"
for row in rows:
    val = row.get("Characteristics[CellType]", NA)
    if val != NA and val.strip().lower() == "epithelial":
        fix(row, "Characteristics[CellType]", "epithelial cell")

# A22: ConcentrationOfCompound "uM" → "µM"
for row in rows:
    val = row.get("Characteristics[ConcentrationOfCompound]", NA)
    if val != NA:
        normed = re.sub(r'(\d)\s*uM\b', r'\1 µM', val)
        if normed != val:
            fix(row, "Characteristics[ConcentrationOfCompound]", normed)

# A23: Disease normalization
DIS_MAP = {"alzheimer disease": "Alzheimer's disease", "alzheimers disease": "Alzheimer's disease",
           "parkinson disease": "Parkinson's disease", "huntington disease": "Huntington's disease"}
for col in ["Characteristics[Disease]", "FactorValue[Disease]"]:
    for row in rows:
        val = row.get(col, NA)
        if val != NA and val.lower() in DIS_MAP and val != DIS_MAP[val.lower()]:
            fix(row, col, DIS_MAP[val.lower()])

# A24: Single-value FactorValues → NA
factor_cols = [c for c in META_COLS if c.startswith("FactorValue[")]
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    for col in factor_cols:
        vals = set(r.get(col, NA) for r in pr) - {NA}
        if len(vals) == 1:
            for r in pr:
                if r.get(col, NA) != NA:
                    fix(r, col, NA)

# A25: FactorValue[FractionIdentifier] mirroring Comment → NA
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    fv = set(r.get("FactorValue[FractionIdentifier]", NA) for r in pr) - {NA}
    ci = set(r.get("Comment[FractionIdentifier]", NA) for r in pr) - {NA}
    if fv and fv == ci:
        for r in pr:
            if r.get("FactorValue[FractionIdentifier]", NA) != NA:
                fix(r, "FactorValue[FractionIdentifier]", NA)

# A26: OrganismPart case normalization
OP_MAP = {"cell culture": "cell culture", "bone marrow": "bone marrow",
          "epithelial cell": "epithelial cell", "malignant cell": "malignant cell"}
for row in rows:
    val = row.get("Characteristics[OrganismPart]", NA)
    if val != NA and val.lower() in OP_MAP and val != OP_MAP[val.lower()]:
        fix(row, "Characteristics[OrganismPart]", OP_MAP[val.lower()])

print(f"Part A (cleanup): {fixes} fixes")
cleanup_fixes = fixes

# ========================================================================
# PART B: FILL BOOST — fill missing values using general rules
# ========================================================================

# B1: BiologicalReplicate from filenames
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if sum(1 for r in pr if r.get("Characteristics[BiologicalReplicate]", NA) != NA) > len(pr) * 0.5:
        continue
    has_fracs = any(r.get("Comment[FractionIdentifier]", NA) != NA for r in pr)
    
    # Check if TMT labeling
    labels = set(r.get("Characteristics[Label]", NA) for r in pr) - {NA}
    is_tmt = any("tmt" in l.lower() or "itraq" in l.lower() for l in labels)
    
    for r in pr:
        if r.get("Characteristics[BiologicalReplicate]", NA) != NA: continue
        fn = r['Raw Data File']
        
        # Pattern 1: Explicit rep/BR markers (most reliable)
        m = re.search(r'[_-](?:rep|Rep|REP|BR|br)[-_]?(\d+)', fn)
        if m:
            fill_if_na(r, "Characteristics[BiologicalReplicate]", str(int(m.group(1))))
            continue
        
        # Pattern 2: _R1, _R2
        m = re.search(r'[_-]R(\d+)[_.]', fn)
        if m:
            fill_if_na(r, "Characteristics[BiologicalReplicate]", str(int(m.group(1))))
            continue
        
        # Pattern 3: For TMT fractionated studies — use run/set ID from filename
        # e.g. "01-036-VGL-..." → run 36, "20210818_VGL_01_026_..." → run 26
        if is_tmt and has_fracs:
            m = re.search(r'^(\d{2})[_-](\d{3})[_-]', fn)
            if m:
                fill_if_na(r, "Characteristics[BiologicalReplicate]", str(int(m.group(2))))
                continue
            m = re.search(r'[_-](\d{2})[_-](\d{3})[_-]', fn)
            if m:
                fill_if_na(r, "Characteristics[BiologicalReplicate]", str(int(m.group(2))))
                continue
        
        # Pattern 4: Trailing _N.raw for non-fractionated studies only
        if not has_fracs:
            m = re.search(r'[_-](\d{1,2})\.raw$', fn)
            if m:
                fill_if_na(r, "Characteristics[BiologicalReplicate]", str(int(m.group(1))))

# B2: NumberOfTechnicalReplicates default "1"
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if all(r.get("Characteristics[NumberOfTechnicalReplicates]", NA) == NA for r in pr):
        for r in pr:
            fill_if_na(r, "Characteristics[NumberOfTechnicalReplicates]", "1")

# B3: Specimen from MaterialType
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Characteristics[Specimen]", NA) != NA for r in pr): continue
    mats = set(r.get("Characteristics[MaterialType]", NA) for r in pr) - {NA}
    if mats and mats <= {"cell line", "cells"}:
        for r in pr:
            fill_if_na(r, "Characteristics[Specimen]", "cell culture")

# B4: Sex from cell line (general biology)
FEMALE_LINES = {"hek293t", "hek293", "293t", "hela", "mcf7",
                "mdamb231", "huvec", "t47d", "skbr3"}
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Characteristics[Sex]", NA) != NA for r in pr): continue
    cls = set()
    for r in pr:
        cl = r.get("Characteristics[CellLine]", NA)
        if cl != NA: cls.add(cl.lower().replace(" ", "").replace("-", ""))
    if cls and all(c in FEMALE_LINES for c in cls):
        for r in pr:
            fill_if_na(r, "Characteristics[Sex]", "female")

# B5: MS2MassAnalyzer from Instrument
I2MS2 = {
    "orbitrap exploris 480": "orbitrap", "q exactive": "orbitrap",
    "q exactive hf": "orbitrap", "q exactive hf-x": "orbitrap",
    "q exactive plus": "orbitrap", "orbitrap elite": "orbitrap",
    "orbitrap fusion": "orbitrap", "orbitrap fusion lumos": "orbitrap",
    "ltq-orbitrap xl": "ion trap", "ltq-orbitrap": "ion trap",
    "orbitrap velos": "ion trap", "orbitrap astral": "Astral",
    "tripletof 5600+": "TOF", "zeno tof 7600": "TOF",
    "synapt xs": "TOF", "timstof pro": "TOF", "timstof pro 2": "TOF",
}
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Comment[MS2MassAnalyzer]", NA) != NA for r in pr): continue
    for r in pr:
        inst = r.get("Comment[Instrument]", NA)
        if inst != NA:
            ms2 = I2MS2.get(inst.lower().strip())
            if ms2:
                for r2 in pr:
                    fill_if_na(r2, "Comment[MS2MassAnalyzer]", ms2)
                break

# B6: NumberOfSamples = count raw files
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Characteristics[NumberOfSamples]", NA) != NA for r in pr): continue
    n = len(set(r['Raw Data File'] for r in pr))
    for r in pr:
        fill_if_na(r, "Characteristics[NumberOfSamples]", str(n))

# B7: IonizationType from FlowRate
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Comment[IonizationType]", NA) != NA for r in pr): continue
    for r in pr:
        flow = r.get("Comment[FlowRateChromatogram]", NA)
        if flow == NA: continue
        m = re.search(r'(\d+)\s*nL/min', flow)
        if m and int(m.group(1)) <= 1000:
            for r2 in pr:
                fill_if_na(r2, "Comment[IonizationType]", "nanoESI")
            break

# B8: NumberOfFractions from FractionIdentifiers
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Comment[NumberOfFractions]", NA) != NA for r in pr): continue
    fids = set(r.get("Comment[FractionIdentifier]", NA) for r in pr) - {NA}
    if len(fids) >= 2:
        for r in pr:
            fill_if_na(r, "Comment[NumberOfFractions]", str(len(fids)))

# B9: Label normalization — "TMTpro" → "TMTpro 16plex" (specificity matters)
LABEL_SPECIFICITY = {
    "tmtpro": "TMTpro 16plex",
    "tmt": "TMT",
}
for row in rows:
    val = row.get("Characteristics[Label]", NA)
    if val != NA and val.lower().strip() in LABEL_SPECIFICITY:
        fix(row, "Characteristics[Label]", LABEL_SPECIFICITY[val.lower().strip()])

# B10: Separation fix — "SCX"/"high-pH RP"/"MudPIT" in Separation → move to FractionationMethod, set Separation to "C18"
FRAC_METHODS_IN_WRONG_COL = {"scx", "high-ph rp", "mudpit", "sds-page", "high ph rp", "basic rp"}
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    for r in pr:
        sep = r.get("Comment[Separation]", NA)
        if sep != NA and sep.lower().strip() in FRAC_METHODS_IN_WRONG_COL:
            # Move to FractionationMethod if empty
            fill_if_na(r, "Comment[FractionationMethod]", sep)
            # Set Separation to C18 (the analytical column is almost always C18)
            fix(r, "Comment[Separation]", "C18")

# B11: NumberOfBiologicalReplicates — count distinct BiologicalReplicate values
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Characteristics[NumberOfBiologicalReplicates]", NA) != NA for r in pr):
        continue
    br_vals = set(r.get("Characteristics[BiologicalReplicate]", NA) for r in pr) - {NA}
    if len(br_vals) >= 2:
        for r in pr:
            fill_if_na(r, "Characteristics[NumberOfBiologicalReplicates]", str(len(br_vals)))

# B12: AcquisitionMethod — if FragmentationMethod is filled but AcquisitionMethod empty
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Comment[AcquisitionMethod]", NA) != NA for r in pr): continue
    frags = set(r.get("Comment[FragmentationMethod]", NA) for r in pr) - {NA}
    if frags:
        for r in pr:
            fill_if_na(r, "Comment[AcquisitionMethod]", "DDA")

# B13: Separation default "C18" — if any LC column exists but Separation is empty
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("Comment[Separation]", NA) != NA for r in pr): continue
    has_lc = any(r.get("Comment[FlowRateChromatogram]", NA) != NA or 
                 r.get("Comment[GradientTime]", NA) != NA for r in pr)
    if has_lc:
        for r in pr:
            fill_if_na(r, "Comment[Separation]", "C18")

# B14: FactorValue[Bait] — if Characteristics[Bait] has multiple distinct values,
# the bait IS the experimental factor being compared. Copy Bait → FactorValue[Bait].
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("FactorValue[Bait]", NA) != NA for r in pr): continue
    bait_vals = set(r.get("Characteristics[Bait]", NA) for r in pr) - {NA}
    if len(bait_vals) >= 2:  # multiple baits = bait is a factor
        for r in pr:
            bait = r.get("Characteristics[Bait]", NA)
            if bait != NA:
                fill_if_na(r, "FactorValue[Bait]", bait)

# B15: FactorValue[GeneticModification] — if GeneticModification has multiple values
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("FactorValue[GeneticModification]", NA) != NA for r in pr): continue
    gm_vals = set(r.get("Characteristics[GeneticModification]", NA) for r in pr) - {NA}
    if len(gm_vals) >= 2:
        for r in pr:
            gm = r.get("Characteristics[GeneticModification]", NA)
            if gm != NA:
                fill_if_na(r, "FactorValue[GeneticModification]", gm)

# B16: FactorValue[Treatment] — if Treatment has multiple values
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("FactorValue[Treatment]", NA) != NA for r in pr): continue
    treat_vals = set(r.get("Characteristics[Treatment]", NA) for r in pr) - {NA}
    if len(treat_vals) >= 2:
        for r in pr:
            treat = r.get("Characteristics[Treatment]", NA)
            if treat != NA:
                fill_if_na(r, "FactorValue[Treatment]", treat)

# B17: FactorValue[Disease] — if Disease has multiple values
for pxd in pxd_ids:
    pr = [r for r in rows if r['PXD'] == pxd]
    if any(r.get("FactorValue[Disease]", NA) != NA for r in pr): continue
    dis_vals = set(r.get("Characteristics[Disease]", NA) for r in pr) - {NA}
    if len(dis_vals) >= 2:
        for r in pr:
            dis = r.get("Characteristics[Disease]", NA)
            if dis != NA:
                fill_if_na(r, "FactorValue[Disease]", dis)

print(f"Part B (fill):    {fixes - cleanup_fixes} fills")
partb_fixes = fixes

# ========================================================================
# PART C: TEXT-MINING FILLS — extract from paper text using regex
# Requires PubText.json. General rules, no PXD-specific logic.
# ========================================================================

if pubtext:
    def get_full_text(pxd):
        """Get full paper text for a PXD."""
        paper = pubtext.get(pxd, {})
        parts = []
        for key in ['TITLE', 'ABSTRACT', 'METHODS', 'RESULTS', 'INTRO', 'DISCUSS', 'FIG']:
            t = paper.get(key, '')
            if t:
                parts.append(t)
        return ' '.join(parts)

    for pxd in pxd_ids:
        pr = [r for r in rows if r['PXD'] == pxd]
        text = get_full_text(pxd)
        if not text:
            continue
        text_lower = text.lower()

        # C1: Age — extract from methods section
        if all(r.get("Characteristics[Age]", NA) == NA for r in pr):
            age_val = None
            # Only search METHODS section to avoid false matches
            methods_text = pubtext.get(pxd, {}).get('METHODS', '').lower()
            if not methods_text:
                methods_text = text_lower
            
            # Patterns ordered from most specific to least
            age_patterns = [
                # "aged 8 to 10 weeks" → "8 weeks"
                (r'aged?\s+(\d+)\s*(?:to|-)\s*\d+\s*week', 'weeks'),
                # "8-week-old" / "8 week old"
                (r'(\d+)[\s-]*week[\s-]*old', 'weeks'),
                # "aged 8 weeks"
                (r'aged?\s+(\d+)\s*week', 'weeks'),
                # "X to Y months old" / "X-month-old"
                (r'(\d+)\s*(?:to|-)\s*\d+\s*month[\s-]*old', 'months'),
                (r'(\d+)[\s-]*month[\s-]*old', 'months'),
                (r'aged?\s+(\d+)\s*month', 'months'),
                # "55-year-old"
                (r'(\d+)[\s-]*year[\s-]*old', 'years'),
            ]
            for pat, unit in age_patterns:
                m = re.search(pat, methods_text)
                if m:
                    num = m.group(1)
                    # Sanity checks: age should be reasonable
                    n = int(num)
                    if unit == 'weeks' and 1 <= n <= 200:
                        age_val = f"{num} {unit}"
                    elif unit == 'months' and 1 <= n <= 120:
                        age_val = f"{num} {unit}"
                    elif unit == 'years' and 1 <= n <= 120:
                        age_val = f"{num} {unit}"
                    break
            if age_val:
                for r in pr:
                    fill_if_na(r, "Characteristics[Age]", age_val)

        # C2: DevelopmentalStage — determine from context
        if all(r.get("Characteristics[DevelopmentalStage]", NA) == NA for r in pr):
            # Check what kind of organism/material this is
            mat_types = set(r.get("Characteristics[MaterialType]", NA) for r in pr) - {NA}
            organisms = set(r.get("Characteristics[Organism]", NA) for r in pr) - {NA}
            is_animal = any(o in org for org in organisms 
                          for o in ['Mus musculus', 'Rattus', 'Danio', 'Drosophila',
                                    'Caenorhabditis', 'Sus scrofa', 'Bos taurus'])
            is_tissue = 'tissue' in mat_types or 'biofluid' in mat_types
            is_primary_cells = 'cells' in mat_types

            # Only fill for animal studies (tissue, biofluid, or primary cells)
            if is_animal and (is_tissue or is_primary_cells):
                stage = None
                if re.search(r'\b(?:embryo|embryonic)\b', text_lower):
                    # Check if the sample itself is embryonic
                    methods = pubtext.get(pxd, {}).get('METHODS', '').lower()
                    if re.search(r'\b(?:embryo|embryonic)\b.*(?:sample|tissue|cell|harvest|collect|isolat)', methods):
                        stage = "embryonic"
                if not stage and re.search(r'\b(?:fetus|fetal)\b', text_lower):
                    methods = pubtext.get(pxd, {}).get('METHODS', '').lower()
                    if re.search(r'\b(?:fetus|fetal)\b.*(?:sample|tissue|cell|harvest|collect|isolat)', methods):
                        stage = "fetal"
                if not stage and re.search(r'\bneonatal?\b', text_lower):
                    stage = "neonatal"
                if not stage:
                    # Default: if animal study with no embryo/fetal context → adult
                    stage = "adult"
                for r in pr:
                    fill_if_na(r, "Characteristics[DevelopmentalStage]", stage)

        # C3: Depletion — check if abundant protein depletion was used
        if all(r.get("Characteristics[Depletion]", NA) == NA for r in pr):
            methods = pubtext.get(pxd, {}).get('METHODS', '').lower()
            # Look for actual sample depletion keywords in METHODS only
            # Be very specific to avoid matching "top 20 ions" MS acquisition parameters
            depletion_positive = [
                r'(?:abundant protein|high[\s-]?abundance protein).{0,40}(?:deplet|remov)',
                r'(?:deplet|remov).{0,40}(?:abundant protein|high[\s-]?abundance protein)',
                r'(?:MARS|ProteoMiner|immunodepletion|depletion column|depletion kit|depletion spin)',
                r'top[\s-]?\d+[\s-]?(?:protein|abundant|depletion)',
                r'(?:Agilent|Pierce|Thermo).{0,20}depletion',
                r'immuno[\s-]?affinity[\s-]?depletion',
                r'(?:serum|plasma).{0,30}deplet',
            ]
            is_depleted = any(re.search(pat, methods, re.IGNORECASE) for pat in depletion_positive)
            if is_depleted:
                for r in pr:
                    fill_if_na(r, "Characteristics[Depletion]", "depletion")
            else:
                for r in pr:
                    fill_if_na(r, "Characteristics[Depletion]", "no depletion")

    print(f"Part C (text):    {fixes - partb_fixes} fills")
else:
    print("Part C (text):    skipped (no PubText)")

# ========================================================================
# WRITE
# ========================================================================
with open(args.output, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)

filled = sum(1 for r in rows for c in META_COLS if r[c] != NA)
total = len(rows) * len(META_COLS)
print(f"\n{'='*60}")
print(f"DONE — {args.output}")
print(f"{'='*60}")
print(f"Total fixes: {fixes}")
print(f"Total filled: {filled}/{total} ({100*filled/total:.1f}%)")
for pxd in sorted(pxd_ids):
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%)")
