# Temporary Internal Google Authentication Mode

## Status and scope

`SQAG_AUTH_MODE=internal_google` is a temporary, private, single-instance
internal-alpha lane. It is not public-release authentication and can never make
`production_ready=true`. It does not create accounts, workspaces, memberships,
OAuth clients, credentials, or provider infrastructure. Platform authentication
remains the final launch architecture.

Deploy mode has one explicit, fail-closed selector:

| `SQAG_AUTH_MODE` | Meaning | Allowed with `APP_MODE=deploy` |
| --- | --- | --- |
| `platform` | Platform launch, finalization, validation, entitlement, and revocation | Yes, with the complete existing Platform contract |
| `internal_google` | Temporary exact-allowlist Google OIDC lane | Yes, with the complete contract below |
| `local` | Local and isolated component testing only | No |

`APP_MODE=deploy` requires an explicit selector and `AUTH_REQUIRED=true`.
Unknown, missing, mixed, incomplete, or local configurations stop before the
app serves protected routes. Platform configuration and internal-Google
configuration are mutually exclusive.

## OIDC transaction and verification

The internal lane uses Google authorization code flow with:

- 256-bit random state, nonce, and PKCE verifier values;
- one-time, process-local transactions with a ten-minute maximum lifetime;
- PKCE `S256` only;
- the exact configured public callback
  `https://quote.swooshz.com/callback`, never the reverse-proxy request URL;
- Google discovery metadata pinned to the exact issuer, authorization endpoint,
  token endpoint, HTTPS JWKS URI, `S256`, and `RS256`;
- PyJWT 2.13.0 and cryptography 49.0.0 for maintained JWK, RSA signature, and
  issuer/audience/time/required-claim validation;
- a fixed `RS256` allowlist, exact audience, exact issuer, bounded clock skew,
  exact nonce comparison, stable nonempty Google `sub`, `email_verified=true`,
  and a canonical exact email;
- bounded provider timeouts and response bodies, exact response MIME types,
  strict JSON objects, and no redirect following.

SQAG does not implement JWT cryptography or JWK parsing. It does not use
userinfo or refresh tokens. Callback parameters are size- and count-bounded,
single-valued, allowlisted, and parsed once. The state transaction is consumed
before token verification, so failures and retries cannot replay it. A browser
callback is not required to carry an `Origin` header.

The app never logs authorization codes, state, nonce, PKCE verifiers, provider
tokens, client secrets, raw provider responses, or tester email addresses.
Audit records contain bounded failure categories and an opaque subject digest
where correlation is necessary.

## Exact subject-bound admission and roles

The host secret/configuration manager supplies:

```text
SQAG_INTERNAL_WORKSPACE_ID
SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON
```

The workspace identifier is one fixed 3-64 character ASCII identifier using
letters, digits, underscore, or hyphen. The identity value is one server-only,
valid UTF-8 JSON array of 1-100 records and at most 16 KiB. Every record has
exactly three string fields:

```json
[
  {
    "sub": "synthetic-google-subject-001",
    "email": "synthetic-admin@example.test",
    "role": "admin"
  }
]
```

The subject is an exact 6-255 character ASCII identifier using letters,
digits, dot, underscore, colon, or hyphen. Email parsing rejects surrounding
whitespace and case-folds only; it does not apply Gmail dot, alias, plus,
domain, or Unicode transformations. The only roles are exactly `admin` and
`operator`.

Configuration rejects malformed JSON, non-arrays, empty or oversized arrays,
non-object entries, missing or additional keys, non-string or nested values,
padded or blank values, invalid subjects, wildcards, domain-only or malformed
emails, duplicate subjects, duplicate canonical emails, conflicting roles,
and unsupported roles. The former `SQAG_INTERNAL_ALLOWED_EMAILS`,
`SQAG_INTERNAL_ADMIN_EMAILS`, and `SQAG_INTERNAL_OPERATOR_EMAILS` variables are
deprecated rejected configuration. Supplying any of them, including alongside
the JSON authority, fails deploy readiness. There is no second admission
authority.

After token verification, SQAG looks up the approved record by exact `sub`,
then requires the token's canonical verified email to equal that record and
derives the role only from that record. Unknown subjects, subject/email
substitution, account reassignment, and role inheritance are rejected before
session creation. No domain inference, trust-on-first-login, automatic
enrolment, email-only bootstrap, self-registration, or any-authenticated-user
fallback exists.

Obtaining each real tester's Google subject is a separate explicitly authorised
provider-enrolment operation. Repository tests use synthetic subjects only.
SQAG provides no endpoint that returns subjects.

## Session, revocation, and logout

Successful authentication rotates any prior identifier and issues a host-only
`Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/` cookie in deploy mode. The
eight-hour bounded session binds the authentication mode, Google `sub`,
canonical verified email, fixed workspace, exact role, issue/expiry times,
random session identifier, and a SHA-256 policy fingerprint.

The policy fingerprint includes the fixed workspace and the complete canonical
subject/email/role map sorted independently of input JSON order. Every
protected request revalidates the signed cookie against the process-local
registry, current exact identity record, workspace, role, mode, expiry, and
fingerprint. Subject/email/role/workspace removal or change, mode change,
expiry, logout, cookie tampering, or process restart denies the next request.
JSON reordering alone preserves the fingerprint. The configured identity map
is available again after restart, while process-local sessions remain
invalidated. This intentionally limits the temporary lane to one app instance;
multi-instance sessions require a durable shared revocation store and are not
credited by readiness.

Logout is an authenticated, CSRF-checked `POST /logout`. `GET /logout` returns
405 and cannot mutate state. Logout revokes the server-side session, clears
cookies and browser storage, and returns only a fixed safe navigation target in
`X-SQAG-Logout-Location`.

## Readiness

- `internal_google`: may make `internal_alpha_ready=true` only when every
  existing hosted prerequisite and this entire contract pass. It always adds a
  public-production blocker.
- `platform`: retains the existing Platform readiness and per-request
  validation/revocation contract.
- `local`: can never satisfy deploy, internal-alpha, or production readiness.

The committed Coolify template is a placeholder-only inventory for the
temporary internal lane. Populating it, creating an OAuth client, deploying,
or testing real credentials requires separate authorization and evidence.

## Public-release removal gate

Public release remains blocked until `internal_google`, its identity JSON and
fixed workspace configuration, its direct Google login/callback routes, and
all temporary sessions are removed or permanently disabled from public-release
paths. Provider-side OAuth client retirement and redirect removal are separate
authorised operations. Platform must own login, membership, role, entitlement,
launch, and revocation before public release.
