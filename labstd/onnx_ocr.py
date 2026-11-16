import importlib.util
import math
import os

import numpy as np
from PIL import Image

_DET = None
_REC = None
_CHARS = None

DET_THRESH = 0.3
BOX_THRESH = 0.5
UNCLIP = 1.6
MAX_SIDE = 2000
REC_H = 48
REC_BASE_W = 320
REC_BATCH = 6
MIN_TEXT_SCORE = 0.4


def model_dir():
    env = os.environ.get("LAB_OCR_MODEL_DIR")
    if env:
        return env
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("rapidocr-onnxruntime is not installed (needed only for its model files)")
    return os.path.join(list(spec.submodule_search_locations)[0], "models")


def _session(name):
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(os.path.join(model_dir(), name), sess_options=opts, providers=["CPUExecutionProvider"])


def _load():
    global _DET, _REC, _CHARS
    if _DET is None:
        _DET = _session("ch_PP-OCRv4_det_infer.onnx")
        _REC = _session("ch_PP-OCRv4_rec_infer.onnx")
        chars = _REC.get_modelmeta().custom_metadata_map["character"].splitlines()
        chars.insert(len(chars), " ")
        chars.insert(0, "blank")
        _CHARS = chars


def _dilate2x2(m):
    d = m.copy()
    d[1:, :] |= m[:-1, :]
    d[:, 1:] |= m[:, :-1]
    d[1:, 1:] |= m[:-1, :-1]
    return d


def detect(img):
    from scipy import ndimage
    w0, h0 = img.size
    scale = 1.0
    if max(w0, h0) > MAX_SIDE:
        scale = MAX_SIDE / float(max(w0, h0))
    w1, h1 = int(w0 * scale), int(h0 * scale)
    if min(w1, h1) < 736:
        r = 736.0 / min(w1, h1)
        w1, h1 = int(w1 * r), int(h1 * r)
    rw, rh = max(32, int(round(w1 / 32) * 32)), max(32, int(round(h1 / 32) * 32))
    small = img.resize((rw, rh), Image.BILINEAR)
    x = (np.asarray(small, dtype=np.float32) / 255.0 - 0.5) / 0.5
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    pred = _DET.run(None, {_DET.get_inputs()[0].name: x})[0][0, 0]
    seg = _dilate2x2(pred > DET_THRESH)
    labels, n = ndimage.label(seg, structure=np.ones((3, 3), dtype=int))
    boxes = []
    for i, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        if min(h, w) < 3:
            continue
        region = labels[sl] == i
        score = float(pred[sl][region].mean())
        if score < BOX_THRESH:
            continue
        d = (w * h) * UNCLIP / (2.0 * (w + h))
        x0, x1 = xs.start - d, xs.stop + d
        y0, y1 = ys.start - d, ys.stop + d
        sx, sy = w0 / float(rw), h0 / float(rh)
        boxes.append((max(0, int(x0 * sx)), max(0, int(y0 * sy)), min(w0, int(math.ceil(x1 * sx))), min(h0, int(math.ceil(y1 * sy)))))
    return [b for b in boxes if b[2] - b[0] > 3 and b[3] - b[1] > 3]


def recognize(img, boxes):
    crops = [img.crop(b) for b in boxes]
    ratios = [c.width / float(c.height) for c in crops]
    order = np.argsort(ratios)
    out = [("", 0.0)] * len(crops)
    for s in range(0, len(order), REC_BATCH):
        idx = order[s:s + REC_BATCH]
        max_ratio = max([REC_BASE_W / REC_H] + [ratios[i] for i in idx])
        width = int(REC_H * max_ratio)
        batch = np.zeros((len(idx), 3, REC_H, width), dtype=np.float32)
        for k, i in enumerate(idx):
            c = crops[i]
            rw = min(width, int(math.ceil(REC_H * ratios[i])))
            arr = np.asarray(c.resize((max(1, rw), REC_H), Image.BILINEAR), dtype=np.float32) / 255.0
            batch[k, :, :, :max(1, rw)] = ((arr - 0.5) / 0.5).transpose(2, 0, 1)
        preds = _REC.run(None, {_REC.get_inputs()[0].name: batch})[0]
        ids, probs = preds.argmax(axis=2), preds.max(axis=2)
        for k, i in enumerate(idx):
            text, ps, prev = [], [], -1
            for t, p in zip(ids[k], probs[k]):
                if t != prev and t != 0:
                    text.append(_CHARS[t])
                    ps.append(float(p))
                prev = t
            out[i] = ("".join(text), float(np.mean(ps)) if ps else 0.0)
    return out


def ocr_image(img):
    _load()
    img = img.convert("RGB")
    boxes = detect(img)
    if not boxes:
        return []
    recs = recognize(img, boxes)
    items = []
    for (x0, y0, x1, y1), (text, score) in zip(boxes, recs):
        if text.strip() and score >= MIN_TEXT_SCORE:
            items.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text.strip(), "score": score})
    return items
