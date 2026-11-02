# Internal staff password access

`SQAG_AUTH_MODE=internal_password` serves a private, single-instance staff login.
The shared password proves group membership. A holder may select any allowed
email alias, including its configured admin or operator role. This does not prove
which individual is using that alias. There is no registration or password reset.

## Server configuration

Configure values only in the host secret manager, under separate live authority.
Never put actual emails, identity bindings, passwords, verifier values, session
secrets or API keys in source, fixtures, logs, GitHub, evidence or model prompts.

- `APP_MODE=deploy`, `AUTH_REQUIRED=true`, `SQAG_AUTH_MODE=internal_password`.
- `SESSION_SECRET`: at least 32 characters; preserve the current signing and
  browser-recovery secret for an approved cutover.
- `SQAG_INTERNAL_ALLOWED_EMAILS`: a semicolon-separated list. Trim and casefold
  each email; empty elements, duplicates, wildcards, domains and malformed or
  non-ASCII emails are refused. There is no dot, plus or domain alias inference.
- `SQAG_INTERNAL_IDENTITY_BINDINGS_JSON`: an array of exact `email`, `user_id`
  and `role` records. Its canonical email set must equal the allowlist. IDs must
  be unique and preserve the existing opaque storage IDs. Roles are exactly
  `admin` or `operator`; the array order has no effect on policy identity.
- `SQAG_INTERNAL_WORKSPACE_ID`: the existing fixed workspace, with 3-64 ASCII
  letters, digits, underscores or hyphens, starting with a letter or digit.
- `SQAG_INTERNAL_LOGIN_PASSWORD`: one shared password, 12-1024 UTF-8 bytes,
  without control characters. These are application limits, not host limits.
- `SQAG_PLATFORM_LAUNCH_MODE=disabled`, one dedicated HTTPS origin in
  `SQAG_PUBLIC_BASE_URL`, and exact direct proxy CIDRs in
  `SQAG_TRUSTED_PROXY_CIDRS`. There is no trust-all proxy range.

The allowlist and bindings are bounded to 100 identities and 16 KiB each. Emails
are at most 254 characters; stable IDs retain the previous 6-255-character ASCII
syntax. Host parsing and env-size compatibility require separate installed tests.
The former internal Google mode, identity map, admin/operator email lists,
OIDC configuration and Platform configuration are refused in password mode.

## Request and session behaviour

`GET /login` renders a standalone form without listing allowed identities. It
limits form issuance to ten requests per effective client per 60 seconds before
allocating a nonce. This bucket is separate from the password-attempt budget.
Login logs keep only the canonical `/login` path, discarding submitted authority,
userinfo, query names/values and fragments, including initial double slashes.
`POST /login` requires the expected host, trusted deploy configuration, exact
origin and a short-lived browser-bound nonce. The server consumes the nonce
atomically, accepts only three bounded form fields, and rate-limits before
password verification. The initial limit is ten attempts per effective client per
60 seconds, with bounded shared overflow buckets. The shared credential uses
PBKDF2-HMAC-SHA256 with 600,000 iterations and a 32-byte result before a constant-time fixed-size comparison, including unknown
aliases and bounded invalid-input dummy checks. The salt is purpose-separated
with HMAC from the existing `SESSION_SECRET` and canonical workspace/identity
context. Identical configuration and alias reordering keep the same policy
fingerprint; password, signing-secret or identity-policy changes revoke sessions.
No extra credential, salt setting, persistent verifier or package is required.
The KDF uses the OpenSSL support already required by the pinned Python runtime.
The work factor follows [OWASP password guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2);
configuration/session checks also derive the verifier, so qualify request cost on
the intended single-instance host before cutover. Unknown aliases
and wrong passwords receive the same generic failure and a support reference.

The HTTP server owns its nonce and session registries. A successful login rotates
the prior session; the host-only cookie contains only the mode and opaque session
ID. Deploy cookies are Secure, HttpOnly and SameSite=Lax. Sessions expire after
eight hours; at most 4,096 sessions and 1,024 pending nonces are retained. Restart,
logout and any material policy or password change invalidate sessions. Existing
Google-mode cookies cannot establish password access.

`/api/session` returns permissions and an opaque browser-recovery scope without
an identity roster, binding digest or internal user record. `POST /logout`
requires the authenticated session's CSRF token and revokes the copied cookie on
the server. A domain revisit stays signed out until an explicit new login.

## Existing work and independent modes

Storage, jobs, downloads and asynchronous snapshots retain the same stable user
and workspace keys. Operators retain owner-only editing. Existing same-workspace
admin visibility of progressed/generated quotations remains bounded; other users'
drafts are not made editable. A new email-derived ID or ownership migration is
not part of this implementation. Configured continuity and actual saved-data
access require installed qualification before any cutover.

The independent Platform launch/session contracts and explicitly local OIDC
component adapter remain covered by their existing tests. The local adapter does
not grant deploy access. Remove it only when its independent component consumers
are retired; it is not a compatibility route for the removed internal Google mode.

## Validation and qualification

Run the password policy, actual-HTTP, identity-continuity, ownership, recovery and
browser suites listed in [the testing plan](testing-plan.md). Synthetic source
checks do not prove deployed proxy behaviour, credential loading, record access or
owner acceptance. Live configuration, deployment, customer-data access and paid
provider checks require their own exact approval. Public production readiness and
multi-instance revocation remain outside this temporary internal boundary.
