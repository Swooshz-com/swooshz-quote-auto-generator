# AI gateway contract

All maintained inference uses `webapp/ai_gateway.py` and the fixed OpenRouter
Responses endpoint. The business layer retains the existing prompts, catalogue
snapshot, PDF rendering, media order, JSON admission and pricing/output rules.
There is no direct OpenAI or DeepSeek inference transport or provider/model
fallback. Platform HTTP calls belong to the independent Platform auth flow.

## Explicit route settings

For each suffix below, supply all three server-only configuration names:
`OPENROUTER_<SUFFIX>_MODEL`, `OPENROUTER_<SUFFIX>_REASONING_EFFORT` and
`OPENROUTER_<SUFFIX>_PROVIDER_ONLY`.

| Suffix | Operation |
| --- | --- |
| `DRAFT` | Normal full image/PDF analysis |
| `DRAFT_HIGH_QUALITY` | High Quality full image/PDF analysis |
| `BASIS_LINE` | Selected-line changes |
| `BASIS_ANSWER` | Answers and other basis chat |
| `PRICING_IMPORT` | Pricing-reference import normalisation |
| `PRICING_METADATA` | Matching metadata before and after saving |

Models are explicit namespaced IDs, without automatic router or tier shortcuts.
Effort must be exactly `none`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`.
It is passed literally without trimming, changing case or remapping. A
syntactically valid pair still needs provider/account/media compatibility proof.
`PROVIDER_ONLY` is a JSON array containing exactly one reviewed endpoint slug.
Each operation can pin a different endpoint; no common downstream vendor is
assumed. All six routes must be complete. There are no source model defaults.

Also configure `OPENROUTER_API_KEY`, a 10-1800-second integer
`OPENROUTER_REQUEST_TIMEOUT_SECONDS`, and these exact privacy/routing settings:

- `OPENROUTER_REQUIRE_PARAMETERS=true`.
- `OPENROUTER_ALLOW_FALLBACKS=false`.
- `OPENROUTER_DATA_COLLECTION=deny`.
- `OPENROUTER_ZDR` must be exactly `true` or `false`; it has no default.

The gateway sends an explicit boolean `provider.zdr` for either value. Setting
`false` is an owner-approved relaxation of the zero-data-retention requirement;
data collection stays denied. The deploy template retains `true` as its default.
Missing, empty or other values fail with `provider_policy` before any request.
Live compatibility is proved only by installed qualification of the exact
model/provider/account/media/effort/privacy combination.

Configuration is captured once into an immutable operation snapshot. Metadata
batches reuse that snapshot. The credential is server-only and never enters
route serialisation or diagnostic output. Deployed routing reads process settings;
local development may read its deliberately configured dotenv source. Old
provider keys and selectors do not select a route or supply a gateway credential.

## Draft synthesis response contract

`OPENROUTER_DRAFT_SCHEMA_ENABLED` defaults off when absent, empty or `false`.
Only exact `true` opts in; other values reject configuration before sending.
The immutable gateway snapshot captures this setting. No host configuration is
changed by the source implementation. Enabling a real endpoint requires separate
owner approval and eligibility qualification; fake transports prove no endpoint
capability, Responses schema subset, model accuracy or cost saving.

When enabled, standard and High Quality final drafts, including batched synthesis,
send `text.format` with `type=json_schema`, `name=draft_synthesis_v2`, `strict=true`
and the generated schema. Page observations, basis chat, pricing import and
matching metadata retain their own existing contracts. A trusted contract identity
is checked separately from the operation and body. Missing, modified or unexpected
schemas reject before credential access or transport. Both request validators and
the synthesis size preflight include the schema. The shape fingerprint includes
only the fixed public contract structure, never catalogue or prompt values.

`webapp/ai_response_contracts.py` owns the field tree that generates the schema,
field instructions and local structural admission. Objects are recursively closed
and all properties required; optional values use null, empty arrays use `[]`.
Fresh basis confidence uses only integer `confidence_pct`. Unmeasured basis
quantity/unit, optional basis pricing keyword, item price mode/Included label and
finding identity/confidence can be null and adapt back to absence. Fresh output
cannot supply amounts, pricing authority, legacy aliases or clarification arrays.
Saved/history aliases and the other operations' parsers retain compatibility.
Exact-object JSON parsing, duplicate-key rejection and local shape checks precede
the existing identity, quantity, coverage, catalogue and sealing guards. Ranges,
positive values and nonblank text are checked locally; unsupported range/pattern
schema keywords are not assumed to work at an unqualified endpoint.

An area can receive automatic catalogue pricing when its basis row has a
matching measurement statement from a source known to the server. Direct drafts
can cite text in the supplied PDF or quote notes. Batch synthesis can also cite
the earlier observation for a specific visual. The source label, basis line and
linked item must identify the same physical scope, including location and extent.
Conflicting or reused evidence stays in pricing review. An identifiable claim
can conflict with another claim even when an annotation or ambiguous amount
prevents it from supporting a rate on its own. A derived area requires
both dimensions in metres and full coverage of the same quoted scope. A comma
may group thousands in sets of three and a dot may mark the decimal point.
Ambiguous number formats stay in pricing review.
An image without corroborating measurement text can still suggest an editable
quantity for pricing review. Informational rows remain in Quote Basis and stay
out of the bill. The server signs the admitted record with the row, quantity,
unit, quote session and catalogue reference. The saved pricing ledger retains
the record and the catalogue presentation it can use. A change in location or
extent invalidates the rate and advances the row revision. Case, spacing and
trailing punctuation differences in the same scope preserve eligibility.

Existing drafts and publications remain readable. Older ledger records retain
their historical interpretation and check current and reset snapshots for
provisional measurements. Earlier structured area records without a scope
binding or complete claim comparison reopen for pricing review. Older application versions do not recognise the
measurement seal or ledger version. Finish stopping older writers before serving
the same draft data with this version.

For schema-bearing calls, HTTP 400/404/405/415/422 or closed schema parameter/code
errors terminate as `response_schema_rejected` at `provider_response_schema`.
This deliberately includes ambiguous request rejections without claiming their
cause. A successful HTTP envelope carrying a machine schema rejection is terminal
too. Provider prose never classifies errors. There is no retry, schema removal,
provider/model switch, repair or local starter fallback for these failures, even
in local mode. Staff receive the generic blocked error with a matching support
reference; diagnostics contain fixed codes, contract name and safe usage/counts.
No prompt, response, upload or upstream error prose is logged.
Other failures in an opted-in final draft also block local starter recovery;
their existing fixed failure classifications are preserved.

Local validation also rejects an endpoint that ignores enforcement. Only the
existing eligible absent-field reasons can reach the one batched-synthesis repair;
its captured route, schema and original evidence remain identical. Single-send
drafts never repair. Supplied invalid fields, extra properties, malformed JSON,
refusal and schema rejection do not qualify. Send reservation/settlement remains
per attempt, including rejected responses and repairs; unknown usage/cost remains
unknown. Other operations need their own schemas and separate rollout approval.

## PDF reference limits

Analysis accepts PDFs up to 48 MiB each, 96 pages in total across PDFs and
128 MiB across all reference files. Up to eight reference files remain allowed.
The draft and checkpoint JSON body ceiling remains about 173 MiB, including
base64 and metadata; non-PDF fields retain the 24 MiB standard budget.
Stored reference artifacts and database artifacts admit up to 48 MiB each.
The object adapter adds no smaller application byte ceiling; installed object
storage and ingress limits still require verification before rollout.

PDFs above 16 MiB or analyses above 12 PDF pages use rendered-image batches.
Each batch carries at most six images and 17 MiB of media characters.
Rendered pages remain bounded to 1 MiB each, so 96 PDF pages fit 16 batches
plus one synthesis send, within the unchanged 18-send budget.
Page-observation batches run in a fixed pool of at most four sends per analysis.
Results keep source order regardless of completion order. The first failure stops
new submissions; in-flight sends finish and settle before the lowest failing
batch number is reported. Synthesis starts only after every batch succeeds.
Provider rate limits and timeouts fail the whole analysis with the existing
batch failure reason, with zero retries. Concurrent analyses each have their own
pool; this does not impose a global provider concurrency limit.

Synthesis restates the required physical-line fields, including integer confidence
from 0 to 100. A synthesis response missing a required field can receive exactly
one repair re-ask on the same captured route, within the same 18-send budget.
The request keeps the original synthesis input and adds only missing field paths
and required-field instructions; it never includes the rejected output or reruns
page batches. Only absent fields qualify. Supplied invalid values, invalid JSON,
refusals and provider failures do not. A second failure follows the existing
blocked-result and support-reference path. Single-send analysis never repairs.
`ENABLE_SYNTHESIS_CONTRACT_REPAIR` gates this optional extra send. Batch usage
records its count and outcome; contract diagnostics contain bounded indexes and
fixed field names only, with no supplied keys, row IDs or model text.

`OPENROUTER_DRAFT_BATCH_REASONING_EFFORT` is optional. Missing or empty values
inherit the selected draft route's effort. Set it to `medium` for a separately
reviewed lower-effort page-observation run. It accepts exactly the same supported
values as route efforts, with no trimming or case conversion; invalid values
reject the captured configuration before any send. The immutable snapshot applies
to both standard and high-quality page batches only. Synthesis, the single-send
path and all other operations retain their existing route effort. The deploy
template leaves this setting empty; it does not change a host's configuration.

Mixed uploads can require more batches and still fail the existing call-budget
preflight. Original PDF input-file sends retain the 50,000,000-byte combined
limit; a 48 MiB PDF uses image batches rather than an original-file send.

Limit failures log numeric PDF page and byte totals and configured limits.
An oversized or unreadable PDF is not parsed for diagnostics: the numeric
page-count-complete flag is zero and the page total is partial. No filenames
or document contents enter these diagnostics. Staff see the applicable limit
and a support reference.

## Transport and admission

The gateway owns the exact endpoint, HTTPS transport, provider policy, bounded
body reads and response admission. It refuses redirects, wrong MIME, duplicate
JSON keys, invalid JSON, body overflow and truncated declared bodies. Requests
carry `store=false` and no model arrays, tools, plugins or provider conversation
state. Single-send analysis and pricing calls send once; batched synthesis has
only the missing-field repair described above. Basis line/answer calls retain
at most two same-route transient retries with the existing two/five-second delays.
Auth, payment, permission, unsupported input, refusal and partial output are never
replayed. Closed canonical Responses error types and native machine codes override
transient HTTP status. Unknown explicit machine categories fail without replay;
raw error messages never select retries. An HTTP error carrying partial output is
also terminal. No retry selects another model, provider, tier or parser.

Operation correlation has a scoped owner. Nested operations restore the exact enclosing operation, counters and usage on every exit, including logging failures. Missing or invalid configuration and credentials record validation at attempt zero. Actual sends retain one terminal record each; full-analysis and metadata owners keep correlation until business admission completes.

Only completed, coherent assistant text from the captured model proceeds to the
existing JSON and business validators. Error-only, failed, incomplete, refusal,
mixed or missing output fails visibly. Usage contains only supported nonnegative
integer counters with consistent totals; unavailable or malformed usage is not
invented. Diagnostic events contain safe status, counts and matching support
references, without prompts, uploads, model outputs, provider error text,
credentials, identity maps or arbitrary configured model values.

Unexpected-error server logs include up to four repository-relative `failure_site`
locations (file, function and line, innermost first), an optional innermost external
module/function in `failure_site_external`, and up to four exception class names in
`error_chain`; each entry is limited to 120 characters and the combined fields to
1,200 characters. These fields stay server-side and never contain exception
messages, arguments, locals, source lines or absolute paths; diagnostic failure
preserves the original log fields without these additions.
Publication admission failures also include a server-only `failure_reason` field containing a validated static condition name, without supplied values.

The [Responses usage guide](https://openrouter.ai/docs/api_reference/responses/basic-usage),
[reasoning guide](https://openrouter.ai/docs/api_reference/responses/reasoning),
[stateless error contract](https://openrouter.ai/docs/api_reference/responses/error-handling)
and [provider policy documentation](https://openrouter.ai/docs/guides/routing/provider-selection)
are protocol references. They do not establish account entitlement or exact
Responses media/effort/privacy equivalence for this installation.

## Remaining installed holds

Per-operation model and effort mappings are recorded for the current candidate.
Recorded mappings do not prove the effective installed settings or exact
model/provider/account/media/privacy compatibility. Approved downstream
endpoint/privacy behaviour, original-PDF-plus-image compatibility,
identity continuity and successful qualification of all six installed operations
remain acceptance holds.
Synthetic tests prove source contracts only. No credentials, live inference,
deployment or customer-record operation is authorised by these instructions.
