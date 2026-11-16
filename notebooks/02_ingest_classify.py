# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02 Read and classify (step 3)
# MAGIC Finds new files in the raw volume, hashes them, detects the format and picks the processing route.

# COMMAND ----------

# MAGIC %pip install -q pymupdf

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)

# COMMAND ----------

import glob
from labstd.schemas import to_spark
from labstd.classify import classify_file
raw = cfg.vol(cfg.folders["raw"])
known = {r["file_hash"] for r in spark.table(cfg.t("bronze_reports")).select("file_hash").collect()}
new = {}
for p in sorted(glob.glob(raw + "/**/*", recursive=True)):
    if os.path.isfile(p):
        info = classify_file(p, cfg.d)
        if info["file_hash"] not in known and info["file_hash"] not in new:
            new[info["file_hash"]] = info
print(len(new), "new files")

# COMMAND ----------

import pandas as pd
if new:
    to_spark(spark, pd.DataFrame(list(new.values())), "bronze_reports").write.mode("append").saveAsTable(cfg.t("bronze_reports"))
display(spark.table(cfg.t("bronze_reports")).select("file_name", "format", "route", "pdf_chars_per_page"))