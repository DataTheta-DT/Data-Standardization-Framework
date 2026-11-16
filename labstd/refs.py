import os

import pandas as pd

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = {"loinc": ("official", "loinc_lab_active.csv")}

TABLES = {"loinc": "ref_loinc"}


def ucum_paths(root=None):
    d = os.path.join(root or PKG_ROOT, "reference", "official", "ucum")
    return os.path.join(d, "ucum-cs.units"), os.path.join(d, "ucum-ci.units"), os.path.join(d, "ucum-essence.xml")


def load_from_dir(root=None):
    root = root or PKG_ROOT
    out = {}
    for key, (sub, fn) in FILES.items():
        out[key] = pd.read_csv(os.path.join(root, "reference", sub, fn), dtype=str, keep_default_na=False)
    return out


def load_from_spark(spark, cfg):
    return {key: spark.table(cfg.t(TABLES[key])).toPandas() for key in FILES}
