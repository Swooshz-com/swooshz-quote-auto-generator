# Current CI/CD Status

Last updated: 2026-07-09

Source of truth: `.github/workflows/ci.yml`

## Active Workflow

- Workflow name: `CI`
- Triggers: pull requests, pushes to `main`, and manual `workflow_dispatch`
- Permissions: read-only repository contents
- No deployment job is configured
- This phase-planning PR adds no deployment job
- No production environment mutation is performed by CI
- CI does not require OpenAI, DeepSeek, Gemini, OIDC, deployment, or production secrets

## Active Jobs

- `Secret scan`: checks the repository with Gitleaks before validation work proceeds.
- `Dependency audit`: installs Node dependencies with `npm ci` and runs `npm audit --audit-level=high`.
- `Validate app`: runs after the security gates pass.

## Validate App Checks

- Installs Python 3.12 and Node 22.
- Installs pinned Python dependencies with `python -m pip install --only-binary=:all: -r requirements.txt`.
- Installs `pip-audit` and runs `python -m pip_audit -r requirements.txt --strict` against pinned Python dependencies.
- Installs Playwright Chromium.
- Checks JavaScript syntax for `webapp/static/app.js`, `scripts/playwright-smoke.mjs`, and `scripts/playwright-ai-basis-chat-stress.mjs`.
- Checks Python syntax for `webapp/server.py`, quote/pricing scripts, and validation guard scripts.
- Runs `python scripts/validate_local_pdf_dependency_usage.py` to keep `pypdfium2` and `Pillow` usage on the local PDF rendering path only.
- Runs `python scripts/validate_dynamic_pricing_reference_rules.py` to keep pricing-reference matching data-driven and block source-code semantic family/synonym packs.
- Runs `python scripts/scan_sensitive_fixtures.py --fail-on-review` so review-level sensitive fixture findings fail CI.
- Runs `python -m unittest discover -s tests`.
- Runs `npm run playwright:ai-stress`.
- Runs `npm run playwright:smoke`.

## Security And Secrets

- `.env.example` must contain placeholders only.
- Local `.env` files stay ignored and must not be committed.
- CI must stay free of production/customer secrets unless a future deployment design explicitly documents the new boundary and approval path.

## Not Configured

- No deployment workflow is enabled.
- CodeQL is not enabled.
- Branch protection requirements are not documented as complete in this repo yet.

## Maintenance Rule

Update this file whenever CI/CD jobs, triggers, required checks, branch protection, deployment behavior, secret requirements, or security gates change.
