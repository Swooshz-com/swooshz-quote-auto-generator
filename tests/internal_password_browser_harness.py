#!/usr/bin/env python3
"""Loopback-only synthetic password login harness; never loads host dotenv values."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SQAG_DISABLE_DOTENV"] = "1"
from webapp import server as webapp
from tests.gateway_test_support import synthetic_configuration

SYNTHETIC_PASSWORD = "synthetic-browser-group-password"


def configure_auth_env():
    for key in tuple(os.environ):
        if key.startswith(("SQAG_", "OIDC_", "AUTH_", "QUOTE_", "OPENAI_", "OPENROUTER_", "DEEPSEEK_")) or key in {"APP_MODE", "SESSION_SECRET", "USER_TYPE", "LOCAL_USER_ROLE"}:
            os.environ.pop(key, None)
    os.environ.update({
        "SQAG_DISABLE_DOTENV":"1", "APP_MODE":"local", "AUTH_REQUIRED":"true", "SQAG_AUTH_MODE":"internal_password",
        "SESSION_SECRET":"synthetic-browser-session-secret-with-enough-entropy",
        "SQAG_TRACKING_HMAC_KEY":"synthetic-browser-tracking-key", "SQAG_TRACKING_HMAC_KEY_VERSION":"synthetic-v1",
        "SQAG_TRUSTED_PROXY_CIDRS":"127.0.0.1/32", "SQAG_PLATFORM_LAUNCH_MODE":"disabled",
        "SQAG_PUBLIC_BASE_URL":"https://internal-alpha.example.test", "SQAG_INTERNAL_WORKSPACE_ID":"workspace-internal-alpha",
        "SQAG_INTERNAL_ALLOWED_EMAILS":"alpha-admin"+"@"+"example.test;alpha-operator"+"@"+"example.test",
        "SQAG_INTERNAL_IDENTITY_BINDINGS_JSON":json.dumps([
            {"user_id":"synthetic-browser-subject", "email":"alpha-admin"+"@"+"example.test", "role":"admin"},
            {"user_id":"synthetic-browser-operator-subject", "email":"alpha-operator"+"@"+"example.test", "role":"operator"}]),
        "SQAG_INTERNAL_LOGIN_PASSWORD":SYNTHETIC_PASSWORD,
        **synthetic_configuration(),
    })


class HarnessHandler(webapp.QuoteRunnerHandler):
    pass


def main():
    configure_auth_env()
    if len(sys.argv)>6:
        os.environ.update({"QUOTE_DATA_ROOT":sys.argv[2], "QUOTE_OUTPUT_ROOT":sys.argv[3], "QUOTE_TMP_ROOT":sys.argv[4], "QUOTE_LOG_ROOT":sys.argv[5], "SQAG_LOCAL_PRICING_REFERENCES_ROOT":sys.argv[6]})
    # SYNTHETIC_WORKSPACE_SETUP
    server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), HarnessHandler)
    print("http://127.0.0.1:"+str(server.server_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
