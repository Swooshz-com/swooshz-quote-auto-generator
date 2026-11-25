#!/usr/bin/env python3
"""Loopback-only synthetic password login harness; never loads host dotenv values.

Usage: internal_password_browser_harness.py <repo root> [data output tmp logs pricing]
       [--storage file|database|sqlite]

--storage database runs the app in live's storage modes (database metadata on a
disposable PostgreSQL database, object artifacts on an in-memory object store);
see tests/disposable_database_mode.py. --storage sqlite keeps metadata and
artifacts in a new SQLite file under the tmp folder with every migration applied
(for features, such as quote templates, that need database storage but no
PostgreSQL server). The default is file storage.
"""
from __future__ import annotations
import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SQAG_DISABLE_DOTENV"] = "1"
from webapp import server as webapp
from tests.gateway_test_support import synthetic_configuration

SYNTHETIC_OPERATOR_PASSWORD = "synthetic-browser-operator-password"


def configure_auth_env():
    for key in tuple(os.environ):
        if key.startswith(("SQAG_", "OIDC_", "AUTH_", "QUOTE_", "OPENAI_", "OPENROUTER_", "DEEPSEEK_")) or key in {"APP_MODE", "SESSION_SECRET", "USER_TYPE", "LOCAL_USER_ROLE"}:
            os.environ.pop(key, None)
    os.environ.update({
        "SQAG_DISABLE_DOTENV":"1", "APP_MODE":"local", "AUTH_REQUIRED":"true", "SQAG_AUTH_MODE":"internal_password",
        "SESSION_SECRET":HARNESS_SESSION_SECRET,
        "SQAG_TRACKING_HMAC_KEY":"synthetic-browser-tracking-key", "SQAG_TRACKING_HMAC_KEY_VERSION":"synthetic-v1",
        "SQAG_TRUSTED_PROXY_CIDRS":"127.0.0.1/32", "SQAG_PLATFORM_LAUNCH_MODE":"disabled",
        "SQAG_PUBLIC_BASE_URL":"https://internal-alpha.example.test", "SQAG_INTERNAL_WORKSPACE_ID":"workspace-internal-alpha",
        "SQAG_INTERNAL_ALLOWED_EMAILS":"alpha-operator"+"@"+"example.test",
        "SQAG_INTERNAL_ALLOWED_EMAILS_ADMIN":"alpha-admin"+"@"+"example.test",
        "SQAG_INTERNAL_IDENTITY_BINDINGS_JSON":json.dumps([
            {"user_id":"synthetic-browser-subject", "email":"alpha-admin"+"@"+"example.test", "role":"admin"},
            {"user_id":"synthetic-browser-operator-subject", "email":"alpha-operator"+"@"+"example.test", "role":"operator"}]),
        "SQAG_INTERNAL_LOGIN_PASSWORD":SYNTHETIC_OPERATOR_PASSWORD,
        "SQAG_INTERNAL_ADMIN_LOGIN_PASSWORD":"synthetic-browser-admin-password",
        **synthetic_configuration(),
    })


class HarnessClock:
    """Test-only clock for the session registry, moved by the browser suite."""
    def __init__(self):
        self.offset = 0

    def __call__(self):
        return time.time() + self.offset


HARNESS_CLOCK_ROUTE = "/__harness/clock"
HARNESS_SESSION_SECRET_ROUTE = "/__harness/session-secret"
# A copy of live runs as its own service with its own generated SESSION_SECRET
# (deploy/rehearsal/resources.json), over the same quote rows.
HARNESS_SESSION_SECRET = "synthetic-browser-session-secret-with-enough-entropy"
HARNESS_COPY_SESSION_SECRET = "synthetic-copy-service-session-secret-with-enough-entropy"


