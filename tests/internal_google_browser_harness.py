#!/usr/bin/env python3
"""Local browser harness for the synthetic internal-Google protocol adapter."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webapp import server as webapp


SYNTHETIC_INTERNAL_ALPHA_ORIGIN = "https://internal-alpha.example.test"


class SyntheticVerifier:
    claim_case = "approved"
    require_account_choice = False

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
        if parsed.path == "/__synthetic_oidc/case":
            params = urllib.parse.parse_qs(parsed.query)
            value = (params.get("value") or [""])[0]
            if value not in {
                "approved",
                "approved-account-choice",
                "unknown-sub",
                "same-email-different-sub",
                "same-sub-different-email",
            }:
                self.send_json({"error": "invalid synthetic case"}, status=400)
                return
            SyntheticVerifier.require_account_choice = value == "approved-account-choice"
            SyntheticVerifier.claim_case = "approved" if SyntheticVerifier.require_account_choice else value
            self.send_json({"status": "ok"})
            return
        if parsed.path == "/__synthetic_oidc/authorize":
            params = urllib.parse.parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            callback = "/callback?" + urllib.parse.urlencode(
                {"state": state, "code": "synthetic-browser-code"}
            )
            if SyntheticVerifier.require_account_choice:
                if params.get("prompt") != ["select_account"]:
                    self.send_json({"error": "account choice not requested"}, status=400)
                    return
                self.send_auth_page(
                    "Choose synthetic account", "Local provider test adapter only.",
                    action_href=callback, action_label="Choose synthetic account",
                )
            else:
                self.send_redirect(callback)
            return
        super().do_GET()


def main() -> int:
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
    # Preserve the production OIDC query; replace only the provider transport.
    real_authorize_url = webapp.internal_google_authorize_url
    webapp.internal_google_authorize_url = lambda transaction: (
        f"{base_url}/__synthetic_oidc/authorize?"
        + urllib.parse.urlsplit(real_authorize_url(transaction)).query
    )

    print(base_url, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        webapp.INTERNAL_AUTH_STATE.reset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
