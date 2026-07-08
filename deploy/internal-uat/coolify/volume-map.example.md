# SQAG Internal-Alpha Runtime Housekeeping Map

The current internal-alpha posture uses database quote-record storage and
object artifact storage. The runtime roots below are housekeeping surfaces for
deploy preflight, temporary work, and logs. They must not become durable
product-visible quote-session or generated-artifact storage in hosted modes.

Use host-approved non-public locations in the host secret/environment manager.
Do not commit populated paths.

| Purpose | Env var | Hosted posture |
| --- | --- | --- |
| Runtime housekeeping | `QUOTE_DATA_ROOT` | Required by deploy preflight; not a profile/pricing/session source of truth. |
| Output staging | `QUOTE_OUTPUT_ROOT` | Required by deploy preflight; generated artifacts must persist through `SQAG_ARTIFACT_STORAGE_MODE=object` and canonical `SQAG_OBJECT_STORAGE_*` env names. |
| Temporary work files | `QUOTE_TMP_ROOT` | Temporary lifecycle only. |
| Runtime logs | `QUOTE_LOG_ROOT` | Metadata-only logs only. |

Do not expose these paths as public static directories, browse them through the
app, or commit their contents to git.
