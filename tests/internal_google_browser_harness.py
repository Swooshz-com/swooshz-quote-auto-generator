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
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/__run550/publication-receipt":
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
            xlsx = (metadata.get("exports") or {}).get("xlsx") or {}
            self.send_json(
                {
                    "status": "ok",
                    "publication": {
                        "run_id": str(publication.get("run_id") or ""),
                        "active_publication_id": str(
                            publication.get("active_publication_id") or ""
                        ),
                        "committed_draft_state_digest": str(
                            publication.get("committed_draft_state_digest") or ""
                        ),
                        "committed_output_revision": publication.get(
                            "committed_output_revision"
                        ),
                    },
                    "xlsx": {
                        "publication_id": str(xlsx.get("publication_id") or "")
                    },
                }
            )
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
        if parsed.path != "/__run550/clone-session":
            super().do_POST()
            return
        session = self.current_auth_session()
        if not session:
            self.send_json({"error": "Not found"}, status=404)
            return
        try:
            payload = self.read_json()
            source_session_id = webapp.safe_quote_session_id(
                payload.get("source_session_id"), ""
            )
            cross_session_id = webapp.safe_quote_session_id(
                payload.get("cross_workspace_session_id"), ""
            )
            mismatch_session_id = webapp.safe_quote_session_id(
                payload.get("mismatch_session_id"), ""
            )
            mismatch_run_id = webapp.safe_reference(
                payload.get("mismatch_run_id"), "run-"
            )
            if not all(
                (source_session_id, cross_session_id, mismatch_session_id, mismatch_run_id)
            ):
                raise ValueError("invalid fixture identity")
            storage = webapp.app_storage_for_auth_session(session)
            if not isinstance(storage, webapp.DatabaseSqagStorage):
                raise ValueError("database fixture storage required")
            with storage.connection() as connection:
                source = connection.execute(
                    "select metadata_json, draft_files_json, created_at, updated_at "
                    "from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                    (storage.workspace_id, source_session_id),
                ).fetchone()
                if source is None:
                    raise ValueError("source fixture missing")
                source_metadata = json.loads(str(source["metadata_json"] or "{}"))
                cross_metadata = dict(source_metadata)
                cross_metadata["session_id"] = cross_session_id
                connection.execute(
                    "insert into sqag_quote_sessions "
                    "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                    "values (?, ?, ?, ?, ?, ?)",
                    (
                        "workspace-run550-other",
                        cross_session_id,
                        json.dumps(cross_metadata, separators=(",", ":"), sort_keys=True),
                        source["draft_files_json"],
                        source["created_at"],
                        source["updated_at"],
                    ),
                )
                mismatch_metadata = dict(source_metadata)
                mismatch_metadata["session_id"] = mismatch_session_id
                publication = dict(mismatch_metadata.get("publication") or {})
                publication["run_id"] = mismatch_run_id
                publication["state"] = "published"
                mismatch_metadata["publication"] = publication
                connection.execute(
                    "insert into sqag_quote_sessions "
                    "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                    "values (?, ?, ?, ?, ?, ?)",
                    (
                        storage.workspace_id,
                        mismatch_session_id,
                        json.dumps(mismatch_metadata, separators=(",", ":"), sort_keys=True),
                        source["draft_files_json"],
                        source["created_at"],
                        source["updated_at"],
                    ),
                )
            self.send_json({"status": "ok"})
        except (ValueError, webapp.RequestBodyError):
            self.send_json({"error": "Invalid fixture request"}, status=400)


def main() -> int:
    runtime = tempfile.TemporaryDirectory(prefix="sqag-run550-auth-")
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
