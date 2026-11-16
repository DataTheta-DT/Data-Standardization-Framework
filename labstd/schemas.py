from pyspark.sql.types import (BooleanType, DoubleType, IntegerType, LongType, StringType, StructField, StructType, TimestampType)

T = {"s": StringType(), "d": DoubleType(), "i": IntegerType(), "l": LongType(), "t": TimestampType(), "b": BooleanType()}


def _mk(spec):
    return StructType([StructField(n, T[t], True) for n, t in spec])


BRONZE_REPORTS = [("file_hash", "s"), ("file_path", "s"), ("file_name", "s"), ("file_size", "l"), ("modified_at", "t"), ("format", "s"), ("route", "s"), ("pdf_chars_per_page", "d"), ("ingested_at", "t")]
BRONZE_EXTRACTIONS = [("file_hash", "s"), ("file_path", "s"), ("route", "s"), ("engine", "s"), ("engine_conf", "d"), ("text", "s"), ("tables_json", "s"), ("status", "s"), ("error", "s"), ("extracted_at", "t")]
SILVER_DOCS = [("report_id", "s"), ("file_hash", "s"), ("doc_json", "s"), ("structured_at", "t")]
SILVER_ROWS = [
    ("report_id", "s"), ("row_idx", "i"), ("row_key", "s"), ("source_file", "s"), ("source_format", "s"), ("route", "s"),
    ("patient_id", "s"), ("patient_name", "s"), ("sex", "s"), ("dob", "s"), ("collected_at", "s"), ("lab_name", "s"),
    ("raw_test_name", "s"), ("raw_value", "s"), ("raw_unit", "s"), ("raw_ref", "s"), ("raw_flag", "s"),
    ("source_code", "s"), ("source_system", "s"), ("value_num", "d"), ("comparator", "s"), ("ref_low", "d"), ("ref_high", "d"),
    ("flag", "s"), ("extraction_conf", "d"), ("structured_by", "s"),
]
STD_COLS = [
    ("loinc_proposed", "s"), ("loinc_num", "s"), ("loinc_name", "s"), ("loinc_score", "d"), ("loinc_method", "s"), ("loinc_status", "s"), ("loinc_candidates", "s"),
    ("unit_status", "s"), ("unit_ucum", "s"), ("canonical_unit", "s"), ("value_std", "d"), ("ref_low_std", "d"), ("ref_high_std", "d"), ("convert_status", "s"), ("flag_derived", "s"),
]
VAL_COLS = [("issues", "s"), ("n_errors", "i"), ("n_warnings", "i"), ("confidence", "d"), ("status", "s"), ("processed_at", "t")]
SILVER_STANDARDIZED = SILVER_ROWS + STD_COLS
SILVER_RESULTS = SILVER_STANDARDIZED + VAL_COLS
# The final, standardized table. Kept deliberately simple: only what a clinician
# or data consumer needs, plus a "structured_by" column recording whether this
# report's rows were read by the LLM (and verified) or by the built-in rules.
GOLD_RESULTS = [
    ("report_id", "s"), ("patient_id", "s"), ("patient_name", "s"), ("sex", "s"), ("dob", "s"), ("collected_at", "s"), ("lab_name", "s"),
    ("source_file", "s"), ("test_name", "s"), ("reported_value", "s"), ("reported_unit", "s"), ("reference_range", "s"),
    ("loinc_num", "s"), ("loinc_name", "s"), ("standardized_value", "d"), ("standardized_unit", "s"), ("ref_low", "d"), ("ref_high", "d"),
    ("flag", "s"), ("status", "s"), ("confidence", "d"), ("issues", "s"), ("structured_by", "s"), ("built_at", "t"),
]
GOLD_FHIR = [("report_id", "s"), ("resource_type", "s"), ("resource_id", "s"), ("resource_json", "s"), ("built_at", "t")]
REF_LOINC = [("loinc_num", "s"), ("long_common_name", "s"), ("shortname", "s"), ("component", "s"), ("property", "s"), ("time_aspct", "s"), ("system", "s"), ("scale_typ", "s"), ("method_typ", "s"), ("class", "s"), ("canonical_unit", "s"), ("common_test_rank", "i"), ("relatednames", "s"), ("consumer_names", "s"), ("status", "s"), ("version_last_changed", "s")]

SPECS = {
    "bronze_reports": BRONZE_REPORTS, "bronze_extractions": BRONZE_EXTRACTIONS, "silver_docs": SILVER_DOCS, "silver_rows": SILVER_ROWS,
    "silver_standardized": SILVER_STANDARDIZED, "silver_results": SILVER_RESULTS,
    "gold_results": GOLD_RESULTS, "gold_fhir": GOLD_FHIR, "ref_loinc": REF_LOINC,
}


def schema_for(name):
    return _mk(SPECS[name])


def columns_for(name):
    return [n for n, _ in SPECS[name]]


def clean_records(pdf, name):
    import math
    import numpy as np
    import pandas as pd
    cols = columns_for(name)
    types = dict(SPECS[name])
    out = []
    for rec in pdf.reindex(columns=cols).to_dict("records"):
        row = {}
        for c in cols:
            v = rec[c]
            if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT or (not isinstance(v, (list, dict)) and pd.isna(v)):
                row[c] = None
                continue
            if isinstance(v, np.generic):
                v = v.item()
            t = types[c]
            if t == "d":
                v = float(v)
            elif t in ("i", "l"):
                v = int(v)
            elif t == "s":
                v = str(v)
            elif t == "t" and hasattr(v, "to_pydatetime"):
                v = v.to_pydatetime()
            row[c] = v
        out.append(row)
    return out


def to_spark(spark, pdf, name):
    return spark.createDataFrame(clean_records(pdf, name), schema=schema_for(name))
