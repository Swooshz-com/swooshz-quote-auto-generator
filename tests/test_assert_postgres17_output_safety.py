"""Focused tests for scripts/assert_postgres17.py output safety.

These tests verify that the PostgreSQL 17 assertion helper fails closed without
leaking environment-derived values, raw driver exceptions, connection strings,
or tracebacks containing sensitive information.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, "scripts")
import assert_postgres17


SENTINEL_HOST = "SENTINEL_HOST_VALUE_12345"
SENTINEL_PORT = "SENTINEL_PORT_VALUE_67890"
SENTINEL_USER = "SENTINEL_USER_VALUE_ABCDE"
SENTINEL_DB = "SENTINEL_DB_VALUE_FGHIJ"
SENTINEL_DRIVER_MSG = "SENTINEL_DRIVER_EXCEPTION_MESSAGE_XYZ"
SENTINEL_CLASS_NAME = "Sensitive_Internal_Endpoint_Sentinel_7a8b9c"


def _run_assertion(env_overrides: dict[str, str] | None = None):
    env = {
        "SQAG_TEST_POSTGRES_HOST": SENTINEL_HOST,
        "SQAG_TEST_POSTGRES_PORT": "5432",
        "SQAG_TEST_POSTGRES_USER": SENTINEL_USER,
        "SQAG_TEST_POSTGRES_DB": SENTINEL_DB,
    }
    if env_overrides:
        env.update(env_overrides)
    
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    
    with mock.patch.dict("os.environ", env, clear=True):
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            try:
                exit_code = assert_postgres17.main()
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 1
    
    return exit_code, stdout_capture.getvalue(), stderr_capture.getvalue()


def _assert_no_sentinels(stdout: str, stderr: str) -> None:
    combined = stdout + stderr
    assert SENTINEL_HOST not in combined, f"Host sentinel leaked: {combined}"
    assert SENTINEL_PORT not in combined, f"Port sentinel leaked: {combined}"
    assert SENTINEL_USER not in combined, f"User sentinel leaked: {combined}"
    assert SENTINEL_DB not in combined, f"DB sentinel leaked: {combined}"
    assert SENTINEL_DRIVER_MSG not in combined, f"Driver message sentinel leaked: {combined}"
    assert "Traceback" not in combined, f"Traceback leaked: {combined}"
    assert "Connection refused" not in combined, f"Connection string leaked: {combined}"


class TestMissingRequiredVariables(unittest.TestCase):
    def test_missing_host(self):
        exit_code, stdout, stderr = _run_assertion({"SQAG_TEST_POSTGRES_HOST": ""})
        self.assertEqual(exit_code, 10)
        self.assertIn("SQAG_TEST_POSTGRES_HOST", stderr)
        self.assertIn("is not set", stderr)
        _assert_no_sentinels(stdout, stderr)

    def test_missing_port(self):
        exit_code, stdout, stderr = _run_assertion({"SQAG_TEST_POSTGRES_PORT": ""})
        self.assertEqual(exit_code, 10)
        self.assertIn("SQAG_TEST_POSTGRES_PORT", stderr)
        self.assertIn("is not set", stderr)
        _assert_no_sentinels(stdout, stderr)

    def test_missing_user(self):
        exit_code, stdout, stderr = _run_assertion({"SQAG_TEST_POSTGRES_USER": ""})
        self.assertEqual(exit_code, 10)
        self.assertIn("SQAG_TEST_POSTGRES_USER", stderr)
        self.assertIn("is not set", stderr)
        _assert_no_sentinels(stdout, stderr)


class TestMalformedPort(unittest.TestCase):
    def test_malformed_port_sentinel(self):
        exit_code, stdout, stderr = _run_assertion({"SQAG_TEST_POSTGRES_PORT": SENTINEL_PORT})
        self.assertEqual(exit_code, 18)
        self.assertIn("malformed port value", stderr)
        _assert_no_sentinels(stdout, stderr)


class TestPsycopgImportFailure(unittest.TestCase):
    def test_psycopg_import_failure_with_sentinel(self):
        with mock.patch.dict(sys.modules, {"psycopg": None}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 11)
            self.assertIn("psycopg import failed", stderr)
            _assert_no_sentinels(stdout, stderr)


class TestConnectionFailure(unittest.TestCase):
    def test_connection_failure_with_sentinel_driver_message(self):
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.side_effect = Exception(SENTINEL_DRIVER_MSG)
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 12)
            self.assertIn("PostgreSQL connection or version query failed", stderr)
            _assert_no_sentinels(stdout, stderr)

    def test_connection_failure_with_dynamically_named_exception(self):
        """Verify that dynamically named exception classes do not leak after fix."""
        dynamic_exception_class = type(
            SENTINEL_CLASS_NAME,
            (Exception,),
            {}
        )
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.side_effect = dynamic_exception_class("test message")
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 12)
            combined = stdout + stderr
            self.assertNotIn(SENTINEL_CLASS_NAME, combined,
                           f"Sentinel class name must not appear: {combined}")
            self.assertIn("PostgreSQL connection or version query failed", stderr)


class TestQueryFailure(unittest.TestCase):
    def test_query_failure(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.execute.side_effect = Exception(SENTINEL_DRIVER_MSG)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 12)
            self.assertIn("PostgreSQL connection or version query failed", stderr)
            _assert_no_sentinels(stdout, stderr)

    def test_query_failure_with_dynamically_named_exception(self):
        """Verify query failures also use fixed category, not exception class name."""
        dynamic_exception_class = type(
            SENTINEL_CLASS_NAME,
            (Exception,),
            {}
        )
        mock_cursor = mock.MagicMock()
        mock_cursor.execute.side_effect = dynamic_exception_class("query error")
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 12)
            combined = stdout + stderr
            self.assertNotIn(SENTINEL_CLASS_NAME, combined,
                           f"Sentinel class name must not appear: {combined}")
            self.assertIn("PostgreSQL connection or version query failed", stderr)


class TestVersionValidation(unittest.TestCase):
    def test_no_row(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 13)
            self.assertIn("server_version_num returned no row", stderr)
            _assert_no_sentinels(stdout, stderr)

    def test_empty_version(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("",)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 14)
            self.assertIn("server_version_num returned empty", stderr)
            _assert_no_sentinels(stdout, stderr)

    def test_non_numeric_version(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("NOT_A_NUMBER",)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 15)
            self.assertIn("malformed server_version_num", stderr)
            _assert_no_sentinels(stdout, stderr)

    def test_too_short_version(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("123",)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 16)
            self.assertIn("server_version_num too short", stderr)
            _assert_no_sentinels(stdout, stderr)


class TestWrongMajorVersion(unittest.TestCase):
    def test_postgresql_16(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("160001",)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 17)
            self.assertIn("expected PostgreSQL 17", stderr)
            self.assertIn("major version 16", stderr)
            self.assertIn("160001", stderr)
            _assert_no_sentinels(stdout, stderr)

    def test_postgresql_18(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("180001",)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 17)
            self.assertIn("expected PostgreSQL 17", stderr)
            self.assertIn("major version 18", stderr)
            self.assertIn("180001", stderr)
            _assert_no_sentinels(stdout, stderr)


class TestSuccess(unittest.TestCase):
    def test_postgresql_17_success(self):
        mock_cursor = mock.MagicMock()
        mock_cursor.fetchone.return_value = ("170001",)
        mock_connection = mock.MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_psycopg = mock.MagicMock()
        mock_psycopg.connect.return_value.__enter__.return_value = mock_connection
        with mock.patch.dict(sys.modules, {"psycopg": mock_psycopg}):
            exit_code, stdout, stderr = _run_assertion()
            self.assertEqual(exit_code, 0)
            self.assertIn("PostgreSQL major version is 17", stdout)
            self.assertIn("170001", stdout)
            _assert_no_sentinels(stdout, stderr)


if __name__ == "__main__":
    unittest.main()
