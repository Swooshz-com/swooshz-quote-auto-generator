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
  failed-transaction rollback evidence. For the telemetry attempt evolution,
  also prove the 009-to-010 prefix transition, renamed historical CHECK
  discovery, successor constraint name and validation, row preservation, and
  refusal of missing, duplicate, unvalidated, drifted, premature, and stale
  attempt constraints on disposable PostgreSQL 17. Never point this CI test
  at a provider or production database.

- AI telemetry attempt numbering, pre-transport validation, provider fallback,
  or model privacy: run `tests.test_telemetry_producer`,
  `tests.test_openai_draft_request_contract`, and
  `tests.test_ai_basis_chat_stress`. Assert attempts count actual sends,
  request-validation events persist only the canonical zero-send tuple, and
  invalid model labels are projected before logs, summaries, fingerprints,
  and telemetry.
- Draft pricing authority binding, run-to-session forensic linkage, or live
  Settings selector refresh after a same-id pricing-reference save/delete:
  run the focused pricing-draft regressions in `tests.test_webapp`,
  `tests.test_forensics_feedback_retention`, and
  `tests.test_openai_draft_request_contract`, plus
  `node scripts/playwright-smoke.mjs --g3-pricing`. The browser mode starts a
  temporary local server and deterministic synthetic provider; it checks exact
  catalogue rebinding, unresolved custom rows, persisted quote state, safe
  forensic counts, feedback lookup, and immediate selector refresh without a
  live provider call. Also run `tests.test_generate_quote` to retain the XLSX
  boundary checks.
  Cover mixed identified/unidentified rows, duplicate IDs, and repeated text
  with differing quantities or units. Basis ownership must not transfer a
  selector to another row; alignment must recalculate trusted amounts and
  repeated normalization must preserve the result.
- Possible Match selection, AI Confirm ticking, quote-basis click responsiveness
  or Confirm Quotation Basis: run the `test_owner_uat_*` and `test_ledger_*`
  cases in `tests.test_webapp` and `npm run playwright:owner-uat-basis`. The
  rendered script uses the real server, UI, normalisation, persistence and XLSX
  path with a deterministic synthetic draft, a saved (`EXISTING`) quote, similar
  catalogue entries, a non-reference quote currency and injected request
  latency. It checks trusted catalogue authority, FX-correct quoted price and
  amount, review-only rows staying `???`, Output edits, reopen and protected
  XLSX amounts bound to their own workbook rows plus the totals block.
  Include a fresh Custom proposal with equivalent `pcs`/`nos` units and a
  saved catalogue `reference_section` different from its canonical section.
  Confirm must retain the server-admitted row and its authority by stable
  source ID through Output, save/reopen and XLSX; unidentified or unrelated
  rows still require section-compatible heuristic matching. The in-flight
  save regression uses `--fresh-analysis` to cover this without an initial reopen.
