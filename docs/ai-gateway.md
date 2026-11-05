# AI gateway contract

All maintained inference uses `webapp/ai_gateway.py` and the fixed OpenRouter
Responses endpoint. The business layer retains the existing prompts, catalogue
snapshot, PDF rendering, media order, JSON admission and pricing/output rules.
There is no direct OpenAI or DeepSeek inference transport or provider/model
fallback. Existing local OIDC and Platform HTTP calls are independent auth flows.

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

## Transport and admission

The gateway owns the exact endpoint, HTTPS transport, provider policy, bounded
body reads and response admission. It refuses redirects, wrong MIME, duplicate
JSON keys, invalid JSON, body overflow and truncated declared bodies. Requests
carry `store=false` and no model arrays, tools, plugins or provider conversation
state. Full analysis and pricing calls send once. Basis line/answer calls retain
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
