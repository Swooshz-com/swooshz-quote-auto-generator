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

## Exact admission and roles

The host secret/configuration manager supplies:

```text
SQAG_INTERNAL_WORKSPACE_ID
SQAG_INTERNAL_ALLOWED_EMAILS
SQAG_INTERNAL_ADMIN_EMAILS
SQAG_INTERNAL_OPERATOR_EMAILS
```

The workspace identifier is one fixed 3-64 character ASCII identifier using
letters, digits, underscore, or hyphen. Email lists are comma-separated exact
addresses. Parsing trims surrounding whitespace and case-folds addresses; it
does not apply Gmail dot, alias, or plus-address transformations.

Configuration is rejected for empty entries, wildcards, domain-only entries,
malformed addresses, case-folded duplicates, role entries outside the main
allowlist, multiple roles, allowed users without exactly one supported role, or
unknown `SQAG_INTERNAL_*_EMAILS` role variables. The only roles are `admin` and
`operator`. No domain inference, self-registration, or any-authenticated-user
fallback exists. Google `sub`, not email, is the primary external identity.

## Session, revocation, and logout

Successful authentication rotates any prior identifier and issues a host-only
`Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/` cookie in deploy mode. The
eight-hour bounded session binds the authentication mode, Google `sub`,
canonical verified email, fixed workspace, exact role, issue/expiry times,
random session identifier, and a SHA-256 policy fingerprint.

The process-local server registry is authoritative. Every protected request
revalidates the signed cookie against that registry and the current allowlist,
role mapping, workspace, mode, expiry, and policy fingerprint. Allowlist
removal, role/workspace change, mode change, expiry, logout, cookie tampering,
or process restart therefore denies the next request. This intentionally limits
the temporary lane to one app instance; multi-instance sessions require a
durable shared revocation store and are not credited by readiness.

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