- Pricing trust is server state (the quote's `pricing_ledger`), never request
  JSON. A row keeps a catalogue price only while the server grants that exact
  row, item, reference digest and catalogue unit: an analysis match sealed by
  the server (`pricing_analysis_seal`, ingested into the ledger on the next
  save) or an operator selection recorded by `POST
  /api/quote-sessions/pricing-selections` against the row's current revision.
  Saves never grant: they bump the row revision on any change and permanently
  supersede a grant after a saved Exclude, Custom, removed row, other catalogue
  item, other pricing reference or incompatible unit. Manual and Included stay
  operator overrides; Use Reference Price restores only a live grant. The
  `test_ledger_*` cases cover forged authority (variant flip, deleted authority,
  self-built catalogue authority), replay after a saved exclusion or
  replacement, stale revisions and concurrent tabs, an interrupted commit,
  legacy quotes (persisted catalogue prices kept, `HISTORICAL` rows never
  promoted), cross-user, cross-workspace and cross-quote isolation in sqlite
  database mode, and units: a quantity is reused only when both units count the
  same thing (`pcs`/`nos`, `m`/`m length`/`m run`, `sqm`, `sets`, `lot`); `10 pcs`
  is never `10 sets`, and count, lot or length is never area. Such rows stay
  unpriced for manual review; no conversion factor is inferred. The rendered
  script expects the `lot` video row to stay `???` and exactly one
  pricing-selections request at Confirm. Fresh Possible Match candidates use the same
  measured-unit and detail compatibility checks as Confirm; incompatible candidates
  are omitted without inferring a quantity conversion. The rendered script injects
  one synthetic pre-upgrade cached candidate and checks its explicit unit refusal.
  Compatible aliases retain their saved basis quantity and use the server catalogue
  unit and price. Quantity divergence refuses before any grant; original provider
  units survive draft finalisation and saved-ledger review. For timing evidence add
  `--measure-only --cpu-throttle=4 --catalog-extra=300` (optionally
  `--cpu-profile`); it reports Event Timing input-to-paint per click and the
  time until `Preparing Output` paints.
- The analysis seal attests only rows derived from the provider's result, in
  the unit the provider measured each row in (carried internally from the first
  normalisation, before rows re-unit to their catalogue items). Request or
  profile-default fallback rows are never sealed as analysis matches. Cover this
  through `POST /api/draft` with a deterministic provider stub, not only
  `normalize_ai_draft`.
- Database/object publication promotion publishes the staged outputs but keeps
  the draft and pricing ledger of any save that landed after staging, so a
  later saved Exclude or replacement is never undone by promotion. The
  rendered script's `--inflight-save` option starts a delayed draft save that
  is still in flight at Confirm and expects the single pricing-selections
  request to succeed (no false "changed in another tab" refusal).
## Manual price and restored search contract

Output defaults to editable reference prices, read-only FX and derived quoted unit prices.
The explicit Quote-currency override action stages an empty transient replacement;
only a valid cent-exact commit changes authority. Escape cancels. Catalogue no-op
and cancellation preserve authority and revision. Invalid editors block saves,
navigation and generation before effects. Resolve tagged overrides through Reference
price before switching quote currency. Preserve line-first FX rounding (1.01 x 3
at FX1.37 gives 4.15, while displayed unit is 1.38) and runtime multiply ties.
A new manual authority records `price_currency` only when it matches that quote
currency. Its value is not multiplied by FX again. Catalog prices and legacy
manual authorities without that field retain reference-currency conversion.
The server and XLSX generator reject mismatched tags and extra catalog fields.

Run `python -m unittest tests.test_owner_uat_contract` for the cross-boundary
regressions. Its rendered G3 fixture uses the native temporary directory to keep
Windows publication paths bounded; evidence stays under `_logs/browser/`.
It checks suggestion selection after Output, a distinct catalog positive
control, forged suggestion prices, FX editing and reopening, persisted visible
Dashboard search, protected download identity, and actual XLSX row amounts.
No live provider is used. Retain existing authority, generator and commercial
regressions and require complete exact-head CI, including native output.

FXUI-R01 through R11 are bound by the owner contract suite: explicit reference and
quote denominations, catalogue no-op/cancel, immutable C17 predecessor readback,
transient staging, exact precision, previous-currency admission, unchanged runtime
line-first rounding, rendered event/sort/mobile behavior, deferred save ordering,
and authority/current-publication protection. The immutable synthetic predecessor
in `tests/fixtures/fxui-c17-predecessor.json` was produced by C17 main
`eda4dd086b8e570632321a0b8e9f595ec6903a5b`, SHA256
`1db57ff326ad072ecef74f146c5c5c1965fe5d55c1028de0ff30dcf0ea5a0d99`.
Candidate tests consume those bytes unchanged in browser and server checks.
The rendered carrier also checks invalid editors before save/job/window effects
and consumes the protected generated XLSX, its persisted authority and SHA256.

Delayed generation synchronization must refuse keyboard editor opening while busy.
A transient invalid editor appearing across the await must block actual job
dispatch; cancel and retry must dispatch exactly one job. The shared dispatch
admission also covers saved-job retries and XLSX/PDF consumers. Verify desktop
heading text fits inside cells at 1440px and 1100px, retaining mobile readability.
Accepted large decimal prices must reopen as plain decimal text in both
denominations. Enter/Tab without changes must preserve exact authority, revision,
draft key, save count and artifact identity; precision-changing input still rejects.

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

The final assembled Responses envelope enforces OpenAI's combined `input_file` limit using decoded file bytes and a deterministic decimal ceiling of 50,000,000 bytes, in addition to per-file and envelope-size validation. Contract tests cover the accepted five-PDF reproducer and below/exact/above aggregate boundaries before mocked transport. The same final boundary validates bounded syntax and exact equality to the captured full-draft model and reasoning settings. It does not keep a model registry or model-specific effort list. The requested runtime settings are `gpt-6.1-sol/high` for Normal Analyse and `gpt-6-astra/high` for High Quality; they are configuration examples, not source defaults.

Reasoning configuration is fail-closed at that final boundary. Normal Analyse reads
`OPENAI_DRAFT_MODEL` and `OPENAI_DRAFT_REASONING_EFFORT`; High Quality reads
`OPENAI_DRAFT_HIGH_QUALITY_MODEL` and
`OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT`. Each selected mode requires both
explicit settings. Missing, empty, whitespace-only or malformed values,
and assembled/configured mismatches reject with
`failure_boundary=request_validation`, `attempt_number=0`, and zero sends.
There are no source defaults or cross-mode fallbacks for full Analyse.
Model IDs admit 1-200 ASCII letters/digits and `._:/-`, starting with a letter
or digit. After the existing trim/whitespace/lowercase normalisation, effort
identifiers admit 1-64 ASCII letters/digits and `_-`, starting with a letter.
Unknown syntactically valid model IDs and efforts reach Responses unchanged.
The provider decides access, reasoning compatibility and image/PDF capability;
accepting an identifier does not promise those capabilities.
[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)
and the [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
document the requested High Quality pair. There is no automatic discovery,
provider retry, model substitution or effort downgrade for full Analyse.
High Quality aliases retain their existing mode semantics. Reader tests cover missing,
empty, whitespace, dotenv, and process-override values using mocked
dotenv access. Each operation captures one immutable mode/model/effort snapshot
for its assembled request, final validator and telemetry. A mid-prompt setting
change must not alter the captured pair, trigger a false mismatch or misreport
the sent model. A later operation must observe changed settings. Final-envelope
tests cover arbitrary/future model and effort identifiers in both modes,
malformed configured/body efforts, configured/body mismatch, invalid explicit
models, and model mismatch;
`draft_quote_basis` must propagate request validation without local starter
fallback. A six-page synthetic PDF exercises the valid High Quality
`gpt-6-astra/high` configuration through real local PDF rendering and mocked
transport, preserving the original PDF and retaining no debug images. Support
operations keep their existing route-specific defaults and validation. Run the focused oracle with
`python -m unittest tests.test_openai_draft_request_contract`.

OpenAI support-operation model readers apply route defaults only to absent or blank
values; full Analyse requires explicit per-mode settings and has no source defaults.
In deployed mode, full Analyse reads process environment values only, so missing or
blank Coolify settings cannot fall through to a local `.env`. Local development
retains `.env` support. Tests poison the dotenv reader to prove this distinction.
Full Analyse accepts operator-configured model/effort identifiers without source
registration, and ignores attempted client overrides. Exercise malformed values
through Normal and High Quality full-draft construction. Support operations keep
their existing strict route model checks: exercise `!!!` and unsupported
`gpt-6-sol` through selected-line and answer basis-chat dispatch, and OpenAI
pricing import and metadata requests. Each invalid configuration must report
`failure_boundary=request_validation`, `attempt_number=0`, and zero provider
sends. A basis-chat request-validation failure is terminal; it must not fall
through from an invalid answer route to a valid selected-line model. Provider,
runtime, and model-output fallback tests must continue to prove the existing
alternate-provider behavior. Syntactically valid configurations rejected by the
provider must make one send, return the protected sanitised error reference, and
never fall back to another model/effort. Arbitrary configuration canaries must
not survive log projection, telemetry, generation-evidence compaction or the
structural request hash. Only the fixed support-route model retains a public
OpenAI diagnostic label; configurable full-Analyse labels are redacted.

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
