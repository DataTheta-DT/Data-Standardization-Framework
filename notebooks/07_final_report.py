# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 07 Validate and save final output (step 7, part 2)
# MAGIC Validates every row (step 7) and saves the final tables (step 7's "Save"): the simple `gold_lab_results` table, FHIR R4 resources, and one standardized JSON, CSV and Excel file per report in `standardized_reports`. Each row shows the value as reported next to the standardized LOINC code, value, unit and range, its status (PASS/WARN/FAIL), and a `structured_by` column (LLM or Rules). Gold and FHIR hold PASS and WARN rows only; the files hold every row with its status and reasons.

# COMMAND ----------

# MAGIC %pip install -q openpyxl jsonschema

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)

# COMMAND ----------

import json
import pandas as pd
from datetime import datetime, timezone
from labstd.schemas import to_spark
from labstd.fhir import FHIR_ELIGIBLE, build_bundle, bundle_resources
from labstd.structure import structured_by_label
from labstd.validate import issues_to_text, records
all_rows = records(spark.table(cfg.t("silver_results")).toPandas())
now = datetime.now(timezone.utc)
print(len(all_rows), "rows total")

# COMMAND ----------

# Each report's doc_json (written by 04_structure) carries, per row, whether the
# LLM (verified against the report text) or the built-in rules produced it. Attach
# that to every row here so both the gold table and the standardized files below
# can show a plain "structured_by" column.
metas = {}
for d in spark.table(cfg.t("silver_docs")).select("report_id", "doc_json").collect():
    doc = json.loads(d["doc_json"] or "{}")
    if doc:
        metas[d["report_id"]] = doc
extraction_method = {(rid, res["row_idx"]): res.get("extraction_method", "rules") for rid, m in metas.items() for res in m["results"]}
for r in all_rows:
    r["extraction_method"] = extraction_method.get((r["report_id"], r["row_idx"]), "rules")
    r["structured_by"] = structured_by_label(r["extraction_method"])
rows = [r for r in all_rows if r["status"] in FHIR_ELIGIBLE]

# COMMAND ----------

# The final, simple standardized table: only what a clinician or data consumer
# needs (patient, test, reported and standardized value/unit/range, status), plus
# "structured_by". Internal diagnostics stay in the standardized JSON files below.
gold = []
for r in rows:
    gold.append({
        "report_id": r["report_id"], "patient_id": r["patient_id"], "patient_name": r["patient_name"], "sex": r["sex"],
        "dob": r["dob"], "collected_at": r["collected_at"], "lab_name": r["lab_name"], "source_file": r["source_file"],
        "test_name": r["raw_test_name"], "reported_value": r["raw_value"], "reported_unit": r["raw_unit"], "reference_range": r["raw_ref"],
        "loinc_num": r["loinc_num"], "loinc_name": r["loinc_name"], "standardized_value": r["value_std"], "standardized_unit": r["canonical_unit"],
        "ref_low": r["ref_low_std"], "ref_high": r["ref_high_std"], "flag": r["flag_derived"],
        "status": r["status"], "confidence": r["confidence"], "issues": issues_to_text(r["issues"]),
        "structured_by": r["structured_by"], "built_at": now,
    })
if gold:
    to_spark(spark, pd.DataFrame(gold), "gold_results").write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(cfg.t("gold_results"))
display(spark.table(cfg.t("gold_results")).select("patient_id", "collected_at", "test_name", "loinc_num", "loinc_name", "standardized_value", "standardized_unit", "ref_low", "ref_high", "flag", "status", "structured_by"))

# COMMAND ----------

by_report = {}
for r in rows:
    by_report.setdefault(r["report_id"], []).append(r)
fhir_dir = cfg.vol(cfg.folders["fhir"])
os.makedirs(fhir_dir, exist_ok=True)
fhir_rows = []
for rid, rs in by_report.items():
    bundle = build_bundle(rs)
    with open(os.path.join(fhir_dir, f"{rid}.json"), "w") as f:
        json.dump(bundle, f, indent=1)
    for rtype, rid2, js in bundle_resources(bundle):
        fhir_rows.append({"report_id": rid, "resource_type": rtype, "resource_id": rid2, "resource_json": js, "built_at": now})
if fhir_rows:
    to_spark(spark, pd.DataFrame(fhir_rows), "gold_fhir").write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(cfg.t("gold_fhir"))
print(len(by_report), "bundles written to", fhir_dir)

# COMMAND ----------

import shutil, tempfile
from labstd.export import output_names
from labstd.standardized import build_report_doc, csv_bytes, excel_bytes, issues_frame, json_bytes, results_frame, summary_frame
grouped = {}
for r in all_rows:
    grouped.setdefault(r["report_id"], []).append(r)
docs = [build_report_doc(metas[rid], grouped.get(rid, [])) for rid in metas]
out_dir = cfg.vol(cfg.folders["standardized"])
os.makedirs(out_dir, exist_ok=True)
names = output_names(pd.DataFrame({"report_id": [d["report_id"] for d in docs], "source_file": [d["source"]["file_name"] for d in docs]})) if docs else {}
tmp = tempfile.mkdtemp()
count = 0
for d in docs:
    base = names[d["report_id"]] + "_standardized"
    res, summ, iss = results_frame([d]), summary_frame([d]), issues_frame([d])
    files = {base + ".csv": csv_bytes(res), base + ".xlsx": excel_bytes(res, summ, iss), base + ".json": json_bytes(d)}
    for fname, data in files.items():
        local = os.path.join(tmp, fname)
        with open(local, "wb") as f:
            f.write(data)
        shutil.copyfile(local, os.path.join(out_dir, fname))
        count += 1
shutil.rmtree(tmp, ignore_errors=True)
print(count, "files written to", out_dir)

# COMMAND ----------

display(spark.sql(f"SELECT resource_type, count(*) AS resources FROM {cfg.t('gold_fhir')} GROUP BY resource_type"))