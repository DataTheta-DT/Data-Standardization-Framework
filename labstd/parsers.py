import csv
import io
import re
from html.parser import HTMLParser

HEADER_MAP = {
    "test_name": {"test", "test name", "testname", "investigation", "parameter", "analyte", "component", "description", "observation name", "name of test", "test description"},
    "value": {"result", "results", "value", "observed value", "result value", "observation", "test result", "finding"},
    "unit": {"unit", "units", "uom", "unit of measure"},
    "ref": {"reference range", "ref range", "reference", "normal range", "biological reference interval", "reference interval", "range", "normal values", "ref interval", "bio ref interval", "biological ref interval"},
    "flag": {"flag", "abnormal flag", "interpretation", "abnormal", "abn flag", "h/l", "status"},
    "source_code": {"loinc", "loinc code", "loinc num", "test code", "code"},
    "patient_id": {"patient id", "mrn", "uhid", "pid", "patient mrn"},
    "patient_name": {"patient name"},
    "sex": {"sex", "gender"},
    "dob": {"dob", "date of birth", "birth date", "birthdate"},
    "collected_at": {"collected", "collection date", "collected at", "sample date", "specimen date", "collection time", "sample collected", "date collected", "collection datetime"},
    "report_id": {"report id", "accession", "accession number", "order id", "report no"},
    "lab_name": {"lab", "lab name", "laboratory"},
}
PREFIX_OK = ("ref", "unit", "value")
FLAG_TOKENS = {"h", "l", "hh", "ll", "n", "a", "high", "low", "normal", "abnormal", "critical", "h*", "l*", "*h", "*l"}
REF_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?)|((?:<=|>=|<|>|≤|≥)\s*\d)", re.I)
NUM_RE = re.compile(r"^[<>]=?\s*\d|^≤\s*\d|^≥\s*\d|^\d")
VALUE_SPLIT = re.compile(r"^\s*((?:[<>]=?|≤|≥)?\s*\d[\d,]*(?:\.\d+)?)\s*(.*)$")


def hkey(s):
    s = re.sub(r"[^a-z0-9/ ]+", " ", str(s).lower().replace("_", " "))
    return " ".join(s.split())


def map_headers(cols):
    out = {}
    for i, c in enumerate(cols):
        k = hkey(c)
        if not k:
            continue
        for canon, names in HEADER_MAP.items():
            if canon in out:
                continue
            if k in names:
                out[canon] = i
                break
        else:
            for canon in PREFIX_OK:
                if canon in out:
                    continue
                if any(len(n) >= 4 and k.startswith(n) for n in HEADER_MAP[canon]):
                    out[canon] = i
                    break
    return out


def is_flag(s):
    return str(s).strip().lower() in FLAG_TOKENS


def is_ref(s):
    return bool(REF_RE.search(str(s)))


def looks_like_unit(s):
    s = str(s).strip()
    if not s or len(s) > 24 or is_flag(s) or s.lower() in NOT_UNITS:
        return False
    if re.fullmatch(r"[\d\.\s,\-–]+", s):
        return False
    return bool(re.search(r"[A-Za-z%µμ/]", s))


def split_value_unit(raw):
    raw = "" if raw is None else str(raw).strip()
    m = VALUE_SPLIT.match(raw)
    if not m:
        return raw, None, None
    value = m.group(1).replace(" ", "")
    rest = m.group(2).strip()
    if not rest:
        return value, None, None
    if is_flag(rest):
        return value, None, rest
    return value, rest, None


def make_row(name, value, unit, ref, flag, conf, code=None, system=None):
    return {
        "raw_test_name": name,
        "raw_value": value,
        "raw_unit": unit,
        "raw_ref": ref,
        "raw_flag": flag,
        "source_code": code,
        "source_system": system,
        "extraction_conf": conf,
    }


def clean_name(s):
    s = re.sub(r"\s+", " ", str(s)).strip(" *:\t")
    return s


def row_from_cells(cells, hm, conf):
    def cell(k):
        i = hm.get(k)
        if i is None or i >= len(cells) or cells[i] is None:
            return None
        v = str(cells[i]).strip()
        return v or None

    name = cell("test_name")
    raw_value = cell("value")
    if not name or raw_value is None or ":" in name or NOT_TEST_RE.match(name):
        return None
    unit, ref, flag = cell("unit"), cell("ref"), cell("flag")
    value, u2, f2 = split_value_unit(raw_value)
    unit = unit or u2
    flag = flag or f2
    if NUM_RE.match(value) is None and not re.search(r"[A-Za-z]", value):
        return None
    return make_row(clean_name(name), value, unit, ref, flag, conf, cell("source_code"), "LN" if cell("source_code") and re.fullmatch(r"\d{1,7}-\d", cell("source_code")) else None)


