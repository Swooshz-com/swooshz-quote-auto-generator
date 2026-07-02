#!/usr/bin/env python3
"""Report KQAG production-readiness posture without printing private values."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


def main() -> int:
    status = webapp.production_readiness_status()
    print(json.dumps(status, indent=2, ensure_ascii=True))
    return 0 if status["production_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
