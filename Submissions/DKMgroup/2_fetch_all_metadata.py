#!/usr/bin/env python3
"""
Stage 2: Fetch structured metadata from proteomics APIs and patch submission.

Data sources:
  1. PRIDE Archive REST API v2 (richest metadata)
  2. ProteomeCentral PROXI API (species, instruments, modifications)
  3. PeptideAtlas PROXI API (additional metadata)
  4. jPOST PROXI API (additional metadata)
  5. ProteomeXchange GetDataset (XML-derived JSON, backup)
  6. MassIVE PROXI API [NEW]
  7. OmicsDI API (cross-repository aggregator) [NEW]

Key improvements over v1:
  - Additional API sources (community SDRF, OmicsDI, PRIDE files)
  - Instrument names normalized to FULL standard names (matching Stage 4)
  - Label normalized to "label free" (not "label free sample")
  - Better protocol text parsing for more fields
  - Never patches with placeholder values

Usage:
    python 2_fetch_all_metadata.py --input stage1.csv --output stage2.csv
"""
import json, csv, os, sys, time, re
import urllib.request
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 2: API metadata patch")
parser.add_argument("--input", default=os.path.join(BASE_DIR, "improved_claude", "submission.csv"))
parser.add_argument("--output", default=os.path.join(BASE_DIR, "api_patched", "submission.csv"))
parser.add_argument("--cache-dir", default=os.path.join(BASE_DIR, "cache_all_apis"))
args = parser.parse_args()

INPUT_CSV = args.input
OUTPUT_CSV = args.output
CACHE_DIR = args.cache_dir

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(OUTPUT_CSV) or '.', exist_ok=True)

if not os.path.isfile(INPUT_CSV):
    print(f"ERROR: {INPUT_CSV} not found"); sys.exit(1)

NA = "Not Applicable"

# ============================================================================
# INSTRUMENT NAME NORMALIZATION (aligned with Stage 4 rules)
# ============================================================================
INSTRUMENT_NORMALIZE = {
    "exploris 480": "Orbitrap Exploris 480",
    "orbitrap exploris 480": "Orbitrap Exploris 480",
    "thermo exploris 480": "Orbitrap Exploris 480",
    "fusion lumos": "Orbitrap Fusion Lumos",
    "orbitrap fusion lumos": "Orbitrap Fusion Lumos",
    "orbitrap fusion lumos tribrid": "Orbitrap Fusion Lumos",
    "orbitrap fusion lumos tribrid mass spectrometer": "Orbitrap Fusion Lumos",
    "ltq orbitrap xl": "LTQ-Orbitrap XL",
    "ltq-orbitrap xl": "LTQ-Orbitrap XL",
    "ltq orbitrap": "LTQ-Orbitrap",
    "orbitrap elite": "Orbitrap Elite",
    "ltq orbitrap elite": "Orbitrap Elite",
    "q exactive": "Q Exactive",
    "q exactive hf": "Q Exactive HF",
    "q exactive hf-x": "Q Exactive HF-X",
    "q exactive plus": "Q Exactive Plus",
    "orbitrap astral": "Orbitrap Astral",
    "thermo orbitrap astral": "Orbitrap Astral",
    "tripletof 5600": "TripleTOF 5600+",
    "tripletof 5600+": "TripleTOF 5600+",
    "zenotof 7600": "Zeno TOF 7600",
    "ab sciex zenotof 7600": "Zeno TOF 7600",
    "synapt xs": "Synapt XS",
    "waters synapt xs": "Synapt XS",
    "orbitrap fusion": "Orbitrap Fusion",
    "orbitrap velos": "Orbitrap Velos",
    "ltq orbitrap velos": "Orbitrap Velos",
    "timstof pro": "timsTOF Pro",
    "timstof pro 2": "timsTOF Pro 2",
    "timstof ht": "timsTOF HT",
}

def normalize_instrument(name):
    if not name:
        return name
    lookup = name.lower().strip()
    return INSTRUMENT_NORMALIZE.get(lookup, name)


