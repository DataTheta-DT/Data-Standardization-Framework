import json
import re

from rapidfuzz import fuzz, process

SPELLING = [("haem", "hem"), ("aemia", "emia"), ("leuc", "leuk"), ("oedema", "edema"), ("oestr", "estr")]
GENERIC_WORDS = {"value", "values", "level", "levels", "test", "estimation", "estimated"}
SPECIMEN_WORDS = {"blood", "serum", "plasma"}
STOP_TOKENS = {"total", "value", "values", "level", "levels", "test", "of", "the", "in", "by", "and", "for", "a", "an", "to", "with"}
TIER_SCORE = {1: 100, 2: 100, 3: 90, 4: 80}


def _stem(w):
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def norm(s):
    s = str(s or "").lower().replace("µ", "u").replace("%", " percent ")
    for a, b in SPELLING:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9+ ]+", " ", s)
    return " ".join(_stem(w) for w in s.split())


def name_forms(name):
    name = str(name or "")
    raw_forms = [name]
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    if spaced != name:
        raw_forms.append(spaced)
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if len(parts) > 1:
        raw_forms += parts
    raw_forms.append(re.sub(r"\([^)]*\)", " ", name))
    raw_forms += re.findall(r"\(([^)]*)\)", name)
    base = []
    for rf in raw_forms:
        n = norm(rf)
        if n and n not in base:
            base.append(n)
    forms = list(base)

    def add(x):
        x = " ".join(x.split())
        if x and x not in forms:
            forms.append(x)

    for n in base:
        add(" ".join(w for w in n.split() if w not in GENERIC_WORDS))
    for n in base:
        add(" ".join(w for w in n.split() if w not in STOP_TOKENS))
    for n in list(forms):
        add(" ".join(w for w in n.split() if w not in SPECIMEN_WORDS))
    for n in list(forms):
        words = n.split()
        if 0 < len(words) <= 4 and words[-1] not in ("count", "percent") and words[0] not in ("count",):
            add(n + " count")
    return forms


def _analyte(long_name):
    head = re.split(r"\s\[|\sin\s", str(long_name), maxsplit=1)[0]
    return head.split("/")[0]


def _consumer_keys(cn):
    base = cn.rsplit(", ", 1)[0] if ", " in cn else cn
    base = base.split("/")[0]
    keys = [re.sub(r"\([^)]*\)", " ", base)]
    for inner in re.findall(r"\(([^)]*)\)", base):
        keys += [x for x in re.split(r"[;,]", inner)]
    keys.append(base.replace("(", " ").replace(")", " "))
    return keys


def _tiers(rec):
    out = {}

    def put(k, t):
        k = norm(k)
        if k and out.get(k, 9) > t:
            out[k] = t

    put(_analyte(rec["long_common_name"]), 1)
    for cn in str(rec.get("consumer_names", "")).split(" | "):
        if cn.strip():
            for k in _consumer_keys(cn):
                put(k, 1)
    comp = str(rec["component"]).split("^")[0]
    if norm(comp) != "observation":
        put(comp, 2)
    short = str(rec.get("shortname", "")).split()
    if short:
        put(short[0], 2)
    for s in str(rec["relatednames"]).split(";"):
        put(s, 3)
    return out


