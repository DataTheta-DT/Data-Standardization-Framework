import json
import sys

from .ocr import run_ocr


def main():
    out = run_ocr(sys.argv[1], {"backend": sys.argv[2] if len(sys.argv) > 2 else "auto"})
    sys.stdout.write("\n@@RESULT@@" + json.dumps(out))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
