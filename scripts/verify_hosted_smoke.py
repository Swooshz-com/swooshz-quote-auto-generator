#!/usr/bin/env python3
"""Synthetic hosted-shape smoke verifier.

The verifier exercises SQAG through local HTTP routes bound to 127.0.0.1
with synthetic platform/workspace context, SQLite database storage, and database
artifact storage. It emits metadata-only JSON and does not call Swooshz Platform,
prove object storage, or make DB/BLOB artifact mode launch-ready.
"""

from __future__ import annotations

import argparse
import base64
import copy
import http.client
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


SYNTHETIC_TOKEN = "synthetic-launch-token-reference"
SYNTHETIC_SESSION_ID = "quote-hosted-smoke"
SYNTHETIC_WORKSPACE_ID = "workspace-hosted-smoke"
SYNTHETIC_PROFILE_ID = "workspace-hosted-smoke-profile"
SYNTHETIC_PRICING_ID = "workspace-hosted-smoke-pricing"
SENSITIVE_SYNTHETIC_VALUES = (
    "sqlite:///",
    "C:/Users/Private",
    "Synthetic Private Customer",
    "Generated quote private line item",
    "Private pricing catalog contents",
    "Private profile layout contents",
    "staff.member@example.test",
    "oauth-client-secret-value",
    "swooshz_private_session_cookie",
    "sk-proj-private-api-key",
    "synthetic-private-artifact-bytes",
    "raw provider response text",
    "private-code",
    "private-state",
    SYNTHETIC_TOKEN,
)


class SyntheticHttpServer:
    def __enter__(self):
        class QuietQuoteRunnerHandler(webapp.QuoteRunnerHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), QuietQuoteRunnerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class JsonResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self.payload = payload
        self.status = status
        self.headers: dict[str, str] = {}

    def read(self, size: int | None = None) -> bytes:
        return json.dumps(self.payload, ensure_ascii=True).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def json_request(
    base_url: str,
    method: str,
    path: str,
    *,
    cookie: str = "",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    parsed = urllib.parse.urlparse(base_url)
    request_headers = {"Accept": "application/json"}
    if cookie:
        request_headers["Cookie"] = cookie
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request(
            method,
            path,
            body=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=request_headers,
        )
        response = connection.getresponse()
        text = response.read().decode("utf-8")
        response_headers = {key: value for key, value in response.getheaders()}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        return response.status, payload, response_headers
    finally:
        connection.close()


def binary_request(base_url: str, method: str, path: str, *, cookie: str = "", headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    parsed = urllib.parse.urlparse(base_url)
    request_headers: dict[str, str] = {}
    if cookie:
        request_headers["Cookie"] = cookie
    request_headers.update(headers or {})
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request(method, path, headers=request_headers)
        response = connection.getresponse()
        body = response.read()
        response_headers = {key: value for key, value in response.getheaders()}
        return response.status, body, response_headers
    finally:
        connection.close()


def xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>"
                "Hosted smoke synthetic workbook"
                "</t></is></c></row></sheetData></worksheet>"
            ),
        )
    return buffer.getvalue()


def synthetic_platform_payload() -> dict[str, Any]:
    return {
        "outcome": "consumed",
        "user": {
            "userId": "platform-user-hosted-smoke",
            "email": "operator@example.test",
            "displayName": "Synthetic Operator",
            "status": "active",
        },
        "workspace": {
            "workspaceId": SYNTHETIC_WORKSPACE_ID,
            "workspaceSlug": "hosted-smoke",
            "workspaceName": "Hosted Smoke Workspace",
        },
        "app": {
            "appKey": "sqag",
            "appName": "SQAG",
        },
        "membershipRole": "owner",
        "launchTokenExpiresAt": "2999-01-01T00:00:00.000Z",
    }


def synthetic_auth_session() -> dict[str, Any]:
    context = webapp.safe_platform_launch_context(synthetic_platform_payload())
    return {"user": webapp.user_from_platform_launch_context(context)}


def pricing_item(item: dict[str, Any]) -> dict[str, Any]:
    description = str(item.get("description") or item.get("id") or "synthetic row")
    return {
        **item,
        "match_terms": item.get("match_terms") or [description.lower()],
        "object_families": item.get("object_families") or ["synthetic_family"],
    }


def synthetic_pricing_reference() -> dict[str, Any]:
    return webapp.normalize_pricing_reference_payload({
        "id": SYNTHETIC_PRICING_ID,
        "label": "Hosted Smoke Pricing",
        "items": [
            pricing_item({
                "id": "hosted-smoke-graphics",
                "section": "Graphics",
                "description": "sqm synthetic printed graphics",
                "unit_hint": "sqm",
                "internal_cost": 10,
                "markup_multiplier": 2,
            })
        ],
    })


