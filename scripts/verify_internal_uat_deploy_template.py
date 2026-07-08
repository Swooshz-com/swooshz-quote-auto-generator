#!/usr/bin/env python3
"""Verify the internal UAT Coolify env template without printing values."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "deploy" / "internal-uat" / "coolify" / "sqag.uat.env.example"

REQUIRED_KEYS = (
    "APP_MODE",
    "AUTH_REQUIRED",
    "SESSION_SECRET",
    "SQAG_STORAGE_MODE",
    "SQAG_ARTIFACT_STORAGE_MODE",
    "SQAG_DATABASE_URL",
    "SQAG_PLATFORM_LAUNCH_MODE",
    "SQAG_PLATFORM_BASE_URL",
    "OIDC_ISSUER_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_REDIRECT_URI",
    "OIDC_AUTHORIZE_URL",
    "OIDC_TOKEN_URL",
    "OIDC_USERINFO_URL",
    "OIDC_LOGOUT_URL",
    "AUTH_ALLOWED_EMAILS",
    "AUTH_ALLOWED_DOMAINS",
    "AUTH_ALLOW_ANY_AUTHENTICATED_USER",
    "AUTH_APPROVED_TESTER_ROLE",
    "QUOTE_DATA_ROOT",
    "QUOTE_OUTPUT_ROOT",
    "QUOTE_TMP_ROOT",
    "QUOTE_LOG_ROOT",
    "PORT",
)

PLACEHOLDER_KEYS = {
    "SESSION_SECRET",
    "SQAG_DATABASE_URL",
    "SQAG_PLATFORM_BASE_URL",
    "OIDC_ISSUER_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_REDIRECT_URI",
    "OIDC_AUTHORIZE_URL",
    "OIDC_TOKEN_URL",
    "OIDC_USERINFO_URL",
    "OIDC_LOGOUT_URL",
    "AUTH_ALLOWED_EMAILS",
    "AUTH_ALLOWED_DOMAINS",
    "QUOTE_DATA_ROOT",
    "QUOTE_OUTPUT_ROOT",
    "QUOTE_TMP_ROOT",
    "QUOTE_LOG_ROOT",
    "PORT",
}

EXPECTED_VALUES = {
    "APP_MODE": "deploy",
    "AUTH_REQUIRED": "true",
    "SQAG_STORAGE_MODE": "database",
    "SQAG_ARTIFACT_STORAGE_MODE": "database",
    "SQAG_PLATFORM_LAUNCH_MODE": "platform",
    "AUTH_ALLOW_ANY_AUTHENTICATED_USER": "false",
}

FORBIDDEN_MARKERS = {
    "embedded-logo-reference": re.compile(r"logo_data_url|data:image/[^;\s]+;base64,", re.IGNORECASE),
    "real-looking-secret": re.compile(
        r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----|access_token|refresh_token|id_token)",
        re.IGNORECASE,
    ),
    "private-local-path": re.compile(r"([A-Za-z]:\\Users\\|/Users/|/home/[^/\s]+/)", re.IGNORECASE),
    "committed-url-or-db-url": re.compile(
        r"\b(?:https?://(?!<)|postgres(?:ql)?://|mysql://|sqlite:///|s3://)",
        re.IGNORECASE,
    ),
    "generated-export-path": re.compile(
        r"(^|[/\\])(?:_output|generated[-_]?outputs?)(?:[/\\]|$)|quotation\.(xlsx|pdf)",
        re.IGNORECASE,
    ),
    "bank-or-payment-data": re.compile(r"\b(bank account|account number|swift|iban|paynow|uen)\b", re.IGNORECASE),
    "runtime-file-content": re.compile(r"quote-session\.json|draft-files\.json|pricing-catalog\.json", re.IGNORECASE),
}


@dataclass(frozen=True)
class Finding:
    key: str
    category: str
    message: str


def parse_env_template(path: Path) -> tuple[dict[str, str], list[Finding]]:
    values: dict[str, str] = {}
    findings: list[Finding] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values, [Finding(str(path), "missing-template", "template cannot be read")]

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            findings.append(Finding(f"line {line_number}", "invalid-line", "line must use KEY=value syntax"))
            continue
        normalized_key = key.strip()
        if normalized_key in values:
            findings.append(Finding(normalized_key, "duplicate-key", "key appears more than once"))
        values[normalized_key] = value.strip().strip("'\"")
    return values, findings


def is_placeholder(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def verify_template(path: Path = DEFAULT_TEMPLATE) -> dict[str, object]:
    findings: list[Finding] = []
    if not path.exists():
        findings.append(Finding(str(path), "missing-template", "template does not exist"))
        return {"status": "blocked", "findings": findings}

    values, parse_findings = parse_env_template(path)
    findings.extend(parse_findings)

    for key in REQUIRED_KEYS:
        if key not in values:
            findings.append(Finding(key, "missing-key", "required key is missing"))
        elif values[key] == "":
            findings.append(Finding(key, "empty-value", "required key is empty"))

    for key in PLACEHOLDER_KEYS:
        value = values.get(key, "")
        if value and key == "OIDC_LOGOUT_URL" and value == "<optional-placeholder>":
            continue
        if value and not is_placeholder(value):
            findings.append(Finding(key, "non-placeholder-value", "provider-specific or secret value must remain a placeholder"))

    for key, expected in EXPECTED_VALUES.items():
        if values.get(key, "") != expected:
            findings.append(Finding(key, "unexpected-value", "value does not match the internal UAT template expectation"))

    template_text = path.read_text(encoding="utf-8", errors="replace")
    for category, pattern in FORBIDDEN_MARKERS.items():
        if pattern.search(template_text):
            findings.append(Finding(path.name, category, "template contains a forbidden marker"))

    return {"status": "ready" if not findings else "blocked", "findings": findings}


def finding_to_dict(finding: Finding) -> dict[str, str]:
    return {"key": finding.key, "category": finding.category, "message": finding.message}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", nargs="?", default=str(DEFAULT_TEMPLATE), help="Path to sqag.uat.env.example")
    args = parser.parse_args(argv)
    status = verify_template(Path(args.template))
    findings = [finding_to_dict(finding) for finding in status["findings"]]
    print(f"status={status['status']}")
    for finding in findings:
        print(f"{finding['category']}: {finding['key']} - {finding['message']}")
    return 0 if status["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
