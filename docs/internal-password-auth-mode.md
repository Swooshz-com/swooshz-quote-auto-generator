# Internal staff password access

`SQAG_AUTH_MODE=internal_password` serves a private, single-instance staff login.
Each role has a separate shared password. A holder can sign in using an alias
assigned to that role. This does not verify the individual using the alias.
There is no registration or password reset.

## Server configuration

Configure values only in the host secret manager, under separate live authority.
Never put actual emails, identity bindings, passwords, verifier values, session
secrets or API keys in source, fixtures, logs, GitHub, evidence or model prompts.

- `APP_MODE=deploy`, `AUTH_REQUIRED=true`, `SQAG_AUTH_MODE=internal_password`.
- `SESSION_SECRET`: at least 32 characters; preserve the current signing and
  browser-recovery secret for an approved cutover.
- `SQAG_INTERNAL_ALLOWED_EMAILS` and `SQAG_INTERNAL_ALLOWED_EMAILS_ADMIN` are
  semicolon-separated operator and admin alias lists. Either list may be empty.
  At least one alias must be configured. The admin role takes precedence on
  overlap. Each list rejects duplicate canonical emails, empty elements,
  wildcards, domains and malformed or non-ASCII emails. Emails are trimmed and
  casefolded. There is no dot, plus or domain alias inference.
- `SQAG_INTERNAL_IDENTITY_BINDINGS_JSON` optionally preserves legacy IDs. Keep
  the existing exact `email`, `user_id` and `role` record shape, including
  inactive bindings. The legacy role field is validated for format. Only the
  role lists grant access. IDs must be unique after storage projection.
- `SQAG_INTERNAL_WORKSPACE_ID` is the existing fixed workspace, with 3-64 ASCII
  letters, digits, underscores or hyphens, starting with a letter or digit.
- `SQAG_INTERNAL_LOGIN_PASSWORD` is required for effective operator aliases.
  `SQAG_INTERNAL_ADMIN_LOGIN_PASSWORD` is required for admin aliases. Each
  configured password is 12-1024 UTF-8 bytes without control characters. If
  both are configured, they must differ. These are application limits.
- `SQAG_PLATFORM_LAUNCH_MODE=disabled`, one dedicated HTTPS origin in
  `SQAG_PUBLIC_BASE_URL`, and exact direct proxy CIDRs in
  `SQAG_TRUSTED_PROXY_CIDRS`. There is no trust-all proxy range.

The alias union and the binding array are each limited to 100 identities.
Each list and the binding JSON are each limited to 16 KiB. Emails
are at most 254 characters; stable IDs retain the previous 6-255-character ASCII
syntax. Host parsing and env-size compatibility require separate installed tests.
The retired Google mode cannot start a session. The former Google identity map
and role lists are ignored by the runtime and rejected by the current deployment
template verifier. They are `SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON`,
`SQAG_INTERNAL_ADMIN_EMAILS` and `SQAG_INTERNAL_OPERATOR_EMAILS`. Keep them only
with rollback snapshots that still run the old Google implementation. The
independent local OIDC and Platform settings remain incompatible with password
mode.

## Request and session behaviour

`GET /login` renders a branded form without listing allowed identities. Staff
enter their approved email and the password for their role. It
limits form issuance to ten requests per effective client per 60 seconds before
allocating a nonce. This bucket is separate from the password-attempt budget.
Login logs keep only the canonical `/login` path, discarding submitted authority,
userinfo, query names/values and fragments, including initial double slashes.
`POST /login` requires the expected host, trusted deploy configuration, exact
origin and a short-lived browser-bound nonce. The server consumes the nonce
atomically, accepts only three bounded form fields, and rate-limits before
password verification. The initial limit is ten attempts per effective client per
60 seconds, with bounded shared overflow buckets. Each configured credential uses
PBKDF2-HMAC-SHA256 with 600,000 iterations and a 32-byte result before a constant-time fixed-size comparison, including unknown
aliases and bounded invalid-input dummy checks. The salt is purpose-separated
with HMAC from the existing `SESSION_SECRET` and canonical workspace/identity
context. Identical configuration and alias reordering keep the same policy
fingerprint; password, signing-secret or identity-policy changes revoke sessions.
No extra credential, salt setting, persistent verifier or package is required.
The KDF uses the OpenSSL support already required by the pinned Python runtime.
The work factor follows [OWASP password guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2);
the server derives configured verifiers once at startup. Each guarded login
attempt derives one candidate. Readiness and protected requests run no PBKDF2.
After changing authentication settings, restart the server before staff sign
in again. Unknown aliases and wrong passwords receive the same generic failure
and a support reference.

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
drafts are not made editable. Unbound aliases receive a versioned workspace-and-email ID. Password and signing
secret rotation leave that ID stable, as does removal and re-addition of the same
alias. Renaming an alias needs a separate continuity decision. Admins manage
pricing references, including imports and exports. Operators can select a
reference and use it to price a quote. Installed qualification is required to
verify continuity and saved-data access before cutover.

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
