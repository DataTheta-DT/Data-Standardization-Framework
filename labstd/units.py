import math

from .ucum import G_IDX, MOL, UcumError


def sig(v):
    return float(f"{v:.6g}")


class UnitStandardizer:
    def __init__(self, ucum):
        self.ucum = ucum

    def interpretations(self, raw):
        if raw is None or str(raw).strip() == "":
            return []
        c = self.ucum.clean(raw)
        out = []
        if self.ucum.is_valid(c):
            out.append(c)
        cs = self.ucum.ci_to_cs(c)
        if cs and cs not in out:
            out.append(cs)
        return out

    def _factor(self, from_u, to_u):
        try:
            ma, da = self.ucum.parse(from_u)
            mb, db = self.ucum.parse(to_u)
        except UcumError:
            return None
        if da != db or self.ucum.markers(from_u) != self.ucum.markers(to_u):
            return None
        return ma / mb

    def can_convert(self, from_u, to_u):
        return bool(from_u and to_u and self._factor(from_u, to_u) is not None)

    def can_convert_any(self, interps, to_u):
        return any(self.can_convert(u, to_u) for u in interps)

    def pick(self, interps, to_u):
        best, best_score = None, None
        for u in interps:
            f = self._factor(u, to_u) if to_u else None
            if f is None or f <= 0:
                continue
            score = abs(math.log10(f))
            if best is None or score < best_score:
                best, best_score = u, score
        return best if best is not None else (interps[0] if interps else None)

    def mass_mole_mismatch(self, from_u, to_u):
        try:
            ma, da = self.ucum.parse(from_u)
            mb, db = self.ucum.parse(to_u)
        except UcumError:
            return False
        if da == db:
            return False
        others = [i for i in range(len(da)) if i not in (MOL, G_IDX)]
        if not all(da[i] == db[i] for i in others):
            return False
        k = da[MOL]
        if k != 0 and db[MOL] == 0 and db[G_IDX] - da[G_IDX] == k:
            return True
        k = db[MOL]
        return k != 0 and da[MOL] == 0 and da[G_IDX] - db[G_IDX] == k

    def convert(self, value, from_u, to_u):
        if value is None:
            return None, "no_value"
        f = self._factor(from_u, to_u)
        if f is None:
            if self.mass_mole_mismatch(from_u, to_u):
                return None, "unsupported_mass_mole"
            try:
                same_dims = self.ucum.parse(from_u)[1] == self.ucum.parse(to_u)[1]
            except UcumError:
                same_dims = False
            return None, "unsupported_conversion" if same_dims else "no_path"
        return sig(value * f), "identity" if math.isclose(f, 1.0, rel_tol=1e-9) else "converted"
