"""Typed, fail-closed runner for the schema-v3 privilege contract.

Connections and SQL are supplied through a typed adapter.  This module owns
the phase machine, canonical partition proof, exact inverse boundary, and
public-safe terminal receipt.  It has no connection discovery or secret CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import json
from typing import Any, Mapping, Protocol, Sequence

from scripts import validate_runtime_privilege_contract as canonical_contract


class Phase(str, Enum):
    ADMISSION = "admission"
    PLAN = "plan"
    FORWARD = "forward"
    FINAL_VERIFY = "final_verify"


class RollbackStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    RESTORED = "restored"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class SemanticCode(str, Enum):
    PRESTATE_MISMATCH = "prestate_mismatch"
    CONTRACT_INVALID = "contract_invalid"
    TABLE_ACL_TUPLE_SHAPE_INVALID = "table_acl_tuple_shape_invalid"
    PARTITION_COVERAGE = "partition_coverage"
    PARTITION_DISJOINTNESS = "partition_disjointness"
    AUTHORISED_DELTA_DATABASE = "authorized_delta_database"
    AUTHORISED_DELTA_SCHEMA = "authorized_delta_schema"
    AUTHORISED_DELTA_TABLE_ACL = "authorized_delta_table_acl"
    AUTHORISED_DELTA_COLUMN_ACL = "authorized_delta_column_acl"
    PRESERVED_COMPLEMENT = "preserved_complement"
    CANONICAL_PROVIDER_ADMIN_VERIFIER = "canonical_provider_admin_verifier"
    CANONICAL_OPERATOR_VERIFIER = "canonical_operator_verifier"
    INVERSE_CAPTURE = "inverse_capture"
    FORWARD_MUTATION = "forward_mutation"
    RESTORATION = "restoration"


CHECK_NAMES: tuple[str, ...] = (
    SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER.value,
    SemanticCode.CANONICAL_OPERATOR_VERIFIER.value,
    SemanticCode.AUTHORISED_DELTA_DATABASE.value,
    SemanticCode.AUTHORISED_DELTA_SCHEMA.value,
    SemanticCode.AUTHORISED_DELTA_TABLE_ACL.value,
    SemanticCode.AUTHORISED_DELTA_COLUMN_ACL.value,
    SemanticCode.PRESERVED_COMPLEMENT.value,
    SemanticCode.PARTITION_COVERAGE.value,
    SemanticCode.PARTITION_DISJOINTNESS.value,
)
_AUTHORIZED_SURFACES = frozenset({"database", "schema", "table_acl", "column_acl"})
_REQUIRED_SURFACES = frozenset(_AUTHORIZED_SURFACES)
_KNOWN_SURFACES = frozenset(
    {
        "database",
        "schema",
        "table_acl",
        "column_acl",
        "membership",
        "role_attributes",
        "object_ownership",
        "default_acls",
        "unrelated_objects",
        "unrelated_data",
    }
)
_SAFE_CODES = frozenset(item.value for item in SemanticCode)


@lru_cache(maxsize=1)
def _default_manifest() -> dict[str, Any]:
    return canonical_contract.validate_manifest()


def canonical_manifest() -> Mapping[str, Any]:
    """Return the validated repository contract used by the runner."""
    return _default_manifest()


def _validated_manifest(manifest: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return _default_manifest() if manifest is None else canonical_contract.validate_manifest(manifest)


class GateError(RuntimeError):
    """Known semantic failure with a stable, public-safe code."""

    def __init__(
        self,
        code: SemanticCode | str,
        phase: Phase,
        *,
        mutation_started: bool = False,
    ) -> None:
        safe_code = code.value if isinstance(code, SemanticCode) else str(code)
        if safe_code not in _SAFE_CODES:
            raise ValueError("unknown semantic failure code")
        self.code = safe_code
        self.phase = phase
        self.mutation_started = bool(mutation_started)
        super().__init__(safe_code)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("unsupported snapshot value")


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value):
            return {item[0]: _thaw(item[1]) for item in value}
        return tuple(_thaw(item) for item in value)
    return value


TableAclRow = tuple[str, str, str, str, bool]


def validate_table_acl_row(row: Sequence[Any], *, phase: Phase = Phase.PLAN) -> TableAclRow:
    """Require (table, owner, grantee, privilege, grantable)."""
    if isinstance(row, (str, bytes)) or len(row) != 5:
        raise GateError(SemanticCode.TABLE_ACL_TUPLE_SHAPE_INVALID, phase)
    table, owner, grantee, privilege, grantable = row
    if not all(isinstance(item, str) and item for item in (table, owner, grantee, privilege)):
        raise GateError(SemanticCode.TABLE_ACL_TUPLE_SHAPE_INVALID, phase)
    if not isinstance(grantable, bool):
        raise GateError(SemanticCode.TABLE_ACL_TUPLE_SHAPE_INVALID, phase)
    return (table, owner, grantee, privilege, grantable)


def select_authorised_table_acl_rows(
    rows: Sequence[Sequence[Any]],
    declared_tables: Sequence[str],
    authorised_grantees: Sequence[str],
    *,
    phase: Phase = Phase.PLAN,
) -> tuple[TableAclRow, ...]:
    """Select declared tables and bind the authorised grantee to row[2]."""
    tables = frozenset(declared_tables)
    grantees = frozenset(authorised_grantees)
    if not all(isinstance(item, str) and item for item in (*tables, *grantees)):
        raise GateError(SemanticCode.CONTRACT_INVALID, phase)
    checked = tuple(validate_table_acl_row(row, phase=phase) for row in rows)
    return tuple(row for row in checked if row[0] in tables and row[2] in grantees)


@dataclass(frozen=True)
class PrivilegeSnapshot:
    """Immutable internal snapshot; never emitted by SafeReceipt."""

    _items: tuple[tuple[str, Any], ...]

    @classmethod
    def from_mapping(
        cls,
        observations: Mapping[str, Any],
        *,
        phase: Phase = Phase.PLAN,
    ) -> "PrivilegeSnapshot":
        if not isinstance(observations, Mapping) or not _REQUIRED_SURFACES.issubset(observations):
            raise GateError(SemanticCode.PRESTATE_MISMATCH, phase)
        normalized: dict[str, Any] = {}
        for surface, value in observations.items():
            if not isinstance(surface, str) or not surface:
                raise GateError(SemanticCode.PRESTATE_MISMATCH, phase)
            if surface == "table_acl":
                if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                    raise GateError(SemanticCode.TABLE_ACL_TUPLE_SHAPE_INVALID, phase)
                normalized[surface] = tuple(
                    sorted(validate_table_acl_row(row, phase=phase) for row in value)
                )
            else:
                try:
                    normalized[surface] = _freeze(value)
                except (TypeError, ValueError):
                    raise GateError(SemanticCode.PRESTATE_MISMATCH, phase) from None
        return cls(tuple(sorted(normalized.items())))

    def value(self, surface: str) -> Any:
        for key, value in self._items:
            if key == surface:
                return value
        return None

    def mapping(self) -> dict[str, Any]:
        return dict(self._items)


@dataclass(frozen=True)
class Identity:
    session_user: str
    current_user: str


@dataclass(frozen=True)
class AdmissionEvidence:
    provider_admin: Identity
    operator: Identity
    same_target: bool
    postgres_major: int
    pg_authid_access_count: int = 0


@dataclass(frozen=True)
class InverseCapture:
    prestate: PrivilegeSnapshot


@dataclass(frozen=True)
class MutationOutcome:
    started: bool


@dataclass(frozen=True)
class PartitionProof:
    coverage: bool
    disjointness: bool
    authorised_count: int
    preserved_count: int


@dataclass(frozen=True)
class SurfaceComparison:
    before: Any
    target: Any
    matches: bool


@dataclass(frozen=True)
class ConvergencePlan:
    prestate: PrivilegeSnapshot
    target: PrivilegeSnapshot
    inverse: InverseCapture
    authorised: Mapping[str, SurfaceComparison]
    preserved_before: Any
    partition: PartitionProof


class ConvergenceAdapter(Protocol):
    def admit(self, manifest: Mapping[str, Any]) -> AdmissionEvidence: ...

    def read_snapshot(self) -> PrivilegeSnapshot | Mapping[str, Any]: ...

    def target_snapshot(self, manifest: Mapping[str, Any]) -> PrivilegeSnapshot | Mapping[str, Any]: ...

    def capture_inverse(self, prestate: PrivilegeSnapshot) -> InverseCapture: ...

    def apply_authorised_delta(self, plan: ConvergencePlan) -> MutationOutcome: ...

    def verify_canonical(self, context: str, manifest: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def rollback(self, inverse: InverseCapture) -> None: ...

    def verify_restoration(self, inverse: InverseCapture) -> bool: ...


def _as_snapshot(value: PrivilegeSnapshot | Mapping[str, Any], phase: Phase) -> PrivilegeSnapshot:
    return value if isinstance(value, PrivilegeSnapshot) else PrivilegeSnapshot.from_mapping(value, phase=phase)


def _contract_roles(manifest: Mapping[str, Any]) -> frozenset[str]:
    roles = manifest.get("roles")
    if not isinstance(roles, Mapping):
        raise GateError(SemanticCode.CONTRACT_INVALID, Phase.PLAN)
    names = {
        item.get("name")
        for item in roles.values()
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    if len(names) != 3:
        raise GateError(SemanticCode.CONTRACT_INVALID, Phase.PLAN)
    return frozenset(names)


def _contract_tables(manifest: Mapping[str, Any]) -> frozenset[str]:
    namespace = manifest.get("namespace")
    tables = namespace.get("tables") if isinstance(namespace, Mapping) else None
    if not isinstance(tables, list) or not all(isinstance(item, str) and item for item in tables):
        raise GateError(SemanticCode.CONTRACT_INVALID, Phase.PLAN)
    return frozenset(tables)


def _authorised_table_rows(snapshot: PrivilegeSnapshot, manifest: Mapping[str, Any]) -> tuple[TableAclRow, ...]:
    return select_authorised_table_acl_rows(
        snapshot.value("table_acl") or (),
        _contract_tables(manifest),
        _contract_roles(manifest),
    )


def _preserved_projection(snapshot: PrivilegeSnapshot, manifest: Mapping[str, Any]) -> Any:
    selected = set(_authorised_table_rows(snapshot, manifest))
    output: list[tuple[str, Any]] = []
    for surface, value in snapshot._items:
        if surface == "table_acl":
            value = tuple(row for row in value if row not in selected)
        elif surface in _AUTHORIZED_SURFACES:
            continue
        output.append((surface, value))
    return tuple(output)


def _partition_keys(snapshot: PrivilegeSnapshot, manifest: Mapping[str, Any]) -> tuple[set[Any], set[Any]]:
    authorised: set[Any] = set()
    preserved: set[Any] = set()
    selected = set(_authorised_table_rows(snapshot, manifest))
    for surface, value in snapshot._items:
        if surface not in _KNOWN_SURFACES:
            continue
        if surface == "table_acl":
            for row in value:
                key = (surface, *row)
                (authorised if row in selected else preserved).add(key)
        elif surface in _AUTHORIZED_SURFACES:
            authorised.add((surface, value))
        else:
            preserved.add((surface, value))
    return authorised, preserved


def _partition_proof(
    before: PrivilegeSnapshot,
    target: PrivilegeSnapshot,
    manifest: Mapping[str, Any],
) -> PartitionProof:
    before_authorised, before_preserved = _partition_keys(before, manifest)
    target_authorised, target_preserved = _partition_keys(target, manifest)
    authorised = before_authorised | target_authorised
    preserved = before_preserved | target_preserved
    all_keys: set[Any] = set()
    unknown: set[str] = set()
    for snapshot in (before, target):
        for surface, value in snapshot._items:
            if surface not in _KNOWN_SURFACES:
                unknown.add(surface)
                continue
            if surface == "table_acl":
                all_keys.update((surface, *row) for row in value)
            else:
                all_keys.add((surface, value))
    return PartitionProof(
        coverage=not unknown and authorised | preserved == all_keys,
        disjointness=not authorised.intersection(preserved),
        authorised_count=len(authorised),
        preserved_count=len(preserved),
    )


def build_plan(
    prestate: PrivilegeSnapshot | Mapping[str, Any],
    target: PrivilegeSnapshot | Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
    *,
    _validated: bool = False,
) -> ConvergencePlan:
    contract = manifest if _validated else _validated_manifest(manifest)
    before = _as_snapshot(prestate, Phase.PLAN)
    desired = _as_snapshot(target, Phase.PLAN)
    authorised: dict[str, SurfaceComparison] = {}
    for surface in sorted(_AUTHORIZED_SURFACES):
        before_value = (
            _authorised_table_rows(before, contract)
            if surface == "table_acl"
            else before.value(surface)
        )
        target_value = (
            _authorised_table_rows(desired, contract)
            if surface == "table_acl"
            else desired.value(surface)
        )
        authorised[surface] = SurfaceComparison(before_value, target_value, before_value == target_value)
    proof = _partition_proof(before, desired, contract)
    if not proof.coverage:
        raise GateError(SemanticCode.PARTITION_COVERAGE, Phase.PLAN)
    if not proof.disjointness:
        raise GateError(SemanticCode.PARTITION_DISJOINTNESS, Phase.PLAN)
    return ConvergencePlan(
        prestate=before,
        target=desired,
        inverse=InverseCapture(before),
        authorised=authorised,
        preserved_before=_preserved_projection(before, contract),
        partition=proof,
    )


def _check_final_state(
    plan: ConvergencePlan,
    final_state: PrivilegeSnapshot,
    manifest: Mapping[str, Any],
) -> dict[str, str]:
    results = {name: "PASS" for name in CHECK_NAMES}
    results[SemanticCode.AUTHORISED_DELTA_DATABASE.value] = "PASS" if final_state.value("database") == plan.target.value("database") else "FAIL"
    results[SemanticCode.AUTHORISED_DELTA_SCHEMA.value] = "PASS" if final_state.value("schema") == plan.target.value("schema") else "FAIL"
    results[SemanticCode.AUTHORISED_DELTA_TABLE_ACL.value] = "PASS" if _authorised_table_rows(final_state, manifest) == _authorised_table_rows(plan.target, manifest) else "FAIL"
    results[SemanticCode.AUTHORISED_DELTA_COLUMN_ACL.value] = "PASS" if final_state.value("column_acl") == plan.target.value("column_acl") else "FAIL"
    results[SemanticCode.PRESERVED_COMPLEMENT.value] = "PASS" if _preserved_projection(final_state, manifest) == plan.preserved_before else "FAIL"
    results[SemanticCode.PARTITION_COVERAGE.value] = "PASS" if plan.partition.coverage else "FAIL"
    results[SemanticCode.PARTITION_DISJOINTNESS.value] = "PASS" if plan.partition.disjointness else "FAIL"
    return results


@dataclass(frozen=True)
class SafeReceipt:
    verdict: str
    phase: str
    mutation_started: bool
    final_verification_reached: bool
    rollback: str
    semantic_failure_code: str | None = None
    unexpected_exception_code: str | None = None
    checks: Mapping[str, str] = field(default_factory=dict)
    counts: Mapping[str, int] = field(default_factory=dict)
    final_database_owner: str | None = None
    final_public_schema_owner: str | None = None
    restoration_verified: bool | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "phase": self.phase,
            "mutation_started": bool(self.mutation_started),
            "final_verification_reached": bool(self.final_verification_reached),
            "rollback": self.rollback,
            "semantic_failure_code": self.semantic_failure_code,
            "unexpected_exception_code": self.unexpected_exception_code,
            "checks": {name: self.checks.get(name, "UNKNOWN") for name in CHECK_NAMES},
            "counts": {str(key): int(value) for key, value in self.counts.items()},
            "final_database_owner": self.final_database_owner,
            "final_public_schema_owner": self.final_public_schema_owner,
            "restoration_verified": self.restoration_verified,
        }

    def to_public_json(self) -> str:
        return json.dumps(self.to_public_dict(), sort_keys=True, separators=(",", ":"))


def _counts(plan: ConvergencePlan | None) -> dict[str, int]:
    if plan is None:
        return {}
    return {
        "authorised_delta_surfaces": len(plan.authorised),
        "authorised_partition_items": plan.partition.authorised_count,
        "preserved_partition_items": plan.partition.preserved_count,
        "table_acl_rows": len(plan.prestate.value("table_acl") or ()),
    }


def _checks() -> dict[str, str]:
    return {name: "UNKNOWN" for name in CHECK_NAMES}


def _failure(
    *,
    phase: Phase,
    mutation_started: bool,
    final_reached: bool,
    semantic_code: str | None,
    unexpected: bool,
    checks: Mapping[str, str],
    plan: ConvergencePlan | None,
    rollback: RollbackStatus = RollbackStatus.NOT_REQUIRED,
    restoration_verified: bool | None = None,
) -> SafeReceipt:
    return SafeReceipt(
        verdict="BLOCKED",
        phase=phase.value,
        mutation_started=mutation_started,
        final_verification_reached=final_reached,
        rollback=rollback.value,
        semantic_failure_code=None if unexpected else semantic_code,
        unexpected_exception_code="unexpected_exception" if unexpected else None,
        checks=checks,
        counts=_counts(plan),
        restoration_verified=restoration_verified,
    )


def _safe_owner(snapshot: PrivilegeSnapshot, surface: str, manifest: Mapping[str, Any]) -> str | None:
    value = _thaw(snapshot.value(surface))
    if not isinstance(value, Mapping):
        return None
    owner = value.get("owner")
    ownership = manifest.get("ownership")
    roles = manifest.get("roles")
    allowed = set(ownership.values()) if isinstance(ownership, Mapping) else set()
    if isinstance(roles, Mapping) and isinstance(roles.get("migrator"), Mapping):
        allowed.add(roles["migrator"].get("name"))
    return owner if isinstance(owner, str) and owner in allowed else None


def _mutation_started(adapter: Any, exc: BaseException, started: bool) -> bool:
    return bool(started or getattr(exc, "mutation_started", False) or getattr(adapter, "mutation_started", False))


def _rollback(
    adapter: ConvergenceAdapter,
    inverse: InverseCapture,
    prestate: PrivilegeSnapshot,
    *,
    phase: Phase,
    final_reached: bool,
    semantic_code: str | None,
    unexpected: bool,
    checks: Mapping[str, str],
    plan: ConvergencePlan,
) -> SafeReceipt:
    try:
        adapter.rollback(inverse)
    except Exception:
        return _failure(
            phase=phase,
            mutation_started=True,
            final_reached=final_reached,
            semantic_code=semantic_code or SemanticCode.RESTORATION.value,
            unexpected=unexpected,
            checks=checks,
            plan=plan,
            rollback=RollbackStatus.FAILED,
            restoration_verified=False,
        )
    try:
        restored = _as_snapshot(adapter.read_snapshot(), Phase.FINAL_VERIFY)
        exact = bool(adapter.verify_restoration(inverse)) and restored == prestate
    except Exception:
        exact = False
    return _failure(
        phase=phase,
        mutation_started=True,
        final_reached=final_reached,
        semantic_code=semantic_code,
        unexpected=unexpected,
        checks=checks,
        plan=plan,
        rollback=RollbackStatus.RESTORED if exact else RollbackStatus.AMBIGUOUS,
        restoration_verified=exact,
    )


def run_convergence(
    adapter: ConvergenceAdapter,
    *,
    manifest: Mapping[str, Any] | None = None,
) -> SafeReceipt:
    """Execute all phases and return a receipt containing allowlisted fields only."""
    checks = _checks()
    phase = Phase.ADMISSION
    mutation_started = False
    final_reached = False
    plan: ConvergencePlan | None = None
    prestate: PrivilegeSnapshot | None = None
    inverse: InverseCapture | None = None
    try:
        contract = _validated_manifest(manifest)
        admission = adapter.admit(contract)
        expected_provider = contract["ownership"]["database_owner"]
        expected_operator = contract["roles"]["migrator"]["name"]
        if (
            not isinstance(admission, AdmissionEvidence)
            or admission.provider_admin.session_user != expected_provider
            or admission.provider_admin.current_user != expected_provider
            or admission.operator.session_user != expected_operator
            or admission.operator.current_user != expected_operator
            or not admission.same_target
            or int(admission.postgres_major) != 17
            or int(admission.pg_authid_access_count) != 0
        ):
            raise GateError(SemanticCode.PRESTATE_MISMATCH, phase)
        prestate = _as_snapshot(adapter.read_snapshot(), phase)
        phase = Phase.PLAN
        target = _as_snapshot(adapter.target_snapshot(contract), phase)
        plan = build_plan(prestate, target, contract, _validated=True)
        inverse = adapter.capture_inverse(prestate)
        if not isinstance(inverse, InverseCapture) or inverse.prestate != prestate:
            raise GateError(SemanticCode.INVERSE_CAPTURE, phase)
    except GateError as exc:
        return _failure(
            phase=exc.phase,
            mutation_started=False,
            final_reached=False,
            semantic_code=exc.code,
            unexpected=False,
            checks=checks,
            plan=plan,
        )
    except canonical_contract.RuntimePrivilegeContractError:
        return _failure(
            phase=phase,
            mutation_started=False,
            final_reached=False,
            semantic_code=SemanticCode.CONTRACT_INVALID.value,
            unexpected=False,
            checks=checks,
            plan=plan,
        )
    except Exception:
        return _failure(
            phase=phase,
            mutation_started=False,
            final_reached=False,
            semantic_code=None,
            unexpected=True,
            checks=checks,
            plan=plan,
        )

    assert plan is not None and inverse is not None and prestate is not None
    try:
        phase = Phase.FORWARD
        try:
            outcome = adapter.apply_authorised_delta(plan)
            mutation_started = bool(outcome.started) if isinstance(outcome, MutationOutcome) else bool(outcome)
        except GateError as exc:
            mutation_started = _mutation_started(adapter, exc, mutation_started)
            if mutation_started:
                return _rollback(
                    adapter,
                    inverse,
                    prestate,
                    phase=exc.phase,
                    final_reached=False,
                    semantic_code=exc.code,
                    unexpected=False,
                    checks=checks,
                    plan=plan,
                )
            return _failure(
                phase=exc.phase,
                mutation_started=False,
                final_reached=False,
                semantic_code=exc.code,
                unexpected=False,
                checks=checks,
                plan=plan,
            )
        except Exception as exc:
            mutation_started = _mutation_started(adapter, exc, mutation_started)
            if mutation_started:
                return _rollback(
                    adapter,
                    inverse,
                    prestate,
                    phase=phase,
                    final_reached=False,
                    semantic_code=None,
                    unexpected=True,
                    checks=checks,
                    plan=plan,
                )
            return _failure(
                phase=phase,
                mutation_started=False,
                final_reached=False,
                semantic_code=None,
                unexpected=True,
                checks=checks,
                plan=plan,
            )

        phase = Phase.FINAL_VERIFY
        final_reached = True
        try:
            provider_result = adapter.verify_canonical("provider_admin", contract)
        except GateError as exc:
            checks[SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER.value] = "FAIL"
            raise exc
        except canonical_contract.RuntimePrivilegeContractError:
            checks[SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER.value] = "FAIL"
            raise GateError(SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER, phase, mutation_started=True) from None
        if not isinstance(provider_result, Mapping) or provider_result.get("status") != "verified":
            checks[SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER.value] = "FAIL"
            raise GateError(SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER, phase, mutation_started=True)
        checks[SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER.value] = "PASS"

        try:
            operator_result = adapter.verify_canonical("operator", contract)
        except GateError as exc:
            checks[SemanticCode.CANONICAL_OPERATOR_VERIFIER.value] = "FAIL"
            raise exc
        except canonical_contract.RuntimePrivilegeContractError:
            checks[SemanticCode.CANONICAL_OPERATOR_VERIFIER.value] = "FAIL"
            raise GateError(SemanticCode.CANONICAL_OPERATOR_VERIFIER, phase, mutation_started=True) from None
        if not isinstance(operator_result, Mapping) or operator_result.get("status") != "verified":
            checks[SemanticCode.CANONICAL_OPERATOR_VERIFIER.value] = "FAIL"
            raise GateError(SemanticCode.CANONICAL_OPERATOR_VERIFIER, phase, mutation_started=True)
        checks[SemanticCode.CANONICAL_OPERATOR_VERIFIER.value] = "PASS"

        final_state = _as_snapshot(adapter.read_snapshot(), phase)
        checks.update(_check_final_state(plan, final_state, contract))
        for code in (
            SemanticCode.AUTHORISED_DELTA_DATABASE.value,
            SemanticCode.AUTHORISED_DELTA_SCHEMA.value,
            SemanticCode.AUTHORISED_DELTA_TABLE_ACL.value,
            SemanticCode.AUTHORISED_DELTA_COLUMN_ACL.value,
            SemanticCode.PRESERVED_COMPLEMENT.value,
            SemanticCode.PARTITION_COVERAGE.value,
            SemanticCode.PARTITION_DISJOINTNESS.value,
        ):
            if checks[code] != "PASS":
                raise GateError(code, phase, mutation_started=True)
        return SafeReceipt(
            verdict="PASS",
            phase=phase.value,
            mutation_started=mutation_started,
            final_verification_reached=True,
            rollback=RollbackStatus.NOT_REQUIRED.value,
            checks=checks,
            counts=_counts(plan),
            final_database_owner=_safe_owner(final_state, "database", contract),
            final_public_schema_owner=_safe_owner(final_state, "schema", contract),
        )
    except GateError as exc:
        return _rollback(
            adapter,
            inverse,
            prestate,
            phase=exc.phase,
            final_reached=final_reached,
            semantic_code=exc.code,
            unexpected=False,
            checks=checks,
            plan=plan,
        )
    except canonical_contract.RuntimePrivilegeContractError:
        return _rollback(
            adapter,
            inverse,
            prestate,
            phase=phase,
            final_reached=final_reached,
            semantic_code=SemanticCode.CONTRACT_INVALID.value,
            unexpected=False,
            checks=checks,
            plan=plan,
        )
    except Exception:
        return _rollback(
            adapter,
            inverse,
            prestate,
            phase=phase,
            final_reached=final_reached,
            semantic_code=None,
            unexpected=True,
            checks=checks,
            plan=plan,
        )


__all__ = [
    "AdmissionEvidence",
    "CHECK_NAMES",
    "ConvergenceAdapter",
    "ConvergencePlan",
    "GateError",
    "Identity",
    "InverseCapture",
    "MutationOutcome",
    "PartitionProof",
    "Phase",
    "PrivilegeSnapshot",
    "SafeReceipt",
    "SemanticCode",
    "build_plan",
    "canonical_manifest",
    "run_convergence",
    "select_authorised_table_acl_rows",
    "validate_table_acl_row",
]
