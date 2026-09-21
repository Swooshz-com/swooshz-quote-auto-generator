# Testing Plan

## Purpose

Every code change must test the features it can affect. A small edit can use focused checks, but the checks must cover the real affected surfaces instead of only proving a demo fixture still works.

## Required Impact Pass

Before finishing a change:

- Identify the affected user-facing features, APIs, scripts, data files, generated outputs, and security boundaries.
- Run or add tests for every affected feature path.
- Prefer targeted tests first, then broaden when shared behavior, prompts, parsing, pricing, persistence, export, auth, or CI behavior changes.
- If an affected feature cannot be tested locally, document the reason and the manual or CI check that must cover it.
- Do not replace a missing general test with a demo-only fixture check.

## Baseline Checks

Use the smallest relevant subset for the change, then broaden as risk increases:

```powershell
git diff --check
node --check webapp\static\app.js
python -m py_compile webapp\server.py webapp\postgres_migrations.py scripts\migrate_sqag_storage.py scripts\preflight_sqag_migrations.py scripts\generate_quote.py scripts\live_ai_basis_chat_smoke.py
python scripts\validate_local_pdf_dependency_usage.py
python -m unittest tests.test_internal_google_auth tests.test_internal_google_auth_webapp
python -m unittest discover -s tests
npm run playwright:ai-stress
npm run playwright:smoke
```

## Feature Coverage Guide

- Quote basis, `Re`, `Ask For Changes`, AI parsing, or basis UI state: follow `docs/ai-basis-chat-test-playbook.md`, run the mocked tests, and run `npm run playwright:ai-stress` when practical.
- Pricing reference import, catalog matching, currency/tax metadata, or template parsing: add/update parser and normalization tests, then run the relevant `tests.test_webapp` and `tests.test_generate_quote` cases.
- Output pricing review, manual price detection, subtotal, Excel download, or quote generation: run generator unit tests plus an app smoke test that reaches output.
- Upload intake, PDF/image handling, sample loading, browser refresh persistence, or local file state: run server tests for request validation and a rendered Playwright flow for the affected upload/persistence path.
- Frontend layout, controls, settings, privacy page, or responsive behavior: run syntax checks and a browser/Playwright visual interaction loop on the changed screen.
- Auth, permissions, OIDC, cookies, CSRF, file download boundaries, or secret
  handling: run `tests.test_internal_google_auth`,
  `tests.test_internal_google_auth_webapp`, focused Platform/session tests, and
  then the full unit and Playwright suites. Internal identity changes must cover
  strict bounded JSON parsing, bidirectional subject/email substitution,
  role/workspace changes, policy fingerprinting, restart, route denial without
  a cookie, and browser positive/negative flows. Provider behavior must use
  local synthetic adapters only.
- CI/CD, package scripts, dependency setup, or workflow files: validate the YAML/script syntax when possible, run the nearest local command, and update `docs/current-cicd-status.md`.
- PostgreSQL migration manifest, ledger, preflight, or operator command: run
  `tests.test_postgres_migration_ledger` against the isolated CI PostgreSQL
  service and require fresh apply, complete schema, exact checksums, no-op
  replay, drift refusal, concurrency serialization, read-only preflight, and
  failed-transaction rollback evidence. Never point this CI test at a provider
  or production database.

## Regression Standard

### Full-draft Responses contract

Run `python -m unittest tests.test_openai_draft_request_contract` for full-draft
request changes. This suite uses synthetic in-memory JPEG/PNG/WEBP/PDF media,
mocked transport, and a socket-level outbound-network denial backstop. No live
provider call is part of this validation.

Invalid supplied references reject the entire draft before filtering, and the
assembled Responses envelope is checked immediately before sending. Original
PDF attachments, rendered-page order/budget, catalog visuals, configured model
and reasoning, and prompt semantics remain unchanged for valid inputs. Full
drafts have one provider-send budget; unrelated routes retain their retry rules.
Draft PDF rendering does not retain local debug images.

Failure diagnostics permit only enumerated provider classifications and bounded
structural `error.param` paths supplied by the provider. Paths are never inferred
from messages. `request_shape_sha256` hashes a versioned structural projection:
field/type markers, ordered content discriminators, detail levels, and MIME
classes. Prompt text, filenames, media/base64, private identifiers/URLs, model
configuration values, credentials, headers, and raw provider bodies are excluded.
The fingerprint is not evidence of historical failure causation.

The final assembled Responses envelope enforces OpenAI's combined `input_file` limit using decoded file bytes and a deterministic decimal ceiling of 50,000,000 bytes, in addition to per-file and envelope-size validation. Contract tests cover the accepted five-PDF reproducer and below/exact/above aggregate boundaries before mocked transport. The same final boundary validates configured reasoning effort against the configured model; current `gpt-5.5` accepts only `none`, `low`, `high`, and `xhigh`.

Contract coverage includes exact Responses field sets, invalid envelopes, whole
request rejection, one-send failures, privacy canaries, and N-1/N/N+1 boundaries
for reference/catalog counts, decoded media/derived-image bytes, dimensions,
pixels, page budgets, and inbound/outbound JSON. Byte/pixel/JSON boundary tests
use scaled ceilings to exercise exact equality without oversized fixtures.
Hosted acceptance remains a separate gate requiring explicit live-operation
authority; deterministic local validation does not establish live acceptance.

A fix is not complete until the failing behavior has a regression test at the right layer:

- Unit tests for deterministic parsing, normalization, pricing, persistence serialization, and XLSX generation.
- Mocked AI tests for prompt/response contracts and malformed provider responses.
- Playwright tests for rendered controls, navigation, refresh behavior, upload flows, and visible error handling.
- CI checks for repository-wide syntax, unit, and smoke coverage.

When production code changes without a matching automated test, the final report must say which affected feature remains manually tested or untested and why.
