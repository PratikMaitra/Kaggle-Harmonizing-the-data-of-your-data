#!/usr/bin/env python3
"""
Fetch structured metadata from multiple proteomics APIs:
  1. PRIDE Archive REST API v2 (richest metadata)
  2. ProteomeCentral PROXI API (species, instruments, modifications)
  3. PeptideAtlas PROXI API (additional metadata)
  4. jPOST PROXI API (additional metadata)
  5. ProteomeXchange GetDataset (XML-derived JSON, backup)

Merges all sources and patches submission.csv to fill weak/missing columns.

Usage:
    # Test (defaults):
    python fetch_all_metadata.py

    # Training:
    python fetch_all_metadata.py \
        --input error_analysis/predictions.csv \
        --output error_analysis/predictions_api_patched.csv \
        --cache-dir cache_all_apis
"""
import json, csv, os, sys, time
import urllib.request
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Stage 2: API metadata patch")
parser.add_argument("--input", default=os.path.join(BASE_DIR, "improved_claude", "submission.csv"),
                    help="Input CSV (Stage 1 output)")
parser.add_argument("--output", default=os.path.join(BASE_DIR, "api_patched", "submission.csv"),
                    help="Output CSV path")
parser.add_argument("--cache-dir", default=os.path.join(BASE_DIR, "cache_all_apis"),
                    help="Cache directory for API responses")
args = parser.parse_args()

INPUT_CSV = args.input
OUTPUT_CSV = args.output
OUTPUT_DIR = os.path.dirname(OUTPUT_CSV)
CACHE_DIR = args.cache_dir

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

if not os.path.isfile(INPUT_CSV):
    print(f"ERROR: Input CSV not found: {INPUT_CSV}")
    print(f"Usage: python fetch_all_metadata.py --input path/to/submission.csv --output path/to/output.csv")
    sys.exit(1)

NA = "Not Applicable"

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
}

