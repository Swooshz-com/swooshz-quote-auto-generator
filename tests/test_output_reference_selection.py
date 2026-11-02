"""Explicit Output reference selection must persist authority before export."""
import json
import math
import os
import re
import subprocess
import unittest
from tests.rendered_browser_support import owned_browser_evidence_directory
from pathlib import Path

from tests.test_webapp import ROOT, require_node


# Closed test-only metadata vocabulary; arbitrary trace strings never reach stdout.
REFERENCE_OUTAGE_WRITERS = ('saveSessionState', 'saveWorkspaceViewState', 'applyQuoteSessionSnapshot', 'purgeBrowserRecoveryState', 'clearSessionState', 'clearWorkspaceViewState', 'restoredWorkspaceViewState', 'recoverRequiredAuthentication', 'saveQuoteSessionDraftState', 'queueQuoteSessionDraftStateSave', 'setSidePanel', 'applySessionData')
REFERENCE_OUTAGE_FIELDS = ('version', 'savedAt', 'browserRecoveryScope', 'quoteSessionId', 'activeAppView', 'activeSidePanel', 'restorableOverlay', 'pricingReferenceSettingsMode', 'profileId', 'pricingReferenceId', 'pricingReferenceSource', 'lineItems', 'outputRows', 'originalOutputRows', 'pricingMatches', 'pricingIssues', 'pricingLedgerRevisions', 'basisConfirmed', 'quoteBasis', 'quoteBasisSections', 'originalAnalysisSnapshot', 'quoteDetails', 'activeJob', 'outputRevision', 'downloadFileRevision', 'pdfFileRevision', 'dashboardActiveSessionId', 'images', 'basisChat')
REFERENCE_OUTAGE_FAULTS = ('unavailable', 'empty', 'invalid', 'missing-authority-items', 'malformed-authority-items', 'missing-selected-item', 'duplicate-selected-item', 'invalid-item-price', 'changed-item-price', 'changed-item-unit', 'changed-item-description', 'changed-item-section')
REFERENCE_OUTAGE_PHASES = ("baseline", "reload-request", "dom-ready", "profiles-rejected", "observation")
REFERENCE_OUTAGE_ENUMS = {
    "stage": ("setup",) + tuple(phase + "-" + fault for phase in REFERENCE_OUTAGE_PHASES for fault in REFERENCE_OUTAGE_FAULTS),
    "kind": ("document", "write", "hashes", "snapshot", "lifecycle", "observer_error", "marker", "response"),
    "lifecycle": (None, "DOMContentLoaded", "beforeunload", "pagehide"),
    "key": (None, "quote", "workspace"), "operation": (None, "set", "remove"),
    "path": (None, "/api/session", "/api/profiles"), "otherFieldsChanged": (None, True, False),
}
REFERENCE_OUTAGE_NUMBERS = ("sequence", "document", "nodeMs", "event", "browserMs", "appLine", "beforeLength", "afterLength", "status")
REFERENCE_OUTAGE_HASHES = {
    "beforeStorageHash": r"fnv1a32-utf16-pair:[a-f0-9]{16}",
    "afterStorageHash": r"fnv1a32-utf16-pair:[a-f0-9]{16}",
    "scopeStorageHash": r"fnv1a32-utf16-pair:[a-f0-9]{16}",
    "beforeSha256": r"[a-f0-9]{64}", "afterSha256": r"[a-f0-9]{64}", "scopeSha256": r"[a-f0-9]{64}",
}
REFERENCE_OUTAGE_FLAGS = ("isBooting", "authenticated", "isPageUnloading", "isSigningOut", "authenticationRecoveryRequested", "isRecoveryScopeTransitioning", "quoteSessionRestoreBusy")


