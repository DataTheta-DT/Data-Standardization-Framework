"""Run the same report text through several LLM providers, one provider at a time, and line the results up.

Each provider is called on its own (no fallback chain), its answer goes through the same verification the pipeline uses
(every value must appear in the source text), and the verified rows are compared with the rule-based parser and with the
other providers. Nothing here writes to the pipeline tables.

An item is a dict: {"name", "route", "text", "tables_json", "conf"}.
"""
import glob
import json
import os
import time

import pandas as pd

from .llm import _key, verify
from .parsers import parse_extraction
from .pipeline import llm_applies

RULES = "rules"


def items_from_paths(paths, cfg_d):
    """Reads files (or folders) into items. CSV and HL7 are left out: they are parsed directly and never reach an LLM."""
    from .classify import classify_file
    from .extract import extract_file
    files = []
    for p in paths:
        files += sorted(f for f in glob.glob(os.path.join(p, "**", "*"), recursive=True) if os.path.isfile(f)) if os.path.isdir(p) else [p]
    items, skipped = [], []
    for f in files:
        info = classify_file(f, cfg_d)
        if info["route"] not in ("pdf_text", "ocr"):
            skipped.append((os.path.basename(f), f"route {info['route']}: not sent to an LLM"))
            continue
        ext = extract_file(f, info["route"], cfg_d)
        if ext["status"] != "ok" or not llm_applies(info["route"], ext.get("text")):
            skipped.append((os.path.basename(f), "no text could be extracted: " + str(ext.get("error"))))
            continue
        items.append({"name": os.path.basename(f), "route": info["route"], "text": ext["text"], "tables_json": ext.get("tables_json"), "conf": ext.get("conf")})
    return items, skipped


def _rule_rows(item):
    tables = json.loads(item["tables_json"]) if item.get("tables_json") else []
    rows = []
    for rep in parse_extraction(item["route"], item["text"], tables, item.get("conf")):
        rows += rep["rows"]
    return rows


def _patient_fields(report):
    p = (report or {}).get("patient") or {}
    return sum(1 for k in ("id", "name", "sex", "age_years", "dob") if p.get(k) not in (None, ""))


def run_one(client, item):
    """One provider on one item. Never raises: a failure is a result."""
    rec = {"file": item["name"], "provider": client.provider, "model": client.model, "status": "ok", "seconds": 0.0, "calls": 0, "error": None, "rows": [], "dropped": 0, "patient_fields": 0}
    if not client.enabled:
        rec.update(status="skipped", error=client.status())
        return rec
    if client.blocked_reason:
        rec.update(status="skipped", error="blocked earlier in this run: " + client.blocked_reason[:200])
        return rec
    calls0, t0 = client.stats["calls"], time.time()
    try:
        raw = client.extract(item["text"])
        if raw is None:
            raise RuntimeError("the model answered but not with JSON that could be read")
        report = verify(raw, item["text"], item.get("conf"))
        rec.update(rows=report["rows"], dropped=report["dropped"], patient_fields=_patient_fields(report), proposed=len(raw.get("results") or []))
    except Exception as e:
        from .llm import describe_error
        rec.update(status="failed", error=describe_error(e)[:400])
    rec.update(seconds=round(time.time() - t0, 2), calls=client.stats["calls"] - calls0, model=client.active_model or client.model)
    return rec


def compare(items, clients, progress=None):
    """Returns {"runs", "rows", "summary"} as DataFrames, plus "details" (the raw run records)."""
    details = []
    for item in items:
        base = {"file": item["name"], "provider": RULES, "model": None, "status": "ok", "seconds": 0.0, "calls": 0, "error": None, "dropped": 0, "patient_fields": 0}
        details.append(dict(base, rows=_rule_rows(item)))
        for c in clients:
            if progress:
                progress(f"{item['name']}  ->  {c.provider}")
            details.append(run_one(c, item))
    return {"details": details, **_tables(details)}


