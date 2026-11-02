"""A02-A05, A09-A11, A15-A16: group access and stable ownership contracts."""
import concurrent.futures
import dataclasses
import json
import itertools
import threading
import unittest
from unittest import mock
from webapp import internal_password_auth as auth


def email(name="admin"):
    return name + "@" + "example.test"


def policy_values():
    return dict(workspace_id="workspace-internal-alpha", allowed_emails=email()+";"+email("operator"),
                bindings_json=json.dumps([dict(user_id="stable-google-subject", email=email(), role="admin"),
                                          dict(user_id="stable-google-operator-subject", email=email("operator"), role="operator")]),
                password="synthetic-group-password-value",
                salt_key=b"synthetic-session-secret-with-enough-entropy")


def policy(**changes):
    return auth.InternalAuthPolicy.from_values(**(policy_values() | changes))


class InternalPasswordAuthContractTest(unittest.TestCase):
    def test_A02_A05_canonical_set_and_reordering_are_stable(self):
        values = policy_values()
        rows = json.loads(values["bindings_json"])
        rows.reverse()
        rows[0]["email"] = rows[0]["email"].upper()
        other = policy(bindings_json=json.dumps(rows), allowed_emails="  "+email("operator").upper()+" ; "+email())
        self.assertEqual(policy().fingerprint, other.fingerprint)
        self.assertNotEqual(policy(password="different-synthetic-password").fingerprint, other.fingerprint)

    def test_A03_invalid_allowlist_matrix(self):
        cases = [None, [], "", ";", email()+";", email()+";"+email().upper(),
                 "*"+"@"+"example.test", "example.test", "x\n"+email(), "\u00e9"+email(),
                 email()+","+email("other"), ";".join(email(str(i)) for i in range(101)), "a"*16500]
        for value in cases:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(auth.InternalAuthConfigError):
                policy(allowed_emails=value)

    def test_A04_binding_matrix(self):
        rows = json.loads(policy_values()["bindings_json"])
        variants = [None, "{}", "[]", "[NaN]", '[{"user_id":"x","user_id":"y"}]', json.dumps(rows[:1])]
        for field, value in [("user_id", rows[0]["user_id"]), ("email", rows[0]["email"]), ("role", "owner"), ("role", None), ("user_id", "short"), ("workspace", "extra")]:
            changed = [dict(r) for r in rows]; changed[1][field] = value
            variants.append(json.dumps(changed))
        for value in variants:
            with self.subTest(index=variants.index(value)), self.assertRaises(auth.InternalAuthConfigError):
                policy(bindings_json=value)
        for workspace in [None, "x", " bad", "space here", "x"*65]:
            with self.subTest(workspace=workspace), self.assertRaises(auth.InternalAuthConfigError):
                policy(workspace_id=workspace)

    def test_A09_A10_shared_password_selects_exact_stable_alias(self):
        state = auth.InternalAuthState(); selected = policy()
        for name, uid, role in [("admin", "stable-google-subject", "admin"), ("operator", "stable-google-operator-subject", "operator")]:
            cookie = state.authenticate(email=email(name), password=policy_values()["password"], policy=selected)
            self.assertEqual(set(cookie), {"auth_mode", "session_id"})
            record = state.validate_session(cookie, policy=selected, current_mode=auth.INTERNAL_AUTH_MODE)
            self.assertEqual(record["user"], dict(subject=uid, account=selected.workspace_id, internal_role=role, auth_mode=auth.INTERNAL_AUTH_MODE))
        verifier = mock.Mock(wraps=auth.secrets.compare_digest)
        state = auth.InternalAuthState(verifier=verifier)
        for candidate, password in [(email("unknown"), policy_values()["password"]), (email(), "wrong-password-value"), (None, None)]:
            with self.assertRaises(auth.InternalAuthStateError):
                state.authenticate(email=candidate, password=password, policy=selected)
        self.assertEqual(verifier.call_count, 3)

    def test_A09_invalid_input_never_matches_the_dummy_comparison_path(self):
        selected=policy(password="invalid-login-input")
        state=auth.InternalAuthState()
        for password in [None, "", "short", "x"*1025, "bad\npassword"]:
            with self.subTest(kind=type(password).__name__),self.assertRaises(auth.InternalAuthStateError):
                state.authenticate(email=email(),password=password,policy=selected)

    def test_password_kdf_context_and_reload_matrix(self):
        selected = policy()
        self.assertEqual(len(selected.credential_digest), 32)
        self.assertEqual(len(selected.credential_salt), 32)
        self.assertEqual(selected.fingerprint, policy().fingerprint)
        self.assertEqual(selected.credential_salt, policy().credential_salt)
        for change in ({"password": "rotated-synthetic-password"},
                       {"salt_key": b"different-synthetic-session-secret-key"},
                       {"workspace_id": "another-synthetic-workspace"},
                       {"bindings_json": policy_values()["bindings_json"].replace('"admin"', '"operator"')}):
            with self.subTest(field=next(iter(change))):
                changed = policy(**change)
                self.assertNotEqual(selected.fingerprint, changed.fingerprint)
                state = auth.InternalAuthState()
                cookie = state.authenticate(email=email(), password=policy_values()["password"], policy=selected)
                with self.assertRaises(auth.InternalAuthStateError):
                    state.validate_session(cookie, policy=changed, current_mode=auth.INTERNAL_AUTH_MODE)
        for value in (None, b"", b"x" * 31, "invalid-key-type"):
            with self.subTest(key_type=type(value).__name__), self.assertRaises(auth.InternalAuthConfigError):
                policy(salt_key=value)
        self.assertNotIn(selected.credential_digest.hex(), repr(selected))
        self.assertNotIn(selected.credential_salt.hex(), repr(selected))

    def test_configuration_validation_keeps_all_bounds_without_a_kdf(self):
        values = policy_values()
        changes = [
            {"workspace_id": None}, {"workspace_id": "bad workspace"},
            {"allowed_emails": None}, {"allowed_emails": email() + ";"},
            {"allowed_emails": email() + ";" + email().upper()},
            {"bindings_json": "[NaN]"}, {"bindings_json": "[]"},
            {"bindings_json": values["bindings_json"].replace('"admin"', '"owner"')},
            {"password": None}, {"password": "short"}, {"password": "x" * 1025},
            {"password": "bad\npassword-value"}, {"password": "bad\ud800password-value"},
            {"password": "\u00e9" * 5}, {"password": "\u00e9" * 513},
            {"salt_key": None}, {"salt_key": "invalid-key-type"}, {"salt_key": b"x" * 31},
        ]
        with mock.patch.object(auth.hashlib, "pbkdf2_hmac", side_effect=AssertionError("KDF must not run")) as derive:
            auth.InternalAuthPolicy.validate_values(**values)
            for change in changes:
                for reader in (auth.InternalAuthPolicy.validate_values, auth.InternalAuthPolicy.from_values):
                    with self.subTest(field=next(iter(change)), reader=reader.__name__), self.assertRaises(auth.InternalAuthConfigError):
                        reader(**(values | change))
            derive.assert_not_called()
        with mock.patch.object(auth.InternalAuthPolicy, "validate_values", wraps=auth.InternalAuthPolicy.validate_values) as validate:
            policy()
            validate.assert_called_once_with(**values)

    def test_password_kdf_and_comparison_cover_every_login_outcome(self):
        selected = policy()
        cases = [(email(), policy_values()["password"], True),
                 (email("operator"), policy_values()["password"], True),
                 (email(), "wrong-password-value", False),
                 (email("unknown"), policy_values()["password"], False),
                 (None, policy_values()["password"], False),
                 (email(), None, False), (email(), "", False),
                 (email(), "x" * 1025, False), (email(), "invalid\npassword", False)]
        for alias, password, accepted in cases:
            compare = mock.Mock(wraps=auth.secrets.compare_digest)
            state = auth.InternalAuthState(verifier=compare)
            with self.subTest(alias_known=alias == email(), password_type=type(password).__name__), mock.patch.object(
                auth.hashlib, "pbkdf2_hmac", wraps=auth.hashlib.pbkdf2_hmac
            ) as derive:
                if accepted:
                    state.authenticate(email=alias, password=password, policy=selected)
                else:
                    with self.assertRaises(auth.InternalAuthStateError) as caught:
                        state.authenticate(email=alias, password=password, policy=selected)
                    self.assertEqual(str(caught.exception), "Authentication could not be completed.")
                derive.assert_called_once()
                self.assertEqual(derive.call_args.args[0], "sha256")
                self.assertEqual(derive.call_args.args[2:], (selected.credential_salt, 600_000))
                self.assertEqual(derive.call_args.kwargs, {"dklen": 32})
                compare.assert_called_once()
                self.assertEqual(tuple(map(len, compare.call_args.args)), (32, 32))

    def test_password_utf8_byte_bounds_and_invalid_encoding(self):
        for value in ("\u00e9" * 6, "\u00e9" * 512):
            self.assertEqual(len(policy(password=value).credential_digest), 32)
        for value in ("\u00e9" * 5, "\u00e9" * 513, "bad\x00password-value", "bad\ud800password-value"):
            with self.subTest(length=len(value)), self.assertRaises(auth.InternalAuthConfigError):
                policy(password=value)

    def test_A07_A16_nonce_expiry_replay_browser_binding_and_concurrent_consumption(self):
        now = [1000]; state = auth.InternalAuthState(clock=lambda: now[0])
        for variation in ["wrong_binding", "expired", "missing"]:
            nonce, binding = state.begin_login()
            with self.subTest(variation=variation), self.assertRaises(auth.InternalAuthStateError):
                if variation == "expired": now[0] += auth.LOGIN_NONCE_TTL_SECONDS
                state.consume_login("a"*32 if variation == "missing" else nonce, "b"*32 if variation == "wrong_binding" else binding)
        nonce, binding = state.begin_login()
        def consume(_):
            try: state.consume_login(nonce, binding); return True
            except auth.InternalAuthStateError: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(consume, range(8))), 1)
        with mock.patch.object(auth, "MAX_LOGIN_NONCES", 1):
            state = auth.InternalAuthState(); state.begin_login()
            with self.assertRaises(auth.InternalAuthStateError): state.begin_login()

    def test_A11_A15_A16_rotation_revocation_policy_and_capacity(self):
        now = [1000]; state = auth.InternalAuthState(clock=lambda: now[0]); selected = policy()
        def login(previous=""):
            return state.authenticate(email=email(), password=policy_values()["password"], policy=selected, previous_session_id=previous)
        first=login(); second=login(first["session_id"])
        with self.assertRaises(auth.InternalAuthStateError): state.validate_session(first, policy=selected, current_mode=auth.INTERNAL_AUTH_MODE)
        for changed in [policy(password="changed-group-password"), policy(workspace_id="other-workspace"), policy(bindings_json=policy_values()["bindings_json"].replace('"admin"', '"operator"'))]:
            current=login()
            with self.subTest(change=changed.workspace_id), self.assertRaises(auth.InternalAuthStateError):
                state.validate_session(current, policy=changed, current_mode=auth.INTERNAL_AUTH_MODE)
        current=login(); now[0] += auth.INTERNAL_SESSION_TTL_SECONDS
        with self.assertRaises(auth.InternalAuthStateError): state.validate_session(current, policy=selected, current_mode=auth.INTERNAL_AUTH_MODE)
        with self.assertRaises(auth.InternalAuthStateError): auth.InternalAuthState().validate_session(second, policy=selected, current_mode=auth.INTERNAL_AUTH_MODE)
        current=login(); self.assertTrue(state.revoke_session(current["session_id"]))
        with self.assertRaises(auth.InternalAuthStateError): state.validate_session(current, policy=selected, current_mode=auth.INTERNAL_AUTH_MODE)
        with mock.patch.object(auth, "MAX_INTERNAL_SESSIONS", 1):
            state=auth.InternalAuthState(); first=login()
            with self.assertRaises(auth.InternalAuthStateError): login()
            login(first["session_id"])


