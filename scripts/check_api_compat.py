"""Check a generated API spec against the v1 baseline; exit 1 on a change that breaks callers.

    python scripts/check_api_compat.py
    python scripts/check_api_compat.py --spec <spec.json> --baseline <baseline.json>

The v1 baseline is a snapshot of the contract proposal, not a released API. Replace it
only with the owner's OK (`--write-baseline`), never to make this check pass.
Standard library only.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.api_compat import compatibility_breaks  # noqa: E402

DEFAULT_SPEC = ROOT / "docs" / "quotation-api-v1.openapi.json"
DEFAULT_BASELINE = ROOT / "docs" / "api" / "quotation-api-v1-baseline.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--write-baseline", action="store_true",
                        help="copy the spec to the v1 baseline (only with the owner's OK)")
    args = parser.parse_args(argv)
    text = args.spec.read_text(encoding="utf-8")
    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote baseline {args.baseline.name}")
        return 0
    breaks = compatibility_breaks(json.loads(args.baseline.read_text(encoding="utf-8")), json.loads(text))
    for line in breaks:
        print("Breaking change: " + line, file=sys.stderr)
    if breaks:
        print(f"{len(breaks)} breaking change(s) against {args.baseline.name}. Keep the old shape, mark the "
              "operation preview, or add a new API version.", file=sys.stderr)
        return 1
    print(f"Compatible with {args.baseline.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