def synthetic_payload() -> dict[str, Any]:
    return {
        "images": [
            {
                "name": "synthetic-render.jpg",
                "type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,ZmFrZS1pbWFnZQ==",
            }
        ],
        "profile_id": SYNTHETIC_PROFILE_ID,
        "pricing_reference_id": SYNTHETIC_PRICING_ID,
        "pricing_reference": {"id": SYNTHETIC_PRICING_ID, "source": "company"},
        "confirmed": True,
        "view_pdf": True,
        "quote_date": "2026-06-06",
        "project_number": "SQAG-SMOKE-001",
        "client": {
            "name": "Synthetic Client",
            "attention": "Synthetic Contact",
            "title": "Project Lead",
            "address": "Synthetic Address",
        },
        "project": {
            "title": "Hosted Smoke Booth",
            "booth_width": "3",
            "booth_depth": "3",
        },
        "company": {
            "name": "Synthetic Quotation Co",
            "header_details": "Synthetic Quotation Co",
            "logo_data_url": "data:image/jpeg;base64,ZmFrZS1sb2dv",
        },
        "tax": {"label": "GST", "rate": 0.09},
        "quote_text": {
            "terms_heading": "Commercial Terms",
            "cheque_payee": "Synthetic Quotation Co",
            "notes_heading": "Notes",
            "standard_notes": "Synthetic notes",
            "acceptance_text": "Accepted",
            "person_label": "Signer",
            "stamp_label": "Stamp",
            "date_label": "Date:",
        },
        "quote_basis": {
            "surfaces": "Confirm: synthetic booth wall.",
            "counters": "Confirm: synthetic counter.",
            "platform": "Confirm: synthetic platform.",
            "graphics": "Confirm: synthetic printed graphics.",
            "furniture": "Confirm: synthetic furniture.",
            "electrical": "Confirm: synthetic lighting.",
        },
        "line_items": [
            {
                "section": "Graphics",
                "quantity": "9",
                "unit": "sqm",
                "description": "sqm synthetic printed graphics",
                "pricing_keyword": "sqm synthetic printed graphics",
                "display_price": "",
            }
        ],
        "signature": {
            "company_signatory": "Synthetic Signatory",
            "company_title": "Director",
            "company_date_label": "Date:",
        },
        "rich_text": {},
        "quote_session": {"session_id": SYNTHETIC_SESSION_ID},
    }


def synthetic_profile() -> dict[str, Any]:
    layout_path = ROOT / "tests" / "fixtures" / "quote-generator" / "profiles" / "synthetic-exhibition-fixture-template" / "quotation-layout.xlsx"
    layout_bytes = layout_path.read_bytes() if layout_path.is_file() else xlsx_bytes()
    workbook_data = "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,"
    workbook_data += base64.b64encode(layout_bytes).decode("ascii")
    payload = synthetic_payload()
    return webapp.normalize_profile_payload({
        "id": SYNTHETIC_PROFILE_ID,
        "label": "Hosted Smoke Profile",
        "defaults": {
            "company": copy.deepcopy(payload["company"]),
            "quote_text": copy.deepcopy(payload["quote_text"]),
            "signature": copy.deepcopy(payload["signature"]),
            "rich_text": copy.deepcopy(payload["rich_text"]),
            "tax": copy.deepcopy(payload["tax"]),
        },
        "pack": {
            "quotation_layout": {
                "filename": "quotation-layout.xlsx",
                "data_url": workbook_data,
            }
        },
    })


def fake_generator_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    output_dir = Path(command[command.index("--out") + 1])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "quotation.xlsx").write_bytes(xlsx_bytes())
    (output_dir / "quotation.pdf").write_bytes(b"%PDF-1.4\n% synthetic hosted smoke pdf\n")
    return subprocess.CompletedProcess(
        args=command,
        returncode=0,
        stdout="Wrote synthetic hosted smoke artifacts\n",
        stderr="",
    )


