import os
import re
import shutil
import tempfile

import pandas as pd

# The Structure-step output: one simple, readable table per report, exactly as the
# report was read - before any LOINC/UCUM standardization. Internal fields used
# only to standardize later (parsed numeric value, comparator, parsed reference
# bounds, HL7 source codes, extraction confidence) are kept out of this table;
# "structured_by" is the one addition, recording whether the LLM (verified against
# the report text) or the built-in rules produced this row.
COLUMNS = [
    ("report_id", "report_id"), ("source_file", "source_file"),
    ("patient_id", "patient_id"), ("patient_name", "patient_name"), ("sex", "sex"), ("dob", "dob"),
    ("collected_at", "collected_at"), ("lab_name", "lab_name"), ("raw_test_name", "test_name"), ("raw_value", "result_value"),
    ("raw_unit", "unit"), ("raw_ref", "reference_range"), ("raw_flag", "flag"), ("structured_by", "structured_by"),
]


def safe(s):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(s)).strip()


def output_names(reports):
    df = reports[["report_id", "source_file"]].drop_duplicates().copy()
    df["file_key"] = df["report_id"].str.rsplit("-", n=1).str[0]
    df["idx"] = df["report_id"].str.rsplit("-", n=1).str[1].astype(int)
    df["stem"] = df["source_file"].map(lambda f: safe(os.path.splitext(f)[0]))
    df["ext"] = df["source_file"].map(lambda f: os.path.splitext(f)[1].lstrip(".").lower())
    files = df[["file_key", "stem", "ext", "source_file"]].drop_duplicates("file_key")
    base = {}
    for stem, g in files.groupby("stem"):
        keys = list(g["file_key"])
        if len(keys) == 1:
            base[keys[0]] = stem
            continue
        ext_counts = g["ext"].value_counts().to_dict()
        for r in g.itertuples():
            name = f"{stem}_{r.ext}" if r.ext and ext_counts[r.ext] == 1 else f"{stem}_{r.ext}_{r.file_key[:8]}" if r.ext else f"{stem}_{r.file_key[:8]}"
            base[r.file_key] = name
    counts = df.groupby("file_key")["report_id"].nunique().to_dict()
    names = {}
    for r in df.sort_values(["file_key", "idx"]).itertuples():
        pos = list(df[df.file_key == r.file_key].sort_values("idx")["report_id"]).index(r.report_id) + 1
        names[r.report_id] = base[r.file_key] if counts[r.file_key] == 1 else f"{base[r.file_key]}_{pos}"
    return names


def report_frame(g):
    g = g.sort_values("row_idx")
    return g[[c for c, _ in COLUMNS if c in g.columns]].rename(columns=dict(COLUMNS))


def _write_xlsx(df, path):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="structured")
        ws = xw.sheets["structured"]
        ws.freeze_panes = "A2"
        for i, col in enumerate(df.columns, start=1):
            width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width, 45)


def export_structured(rows_df, out_dir, names, report_ids=None):
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp()
    written = []
    try:
        for rid, g in rows_df.groupby("report_id", sort=False):
            if report_ids is not None and rid not in report_ids:
                continue
            frame = report_frame(g)
            base = names[rid] + "_structured"
            for ext, writer in (("csv", lambda d, p: d.to_csv(p, index=False)), ("xlsx", _write_xlsx)):
                local = os.path.join(tmp, f"{base}.{ext}")
                writer(frame, local)
                shutil.copyfile(local, os.path.join(out_dir, f"{base}.{ext}"))
                written.append(f"{base}.{ext}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return written


def missing_report_ids(names, out_dir):
    missing = set()
    for rid, base in names.items():
        for ext in ("csv", "xlsx"):
            if not os.path.exists(os.path.join(out_dir, f"{base}_structured.{ext}")):
                missing.add(rid)
    return missing
