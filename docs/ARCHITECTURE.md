# Quote engine architecture

## Drafting

The staff draft route and draft worker both enter through
`webapp/server.py::draft_quote_basis`. The server checks media and pricing
authority, captures the selected catalogue and provider configuration, and
prepares the prompt and media. It passes those inputs through
`OpenAIDraftRequest` to `webapp/quote_engine.py::QuoteDraftRunner`.

The runner sends a single prepared draft or coordinates observations and
synthesis. The server adapter builds and validates the existing provider
requests and admits their responses. The existing batch runner, send budget
and `webapp/ai_gateway.py` own parallel submission, spend claims and usage.
Synthesis may repair eligible missing fields once within the same send budget.
The server maps failures, applies protected-mode rules and rechecks the
catalogue snapshot before returning a draft.

The gateway option `OPENROUTER_DRAFT_IMAGE_SCHEMA_ENABLED` defaults off and
is independent of the synthesis schema setting. When enabled, the gateway
adds the strict `draft_image_observations_v1` schema only to Standard and High
Quality image batches. Each request contains one prompt and one to six
high-detail images. The gateway checks the provider envelope and bounded
JSON with nonblank observations before marking the send successful. The server
still checks one observation per source, preserves source order and labels,
and controls synthesis and quote creation. A rejected response stops drafting
with a support reference. The gateway reads up to 8 MiB for a successful
response or 128 KiB for an HTTP error, plus one byte to detect an oversized
body. Fixed-length reads must match the declared length. Chunked reads
require the final blank line after the zero chunk and any trailers, with
separate limits on framing bytes. Conflicting or unsupported framing is
rejected. An incomplete, oversized or invalidly framed body supplies no
diagnostics, usage or cost, and the gateway does not retry it. Complete
transient HTTP errors remain eligible for retry on Basis routes. For a
complete image-batch error, the gateway checks the provider's machine
fields before filtering diagnostics. Schema rejections require an explicit
schema target or a schema-specific marker, with no contradictory error
marker.

The current batch runner also supports the default-off request format.
Retire that format after both installed routes qualify for the strict schema
and the operator accepts the change.

## Pricing and output

Provider draft evidence enters catalogue matching during server-side draft
normalisation. The shared engine matches rows and calculates amounts against
the resolved catalogue. This is the entry point for a future pass that
shortlists matches against the full selected snapshot before choosing a row.
The server verifies pricing authority and grants, and owns quote versions and
publication. `scripts/generate_quote.py` independently validates the trusted
brief and produces the XLSX with the selected profile layout or quote template.

The external quotation API remains disabled. Staff access and persisted quote
data are governed by the existing server and storage boundaries.

## Request database work

Each HTTP GET or POST, and each stage of an Excel or PDF job, runs inside one
`RequestWork` (`webapp/server.py`). It owns that request's database
connections and the schema checks that passed. A block that asks for a
connection gets one that no other block holds. When the block ends cleanly the
connection is rolled back and kept for the next block with the same database
URL and session role, for at most 30 seconds; an error leaving the block, or a
failed rollback, closes it. A block opened inside another gets its own
connection, so transactions, locks and what each one sees stay as before. The
PostgreSQL role check runs once per physical connection. A passed schema check
is not repeated in the same request or stage; a failed one is. A job closes its
idle connections before the generator runs, and publication is a new stage.
Only the thread that started the request sees its `RequestWork`. Deletes,
draft and basis-chat jobs, retention, migrations, scripts and warm-up open one
connection per block as before. Nothing outlives the request: there is no
connection pool and no cache. Because a rollback does not reset session-level
PostgreSQL state, code that runs on these connections must not use any: no
`SET` without `LOCAL`, no `set_config(..., false)`, no session advisory locks
(use the `_xact_` forms) and no temporary tables. Each returned connection costs
one rollback round trip when its block left a transaction open; closing it
used not to wait. `tests/test_request_work_budget.py` holds each
route's connection, schema-check, statement and object-read counts.
