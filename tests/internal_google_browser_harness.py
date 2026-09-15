#!/usr/bin/env python3
"""Local browser harness for the synthetic internal-Google protocol adapter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webapp import server as webapp


SYNTHETIC_INTERNAL_ALPHA_ORIGIN = "https://internal-alpha.example.test"


class SyntheticVerifier:
    claim_case = "approved"

    def exchange_and_verify(self, *, code: str, code_verifier: str, nonce: str):
        if (
            code != "synthetic-browser-code"
            or not code_verifier
            or not nonce
        ):
            raise webapp.OidcProtocolError("synthetic_oidc_denied")
        claims = {
            "approved": (
                "synthetic-browser-subject",
                "alpha-admin@example.test",
            ),
            "unknown-sub": (
                "synthetic-browser-unknown-subject",
                "alpha-admin@example.test",
            ),
            "same-email-different-sub": (
                "synthetic-browser-reassigned-subject",
                "alpha-admin@example.test",
            ),
            "same-sub-different-email": (
                "synthetic-browser-subject",
                "alpha-operator@example.test",
            ),
        }
        subject, email = claims.get(self.claim_case, claims["unknown-sub"])
        return {"sub": subject, "email": email, "email_verified": True}


class HarnessHandler(webapp.QuoteRunnerHandler):
    artifact_audit: list[dict[str, str]] = []

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/__run560/artifact-audit":
            if not self.current_auth_session():
                self.send_json({"error": "Not found"}, status=404)
                return
            self.send_json({"events": list(self.artifact_audit)})
            return
        if parsed.path == "/__run560/publication-receipt":
            session = self.current_auth_session()
            if not session:
                self.send_json({"error": "Not found"}, status=404)
                return
            params = urllib.parse.parse_qs(parsed.query)
            session_id = webapp.safe_quote_session_id(
                (params.get("session_id") or [""])[0], ""
            )
            storage = webapp.app_storage_for_auth_session(session)
            if not session_id or not isinstance(storage, webapp.DatabaseSqagStorage):
                self.send_json({"error": "Not found"}, status=404)
                return
            metadata, _draft_files = storage._read_quote_session_metadata_for_workspace(
                session_id
            )
            if not metadata:
                self.send_json({"error": "Not found"}, status=404)
                return
            publication = metadata.get("publication") or {}
            export = (metadata.get("exports") or {}).get("xlsx") or {}
            run_id = webapp.safe_reference(publication.get("run_id"), "run-")
            version = storage._publication_version_row(run_id) if run_id else None
            self.send_json({
                "publication": {
                    "run_id": run_id,
                    "state": str(publication.get("state") or ""),
                    "committed_draft_state_digest": str(
                        publication.get("committed_draft_state_digest") or ""
                    ),
                    "committed_output_revision": publication.get(
                        "committed_output_revision"
                    ),
                },
                "version": {
                    "session_id": str(version["session_id"] if version else ""),
                    "state": str(version["state"] if version else ""),
                },
                "xlsx": {
                    "sha256": str(export.get("sha256") or ""),
                    "size_bytes": export.get("size_bytes"),
                    "stale": export.get("stale"),
                },
            })
            return
        if parsed.path == "/__synthetic_oidc/case":
            params = urllib.parse.parse_qs(parsed.query)
            value = (params.get("value") or [""])[0]
            if value not in {
                "approved",
                "unknown-sub",
                "same-email-different-sub",
                "same-sub-different-email",
            }:
                self.send_json({"error": "invalid synthetic case"}, status=400)
                return
            SyntheticVerifier.claim_case = value
            self.send_json({"status": "ok"})
            return
        if parsed.path == "/__synthetic_oidc/authorize":
            params = urllib.parse.parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            callback = "/callback?" + urllib.parse.urlencode(
                {"state": state, "code": "synthetic-browser-code"}
            )
            self.send_redirect(callback)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {
            "/__run560/restore-version-metadata",
            "/__run560/clone-wrong-version",
            "/__run560/materialize-publication",
            "/__run560/materialize-cross-workspace",
        }:
            super().do_POST()
            return
        session = self.current_auth_session()
        if not session:
            self.send_json({"error": "Not found"}, status=404)
            return
        try:
            payload = self.read_json()
            storage = webapp.app_storage_for_auth_session(session)
            if not isinstance(storage, webapp.DatabaseSqagStorage):
                raise ValueError("database fixture storage required")
            if parsed.path == "/__run560/restore-version-metadata":
                session_id = webapp.safe_quote_session_id(payload.get("session_id"), "")
                run_id = webapp.safe_reference(payload.get("run_id"), "run-")
                with storage.connection() as connection:
                    version = storage._publication_version_row(run_id, connection)
                    if not version or str(version["session_id"]) != session_id:
                        raise ValueError("publication fixture missing")
                    connection.execute(
                        "update sqag_quote_sessions set metadata_json = ?, updated_at = ? "
                        "where workspace_id = ? and session_id = ?",
                        (
                            version["metadata_json"],
                            webapp.utc_timestamp(),
                            storage.workspace_id,
                            session_id,
                        ),
                    )
                    connection.commit()
                self.send_json({"status": "ok"})
                return
            if parsed.path == "/__run560/clone-wrong-version":
                source_session_id = webapp.safe_quote_session_id(payload.get("source_session_id"), "")
                target_session_id = webapp.safe_quote_session_id(payload.get("target_session_id"), "")
                other_run_id = webapp.safe_reference(payload.get("other_run_id"), "run-")
                with storage.connection() as connection:
                    source = connection.execute(
                        "select metadata_json, draft_files_json, created_at, updated_at "
                        "from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                        (storage.workspace_id, source_session_id),
                    ).fetchone()
                    if not source:
                        raise ValueError("source fixture missing")
                    metadata = json.loads(str(source["metadata_json"] or "{}"))
                    metadata["session_id"] = target_session_id
                    metadata["owner"] = {"user_id": storage.user_id}
                    publication = dict(metadata.get("publication") or {})
                    publication["run_id"] = other_run_id
                    publication["state"] = "published"
                    metadata["publication"] = publication
                    connection.execute(
                        "insert into sqag_quote_sessions "
                        "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                        "values (?, ?, ?, ?, ?, ?)",
                        (
                            storage.workspace_id,
                            target_session_id,
                            json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                            source["draft_files_json"],
                            source["created_at"],
                            source["updated_at"],
                        ),
                    )
                    connection.commit()
                self.send_json({"status": "ok"})
                return

            target_session_id = webapp.safe_quote_session_id(payload.get("session_id"), "")
            run_id = webapp.safe_reference(payload.get("run_id"), "run-")
            generation_payload = payload.get("generation_payload")
            if not target_session_id or not run_id or not isinstance(generation_payload, dict):
                raise ValueError("invalid cross-workspace fixture")
            cross_workspace = parsed.path == "/__run560/materialize-cross-workspace"
            target_storage = webapp.DatabaseSqagStorage(
                webapp.configured_database_url(),
                "workspace-run560-b" if cross_workspace else storage.workspace_id,
                role="admin",
                user_id="synthetic-run560-b" if cross_workspace else storage.user_id,
            )
            output_dir = Path(tempfile.mkdtemp(prefix="run560-publication-"))
            artifact_bytes = (
                b"synthetic-run560-workspace-b"
                if cross_workspace
                else f"synthetic-run560-{run_id}".encode("ascii")
            )
            (output_dir / "quotation.xlsx").write_bytes(artifact_bytes)
            generation_payload = json.loads(json.dumps(generation_payload))
            quote_session = generation_payload.setdefault("quote_session", {})
            quote_session["session_id"] = target_session_id
            quote_session.setdefault("status", {})["quote_generated"] = False
            if not cross_workspace:
                details = {
                    "quote_date": "2026-09-15",
                    "project_number": "RUN-560",
                    "client": generation_payload.get("client") or {"name": "Synthetic Run 560 Customer"},
                    "project": generation_payload.get("project") or {"title": "Synthetic Run 560 Protected Export"},
                    "company": generation_payload.get("company") or {"name": "Synthetic Run 560 Company"},
                    "currency": "SGD",
                    "exchange_rate": 1,
                    "tax": {"label": "GST", "rate": 0.09},
                    "quote_text": {
                        "payment_terms": ["Synthetic terms"],
                        "cheque_payee": "Synthetic Run 560 Company",
                        "notes_heading": "Notes",
                        "standard_notes": "Synthetic note",
                        "acceptance_text": "Accepted",
                        "person_label": "Person",
                        "stamp_label": "Stamp",
                        "date_label": "Date",
                    },
                    "signature": {
                        "company_signatory": "Synthetic Signatory",
                        "company_title": "Director",
                        "company_date_label": "Date",
                    },
                    "rich_text": {},
                }
                pricing_reference = generation_payload.get("pricing_reference") or {}
                pricing_id = webapp.safe_resource_id(pricing_reference.get("id"), "")
                pricing_source = webapp.clean_text(pricing_reference.get("source"))
                pricing_digest = webapp.clean_text(pricing_reference.get("digest_sha256"))
                details["commercial_snapshot"] = {
                    "schema": webapp.QUOTE_COMMERCIAL_SNAPSHOT_SCHEMA,
                    "version": webapp.QUOTE_COMMERCIAL_SNAPSHOT_VERSION,
                    "owner": "quote",
                    "lifecycle": "RECOVERED",
                    "origin": "session_recovery",
                    "presence": {
                        key: "captured" if webapp.quote_commercial_value_is_present(value) else "intentional_empty"
                        for key, value in webapp.quote_commercial_snapshot_raw_values(details).items()
                    },
                    "pricing_basis": {
                        "currency": "SGD",
                        "source": pricing_source,
                        "id": pricing_id,
                        "digest": pricing_digest,
                    },
                }
                quote_session["draft_state"] = {
                    "version": 5,
                    "quoteCommercialLifecycle": "RECOVERED",
                    "selectedPresetValue": "company:synthetic-run560-company",
                    "quoteDetails": details,
                    "outputRevision": generation_payload.get("output_revision", 1),
                    "outputRows": generation_payload.get("line_items") or [],
                    "pricingMatches": generation_payload.get("line_items") or [],
                    "lineItems": generation_payload.get("line_items") or [],
                }
            target_storage.create_or_update_quote_session(
                generation_payload,
                result={"status": "completed", "files": [{"name": "quotation.xlsx"}]},
                output_dir=output_dir,
                session_id=target_session_id,
                generation_run_id=run_id,
                generation_job_id="job-run560-workspace-b",
            )
            self.send_json({
                "status": "ok",
                "size_bytes": len(artifact_bytes),
                "sha256": webapp.artifact_checksum(artifact_bytes),
            })
        except (ValueError, webapp.RequestBodyError, webapp.SqagStorageAccessError):
            self.send_json({"error": "Invalid fixture request"}, status=400)


def main() -> int:
    runtime = tempfile.TemporaryDirectory(prefix="sqag-run560-auth-")
    runtime_root = Path(runtime.name)
    for key in tuple(os.environ):
        if (
            key.startswith(("SQAG_", "OIDC_", "AUTH_", "QUOTE_"))
            or key in {"APP_MODE", "SESSION_SECRET", "USER_TYPE", "LOCAL_USER_ROLE"}
        ):
            os.environ.pop(key, None)
    os.environ.update(
        {
            "APP_MODE": "deploy",
            "AUTH_REQUIRED": "true",
            "SQAG_AUTH_MODE": "internal_google",
            "SESSION_SECRET": "synthetic-browser-session-secret-with-enough-entropy",
            "SQAG_TRACKING_HMAC_KEY": "synthetic-browser-tracking-key",
            "SQAG_TRACKING_HMAC_KEY_VERSION": "synthetic-v1",
            "SQAG_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
            "SQAG_PLATFORM_LAUNCH_MODE": "disabled",
            "SQAG_PUBLIC_BASE_URL": SYNTHETIC_INTERNAL_ALPHA_ORIGIN,
            "SQAG_INTERNAL_WORKSPACE_ID": "workspace-internal-alpha",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "database",
            "SQAG_DATABASE_URL": f"sqlite:///{(runtime_root / 'sqag.sqlite3').as_posix()}",
            "QUOTE_DATA_ROOT": str(runtime_root / "data"),
            "QUOTE_OUTPUT_ROOT": str(runtime_root / "output"),
            "QUOTE_TMP_ROOT": str(runtime_root / "tmp"),
            "QUOTE_LOG_ROOT": str(runtime_root / "logs"),
            "SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON": json.dumps(
                [
                    {
                        "sub": "synthetic-browser-subject",
                        "email": "alpha-admin@example.test",
                        "role": "admin",
                    },
                    {
                        "sub": "synthetic-browser-operator-subject",
                        "email": "alpha-operator@example.test",
                        "role": "operator",
                    },
                ],
                separators=(",", ":"),
            ),
            "OIDC_ISSUER_URL": "https://accounts.google.com",
            "OIDC_CLIENT_ID": "synthetic-browser-client",
            "OIDC_CLIENT_SECRET": "synthetic-browser-client-secret",
            "OIDC_REDIRECT_URI": f"{SYNTHETIC_INTERNAL_ALPHA_ORIGIN}/callback",
            "OIDC_AUTHORIZE_URL": "https://accounts.google.com/o/oauth2/v2/auth",
            "OIDC_TOKEN_URL": "https://oauth2.googleapis.com/token",
        }
    )
    webapp.INTERNAL_AUTH_STATE.reset()
    webapp.apply_sqag_storage_migrations(webapp.configured_database_url())

    original_version_row = webapp.DatabaseSqagStorage._publication_version_row
    original_version_artifact = webapp.DatabaseSqagStorage._publication_version_artifact

    def audited_version_row(storage, run_id, connection=None):
        HarnessHandler.artifact_audit.append({
            "stage": "version", "workspace": storage.workspace_id, "run_id": str(run_id),
        })
        return original_version_row(storage, run_id, connection)

    def audited_version_artifact(storage, session_id, run_id, kind, **kwargs):
        HarnessHandler.artifact_audit.append({
            "stage": "artifact", "workspace": storage.workspace_id,
            "session_id": str(session_id), "run_id": str(run_id), "kind": str(kind),
        })
        return original_version_artifact(storage, session_id, run_id, kind, **kwargs)

    webapp.DatabaseSqagStorage._publication_version_row = audited_version_row
    webapp.DatabaseSqagStorage._publication_version_artifact = audited_version_artifact
    webapp.is_allowed_host_header = lambda _host: True
    webapp.request_sqag_origin = lambda _host: webapp.configured_sqag_public_base_url()
    webapp.google_oidc_verifier = lambda: SyntheticVerifier()
    original_cookie_header = webapp.cookie_header_value
    webapp.cookie_header_value = lambda *args, **kwargs: original_cookie_header(
        *args, **kwargs
    ).replace("; Secure", "")

    server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), HarnessHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    webapp.internal_google_authorize_url = lambda transaction: (
        f"{base_url}/__synthetic_oidc/authorize?"
        + urllib.parse.urlencode(
            {
                "state": transaction.state,
                "nonce": transaction.nonce,
                "code_challenge": transaction.code_challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    print(base_url, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        webapp.INTERNAL_AUTH_STATE.reset()
        runtime.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