class ScheduledRegistryLock:
    """Exercise each linearisation order while retaining a real registry RLock."""
    def __init__(self):
        self.raw = threading.RLock()
        self.condition = threading.Condition()
        self.local = threading.local()
        self.order = ()
        self.events = []

    def __enter__(self):
        label = getattr(self.local, "label", None)
        if label is not None:
            with self.condition:
                if not self.condition.wait_for(
                    lambda: len(self.events) < len(self.order)
                    and self.order[len(self.events)] == label, timeout=5,
                ):
                    raise AssertionError("Concurrent registry operation did not reach its lock.")
        self.raw.acquire()
        return self

    def __exit__(self, *args):
        label = getattr(self.local, "label", None)
        if label is not None:
            with self.condition:
                self.events.append(label)
                self.condition.notify_all()
        self.raw.release()


def simultaneous(operations, scheduled_lock=None):
    barrier = threading.Barrier(len(operations) + 1)
    def run(label, operation):
        if scheduled_lock is not None:
            scheduled_lock.local.label = label
        barrier.wait(timeout=5)
        try:
            return "allowed", operation()
        except auth.InternalAuthStateError as exc:
            return "denied", exc.reason
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(operations)) as pool:
        pending = {label: pool.submit(run, label, operation) for label, operation in operations.items()}
        barrier.wait(timeout=5)
        return {label: result.result(timeout=10) for label, result in pending.items()}


