# SQAG Internal-Alpha VPS/Coolify Scaffold

This folder contains SQAG-specific placeholder templates for an
already-prepared VPS/Coolify-style host. It intentionally does not describe
generic VPS setup, Coolify installation, SSH, firewall, DNS, TLS, deployment,
or server maintenance.

Use with:

- Docs: `docs/internal-uat-coolify-deploy.md`
- Env template: `deploy/internal-uat/coolify/sqag.uat.env.example`
- Volume map: `deploy/internal-uat/coolify/volume-map.example.md`

Recommended app settings:

- Runtime/buildpack: Nixpacks with exact Python-only contract at `nixpacks.toml`
  (see validator: `python scripts/validate_nixpacks_python_contract.py`)
- Python version: `3.12.13` bound through `.python-version` and pinned
  exactly through the immutable `nixpkgsArchive` in `[phases.setup]`.
- Python dependencies: `requirements.txt`
- Start command: `python webapp/server.py` (bound in `nixpacks.toml`)
- The root `package.json` is CI/local-Playwright tooling and must not cause
  Nixpacks to select Node as a production provider. The `nixpacks.toml`
  `providers = ["python"]` declaration enforces Python-only production builds.
- Port: value supplied by `PORT`
- Healthcheck path: `/api/health`
- Instance count: `1`
- Storage posture: `SQAG_STORAGE_MODE=database` and
  `SQAG_ARTIFACT_STORAGE_MODE=object` with the canonical
  `SQAG_OBJECT_STORAGE_*` names supplied through the host secret manager
- Auth posture: `AUTH_REQUIRED=true`, a host-secret-managed `SESSION_SECRET`
  of at least 32 characters, a dedicated host-secret-managed
  `SQAG_TRACKING_HMAC_KEY`, non-secret `SQAG_TRACKING_HMAC_KEY_VERSION`
  configuration matching the runtime format,
  `SQAG_PLATFORM_LAUNCH_MODE=platform`, and an HTTPS `SQAG_PLATFORM_BASE_URL`
  except for explicit loopback-only local smoke endpoints. Standalone OIDC
  does not establish a workspace and cannot start deploy mode.
- Proxy posture: `SQAG_TRUSTED_PROXY_CIDRS` contains only the exact CIDR or
  comma-separated CIDRs of the Coolify/Traefik proxy peers that connect
  directly to SQAG. Do not use a trust-all network. Missing or malformed proxy
  configuration blocks deploy preflight and startup.

SQAG accepts `X-Forwarded-For` only when the direct socket peer belongs to one
of those configured proxy networks. It validates and bounds the forwarded
chain, walks it from the proxy side, and falls back to the socket peer when the
header cannot be trusted. Discover and enter the actual proxy CIDRs only in the
host environment manager; keep them out of this placeholder template and PR
output.

Before starting deploy-mode UAT, set the env values in Coolify secrets or
environment management and run:

```powershell
python scripts\verify_internal_uat_deploy_template.py
```

```powershell
python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\internal-alpha-hosted
```

This validation uses synthetic/local hosted-contract smoke on `127.0.0.1`.
It does not test a public hosted URL and is not live Neon/R2 or Platform
handoff evidence.

Keep populated env files, real Platform values, database URLs, private
profile/pricing files, runtime data, generated quote exports, object-provider
values, hostnames, and server addresses out of git and PR output.
