import os
import shutil
import tempfile

import pandas as pd
import yaml

from .classify import classify_file
from .export import output_names
from .extract import extract_file
from .pipeline import build_tools, standardize_frame, structure_extraction
from .refs import PKG_ROOT, load_from_dir
from .standardized import build_report_doc, issues_frame, results_frame, summary_frame, validate_report_json
from .structure import flatten_doc
from .validate import validate_frame


def load_config(root=None):
    with open(os.path.join(root or PKG_ROOT, "config", "pipeline_config.yaml")) as f:
        return yaml.safe_load(f)


class LabPipeline:
    def __init__(self, cfg_d=None, root=None, llm=None, tools=None):
        self.root = root or PKG_ROOT
        self.cfg = cfg_d or load_config(self.root)
        self.llm = llm
        self.mapper, self.units = tools or build_tools(load_from_dir(self.root), self.cfg["thresholds"], self.root)

    def process_bytes(self, name, data):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, os.path.basename(name))
            with open(path, "wb") as f:
                f.write(data)
            return self.process_path(path, display_name=name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def process_path(self, path, display_name=None):
        out = {"file": display_name or os.path.basename(path), "docs": [], "error": None, "route": None}
        try:
            info = classify_file(path, self.cfg)
            info["file_name"] = out["file"]
            out["route"] = info["route"]
            if info["route"] == "unsupported":
                out["error"] = "unsupported file type"
                return out
            ext = extract_file(path, info["route"], self.cfg)
            if ext["status"] != "ok":
                out["error"] = "extraction failed: " + str(ext.get("error"))
                return out
            metas = structure_extraction(info, ext, self.cfg, self.llm)
            if not metas:
                out["error"] = "no test results were found in the file"
                return out
            rows = []
            for m in metas:
                rows += flatten_doc(m)
            df = pd.DataFrame(rows)
            std = standardize_frame(df, self.mapper, self.units)
            val = validate_frame(std, self.mapper, self.cfg["thresholds"])
            by_report = {rid: g.to_dict("records") for rid, g in val.groupby("report_id")}
            for m in metas:
                out["docs"].append(build_report_doc(m, by_report.get(m["report_id"], [])))
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out

    def process_many(self, files):
        docs, errors = [], []
        for name, data in files:
            r = self.process_bytes(name, data)
            if r["error"]:
                errors.append({"file": r["file"], "error": r["error"]})
            docs += r["docs"]
        return build_result(docs, errors)


def build_result(docs, errors):
    names = {}
    if docs:
        names = output_names(pd.DataFrame({"report_id": [d["report_id"] for d in docs], "source_file": [d["source"]["file_name"] for d in docs]}))
    return {
        "docs": docs, "errors": errors, "names": names,
        "results": results_frame(docs), "summary": summary_frame(docs), "issues": issues_frame(docs),
        "schema_errors": {d["report_id"]: validate_report_json(d) for d in docs},
    }
