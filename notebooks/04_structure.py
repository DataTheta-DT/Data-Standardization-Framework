# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 04 Structure using LLM/rules (step 5)
# MAGIC Converts the extracted text into one common JSON document per report and a flat row table. The LLM settings are in config/pipeline_config.yaml (llm.providers: Hugging Face, Gemini, Groq, OpenRouter, OpenAI, Ollama, Databricks, any OpenAI-compatible endpoint). Providers are tried in the listed order for every file; one without an API key is skipped and one that fails hands over to the next. API keys come from environment variables, the .env file in the project folder, or Databricks secrets in the scope lab_llm (secret name = the variable name in lower case, for example gemini_api_key; the older lab_llm/api_key still works for LLM_API_KEY). Without any key, rules do the parsing. If there is a key, an LLM structures the PDF and OCR text into JSON and every value is checked against the source text before it is used (LLM_MODE: primary, fallback or assist). To compare providers on the same files, run notebook 08. CSV and HL7 are always parsed directly. It also writes one structured CSV and one Excel file per report to the structured_reports folder, named <original file name>_structured. Reports whose files are missing are written even if they were structured in an earlier run. Set the regenerate_all widget to yes to rebuild the files for every report.

# COMMAND ----------

# MAGIC %pip install -q python-dateutil openpyxl

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)
dbutils.widgets.dropdown("regenerate_all", "no", ["no", "yes"])

# COMMAND ----------

import pandas as pd
from datetime import datetime, timezone
from labstd.schemas import to_spark
from labstd.pipeline import structure_extraction
from labstd.llm import all_key_vars
from labstd.llm_router import build_llm
from labstd.env import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
for var in all_key_vars():  # Databricks secrets fill any key that is not already set
    if not os.environ.get(var):
        for secret in ([var.lower()] + (["api_key"] if var == "LLM_API_KEY" else [])):
            try:
                os.environ[var] = dbutils.secrets.get("lab_llm", secret)
                break
            except Exception:
                pass
llm = build_llm(cfg.d.get("llm"))
print("LLM:", llm.status())
from labstd.structure import doc_to_json, flatten_doc
q = f"""SELECT e.file_hash, e.text, e.tables_json, e.engine, e.engine_conf AS conf, b.file_name, b.format, b.route
FROM {cfg.t('bronze_extractions')} e JOIN {cfg.t('bronze_reports')} b ON e.file_hash = b.file_hash
LEFT ANTI JOIN {cfg.t('silver_docs')} d ON e.file_hash = d.file_hash
WHERE e.status = 'ok'"""
docs_out, rows_out = [], []
for r in spark.sql(q).collect():
    info = {"file_hash": r["file_hash"], "file_name": r["file_name"], "format": r["format"], "route": r["route"]}
    ext = {"text": r["text"], "tables_json": r["tables_json"], "engine": r["engine"], "conf": r["conf"]}
    docs = structure_extraction(info, ext, cfg.d, llm)
    if not docs:
        docs_out.append({"report_id": r["file_hash"][:16] + "-none", "file_hash": r["file_hash"], "doc_json": "{}", "structured_at": datetime.now(timezone.utc)})
    for doc in docs:
        docs_out.append({"report_id": doc["report_id"], "file_hash": r["file_hash"], "doc_json": doc_to_json(doc), "structured_at": datetime.now(timezone.utc)})
        rows_out += flatten_doc(doc)
print(len(docs_out), "reports,", len(rows_out), "rows")

# COMMAND ----------

if docs_out:
    to_spark(spark, pd.DataFrame(docs_out), "silver_docs").write.mode("append").saveAsTable(cfg.t("silver_docs"))
if rows_out:
    to_spark(spark, pd.DataFrame(rows_out), "silver_rows").write.mode("append").saveAsTable(cfg.t("silver_rows"))
display(spark.table(cfg.t("silver_rows")).select("report_id", "raw_test_name", "raw_value", "raw_unit", "raw_ref", "value_num", "ref_low", "ref_high"))

# COMMAND ----------

from labstd.export import export_structured, missing_report_ids, output_names
all_rows = spark.table(cfg.t("silver_rows")).toPandas()
out_dir = cfg.vol(cfg.folders["structured"])
if len(all_rows):
    names = output_names(all_rows)
    new_ids = {r["report_id"] for r in rows_out}
    target = None if dbutils.widgets.get("regenerate_all") == "yes" else new_ids | missing_report_ids(names, out_dir)
    written = export_structured(all_rows, out_dir, names, target)
    print(len(written), "files written to", out_dir)
    print(written[:10])
else:
    print("no rows in silver_extracted_rows yet, nothing to export")