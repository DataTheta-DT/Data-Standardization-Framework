import statistics


def cluster_lines(items):
    if not items:
        return []
    heights = [it["y1"] - it["y0"] for it in items]
    tol = max(2.0, 0.5 * statistics.median(heights))
    items = sorted(items, key=lambda it: ((it["y0"] + it["y1"]) / 2, it["x0"]))
    lines, cur, cur_y = [], [], None
    for it in items:
        yc = (it["y0"] + it["y1"]) / 2
        if cur_y is None or abs(yc - cur_y) <= tol:
            cur.append(it)
            cur_y = yc if cur_y is None else (cur_y * (len(cur) - 1) + yc) / len(cur)
        else:
            lines.append(cur)
            cur, cur_y = [it], yc
    if cur:
        lines.append(cur)
    return [sorted(l, key=lambda it: it["x0"]) for l in lines]


def line_to_cells(line, gap):
    cells, cur, prev_x1 = [], [], None
    for it in line:
        if prev_x1 is not None and it["x0"] - prev_x1 > gap:
            cells.append(" ".join(cur))
            cur = []
        cur.append(it["text"])
        prev_x1 = it["x1"]
    if cur:
        cells.append(" ".join(cur))
    return cells


def items_to_text(items):
    if not items:
        return ""
    gap = statistics.median([it["y1"] - it["y0"] for it in items])
    return "\n".join(" | ".join(line_to_cells(l, gap)) for l in cluster_lines(items))
