# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 05 Standardize using LOINC and UCUM (step 6)
# MAGIC Maps test names to the official LOINC table (names, consumer names, related names, unit fit and the official common-test rank) and converts units with the official UCUM definitions only. A unit that is not valid UCUM is kept as written with unit_status unit_unrecognized and is not converted. A mapping that is not clear is not used. The proposed code is kept for information.

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
from labstd.schemas import to_spark
from labstd.pipeline import build_tools, standardize_frame
from labstd.refs import load_from_spark
th = cfg.d["thresholds"]
mapper, units = build_tools(load_from_spark(spark, cfg), th)
todo = spark.sql(f"SELECT r.* FROM {cfg.t('silver_rows')} r LEFT ANTI JOIN {cfg.t('silver_standardized')} s ON r.row_key = s.row_key").toPandas()
print(len(todo), "rows to standardize")

# COMMAND ----------

if len(todo):
    std = standardize_frame(todo, mapper, units)
    to_spark(spark, std, "silver_standardized").write.mode("append").saveAsTable(cfg.t("silver_standardized"))
display(spark.table(cfg.t("silver_standardized")).select("raw_test_name", "loinc_num", "loinc_score", "loinc_method", "loinc_status", "loinc_proposed", "raw_unit", "unit_status", "unit_ucum", "value_num", "value_std", "canonical_unit", "convert_status"))