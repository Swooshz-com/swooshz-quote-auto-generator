"""Injected admission storage; never composed by application startup.

PostgreSQL connections must authenticate directly as sqag_runtime and use READ
COMMITTED. SQLite is a synthetic-fixture adapter only, with explicit fixture DDL.
Mutation callers own the transaction and must supply a trusted audit sink whose
stage(connection, event) enlists durable evidence in that same transaction.
No production audit sink, operator protocol or exactly-once job release is wired.
"""

from contextlib import contextmanager
import json
import re

from webapp.quote_api_auth import ServiceCredential
from webapp.quote_context import QuoteContext


MAX_REVISION = 9223372036854775806
MAX_TIME = 253402300799
OPERATIONS = ("create", "draft", "price", "export", "poll", "basis", "result", "artifact")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)
RESOURCE = re.compile(r"[A-Za-z0-9_-]{1,128}", re.ASCII)
CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{21}[AQgw]", re.ASCII)
TENANT_COLUMNS = ("tenant_id", "workspace_id", "state", "authority_revision", "policy_json",
                  "created_at", "expires_at", "revoked_at", "create_minute_start",
                  "create_minute_count", "create_day_start", "create_day_count", "active_jobs",
                  "counter_revision")
CALLER_COLUMNS = ("tenant_id", "caller_id", "owner_subject", "credential_id", "state",
                  "credential_revision", "authority_revision", "operations_mask", "created_at",
                  "expires_at", "revoked_at", "poll_minute_start", "poll_minute_count", "counter_revision")
LOOKUP_COLUMNS = ("credential_id", "verifier", "tenant_id", "workspace_id", "caller_id",
                  "owner_subject", "credential_revision", "tenant_revision", "caller_revision",
                  "operations_mask", "expires_at", "caller_state", "tenant_state", "policy_json")
FIXTURE_LOOKUP = """select c.credential_id, c.verifier, c.tenant_id, t.workspace_id,
    c.caller_id, c.owner_subject, c.credential_revision,
    t.authority_revision as tenant_revision, c.authority_revision as caller_revision,
    c.operations_mask, min(c.expires_at,t.expires_at) as expires_at,
    c.state as caller_state, t.state as tenant_state, t.policy_json
    from public.sqag_quote_api_callers c join public.sqag_quote_api_tenants t
    on t.tenant_id=c.tenant_id where c.credential_id=?"""


class RegistryError(RuntimeError):
    def __init__(self):
        super().__init__("Quote registry unavailable")


class RegistryDenied(RegistryError):
    """Missing, foreign, stale, expired or exhausted authority; no writes."""


def _require(condition):
    if not condition:
        raise RegistryError()


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def validate_policy(text):
    """Closed, bounded policy references. Resource resolution is a later fence."""
    _require(type(text) is str and 2 <= len(text.encode("utf-8")) <= 65536)
    try:
        policy = json.loads(text, object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(RegistryError()))
        _require(type(policy) is dict and set(policy) == {
            "version", "pricing_reference_ids", "profile_ids", "active_pricing_reference_id",
            "active_profile_id", "tax_rate"})
        _require(type(policy["version"]) is int and policy["version"] == 1)
        for plural, active in (("pricing_reference_ids", "active_pricing_reference_id"),
                              ("profile_ids", "active_profile_id")):
            values = policy[plural]
            _require(type(values) is list and len(values) <= 256)
            _require(all(type(v) is str and RESOURCE.fullmatch(v) for v in values))
            _require(len(set(values)) == len(values))
            _require(policy[active] is None or (type(policy[active]) is str and policy[active] in values))
        tax = policy["tax_rate"]
        _require(tax is None or (type(tax) is str and re.fullmatch(
            r"(?:0|1|0\.[0-9]{0,5}[1-9])", tax, re.ASCII)))
        # A missing active pair is unavailable, even if the credential is disabled.
        _require(policy["active_pricing_reference_id"] is not None and policy["active_profile_id"] is not None)
        return policy
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise RegistryError() from None


def _projection(row, columns):
    _require(row is not None)
    if hasattr(row, "keys"):
        _require(set(row.keys()) == set(columns))
        return {key: row[key] for key in columns}
    _require(len(row) == len(columns))
    return dict(zip(columns, row))


