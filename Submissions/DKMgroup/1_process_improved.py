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
parser.add_argument("--pubtext", default=os.path.join(BASE_DIR, "TestPubText", "PubText.json"),
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
#API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
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
NEVER output placeholder values like "not available", "not specified", "unknown" — just omit the field.

MANDATORY STUDY-LEVEL FILLS (put in _GLOBAL_):
- Characteristics[Organism]: ALWAYS. Scientific name only: "Homo sapiens", "Mus musculus". No parentheticals.
- Characteristics[Label]: ALWAYS. "label free" if no labeling. "TMTpro 16plex" (not just "TMTpro"), "TMT 10plex", "SILAC" if labeled. NEVER "label free sample".
- Characteristics[MaterialType]: ALWAYS. "cell line", "tissue", "cells", "biofluid".
- Characteristics[Disease]: ALWAYS. "normal" if healthy/no disease context.
- Characteristics[CleavageAgent]: ALWAYS. "trypsin", "trypsin/Lys-C", "pepsin". If both trypsin and Lys-C → "trypsin/Lys-C".
- Characteristics[ReductionReagent]: ALWAYS if mentioned. "DTT" or "TCEP". NOT enzymes.
- Characteristics[AlkylationReagent]: ALWAYS if mentioned. "iodoacetamide" or "chloroacetamide".
- Characteristics[Temperature]: "37°C" for any mammalian cell culture study. Fill if temperature is a study variable.
- Characteristics[NumberOfSamples]: Count total raw files. If 24 files → "24".
- Characteristics[NumberOfTechnicalReplicates]: Count tech reps per sample. "1" if not mentioned (default). "2" if duplicate injections. "3" if triplicate.
- Characteristics[NumberOfBiologicalReplicates]: Count biological replicates per condition. e.g. "3" for triplicates.
- Characteristics[Sex]: Fill if the sex/gender of the source organism or cell line is known or can be inferred. Use your biological knowledge. "male" or "female".
- Characteristics[Specimen]: "cell culture" for cell-line studies. Describe tissue specimens (e.g. "postmortem brain", "milk serum").
- Characteristics[DevelopmentalStage]: "adult" for adult animal studies. Fill if stated.
- Characteristics[Strain]: Fill for animal studies when strain is mentioned in the methods.
- Characteristics[CellLine]: Fill if a cell line is used. Use the standard cell line name.
- Characteristics[Modification]: Database search modifications in order. See rules below.
- Comment[Instrument]: ALWAYS. FULL name: "Orbitrap Exploris 480", "Q Exactive HF", "Orbitrap Astral", "LTQ-Orbitrap XL", "TripleTOF 5600+", "Orbitrap Fusion Lumos", "Zeno TOF 7600", "Synapt XS".
- Comment[AcquisitionMethod]: ALWAYS. "DDA", "DIA", "PRM", "SWATH", "MSE". IDA = DDA.
- Comment[FragmentationMethod]: ALWAYS. "HCD", "CID", "ETD", "EtHCD".
- Comment[MS2MassAnalyzer]: ALWAYS infer from instrument. "orbitrap", "ion trap", "TOF", "Astral".
- Comment[Separation]: ALWAYS. This is the ANALYTICAL column type. "C18" for reversed-phase. "FAIMS" if FAIMS used. NEVER put "SCX" or "high-pH RP" here — those are fractionation methods.
- Comment[IonizationType]: ALWAYS. "nanoESI" if flow ≤1 µL/min. "ESI" if flow >1 µL/min.
- Comment[CollisionEnergy]: Just the NUMBER. "32" not "32 NCE" or "32%".
- Comment[FlowRateChromatogram]: ALWAYS in nL/min. Convert: 0.25 µL/min = "250 nL/min".
- Comment[GradientTime]: e.g. "130 min", "90 min".
- Comment[PrecursorMassTolerance]: e.g. "20 ppm", "10 ppm".
- Comment[FragmentMassTolerance]: e.g. "0.02 Da", "0.5 Da". NOT FWHM. NOT lock mass.
- Comment[NumberOfMissedCleavages]: e.g. "2".
- Comment[EnrichmentMethod]: "immunoprecipitation" for IP/Co-IP/AP-MS. "phosphopeptide enrichment" for phospho.
- Comment[FractionationMethod]: OFFLINE fractionation before LC-MS. "SCX", "high-pH RP", "SDS-PAGE", "MudPIT". NOT the analytical column.
- Comment[NumberOfFractions]: Count distinct fractions from filenames.

MANDATORY PER-FILE FILLS (put under each filename key):
- Characteristics[BiologicalReplicate]: ALWAYS parse from filename. Look for rep1/rep2/BR1/BR2 patterns. If no rep marker, use sequential numbering. For TMT fractionated runs, use the run/set number.
- Comment[FractionIdentifier]: Parse from filename. Use sequential integers: "1", "2", "3".
- Characteristics[Treatment]: Parse from filename if conditions vary (DMSO, drug, WT, KO, mock, infected, etc.)
- FactorValue[Treatment]: Set per-file when treatments differ across samples.
- Characteristics[Bait]: Parse from filename if AP-MS study with bait proteins.
- Characteristics[GeneticModification]: Parse from filename if genotypes differ (WT, KO, mutant).
- Characteristics[Time]: Parse from filename if timepoints present (0sec, 5sec, 24h).
</critical_behavior>

<value_format>
Every value is a SHORT string (1-5 words). No sentences. No parentheticals. No elaboration.
NEVER use "not available", "not specified", "unknown" as values — just omit the field.

WRONG: "Homo sapiens (human)" → RIGHT: "Homo sapiens"
WRONG: "label free sample" → RIGHT: "label free"
WRONG: "28 NCE" → RIGHT: "28"
WRONG: "30%" → RIGHT: "30"
WRONG: "Orbitrap Exploris 480 mass spectrometer" → RIGHT: "Orbitrap Exploris 480"
WRONG: "not available" → RIGHT: (omit the field entirely)
</value_format>

<categories>
--- Characteristics (sample-level) ---
Characteristics[Age]: e.g. "45 years", "8 weeks"
Characteristics[AlkylationReagent]: e.g. "iodoacetamide", "chloroacetamide"
Characteristics[AnatomicSiteTumor]: Tumor location
Characteristics[AncestryCategory]: Donor ethnicity
Characteristics[BMI]: e.g. "25.3"
Characteristics[Bait]: Bait protein in AP-MS. Parse from filenames.
Characteristics[BiologicalReplicate]: ALWAYS FILL. Plain number from filename: "1", "2", "3".
Characteristics[CellLine]: Fill if a cell line is used. Use the standard name from the paper.
Characteristics[CellPart]: Subcellular fraction ONLY: "nucleus", "exosomes", "microvesicles". NOT cell types.
Characteristics[CellType]: Cell type: "fibroblasts", "macrophage". NOT cell line names.
Characteristics[CleavageAgent]: e.g. "trypsin", "trypsin/Lys-C", "pepsin"
Characteristics[Compound]: Drug or chemical compound used in the experiment.
Characteristics[ConcentrationOfCompound]: e.g. "25 µM"
Characteristics[Depletion]: Depletion method
Characteristics[DevelopmentalStage]: "adult" for adult animals, "embryonic" for embryos. Fill for animal studies.
Characteristics[Disease]: "normal" for healthy. Disease name if diseased.
Characteristics[DiseaseTreatment]: Treatment of disease
Characteristics[GeneticModification]: e.g. "Tmem9 knockout", "PRNP F198S". Parse from filenames (WT, KO, etc.)
Characteristics[Genotype]: e.g. "wild type". Fill if genotype information is available.
Characteristics[GrowthRate]: Growth rate
Characteristics[Label]: "label free", "TMTpro 16plex", "TMT", "SILAC". NEVER "label free sample".
Characteristics[MaterialType]: "cell line", "tissue", "cells", "biofluid"
Characteristics[Modification]: Database search modifications. For TMT: TMTpro → Carbamidomethyl → Oxidation → Met-loss → Acetyl. For label-free: Oxidation → Carbamidomethyl → Acetyl → Deamidated.
Characteristics[NumberOfBiologicalReplicates]: Count bio reps per condition. e.g. "3"
Characteristics[NumberOfSamples]: Count total raw files.
Characteristics[NumberOfTechnicalReplicates]: "1" if not mentioned. "2" for duplicates. "3" for triplicates.
Characteristics[Organism]: Scientific name ONLY. No parentheticals.
Characteristics[OrganismPart]: e.g. "brain", "liver", "serum", "milk"
Characteristics[OriginSiteDisease]: Anatomical site of disease
Characteristics[PooledSample]: "yes" if pooled, "no" if not
Characteristics[ReductionReagent]: "DTT" or "TCEP". NOT enzymes.
Characteristics[SamplingTime]: Collection timepoint
Characteristics[Sex]: Fill when sex of source organism or cell line is known or inferable. "male" or "female".
Characteristics[Specimen]: "cell culture" for cell line studies. Describe tissue specimens.
Characteristics[SpikedCompound]: e.g. "iRT peptides"
Characteristics[Staining]: Staining before MS
Characteristics[Strain]: Fill when animal strain is mentioned in methods.
Characteristics[SyntheticPeptide]: "yes" if synthetic peptides used
Characteristics[Temperature]: "37°C" for mammalian cell culture. Fill if temperature is studied.
Characteristics[Time]: e.g. "0sec", "5sec", "30sec". Parse from filenames.
Characteristics[Treatment]: Treatment applied. e.g. "DMSO", drug name with concentration. Parse from filenames.

--- Comment (technical) ---
Comment[AcquisitionMethod]: "DDA", "DIA", "PRM", "SWATH", "MSE"
Comment[CollisionEnergy]: Just the number, no units. "32" not "32 NCE".
Comment[EnrichmentMethod]: "immunoprecipitation" for AP-MS/Co-IP. "phosphopeptide enrichment" for phospho.
Comment[FlowRateChromatogram]: Always nL/min. "250 nL/min", "500 nL/min".
Comment[FractionIdentifier]: Sequential integers from filename: "1", "2", "3".
Comment[FractionationMethod]: "MudPIT", "SCX", "high-pH RP", "SDS-PAGE"
Comment[FragmentationMethod]: "HCD", "CID", "ETD", "EtHCD"
Comment[FragmentMassTolerance]: e.g. "0.02 Da". NOT lock masses, NOT FWHM.
Comment[GradientTime]: e.g. "130 min"
Comment[Instrument]: FULL name: "Orbitrap Exploris 480", "Q Exactive HF", "Orbitrap Astral"
Comment[IonizationType]: "nanoESI" for ≤1 µL/min flow. "ESI" for >1 µL/min.
Comment[MS2MassAnalyzer]: "orbitrap", "ion trap", "TOF", "Astral"
Comment[NumberOfFractions]: Count distinct fractions. e.g. "8"
Comment[NumberOfMissedCleavages]: e.g. "2"
Comment[PrecursorMassTolerance]: e.g. "20 ppm". NOT FWHM.
Comment[Separation]: ANALYTICAL column. "C18" for reversed-phase. "FAIMS" if FAIMS. NEVER "SCX" (that's FractionationMethod).

--- FactorValue (ONLY when that variable is compared across samples) ---
FactorValue[Bait]: If different baits are compared
FactorValue[CellPart]: If different compartments are compared
FactorValue[Compound]: If drug vs control is compared
FactorValue[ConcentrationOfCompound].1: If doses are compared
FactorValue[Disease]: If disease states are compared
FactorValue[FractionIdentifier]: If fractions are the study variable (usually NOT)
FactorValue[GeneticModification]: If genotypes (WT vs KO) are compared
FactorValue[Temperature]: If temperatures are compared
FactorValue[Treatment]: If treatments are compared across samples

When you set a FactorValue, ALSO set the matching Characteristics column.
</categories>

<filename_parsing>
CRITICAL: Parse EVERY .raw filename to extract per-file metadata. This is where most per-file values come from.

For EVERY file, you MUST extract:
1. BiologicalReplicate: Look for rep1/rep2, BR1/BR2, _R1/_R2, or sequential numbers.
   For TMT studies with fractions, use the run/set identifier (e.g. first 2-3 digit number in filename).
2. FractionIdentifier: Look for F01/F02, frac1/frac2, or sequential numbers at the end of filename.
3. Treatment/Condition: Look for DMSO/drug/WT/KO/ctrl/mock/infected/treated patterns.
4. Bait protein: For AP-MS, look for protein/gene names that are bait targets.
5. Time points: Look for 0sec/5sec/30sec/24h patterns.
6. Genotype: Look for WT/KO/mutant/delta patterns.

Common filename structures (general patterns):
  "DATE_CONDITION_TREATMENT_CELLTYPE_REPLICATE.raw" → parse Treatment and BiologicalReplicate
  "PLASMID_GENOTYPE_REPLICATE.raw" → parse GeneticModification and BiologicalReplicate
  "PROTEIN_CONDITION_TIMEPOINT_REPLICATE_VIAL.raw" → parse Treatment, Time, BiologicalReplicate
  "RUNID-RESEARCHER-DATE-BAIT-LABEL-FRACTION_NUM.raw" → parse Bait and FractionIdentifier
</filename_parsing>

<rules>
1. FILL as many columns as possible. Being conservative is worse than being slightly wrong.
2. Characteristics[Organism]: scientific name only, no parentheticals.
3. Comment[Instrument]: FULL standard name with Orbitrap prefix where applicable.
4. Characteristics[Modification] order: TMT studies: TMTpro → Carbamidomethyl → Oxidation → Met-loss → Acetyl. Label-free: Oxidation → Carbamidomethyl → Acetyl → Deamidated.
5. Comment[EnrichmentMethod]: "immunoprecipitation" not "FLAG IP".
6. CellType ≠ CellLine. CellPart = subcellular fraction only.
7. FragmentMassTolerance = mass tolerance only. PrecursorMassTolerance = mass tolerance only.
8. ReductionReagent = reducing agent only (DTT, TCEP), not enzymes.
9. Parse filenames aggressively — this is where BiologicalReplicate, Treatment, Bait, Time come from.
10. NEVER output "not available", "not specified", "unknown" — just omit.
11. Characteristics[Label]: exactly "label free". NEVER "label free sample".
12. Comment[CollisionEnergy]: just the number. "32" not "32 NCE".
13. Comment[FlowRateChromatogram]: always nL/min.
14. Characteristics[Sex]: FILL when the sex of the source organism or cell line is known. Use your knowledge.
15. Characteristics[NumberOfTechnicalReplicates]: "1" if not explicitly stated otherwise.
16. Characteristics[Specimen]: "cell culture" for cell line studies. ALWAYS fill.
17. Characteristics[BiologicalReplicate]: ALWAYS parse from filename. Every file must have one.
</rules>

<output_format>
Single JSON object. "_GLOBAL_" for shared metadata. Per-file keys for values that DIFFER between files.
You MUST have per-file entries for BiologicalReplicate, FractionIdentifier, Treatment, Bait, Time, etc.
Each value: a SHORT string. Not a list, not a sentence, no parentheticals.

{
  "_GLOBAL_": {
    "Characteristics[Organism]": "Homo sapiens",
    "Characteristics[Disease]": "normal",
    "Characteristics[MaterialType]": "cell line",
    "Characteristics[CellLine]": "CellLineName",
    "Characteristics[Sex]": "female",
    "Characteristics[CleavageAgent]": "trypsin/Lys-C",
    "Characteristics[Label]": "label free",
    "Characteristics[Temperature]": "37°C",
    "Characteristics[Specimen]": "cell culture",
    "Characteristics[NumberOfTechnicalReplicates]": "1",
    "Characteristics[NumberOfBiologicalReplicates]": "3",
    "Characteristics[NumberOfSamples]": "24",
    "Characteristics[Modification]": ["Oxidation", "Carbamidomethyl", "Acetyl"],
    "Characteristics[ReductionReagent]": "DTT",
    "Characteristics[AlkylationReagent]": "iodoacetamide",
    "Comment[Instrument]": "Q Exactive HF",
    "Comment[AcquisitionMethod]": "DDA",
    "Comment[FragmentationMethod]": "HCD",
    "Comment[MS2MassAnalyzer]": "orbitrap",
    "Comment[Separation]": "C18",
    "Comment[CollisionEnergy]": "28",
    "Comment[EnrichmentMethod]": "immunoprecipitation",
    "Comment[IonizationType]": "nanoESI",
    "Comment[NumberOfMissedCleavages]": "2",
    "Comment[PrecursorMassTolerance]": "10 ppm",
    "Comment[FragmentMassTolerance]": "0.02 Da",
    "Comment[FlowRateChromatogram]": "250 nL/min",
    "Comment[GradientTime]": "150 min"
  },
  "sample_condition1_rep1.raw": {
    "Characteristics[Treatment]": "condition1",
    "FactorValue[Treatment]": "condition1",
    "Characteristics[BiologicalReplicate]": "1"
  },
  "sample_condition2_rep1.raw": {
    "Characteristics[Treatment]": "condition2",
    "FactorValue[Treatment]": "condition2",
    "Characteristics[BiologicalReplicate]": "1"
  },
  "sample_condition1_rep2.raw": {
    "Characteristics[Treatment]": "condition1",
    "FactorValue[Treatment]": "condition1",
    "Characteristics[BiologicalReplicate]": "2"
  }
}
</output_format>"""


def build_prompt(paper, raw_files):
    """Build prompt with ALL paper sections, not just title/abstract/methods."""
    title = paper.get('TITLE', '')
    abstract = paper.get('ABSTRACT', '')
    methods = paper.get('METHODS', '')
    intro = paper.get('INTRO', '')
    results = paper.get('RESULTS', '')
    discuss = paper.get('DISCUSS', '')
    fig = paper.get('FIG', '')
    
    # Build full manuscript with all sections
    sections = []
    if title:
        sections.append(f"TITLE:\n{title}")
    if abstract:
        sections.append(f"ABSTRACT:\n{abstract}")
    if intro:
        sections.append(f"INTRODUCTION:\n{intro}")
    if results:
        sections.append(f"RESULTS:\n{results}")
    if discuss:
        sections.append(f"DISCUSSION:\n{discuss}")
    if methods:
        sections.append(f"METHODS:\n{methods}")
    if fig:
        sections.append(f"FIGURES:\n{fig}")
    
    manuscript = "\n\n".join(sections)
    
    # Truncation: prioritize METHODS, ABSTRACT, TITLE, then others
    if len(manuscript) > 150000:
        # Keep title, abstract, methods in full; truncate others
        core = f"TITLE:\n{title}\n\nABSTRACT:\n{abstract}\n\nMETHODS:\n{methods}"
        remaining_budget = 150000 - len(core)
        extras = []
        for section_name, section_text in [("RESULTS", results), ("INTRODUCTION", intro), 
                                            ("FIGURES", fig), ("DISCUSSION", discuss)]:
            if section_text and remaining_budget > 1000:
                chunk = section_text[:remaining_budget]
                extras.append(f"{section_name}:\n{chunk}")
                remaining_budget -= len(chunk)
        manuscript = core + "\n\n" + "\n\n".join(extras) if extras else core
        manuscript += "\n[...some sections truncated for length...]"
    
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


SYSTEM_PROMPT = """You are a proteomics expert specializing in SDRF metadata extraction. 
You read scientific papers and extract structured metadata with high accuracy.
You understand mass spectrometry instruments, sample preparation, and SDRF standards.
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
        paper,
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
