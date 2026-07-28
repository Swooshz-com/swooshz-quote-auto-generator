"""Fail-closed assertion that the disposable CI PostgreSQL service is version 17.

This script connects to the same disposable PostgreSQL service used by the
integration tests and proves the running server's major version is exactly
17 by reading ``server_version_num``. It refuses to accept 16, 18, an empty
value, a malformed value, a mocked value, or a value supplied only through
an environment variable.

The script must be invoked with the same ``SQAG_TEST_POSTGRES_*`` variables
that the rest of the test suite uses. The image tag on the service container
is not trusted: only the live server response is authoritative.

The script emits only the server-reported version number and exit status.
It never prints credentials, environment values, or any other secret-bearing
information.
"""

from __future__ import annotations

import os
import sys


def _read_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(
            f"FAIL: required environment variable {name!r} is not set",
            file=sys.stderr,
        )
        sys.exit(10)
    return value


def main() -> int:
    host = _read_required("SQAG_TEST_POSTGRES_HOST")
    port = int(_read_required("SQAG_TEST_POSTGRES_PORT"))
    user = _read_required("SQAG_TEST_POSTGRES_USER")
    dbname = os.environ.get("SQAG_TEST_POSTGRES_DB", "postgres").strip() or "postgres"

    try:
        import psycopg
    except ImportError as exc:
        print(f"FAIL: psycopg is not importable: {exc}", file=sys.stderr)
        return 11

    try:
        with psycopg.connect(
            host=host,
            port=port,
            user=user,
            dbname=dbname,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW server_version_num")
                row = cursor.fetchone()
    except Exception as exc:
        print(
            f"FAIL: could not connect to PostgreSQL at {host}:{port} as {user}: {exc}",
            file=sys.stderr,
        )
        return 12

    if not row or row[0] is None:
        print("FAIL: server_version_num returned no row", file=sys.stderr)
        return 13

    value = str(row[0]).strip()
    if not value:
        print("FAIL: server_version_num returned empty", file=sys.stderr)
        return 14

    if not value.isdigit():
        print(f"FAIL: malformed server_version_num: {value!r}", file=sys.stderr)
        return 15

    if len(value) < 5:
        print(f"FAIL: server_version_num too short: {value!r}", file=sys.stderr)
        return 16

    major = int(value[:2])
    if major != 17:
        print(
            f"FAIL: expected PostgreSQL 17, got major version {major} "
            f"(server_version_num={value})",
            file=sys.stderr,
        )
        return 17

    print(f"OK: PostgreSQL major version is 17 (server_version_num={value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
