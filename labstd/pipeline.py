import glob
import json
import os

import pandas as pd
import yaml

from .classify import classify_file
from .extract import extract_file
from .fhir import FHIR_ELIGIBLE
from .parsers import parse_extraction
from .structure import build_docs, flatten_doc
from .validate import records, standardize_row, validate_frame


def build_tools(refs, th, root=None):
    from .loinc import LoincMapper
    from .refs import ucum_paths
    from .ucum import Ucum
    from .units import UnitStandardizer
    cs, ci, xml = ucum_paths(root)
    units = UnitStandardizer(Ucum(cs, ci, xml))
    mapper = LoincMapper(refs["loinc"], th, units)
    return mapper, units


def load_refs(root, th):
    from .refs import load_from_dir
    return build_tools(load_from_dir(root), th, root)


def llm_applies(route, text):
    """The LLM only reads text from digital PDFs and OCR. CSV and HL7 are parsed directly."""
    return bool(text) and route in ("pdf_text", "ocr")


def structure_extraction(file_info, extraction, cfg_d, llm=None):
    route = file_info["route"]
    tables = json.loads(extraction["tables_json"]) if extraction.get("tables_json") else []
    text = extraction.get("text")
    conf = extraction.get("conf")
    reports = parse_extraction(route, text, tables, conf)
    structuring = "rules"
    info = {"status": "not_applicable", "model": None, "calls": 0, "seconds": 0.0, "error_kind": None, "error": None,
            "detail": "CSV and HL7 files are parsed directly; the LLM only reads PDF text and OCR text."}
    if llm_applies(route, text):
        if llm is None or not llm.enabled:
            why = llm.status() if llm is not None else "no LLM client"
            info.update(status="disabled", detail=why)
        elif getattr(llm, "blocked_reason", None):
            structuring = "rules (llm skipped: " + llm.blocked_reason[:300] + ")"
            info.update(status="skipped", error_kind=llm.stats.get("last_kind"), error=llm.blocked_reason,
                        detail="An earlier file in this run failed with a key or credit problem, so no more calls were made.")
        else:
            from .llm import describe_error, error_kind, merge, verify
            calls0, secs0 = llm.stats["calls"], llm.stats["seconds"]
            try:
                proposed = verify(llm.extract(text), text, conf)
                reports, structuring = merge(reports, proposed, llm.mode)
                provider = getattr(llm, "active_provider", None)
                if llm.active_model:
                    structuring += f" [model {provider + '/' if provider else ''}{llm.active_model}]"
                used = structuring.startswith("llm_") or structuring.startswith("rules+llm")
                info.update(status="applied" if used else "no_effect", detail=structuring,
                            rows_proposed=len(proposed["rows"]) if proposed else 0, rows_dropped=proposed.get("dropped", 0) if proposed else 0)
            except Exception as e:
                structuring = "rules (llm failed: " + describe_error(e)[:400] + ")"
                info.update(status="failed", error_kind=error_kind(e), error=describe_error(e), detail=structuring)
            info.update(model=llm.active_model or llm.model, calls=llm.stats["calls"] - calls0, seconds=round(llm.stats["seconds"] - secs0, 2))
            if getattr(llm, "active_provider", None) is not None or hasattr(llm, "attempts"):
                # which provider answered, and which ones were tried first and failed (empty for a single client)
                info["provider"] = llm.active_provider if info["status"] in ("applied", "no_effect") else None
                info["attempts"] = list(getattr(llm, "attempts", []))
    docs = build_docs(file_info, reports, extraction.get("engine"), structuring)
    for d in docs:
        d["source"]["llm"] = dict(info)
    return docs


def standardize_frame(rows_df, mapper, units):
    out = [standardize_row(r, mapper, units) for r in records(rows_df)]
    return pd.DataFrame(out)


def run_local(files_dir, root, cfg_d):
    ref_dir = root
    th = cfg_d["thresholds"]
    mapper, units = load_refs(ref_dir, th)
    rows = []
    infos = []
    for p in sorted(glob.glob(os.path.join(files_dir, "*"))):
        if not os.path.isfile(p):
            continue
        info = classify_file(p, cfg_d)
        infos.append(info)
        ext = extract_file(p, info["route"], cfg_d)
        if ext["status"] != "ok":
            continue
        for doc in structure_extraction(info, ext, cfg_d):
            rows += flatten_doc(doc)
    std = standardize_frame(pd.DataFrame(rows), mapper, units)
    res = validate_frame(std, mapper, th)
    return pd.DataFrame(infos), res, mapper, units


def eligible(results_df):
    return results_df[results_df["status"].isin(FHIR_ELIGIBLE)]