def _record(row):
    r = _projection(row, LOOKUP_COLUMNS)
    for key, grammar in (("credential_id", CREDENTIAL), ("tenant_id", ID),
                         ("caller_id", ID), ("workspace_id", RESOURCE)):
        _require(type(r[key]) is str and grammar.fullmatch(r[key]))
    _require(type(r["owner_subject"]) is str and r["owner_subject"].startswith("service:")
             and CREDENTIAL.fullmatch(r["owner_subject"][8:]))
    verifier = r["verifier"]
    if isinstance(verifier, memoryview):
        verifier = verifier.tobytes()
    _require(type(verifier) is bytes and len(verifier) == 32)
    for key in ("credential_revision", "tenant_revision", "caller_revision"):
        _integer(r[key], 1, MAX_REVISION)
    mask = _integer(r["operations_mask"], 0, 255)
    _integer(r["expires_at"], 1, MAX_TIME)
    _require(r["caller_state"] in ("active", "disabled", "revoked")
             and r["tenant_state"] in ("active", "disabled", "revoked"))
    validate_policy(r["policy_json"])
    return ServiceCredential(
        credential_id=r["credential_id"], verifier=verifier, tenant_id=r["tenant_id"],
        workspace_id=r["workspace_id"], caller_id=r["caller_id"], owner_subject=r["owner_subject"],
        revision=str(r["credential_revision"]),
        authority_revision=f't{r["tenant_revision"]}:c{r["caller_revision"]}',
        operations=frozenset(op for bit, op in enumerate(OPERATIONS) if mask & (1 << bit)),
        expires_at=r["expires_at"], enabled=r["caller_state"] == "active",
        tenant_enabled=r["tenant_state"] == "active",
        revoked="revoked" in (r["caller_state"], r["tenant_state"]))


