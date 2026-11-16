import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from labstd.refs import load_from_dir, ucum_paths
from labstd.ucum import Ucum


def main():
    loinc = load_from_dir(ROOT)["loinc"]
    ucum = Ucum(*ucum_paths(ROOT))
    lines = ["# Reference validation report", "", "All reference data is official: LOINC 2.83 and UCUM 2.2. Nothing is added by this project.", ""]
    problems = 0

    def check(title, bad):
        nonlocal problems
        lines.append(f"- {title}: {'OK' if not bad else str(len(bad)) + ' problem(s)'}")
        for b in list(bad)[:20]:
            lines.append(f"    - {b}")
        problems += len(bad)

    check("LOINC codes are unique", loinc["loinc_num"][loinc["loinc_num"].duplicated()].tolist())
    check("LOINC rows are all ACTIVE", loinc.loc[loinc["status"] != "ACTIVE", "loinc_num"].tolist())
    check("LOINC rows are all quantitative (Qn)", loinc.loc[loinc["scale_typ"] != "Qn", "loinc_num"].tolist())
    units = sorted(set(u for u in loinc["canonical_unit"] if u))
    unparsed = [u for u in units if not ucum.is_valid(u)]
    lines.append(f"- official LOINC example units: {len(units)} distinct, {len(unparsed)} not handled by the UCUM engine (rows with these units are not converted)")
    for u in unparsed:
        lines.append(f"    - {u}")
    bad_cs = [c for c, v in ucum.cs.units.items() if not v["special"] and not ucum.is_valid(c)]
    check("every non-special case-sensitive UCUM unit parses", bad_cs)
    lines.append("")
    lines.append(f"LOINC lab rows: {len(loinc)}; with consumer names: {(loinc['consumer_names'] != '').sum()}; with a common-test rank: {(loinc['common_test_rank'].astype(int) > 0).sum()}")
    lines.append(f"UCUM case-sensitive units: {len(ucum.cs.units)}; case-insensitive units: {len(ucum.ci.units)}; prefixes: {len(ucum.cs.prefixes)}")
    out = os.path.join(ROOT, "reference", "VALIDATION_REPORT.md")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
