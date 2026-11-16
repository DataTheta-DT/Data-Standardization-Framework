# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 03 Extract (step 4)
# MAGIC Runs the extractor for each route. Digital PDFs use the text layer, HL7 and CSV are read as text, scans and images go to OCR. OCR uses PP-OCR ONNX models without OpenCV (OpenCV crashes on FIPS-enabled hosts). If paddleocr is installed on a GPU cluster, PaddleOCR-VL is tried first. Set ocr.backend in config to onnx or paddleocr_vl to force one.

# COMMAND ----------

# MAGIC %pip install -q pymupdf onnxruntime scipy pillow

# COMMAND ----------

# MAGIC %pip install -q --no-deps rapidocr-onnxruntime

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
from datetime import datetime
from labstd.schemas import to_spark
from labstd.extract import extract_file
pending = spark.sql(f"SELECT b.* FROM {cfg.t('bronze_reports')} b LEFT ANTI JOIN {cfg.t('bronze_extractions')} e ON b.file_hash = e.file_hash").collect()
pending = sorted(pending, key=lambda r: r["route"] == "ocr")
out = []
for r in pending:
    res = extract_file(r["file_path"], r["route"], cfg.d)
    print(r["route"], res["status"], res.get("error") or "", r["file_path"].split("/")[-1])
    out.append({"file_hash": r["file_hash"], "file_path": r["file_path"], "route": r["route"], "engine": res.get("engine"), "engine_conf": res.get("conf"), "text": res.get("text"), "tables_json": res.get("tables_json"), "status": res["status"], "error": res.get("error"), "extracted_at": datetime.utcnow()})
print(len(out), "files extracted")

# COMMAND ----------

if out:
    to_spark(spark, pd.DataFrame(out), "bronze_extractions").write.mode("append").saveAsTable(cfg.t("bronze_extractions"))
display(spark.table(cfg.t("bronze_extractions")).select("route", "engine", "engine_conf", "status", "error"))