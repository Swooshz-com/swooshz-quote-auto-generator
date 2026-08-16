#!/usr/bin/env python3
"""Validate the SQAG A23 PostgreSQL-17 finality contract.

The real-reference mode is intentionally explicit and non-skippable.  A
static invocation can validate the schema/policy contract, but its receipt is
marked synthetic and can never satisfy the A/B/C/P gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.postgresql17_finality_engine import (  # noqa: E402
    ENGINE_VERSION,
    FinalityError,
    FieldDescriptor,
    ExecutionTrace,
    FinalitySnapshot,
    Observation,
    PolicyRegistry,
    classify_field_policy,
    load_coverage_manifest,
)


def validate_static_contract() -> dict[str, object]:
    coverage = load_coverage_manifest(ROOT)
    descriptors = (
        FieldDescriptor("pg_catalog", "pg_class", "r", "relname", "name"),
        FieldDescriptor("pg_catalog", "pg_class", "r", "relnamespace", "oid"),
        FieldDescriptor("pg_catalog", "pg_class", "r", "relpages", "integer"),
        FieldDescriptor("pg_catalog", "pg_authid", "r", "rolpassword", "text"),
        FieldDescriptor("pg_catalog", "pg_trigger", "r", "tgrelid", "oid"),
    )
    registry = PolicyRegistry(descriptors)
    observations = tuple(
        Observation.from_value(
            reference_id="synthetic",
            descriptor=descriptor,
            policy=registry.policy_for(descriptor.key),
            row_identity="synthetic-row",
            object_kind="catalogue:r",
            value=None,
            boundary=True,
        )
        for descriptor in descriptors
    )
    snapshot = FinalitySnapshot.build(
        reference_id="synthetic",
        observations=observations,
        registry=registry,
        trace=ExecutionTrace.synthetic(registry.field_keys, len(observations)),
    )
    return {
        "collector_mode": "synthetic_contract_only",
        "synthetic": True,
        "real_connection": False,
        "engine_version": ENGINE_VERSION,
        "coverage_schema_version": coverage.get("schema_version"),
        "policy_registry_digest": registry.digest,
        "snapshot": snapshot.public_dict(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate SQAG A23 PostgreSQL-17 finality.")
    parser.add_argument("--real-references", action="store_true", help="Run independent live Docker PostgreSQL-17 A/B/C/P proof.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        if args.real_references:
            from scripts.postgresql17_reference_harness import run_real_references

            report = run_real_references(args.run_id)
        else:
            report = validate_static_contract()
    except FinalityError as exc:
        report = {"status": "RED", "error_code": exc.code}
        if args.as_json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"RED: {exc.code}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        mode = "live PostgreSQL-17 A/B/C/P" if args.real_references else "synthetic contract"
        print(f"A23 finality validation passed ({mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