# ============================================================================
# FETCH WITH CACHING
# ============================================================================
def fetch_url(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Try utf-8, fall back to latin-1
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
            return json.load(f)

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
# EXTRACTORS FOR EACH API TYPE
# ============================================================================

def extract_pride(data):
    """Extract from PRIDE Archive v2 project JSON."""
    if not data:
        return {}
    meta = {}

    # Organisms
    for org in data.get("organisms", []):
        name = org.get("name", "")
        if name:
            meta["Characteristics[Organism]"] = name
            break

    # Organism parts
    for p in data.get("organismParts", []):
        name = p.get("name", "")
        if name:
            meta["Characteristics[OrganismPart]"] = name
            break

    # Diseases
    for d in data.get("diseases", []):
        name = d.get("name", "")
        if name:
            meta["Characteristics[Disease]"] = name
            break

    # Instruments
    for inst in data.get("instruments", []):
        val = inst.get("value", "") or inst.get("name", "")
        if val and val != "instrument model":
            meta["Comment[Instrument]"] = val
            break

    # Quantification -> Label
    for q in data.get("quantificationMethods", []):
        name = (q.get("name", "") or "").lower()
        if name:
            if "label free" in name or "label-free" in name:
                meta["Characteristics[Label]"] = "label free"
            elif "tmt" in name:
                meta["Characteristics[Label]"] = "TMT"
            elif "silac" in name:
                meta["Characteristics[Label]"] = "SILAC"
            elif "itraq" in name:
                meta["Characteristics[Label]"] = "iTRAQ"
            break

    # Sample attributes
    for attr in data.get("sampleAttributes", []):
        name = (attr.get("name", "") or "").lower()
        value = attr.get("value", "")
        if not value:
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

    # Sample processing protocol -> cleavage, reduction, alkylation
    proto = (data.get("sampleProcessingProtocol", "") or "").lower()
    if proto and proto != "not available":
        if "trypsin" in proto:
            meta.setdefault("Characteristics[CleavageAgent]", "trypsin")
        if "lys-c" in proto:
            meta.setdefault("Characteristics[CleavageAgent]", "trypsin/Lys-C")
        if "dtt" in proto or "dithiothreitol" in proto:
            meta.setdefault("Characteristics[ReductionReagent]", "DTT")
        if "tcep" in proto:
            meta.setdefault("Characteristics[ReductionReagent]", "TCEP")
        if "iodoacetamide" in proto or " iaa " in proto:
            meta.setdefault("Characteristics[AlkylationReagent]", "iodoacetamide")
        if "chloroacetamide" in proto or " caa " in proto:
            meta.setdefault("Characteristics[AlkylationReagent]", "chloroacetamide")
        # MS instrument from protocol text
        for inst_kw in ["Q Exactive", "Orbitrap", "LTQ", "Lumos", "Exploris",
                        "timsTOF", "TripleTOF", "Synapt", "QTOF", "Astral",
                        "Velos", "Elite", "Fusion"]:
            if inst_kw.lower() in proto:
                meta.setdefault("Comment[Instrument]", inst_kw)
        # Acquisition method
        if "data-dependent" in proto or "dda" in proto:
            meta.setdefault("Comment[AcquisitionMethod]", "DDA")
        if "data-independent" in proto or " dia " in proto:
            meta.setdefault("Comment[AcquisitionMethod]", "DIA")
        # Fragmentation
        if " hcd " in proto or "higher-energy" in proto:
            meta.setdefault("Comment[FragmentationMethod]", "HCD")
        if " cid " in proto or "collision-induced" in proto:
            meta.setdefault("Comment[FragmentationMethod]", "CID")
        # Gradient time
        import re
        grad_match = re.search(r'(\d+)\s*(?:min|minute)\s*gradient', proto)
        if grad_match:
            meta.setdefault("Comment[GradientTime]", f"{grad_match.group(1)} min")
        # Flow rate
        flow_match = re.search(r'(\d+)\s*n[Ll]/min', proto)
        if flow_match:
            meta.setdefault("Comment[FlowRateChromatogram]", f"{flow_match.group(1)} nL/min")
        # Missed cleavages
        mc_match = re.search(r'(\d)\s*missed\s*cleavage', proto)
        if mc_match:
            meta.setdefault("Comment[NumberOfMissedCleavages]", mc_match.group(1))

    # Data processing protocol -> more MS params
    data_proto = (data.get("dataProcessingProtocol", "") or "").lower()
    if data_proto and data_proto != "not available":
        # Precursor tolerance
        import re
        prec_match = re.search(r'(\d+)\s*ppm', data_proto)
        if prec_match:
            meta.setdefault("Comment[PrecursorMassTolerance]", f"{prec_match.group(1)} ppm")
        frag_match = re.search(r'(\d+\.?\d*)\s*da', data_proto)
        if frag_match:
            meta.setdefault("Comment[FragmentMassTolerance]", f"{frag_match.group(1)} Da")
        mc_match = re.search(r'(\d)\s*missed\s*cleavage', data_proto)
        if mc_match:
            meta.setdefault("Comment[NumberOfMissedCleavages]", mc_match.group(1))

    return meta


def extract_proxi(data):
    """Extract from PROXI /datasets response (list or dict)."""
    if not data:
        return {}
    # PROXI can return a list with one item or a dict
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}

    meta = {}

    # Species
    for sp in data.get("species", []):
        if isinstance(sp, dict):
            name = sp.get("name", "")
            if name:
                meta["Characteristics[Organism]"] = name
                break
        elif isinstance(sp, str):
            meta["Characteristics[Organism]"] = sp
            break

    # Instruments
    for inst in data.get("instruments", []):
        if isinstance(inst, dict):
            name = inst.get("name", "")
            if name and name != "instrument model":
                meta["Comment[Instrument]"] = name
                break
        elif isinstance(inst, str):
            meta["Comment[Instrument]"] = inst
            break

    # Contacts (not directly useful but can indicate lab)
    # Modifications
    for mod in data.get("modifications", []):
        if isinstance(mod, dict):
            name = mod.get("name", "")
            if name:
                # Skip biological PTMs, only want search mods
                # (this is the PROXI version, less detailed)
                pass

    # Title and description can contain useful keywords
    title = data.get("title", "") or ""
    desc = data.get("description", "") or ""
    combined = (title + " " + desc).lower()

    # Try to get organism from title/description if not from species field
    if "Characteristics[Organism]" not in meta:
        for org, name in [("homo sapiens", "Homo sapiens"), ("human", "Homo sapiens"),
                          ("mus musculus", "Mus musculus"), ("mouse", "Mus musculus"),
                          ("rattus", "Rattus norvegicus"), ("rat ", "Rattus norvegicus"),
                          ("e. coli", "Escherichia coli"), ("escherichia", "Escherichia coli"),
                          ("bos taurus", "Bos taurus"), ("bovine", "Bos taurus"),
                          ("drosophila", "Drosophila melanogaster"),
                          ("arabidopsis", "Arabidopsis thaliana"),
                          ("saccharomyces", "Saccharomyces cerevisiae"),
                          ("yeast", "Saccharomyces cerevisiae"),
                          ("plasmodium", "Plasmodium falciparum"),
                          ("zebrafish", "Danio rerio")]:
            if org in combined:
                meta["Characteristics[Organism]"] = name
                break

    # Disease hints from title/description
    if "Characteristics[Disease]" not in meta:
        for disease_kw, disease_val in [
            ("alzheimer", "Alzheimer's disease"), ("cancer", "cancer"),
            ("tumor", "cancer"), ("carcinoma", "carcinoma"),
            ("leukemia", "leukemia"), ("melanoma", "melanoma"),
            ("diabetes", "diabetes"), ("normal", "normal"),
        ]:
            if disease_kw in combined:
                meta["Characteristics[Disease]"] = disease_val
                break

    # Material type hints
    if "Characteristics[MaterialType]" not in meta:
        for mat_kw, mat_val in [
            ("cell line", "cell line"), ("tissue", "tissue"),
            ("serum", "biofluid"), ("plasma", "biofluid"),
            ("urine", "biofluid"), ("blood", "biofluid"),
        ]:
            if mat_kw in combined:
                meta["Characteristics[MaterialType]"] = mat_val
                break

    return meta


