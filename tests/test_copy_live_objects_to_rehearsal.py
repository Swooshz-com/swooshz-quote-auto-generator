"""Live-to-rehearsal object copy tests with fake DB and storage clients.

No network, no boto3 calls, no psycopg, no real database or bucket. Fixture values are
synthetic (example.test hosts, made-up keys and secrets).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.copy_live_objects_to_rehearsal as copy_script  # noqa: E402
from webapp import object_storage  # noqa: E402

MAP_BUCKET = json.loads((ROOT / "deploy" / "rehearsal" / "resources.json").read_text(encoding="utf-8"))["storage"][
    "bucket"
]
# A fake production endpoint and region: live's real names stay only in the rehearsal map.
REGION = "c-0.fake-region-1.aws.neon.tech"
PRODUCTION_ENDPOINT = "ep-fake-production-000001"
PRODUCTION_HOST = f"{PRODUCTION_ENDPOINT}.{REGION}"
PRODUCTION_POOLER_HOST = f"{PRODUCTION_ENDPOINT}-pooler.{REGION}"
# A temp map with the production guard section, so the guard's rules run against a fixture
# and not against whatever the real map holds.
GUARD_DIR = tempfile.TemporaryDirectory()
GUARD_MAP = Path(GUARD_DIR.name) / "rehearsal-map.json"
GUARD_MAP.write_text(
    json.dumps(
        {
            "storage": {"bucket": MAP_BUCKET},
            "production_guard": {
                "neon_project": "swooshz-sqag",
                "branch_prefix": "rehearsal-",
                "forbidden_endpoints": [
                    {"name": "live default branch compute", "endpoint_id": PRODUCTION_ENDPOINT,
                     "branch_id": "br-fake-production-000001"}
                ],
                "source": "test fixture",
            },
        }
    ),
    encoding="utf-8",
)
SCHEMA = "sqag-live-walk-targets/v1"
QUOTE_A = "quote-" + "a" * 24
QUOTE_B = "quote-" + "b" * 24
WORKSPACE = "ws-planted-workspace"
SOURCE_BUCKET = "live-bucket-planted-name"
PLANTED_NAME = "Customer-Name-Planted.pdf"
KEY_1 = f"workspaces/{WORKSPACE}/quote/{QUOTE_A}/booth/{PLANTED_NAME}"
KEY_2 = f"workspaces/{WORKSPACE}/quote/{QUOTE_B}/render/render-02.png"
PAYLOAD_1 = b"booth render bytes one"
PAYLOAD_2 = b"booth render bytes two"
DB_URL = (
    "postgresql://runtime-user:planted-db-password@ep-fake-rehearsal-000001.c-0.fake-region-1.aws.neon.tech"
    "/neondb?sslmode=require"
)
REHEARSAL_GUARD_LINE = "target host ep-fake-rehearsal-000001.c-0.fake-region-1.aws.neon.tech branch not checked\n"
LIVE_ENDPOINT = "https://live-r2.example.test"
REHEARSAL_ENDPOINT = "https://rehearsal-r2.example.test"
LIVE_KEY_ID = "live-key-id-planted"
REHEARSAL_KEY_ID = "rehearsal-key-id-planted"
LIVE_SECRET = "live-secret-planted-value"
REHEARSAL_SECRET = "rehearsal-secret-planted-value"
PLANTED = (
    DB_URL,
    "planted-db-password",
    WORKSPACE,
    PLANTED_NAME,
    SOURCE_BUCKET,
    LIVE_ENDPOINT,
    REHEARSAL_ENDPOINT,
    LIVE_KEY_ID,
    REHEARSAL_KEY_ID,
    LIVE_SECRET,
    REHEARSAL_SECRET,
    QUOTE_A,
    QUOTE_B,
    KEY_1,
    KEY_2,
)
WRITE_METHODS = ("put_object", "delete_object", "delete_objects", "copy_object", "upload_fileobj")
REPORT_KEYS = {
    "mode",
    "status",
    "objects",
    "bytes",
    "copied",
    "already_present",
    "mismatches",
    "errors",
    "write_attempts_refused",
    "keys_sha256",
    "quote_ids",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def app_metadata(data: bytes) -> dict[str, str]:
    """Object metadata the app writes and checks on read (webapp/object_storage.py)."""
    return {
        "sqag-workspace-id": WORKSPACE,
        "sqag-owner-type": "quote",
        "sqag-owner-id": "quote-owner-planted",
        "sqag-artifact-kind": "booth",
        "sqag-checksum-sha256": sha(data),
    }


def db_row(
    key: str,
    data: bytes,
    *,
    session: str = QUOTE_A,
    workspace: str = WORKSPACE,
    size: int | None = None,
    checksum: str | None = None,
) -> tuple:
    """A row in the column order of copy_script.ROWS_SQL."""
    return (workspace, "quote", session, "booth", len(data) if size is None else size, checksum or sha(data), key)


def live_objects() -> dict[str, tuple[bytes, str, dict[str, str]]]:
    return {
        KEY_1: (PAYLOAD_1, "application/pdf", app_metadata(PAYLOAD_1)),
        KEY_2: (PAYLOAD_2, "image/png", app_metadata(PAYLOAD_2)),
    }


DEFAULT_ROWS = [db_row(KEY_1, PAYLOAD_1, session=QUOTE_A), db_row(KEY_2, PAYLOAD_2, session=QUOTE_B)]


class FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeDb:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.params: list[Any] = []
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self.statements.append(sql)
        if params is not None:
            self.params.append(params)
        return FakeCursor(self.rows if sql.startswith("select") else [])

    def close(self) -> None:
        self.closed = True


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("planted provider message")
        self.response = {"Error": {"Code": code}}


class Boom(Exception):
    pass


class FakeS3:
    """Records every method call. objects maps key -> (bytes, content type, metadata)."""

    def __init__(self, objects: dict | None = None, *, drop_metadata: bool = False) -> None:
        self.objects = dict(objects or {})
        self.drop_metadata = drop_metadata
        self.calls: list[str] = []
        self.put_keys: list[str] = []

    def head_object(self, Bucket: str, Key: str) -> dict:
        self.calls.append("head_object")
        if Key not in self.objects:
            raise FakeClientError("404")
        data, content_type, metadata = self.objects[Key]
        return {"ContentLength": len(data), "ContentType": content_type, "Metadata": dict(metadata)}

    def get_object(self, Bucket: str, Key: str) -> dict:
        self.calls.append("get_object")
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey")
        data, content_type, metadata = self.objects[Key]
        return {"Body": io.BytesIO(data), "ContentType": content_type, "Metadata": dict(metadata)}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str, Metadata: dict) -> dict:
        self.calls.append("put_object")
        self.put_keys.append(Key)
        self.objects[Key] = (bytes(Body), ContentType, {} if self.drop_metadata else dict(Metadata))
        return {}

    def delete_object(self, **kwargs: Any) -> None:
        self.calls.append("delete_object")

    def delete_objects(self, **kwargs: Any) -> None:
        self.calls.append("delete_objects")

    def copy_object(self, **kwargs: Any) -> None:
        self.calls.append("copy_object")

    def upload_fileobj(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append("upload_fileobj")


class ExplodingS3(FakeS3):
    def get_object(self, Bucket: str, Key: str) -> dict:
        self.calls.append("get_object")
        raise Boom(f"{LIVE_ENDPOINT} {KEY_1} {LIVE_SECRET}")


def base_env() -> dict[str, str]:
    values = (LIVE_ENDPOINT, LIVE_KEY_ID, LIVE_SECRET, REHEARSAL_ENDPOINT, REHEARSAL_KEY_ID, REHEARSAL_SECRET)
    env = dict(zip(copy_script.SOURCE_NAMES + copy_script.DEST_NAMES, values))
    env[copy_script.DB_URL_ENV] = DB_URL
    return env


def write_json(directory: str, payload: Any) -> Path:
    fd, name = tempfile.mkstemp(dir=directory, suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload if isinstance(payload, str) else json.dumps(payload))
    return Path(name)


def ids_doc(ids: list[str]) -> dict:
    return {"schema": SCHEMA, "quote_ids": ids}


def argv_for(
    targets: Path,
    mode: str = "plan",
    *,
    confirm: bool = True,
    source: str = SOURCE_BUCKET,
    dest: str = MAP_BUCKET,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        "--targets", str(targets), "--source-bucket", source, "--expect-dest-bucket", dest, "--mode", mode,
        "--map", str(GUARD_MAP),
    ]
    if confirm:
        argv.append("--confirm-live-read")
    return argv + (extra or [])


class CopyLiveObjectsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.targets = write_json(self.dir, ids_doc([QUOTE_A, QUOTE_B]))
        self.opened: list[str] = []
        self.db = FakeDb(DEFAULT_ROWS)
        self.source = FakeS3(live_objects())
        self.dest = FakeS3()

    def run_script(self, argv: list[str], *, env=None, rows=None, source=None, dest=None):
        self.db = FakeDb(DEFAULT_ROWS if rows is None else rows)
        self.source = source if source is not None else FakeS3(live_objects())
        self.dest = dest if dest is not None else FakeS3()
        self.opened = []
        self.stderr = io.StringIO()

        def opener(name: str, value: Any):
            def factory(_env: Mapping[str, str]) -> Any:
                self.opened.append(name)
                return value

            return factory

        with contextlib.redirect_stderr(self.stderr):
            return copy_script.run(
                argv,
                base_env() if env is None else env,
                db_factory=opener("db", self.db),
                source_factory=opener("source", self.source),
                dest_factory=opener("dest", self.dest),
            )

    def test_production_branch_host_is_refused_before_any_call(self) -> None:
        for host in (PRODUCTION_HOST, PRODUCTION_POOLER_HOST):
            with self.subTest(host):
                url = f"postgresql://runtime-user:planted-db-password@{host}/neondb?sslmode=require"
                code, report = self.run_script(
                    argv_for(self.targets, "copy"), env={**base_env(), copy_script.DB_URL_ENV: url}
                )
                self.assertEqual((code, report["status"]), (2, "refused"), report)
                self.assertEqual(report["error"], "production database guard refused: target is a recorded production endpoint")
                self.assertIn(f"target host {host} branch not checked\n", self.stderr.getvalue())
                self.assertEqual((self.opened, self.db.statements), ([], []))
                self.assertEqual((self.source.calls, self.dest.calls), ([], []))
                for text in (json.dumps(report), self.stderr.getvalue()):
                    self.assertNotIn("planted-db-password", text)

    def test_refusals_stop_before_any_db_or_storage_call(self) -> None:
        cases: list[tuple[str, list[str], dict[str, str]]] = [
            ("targets not json", argv_for(write_json(self.dir, "{nope")), base_env()),
            ("wrong schema", argv_for(write_json(self.dir, {"schema": "other", "quote_ids": [QUOTE_A]})), base_env()),
            ("bad id", argv_for(write_json(self.dir, ids_doc(["quote-XYZ"]))), base_env()),
            ("uppercase id", argv_for(write_json(self.dir, ids_doc(["quote-" + "A" * 24]))), base_env()),
            ("no ids", argv_for(write_json(self.dir, ids_doc([]))), base_env()),
            ("duplicate ids", argv_for(write_json(self.dir, ids_doc([QUOTE_A, QUOTE_A]))), base_env()),
            (
                "too many ids",
                argv_for(write_json(self.dir, ids_doc([f"quote-{i:024x}" for i in range(51)]))),
                base_env(),
            ),
            ("missing confirm, plan", argv_for(self.targets, "plan", confirm=False), base_env()),
            ("missing confirm, copy", argv_for(self.targets, "copy", confirm=False), base_env()),
            ("source name says rehearsal", argv_for(self.targets, source="sqag-rehearsal-live"), base_env()),
            ("destination not the map bucket", argv_for(self.targets, dest="other-rehearsal"), base_env()),
            (
                "map unreadable",
                argv_for(self.targets, extra=["--map", str(Path(self.dir) / "missing.json")]),
                base_env(),
            ),
            (
                "http live endpoint",
                argv_for(self.targets),
                {**base_env(), copy_script.SRC_ENDPOINT: "http://live-r2.example.test"},
            ),
            (
                "http rehearsal endpoint",
                argv_for(self.targets),
                {**base_env(), copy_script.DST_ENDPOINT: "http://rehearsal-r2.example.test"},
            ),
            ("same access key id", argv_for(self.targets), {**base_env(), copy_script.DST_KEY_ID: LIVE_KEY_ID}),
            (
                "database URL scheme",
                argv_for(self.targets),
                {**base_env(), copy_script.DB_URL_ENV: "mysql://user@db.example.test/x"},
            ),
        ]
        cases += [
            (f"missing {name}", argv_for(self.targets), {k: v for k, v in base_env().items() if k != name})
            for name in copy_script.REQUIRED_ENV
        ]
        cases += [
            (f"blank {name}", argv_for(self.targets), {**base_env(), name: "  "}) for name in copy_script.REQUIRED_ENV
        ]
        for label, argv, env in cases:
            with self.subTest(label):
                code, report = self.run_script(argv, env=env)
                self.assertEqual((code, report["status"]), (2, "refused"), label)
                self.assertEqual(self.opened, [], label)
                self.assertEqual(self.db.statements, [], label)

    def test_plan_reads_rows_and_heads_source_only(self) -> None:
        code, report = self.run_script(argv_for(self.targets, "plan"))
        self.assertEqual((code, report["status"]), (0, "planned"), report)
        self.assertEqual((report["objects"], report["bytes"], report["copied"]), (2, len(PAYLOAD_1) + len(PAYLOAD_2), 0))
        self.assertEqual(report["quote_ids"], 2)
        self.assertEqual(self.opened, ["db", "source"])
        self.assertEqual(set(self.source.calls), {"head_object"})
        self.assertEqual(self.dest.calls, [])
        self.assertEqual(self.db.params, [([QUOTE_A, QUOTE_B],)])

    def test_database_is_opened_read_only_before_the_query(self) -> None:
        self.run_script(argv_for(self.targets, "plan"))
        self.assertEqual(len(self.db.statements), 3)
        self.assertEqual(self.db.statements[0], "SET default_transaction_read_only = on")
        self.assertEqual(self.db.statements[1], "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        self.assertIn("from sqag_object_artifacts where session_id = any(%s)", self.db.statements[2])
        self.assertIn("status = 'active' and object_provider_type = 's3_compatible'", self.db.statements[2])

    def test_make_db_connects_with_autocommit_through_the_lazy_driver(self) -> None:
        fake_driver = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"psycopg": fake_driver}):
            copy_script.make_db(base_env())
        fake_driver.connect.assert_called_once_with(DB_URL, autocommit=True)

    def test_copy_writes_each_object_with_its_type_and_metadata(self) -> None:
        code, report = self.run_script(argv_for(self.targets, "copy"))
        self.assertEqual((code, report["status"]), (0, "copied"), report)
        self.assertEqual(
            (report["copied"], report["already_present"], report["mismatches"], report["errors"]), (2, 0, 0, 0)
        )
        self.assertEqual(self.dest.put_keys, [KEY_1, KEY_2])
        self.assertEqual(self.dest.objects[KEY_1], (PAYLOAD_1, "application/pdf", app_metadata(PAYLOAD_1)))
        self.assertEqual(self.dest.objects[KEY_2], (PAYLOAD_2, "image/png", app_metadata(PAYLOAD_2)))
        self.assertEqual(set(self.source.calls), {"get_object"})
        self.assertEqual(report["write_attempts_refused"], 0)
        self.assertEqual(report["bytes"], len(PAYLOAD_1) + len(PAYLOAD_2))
        self.assertEqual(
            report["keys_sha256"], hashlib.sha256("\n".join(sorted([KEY_1, KEY_2])).encode("utf-8")).hexdigest()
        )

    def test_objects_already_in_destination_are_skipped(self) -> None:
        dest = FakeS3({KEY_1: (PAYLOAD_1, "application/pdf", app_metadata(PAYLOAD_1))})
        code, report = self.run_script(argv_for(self.targets, "copy"), dest=dest)
        self.assertEqual((code, report["copied"], report["already_present"]), (0, 1, 1), report)
        self.assertEqual(self.dest.put_keys, [KEY_2])

    def test_different_object_at_a_key_is_a_mismatch_and_is_not_overwritten(self) -> None:
        variants = {
            "other length": (b"short", app_metadata(PAYLOAD_1)),
            "other metadata": (PAYLOAD_1, {}),
        }
        for label, (data, metadata) in variants.items():
            with self.subTest(label):
                dest = FakeS3({KEY_1: (data, "application/pdf", metadata)})
                code, report = self.run_script(argv_for(self.targets, "copy"), dest=dest)
                self.assertEqual((code, report["mismatches"], report["copied"]), (1, 1, 1), report)
                self.assertEqual(dest.objects[KEY_1], (data, "application/pdf", metadata))
                self.assertEqual(dest.put_keys, [KEY_2])

    def test_source_bytes_or_size_that_disagree_with_the_row_are_not_written(self) -> None:
        tampered = live_objects()
        tampered[KEY_1] = (b"X" * len(PAYLOAD_1), "application/pdf", app_metadata(PAYLOAD_1))
        wrong_size_rows = [db_row(KEY_1, PAYLOAD_1, size=len(PAYLOAD_1) + 1), DEFAULT_ROWS[1]]
        cases = {
            "checksum": (None, FakeS3(tampered)),
            "size": (wrong_size_rows, None),
        }
        for label, (rows, source) in cases.items():
            with self.subTest(label):
                code, report = self.run_script(argv_for(self.targets, "copy"), rows=rows, source=source)
                self.assertEqual(
                    (code, report["status"], report["mismatches"], report["copied"]), (1, "fail", 1, 1), report
                )
                self.assertEqual(self.dest.put_keys, [KEY_2])

    def test_source_write_attempts_are_refused_and_recorded(self) -> None:
        fake = FakeS3(live_objects())
        source = copy_script.ReadOnlySource(fake)
        for name in WRITE_METHODS:
            with self.subTest(name), self.assertRaises(copy_script.ReadOnlySourceViolation):
                getattr(source, name)(Bucket=SOURCE_BUCKET, Key=KEY_1, Body=b"x")
        self.assertEqual(source.write_attempts_refused, len(WRITE_METHODS))
        self.assertEqual(fake.calls, [])
        self.assertEqual(fake.put_keys, [])
        source.head_object(Bucket=SOURCE_BUCKET, Key=KEY_1)
        self.assertEqual(fake.calls, ["head_object"])

    def test_row_problems_refuse_before_any_storage_call(self) -> None:
        many = [
            db_row(f"workspaces/{WORKSPACE}/quote/{QUOTE_A}/booth/obj-{i:05d}.bin", PAYLOAD_1) for i in range(2001)
        ]
        big = 600 * 1024 * 1024
        cases = {
            "no rows": [],
            "two workspaces": [DEFAULT_ROWS[0], db_row(KEY_2, PAYLOAD_2, workspace="ws-other-planted")],
            "key outside workspaces": [db_row("live/objects/" + PLANTED_NAME, PAYLOAD_1)],
            "key with dot dot": [db_row(f"workspaces/{WORKSPACE}/../escape.pdf", PAYLOAD_1)],
            "one key, two contents": [db_row(KEY_1, PAYLOAD_1), db_row(KEY_1, PAYLOAD_2)],
            "more than 2000 objects": many,
            "more than 1 GiB": [
                db_row(KEY_1, b"", size=big, checksum="c" * 64),
                db_row(KEY_2, b"", size=big, checksum="d" * 64),
            ],
        }
        for label, rows in cases.items():
            with self.subTest(label):
                code, report = self.run_script(argv_for(self.targets, "copy"), rows=rows)
                self.assertEqual((code, report["status"]), (2, "refused"), label)
                self.assertEqual(self.opened, ["db"], label)
                self.assertEqual(self.source.calls, [], label)
                self.assertEqual(self.dest.calls, [], label)

    def test_limits_are_inclusive_at_2000_objects_and_1_gib(self) -> None:
        rows = [
            db_row(f"workspaces/{WORKSPACE}/quote/{QUOTE_A}/booth/obj-{i:05d}.bin", PAYLOAD_1) for i in range(2000)
        ]
        self.assertEqual(len(copy_script.plan_objects(rows)), 2000)
        half = 512 * 1024 * 1024
        pair = [db_row(KEY_1, b"", size=half, checksum="c" * 64), db_row(KEY_2, b"", size=half, checksum="d" * 64)]
        self.assertEqual(sum(obj.size for obj in copy_script.plan_objects(pair)), 1024**3)

    def test_report_and_output_hold_no_keys_names_ids_urls_or_secrets(self) -> None:
        report_path = Path(self.dir) / "report.json"
        db = FakeDb(DEFAULT_ROWS)
        source = FakeS3(live_objects())
        dest = FakeS3()
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, base_env(), clear=True),
            mock.patch.object(copy_script, "make_db", lambda _env: db),
            mock.patch.object(copy_script, "make_source_client", lambda _env: source),
            mock.patch.object(copy_script, "make_dest_client", lambda _env: dest),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = copy_script.main(argv_for(self.targets, "copy", extra=["--report", str(report_path)]))
        self.assertEqual(code, 0)
        printed = out.getvalue()
        written = report_path.read_text(encoding="utf-8")
        self.assertEqual(set(json.loads(printed)), REPORT_KEYS)
        self.assertEqual(json.loads(written), json.loads(printed))
        self.assertEqual(err.getvalue(), REHEARSAL_GUARD_LINE)
        for text in (printed, written, err.getvalue()):
            for planted in PLANTED:
                self.assertNotIn(planted, text)

    def test_provider_error_is_counted_and_named_by_type_only(self) -> None:
        code, report = self.run_script(argv_for(self.targets, "copy"), source=ExplodingS3(live_objects()))
        self.assertEqual((code, report["status"], report["errors"]), (1, "fail", 2), report)
        self.assertEqual(report["error"], "storage error: Boom")
        text = json.dumps(report)
        for planted in PLANTED:
            self.assertNotIn(planted, text)

    def test_destination_that_drops_metadata_fails_the_copy_check(self) -> None:
        code, report = self.run_script(argv_for(self.targets, "copy"), dest=FakeS3(drop_metadata=True))
        self.assertEqual((code, report["copied"], report["errors"]), (1, 0, 2), report)
        self.assertEqual(report["error"], "destination check failed after the copy")

    def test_missing_source_object_is_an_error_not_a_skip(self) -> None:
        source = FakeS3({KEY_1: live_objects()[KEY_1]})
        code, report = self.run_script(argv_for(self.targets, "plan"), source=source)
        self.assertEqual((code, report["errors"], report["error"]), (1, 1, "storage error: FakeClientError"), report)

    def test_retry_policy_matches_the_webapp(self) -> None:
        self.assertEqual(dict(copy_script.S3_CLIENT_RETRIES), dict(object_storage.S3_CLIENT_RETRIES))


if __name__ == "__main__":
    unittest.main()
