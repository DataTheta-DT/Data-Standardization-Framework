import io
import json
import os

import pandas as pd

from .structure import structured_by_label
from .validate import issues_to_text

SCHEMA_VERSION = "2.0"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "standardized_report.schema.json")


def _clean(v):
    if v is None:
        return None
    try:
        if v != v:
            return None
    except Exception:
        pass
    if hasattr(v, "item"):
        return v.item()
    return v


def _row_doc(r, idx):
    issues = json.loads(r.get("issues") or "[]")
    try:
        cands = json.loads(r.get("loinc_candidates") or "[]")
    except (TypeError, ValueError):
        cands = []
    return {
        "row_number": idx + 1,
        "raw": {"test_name": r.get("raw_test_name"), "value": _clean(r.get("raw_value")), "unit": _clean(r.get("raw_unit")), "reference_range": _clean(r.get("raw_ref")), "flag": _clean(r.get("raw_flag"))},
        "standardized": {
            "loinc_num": _clean(r.get("loinc_num")),
            "loinc_name": _clean(r.get("loinc_name")),
            "value": _clean(r.get("value_std")),
            "unit": _clean(r.get("canonical_unit")) if _clean(r.get("value_std")) is not None else None,
            "reference_low": _clean(r.get("ref_low_std")),
            "reference_high": _clean(r.get("ref_high_std")),
            "flag_reported": _clean(r.get("flag")),
            "flag_derived": _clean(r.get("flag_derived")),
            "comparator": _clean(r.get("comparator")),
        },
        "mapping": {"status": r.get("loinc_status") or "unmapped", "method": _clean(r.get("loinc_method")), "score": _clean(r.get("loinc_score")), "proposed_loinc": _clean(r.get("loinc_proposed")), "candidates": cands},
        "unit": {"original": _clean(r.get("raw_unit")), "status": _clean(r.get("unit_status")), "ucum": _clean(r.get("unit_ucum")), "canonical": _clean(r.get("canonical_unit")), "conversion": _clean(r.get("convert_status"))},
        "extraction": {"method": _clean(r.get("extraction_method")) or "rules", "confidence": _clean(r.get("extraction_conf"))},
        "validation": {"status": r.get("status"), "confidence": _clean(r.get("confidence")), "issues": issues},
    }


def build_report_doc(meta, rows):
    rows = sorted(rows, key=lambda r: r.get("row_idx", 0))
    results = [_row_doc(r, i) for i, r in enumerate(rows)]
    n = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        n[r["validation"]["status"]] += 1
    issues = []
    p = meta.get("patient") or {}
    if not results:
        issues.append({"code": "no_results", "severity": "error", "detail": ""})
    if not p.get("id") and not p.get("name"):
        issues.append({"code": "missing_patient_identity", "severity": "warn", "detail": ""})
    if not meta.get("collected_at"):
        issues.append({"code": "missing_collection_time", "severity": "warn", "detail": ""})
    if n["FAIL"] == len(results) and results:
        status = "FAIL"
    elif n["FAIL"] or n["WARN"] or issues:
        status = "WARN"
    else:
        status = "PASS"
    src = meta.get("source") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "report_id": meta["report_id"],
        "source": {"file_name": src.get("file_name"), "file_hash": src.get("file_hash"), "format": src.get("format"), "route": src.get("route"), "engine": src.get("engine"), "structuring": src.get("structuring"), "llm": src.get("llm")},
        "patient": {"id": p.get("id"), "name": p.get("name"), "name_raw": p.get("name_raw"), "sex": p.get("sex"), "age_years": p.get("age_years"), "dob": p.get("dob")},
        "lab_name": meta.get("lab_name"),
        "collected_at": meta.get("collected_at"),
        "reported_at": meta.get("reported_at"),
        "results": results,
        "validation": {"status": status, "rows": len(results), "pass": n["PASS"], "warn": n["WARN"], "fail": n["FAIL"], "issues": issues},
    }


def validate_report_json(doc):
    try:
        import jsonschema
    except ImportError:
        return [] if all(k in doc for k in ("schema_version", "report_id", "source", "patient", "results", "validation")) else ["missing required keys"]
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    v = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(x) for x in e.path)}: {e.message}" for e in v.iter_errors(doc)]


RESULTS_COLUMNS = [
    "report_id", "source_file", "patient_id", "patient_name", "sex", "age_years", "dob", "collected_at", "lab_name",
    "test_name_raw", "value_raw", "unit_raw", "reference_raw", "flag_raw",
    "loinc_num", "loinc_name", "value_std", "unit_ucum", "ref_low_std", "ref_high_std", "flag_derived",
    "validation_status", "confidence", "issues", "structured_by",
]