class HarnessHandler(webapp.QuoteRunnerHandler):
    def do_POST(self):
        # Exist only on this loopback harness when main() installs a clock;
        # nothing in webapp/ knows about them.
        clock = getattr(self.server, "harness_clock", None)
        route = urlparse(self.path).path
        if route == HARNESS_CLOCK_ROUTE and isinstance(clock, HarnessClock):
            self.handle_harness_clock(clock)
            return
        if route == HARNESS_SESSION_SECRET_ROUTE and isinstance(clock, HarnessClock):
            self.handle_harness_session_secret(clock)
            return
        super().do_POST()

    def handle_harness_session_secret(self, clock):
        """Serve the same data as a copy service would ({"copy": true}) or as itself again."""
        if self.client_address[0] != "127.0.0.1":
            self.send_json({"error": "Not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if 0 < length <= 1024 else {}
        if not isinstance(body.get("copy"), bool):
            self.send_json({"error": "copy must be true or false"}, status=400)
            return
        os.environ["SESSION_SECRET"] = HARNESS_COPY_SESSION_SECRET if body["copy"] else HARNESS_SESSION_SECRET
        # A different service: no sign-in carries over, and its sign-in policy is built from its own secret.
        self.server.internal_auth = webapp.InternalAuthState(clock=clock)
        self.server.internal_policy = webapp.internal_auth_policy()
        self.send_json({"status": "ok", "copy": body["copy"]})

    def handle_harness_clock(self, clock):
        if self.client_address[0] != "127.0.0.1":
            self.send_json({"error": "Not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if 0 < length <= 1024 else {}
        advance = body.get("advance_seconds", 0)
        if not isinstance(advance, int) or isinstance(advance, bool) or not 0 <= advance <= 30 * 24 * 60 * 60:
            self.send_json({"error": "advance_seconds must be 0..30 days"}, status=400)
            return
        clock.offset += advance
        if body.get("restart") is True:
            # Same effect as a server restart for sign-ins: every session is gone.
            self.server.internal_auth = webapp.InternalAuthState(clock=clock)
        self.send_json({"status": "ok", "offset_seconds": clock.offset})


def parse_storage(argv):
    args = list(argv)
    storage = "file"
    if "--storage" in args:
        index = args.index("--storage")
        if index + 1 >= len(args) or args[index + 1] not in {"file", "database", "sqlite"}:
            raise SystemExit("--storage must be file, database or sqlite")
        storage = args[index + 1]
        del args[index:index + 2]
    return storage, args


def configure_database_storage(settings):
    """Point the app at a disposable database and an in-memory object store."""
    from tests.disposable_database_mode import DisposableDatabase

    database = DisposableDatabase(settings)
    try:
        database.create()
    except BaseException:
        database.drop()
        raise
    # Synthetic object-store names: the app checks they are set, and the
    # in-memory backend below replaces the S3 client, so nothing leaves the host.
    os.environ.update({
        "SQAG_STORAGE_MODE":"database", "SQAG_ARTIFACT_STORAGE_MODE":"object",
        "SQAG_DATABASE_URL":database.runtime_url,
        "SQAG_OBJECT_STORAGE_PROVIDER":"s3_compatible",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL":"https://object-store.example.test",
        "SQAG_OBJECT_STORAGE_BUCKET":"synthetic-ci-artifacts",
        "SQAG_OBJECT_STORAGE_REGION":"ap-southeast-1",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID":"SYNTHETIC_CI_ACCESS_KEY_ID",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY":"synthetic-ci-secret-access-key",
    })
    backend = webapp.InMemoryObjectStorageBackend()
    webapp.configured_object_storage_backend = lambda: backend
    return database


def configure_sqlite_storage():
    """Point the app at a new SQLite database with every migration applied."""
    tmp_root = Path(os.environ.get("QUOTE_TMP_ROOT") or ROOT / "_tmp")
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"browser-harness-{os.getpid()}.sqlite3"
    if path.exists():
        path.unlink()
    url = "sqlite:///" + path.as_posix()
    webapp.apply_sqag_storage_migrations(url)
    os.environ.update({"SQAG_STORAGE_MODE":"database", "SQAG_ARTIFACT_STORAGE_MODE":"database", "SQAG_DATABASE_URL":url})
    return path


def main():
    storage, argv = parse_storage(sys.argv)
    settings = None
    if storage == "database":
        from tests.disposable_database_mode import bootstrap_settings

        # Read before configure_auth_env clears every SQAG_* name.
        settings = bootstrap_settings()
    configure_auth_env()
    if len(argv)>6:
        os.environ.update({"QUOTE_DATA_ROOT":argv[2], "QUOTE_OUTPUT_ROOT":argv[3], "QUOTE_TMP_ROOT":argv[4], "QUOTE_LOG_ROOT":argv[5], "SQAG_LOCAL_PRICING_REFERENCES_ROOT":argv[6]})
    # SYNTHETIC_WORKSPACE_SETUP
    database = None
    if storage != "file":
        # A stop signal unwinds through the finally blocks so the database is dropped or its file removed.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if storage == "database":
        database = configure_database_storage(settings)
    sqlite_path = configure_sqlite_storage() if storage == "sqlite" else None
    try:
        clock = HarnessClock()
        server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), HarnessHandler,
                                            internal_auth=webapp.InternalAuthState(clock=clock))
        server.harness_clock = clock
        print("http://127.0.0.1:"+str(server.server_port), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        if database is not None:
            database.drop()
        if sqlite_path is not None and sqlite_path.exists():
            sqlite_path.unlink()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