TITLE_RE = re.compile(r"^(?:mrs|mr|ms|miss|master|shri|smt|dr|prof)\b\.?\s*", re.I)
NOT_TEST_RE = re.compile(r"^(?:page|phone|tel|fax|mobile|pin|pincode|barcode|po|order|visit|sample|report|customer|age)\b", re.I)
NOT_UNITS = {"of", "to", "and", "the", "or", "no", "on", "at", "by"}


def parse_cells(cells, conf, require_context=False):
    cells = [str(c).strip() for c in cells if c is not None and str(c).strip() != ""]
    if len(cells) < 2:
        return None
    name = cells[0]
    if not re.search(r"[A-Za-z]", name) or ":" in name or len(name) > 70 or TITLE_RE.match(name) or NOT_TEST_RE.match(name):
        return None
    vi, value, unit, flag = None, None, None, None
    for i in range(1, len(cells)):
        v, u, f = split_value_unit(cells[i])
        if NUM_RE.match(v):
            vi, value, unit = i, v, u
            flag = flag or f
            break
        if is_flag(cells[i]):
            flag = cells[i]
            continue
        return None
    if vi is None:
        return None
    ref = None
    for c in cells[vi + 1:]:
        if flag is None and is_flag(c):
            flag = c
        elif ref is None and is_ref(c):
            ref = c
        elif unit is None and looks_like_unit(c):
            unit = c
    if require_context and not (unit or ref):
        return None
    return make_row(clean_name(name), value, unit, ref, flag, conf)


def parse_tokens(line, conf):
    tokens = [t for t in line.split() if t != "|"]
    idx = None
    for i in range(1, len(tokens)):
        if re.fullmatch(r"(?:[<>]=?|≤|≥)?\d+(?:[.,]\d+)?", tokens[i]):
            idx = i
            break
    if idx is None:
        return None
    name = " ".join(tokens[:idx])
    if ":" in name or len(name) > 70 or not re.search(r"[A-Za-z]", name) or TITLE_RE.match(name) or NOT_TEST_RE.match(name):
        return None
    rem = " ".join(tokens[idx + 1:])
    ref = None
    m = REF_RE.search(rem)
    if m:
        ref = rem[m.start():].strip()
        rem = rem[:m.start()].strip()
    unit = flag = None
    for t in rem.split():
        if flag is None and is_flag(t):
            flag = t
        elif unit is None and looks_like_unit(t):
            unit = t
    if not (unit or ref):
        return None
    return make_row(clean_name(name), tokens[idx], unit, ref, flag, conf)


def parse_line(line, conf):
    line = line.strip()
    if not line:
        return None
    cells = [c for c in re.split(r"\s{2,}|\t|\|", line) if c.strip()]
    if len(cells) >= 3:
        return parse_cells(cells, conf, require_context=True)
    return parse_tokens(line, conf)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cur, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cur = []
        elif tag in ("td", "th"):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.cur is not None:
            self.cur.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cur is not None:
            self.rows.append(self.cur)
            self.cur = None

    def handle_data(self, d):
        if self.cell is not None:
            self.cell.append(d)


def html_table_rows(html):
    p = _TableParser()
    p.feed(html)
    return p.rows


def split_markup(text):
    tables, leftover = [], []
    for m in re.finditer(r"<table.*?</table>", text, re.S | re.I):
        tables.append(html_table_rows(m.group(0)))
    text = re.sub(r"<table.*?</table>", "\n", text, flags=re.S | re.I)
    block = []
    for ln in text.splitlines():
        if ln.count("|") >= 2:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            block.append(cells)
        else:
            if block:
                tables.append(block)
                block = []
            if ln.strip():
                leftover.append(ln)
    if block:
        tables.append(block)
    return tables, leftover


def rows_from_table(rows, conf):
    hidx, hm = None, None
    for i, r in enumerate(rows[:12]):
        m = map_headers(r)
        if "test_name" in m and "value" in m:
            hidx, hm = i, m
            break
    out = []
    if hm is not None:
        width = len(rows[hidx])
        for r in rows[hidx + 1:]:
            row = None
            if len(r) == width:
                row = row_from_cells(r, hm, conf)
                if row and is_flag(row["raw_value"]):
                    row = None
            if row is None:
                row = parse_cells(r, conf)
            if row:
                out.append(row)
    else:
        for r in rows:
            row = parse_cells(r, conf, require_context=True)
            if row:
                out.append(row)
    return out


