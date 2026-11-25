# Repo map

Checked at commit `23b7c25` (25 Nov 2026). The quote-module maintainers own
every part below. Update this map in the change that moves an entry point or a
source of truth. Design detail stays in [architecture](ARCHITECTURE.md).

| Part | Entry points and sources of truth |
| --- | --- |
| Web requests, saved quotes, access | `webapp/server.py`; browser code in `webapp/static/` (`app.js`, `index.html`, `styles.css`); staff password admission in `webapp/internal_password_auth.py`; profile access in `webapp/profile_access.py` |
| Quote drafting and pricing | `webapp/quote_engine.py`, `webapp/ai_gateway.py`, `webapp/ai_response_contracts.py`, `webapp/pricing_response_contracts.py`, `webapp/quote_ai_spend.py`, `webapp/image_observation_contract.py`; [gateway contract](ai-gateway.md); [pricing import](pricing-catalog-import.md) |
| Workbook output | `scripts/generate_quote.py`; layout from the profile workbook, or from a saved quote template (`scripts/quote_template.py`, [schema](quote-template-schema.md), built-in `templates/quote-template/standard.json`); bundled defaults in `templates/` |
| Storage and forensics | Postgres via `migrations/` and `webapp/postgres_migrations.py` ([runbook](postgres-migration-runbook.md)); files via `webapp/object_storage.py` (Cloudflare R2); `webapp/forensics.py` |
| Quote service admission (disabled) | `webapp/quote_api_auth.py`, `webapp/quote_context.py`, `webapp/quote_api_registry.py`; [API contract](quotation-api-v1.md), [privilege contract](runtime-privilege-contract.md) |
| Deployment | `deploy/internal`, `deploy/rehearsal`, `deploy/internal-uat`; `scripts/build_internal_image.sh`, `scripts/rehearsal-check.mjs`, `scripts/neon_branch_guard.py`, `scripts/live_rollback.py`, `scripts/rehearsal_database_url.py` (the only way a rehearsal step uses a database URL), `nixpacks.toml`; [release runbook](internal-uat-coolify-deploy.md); live runs the image pinned on branch `deployment/internal` |
| Validation | `tests/`, browser checks `scripts/playwright-*.mjs` (commands in `package.json`), [testing plan](testing-plan.md), `.github/workflows/ci.yml`, [CI status](current-cicd-status.md), `.github/plain-text.json` |
| Agent guidance and tracking | `AGENTS.md`, `docs/README.md` (doc index), `.github/toolkit-programme.json` |