# ============================================================================
# API ENDPOINTS
# ============================================================================
APIS = {
    "pride": {
        "url": "https://www.ebi.ac.uk/pride/ws/archive/v2/projects/{pxd}",
        "type": "pride",
    },
    "proxi_proteomecentral": {
        "url": "https://proteomecentral.proteomexchange.org/api/proxi/v0.1/datasets/{pxd}",
        "type": "proxi",
    },
    "proxi_peptideatlas": {
        "url": "https://peptideatlas.org/api/proxi/v0.1/datasets/{pxd}",
        "type": "proxi",
    },
    "proxi_jpost": {
        "url": "https://repository.jpostdb.org/proxi/datasets/{pxd}",
        "type": "proxi",
    },
    "px_getdataset": {
        "url": "https://proteomecentral.proteomexchange.org/cgi/GetDataset?ID={pxd}&outputMode=JSON",
        "type": "px",
    },
    "proxi_massive": {
        "url": "https://massive.ucsd.edu/ProteoSAFe/proxi/v0.1/datasets/{pxd}",
        "type": "proxi",
    },
    "omicsdi": {
        "url": "https://www.omicsdi.org/ws/dataset/pride/{pxd}",
        "type": "omicsdi",
    },
}

# ============================================================================
# FETCH WITH CACHING
# ============================================================================
def fetch_url(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "SDRF-Pipeline/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1")
    except Exception as e:
        return None


