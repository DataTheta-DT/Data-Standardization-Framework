import math
import os
import re

BASES = ["m", "s", "g", "rad", "K", "C", "cd"]
MOL = len(BASES)
G_IDX = BASES.index("g")
NDIM = len(BASES) + 1
ZERO = (0,) * NDIM


class UcumError(ValueError):
    pass


def _vec_mul(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _vec_pow(a, n):
    return tuple(x * n for x in a)


def _num(s):
    try:
        return float(s)
    except ValueError:
        raise UcumError(f"bad number {s}")


_LINE = re.compile(r"^(?P<code>\S+)\s*=\s*(?P<body>.*?)\s+(?P<metric>metric|nonmetric)\s*(?:#.*)?$")


class UcumTable:
    def __init__(self, path):
        self.path = path
        self.prefixes = {}
        self.units = {}
        self.order = []
        self.case_sensitive = True
        self._cache = {}
        self._busy = set()
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line.strip() or line.startswith("#"):
                    continue
                if line.startswith("case "):
                    self.case_sensitive = "insensitive" not in line
                    continue
                if line.startswith("prefix "):
                    _, code, val = line.split()[:3]
                    self.prefixes[code] = float(val)
                    continue
                if line.startswith("dimensions") or line.startswith("base "):
                    continue
                m = _LINE.match(line)
                if not m:
                    continue
                body = m.group("body").strip()
                parts = body.split(None, 1)
                special = False
                mag, expr = 1.0, ""
                try:
                    mag = float(parts[0])
                    expr = parts[1] if len(parts) > 1 else "1"
                except (ValueError, IndexError):
                    special = True
                if m.group("code") in self.units:
                    continue
                self.units[m.group("code")] = {"mag": mag, "expr": expr, "metric": m.group("metric") == "metric", "special": special}
                self.order.append(m.group("code"))
        self.base_codes = {}
        with open(path, encoding="utf-8") as f:
            idx = 0
            for line in f:
                if line.startswith("base "):
                    self.base_codes[line.split()[1]] = idx
                    idx += 1
        self.prefix_keys = sorted(self.prefixes, key=len, reverse=True)

    def resolve_atom(self, code):
        if code in self._cache:
            return self._cache[code]
        if code in self._busy:
            raise UcumError(f"circular definition for {code}")
        self._busy.add(code)
        try:
            out = self._resolve(code)
        finally:
            self._busy.discard(code)
        self._cache[code] = out
        return out

    def _resolve(self, code):
        base_key = code if self.case_sensitive else code
        if code in self.base_codes:
            v = [0] * NDIM
            v[self.base_codes[code]] = 1
            return 1.0, tuple(v)
        if code in self.units:
            return self._unit(code)
        for p in self.prefix_keys:
            if code.startswith(p) and len(code) > len(p):
                rest = code[len(p):]
                u = self.units.get(rest)
                if rest in self.base_codes:
                    mag, dims = self.resolve_atom(rest)
                    return self.prefixes[p] * mag, dims
                if u and u["metric"] and not u["special"]:
                    mag, dims = self._unit(rest)
                    return self.prefixes[p] * mag, dims
        raise UcumError(f"unknown unit {code}")

    def _unit(self, code):
        u = self.units[code]
        if u["special"]:
            raise UcumError(f"special unit {code} not supported")
        if code.lower() == "mol":
            v = [0] * NDIM
            v[MOL] = 1
            return 1.0, tuple(v)
        mag, dims = self.parse(u["expr"])
        return u["mag"] * mag, dims

    def _base_atom(self, code):
        if code in self.units or code in self.base_codes:
            return code
        for p in self.prefix_keys:
            if code.startswith(p) and len(code) > len(p):
                rest = code[len(p):]
                if rest in self.units or rest in self.base_codes:
                    return rest
        return None

    def _uses_mol(self, code, seen=None):
        seen = seen or set()
        if code in seen:
            return False
        seen.add(code)
        if code.lower() == "mol":
            return True
        u = self.units.get(code)
        if not u or u["special"]:
            return False
        for t in re.findall(r"[^./(){}\s]+", u["expr"]):
            if re.fullmatch(r"[+-]?\d+", t):
                continue
            base = self._base_atom(t) or (self._base_atom(re.sub(r"[+-]?\d+$", "", t)) if re.search(r"\d$", t) else None)
            if base and self._uses_mol(base, seen):
                return True
        return False

    def markers(self, expr):
        out = set()
        for t in re.findall(r"\{[^}]*\}|\(|\)|\.|/|[^./(){}]+", expr.upper() if not self.case_sensitive else expr):
            if t in "()./" or t.startswith("{") or re.fullmatch(r"[+-]?\d+", t):
                continue
            base = self._base_atom(t) or (self._base_atom(re.sub(r"[+-]?\d+$", "", t)) if re.search(r"\d$", t) else None)
            if not base:
                continue
            if self._uses_mol(base):
                out.add("substance")
            arb = self._arbitrary(base)
            if arb:
                out.add("arbitrary:" + arb)
        return frozenset(out)

    def _arbitrary(self, code, seen=None):
        seen = seen or set()
        u = self.units.get(code)
        if not u or u["special"] or not code.startswith("[") or code in seen:
            return None
        seen.add(code)
        if u["mag"] != 1:
            return None
        e = u["expr"].strip()
        if e == "1":
            return code.lower()
        return self._arbitrary(e, seen) if e in self.units else None

    def parse(self, expr):
        if not self.case_sensitive:
            expr = expr.upper()
        toks = re.findall(r"\{[^}]*\}|\(|\)|\.|/|[^./(){}]+", expr)
        pos = [0]

        def peek():
            return toks[pos[0]] if pos[0] < len(toks) else None

        def take():
            t = toks[pos[0]]
            pos[0] += 1
            return t

        def component():
            t = peek()
            if t is None:
                raise UcumError("unexpected end")
            if t == "(":
                take()
                v = expression()
                if peek() != ")":
                    raise UcumError("missing )")
                take()
                return v
            if t.startswith("{"):
                take()
                return 1.0, ZERO
            take()
            if re.fullmatch(r"[+-]?\d+", t):
                return float(int(t)), ZERO
            try:
                return self.resolve_atom(t)
            except UcumError:
                m = re.match(r"^(.*?)([+-]?\d+)$", t)
                if not m or not m.group(1):
                    raise
                mag, dims = self.resolve_atom(m.group(1))
                e = int(m.group(2))
                return mag ** e, _vec_pow(dims, e)

        def term():
            v = component()
            while peek() is not None and peek().startswith("{"):
                take()
            return v

        def expression():
            if peek() == "/":
                take()
                mag, dims = term()
                mag, dims = 1.0 / mag, _vec_pow(dims, -1)
            else:
                mag, dims = term()
            while peek() in (".", "/"):
                op = take()
                m2, d2 = term()
                if op == ".":
                    mag, dims = mag * m2, _vec_mul(dims, d2)
                else:
                    mag, dims = mag / m2, _vec_mul(dims, _vec_pow(d2, -1))
            return mag, dims

        if not toks:
            raise UcumError("empty unit")
        mag, dims = expression()
        if pos[0] != len(toks):
            raise UcumError("trailing tokens")
        return mag, dims


class Ucum:
    def __init__(self, cs_path, ci_path=None, xml_path=None):
        self.cs = UcumTable(cs_path)
        self.ci = UcumTable(ci_path) if ci_path and os.path.exists(ci_path) else None
        self.ci_to_cs_atom = {}
        self.ci_to_cs_prefix = {}
        if self.ci and xml_path and os.path.exists(xml_path):
            import xml.etree.ElementTree as ET
            for el in ET.parse(xml_path).getroot():
                tag = el.tag.split("}")[-1]
                code, ci = el.attrib.get("Code"), el.attrib.get("CODE")
                if not code or not ci:
                    continue
                if tag == "prefix":
                    self.ci_to_cs_prefix[ci] = code
                elif tag in ("unit", "base-unit"):
                    self.ci_to_cs_atom[ci] = code
        self.print_to_code = {}
        if xml_path and os.path.exists(xml_path):
            import xml.etree.ElementTree as ET
            for el in ET.parse(xml_path).getroot():
                tag = el.tag.split("}")[-1]
                code = el.attrib.get("Code")
                if tag not in ("prefix", "unit", "base-unit") or not code:
                    continue
                for ps in el:
                    if ps.tag.split("}")[-1] == "printSymbol":
                        sym = "".join(ps.itertext()).strip()
                        if sym and len(sym) == 1 and not sym.isascii():
                            self.print_to_code.setdefault(sym, code)
        self._p = {}
        self._m = {}

    def clean(self, raw):
        import unicodedata
        s = unicodedata.normalize("NFKC", str(raw)).strip()
        for sym, code in self.print_to_code.items():
            s = s.replace(sym, code)
        return re.sub(r"\s+", "", s)

    def markers(self, unit):
        if unit not in self._m:
            self._m[unit] = self.cs.markers(unit)
        return self._m[unit]

    def parse(self, unit):
        if unit in self._p:
            return self._p[unit]
        out = self.cs.parse(unit)
        self._p[unit] = out
        return out

    def is_valid(self, unit):
        try:
            self.parse(unit)
            return True
        except UcumError:
            return False

    def ci_to_cs(self, unit):
        if not self.ci:
            return None
        up = unit.upper()
        try:
            self.ci.parse(up)
        except UcumError:
            return None
        out = []
        orig_ann = re.findall(r"\{[^}]*\}", unit)
        ann_i = 0
        for t in re.findall(r"\{[^}]*\}|\(|\)|\.|/|[^./(){}]+", up):
            if t.startswith("{"):
                out.append(orig_ann[ann_i] if ann_i < len(orig_ann) else t)
                ann_i += 1
                continue
            if t in "()./":
                out.append(t)
                continue
            if re.fullmatch(r"[+-]?\d+", t):
                out.append(t)
                continue
            out.append(self._atom_ci_to_cs(t))
        cand = "".join(out)
        return cand if self.is_valid(cand) else None

    def _atom_ci_to_cs(self, t):
        m = re.match(r"^(.*?)([+-]?\d+)$", t)
        for atom, exp in ((t, ""), (m.group(1), m.group(2)) if m and m.group(1) else (None, "")):
            if atom is None:
                continue
            if atom in self.ci_to_cs_atom:
                return self.ci_to_cs_atom[atom] + exp
            for p in sorted(self.ci_to_cs_prefix, key=len, reverse=True):
                if atom.startswith(p) and atom[len(p):] in self.ci_to_cs_atom:
                    return self.ci_to_cs_prefix[p] + self.ci_to_cs_atom[atom[len(p):]] + exp
        return t

    def dims(self, unit):
        return self.parse(unit)[1]

    def magnitude(self, unit):
        return self.parse(unit)[0]

    def commensurable(self, a, b):
        try:
            return self.dims(a) == self.dims(b)
        except UcumError:
            return False

    def factor(self, a, b):
        ma, da = self.parse(a)
        mb, db = self.parse(b)
        if da != db:
            raise UcumError(f"{a} and {b} are not commensurable")
        return ma / mb

    def same(self, a, b):
        try:
            ma, da = self.parse(a)
            mb, db = self.parse(b)
        except UcumError:
            return False
        return da == db and math.isclose(ma, mb, rel_tol=1e-9)
