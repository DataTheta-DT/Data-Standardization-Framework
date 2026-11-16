import glob
import importlib.util
import os
import re
import tempfile

from .layout import items_to_text

_VL = None


def _natural(p):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", os.path.basename(p))]


def _paddle_vl(path):
    global _VL
    from paddleocr import PaddleOCRVL
    if _VL is None:
        _VL = PaddleOCRVL()
    out = _VL.predict(path)
    d = tempfile.mkdtemp()
    for res in out:
        res.save_to_markdown(save_path=d)
    parts = []
    for p in sorted(glob.glob(os.path.join(d, "**", "*.md"), recursive=True), key=_natural):
        with open(p, encoding="utf-8") as f:
            parts.append(f.read())
    text = "\n\n".join(parts)
    if not text.strip():
        raise RuntimeError("PaddleOCR-VL returned no text")
    return {"text": text, "tables": [], "engine": "paddleocr_vl", "conf": None}


def _page_images(path):
    from PIL import Image
    if path.lower().endswith(".pdf"):
        import pymupdf
        doc = pymupdf.open(path)
        out = []
        for p in doc:
            pix = p.get_pixmap(dpi=200, alpha=False)
            out.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return out
    img = Image.open(path)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return [img]


def _onnx(path):
    from .onnx_ocr import ocr_image
    pages, scores = [], []
    for img in _page_images(path):
        items = ocr_image(img)
        scores += [it["score"] for it in items]
        pages.append(items_to_text(items))
    text = "\n\n".join(pages)
    if not text.strip():
        raise RuntimeError("OCR found no text")
    return {"text": text, "tables": [], "engine": "onnx_ppocr", "conf": sum(scores) / len(scores) if scores else None}


def run_ocr(path, ocr_cfg):
    backend = ocr_cfg.get("backend", "auto")
    err = None
    if backend == "paddleocr_vl" or (backend == "auto" and importlib.util.find_spec("paddleocr") is not None):
        try:
            return _paddle_vl(path)
        except Exception as e:
            if backend == "paddleocr_vl":
                raise
            err = str(e)[:200]
    out = _onnx(path)
    out["fallback_reason"] = err
    return out