def results_frame(docs):
    """The final, simple results table: one row per test result, with the value and
    unit as written on the report next to the standardized LOINC code, value and
    unit, the validation status, and a 'structured_by' column recording whether
    the row was read by the LLM (and verified) or by the built-in rules. Internal
    diagnostics (which extraction route or engine was used, LOINC matching method
    and score, raw unit-conversion status, proposed-but-unmapped LOINC codes) are
    kept in the full JSON document for anyone who needs them, not in this table."""
    out = []
    for d in docs:
        for r in d["results"]:
            s = r["standardized"]
            out.append({
                "report_id": d["report_id"], "source_file": d["source"]["file_name"],
                "patient_id": d["patient"]["id"], "patient_name": d["patient"]["name"], "sex": d["patient"]["sex"], "age_years": d["patient"]["age_years"],
                "dob": d["patient"]["dob"], "collected_at": d["collected_at"], "lab_name": d["lab_name"],
                "test_name_raw": r["raw"]["test_name"], "value_raw": r["raw"]["value"], "unit_raw": r["raw"]["unit"],
                "reference_raw": r["raw"]["reference_range"], "flag_raw": r["raw"]["flag"],
                "loinc_num": s["loinc_num"], "loinc_name": s["loinc_name"], "value_std": s["value"], "unit_ucum": s["unit"],
                "ref_low_std": s["reference_low"], "ref_high_std": s["reference_high"], "flag_derived": s["flag_derived"],
                "validation_status": r["validation"]["status"], "confidence": r["validation"]["confidence"],
                "issues": issues_to_text(json.dumps(r["validation"]["issues"])),
                "structured_by": structured_by_label(r["extraction"]["method"]),
            })
    return pd.DataFrame(out, columns=RESULTS_COLUMNS)


def issues_frame(docs):
    out = []
    for d in docs:
        for r in d["results"]:
            for i in r["validation"]["issues"]:
                out.append({"report_id": d["report_id"], "source_file": d["source"]["file_name"], "row_number": r["row_number"], "test_name_raw": r["raw"]["test_name"], "severity": i["severity"], "code": i["code"], "detail": i.get("detail", "")})
        for i in d["validation"]["issues"]:
            out.append({"report_id": d["report_id"], "source_file": d["source"]["file_name"], "row_number": None, "test_name_raw": None, "severity": i["severity"], "code": i["code"], "detail": i.get("detail", "")})
    return pd.DataFrame(out, columns=["report_id", "source_file", "row_number", "test_name_raw", "severity", "code", "detail"])


SUMMARY_COLUMNS = ["source_file", "report_id", "patient", "rows", "pass", "warn", "fail", "report_status", "structured_by"]


def summary_frame(docs):
    """One row per report: how many results passed, need review, or could not be
    standardized, and whether that report was structured by the LLM or by rules
    ('LLM' if any of its rows was LLM-verified, otherwise 'Rules')."""
    out = []
    for d in docs:
        v = d["validation"]
        by_llm = any(r["extraction"]["method"] == "llm_verified" for r in d["results"])
        out.append({
            "source_file": d["source"]["file_name"], "report_id": d["report_id"],
            "patient": d["patient"]["name"] or d["patient"]["id"],
            "rows": v["rows"], "pass": v["pass"], "warn": v["warn"], "fail": v["fail"], "report_status": v["status"],
            "structured_by": "LLM" if by_llm else "Rules",
        })
    return pd.DataFrame(out, columns=SUMMARY_COLUMNS)


def csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


def json_bytes(docs):
    return json.dumps(docs if isinstance(docs, list) else [docs], indent=2, ensure_ascii=False, default=str).encode("utf-8")


def excel_bytes(results, summary, issues):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in (("Results", results), ("Summary", summary), ("Issues", issues)):
            df.to_excel(xw, index=False, sheet_name=name)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width, 45)
    return buf.getvalue()


def safe_name(s):
    import re
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(s)).strip()


def zip_bytes(docs, names):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in docs:
            base = names[d["report_id"]] + "_standardized"
            res, summ, iss = results_frame([d]), summary_frame([d]), issues_frame([d])
            z.writestr(base + ".csv", csv_bytes(res))
            z.writestr(base + ".xlsx", excel_bytes(res, summ, iss))
            z.writestr(base + ".json", json_bytes(d))
    return buf.getvalue()