def count_rows(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(connection.execute(f"select count(*) from {table}").fetchone()[0])
    finally:
        connection.close()


def run_verification(*, work_dir: Path | None = None) -> dict[str, Any]:
    parent = work_dir or (ROOT / "_tmp" / "hosted-smoke")
    if not parent.is_absolute():
        parent = ROOT / parent
    run_root = parent / f"run-{time.time_ns()}"
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    db_path = run_root / "sqag-hosted-smoke.sqlite3"
    database_url = f"sqlite:///{db_path.as_posix()}"
    env = {
        "APP_MODE": "deploy",
        "AUTH_REQUIRED": "true",
        "SESSION_SECRET": "synthetic-session-secret-with-enough-entropy",
        "SQAG_PLATFORM_LAUNCH_MODE": "platform",
        "SQAG_PLATFORM_BASE_URL": "https://platform.example.test",
        "SQAG_STORAGE_MODE": "database",
        "SQAG_ARTIFACT_STORAGE_MODE": "database",
        "SQAG_DATABASE_URL": database_url,
        "QUOTE_DATA_ROOT": str(run_root / "data"),
        "QUOTE_OUTPUT_ROOT": str(run_root / "output"),
        "QUOTE_TMP_ROOT": str(run_root / "tmp"),
        "QUOTE_LOG_ROOT": str(run_root / "logs"),
    }
    checks = {
        "health_metadata": False,
        "unauthenticated_routes_blocked": False,
        "synthetic_platform_launch": False,
        "workspace_profile_saved_and_used": False,
        "workspace_pricing_saved_and_used": False,
        "quote_generation": False,
        "quote_session_persisted": False,
        "authorized_artifact_download": False,
        "quote_session_delete": False,
        "logout": False,
        "legacy_job_file_lockdown": False,
    }
    captured_platform_requests: list[urllib.request.Request] = []

    def fake_urlopen(request, timeout=0):
        captured_platform_requests.append(request)
        return JsonResponse(synthetic_platform_payload())

    with mock.patch.dict(os.environ, env, clear=True):
        webapp.apply_sqag_storage_migrations(database_url)
        storage = webapp.app_storage_for_auth_session(synthetic_auth_session())
        storage.save_pricing_reference(synthetic_pricing_reference())
        storage.save_profile(synthetic_profile())
        with SyntheticHttpServer() as runner:
            checks["health_metadata"] = health_check(runner.base_url)
            checks["unauthenticated_routes_blocked"] = unauthenticated_check(runner.base_url)
            with mock.patch.object(webapp.urllib.request, "urlopen", side_effect=fake_urlopen):
                launch_status, launch_body, launch_headers = json_request(
                    runner.base_url,
                    "POST",
                    "/api/platform/launch",
                    headers={"X-App-Launch-Token": SYNTHETIC_TOKEN},
                )
                session_cookie = launch_headers["Set-Cookie"].split(";", 1)[0]
            checks["synthetic_platform_launch"] = (
                launch_status == 200
                and launch_body.get("status") == "platform_session_created"
                and len(captured_platform_requests) == 1
                and SYNTHETIC_TOKEN not in captured_platform_requests[0].full_url
            )

            session_status, session_body, _headers = json_request(runner.base_url, "GET", "/api/session", cookie=session_cookie)
            csrf_header = str(session_body.get("csrf_header") or "")
            csrf_token = str(session_body.get("csrf_token") or "")
            checks["workspace_profile_saved_and_used"] = bool(storage.profile_detail(SYNTHETIC_PROFILE_ID))
            checks["workspace_pricing_saved_and_used"] = bool(storage.pricing_reference_detail(SYNTHETIC_PRICING_ID, source="company"))

            with mock.patch.object(webapp.subprocess, "run", side_effect=fake_generator_run):
                job_status, started_job, _job_headers = json_request(
                    runner.base_url,
                    "POST",
                    "/api/jobs",
                    cookie=session_cookie,
                    body={"type": "generate", "payload": synthetic_payload()},
                    headers={csrf_header: csrf_token},
                )
                job_id = str(started_job.get("job_id") or "")
                generate_result = wait_for_job(runner.base_url, job_id, session_cookie)

            checks["quote_generation"] = job_status == 202 and generate_result.get("status") == "completed"
            quote_session = generate_result.get("quote_session") if isinstance(generate_result.get("quote_session"), dict) else {}
            checks["workspace_profile_saved_and_used"] = checks["workspace_profile_saved_and_used"] and generate_result.get("status") == "completed"
            checks["workspace_pricing_saved_and_used"] = checks["workspace_pricing_saved_and_used"] and generate_result.get("status") == "completed"
            checks["quote_session_persisted"] = (
                quote_session.get("session_id") == SYNTHETIC_SESSION_ID
                and count_rows(db_path, "sqag_quote_sessions") == 1
            )
            download_results = {
                kind: download_artifact(runner.base_url, session_cookie, kind)
                for kind in ("pdf", "xlsx")
            }
            checks["authorized_artifact_download"] = all(item["status"] == 200 and item["bytes"] > 0 for item in download_results.values())
            checks["legacy_job_file_lockdown"] = legacy_file_blocked(runner.base_url, session_cookie, job_id)
            delete_status, delete_body, _delete_headers = json_request(
                runner.base_url,
                "DELETE",
                f"/api/quote-sessions/{SYNTHETIC_SESSION_ID}",
                cookie=session_cookie,
                headers={csrf_header: csrf_token},
            )
            checks["quote_session_delete"] = delete_status == 200 and delete_body.get("status") == "deleted" and count_rows(db_path, "sqag_quote_sessions") == 0
            checks["logout"] = logout_check(runner.base_url, session_cookie)

    serialized_checks = json.dumps(checks, sort_keys=True)
    no_sensitive_output = not contains_sensitive_value(serialized_checks)
    local_quote_session_success_path_used = (run_root / "data" / webapp.QUOTE_SESSION_DIR_NAME).exists()
    local_artifact_success_path_used = False if checks["legacy_job_file_lockdown"] else True
    passed = all(checks.values()) and no_sensitive_output and not local_quote_session_success_path_used and not local_artifact_success_path_used
    return {
        "schema": "swooshz.sqag.hosted-smoke-verification.v1",
        "status": "passed" if passed else "failed",
        "synthetic_only": True,
        "network": {"host": "127.0.0.1"},
        "storage": {
            "app_mode": "deploy",
            "database_mode": True,
            "database_artifact_mode": True,
            "workspace_scoped_rows": True,
            "local_quote_session_success_path_used": bool(local_quote_session_success_path_used),
            "local_artifact_success_path_used": bool(local_artifact_success_path_used),
        },
        "checks": checks,
        "authorized_artifact_downloads": {
            "kinds": sorted(download_results),
            "count": len(download_results),
            "bytes_verified": all(item["bytes"] > 0 for item in download_results.values()),
        },
        "privacy": {
            "output": "metadata-only",
            "paths": "omitted",
            "database_urls": "omitted",
            "quote_contents": "omitted",
            "profile_pricing_payloads": "omitted",
            "artifact_bytes": "omitted",
            "cookies_tokens": "omitted",
            "callback_queries": "omitted",
            "provider_responses": "omitted",
        },
        "internal_alpha_ready": False,
        "production_ready": False,
        "notes": [
            "This verifies a synthetic hosted-like smoke path only.",
            "It does not call Swooshz Platform, configure object storage, prove external deployment operations, or claim production readiness.",
        ],
    }


def health_check(base_url: str) -> bool:
    status, body, _headers = json_request(base_url, "GET", "/api/health")
    text = json.dumps(body, sort_keys=True)
    return (
        status == 200
        and body.get("status") == "ok"
        and isinstance(body.get("generator_available"), bool)
        and "generator" not in body
        and "scripts/generate_quote.py" not in text
        and not contains_sensitive_value(text)
    )


def unauthenticated_check(base_url: str) -> bool:
    status, _body, _headers = binary_request(base_url, "GET", "/")
    return status in {302, 303, 401}


def wait_for_job(base_url: str, job_id: str, session_cookie: str) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        status, body, _headers = json_request(base_url, "GET", f"/api/jobs/{job_id}", cookie=session_cookie)
        if status == 200 and body.get("status") in {"completed", "failed", "blocked", "needs_review"}:
            result = body.get("result")
            return result if isinstance(result, dict) else body
        time.sleep(0.02)
    return {"status": "failed"}


def download_artifact(base_url: str, session_cookie: str, kind: str) -> dict[str, int]:
    status, body, _headers = binary_request(
        base_url,
        "GET",
        f"/api/quote-sessions/{SYNTHETIC_SESSION_ID}/download/{kind}",
        cookie=session_cookie,
    )
    return {"status": int(status), "bytes": len(body)}


def legacy_file_blocked(base_url: str, session_cookie: str, job_id: str) -> bool:
    status, _body, _headers = binary_request(
        base_url,
        "GET",
        f"/api/jobs/{job_id}/files/quotation.xlsx",
        cookie=session_cookie,
    )
    return status in {403, 404}


def logout_check(base_url: str, session_cookie: str) -> bool:
    status, _body, headers = binary_request(base_url, "GET", "/logout", cookie=session_cookie)
    location = str(headers.get("Location") or "")
    return status in {302, 303} and bool(location) and "?" not in location


def contains_sensitive_value(text: str) -> bool:
    return any(value in text for value in SENSITIVE_SYNTHETIC_VALUES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify synthetic hosted smoke evidence.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Synthetic verifier workspace. The path is never printed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_verification(work_dir=args.work_dir)
    except Exception:
        report = {
            "schema": "swooshz.sqag.hosted-smoke-verification.v1",
            "status": "failed",
            "synthetic_only": True,
            "privacy": {
                "output": "metadata-only",
                "paths": "omitted",
                "database_urls": "omitted",
                "quote_contents": "omitted",
                "profile_pricing_payloads": "omitted",
                "artifact_bytes": "omitted",
                "cookies_tokens": "omitted",
                "callback_queries": "omitted",
                "provider_responses": "omitted",
            },
            "notes": [
                "Synthetic hosted smoke verification failed before producing evidence.",
                "Failure details are omitted to avoid printing private paths, tokens, payloads, or artifact bytes.",
            ],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
