"""Rehearsal bucket clear script tests with a fake S3 client."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.clear_rehearsal_bucket as clear_script  # noqa: E402

MAP = json.loads((ROOT / "deploy" / "rehearsal" / "resources.json").read_text(encoding="utf-8"))
BUCKET = MAP["storage"]["bucket"]


class FakeS3:
    def __init__(self, keys: int) -> None:
        self.keys = [f"workspace/object-{index:05d}" for index in range(keys)]
        self.calls: list[str] = []

    def list_objects_v2(self, Bucket: str, MaxKeys: int, ContinuationToken: str | None = None):
        self.calls.append(f"list:{Bucket}")
        start = int(ContinuationToken or 0)
        page = self.keys[start : start + MaxKeys]
        truncated = start + MaxKeys < len(self.keys)
        result = {"Contents": [{"Key": key} for key in page], "IsTruncated": truncated}
        if truncated:
            result["NextContinuationToken"] = str(start + MaxKeys)
        return result

    def delete_objects(self, Bucket: str, Delete):
        self.calls.append(f"delete:{Bucket}")
        doomed = {item["Key"] for item in Delete["Objects"]}
        self.keys = [key for key in self.keys if key not in doomed]
        return {}


def rehearsal_env(**overrides: str) -> dict[str, str]:
    env = {
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://storage.example.invalid",
        "SQAG_OBJECT_STORAGE_BUCKET": BUCKET,
        "SQAG_OBJECT_STORAGE_REGION": "auto",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "test-access-key-id",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "test-secret-value-not-real",
        "SQAG_INTERNAL_WORKSPACE_ID": MAP["workspace_id"],
    }
    env.update(overrides)
    return env


class ClearRehearsalBucketTests(unittest.TestCase):
    def run_with(self, args: list[str], env: dict[str, str], fake: FakeS3) -> tuple[int, dict]:
        code, report = clear_script.run(args, env, client_factory=lambda _env: fake)
        text = json.dumps(report)
        for secret in (env["SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY"], env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"]):
            self.assertNotIn(secret, text)
        self.assertNotIn("workspace/object-", text)
        return code, report

    def test_check_reports_empty_and_not_empty(self) -> None:
        code, report = self.run_with(["--expect-bucket", BUCKET, "--mode", "check"], rehearsal_env(), FakeS3(0))
        self.assertEqual((code, report["status"]), (0, "empty"))
        code, report = self.run_with(["--expect-bucket", BUCKET, "--mode", "check"], rehearsal_env(), FakeS3(3))
        self.assertEqual((code, report["status"], report["object_count"]), (3, "not_empty", 3))

    def test_clear_deletes_every_page(self) -> None:
        fake = FakeS3(2500)
        code, report = self.run_with(["--expect-bucket", BUCKET, "--mode", "clear"], rehearsal_env(), fake)
        self.assertEqual(code, 0, report)
        self.assertEqual((report["deleted"], report["remaining"]), (2500, 0))
        self.assertEqual(fake.keys, [])

    def test_refuses_live_like_settings_before_any_storage_call(self) -> None:
        cases = [
            (["--expect-bucket", BUCKET], rehearsal_env(SQAG_OBJECT_STORAGE_BUCKET="sqag-live")),
            (["--expect-bucket", "sqag-live"], rehearsal_env(SQAG_OBJECT_STORAGE_BUCKET="sqag-live")),
            (["--expect-bucket", BUCKET], rehearsal_env(SQAG_INTERNAL_WORKSPACE_ID="live-workspace")),
            (["--expect-bucket", BUCKET], rehearsal_env(SQAG_OBJECT_STORAGE_ENDPOINT_URL="http://plain.example.invalid")),
            (["--expect-bucket", BUCKET], rehearsal_env(SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY="")),
        ]
        for args, env in cases:
            fake = FakeS3(5)
            code, report = clear_script.run(args + ["--mode", "clear"], env, client_factory=lambda _env: fake)
            self.assertEqual(code, 2, (args, report))
            self.assertEqual(fake.calls, [])
            self.assertEqual(len(fake.keys), 5)

    def test_provider_error_reports_type_only(self) -> None:
        class Boom(FakeS3):
            def list_objects_v2(self, **kwargs):
                raise RuntimeError("https://storage.example.invalid secret detail")

        code, report = clear_script.run(
            ["--expect-bucket", BUCKET, "--mode", "check"], rehearsal_env(), client_factory=lambda _env: Boom(1)
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "storage error: RuntimeError")


if __name__ == "__main__":
    unittest.main()