def extract_px(data):
    """Extract from ProteomeXchange GetDataset JSON."""
    if not data:
        return {}
    meta = {}

    # Species
    for sp_group in data.get("species", []):
        if isinstance(sp_group, dict):
            for t in sp_group.get("terms", []):
                val = t.get("value", "")
                if val:
                    meta["Characteristics[Organism]"] = val
                    break

    # Instruments
    for inst_group in data.get("instruments", []):
        if isinstance(inst_group, dict):
            for t in inst_group.get("terms", []):
                val = t.get("value", "")
                if val:
                    meta["Comment[Instrument]"] = val
                    break

    return meta


# ============================================================================
# MAIN: LOAD, FETCH ALL, MERGE, PATCH
# ============================================================================
print(f"Loading submission: {INPUT_CSV}")
rows = []
with open(INPUT_CSV, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    COLUMNS = reader.fieldnames
    rows = list(reader)

pxd_ids = sorted(set(r['PXD'] for r in rows))
print(f"Found {len(pxd_ids)} PXDs, {len(rows)} rows\n")

# Fetch from ALL APIs for each PXD
pxd_metadata = {}

for pxd_id in pxd_ids:
    print(f"[{pxd_id}]")
    all_meta = {}  # merged from all sources

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
            else:
                extracted = {}

            if extracted:
                # Only fill gaps — don't overwrite earlier sources
                for k, v in extracted.items():
                    if k not in all_meta:
                        all_meta[k] = v
                print(f"  {api_name}: +{len(extracted)} fields")
        time.sleep(0.3)

    pxd_metadata[pxd_id] = all_meta
    print(f"  TOTAL: {len(all_meta)} fields")
    print()

# ============================================================================
# PATCH SUBMISSION
# ============================================================================
print("Patching submission...")

META_COLS = [c for c in COLUMNS if c not in ("ID", "PXD", "Raw Data File", "Usage")]

# Columns where API data is wrong or per-file (API only has study-level)
NEVER_PATCH = {
    "Characteristics[Modification]", "Characteristics[Modification].1",
    "Characteristics[Modification].2", "Characteristics[Modification].3",
    "Characteristics[Modification].4", "Characteristics[Modification].5",
    "Characteristics[Modification].6",
    "Comment[FractionIdentifier]", "Characteristics[BiologicalReplicate]",
    "Characteristics[TechnicalReplicate]", "Characteristics[Label]", "ID",
    "Characteristics[Treatment]", "FactorValue[Treatment]",
    "FactorValue[Bait]", "FactorValue[CellPart]",
    "FactorValue[Compound]", "FactorValue[ConcentrationOfCompound].1",
    "FactorValue[Disease]", "FactorValue[FractionIdentifier]",
    "FactorValue[GeneticModification]", "FactorValue[Temperature]",
}

print(f"  Protected columns: {len(NEVER_PATCH)}")
patched_count = 0

for row in rows:
    pxd = row['PXD']
    api_meta = pxd_metadata.get(pxd, {})
    for col in META_COLS:
        if col in NEVER_PATCH:
            continue
        if row.get(col, NA) == NA and col in api_meta:
            val = str(api_meta[col]).strip()
            if val and val.lower() not in ("", "none", "null", "n/a", "na",
                                            "not available", "not applicable"):
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
print()
for pxd in sorted(pxd_ids):
    pr = [r for r in rows if r['PXD'] == pxd]
    pf = sum(1 for r in pr for c in META_COLS if r[c] != NA)
    pt = len(pr) * len(META_COLS)
    api_n = len(pxd_metadata.get(pxd, {}))
    print(f"  {pxd}: {len(pr):>5} rows, {pf:>5}/{pt} filled ({100*pf/pt:5.1f}%), API: {api_n} fields")