class PostgresCredentialRegistry:
    """Own fresh lookups and transaction-local counter fences, with no cache."""

    def __init__(self, *, connection_factory, audit_sink=None):
        self._connections = connection_factory
        self._audit = audit_sink

    def _identity(self, connection):
        row = connection.execute("select session_user as session_role, current_user as active_role").fetchone()
        roles = _projection(row, ("session_role", "active_role"))
        _require(roles == {"session_role": "sqag_runtime", "active_role": "sqag_runtime"})
        row = connection.execute("show transaction_isolation").fetchone()
        _require(_projection(row, ("transaction_isolation",))["transaction_isolation"] == "read committed")

    def _now(self, connection):
        row = connection.execute("select floor(extract(epoch from clock_timestamp()))::bigint as now").fetchone()
        return _integer(_projection(row, ("now",))["now"], 0, MAX_TIME)

    def _sql(self, sql):
        return sql

    def lookup(self, credential_id):
        if type(credential_id) is not str or not CREDENTIAL.fullmatch(credential_id):
            return None
        try:
            with self._connections() as connection:
                self._identity(connection)
                rows = connection.execute(
                    "select credential_id, verifier, tenant_id, workspace_id, caller_id, owner_subject, "
                    "credential_revision, tenant_revision, caller_revision, operations_mask, expires_at, "
                    "caller_state, tenant_state, policy_json from public.sqag_quote_api_admission_lookup(?)",
                    (credential_id,)).fetchall()
                _require(len(rows) <= 1)
                record = _record(rows[0]) if rows else None
                _require(record is None or record.credential_id == credential_id)
                return record
        except Exception:
            raise RegistryError() from None

    def _locked(self, connection, context, operation):
        _require(type(context) is QuoteContext and operation in OPERATIONS)
        self._identity(connection)
        tenant = connection.execute(self._sql(
            "select " + ", ".join(TENANT_COLUMNS) +
            " from public.sqag_quote_api_tenants where tenant_id=? for update"), (context.tenant_id,)).fetchone()
        caller = connection.execute(self._sql(
            "select " + ", ".join(CALLER_COLUMNS) +
            " from public.sqag_quote_api_callers where tenant_id=? and caller_id=? for update"),
            (context.tenant_id, context.caller_id)).fetchone()
        if tenant is None or caller is None:
            raise RegistryDenied()
        t, c = _projection(tenant, TENANT_COLUMNS), _projection(caller, CALLER_COLUMNS)
        now = self._now(connection)
        validate_policy(t["policy_json"])
        for row in (t, c):
            _integer(row["authority_revision"], 1, MAX_REVISION)
            _integer(row["counter_revision"], 0, MAX_REVISION)
            _integer(row["created_at"], 0, MAX_TIME)
            _integer(row["expires_at"], row["created_at"] + 1, MAX_TIME)
        _integer(c["credential_revision"], 1, MAX_REVISION)
        mask = _integer(c["operations_mask"], 0, 255)
        current_ops = frozenset(op for bit, op in enumerate(OPERATIONS) if mask & (1 << bit))
        if (not context.owns(auth_mode="service", tenant_id=t["tenant_id"], workspace_id=t["workspace_id"],
                             owner_subject=c["owner_subject"], caller_id=c["caller_id"])
                or c["tenant_id"] != t["tenant_id"] or context.auth_revision != str(c["credential_revision"])
                or context.authority_revision != f't{t["authority_revision"]}:c{c["authority_revision"]}'
                or context.authority_snapshot != (("registry_revision", context.authority_revision),)
                or context.operations != current_ops or operation not in current_ops
                or t["state"] != "active" or c["state"] != "active"
                or t["revoked_at"] is not None or c["revoked_at"] is not None
                or now >= min(t["expires_at"], c["expires_at"])):
            raise RegistryDenied()
        return t, c, now

    def revalidate(self, connection, context, operation):
        """Read-only fence. The caller keeps locks until its transaction ends."""
        try:
            self._locked(connection, context, operation)
        except RegistryError:
            raise
        except Exception:
            raise RegistryError() from None

    @contextmanager
    def _atomic(self, connection):
        connection.execute("savepoint sqag_registry_counter")
        try:
            yield
            connection.execute("release savepoint sqag_registry_counter")
        except Exception:
            connection.execute("rollback to savepoint sqag_registry_counter")
            connection.execute("release savepoint sqag_registry_counter")
            raise RegistryError() from None

    @staticmethod
    def _bucket(start, count, now, size, cap):
        _integer(start, 0, MAX_TIME)
        _integer(count, 0, cap)
        _require(start % size == 0 and start <= now)
        current = now // size * size
        return current, count if start == current else 0

    def reserve(self, connection, context, operation):
        """Counter primitive only; durable receipt/outbox admission is not supplied."""
        try:
            self._reserve(connection, context, operation)
        except RegistryError:
            raise
        except Exception:
            raise RegistryError() from None

    def _reserve(self, connection, context, operation):
        _require(operation in ("create", "poll") and self._audit is not None)
        self._identity(connection)
        with self._atomic(connection):
            t, c, now = self._locked(connection, context, operation)
            _require(t["counter_revision"] < MAX_REVISION and c["counter_revision"] < MAX_REVISION)
            minute, creates = self._bucket(t["create_minute_start"], t["create_minute_count"], now, 60, 15)
            day, daily = self._bucket(t["create_day_start"], t["create_day_count"], now, 86400, 100)
            poll_minute, polls = self._bucket(c["poll_minute_start"], c["poll_minute_count"], now, 60, 120)
            _integer(t["active_jobs"], 0, 2)
            if operation == "create":
                if creates >= 15 or daily >= 100 or t["active_jobs"] >= 2:
                    raise RegistryDenied()
                changed = connection.execute(self._sql(
                    "update public.sqag_quote_api_tenants set create_minute_start=?, create_minute_count=?, "
                    "create_day_start=?, create_day_count=?, active_jobs=?, counter_revision=counter_revision+1 "
                    "where tenant_id=? and counter_revision=?"),
                    (minute, creates + 1, day, daily + 1, t["active_jobs"] + 1, t["tenant_id"], t["counter_revision"]))
                _require(changed.rowcount == 1)
                changed = connection.execute(self._sql(
                    "update public.sqag_quote_api_callers set counter_revision=counter_revision+1 "
                    "where tenant_id=? and caller_id=? and counter_revision=?"),
                    (c["tenant_id"], c["caller_id"], c["counter_revision"]))
            else:
                if polls >= 120:
                    raise RegistryDenied()
                changed = connection.execute(self._sql(
                    "update public.sqag_quote_api_callers set poll_minute_start=?, poll_minute_count=?, "
                    "counter_revision=counter_revision+1 where tenant_id=? and caller_id=? and counter_revision=?"),
                    (poll_minute, polls + 1, c["tenant_id"], c["caller_id"], c["counter_revision"]))
            _require(changed.rowcount == 1)
            # A closed event carries no policy, verifier or identity content.
            self._audit.stage(connection, {"operation": operation, "state": "reserved"})


class SQLiteCredentialRegistry(PostgresCredentialRegistry):
    """Synthetic fixtures only. Never a deployed fallback or automatic DDL owner."""

    def __init__(self, *, connection_factory, clock, audit_sink=None):
        super().__init__(connection_factory=connection_factory, audit_sink=audit_sink)
        self._clock = clock

    def _identity(self, connection):
        _require(connection.in_transaction)

    def _now(self, connection):
        return _integer(self._clock(), 0, MAX_TIME)

    def _sql(self, sql):
        return sql.replace("public.", "main.").replace(" for update", "")

    def lookup(self, credential_id):
        if type(credential_id) is not str or not CREDENTIAL.fullmatch(credential_id):
            return None
        try:
            with self._connections() as connection:
                self._identity(connection)
                rows = connection.execute(self._sql(FIXTURE_LOOKUP), (credential_id,)).fetchall()
                _require(len(rows) <= 1)
                return _record(rows[0]) if rows else None
        except Exception:
            raise RegistryError() from None
