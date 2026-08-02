"""Minimal historical source fragment used by the Run-34 fixture.

Provenance: the membership-edge evaluator extracted from
fa03eca2b0406b864618453f30292c0303f34744. This is a closed source fragment,
not a runtime import and not a request to resolve the historical commit.
"""

from __future__ import annotations

from typing import Any


MEMBERSHIP_ROW_KEYS = frozenset(
    {"role", "member", "grantor", "admin_option", "inherit_option", "set_option"}
)

HISTORICAL_MANIFEST: dict[str, Any] = {
    "roles": {
        "runtime": {
            "provider_control_edges": [
                {
                    "parent_role": "sqag_runtime",
                    "member_role": "neondb_owner",
                    "grantor": "cloud_admin",
                    "admin_option": True,
                    "inherit_option": False,
                    "set_option": False,
                }
            ]
        }
    }
}


def validate_runtime_membership_edges(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    enforce_production_identity: bool = True,
) -> tuple[str, ...]:
    """Preserve the historical pre-hardening edge decision surface.

    The historical evaluator validated the single runtime/provider edge and
    runtime-connected rows. It did not yet reject unrelated protected-role
    edges, protected grantors, protected option flags, duplicate protected
    rows, or recursive protected-role paths. Those omissions are exactly what
    the preserved RED tests assert.
    """

    del enforce_production_identity
    roles = manifest.get("roles") if isinstance(manifest, dict) else None
    runtime = roles.get("runtime") if isinstance(roles, dict) else None
    edges = runtime.get("provider_control_edges") if isinstance(runtime, dict) else None
    if type(edges) is not list or len(edges) != 1 or type(edges[0]) is not dict:
        return ("provider_control_edges_count_invalid_expected_1_got_invalid",)

    edge = edges[0]
    expected_row = {
        "role": edge.get("parent_role"),
        "member": edge.get("member_role"),
        "grantor": edge.get("grantor"),
        "admin_option": edge.get("admin_option"),
        "inherit_option": edge.get("inherit_option"),
        "set_option": edge.get("set_option"),
    }
    if type(rows) is not list:
        return ("role_membership_rows_must_be_list",)

    runtime_name = str(edge.get("parent_role"))
    runtime_rows: list[dict[str, Any]] = []
    for row in rows:
        if type(row) is not dict or set(row) != MEMBERSHIP_ROW_KEYS:
            continue
        if row.get("role") == runtime_name or row.get("member") == runtime_name:
            runtime_rows.append(row)

    errors: list[str] = []
    if len(runtime_rows) != 1:
        errors.append(f"runtime_edge_count_invalid_expected_1_got_{len(runtime_rows)}")
    if len(runtime_rows) == 1 and runtime_rows[0] != expected_row:
        errors.append("provider_control_edge_tuple_mismatch")
    return tuple(errors)
