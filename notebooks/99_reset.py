# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 99 Reset
# MAGIC Drops every table and view of this project (including tables of earlier versions) and empties the output folders in the volume, so you can start fresh. The schema, the volume and the access grants stay. Tables of other projects are not touched.
# MAGIC
# MAGIC Set `confirm` to `RESET` to run. Set `delete_raw_files` to `yes` only if you also want to delete the files in `raw_reports`.

# COMMAND ----------

import os, sys, shutil
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)
dbutils.widgets.text("confirm", "")
dbutils.widgets.dropdown("delete_raw_files", "no", ["no", "yes"])

# COMMAND ----------

if dbutils.widgets.get("confirm") != "RESET":
    raise Exception("Nothing was deleted. Set the confirm widget to RESET and run this notebook again.")
spark.sql(f"DROP VIEW IF EXISTS {cfg.catalog}.{cfg.schema}.review_queue")
legacy = ["review_decisions", "ref_unit_aliases", "ref_unit_conversions", "ref_test_aliases", "ref_limits", "ref_molar_mass"]
for name in list(cfg.d["tables"].values()) + legacy:
    spark.sql(f"DROP TABLE IF EXISTS {cfg.catalog}.{cfg.schema}.{name}")
    print("dropped", name)

# COMMAND ----------

folders = [cfg.folders["fhir"], cfg.folders["structured"], cfg.folders["standardized"]]
if dbutils.widgets.get("delete_raw_files") == "yes":
    folders.append(cfg.folders["raw"])
for f in folders:
    path = cfg.vol(f)
    if not os.path.isdir(path):
        continue
    n = 0
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        n += 1
    print("cleared", n, "items in", path)

# COMMAND ----------

left = [r["tableName"] for r in spark.sql(f"SHOW TABLES IN {cfg.catalog}.{cfg.schema}").collect()]
print("tables left in the schema:", left)