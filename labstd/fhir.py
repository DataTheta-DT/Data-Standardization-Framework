import hashlib
import json
import re

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"
INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
INTERP_DISPLAY = {"N": "Normal", "H": "High", "L": "Low", "A": "Abnormal"}
FHIR_ELIGIBLE = ("PASS", "WARN")


def safe_id(s):
    return re.sub(r"[^A-Za-z0-9\-\.]", "-", str(s))[:64]


def patient_key(r):
    pid = r.get("patient_id")
    if pid:
        return safe_id(pid)
    seed = f"{r.get('patient_name')}|{r.get('dob')}"
    return "p-" + hashlib.sha1(seed.encode()).hexdigest()[:12]


def _qty(v, unit):
    return {"value": v, "unit": unit, "system": UCUM, "code": unit}


def observation(r, pkey):
    obs = {
        "resourceType": "Observation",
        "id": safe_id(r["row_key"].replace("|", "-")),
        "status": "final",
        "category": [{"coding": [{"system": OBS_CAT, "code": "laboratory", "display": "Laboratory"}]}],
        "code": {"coding": [{"system": LOINC, "code": r["loinc_num"], "display": r.get("loinc_name")}], "text": r.get("raw_test_name")},
        "subject": {"reference": f"Patient/{pkey}"},
        "valueQuantity": _qty(r["value_std"], r["canonical_unit"]),
        "meta": {"tag": [{"system": "https://datatheta.example/lab-standardization", "code": r["status"]}]},
    }
    if r.get("collected_at"):
        obs["effectiveDateTime"] = r["collected_at"]
    if r.get("comparator"):
        obs["valueQuantity"]["comparator"] = r["comparator"]
    lo, hi = r.get("ref_low_std"), r.get("ref_high_std")
    if lo is not None or hi is not None:
        rr = {}
        if lo is not None:
            rr["low"] = _qty(lo, r["canonical_unit"])
        if hi is not None:
            rr["high"] = _qty(hi, r["canonical_unit"])
        if r.get("raw_ref"):
            rr["text"] = r["raw_ref"]
        obs["referenceRange"] = [rr]
    flag = r.get("flag_derived") or r.get("flag")
    if flag in INTERP_DISPLAY:
        obs["interpretation"] = [{"coding": [{"system": INTERP, "code": flag, "display": INTERP_DISPLAY[flag]}]}]
    return obs


def patient(r, pkey):
    p = {"resourceType": "Patient", "id": pkey}
    if r.get("patient_id"):
        p["identifier"] = [{"value": r["patient_id"]}]
    if r.get("patient_name"):
        p["name"] = [{"text": r["patient_name"]}]
    if r.get("sex") in ("male", "female", "other", "unknown"):
        p["gender"] = r["sex"]
    if r.get("dob"):
        p["birthDate"] = str(r["dob"])[:10]
    return p


def build_bundle(report_rows):
    first = report_rows[0]
    pkey = patient_key(first)
    obs = [observation(r, pkey) for r in report_rows]
    report = {
        "resourceType": "DiagnosticReport",
        "id": safe_id(first["report_id"]),
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "LAB"}]}],
        "code": {"coding": [{"system": LOINC, "code": "11502-2", "display": "Laboratory report"}]},
        "subject": {"reference": f"Patient/{pkey}"},
        "result": [{"reference": f"Observation/{o['id']}"} for o in obs],
    }
    if first.get("collected_at"):
        report["effectiveDateTime"] = first["collected_at"]
    resources = [patient(first, pkey), report] + obs
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"resource": x, "request": {"method": "PUT", "url": f"{x['resourceType']}/{x['id']}"}} for x in resources],
    }


def bundle_resources(bundle):
    for e in bundle["entry"]:
        x = e["resource"]
        yield x["resourceType"], x["id"], json.dumps(x, ensure_ascii=False)