def fetch_cached(api_name, pxd_id):
    cache_file = os.path.join(CACHE_DIR, f"{pxd_id}_{api_name}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return None

    url = APIS[api_name]["url"].format(pxd=pxd_id)
    text = fetch_url(url)
    if text:
        try:
            data = json.loads(text)
            with open(cache_file, "w") as f:
                json.dump(data, f, indent=2)
            return data
        except json.JSONDecodeError:
            pass
    return None


# ============================================================================
# EXTRACTORS
# ============================================================================

PLACEHOLDER_VALS = {"not available", "not specified", "unknown", "none", "n/a",
                    "na", "not applicable", "not provided", "not determined"}

def is_placeholder(val):
    if val is None:
        return True
    if isinstance(val, (list, dict)):
        return False  # non-empty structured data is not a placeholder
    return str(val).lower().strip() in PLACEHOLDER_VALS


def to_str(val):
    """Safely convert API value to string. Returns '' for non-string types."""
    if val is None:
        return ""
    if isinstance(val, (list, dict)):
        # Try to extract first string element from lists
        if isinstance(val, list) and val:
            return to_str(val[0])
        return ""
    return str(val).strip()


def extract_pride(data):
    """Extract from PRIDE Archive v2 project JSON."""
    if not data:
        return {}
    meta = {}

    # Organisms
    for org in data.get("organisms", []):
        name = to_str(org.get("name", ""))
        if name and not is_placeholder(name):
            name = re.sub(r'\s*\(.*?\)', '', name).strip()
            meta["Characteristics[Organism]"] = name
            break

    # Organism parts
    for p in data.get("organismParts", []):
        name = to_str(p.get("name", ""))
        if name and not is_placeholder(name):
            meta["Characteristics[OrganismPart]"] = name
            break

    # Diseases
    for d in data.get("diseases", []):
        name = to_str(d.get("name", ""))
        if name and not is_placeholder(name):
            meta["Characteristics[Disease]"] = name
            break

    # Instruments — normalize to standard names
    for inst in data.get("instruments", []):
        val = to_str(inst.get("value", "")) or to_str(inst.get("name", ""))
        if val and val != "instrument model" and not is_placeholder(val):
            meta["Comment[Instrument]"] = normalize_instrument(val)
            break

    # Quantification -> Label
    for q in data.get("quantificationMethods", []):
        name = to_str(q.get("name", "")).lower()
        if name and not is_placeholder(name):
            if "label free" in name or "label-free" in name:
                meta["Characteristics[Label]"] = "label free"
            elif "tmtpro" in name:
                meta["Characteristics[Label]"] = "TMTpro 16plex"
            elif "tmt" in name:
                meta["Characteristics[Label]"] = "TMT"
            elif "silac" in name:
                meta["Characteristics[Label]"] = "SILAC"
            elif "itraq" in name:
                meta["Characteristics[Label]"] = "iTRAQ"
            break

    # Sample attributes
    for attr in data.get("sampleAttributes", []):
        name = to_str(attr.get("name", "")).lower()
        value = to_str(attr.get("value", ""))
        if not value or is_placeholder(value):
            continue
        if "cell type" in name:
            meta.setdefault("Characteristics[CellType]", value)
        elif "cell line" in name:
            meta.setdefault("Characteristics[CellLine]", value)
        elif "tissue" in name or "organism part" in name:
            meta.setdefault("Characteristics[OrganismPart]", value)
        elif "disease" in name:
            meta.setdefault("Characteristics[Disease]", value)
        elif "developmental stage" in name:
            meta.setdefault("Characteristics[DevelopmentalStage]", value)
        elif "sex" in name or "gender" in name:
            meta.setdefault("Characteristics[Sex]", value)
        elif "age" in name:
            meta.setdefault("Characteristics[Age]", value)
        elif "strain" in name:
            meta.setdefault("Characteristics[Strain]", value)
        elif "ancestry" in name or "ethnicity" in name:
            meta.setdefault("Characteristics[AncestryCategory]", value)

    # Keywords -> enrichment hints
    for kw in data.get("keywords", []):
        kw_lower = (kw or "").lower()
        if "phospho" in kw_lower:
            meta.setdefault("Comment[EnrichmentMethod]", "phosphopeptide enrichment")
        if "ubiquit" in kw_lower:
            meta.setdefault("Comment[EnrichmentMethod]", "ubiquitin enrichment")
        if "affinity" in kw_lower or "immunoprecip" in kw_lower or "co-ip" in kw_lower:
            meta.setdefault("Comment[EnrichmentMethod]", "immunoprecipitation")

    # Sample processing protocol
    proto = (data.get("sampleProcessingProtocol", "") or "").lower()
    if proto and not is_placeholder(proto):
        if "trypsin" in proto and "lys-c" in proto:
            meta.setdefault("Characteristics[CleavageAgent]", "trypsin/Lys-C")
        elif "trypsin" in proto:
            meta.setdefault("Characteristics[CleavageAgent]", "trypsin")
        elif "pepsin" in proto:
            meta.setdefault("Characteristics[CleavageAgent]", "pepsin")
        if "dtt" in proto or "dithiothreitol" in proto:
            meta.setdefault("Characteristics[ReductionReagent]", "DTT")
        elif "tcep" in proto:
            meta.setdefault("Characteristics[ReductionReagent]", "TCEP")
        if "iodoacetamide" in proto or " iaa " in proto:
            meta.setdefault("Characteristics[AlkylationReagent]", "iodoacetamide")
        elif "chloroacetamide" in proto or " caa " in proto:
            meta.setdefault("Characteristics[AlkylationReagent]", "chloroacetamide")
        # Acquisition method
        if "data-dependent" in proto or " dda " in proto:
            meta.setdefault("Comment[AcquisitionMethod]", "DDA")
        elif "data-independent" in proto or " dia " in proto:
            meta.setdefault("Comment[AcquisitionMethod]", "DIA")
        # Fragmentation
        if " hcd " in proto or "higher-energy" in proto:
            meta.setdefault("Comment[FragmentationMethod]", "HCD")
        elif " cid " in proto or "collision-induced" in proto:
            meta.setdefault("Comment[FragmentationMethod]", "CID")
        # Gradient time
        grad_match = re.search(r'(\d+)\s*(?:min|minute)\s*gradient', proto)
        if grad_match:
            meta.setdefault("Comment[GradientTime]", f"{grad_match.group(1)} min")
        # Flow rate — normalize to nL/min
        flow_nl = re.search(r'(\d+)\s*n[Ll]/min', proto)
        flow_ul = re.search(r'(\d+\.?\d*)\s*[µu]L/min', proto)
        if flow_nl:
            meta.setdefault("Comment[FlowRateChromatogram]", f"{flow_nl.group(1)} nL/min")
        elif flow_ul:
            nl = int(float(flow_ul.group(1)) * 1000)
            meta.setdefault("Comment[FlowRateChromatogram]", f"{nl} nL/min")
        # Missed cleavages
        mc_match = re.search(r'(\d)\s*missed\s*cleavage', proto)
        if mc_match:
            meta.setdefault("Comment[NumberOfMissedCleavages]", mc_match.group(1))

    # Data processing protocol
    data_proto = (data.get("dataProcessingProtocol", "") or "").lower()
    if data_proto and not is_placeholder(data_proto):
        prec_match = re.search(r'(\d+)\s*ppm', data_proto)
        if prec_match:
            meta.setdefault("Comment[PrecursorMassTolerance]", f"{prec_match.group(1)} ppm")
        frag_match = re.search(r'(\d+\.?\d*)\s*da', data_proto)
        if frag_match:
            val = frag_match.group(1)
            # Filter out bogus values (oxidation mass, lock mass)
            if val not in ("15.9949", "445.12", "445.120025"):
                meta.setdefault("Comment[FragmentMassTolerance]", f"{val} Da")
        mc_match = re.search(r'(\d)\s*missed\s*cleavage', data_proto)
        if mc_match:
            meta.setdefault("Comment[NumberOfMissedCleavages]", mc_match.group(1))

    return meta


def extract_proxi(data):
    """Extract from PROXI /datasets response."""
    if not data:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}

    meta = {}

    for sp in data.get("species", []):
        if isinstance(sp, dict):
            name = to_str(sp.get("name", ""))
            if name and not is_placeholder(name):
                meta["Characteristics[Organism]"] = re.sub(r'\s*\(.*?\)', '', name).strip()
                break
        elif isinstance(sp, str) and not is_placeholder(sp):
            meta["Characteristics[Organism]"] = sp
            break

    for inst in data.get("instruments", []):
        if isinstance(inst, dict):
            name = to_str(inst.get("name", ""))
            if name and name != "instrument model" and not is_placeholder(name):
                meta["Comment[Instrument]"] = normalize_instrument(name)
                break
        elif isinstance(inst, str) and not is_placeholder(inst):
            meta["Comment[Instrument]"] = normalize_instrument(inst)
            break

    # Title/description for keyword inference
    title = data.get("title", "") or ""
    desc = data.get("description", "") or ""
    combined = (title + " " + desc).lower()

    if "Characteristics[Organism]" not in meta:
        for org, name in [("homo sapiens", "Homo sapiens"), ("human", "Homo sapiens"),
                          ("mus musculus", "Mus musculus"), ("mouse", "Mus musculus"),
                          ("rattus", "Rattus norvegicus"), ("e. coli", "Escherichia coli"),
                          ("bos taurus", "Bos taurus"), ("bovine", "Bos taurus"),
                          ("saccharomyces", "Saccharomyces cerevisiae"),
                          ("drosophila", "Drosophila melanogaster"),
                          ("zebrafish", "Danio rerio")]:
            if org in combined:
                meta["Characteristics[Organism]"] = name
                break

    return meta