def grab_all(labels, text):
    pat = r"(?:%s)[^:|\n]{0,25}?(?:\s*\|\s*)?\s*:\s*([^|\n]+?)\s*(?:\||\n|$|\s{3,})" % "|".join(labels)
    return [m.group(1).strip() for m in re.finditer(pat, text, re.I)]


def grab_date(labels, text):
    from .structure import to_iso
    for v in grab_all(labels, text):
        iso = to_iso(v) if re.search(r"\d{1,4}\s*[-/. ]\s*[A-Za-z0-9]{1,9}\s*[-/. ]\s*\d{2,4}|\d{8}", v) else None
        if iso:
            return iso
    return None


def grab(labels, text):
    pat = r"(?:%s)[^:|\n]{0,25}?(?:\s*\|\s*)?\s*:\s*([^|\n]+?)\s*(?:\||\n|$|\s{3,})" % "|".join(labels)
    m = re.search(pat, text, re.I)
    return m.group(1).strip() if m else None


def norm_sex(s):
    if not s:
        return None
    m = re.search(r"\b(female|male|other|f|m)\b", s, re.I)
    if not m:
        return None
    v = m.group(1).lower()
    return {"f": "female", "m": "male"}.get(v, v)


NAME_TITLE_RE = re.compile(r"^((?i:mr|mrs|ms|miss|master|shri|smt)\.?)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})")
AGE_SEX_RE = re.compile(r"\b(\d{1,3})\s*(?:yrs?|years?|y)\b\.?(?:\s*\d{1,2}\s*(?:mo|months?|m|d|days?)\b)*\s*(?:/\s*|,\s*|\s+)?(male|female|m|f)\b", re.I)


def find_patient_id(text):
    m = re.search(r"(?:registration\s*(?:id|no)|reg\.?\s*no\.?|patient\s*id|patient\s*no|mrn|uhid|pid)[^:|\n]{0,10}?(?:\s*\|\s*)?\s*:\s*([A-Za-z0-9][A-Za-z0-9\-/]*)(?:[ \t]*\n[ \t]*([A-Za-z0-9][A-Za-z0-9\-]*))?", text, re.I)
    if not m:
        return None
    first, second = m.group(1), m.group(2)
    if first.endswith("-") and second:
        return first + second
    return first


def find_lab_name(text):
    v = grab([r"laboratory\s*name", r"lab\s*name", r"laboratory", r"organi[sz]ation"], text)
    if v:
        return v
    m = re.search(r"(?:test\s+has\s+been\s+)?performed\s+at\s*:?\s*\n?\s*([^\n|]{3,80})", text, re.I)
    return m.group(1).strip() if m else None


def find_patient_name(text):
    v = grab([r"patient\s*name", r"customer\s*name", r"name\s*of\s*patient", r"patient(?!\s*(?:id|no|number|code|information|details))"], text)
    if v:
        return v
    for line in text.splitlines()[:40]:
        first = line.split("|")[0].strip()
        m = NAME_TITLE_RE.match(first)
        if m:
            return first
    return None


def split_title(name):
    if not name:
        return None, None
    m = re.match(r"^((?:mrs|mr|ms|miss|master|shri|smt|dr|prof)\b\.?)\s*(.+)$", name.strip(), re.I)
    if m and m.group(2).strip():
        return m.group(1), m.group(2).strip()
    return None, name.strip()


def extract_meta(text):
    from .structure import to_iso
    text = text or ""
    name_raw = find_patient_name(text)
    title, name = split_title(name_raw)
    age, sex = None, norm_sex(grab([r"sex", r"gender"], text))
    m = AGE_SEX_RE.search(text)
    if m:
        age = int(m.group(1))
        sex = sex or norm_sex(m.group(2))
    if age is None:
        a = grab([r"age"], text)
        if a and re.match(r"^\d{1,3}", a):
            age = int(re.match(r"^\d{1,3}", a).group(0))
    return {
        "patient": {
            "id": find_patient_id(text),
            "name": name,
            "name_raw": name_raw,
            "sex": sex,
            "age_years": age,
            "dob": to_iso(grab([r"dob", r"date\s*of\s*birth", r"birth\s*date"], text)),
        },
        "collected_at": grab_date([r"collected", r"collection\s*date", r"collection\s*time", r"sample\s*collected", r"specimen\s*collected", r"sample\s*date", r"specimen\s*date", r"date\s*collected"], text),
        "reported_at": grab_date([r"reported", r"report\s*date", r"reported\s*on"], text),
        "lab_name": find_lab_name(text),
    }


