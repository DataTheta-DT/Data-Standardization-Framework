# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 00 Setup
# MAGIC Step 1 (Setup): creates the schema, volume and empty Delta tables, and loads the official LOINC and UCUM reference data from `reference/official`.

# COMMAND ----------

import os, sys
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {cfg.catalog}.{cfg.schema}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {cfg.catalog}.{cfg.schema}.{cfg.volume}")
for f in cfg.folders.values():
    os.makedirs(cfg.vol(f), exist_ok=True)

# COMMAND ----------

from labstd.schemas import SPECS, schema_for, to_spark
for name in SPECS:
    if not spark.catalog.tableExists(cfg.t(name)):
        spark.createDataFrame([], schema_for(name)).write.format("delta").saveAsTable(cfg.t(name))

# COMMAND ----------

import pandas as pd
from labstd.refs import FILES, TABLES
for key, (sub, fn) in FILES.items():
    pdf = pd.read_csv(os.path.join(ROOT, "reference", sub, fn), dtype=str, keep_default_na=False)
    to_spark(spark, pdf, TABLES[key]).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(cfg.t(TABLES[key]))
    print(TABLES[key], spark.table(cfg.t(TABLES[key])).count())