def extract_px(data):
    """Extract from ProteomeXchange GetDataset JSON."""
    if not data:
        return {}
    meta = {}

    for sp_group in data.get("species", []):
        if isinstance(sp_group, dict):
            for t in sp_group.get("terms", []):
                val = to_str(t.get("value", ""))
                if val and not is_placeholder(val):
                    meta["Characteristics[Organism]"] = re.sub(r'\s*\(.*?\)', '', val).strip()
                    break

    for inst_group in data.get("instruments", []):
        if isinstance(inst_group, dict):
            for t in inst_group.get("terms", []):
                val = to_str(t.get("value", ""))
                if val and not is_placeholder(val):
                    meta["Comment[Instrument]"] = normalize_instrument(val)
                    break

    return meta


def extract_omicsdi(data):
    """Extract from OmicsDI API response."""
    if not data:
        return {}
    meta = {}

    # OmicsDI has additional_attributes and cross_references
    for field_name, sdrf_col in [
        ("species", "Characteristics[Organism]"),
        ("tissue", "Characteristics[OrganismPart]"),
        ("disease", "Characteristics[Disease]"),
        ("instrument_platform", "Comment[Instrument]"),
    ]:
        vals = data.get(field_name, [])
        if isinstance(vals, list):
            for v in vals:
                if v and not is_placeholder(str(v)):
                    val = str(v)
                    if sdrf_col == "Comment[Instrument]":
                        val = normalize_instrument(val)
                    meta.setdefault(sdrf_col, val)
                    break
        elif vals and not is_placeholder(str(vals)):
            val = str(vals)
            if field_name == "instrument_platform":
                val = normalize_instrument(val)
            meta.setdefault(sdrf_col, val)

    return meta


