import argparse
import csv
import io
import os
import re
import sys
import zipfile

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KEEP = ["LOINC_NUM", "LONG_COMMON_NAME", "SHORTNAME", "COMPONENT", "PROPERTY", "TIME_ASPCT", "SYSTEM", "SCALE_TYP", "METHOD_TYP", "CLASS", "EXAMPLE_UCUM_UNITS", "COMMON_TEST_RANK", "RELATEDNAMES2", "STATUS", "VersionLastChanged"]


def read_loinc(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.endswith("LoincTable/Loinc.csv")][0]
            with z.open(name) as f:
                return pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"), dtype=str, keep_default_na=False)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_consumer_names(path):
    if not path.lower().endswith(".zip"):
        return {}
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith("ConsumerName/ConsumerName.csv")]
        if not names:
            return {}
        with z.open(names[0]) as f:
            cn = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"), dtype=str, keep_default_na=False)
    out = {}
    for code, name in zip(cn["LoincNumber"], cn["ConsumerName"]):
        out.setdefault(code, []).append(name)
    return out


def build_official(loinc_path, out_dir):
    df = read_loinc(loinc_path)
    consumer = read_consumer_names(loinc_path)
    lab = df[(df.CLASSTYPE == "1") & (df.STATUS == "ACTIVE") & (df.SCALE_TYP == "Qn")][KEEP].copy()
    lab["canonical_unit"] = lab["EXAMPLE_UCUM_UNITS"].str.split(";").str[0].str.strip()
    lab["COMMON_TEST_RANK"] = pd.to_numeric(lab["COMMON_TEST_RANK"], errors="coerce").fillna(0).astype(int)
    lab = lab.rename(columns={
        "LOINC_NUM": "loinc_num", "LONG_COMMON_NAME": "long_common_name", "SHORTNAME": "shortname", "COMPONENT": "component",
        "PROPERTY": "property", "TIME_ASPCT": "time_aspct", "SYSTEM": "system", "SCALE_TYP": "scale_typ", "METHOD_TYP": "method_typ",
        "CLASS": "class", "COMMON_TEST_RANK": "common_test_rank", "RELATEDNAMES2": "relatednames", "STATUS": "status",
        "VersionLastChanged": "version_last_changed",
    }).drop(columns=["EXAMPLE_UCUM_UNITS"])
    lab["consumer_names"] = lab["loinc_num"].map(lambda c: " | ".join(consumer.get(c, [])))
    lab = lab[["loinc_num", "long_common_name", "shortname", "component", "property", "time_aspct", "system", "scale_typ", "method_typ", "class", "canonical_unit", "common_test_rank", "relatednames", "consumer_names", "status", "version_last_changed"]]
    lab = lab.sort_values("loinc_num")
    os.makedirs(out_dir, exist_ok=True)
    lab.to_csv(os.path.join(out_dir, "loinc_lab_active.csv"), index=False)
    return df, lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loinc", required=True, help="Loinc zip or LoincTable/Loinc.csv")
    a = ap.parse_args()
    full, lab = build_official(a.loinc, os.path.join(ROOT, "reference", "official"))
    print("official lab rows:", len(lab))


if __name__ == "__main__":
    main()