class LoincMapper:
    def __init__(self, loinc_df, th, units=None):
        self.auto = th["loinc_auto"]
        self.review = th["loinc_review"]
        self.margin = th["loinc_margin"]
        self.rank_auto = th.get("loinc_rank_auto", 2000)
        self.generic_max = th.get("loinc_generic_max", 800)
        self.token_max = th.get("loinc_token_max_candidates", 60)
        self.rank_margin = th.get("loinc_rank_margin", 1.5)
        self.loose_rank_max = th.get("loinc_loose_rank_max", 100)
        self.default_systems = set(th.get("default_specimen_systems", []))
        self.units = units
        self._key_tokens = {}
        self.meta = {}
        names = {}
        for r in loinc_df.to_dict("records"):
            code = r["loinc_num"]
            try:
                rank = int(float(r.get("common_test_rank") or 0))
            except (TypeError, ValueError):
                rank = 0
            self.meta[code] = {"name": r["long_common_name"], "system": r.get("system") or "", "unit": (r.get("canonical_unit") or "").strip() or None, "rank": rank, "rec": r}
            for k, t in _tiers(r).items():
                d = names.setdefault(k, {})
                d[code] = min(t, d.get(code, 9))
        self.names = {}
        for k, v in names.items():
            if len(v) > self.generic_max:
                v = {c: t for c, t in v.items() if t <= 2}
            if v and len(v) <= self.generic_max:
                self.names[k] = v
        ranked = [c for c, m in self.meta.items() if 0 < m["rank"] <= self.rank_auto]
        self.fuzzy_choices = [k for k, codes in self.names.items() if any(c in codes and t <= 2 for c, t in codes.items() if 0 < self.meta[c]["rank"] <= self.rank_auto)]
        self.tok_index = {}
        for c in ranked:
            toks = set()
            for k, t in _tiers(self.meta[c]["rec"]).items():
                toks.update(w for w in k.split() if w not in STOP_TOKENS)
            for w in toks:
                self.tok_index.setdefault(w, set()).add(c)
        self.tok_df_max = max(20, int(0.08 * max(len(ranked), 1)))

    def canonical_unit(self, loinc):
        m = self.meta.get(loinc)
        return m["unit"] if m else None

    def long_name(self, loinc):
        m = self.meta.get(loinc)
        return m["name"] if m else None

    def _result(self, loinc, score, method, status, cands):
        return {
            "loinc_num": loinc,
            "loinc_name": self.long_name(loinc) if loinc else None,
            "loinc_score": float(score),
            "loinc_method": method,
            "loinc_status": status,
            "loinc_candidates": json.dumps(cands),
        }

    def _cand_list(self, codes, scores=None):
        out = []
        for c in codes[:5]:
            m = self.meta[c]
            d = {"loinc_num": c, "name": m["name"], "rank": m["rank"]}
            if scores:
                d["score"] = round(scores.get(c, 0), 1)
            out.append(d)
        return out

    def _compat(self, code, unit_ucums):
        u = self.meta[code]["unit"]
        return bool(u and self.units.can_convert_any(unit_ucums, u))

    def _choose(self, cands, unit_ucum):
        cands = dict(cands)
        unit_ok = True
        if unit_ucum and self.units:
            ok = {c: t for c, t in cands.items() if self._compat(c, unit_ucum)}
            if ok:
                cands = ok
            else:
                unit_ok = False
        pref = {c: t for c, t in cands.items() if self.meta[c]["system"] in self.default_systems}
        if pref:
            cands = pref
        best = min(cands.values())
        cands = {c: t for c, t in cands.items() if t == best}
        ranked = sorted([c for c in cands if self.meta[c]["rank"] > 0], key=lambda c: self.meta[c]["rank"])
        ordered = ranked + sorted([c for c in cands if c not in ranked])
        return ordered, ranked, unit_ok, best

    def _from_candidates(self, cands, unit_ucum, method):
        ordered, ranked, unit_ok, best = self._choose(cands, unit_ucum)
        lst = self._cand_list(ordered)
        if not unit_ok:
            return self._result(ordered[0], 60, method + "_unit_mismatch", "review", lst)
        top_rank = self.meta[ranked[0]]["rank"] if ranked else 0
        clear = len(ranked) == 1 or (len(ranked) > 1 and top_rank * self.rank_margin <= self.meta[ranked[1]]["rank"])
        common = bool(ranked) and top_rank <= self.rank_auto
        loose_ok = bool(ranked) and top_rank <= self.loose_rank_max
        if len(ordered) == 1:
            if best <= 2 or loose_ok:
                return self._result(ordered[0], TIER_SCORE[best], method, "auto", lst)
            return self._result(ordered[0], 60, method + "_weak", "review", lst)
        if ranked and best <= 2 and common:
            return self._result(ranked[0], 95, method + "_ranked", "auto", lst)
        if ranked and best <= 2 and clear:
            return self._result(ranked[0], 85, method + "_ranked_far", "auto", lst)
        if ranked and best == 3 and clear and loose_ok:
            return self._result(ranked[0], 85, method + "_ranked", "auto", lst)
        if ranked and best == 4 and clear and loose_ok:
            return self._result(ranked[0], 80, method + "_ranked", "auto", lst)
        return self._result(None, 100, method + "_ambiguous", "review", lst)

    def _covers(self, code, raw_toks):
        ks = self._key_tokens.get(code)
        if ks is None:
            ks = []
            for k, t in _tiers(self.meta[code]["rec"]).items():
                if t <= 2:
                    toks = frozenset(w for w in k.split() if w not in STOP_TOKENS)
                    if toks:
                        ks.append(toks)
            self._key_tokens[code] = ks
        return any(k <= raw_toks for k in ks)

    def _token_match(self, forms, unit_ucum):
        for f in forms:
            toks = [t for t in f.split() if t not in STOP_TOKENS]
            if not toks or any(t not in self.tok_index for t in toks) or max(len(t) for t in toks) < 4:
                continue
            sets = sorted((self.tok_index[t] for t in toks), key=len)
            cands = set(sets[0])
            for s in sets[1:]:
                cands &= s
            raw_toks = set(toks)
            cands = {c for c in cands if self._covers(c, raw_toks)}
            if not cands:
                continue
            r = self._from_candidates({c: 4 for c in cands}, unit_ucum, "name_tokens")
            if r["loinc_status"] == "auto":
                return r
        return None

    def _source_code(self, raw_name, code):
        if code not in self.meta:
            return self._result(None, 0, "source_code_unknown", "review", [])
        forms = name_forms(raw_name)
        if not forms:
            return self._result(code, 100, "source_code", "auto", [])
        keys = set(_tiers(self.meta[code]["rec"]))
        if any(f in keys for f in forms):
            return self._result(code, 100, "source_code", "auto", [])
        best = max((fuzz.token_sort_ratio(f, k) for f in forms for k in keys), default=0)
        if best >= self.review:
            return self._result(code, 100, "source_code", "auto", [])
        return self._result(code, 60, "source_code_name_conflict", "review", self._cand_list([code]))

    def map(self, raw_name, source_code=None, source_system=None, unit_ucum=None):
        if isinstance(unit_ucum, str):
            unit_ucum = [unit_ucum]
        if source_code and str(source_system or "").upper() in ("LN", "LOINC"):
            return self._source_code(raw_name, source_code)
        forms = name_forms(raw_name)
        if not forms:
            return self._result(None, 0, "none", "unmapped", [])
        first_review = None
        for f in forms:
            hits = self.names.get(f)
            if hits:
                r = self._from_candidates(hits, unit_ucum, "loinc_name")
                if r["loinc_status"] == "auto":
                    return r
                first_review = first_review or r
        r = self._token_match(forms, unit_ucum)
        if r:
            return r
        scored = {}
        for f in forms:
            if len(f) < 4:
                continue
            for choice, score, _ in process.extract(f, self.fuzzy_choices, scorer=fuzz.token_sort_ratio, limit=8):
                for c, t in self.names[choice].items():
                    if t <= 2 and 0 < self.meta[c]["rank"] <= self.rank_auto and score > scored.get(c, 0):
                        scored[c] = score
        if not scored:
            return first_review or self._result(None, 0, "fuzzy", "unmapped", [])
        if unit_ucum and self.units:
            ok = {c: s for c, s in scored.items() if self._compat(c, unit_ucum)}
            if ok:
                scored = ok
        pref = {c: s for c, s in scored.items() if self.meta[c]["system"] in self.default_systems}
        if pref:
            scored = pref
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1], self.meta[kv[0]]["rank"]))
        cl = self._cand_list([c for c, _ in ranked], scored)
        top_l, top_s = ranked[0]
        if top_s < self.review:
            return first_review or self._result(None, top_s, "fuzzy", "unmapped", cl)
        margin_ok = len(ranked) == 1 or top_s - ranked[1][1] >= self.margin
        if top_s >= self.auto and margin_ok:
            return self._result(top_l, top_s, "fuzzy", "auto", cl)
        return self._result(top_l, top_s, "fuzzy", "review", cl)