def _tables(details):
    by_file = {}
    for d in details:
        by_file.setdefault(d["file"], []).append(d)
    run_rows, matrix = [], []
    for fname, runs in by_file.items():
        ok = [r for r in runs if r["status"] == "ok"]
        keysets = {r["provider"]: {_key(x) for x in r["rows"]} for r in ok}
        consensus = {k for k in set().union(*keysets.values()) if sum(k in s for s in keysets.values()) * 2 >= len(keysets)} if keysets else set()
        labels = {}
        for r in ok:
            for x in r["rows"]:
                labels.setdefault(_key(x), x)
        for k, x in labels.items():
            line = {"file": fname, "test_name": x["raw_test_name"], "value": x["raw_value"], "found_by_count": sum(k in s for s in keysets.values()), "consensus": k in consensus}
            for prov, s in keysets.items():
                line[prov] = k in s
            line["units_seen"] = " | ".join(sorted({str(y["raw_unit"]) for r in ok for y in r["rows"] if _key(y) == k and y["raw_unit"]}))
            matrix.append(line)
        for r in runs:
            mine = keysets.get(r["provider"], set())
            others = [s for p, s in keysets.items() if p != r["provider"]]
            rules = keysets.get(RULES, set())
            run_rows.append({
                "file": fname, "provider": r["provider"], "model": r["model"], "status": r["status"], "seconds": r["seconds"], "calls": r["calls"],
                "rows": len(r["rows"]) if r["status"] == "ok" else None,
                "dropped_unverifiable": r["dropped"] if r["provider"] != RULES else None,
                "with_unit": sum(1 for x in r["rows"] if x["raw_unit"]) if r["status"] == "ok" else None,
                "with_ref": sum(1 for x in r["rows"] if x["raw_ref"]) if r["status"] == "ok" else None,
                "with_flag": sum(1 for x in r["rows"] if x["raw_flag"]) if r["status"] == "ok" else None,
                "patient_fields": r["patient_fields"] if r["provider"] != RULES else None,
                "also_found_by_others": len([k for k in mine if any(k in s for s in others)]) if r["status"] == "ok" and others else None,
                "found_by_rules": len(mine & rules) if r["status"] == "ok" and r["provider"] != RULES else None,
                "consensus_rows_found": len(mine & consensus) if r["status"] == "ok" else None,
                "consensus_rows_total": len(consensus),
                "error": r["error"],
            })
    runs_df = pd.DataFrame(run_rows)
    rows_df = pd.DataFrame(matrix)
    return {"runs": runs_df, "rows": rows_df, "summary": summarize(runs_df)}


def summarize(runs):
    """One line per provider across all files."""
    if runs is None or not len(runs):
        return pd.DataFrame()
    out = []
    for prov, g in runs.groupby("provider", sort=False):
        ok = g[g["status"] == "ok"]
        found, total = ok["consensus_rows_found"].sum(), ok["consensus_rows_total"].sum()
        rows = ok["rows"].sum()
        corroborated = ok["also_found_by_others"].sum()
        out.append({
            "provider": prov, "model": next((m for m in g["model"] if m), None),
            "files_ok": len(ok), "files_failed": int((g["status"] == "failed").sum()), "files_skipped": int((g["status"] == "skipped").sum()),
            "rows": int(rows) if len(ok) else 0,
            "dropped_unverifiable": int(ok["dropped_unverifiable"].fillna(0).sum()) if len(ok) else 0,
            "rows_also_found_elsewhere_pct": round(100 * corroborated / rows, 1) if rows and ok["also_found_by_others"].notna().any() else None,
            "consensus_coverage_pct": round(100 * found / total, 1) if total else None,
            "seconds_total": round(float(ok["seconds"].sum()), 1), "seconds_per_file": round(float(ok["seconds"].mean()), 2) if len(ok) else None,
            "last_error": next((e for e in g["error"].dropna()), None),
        })
    return pd.DataFrame(out)


def export(result, out_dir, stem="llm_provider_comparison"):
    """Writes the three tables as CSV and one Excel workbook. Returns the paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name in ("summary", "runs", "rows"):
        p = os.path.join(out_dir, f"{stem}_{name}.csv")
        result[name].to_csv(p, index=False)
        paths.append(p)
    xp = os.path.join(out_dir, f"{stem}.xlsx")
    with pd.ExcelWriter(xp, engine="openpyxl") as xw:
        for name in ("summary", "runs", "rows"):
            result[name].to_excel(xw, index=False, sheet_name=name.capitalize())
            ws = xw.sheets[name.capitalize()]
            ws.freeze_panes = "A2"
    paths.append(xp)
    return paths
