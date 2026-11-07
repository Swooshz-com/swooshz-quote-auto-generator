"""Authority value and synthetic context-required consumer closure checks.

No production resource repository exists in this slice. The fake documents its
required lookup contract, not implemented job/artifact isolation or persistence.
"""

from dataclasses import FrozenInstanceError, replace
import itertools
import unittest
from unittest import mock

from webapp.quote_context import QuoteContext, QUOTE_OPERATIONS, QUOTE_READ_OPERATIONS
from webapp import server
from tests import test_quote_api_auth as fixtures


class FakeResources:
    def __init__(self):
        self.rows = {}
        self.body_reads = 0

    def read(self, context, kind, identity, operation):
        row = self.rows.get((kind, identity))
        if row is None or operation not in context.operations or not context.owns(**row["owner"]):
            raise LookupError("NOT_FOUND")
        self.body_reads += 1
        return row["state"]


class QuoteApiTenancyTest(unittest.TestCase):
    def test_registry_counter_fence_refuses_staff_and_foreign_authority_without_effects(self):
        from tests import test_quote_api_registry as fixtures
        from webapp.quote_api_registry import RegistryError
        fixture=fixtures.RegistryFixture(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        ctx=fixture.ctx()
        for candidate in (replace(ctx,auth_mode='staff',projection='staff_internal'),
                          replace(ctx,owner_subject='foreign-owner'),replace(ctx,workspace_id='foreign_workspace')):
            before=fixture.snapshot()
            with fixture.transaction() as c:
                with self.assertRaises(RegistryError): fixture.adapter.reserve(c,candidate,'create')
            self.assertEqual(before,fixture.snapshot())

    def context(self):
        return QuoteContext("service", "owner.test", "tenant.test", "workspace.test", "caller.test",
            "credential-1", QUOTE_OPERATIONS, "CustomerEstimateView", "authority-1",
            (("registry_revision", "authority-1"),), "ERR-0123ABCD")

    def test_context_copies_collections_preventing_post_admission_authority_changes(self):
        operations, snapshot = {"basis"}, [["revision", "first"]]
        context = replace(self.context(), operations=operations, authority_snapshot=snapshot)
        operations.add("price")
        snapshot[0][1] = "forged"
        self.assertEqual(context.operations, frozenset({"basis"}))
        self.assertEqual(context.authority_snapshot, (("revision", "first"),))
        with self.assertRaises(FrozenInstanceError):
            context.workspace_id = "foreign.test"
        for changes in ({"projection": "staff_internal"}, {"auth_mode": "unknown"}, {"tenant_id": ""},
                        {"operations": {"staff_settings"}}, {"authority_snapshot": (("x", {}),)}):
            with self.subTest(fields=list(changes)), self.assertRaises(ValueError):
                replace(context, **changes)

    def test_resource_owner_matrix_prevents_tenant_caller_subject_and_staff_sharing(self):
        from tests.test_quote_api_contract import REQUEST_EDGES, JOB_EDGES
        context = self.context()
        keys = ("auth_mode", "tenant_id", "workspace_id", "owner_subject", "caller_id")
        owner = {key: getattr(context, key) for key in keys}
        principals = [context, replace(context, auth_revision="rotated")]
        principals += [replace(context, **{key: "foreign.test"}) for key in keys if key != "auth_mode"]
        principals += [replace(context, auth_mode="staff", projection="staff_internal")]
        states = set(REQUEST_EDGES) | set(JOB_EDGES) | {"unknown"}
        resources = FakeResources()
        for principal, kind, state, operation in itertools.product(
                principals, ("request", "job", "receipt", "artifact", "alias", "cached_projection"), states, QUOTE_OPERATIONS):
            resources.rows[kind, "owned"] = {"owner": owner, "state": state}
            with self.subTest(principal=principal.auth_mode, kind=kind, state=state, operation=operation):
                before = resources.body_reads
                if principal in principals[:2]:
                    self.assertEqual(resources.read(principal, kind, "owned", operation), state)
                else:
                    for identity in ("owned", "missing"):
                        with self.assertRaisesRegex(LookupError, "^NOT_FOUND$"):
                            resources.read(principal, kind, identity, operation)
                    self.assertEqual(before, resources.body_reads)
        # State policy is intentionally not implemented by this ownership fake.

    def test_registry_binding_prevents_token_claims_from_overriding_context(self):
        admission = fixtures.QuoteApiAuthTest(methodName="runTest")
        admission.setUp()
        for changes in ({}, {"tenant_id": "tenant-b.test", "workspace_id": "workspace-b.test"},
                        {"caller_id": "caller-b.test", "owner_subject": "owner-b.test"}):
            admission.registry.record = fixtures.credential(**changes)
            context = admission.admit()
            for field in ("tenant_id", "workspace_id", "caller_id", "owner_subject"):
                self.assertEqual(getattr(context, field), getattr(admission.registry.record, field))
            self.assertEqual(context.projection, "CustomerEstimateView")

    def test_validated_staff_roles_never_inherit_local_operator_permissions(self):
        handler = object.__new__(server.QuoteRunnerHandler)
        for role in ("viewer", "operator", "admin"):
            session = {"auth_mode": "platform", "user": {"subject": "exact-subject.test", "account": "workspace.test",
                "platform": {"membershipRole": role, "validationGrantId": "grant.test"}}}
            handler.current_auth_session = lambda: session
            with mock.patch.object(server, "platform_launch_mode_enabled", return_value=True), mock.patch.object(
                    server, "platform_auth_session_complete", return_value=True), mock.patch.object(
                    server, "platform_membership_role_to_local_role", return_value=role):
                context = handler.current_quote_staff_context("ERR-0123ABCD")
                self.assertEqual(context.owner_subject, "exact-subject.test")
                self.assertEqual(context.operations, QUOTE_READ_OPERATIONS if role == "viewer" else QUOTE_OPERATIONS)
                if role == "viewer":
                    # Exercise actual scope admission with a validated-viewer context.
                    headers = fixtures.Message()
                    headers["Cookie"] = "synthetic-validated-session"
                    for operation in QUOTE_OPERATIONS - QUOTE_READ_OPERATIONS:
                        with self.assertRaisesRegex(fixtures.auth.AdmissionError, "SCOPE_DENIED"):
                            fixtures.auth.QuoteApiAdmission().admit(operation=operation, headers=headers,
                                service_enabled=False, staff_enabled=True, reference="ERR-0123ABCD",
                                singleton_header=server.singleton_http_header, rate_limited=lambda: False,
                                staff_context=lambda ref: context,
                                staff_mutation_guard=mock.Mock(side_effect=AssertionError("viewer mutation")))
        handler.current_auth_session = lambda: None
        self.assertIsNone(handler.current_quote_staff_context("ERR-0123ABCD"))


if __name__ == "__main__":
    unittest.main()
