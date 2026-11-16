"""Compare LLM providers on local report files (no Databricks needed).

    python scripts/compare_llm_providers.py --input sample_inputs/real --ping
    python scripts/compare_llm_providers.py --input a.pdf b.pdf --providers gemini,groq --out comparison_out

Keys come from the environment or the .env file in the project root. Providers without a key are skipped.
Needs: pymupdf pandas openpyxl pyyaml rapidfuzz python-dateutil (+ the OCR packages only for scanned files).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from labstd.compare import compare, export, items_from_paths  # noqa: E402
from labstd.env import load_dotenv  # noqa: E402
from labstd.llm_router import build_llm  # noqa: E402
from labstd.service import load_config  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", nargs="+", help="report files or folders (PDF / image)")
    ap.add_argument("--providers", default="", help="comma separated; default = every provider that has a key")
    ap.add_argument("--max-files", type=int, default=0, help="stop after this many files (0 = all)")
    ap.add_argument("--out", default=os.path.join(ROOT, "comparison_out"), help="folder for the CSV / Excel results")
    ap.add_argument("--ping", action="store_true", help="test every provider with one tiny call and exit when --input is not given")
    a = ap.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    cfg = load_config(ROOT)
    router = build_llm(cfg.get("llm"), only=[p.strip() for p in a.providers.split(",") if p.strip()] or None)
    print(router.status(), "\n")
    if a.ping:
        print(pd.DataFrame(router.ping_all()).to_string(index=False), "\n")
    if not a.input:
        return
    clients = [c for c in router.clients if c.enabled]
    if not clients:
        sys.exit("No provider has an API key. Fill in .env first.")

    items, skipped = items_from_paths(a.input, cfg)
    for name, why in skipped:
        print(f"skipped {name}: {why}")
    if a.max_files:
        items = items[: a.max_files]
    if not items:
        sys.exit("No files to compare.")
    result = compare(items, clients, progress=lambda m: print("  ", m))
    pd.set_option("display.width", 220, "display.max_columns", 30, "display.max_colwidth", 60)
    print("\n", result["summary"].to_string(index=False))
    print("\nfiles written:", *export(result, a.out), sep="\n  ")


if __name__ == "__main__":
    main()
