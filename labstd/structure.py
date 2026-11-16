import json
import re
from datetime import datetime

SCHEMA_VERSION = "1.0"


def to_iso(s):
    if not s:
        return None
    s = str(s).strip().replace("0ct", "Oct")
    base = re.sub(r"[+-]\d{4}$", "", s)
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?)?", base)
    try:
        if m:
            y, mo, d, hh, mi, ss = m.groups()
            if hh:
                return datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0)).isoformat()
            return datetime(int(y), int(mo), int(d)).date().isoformat()
        from dateutil import parser
        iso = re.match(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", s) is not None
        dt = parser.parse(s, dayfirst=not iso, yearfirst=iso)
        return dt.isoformat() if re.search(r"\d:\d", s) else dt.date().isoformat()
    except Exception:
        return None


def parse_value(raw):
    if raw is None:
        return None, None
    s = str(raw).strip()
    m = re.fullmatch(r"([<>]=?|≤|≥)?\s*(\d[\d,]*(?:\.\d+)?)", s)
    if not m:
        return None, None
    comp, num = m.group(1), m.group(2)
    if "," in num:
        if re.fullmatch(r"\d+,\d{1,2}", num):
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    comp = {"≤": "<=", "≥": ">="}.get(comp, comp)
    return float(num), comp


def parse_ref(raw):
    if raw is None:
        return None, None
    t = str(raw).lower().replace("–", "-").replace("—", "-")
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)
    t = re.sub(r"\s+", " ", t)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?:<=|≤|<|less than|upto|up to|below)\s*(\d+(?:\.\d+)?)", t)
    if m:
        return None, float(m.group(1))
    m = re.search(r"(?:>=|≥|>|greater than|above|more than)\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), None
    return None, None


def structured_by_label(extraction_method):
    """Plain label for the Structure step: did this row come from the LLM (and pass
    verification against the source text) or from the built-in rule parser?"""
    return "LLM" if extraction_method == "llm_verified" else "Rules"


def norm_flag(raw):
    if raw is None:
        return None
    t = str(raw).strip().lower().replace("*", "")
    if t in ("h", "hh", "high", "critical high"):
        return "H"
    if t in ("l", "ll", "low", "critical low"):
        return "L"
    if t in ("n", "normal"):
        return "N"
    if t in ("a", "abnormal", "critical"):
        return "A"
    return None


def build_docs(file_info, reports, ocr_engine=None, structuring="rules"):
    docs = []
    for i, rep in enumerate(reports):
        results = []
        for j, r in enumerate(rep["rows"]):
            v, comp = parse_value(r["raw_value"])
            lo, hi = parse_ref(r["raw_ref"])
            results.append({
                "row_idx": j,
                "raw_test_name": r["raw_test_name"],
                "raw_value": r["raw_value"],
                "raw_unit": r["raw_unit"],
                "raw_ref": r["raw_ref"],
                "raw_flag": r["raw_flag"],
                "source_code": r["source_code"],
                "source_system": r["source_system"],
                "value_num": v,
                "comparator": comp,
                "ref_low": lo,
                "ref_high": hi,
                "flag": norm_flag(r["raw_flag"]),
                "extraction_conf": r["extraction_conf"],
                "extraction_method": r.get("extraction_method", "rules"),
            })
        docs.append({
            "schema_version": SCHEMA_VERSION,
            "report_id": f"{file_info['file_hash'][:16]}-{i}",
            "source": {"file_name": file_info["file_name"], "file_hash": file_info["file_hash"], "format": file_info["format"], "route": file_info["route"], "engine": ocr_engine, "structuring": structuring},
            "patient": rep["patient"],
            "lab_name": rep.get("lab_name"),
            "collected_at": rep.get("collected_at"),
            "reported_at": rep.get("reported_at"),
            "results": results,
        })
    return docs


def flatten_doc(doc):
    base = {
        "report_id": doc["report_id"],
        "source_file": doc["source"]["file_name"],
        "source_format": doc["source"]["format"],
        "route": doc["source"]["route"],
        "patient_id": doc["patient"].get("id"),
        "patient_name": doc["patient"].get("name"),
        "sex": doc["patient"].get("sex"),
        "dob": doc["patient"].get("dob"),
        "age_years": doc["patient"].get("age_years"),
        "reported_at": doc.get("reported_at"),
        "collected_at": doc.get("collected_at"),
        "lab_name": doc.get("lab_name"),
    }
    rows = []
    for r in doc["results"]:
        row = dict(base)
        row.update(r)
        row["row_key"] = f"{doc['report_id']}|{r['row_idx']}"
        row["structured_by"] = structured_by_label(row.get("extraction_method"))
        rows.append(row)
    return rows


def doc_to_json(doc):
    return json.dumps(doc, ensure_ascii=False)
