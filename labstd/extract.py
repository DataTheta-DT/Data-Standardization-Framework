import json
import os
import subprocess
import sys

from .layout import items_to_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _worker(path, backend, timeout):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([ROOT] + [p for p in sys.path if p])
    env.setdefault("OMP_NUM_THREADS", "2")
    proc = subprocess.run([sys.executable, "-m", "labstd.ocr_worker", path, backend], capture_output=True, text=True, timeout=timeout, env=env, cwd=ROOT)
    marker = "@@RESULT@@"
    if proc.returncode != 0 or marker not in proc.stdout:
        tail = (proc.stderr or proc.stdout or "")[-300:].strip()
        raise RuntimeError(f"OCR worker failed (exit code {proc.returncode}): {tail}")
    return json.loads(proc.stdout.split(marker, 1)[1])


def run_ocr_isolated(path, ocr_cfg, timeout=900):
    backend = ocr_cfg.get("backend", "auto")
    try:
        return _worker(path, backend, timeout)
    except Exception as first:
        if backend != "auto":
            raise
        try:
            out = _worker(path, "onnx", timeout)
            out["fallback_reason"] = str(first)[:200]
            return out
        except Exception as second:
            raise RuntimeError(f"{second} | first attempt: {str(first)[:150]}")


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_pdf_text(path):
    import pymupdf
    doc = pymupdf.open(path)
    pages, tables = [], []
    for page in doc:
        items = [{"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4]} for w in page.get_text("words")]
        pages.append(items_to_text(items))
    return {"text": "\n\n".join(pages), "tables": tables, "engine": "pymupdf", "conf": None}


def extract_file(path, route, cfg_d):
    sc = cfg_d["source_conf"]
    try:
        if route == "hl7_parser":
            out = {"text": read_text(path), "tables": [], "engine": "hl7", "conf": sc["hl7"]}
        elif route == "csv_parser":
            out = {"text": read_text(path), "tables": [], "engine": "csv", "conf": sc["csv"]}
        elif route == "pdf_text":
            out = extract_pdf_text(path)
            out["conf"] = sc["pdf_text"]
        elif route == "ocr":
            out = run_ocr_isolated(path, cfg_d["ocr"])
            if out["conf"] is None:
                out["conf"] = cfg_d["ocr"]["default_conf"]
        else:
            return {"status": "skipped", "error": "unsupported format", "text": None, "tables_json": None, "engine": None, "conf": None}
        out["status"] = "ok"
        out["error"] = out.get("fallback_reason")
        out["tables_json"] = json.dumps(out.pop("tables"))
        out.pop("fallback_reason", None)
        return out
    except Exception as e:
        return {"status": "error", "error": str(e)[:500], "text": None, "tables_json": None, "engine": None, "conf": None}
