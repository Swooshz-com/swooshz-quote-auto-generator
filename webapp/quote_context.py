"""Immutable, server-derived quote authority; never a client payload or session."""

from dataclasses import dataclass


QUOTE_OPERATIONS = frozenset({"create", "draft", "price", "export", "poll", "basis", "result", "artifact"})
QUOTE_READ_OPERATIONS = frozenset({"poll", "basis", "result", "artifact"})


@dataclass(frozen=True, slots=True)
class QuoteContext:
    auth_mode: str
    owner_subject: str
    tenant_id: str
    workspace_id: str
    caller_id: str
    auth_revision: str
    operations: frozenset[str]
    projection: str
    authority_revision: str
    authority_snapshot: tuple[tuple[str, str], ...]
    request_reference: str

    def __post_init__(self):
        if self.auth_mode not in {"staff", "service"}:
            raise ValueError("Invalid quote authority")
        for value in (self.owner_subject, self.tenant_id, self.workspace_id, self.caller_id,
                      self.auth_revision, self.authority_revision, self.request_reference):
            if type(value) is not str or not value or len(value) > 512:
                raise ValueError("Incomplete quote authority")
        # Copy collections so a caller cannot mutate authority after admission.
        operations = frozenset(self.operations)
        snapshot = tuple(tuple(pair) for pair in self.authority_snapshot)
        if not operations <= QUOTE_OPERATIONS or any(
            len(pair) != 2 or any(type(v) is not str or not v or len(v) > 512 for v in pair)
            for pair in snapshot
        ):
            raise ValueError("Invalid quote authority")
        expected = "CustomerEstimateView" if self.auth_mode == "service" else "staff_internal"
        if self.projection != expected:
            raise ValueError("Invalid quote projection")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "authority_snapshot", snapshot)

    def owns(self, *, auth_mode: str, tenant_id: str, workspace_id: str,
             owner_subject: str, caller_id: str) -> bool:
        """Exact conjunctive boundary for future context-required repositories.

        This predicate does not load a resource, validate expiry or grant access.
        Repositories must include these keys AND resource ID in their lookup.
        """
        return (self.auth_mode, self.tenant_id, self.workspace_id, self.owner_subject, self.caller_id) == (
            auth_mode, tenant_id, workspace_id, owner_subject, caller_id)