class InternalPasswordConcurrencyTest(unittest.TestCase):
    def validate(self, state, cookie, selected):
        return state.validate_session(cookie, policy=selected, current_mode=auth.INTERNAL_AUTH_MODE)

    def test_A16_concurrent_capacity_preserves_untargeted_alias(self):
        for capacity in (1, 2, 3):
            with self.subTest(capacity=capacity), mock.patch.object(auth, "MAX_INTERNAL_SESSIONS", capacity):
                selected = policy()
                state = auth.InternalAuthState(clock=lambda: 1000)
                other = state.authenticate(email=email("operator"), password=policy_values()["password"], policy=selected)
                calls = {
                    str(index): lambda: state.authenticate(
                        email=email(), password=policy_values()["password"], policy=selected,
                    ) for index in range(6)
                }
                outcomes = simultaneous(calls)
                admitted = [value for status, value in outcomes.values() if status == "allowed"]
                denied = [value for status, value in outcomes.values() if status == "denied"]
                self.assertEqual(len(admitted), capacity - 1)
                self.assertEqual(denied, ["session_capacity"] * (7 - capacity))
                self.assertEqual(len({cookie["session_id"] for cookie in admitted}), len(admitted))
                for cookie in admitted:
                    self.assertEqual(self.validate(state, cookie, selected)["user"]["subject"], "stable-google-subject")
                self.assertEqual(self.validate(state, other, selected)["user"]["subject"], "stable-google-operator-subject")

    def test_A16_same_previous_sid_rotation_has_one_capacity_winner(self):
        selected = policy()
        with mock.patch.object(auth, "MAX_INTERNAL_SESSIONS", 1):
            state = auth.InternalAuthState(clock=lambda: 1000)
            previous = state.authenticate(email=email(), password=policy_values()["password"], policy=selected)
            outcomes = simultaneous({str(index): lambda: state.authenticate(
                email=email(), password=policy_values()["password"], policy=selected,
                previous_session_id=previous["session_id"],
            ) for index in range(6)})
            admitted = [value for status, value in outcomes.values() if status == "allowed"]
            self.assertEqual(len(admitted), 1)
            self.assertEqual([value for status, value in outcomes.values() if status == "denied"], ["session_capacity"] * 5)
            with self.assertRaises(auth.InternalAuthStateError):
                self.validate(state, previous, selected)
            self.assertEqual(self.validate(state, admitted[0], selected)["user"]["subject"], "stable-google-subject")

    def test_A16_login_logout_revocation_validation_linearisation_table(self):
        # Logout delegates to this same revoke_session operation in the real HTTP handler.
        for alias in ("admin", "operator"):
            for order in itertools.permutations(("rotate", "logout_revoke", "validate")):
                with self.subTest(alias=alias, order=order):
                    selected = policy()
                    lock = ScheduledRegistryLock()
                    state = auth.InternalAuthState(clock=lambda: 1000, lock=lock)
                    previous = state.authenticate(email=email(alias), password=policy_values()["password"], policy=selected)
                    peer_alias = "operator" if alias == "admin" else "admin"
                    peer = state.authenticate(email=email(peer_alias), password=policy_values()["password"], policy=selected)
                    lock.order = order
                    outcomes = simultaneous({
                        "rotate": lambda: state.authenticate(email=email(alias), password=policy_values()["password"], policy=selected, previous_session_id=previous["session_id"]),
                        "logout_revoke": lambda: state.revoke_session(previous["session_id"]),
                        "validate": lambda: self.validate(state, previous, selected),
                    }, lock)
                    self.assertEqual(tuple(lock.events), order)
                    first_mutation = min(order.index("rotate"), order.index("logout_revoke"))
                    self.assertEqual(outcomes["validate"][0], "allowed" if order.index("validate") < first_mutation else "denied")
                    self.assertEqual(outcomes["logout_revoke"], ("allowed", order.index("logout_revoke") < order.index("rotate")))
                    self.assertEqual(outcomes["rotate"][0], "allowed")
                    with self.assertRaises(auth.InternalAuthStateError):
                        self.validate(state, previous, selected)
                    expected_subject = "stable-google-subject" if alias == "admin" else "stable-google-operator-subject"
                    self.assertEqual(self.validate(state, outcomes["rotate"][1], selected)["user"]["subject"], expected_subject)
                    self.assertEqual(self.validate(state, peer, selected)["user"]["subject"], "stable-google-subject" if peer_alias == "admin" else "stable-google-operator-subject")