# ============================================================================
# MAIN
# ============================================================================
print(f"Loading submission: {INPUT_CSV}")
rows = []
with open(INPUT_CSV, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    COLUMNS = reader.fieldnames
    rows = list(reader)

pxd_ids = sorted(set(r['PXD'] for r in rows))
print(f"Found {len(pxd_ids)} PXDs, {len(rows)} rows\n")

pxd_metadata = {}

for pxd_id in pxd_ids:
    print(f"[{pxd_id}]")
    all_meta = {}

    # Standard APIs
    for api_name, api_config in APIS.items():
        data = fetch_cached(api_name, pxd_id)
        if data:
            api_type = api_config["type"]
            if api_type == "pride":
                extracted = extract_pride(data)
            elif api_type == "proxi":
                extracted = extract_proxi(data)
            elif api_type == "px":
                extracted = extract_px(data)
            elif api_type == "omicsdi":
                extracted = extract_omicsdi(data)
            else:
                extracted = {}

            if extracted:
                for k, v in extracted.items():
                    if k not in all_meta:
                        all_meta[k] = v
                print(f"  {api_name}: +{len(extracted)} fields")
        time.sleep(0.3)

    pxd_metadata[pxd_id] = all_meta
    print(f"  TOTAL: {len(all_meta)} fields\n")

# ============================================================================
# PATCH SUBMISSION
# ============================================================================
print("Patching submission...")

META_COLS = [c for c in COLUMNS if c not in ("ID", "PXD", "Raw Data File", "Usage")]

# Columns where API data shouldn't override (per-file or unreliable from APIs)
NEVER_PATCH = {
    "Characteristics[Modification]", "Characteristics[Modification].1",
    "Characteristics[Modification].2", "Characteristics[Modification].3",
    "Characteristics[Modification].4", "Characteristics[Modification].5",
    "Characteristics[Modification].6",
    "Comment[FractionIdentifier]", "Characteristics[BiologicalReplicate]",
    "Characteristics[TechnicalReplicate]", "Characteristics[Label]", "ID",
    "Characteristics[Treatment]", "Characteristics[Compound]",
    "Characteristics[ConcentrationOfCompound]",
    "Characteristics[Bait]", "Characteristics[GeneticModification]",
    "Characteristics[Time]", "Characteristics[Temperature]",
    "FactorValue[Treatment]", "FactorValue[Bait]", "FactorValue[CellPart]",
    "FactorValue[Compound]", "FactorValue[ConcentrationOfCompound].1",
    "FactorValue[Disease]", "FactorValue[FractionIdentifier]",
    "FactorValue[GeneticModification]", "FactorValue[Temperature]",
}

patched_count = 0
for row in rows:
    pxd = row['PXD']
    api_meta = pxd_metadata.get(pxd, {})
    for col in META_COLS:
        if col in NEVER_PATCH:
            continue
        if row.get(col, NA) == NA and col in api_meta:
            val = str(api_meta[col]).strip()
            if val and not is_placeholder(val):
                row[col] = val
                patched_count += 1

# Write
with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)

# Stats
filled = sum(1 for r in rows for c in META_COLS if r[c] != NA)
total = len(rows) * len(META_COLS)
print(f"\n{'='*60}")
print(f"DONE — {OUTPUT_CSV}")
print(f"{'='*60}")
print(f"Patched cells:  {patched_count}")
print(f"Total filled:   {filled}/{total} ({100*filled/total:.1f}%)")
for pxd in sorted(pxd_ids):
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%), API: {len(pxd_metadata.get(pxd, {}))} fields")