def reference_outage_metadata(value):
    expected = set(REFERENCE_OUTAGE_ENUMS) | set(REFERENCE_OUTAGE_NUMBERS) | set(REFERENCE_OUTAGE_HASHES) | {"writers", "changedFields", "flags"}
    if type(value) is not dict or set(value) != expected:
        raise ValueError("Unsafe reference outage metadata.")
    output = {}
    for key, literals in REFERENCE_OUTAGE_ENUMS.items():
        matches = [literal for literal in literals if type(value[key]) is type(literal) and value[key] == literal]
        if len(matches) != 1:
            raise ValueError("Unsafe reference outage metadata.")
        output[key] = matches[0]
    for key in REFERENCE_OUTAGE_NUMBERS:
        number = value[key]
        if number is None and key not in {"sequence", "nodeMs"}:
            output[key] = None
        elif type(number) in {int, float} and math.isfinite(number) and number >= 0:
            output[key] = number
        else:
            raise ValueError("Unsafe reference outage metadata.")
    for key, pattern in REFERENCE_OUTAGE_HASHES.items():
        text = value[key]
        if text is not None and (type(text) is not str or re.fullmatch(pattern, text) is None):
            raise ValueError("Unsafe reference outage metadata.")
        output[key] = text
    for key, vocabulary in (("writers", REFERENCE_OUTAGE_WRITERS), ("changedFields", REFERENCE_OUTAGE_FIELDS)):
        supplied = value[key]
        if type(supplied) is not list or len(supplied) > len(vocabulary):
            raise ValueError("Unsafe reference outage metadata.")
        selected = []
        for item in supplied:
            matches = [literal for literal in vocabulary if type(item) is str and item == literal]
            if len(matches) != 1:
                raise ValueError("Unsafe reference outage metadata.")
            selected.append(matches[0])
        output[key] = selected
    flags = value["flags"]
    if type(flags) is not dict or set(flags) != set(REFERENCE_OUTAGE_FLAGS) or any(item is not None and type(item) is not bool for item in flags.values()):
        raise ValueError("Unsafe reference outage metadata.")
    output["flags"] = {key: None if flags[key] is None else flags[key] is True for key in REFERENCE_OUTAGE_FLAGS}
    return output


def emit_reference_outage_trace(parent):
    schema = "sqag.reference-outage.metadata.v1"
    def status(state, reason, rows=0):
        print(json.dumps({"schema": schema, "status": state, "reason": reason, "rows": rows}), flush=True)
    count = observer_errors = 0
    try:
        runs = [path for path in parent.iterdir() if path.is_dir()]
        if len(runs) != 1:
            status("missing", "run_missing_or_ambiguous")
            return False
        trace = runs[0] / "reference-outage-writes.jsonl"
        with trace.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = reference_outage_metadata(json.loads(line))
                print(json.dumps({"schema": schema, "status": "event", "row": row}, separators=(",", ":")), flush=True)
                count += 1
                observer_errors += row["kind"] == "observer_error"
    except FileNotFoundError:
        status("missing", "trace_missing", count)
        return False
    except (OSError, UnicodeError):
        status("error", "trace_read_failed", count)
        return False
    except (ValueError, TypeError, OverflowError):
        status("error", "trace_invalid_metadata", count)
        return False
    if not count:
        status("missing", "trace_empty")
        return False
    if observer_errors:
        status("error", "observer_error", count)
        return False
    status("emitted", "closed_metadata", count)
    return True


class OutputReferenceSelectionTest(unittest.TestCase):
    def test_selection_closure_matrix(self):
        completed = subprocess.run(
            [require_node(self), "tests/output_reference_selection_harness.cjs"],
            cwd=ROOT, input="{}", text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"status": "PASS", "cases": 78, "bindingCases": 12})

    def test_rendered_duplicate_output_selection_reopen_and_protected_xlsx(self):
        if not (ROOT / "node_modules/playwright/package.json").exists():
            if os.environ.get("CI"):
                self.fail("Playwright is required for Output selection regression.")
            self.skipTest("Playwright is unavailable.")
        with owned_browser_evidence_directory() as tmp:
            try:
                completed = subprocess.run(
                    [require_node(self), "scripts/playwright-owner-uat-basis-repair.mjs",
                     "--lines=0", "--latency-ms=0", "--fresh-analysis",
                     "--currency=SGD", "--output-reference-selection"],
                    cwd=ROOT, env={**os.environ, "SQAG_OWNER_UAT_EVIDENCE_ROOT": tmp, "SQAG_DIAG_REFERENCE_OUTAGE": "1"},
                    text=True, encoding="utf-8", capture_output=True, timeout=180,
                )
            finally:
                trace_emitted = emit_reference_outage_trace(Path(tmp))
            self.assertEqual(completed.returncode, 0, (completed.stderr or completed.stdout)[-5000:])
            self.assertTrue(trace_emitted, "Reference outage diagnostic metadata is missing or unsafe; see metadata status.")
            [run] = [path for path in Path(tmp).iterdir() if path.is_dir()]
            result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "PASS")
        proof = result["outputReferenceSelection"]
        self.assertEqual(proof["status"], "PASS")
        self.assertEqual(proof["profileOutageRecoveryCases"], 12)
        self.assertEqual(proof["rows"], 2)
        self.assertEqual(proof["conditionalSelections"], 1)
        for key in ("targetGrantPersisted", "saveReopen", "profileOutageRecovery", "manualFxAndResetRetained", "originalUnchanged"):
            self.assertIs(proof[key], True)
        self.assertGreater(proof["protectedXlsxBytes"], 1000)
