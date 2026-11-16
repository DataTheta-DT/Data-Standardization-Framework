# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 06 Validate (step 7, part 1)
# MAGIC Checks completeness, extraction confidence, LOINC mapping, units and consistency with fixed rules. Every row gets PASS, WARN or FAIL and a list of reasons. There is no review queue.

# COMMAND ----------

# MAGIC %pip install -q rapidfuzz

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)

# COMMAND ----------

import pandas as pd
from datetime import datetime
from labstd.schemas import to_spark
from labstd.pipeline import build_tools
from labstd.refs import load_from_spark
from labstd.validate import validate_frame
th = cfg.d["thresholds"]
mapper, units = build_tools(load_from_spark(spark, cfg), th)
todo = spark.sql(f"SELECT s.* FROM {cfg.t('silver_standardized')} s LEFT ANTI JOIN {cfg.t('silver_results')} r ON s.row_key = r.row_key").toPandas()
print(len(todo), "rows to validate")

# COMMAND ----------

if len(todo):
    res = validate_frame(todo, mapper, th)
    res["processed_at"] = datetime.utcnow()
    to_spark(spark, res, "silver_results").write.mode("append").saveAsTable(cfg.t("silver_results"))
display(spark.sql(f"SELECT status, route, count(*) AS rows FROM {cfg.t('silver_results')} GROUP BY status, route ORDER BY route, status"))