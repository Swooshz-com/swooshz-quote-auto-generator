# KQAG Internal-Alpha VPS/Coolify Scaffold

This folder contains KQAG-specific placeholder templates for an
already-prepared VPS/Coolify-style host. It intentionally does not describe
generic VPS setup, Coolify installation, SSH, firewall, DNS, TLS, deployment,
or server maintenance.

Use with:

- Docs: `docs/internal-uat-coolify-deploy.md`
- Env template: `deploy/internal-uat/coolify/kqag.uat.env.example`
- Volume map: `deploy/internal-uat/coolify/volume-map.example.md`

Recommended app settings:

- Runtime/buildpack: Python using `requirements.txt`
- Start command: `python webapp/server.py`
- Port: value supplied by `PORT`
- Healthcheck path: `/api/health`
- Instance count: `1`
- Storage posture: `KQAG_STORAGE_MODE=database` and
  `KQAG_ARTIFACT_STORAGE_MODE=database`

Before starting deploy-mode UAT, set the env values in Coolify secrets or
environment management and run:

```powershell
python scripts\verify_internal_uat_deploy_template.py
```

```powershell
python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\internal-alpha-hosted
```

Keep populated env files, real OIDC/platform values, database URLs, private
profile/pricing files, runtime data, generated quote exports, hostnames, and
server addresses out of git and PR output.
