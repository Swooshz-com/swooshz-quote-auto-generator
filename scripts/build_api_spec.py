"""Write docs/quotation-api-v1.openapi.json from the feature catalogue, or check it is current.

    python scripts/build_api_spec.py           # rewrite the spec
    python scripts/build_api_spec.py --check   # exit 1 if the committed spec is stale

Standard library only. Edit webapp/api_catalogue.py or the code that owns a limit,
error or state, never the generated JSON.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.api_spec import SPEC_PATH, build_spec, render  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed spec differs")
    args = parser.parse_args(argv)
    text = render(build_spec())
    current = SPEC_PATH.read_text(encoding="utf-8") if SPEC_PATH.exists() else None
    if args.check:
        if current != text:
            print(f"Stale: {SPEC_PATH.relative_to(ROOT).as_posix()} differs from the catalogue. "
                  "Run: python scripts/build_api_spec.py", file=sys.stderr)
            return 1
        print("API spec is current.")
        return 0
    SPEC_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {SPEC_PATH.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
