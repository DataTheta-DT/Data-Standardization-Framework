# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 01 Input data (step 2)
# MAGIC Copies your three real sample reports and writes six synthetic reports (digital PDF, scanned PNG and JPG, image-only PDF, CSV, HL7) into the raw volume.

# COMMAND ----------

# MAGIC %pip install -q reportlab

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys
ROOT = os.path.dirname(os.getcwd())
sys.path.insert(0, ROOT)
from labstd.config import load_cfg
cfg = load_cfg(ROOT)

# COMMAND ----------

from labstd.samples import write_all
import shutil
raw = cfg.vol(cfg.folders["raw"])
real = os.path.join(ROOT, "sample_inputs", "real")
for f in sorted(os.listdir(real)):
    if not os.path.exists(os.path.join(raw, f)):
        shutil.copyfile(os.path.join(real, f), os.path.join(raw, f))
print(write_all(raw))