#!/usr/bin/env python3
"""Static and package validation for the SQAG A24 finality boundary."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postgresql17_finality_authority import (  # noqa: E402
    AuthorityError,
    load_authority_package,
)

IMPLEMENTATION_PATHS = (
    ROOT / "scripts" / "postgresql17_finality_authority.py",
    ROOT / "scripts" / "postgresql17_finality_engine.py",
    ROOT / "scripts" / "postgresql17_reference_harness.py",
    ROOT / "scripts" / "validate_postgresql17_finality.py",
)


class ValidationRedError(RuntimeError):
    pass


def _source_checks() -> list[str]:
    failures: list[str] = []
    for path in IMPLEMENTATION_PATHS[:3]:
        if not path.is_file():
            failures.append(f"missing:{path.name}")
            continue
        source = path.read_text(encoding="utf-8")
        if path.name in {"postgresql17_finality_engine.py", "postgresql17_reference_harness.py"} and re.search(r"select\s+\*|\b\w+\.\*", source, re.IGNORECASE):
            failures.append(f"wildcard_projection:{path.name}")
        if "str(value)" in source:
            failures.append(f"generic_value_serializer:{path.name}")
        if "load_authority_package" not in source and path.name != "postgresql17_finality_authority.py":
            failures.append(f"authority_not_referenced:{path.name}")
    engine = (ROOT / "scripts" / "postgresql17_finality_engine.py").read_text(encoding="utf-8")
    for token in ("DISCOVERY_SQL", "validate_closed_world", "compile_safe_projection", "safe_json_value", "FORBIDDEN_RAW", "public_safe_receipt"):
        if token not in engine:
            failures.append(f"engine_contract_missing:{token}")
    harness = (ROOT / "scripts" / "postgresql17_reference_harness.py").read_text(encoding="utf-8")
    for token in ("A", "B", "C", "P", "replay_migrations", "maintenance_witness", "cleanup_verified"):
        if token not in harness:
            failures.append(f"harness_contract_missing:{token}")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for token in ("validate_postgresql17_finality.py", "postgresql17_reference_harness.py", "--real"):
        if token not in workflow:
            failures.append(f"workflow_contract_missing:{token}")
    return failures


def validate(*, ci: bool = False) -> dict[str, object]:
    failures = _source_checks()
    if failures:
        raise ValidationRedError(";".join(failures))
    coverage, policy = load_authority_package()
    for plan_id, plan in policy["safe_projection_plans"].items():
        sql = plan.get("sql") or ""
        if re.search(r"\bselect\s+\*|\.\*", sql, re.IGNORECASE):
            raise ValidationRedError(f"wildcard_projection_plan:{plan_id}")
    if coverage["supported_compatibility_authority"]["postgres_major"] != 17 or policy["production_boundary"] != "DISPOSABLE_REFERENCE_CI_ONLY":
        raise ValidationRedError("compatibility_or_boundary_invalid")
    if policy["proof_fixture_authority"].get("disposable_only") is not True:
        raise ValidationRedError("proof_fixture_not_disposable")
    if not any(item["attnum"] < 0 for item in coverage["descriptors"]):
        raise ValidationRedError("negative_attributes_not_covered")
    if not any(item["attisdropped"] for item in coverage["descriptors"]):
        raise ValidationRedError("dropped_attributes_not_covered")
    if not any(item["namespace"] == "pg_toast" for item in coverage["descriptors"]):
        raise ValidationRedError("toast_metadata_not_covered")
    if not any(item["mode"] == "FORBIDDEN_RAW" for item in policy["safety_bindings"].values()):
        raise ValidationRedError("forbidden_raw_safety_bindings_missing")
    if not any(item["policy"] == "dynamic_scalar" for item in policy["semantic_bindings"].values()):
        raise ValidationRedError("dynamic_semantic_bindings_missing")
    if len(policy["anti_false_requirements"]) < 38:
        raise ValidationRedError("anti_false_matrix_incomplete")
    return {
        "status": "PASS", "mode": "CI" if ci else "STATIC", "package_version": policy["package_version"],
        "descriptor_count": coverage["descriptor_count"], "descriptor_digest": coverage["descriptor_digest"],
        "package_digest": policy["package_digest"], "negative_attributes": True, "dropped_attributes": True,
        "toast_metadata": True, "forbidden_raw_bindings": True, "dynamic_binding": True,
        "universe_regeneration": "not-run",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(validate(ci=args.ci), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except (AuthorityError, ValidationRedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("FAIL: finality validation failed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
