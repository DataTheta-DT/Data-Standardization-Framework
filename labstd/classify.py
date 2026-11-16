import hashlib
import os
from datetime import datetime

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_format(path):
    with open(path, "rb") as f:
        head = f.read(512)
    ext = os.path.splitext(path)[1].lower()
    stripped = head.lstrip()
    if stripped.startswith(b"%PDF"):
        return "pdf"
    if stripped.startswith(b"MSH|"):
        return "hl7"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:4] in (b"II*\x00", b"MM\x00*") or ext in IMAGE_EXT:
        return "image"
    if ext in {".csv", ".tsv"}:
        return "csv"
    return "unknown"


def pdf_chars_per_page(path):
    import pymupdf
    doc = pymupdf.open(path)
    if doc.page_count == 0:
        return 0.0
    return sum(len(p.get_text().strip()) for p in doc) / doc.page_count


def route_for(fmt, chars, min_chars):
    if fmt == "hl7":
        return "hl7_parser"
    if fmt == "csv":
        return "csv_parser"
    if fmt == "pdf":
        return "pdf_text" if chars is not None and chars >= min_chars else "ocr"
    if fmt == "image":
        return "ocr"
    return "unsupported"


def classify_file(path, cfg_d):
    fmt = detect_format(path)
    chars = pdf_chars_per_page(path) if fmt == "pdf" else None
    st = os.stat(path)
    return {
        "file_hash": file_sha1(path),
        "file_path": path,
        "file_name": os.path.basename(path),
        "file_size": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime),
        "format": fmt,
        "route": route_for(fmt, chars, cfg_d["ocr"]["min_pdf_chars_per_page"]),
        "pdf_chars_per_page": chars,
        "ingested_at": datetime.utcnow(),
    }
