import json


def issues_to_text(issues_json):
    """Turn the stored JSON list of issues into one short, readable sentence for
    the simple tables (e.g. 'loinc_unmapped (Hemoglobin); ref_range_missing')."""
    try:
        issues = json.loads(issues_json or "[]")
    except (TypeError, ValueError):
        return ""
    return "; ".join(f"{i['code']}" + (f" ({i['detail']})" if i.get("detail") else "") for i in issues)


def records(df):
    return df.astype(object).where(df.notna(), None).to_dict("records")


def derive_flag(v, lo, hi, comparator=None):
    if v is None or comparator:
        return None
    if lo is not None and v < lo:
        return "L"
    if hi is not None and v > hi:
        return "H"
    if lo is None and hi is None:
        return None
    return "N"


def standardize_row(r, mapper, units):
    interps = units.interpretations(r.get("raw_unit"))
    m = mapper.map(r.get("raw_test_name"), r.get("source_code"), r.get("source_system"), interps)
    r.update(m)
    r["loinc_proposed"] = None
    if m["loinc_status"] != "auto":
        r["loinc_proposed"] = m["loinc_num"]
        r["loinc_num"] = None
        r["loinc_name"] = None
    canon = mapper.canonical_unit(r["loinc_num"]) if r["loinc_num"] else None
    r["unit_ucum"] = units.pick(interps, canon) if interps else None
    if not r.get("raw_unit") or not str(r["raw_unit"]).strip():
        r["unit_status"] = "unit_missing"
    elif not interps:
        r["unit_status"] = "unit_unrecognized"
    else:
        r["unit_status"] = "recognized"
    return convert_row(r, mapper, units)


def convert_row(r, mapper, units):
    loinc = r.get("loinc_num")
    r["canonical_unit"] = mapper.canonical_unit(loinc) if loinc else None
    r["value_std"] = r["ref_low_std"] = r["ref_high_std"] = None
    if r.get("value_num") is None:
        r["convert_status"] = "no_value"
    elif not loinc:
        r["convert_status"] = "no_loinc"
    elif not r.get("raw_unit"):
        r["convert_status"] = "no_unit"
    elif r.get("unit_status") == "unit_unrecognized" or not r.get("unit_ucum"):
        r["convert_status"] = "unit_unrecognized"
    elif not r["canonical_unit"]:
        r["convert_status"] = "no_canonical_unit"
    else:
        v, st = units.convert(r["value_num"], r["unit_ucum"], r["canonical_unit"])
        r["convert_status"] = st
        if v is not None:
            r["value_std"] = v
            if r.get("ref_low") is not None:
                r["ref_low_std"], _ = units.convert(r["ref_low"], r["unit_ucum"], r["canonical_unit"])
            if r.get("ref_high") is not None:
                r["ref_high_std"], _ = units.convert(r["ref_high"], r["unit_ucum"], r["canonical_unit"])
    r["flag_derived"] = derive_flag(r["value_std"], r["ref_low_std"], r["ref_high_std"], r.get("comparator"))
    return r


def validate_row(r, th):
    issues = []

    def add(code, sev, detail=""):
        issues.append({"code": code, "severity": sev, "detail": detail})

    if r.get("value_num") is None:
        add("no_numeric_value", "error", str(r.get("raw_value")))
    if not r.get("loinc_num"):
        if r.get("loinc_proposed"):
            add("loinc_uncertain", "error", f"{r.get('loinc_method')} proposed {r.get('loinc_proposed')}")
        else:
            add("loinc_unmapped", "error", str(r.get("raw_test_name")))
    elif float(r.get("loinc_score") or 0) < th.get("loinc_warn_below", 90):
        add("loinc_low_confidence", "warn", f"{r.get('loinc_method')} score {r.get('loinc_score')}")
    if r.get("unit_status") == "unit_unrecognized":
        add("unit_unrecognized", "error", str(r.get("raw_unit")))
    if r.get("value_num") is not None and r.get("loinc_num"):
        st = r.get("convert_status")
        if r.get("unit_status") == "unit_missing":
            add("unit_missing", "error")
        elif st == "no_canonical_unit":
            add("canonical_unit_missing", "error", str(r.get("loinc_num")))
        elif st == "unsupported_mass_mole":
            add("unit_conversion_unsupported", "error", f"mass and molar units cannot be converted ({r.get('unit_ucum')} -> {r.get('canonical_unit')})")
        elif st == "unsupported_conversion":
            add("unit_conversion_unsupported", "error", f"{r.get('unit_ucum')} -> {r.get('canonical_unit')}")
        elif st == "no_path":
            add("unit_incompatible_with_loinc", "error", f"{r.get('unit_ucum')} vs {r.get('canonical_unit')}")
    ec = r.get("extraction_conf")
    if ec is not None and float(ec) < th.get("min_extraction_conf", 0.8):
        add("extraction_low_confidence", "warn", f"{float(ec):.2f}")
    vs = r.get("value_std")
    rl, rh = r.get("ref_low_std"), r.get("ref_high_std")
    if rl is not None and rh is not None and rl >= rh:
        add("ref_range_inverted", "warn", f"{rl}-{rh}")
    if not r.get("raw_ref"):
        add("ref_range_missing", "warn")
    elif r.get("ref_low") is None and r.get("ref_high") is None:
        add("ref_range_unparsed", "warn", str(r.get("raw_ref")))
    if vs is not None and rl is not None and rh is not None and rl < rh:
        mid = (rl + rh) / 2
        if mid > 0 and vs > 0:
            ratio = vs / mid
            if ratio > th["ratio_mismatch"] or ratio < 1 / th["ratio_mismatch"]:
                add("value_ref_scale_mismatch", "warn", f"ratio={ratio:.3g}")
    rep, der = r.get("flag"), r.get("flag_derived")
    if rep in ("H", "L", "N") and der and rep != der:
        add("flag_mismatch", "warn", f"reported={rep} derived={der}")
    if rep == "A" and der == "N":
        add("flag_mismatch", "warn", "reported=A derived=N")
    if not r.get("patient_id") and not r.get("patient_name"):
        add("missing_patient_identity", "warn")
    if not r.get("collected_at"):
        add("missing_collection_time", "warn")
    return issues


def score(r, issues):
    n_err = sum(1 for i in issues if i["severity"] == "error")
    n_warn = sum(1 for i in issues if i["severity"] == "warn")
    ec = r.get("extraction_conf")
    ec = 0.85 if ec is None else float(ec)
    ls = float(r.get("loinc_score") or 0) / 100 if r.get("loinc_num") else 0.5
    conf = ec * ls * (0.9 ** n_warn) * (0.5 ** n_err)
    status = "FAIL" if n_err else ("WARN" if n_warn else "PASS")
    return n_err, n_warn, round(conf, 4), status


def validate_frame(df, mapper, th):
    import pandas as pd
    rows = records(df)
    seen = {}
    for r in rows:
        issues = validate_row(r, th)
        r["_issues"] = issues
        if r.get("loinc_num") and r.get("value_std") is not None:
            seen.setdefault((r["report_id"], r["loinc_num"]), set()).add(r["value_std"])
    for r in rows:
        issues = r.pop("_issues")
        if r.get("loinc_num") and len(seen.get((r["report_id"], r["loinc_num"]), ())) > 1:
            issues.append({"code": "duplicate_analyte_conflict", "severity": "warn", "detail": ""})
        n_err, n_warn, conf, status = score(r, issues)
        r["issues"] = json.dumps(issues)
        r["n_errors"] = n_err
        r["n_warnings"] = n_warn
        r["confidence"] = conf
        r["status"] = status
    return pd.DataFrame(rows)
