# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 08 Compare LLM providers (optional)
# MAGIC Runs the same extracted report text through each configured LLM provider, one provider at a time (no fallback chain), and lines the answers up next to the rule-based parser. Every LLM value goes through the same check as in step 5: it has to appear in the source text, otherwise it is dropped and counted. Nothing is written to the pipeline tables; the comparison tables are saved as CSV and Excel in the `llm_comparison` folder of the volume.
# MAGIC
# MAGIC Run notebook 03 first (the text comes from `bronze_extractions`). Each provider costs one call per ~9,000 characters of report text per file, so start with a few files. Widgets: `providers` (comma separated, empty = every provider that has a key), `max_files`, `ping_first` (a tiny test call per provider to check keys and models).

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
dbutils.widgets.text("providers", "")
dbutils.widgets.text("max_files", "5")
dbutils.widgets.dropdown("ping_first", "yes", ["yes", "no"])

# COMMAND ----------

from labstd.llm import all_key_vars
from labstd.llm_router import build_llm
from labstd.env import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
for var in all_key_vars():
    if not os.environ.get(var):
        for secret in ([var.lower()] + (["api_key"] if var == "LLM_API_KEY" else [])):
            try:
                os.environ[var] = dbutils.secrets.get("lab_llm", secret)
                break
            except Exception:
                pass
wanted = [p.strip() for p in dbutils.widgets.get("providers").split(",") if p.strip()]
router = build_llm(cfg.d.get("llm"), only=wanted or None)
clients = [c for c in router.clients if c.enabled]
print(router.status())
if not clients:
    raise Exception("No provider has an API key. Fill in .env (or the lab_llm secrets) and run again.")

# COMMAND ----------

import pandas as pd
if dbutils.widgets.get("ping_first") == "yes":
    display(pd.DataFrame(router.ping_all()))

# COMMAND ----------

q = f"""SELECT b.file_name, b.route, e.text, e.tables_json, e.engine_conf AS conf
FROM {cfg.t('bronze_extractions')} e JOIN {cfg.t('bronze_reports')} b ON e.file_hash = b.file_hash
WHERE e.status = 'ok' AND b.route IN ('pdf_text', 'ocr') AND e.text IS NOT NULL
LIMIT {int(dbutils.widgets.get('max_files') or 5)}"""
items = [{"name": r["file_name"], "route": r["route"], "text": r["text"], "tables_json": r["tables_json"], "conf": r["conf"]} for r in spark.sql(q).collect()]
print(len(items), "files,", len(clients), "providers")

# COMMAND ----------

from labstd.compare import compare, export
result = compare(items, clients, progress=print)
display(result["summary"])

# COMMAND ----------

# MAGIC %md
# MAGIC `runs`: one line per file and provider (time, rows, how many rows the LLM invented and the check dropped, rows also found elsewhere). `rows`: one line per test result and which providers found it, so you can see exactly where they disagree.

# COMMAND ----------

display(result["runs"])

# COMMAND ----------

display(result["rows"])