def parse_text_report(text, tables, conf):
    meta = extract_meta(text)
    rows = []
    for t in tables or []:
        rows += rows_from_table(t, conf)
    if not rows and text:
        md_tables, leftover = split_markup(text)
        for t in md_tables:
            rows += rows_from_table(t, conf)
        for ln in leftover:
            r = parse_line(ln, conf)
            if r:
                rows.append(r)
    if not rows:
        return []
    meta["rows"] = rows
    return [meta]


def hl7_dt(s):
    from .structure import to_iso
    return to_iso(s)


def _comp(field, i=0):
    parts = field.split("^")
    return parts[i].strip() if i < len(parts) else ""


def parse_hl7(text, conf=1.0):
    segs = [s for s in re.split(r"[\r\n]+", text.strip()) if s.strip()]
    reports, cur = [], None
    for s in segs:
        f = s.strip().split("|")
        t = f[0]
        if t == "MSH":
            cur = {"patient": {"id": None, "name": None, "name_raw": None, "sex": None, "age_years": None, "dob": None}, "lab_name": f[3] if len(f) > 3 and f[3] else (f[2] if len(f) > 2 else None), "collected_at": None, "reported_at": hl7_dt(f[6]) if len(f) > 6 else None, "rows": []}
            reports.append(cur)
        elif cur is None:
            continue
        elif t == "PID":
            if len(f) > 3:
                cur["patient"]["id"] = _comp(f[3]) or None
            if len(f) > 5 and f[5]:
                family, given = _comp(f[5], 0), _comp(f[5], 1)
                cur["patient"]["name"] = " ".join(x for x in (given, family) if x) or None
                cur["patient"]["name_raw"] = cur["patient"]["name"]
            if len(f) > 7:
                cur["patient"]["dob"] = hl7_dt(f[7])
            if len(f) > 8 and f[8]:
                cur["patient"]["sex"] = {"F": "female", "M": "male", "O": "other"}.get(f[8].strip().upper(), "unknown")
        elif t == "OBR":
            if len(f) > 7 and f[7]:
                cur["collected_at"] = hl7_dt(f[7])
        elif t == "OBX":
            if len(f) < 6:
                continue
            code, text_name, system = _comp(f[3], 0), _comp(f[3], 1), _comp(f[3], 2)
            name = text_name or code
            unit = _comp(f[6], 0) if len(f) > 6 and f[6] else None
            ref = f[7].strip() if len(f) > 7 and f[7].strip() else None
            flag = f[8].strip() if len(f) > 8 and f[8].strip() else None
            row = make_row(clean_name(name), f[5].strip(), unit, ref, flag, conf, code or None, system.upper() or None)
            if len(f) > 14 and f[14] and not cur["collected_at"]:
                cur["collected_at"] = hl7_dt(f[14])
            cur["rows"].append(row)
    return [r for r in reports if r["rows"]]


def parse_csv(text, conf=0.99):
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    hm, start = None, 0
    for i, r in enumerate(table[:5]):
        m = map_headers(r)
        if "test_name" in m and "value" in m:
            hm, start = m, i + 1
            break
    if hm is None:
        return []
    groups = {}
    for r in table[start:]:
        row = row_from_cells(r, hm, conf)
        if not row:
            continue

        def g(k):
            i = hm.get(k)
            return r[i].strip() if i is not None and i < len(r) and r[i].strip() else None

        key = g("report_id") or (g("patient_id"), g("patient_name"), g("collected_at"))
        if key not in groups:
            from .structure import to_iso
            groups[key] = {
                "patient": {"id": g("patient_id"), "name": g("patient_name"), "name_raw": g("patient_name"), "sex": norm_sex(g("sex")), "age_years": None, "dob": to_iso(g("dob"))},
                "collected_at": to_iso(g("collected_at")),
                "reported_at": None,
                "lab_name": g("lab_name"),
                "rows": [],
            }
        groups[key]["rows"].append(row)
    return list(groups.values())


def parse_extraction(fmt_route, text, tables, conf):
    if fmt_route == "hl7_parser":
        return parse_hl7(text, conf)
    if fmt_route == "csv_parser":
        return parse_csv(text, conf)
    return parse_text_report(text, tables, conf)
