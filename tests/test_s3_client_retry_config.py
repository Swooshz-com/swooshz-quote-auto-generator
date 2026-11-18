"""Every S3 client SQAG builds pins the botocore retry policy.

botocore 1.44.0 changed the SDK default retry mode from "legacy" (5 attempts,
seconds-scale backoff) to "standard" (3 attempts). These tests build the real
boto3 clients (dummy credentials, loopback endpoint only) so a dependency bump
that changes the default cannot loosen live artifact-storage retries unseen.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.clear_rehearsal_bucket as clear_script  # noqa: E402
import scripts.verify_live_db_object_backup_restore as backup_restore_script  # noqa: E402
import scripts.verify_live_object_storage_provider as provider_script  # noqa: E402
import scripts.verify_live_retention_delete as retention_script  # noqa: E402
from webapp import server as webapp  # noqa: E402
from webapp.object_storage import (  # noqa: E402
    S3_CLIENT_RETRIES,
    object_storage_provider_status,
    s3_client_config,
)

EXPECTED_RETRIES = {"mode": "legacy"}
LEGACY_TOTAL_ATTEMPTS = 5


def synthetic_env(endpoint_url: str = "http://127.0.0.1:9") -> dict[str, str]:
    return {
        "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": endpoint_url,
        "SQAG_OBJECT_STORAGE_BUCKET": "sqag-synthetic-retry-test",
        "SQAG_OBJECT_STORAGE_REGION": "auto",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-secret-key",
    }


def isolated_aws_environment() -> mock._patch_dict:
    """Hide any local AWS config and set the env default the pin must override."""
    clean = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    clean.update(
        {
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            "AWS_RETRY_MODE": "standard",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    return mock.patch.dict(os.environ, clean, clear=True)


def server_backend(env: dict[str, str]):
    with mock.patch.object(webapp, "read_dotenv_value", side_effect=lambda name, *args, **kwargs: env.get(name, "")):
        return webapp.configured_object_storage_backend()


class FlakyS3Handler(BaseHTTPRequestHandler):
    """Answers HEAD with 500 for the first `failures` requests, then 200."""

    failures = 0
    attempts = 0
    lock = threading.Lock()

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        cls = type(self)
        with cls.lock:
            cls.attempts += 1
            fail = cls.attempts <= cls.failures
        self.send_response(500 if fail else 200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


class S3ClientRetryConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = isolated_aws_environment()
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_pinned(self, client: object) -> None:
        self.assertEqual(client.meta.config.retries, EXPECTED_RETRIES)

    def test_shared_policy_is_legacy(self) -> None:
        self.assertEqual(dict(S3_CLIENT_RETRIES), EXPECTED_RETRIES)
        self.assertEqual(s3_client_config().retries, EXPECTED_RETRIES)

    def test_live_app_client_is_pinned(self) -> None:
        self.assert_pinned(server_backend(synthetic_env()).client)

    def test_operator_script_clients_are_pinned(self) -> None:
        env = synthetic_env()
        self.assert_pinned(backup_restore_script._build_s3_backend(env).client)
        self.assert_pinned(retention_script._build_s3_backend(env).client)
        self.assert_pinned(
            provider_script._build_live_s3_backend(env, object_storage_provider_status(env)).client
        )

    def test_standalone_clear_script_matches_shared_policy(self) -> None:
        self.assertEqual(dict(clear_script.S3_CLIENT_RETRIES), EXPECTED_RETRIES)
        self.assert_pinned(clear_script.make_client(synthetic_env()))

    def run_against_flaky_endpoint(self, failures: int) -> tuple[bool, int]:
        FlakyS3Handler.failures = failures
        FlakyS3Handler.attempts = 0
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), FlakyS3Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        backend = server_backend(synthetic_env(f"http://127.0.0.1:{httpd.server_address[1]}"))
        with mock.patch("botocore.endpoint.time.sleep") as sleep:
            ok = backend.readiness_probe()
        self.assertEqual(sleep.call_count, max(0, min(failures, LEGACY_TOTAL_ATTEMPTS - 1)))
        return ok, FlakyS3Handler.attempts

    def test_live_client_rides_out_four_provider_errors(self) -> None:
        # botocore 1.44's "standard" default gives up after 3 attempts here.
        ok, attempts = self.run_against_flaky_endpoint(failures=4)
        self.assertTrue(ok)
        self.assertEqual(attempts, LEGACY_TOTAL_ATTEMPTS)

    def test_live_client_stops_after_legacy_attempt_limit(self) -> None:
        ok, attempts = self.run_against_flaky_endpoint(failures=100)
        self.assertFalse(ok)
        self.assertEqual(attempts, LEGACY_TOTAL_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
