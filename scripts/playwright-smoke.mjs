import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { TEST_REFERENCE_FILE_NAME, seedQuoteDraftFromTestFixture } from "./playwright-test-seeded-setup.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const args = process.argv.slice(2);

function readArg(name, fallback = "") {
  const index = args.indexOf(name);
  if (index >= 0 && args[index + 1]) return args[index + 1];
  const prefix = `${name}=`;
  const inline = args.find((arg) => arg.startsWith(prefix));
  return inline ? inline.slice(prefix.length) : fallback;
}

const options = {
  screenshots: args.includes("--screenshots") || args.includes("--screenshot"),
  headed: args.includes("--headed"),
  keepServer: args.includes("--keep-server"),
  host: readArg("--host", "127.0.0.1"),
  port: Number(readArg("--port", process.env.PLAYWRIGHT_PORT || "8765")),
};

let baseUrl = `http://${options.host}:${options.port}`;
const outputDir = path.join(root, "_logs", "browser", "playwright-smoke");
const quoteDataRoot = path.join(root, "_tmp", "playwright-quote-data");

function stableJson(value) {
  if (Array.isArray(value)) return value.map(stableJson);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableJson(value[key])]));
  }
  return value;
}

function normalizeQueuedDraftValue(value) {
  if (Array.isArray(value)) return value.map(normalizeQueuedDraftValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, normalizeQueuedDraftValue(item)]));
  }
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : value;
}

function queuedDraftStateComparable(value) {
  const comparable = value && typeof value === "object" ? structuredClone(value) : {};
  // Match the app's dedupe key and the server's safe-text projection.
  delete comparable.savedAt;
  delete comparable.activeAppView;
  delete comparable.activeSidePanel;
  if (comparable.quoteCommercialReview == null) delete comparable.quoteCommercialReview;
  const originalLineItems = comparable.originalAnalysisSnapshot?.line_items;
  if (Array.isArray(originalLineItems)) {
    for (const line of originalLineItems) {
      if (!line || typeof line !== "object") continue;
      for (const key of ["category_order", "item_order", "basis_order"]) {
        if (line[key] === "") delete line[key];
      }
    }
  }
  return stableJson(normalizeQueuedDraftValue(comparable));
}

function queuedDraftFileContentFingerprint(file) {
  const savedFingerprint = String(file?.content_fingerprint || "");
  if (savedFingerprint) return savedFingerprint;
  const dataUrl = String(file?.data_url || "");
  const separator = dataUrl.indexOf(",");
  if (separator < 0) return "";
  try {
    const header = dataUrl.slice(0, separator);
    const body = dataUrl.slice(separator + 1);
    const bytes = /;base64/i.test(header)
      ? Buffer.from(body, "base64")
      : Buffer.from(decodeURIComponent(body), "utf8");
    return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
  } catch {
    return "";
  }
}

function queuedDraftFilesComparable(value) {
  return (Array.isArray(value) ? value : []).map((file) => ({
    session_file_key: String(file?.session_file_key || ""),
    file_role: String(file?.file_role || ""),
    name: String(file?.name || ""),
    type: String(file?.type || ""),
    size: Number.isFinite(Number(file?.size)) ? Number(file.size) : 0,
    content_fingerprint: queuedDraftFileContentFingerprint(file),
  }));
}

function queuedDraftStateLeafDiffs(actual, expected, fieldPath = "", differences = []) {
  if (differences.length >= 24) return differences;
  const normalizedActual = normalizeQueuedDraftValue(actual);
  const normalizedExpected = normalizeQueuedDraftValue(expected);
  if (JSON.stringify(stableJson(normalizedActual)) === JSON.stringify(stableJson(normalizedExpected))) return differences;
  if (Array.isArray(normalizedActual) && Array.isArray(normalizedExpected) && normalizedActual.length === normalizedExpected.length) {
    for (let index = 0; index < normalizedActual.length && differences.length < 24; index += 1) {
      queuedDraftStateLeafDiffs(normalizedActual[index], normalizedExpected[index], `${fieldPath}[${index}]`, differences);
    }
    return differences;
  }
  if (
    normalizedActual && normalizedExpected
    && typeof normalizedActual === "object" && typeof normalizedExpected === "object"
    && !Array.isArray(normalizedActual) && !Array.isArray(normalizedExpected)
  ) {
    const keys = new Set([...Object.keys(normalizedActual), ...Object.keys(normalizedExpected)]);
    for (const key of keys) {
      if (differences.length >= 24) break;
      queuedDraftStateLeafDiffs(normalizedActual[key], normalizedExpected[key], fieldPath ? `${fieldPath}.${key}` : key, differences);
    }
    return differences;
  }
  differences.push({
    path: fieldPath,
    expected: (JSON.stringify(normalizedExpected) || "undefined").slice(0, 220),
    actual: (JSON.stringify(normalizedActual) || "undefined").slice(0, 220),
  });
  return differences;
}

function queuedDraftStateDiffKeys(actual, expected) {
  const actualState = queuedDraftStateComparable(actual);
  const expectedState = queuedDraftStateComparable(expected);
  const keys = new Set([...Object.keys(actualState || {}), ...Object.keys(expectedState || {})]);
  return [...keys].filter((key) => JSON.stringify(actualState?.[key]) !== JSON.stringify(expectedState?.[key]));
}

const quoteSessionOperationHeader = "x-sqag-smoke-operation-id";
const quoteSessionFixtureHeader = "x-sqag-smoke-fixture";
const quoteSessionOperationNameHeader = "x-sqag-smoke-operation";
const quoteSessionSessionHeader = "x-sqag-smoke-session-id";
const quoteSessionPersistenceClassHeader = "x-sqag-smoke-persistence-class";
const quoteSessionCorrelationHeader = "x-sqag-smoke-correlation-token";
const quoteSessionQueuedSaveHeader = "x-sqag-smoke-queued-save-id";
// One application postJson invocation may issue at most two transport attempts (the real
// 403 -> session refresh -> same-payload retry). Each attempt carries its ordinal and a
// unique transport identity so Node can hold every attempt inside one invocation.
const quoteSessionTransportAttemptHeader = "x-sqag-smoke-transport-attempt";
const quoteSessionTransportIdHeader = "x-sqag-smoke-transport-id";
const quoteSessionOperationStorageKey = "__sqag_smoke_quote_session_operation_v1";
const quoteSessionSaveResultsKey = "__sqagSmokeQuoteSessionSaveResults";

function quoteSessionPathForRequest(request) {
  try {
    return new URL(request.url()).pathname;
  } catch {
    return "";
  }
}

function quoteSessionRequestId(request, payload = null) {
  const headers = request.headers();
  const headerId = String(headers[quoteSessionSessionHeader] || "").trim();
  const payloadId = typeof payload?.session_id === "string" ? payload.session_id : "";
  const pathMatch = quoteSessionPathForRequest(request).match(/^\/api\/quote-sessions\/([^/]+)/);
  const pathId = pathMatch ? decodeURIComponent(pathMatch[1]) : "";
  return headerId || payloadId || pathId;
}

function quoteSessionValueContains(actual, expected) {
  if (Array.isArray(expected)) {
    return Array.isArray(actual)
      && actual.length === expected.length
      && expected.every((item, index) => quoteSessionValueContains(actual[index], item));
  }
  if (expected && typeof expected === "object") {
    return Boolean(actual && typeof actual === "object" && !Array.isArray(actual))
      && Object.keys(expected).every((key) => quoteSessionValueContains(actual[key], expected[key]));
  }
  return Object.is(actual, expected);
}

const relayHopByHopHeaders = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function readRelayRequestBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    request.once("end", () => resolve(Buffer.concat(chunks)));
    request.once("error", reject);
    request.once("aborted", () => reject(new Error("relay client request aborted before upstream dispatch")));
  });
}

function relayForwardHeaders(request, upstreamUrl) {
  const headers = {};
  for (const [name, value] of Object.entries(request.headers || {})) {
    const lowerName = name.toLowerCase();
    if (relayHopByHopHeaders.has(lowerName) || lowerName === "content-length") continue;
    headers[name] = value;
  }
  const upstreamOrigin = new URL(upstreamUrl).origin;
  const upstreamHost = new URL(upstreamUrl).host;
  if (request.headers?.host) headers.host = upstreamHost;
  if (request.headers?.origin) headers.origin = upstreamOrigin;
  if (request.headers?.referer) {
    try {
      const referer = new URL(request.headers.referer);
      headers.referer = `${upstreamOrigin}${referer.pathname}${referer.search}${referer.hash}`;
    } catch {
      delete headers.referer;
    }
  }
  return headers;
}

function relayResponseHeaders(response, body) {
  const headers = {};
  for (const [name, value] of response.headers) {
    const lowerName = name.toLowerCase();
    if (relayHopByHopHeaders.has(lowerName) || lowerName === "content-length" || lowerName === "content-encoding") continue;
    headers[name] = value;
  }
  if (typeof response.headers.getSetCookie === "function") {
    const setCookies = response.headers.getSetCookie();
    if (setCookies.length) headers["set-cookie"] = setCookies;
  }
  headers["content-length"] = String(body.length);
  return headers;
}

async function createSqag212LoopbackRelay(upstreamUrl) {
  const registrations = new Map();
  const server = createServer((request, response) => {
    void (async () => {
      const requestUrl = new URL(request.url || "/", "http://sqag212-relay.invalid");
      const correlationToken = String(request.headers[quoteSessionCorrelationHeader] || "");
      const registration = correlationToken ? registrations.get(correlationToken) : null;
      if (registration) {
        const expected = registration.expected;
        const actual = {
          method: String(request.method || "").toUpperCase(),
          path: requestUrl.pathname,
          operationId: String(request.headers[quoteSessionOperationHeader] || ""),
          sessionId: String(request.headers[quoteSessionSessionHeader] || ""),
          correlationToken,
        };
        if (
          actual.method !== expected.method
          || actual.path !== expected.path
          || actual.operationId !== expected.operationId
          || actual.sessionId !== expected.sessionId
          || actual.correlationToken !== expected.correlationToken
        ) {
          const error = new Error(`Relay request identity mismatch: ${JSON.stringify({ expected, actual })}`);
          registration.failure = error;
          registration.readyReject(error);
          response.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
          response.end("relay request identity mismatch");
          return;
        }
        const transaction = {
          registration,
          relayRequest: request,
          relayResponse: response,
          applicationRequest: {
            method: actual.method,
            path: actual.path,
            operationId: actual.operationId,
            sessionId: actual.sessionId,
            correlationToken: actual.correlationToken,
          },
          clientCloseObserved: false,
          clientCloseAt: 0,
          clientCloseResolve: registration.clientCloseResolve,
          clientClosePromise: registration.clientClose,
          hold: registration.hold,
          responseCompleted: false,
        };
        registration.transaction = transaction;
        const markClientClose = () => {
          if (transaction.responseCompleted || transaction.clientCloseObserved) return;
          transaction.clientCloseObserved = true;
          transaction.clientCloseAt = Date.now();
          transaction.clientCloseResolve(transaction);
        };
        request.once("aborted", markClientClose);
        response.once("close", markClientClose);
        try {
          const body = await readRelayRequestBody(request);
          const target = new URL(`${requestUrl.pathname}${requestUrl.search}`, upstreamUrl);
          const upstreamResponse = await fetch(target, {
            method: actual.method,
            headers: relayForwardHeaders(request, upstreamUrl),
            body: ["GET", "HEAD"].includes(actual.method) ? undefined : body,
          });
          const upstreamBody = Buffer.from(await upstreamResponse.arrayBuffer());
          transaction.upstreamResponse = {
            status: upstreamResponse.status,
            headers: Object.fromEntries(upstreamResponse.headers.entries()),
            body: upstreamBody,
            bodyText: upstreamBody.toString("utf8"),
            bodyBytes: upstreamBody,
          };
          if (registration.validateUpstream) {
            await registration.validateUpstream(transaction.upstreamResponse, transaction);
          }
          registration.readyResolve(transaction);
          if (registration.hold) {
            await Promise.race([transaction.clientClosePromise, registration.release]);
          }
          if (transaction.clientCloseObserved) return;
          response.writeHead(
            transaction.upstreamResponse.status,
            relayResponseHeaders(upstreamResponse, upstreamBody),
          );
          transaction.responseCompleted = true;
          response.end(upstreamBody);
        } catch (error) {
          registration.failure = registration.failure || error;
          registration.readyReject(error);
          if (!response.writableEnded && !transaction.clientCloseObserved) {
            response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
            response.end("relay upstream failure");
          }
        }
        return;
      }

      try {
        const body = await readRelayRequestBody(request);
        const target = new URL(`${requestUrl.pathname}${requestUrl.search}`, upstreamUrl);
        const upstreamResponse = await fetch(target, {
          method: String(request.method || "GET").toUpperCase(),
          headers: relayForwardHeaders(request, upstreamUrl),
          body: ["GET", "HEAD"].includes(String(request.method || "GET").toUpperCase()) ? undefined : body,
        });
        const upstreamBody = Buffer.from(await upstreamResponse.arrayBuffer());
        response.writeHead(upstreamResponse.status, relayResponseHeaders(upstreamResponse, upstreamBody));
        response.end(upstreamBody);
      } catch (error) {
        if (!response.writableEnded) {
          response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
          response.end("relay upstream failure");
        }
      }
    })();
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("SQAG #212 relay did not expose a loopback address.");
  }

  const register = ({ method, pathName, operationId, sessionId, correlationToken, validateUpstream, hold = true }) => {
    if (!correlationToken || registrations.has(correlationToken)) {
      throw new Error("SQAG #212 relay registration requires a unique correlation token.");
    }
    let readyResolve;
    let readyReject;
    let clientCloseResolve;
    let releaseResolve;
    const registration = {
      expected: {
        method,
        path: pathName,
        operationId,
        sessionId,
        correlationToken,
      },
      hold,
      validateUpstream,
      ready: new Promise((resolve, reject) => {
        readyResolve = resolve;
        readyReject = reject;
      }),
      clientClose: new Promise((resolve) => { clientCloseResolve = resolve; }),
      release: new Promise((resolve) => { releaseResolve = resolve; }),
      readyResolve,
      readyReject,
      clientCloseResolve,
      releaseResolve,
      released: false,
      readyResolved: false,
      browserRequest: null,
      browserRequestBoundAt: 0,
      transaction: null,
      failure: null,
    };
    registrations.set(correlationToken, registration);
    return {
      registration,
      ready: registration.ready,
      clientClose: registration.clientClose,
      release: (value = "release") => {
        registration.released = true;
        registration.releaseResolve(value);
      },
      unregister: () => registrations.delete(correlationToken),
    };
  };

  const bindBrowserRequest = (request) => {
    const correlationToken = String(request.headers()[quoteSessionCorrelationHeader] || "");
    if (!correlationToken) return;
    const registration = registrations.get(correlationToken);
    if (!registration) return;
    registration.browserRequest = request;
    registration.browserRequestBoundAt = Date.now();
  };

  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    register,
    bindBrowserRequest,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

function quoteSessionExpectedFields(payload) {
  const draft = payload?.draft_state && typeof payload.draft_state === "object" ? payload.draft_state : {};
  const draftFields = {};
  for (const key of ["workflowStage", "basisConfirmed", "outputRevision", "quoteCommercialLifecycle", "quoteCommercialReview", "outputRows"]) {
    if (Object.prototype.hasOwnProperty.call(draft, key) && draft[key] != null) draftFields[key] = draft[key];
  }
  return {
    ...(payload?.customer_summary && typeof payload.customer_summary === "object"
      ? { customer_summary: payload.customer_summary } : {}),
    ...(payload?.quote_company_profile && typeof payload.quote_company_profile === "object"
      ? { quote_company_profile: payload.quote_company_profile } : {}),
    ...(payload?.pricing_reference && typeof payload.pricing_reference === "object"
      ? { pricing_reference: payload.pricing_reference } : {}),
    ...(Object.keys(draftFields).length ? { draft_state: draftFields } : {}),
  };
}

function quoteSessionActualFields(session) {
  const value = session && typeof session === "object" ? session : {};
  const draft = value.draft_state && typeof value.draft_state === "object" ? value.draft_state : {};
  const draftFields = {};
  for (const key of ["workflowStage", "basisConfirmed", "outputRevision", "quoteCommercialLifecycle", "quoteCommercialReview", "outputRows"]) {
    if (Object.prototype.hasOwnProperty.call(draft, key)) draftFields[key] = draft[key];
  }
  return {
    ...(value.customer_summary && typeof value.customer_summary === "object"
      ? { customer_summary: value.customer_summary } : {}),
    ...(value.quote_company_profile && typeof value.quote_company_profile === "object"
      ? { quote_company_profile: value.quote_company_profile } : {}),
    ...(value.pricing_reference && typeof value.pricing_reference === "object"
      ? { pricing_reference: value.pricing_reference } : {}),
    ...(Object.keys(draftFields).length ? { draft_state: draftFields } : {}),
  };
}

// Server quote-session updated_at values are ISO timestamps with optional microseconds
// (the fraction is omitted when it is zero), so compare them numerically, not lexically.
function quoteSessionCommitClock(value) {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const seconds = Date.parse(`${match[1]}${match[3]}`);
  if (!Number.isFinite(seconds)) return null;
  return seconds * 1000 + Number(String(match[2] || "").padEnd(6, "0"));
}

function createQuoteSessionDrainTracker(group) {
  const records = [];
  const requestRecords = new WeakMap();
  const detailExpectations = new Map();
  const tasks = new Set();
  const pendingRequiredRequests = new Set();
  const pendingResponseBodies = new Set();
  const pendingReadbacks = new Set();
  const pendingSavePromises = new Set();
  const pendingRequiredJobs = new Set();
  const pendingQueuedSaveWork = new Map();
  const queuedSaveHistory = new Map();
  const attachedPages = new Set();
  const pageIdentities = new WeakMap();
  const currentDocumentByPage = new WeakMap();
  const documentEpisodes = new Map();
  const timerEventFaults = new WeakMap();
  const timerEventAckHolds = new WeakMap();
  const timerEventHandlingHolds = new WeakMap();
  const readbackHolds = new Map();
  // R2B: every successful save must prove its own durable state/files through the authenticated
  // detail boundary before any later save may explain one of its later readbacks.
  const pendingOwnProofs = new Set();
  const ownProofHolds = new Map();
  // R1B: every application invocation owns its transport attempts; a queued timer identity is
  // owned by exactly one invocation. Session refreshes are observed to validate a 403 retry.
  const transportInvocations = new Map();
  const sessionRefreshes = [];
  const sessionRefreshByRequest = new WeakMap();
  let eventSequence = 0;
  const sessionCommitClocks = new Map();
  const observedTimerEventFailures = new Set();
  const issues = [];
  const progressWaiters = new Set();
  const drainWaitingWaiters = new Set();
  const finalReconciliationBarriers = [];
  let drainWaiting = false;
  let drainWaitCount = 0;
  let progressGeneration = 0;
  let operationSequence = 0;
  let pageSequence = 0;
  let drainInvocationSequence = 0;
  const drainReports = [];
  const activeDrainRuns = new Map();
  const timerEventBinding = `__sqagSmokeTimerEvent_${String(group).replace(/[^A-Za-z0-9_]/g, "_")}_${Date.now()}_${Math.random().toString(36).slice(2)}`;

  const notifyProgress = () => {
    progressGeneration += 1;
    for (const waiter of [...progressWaiters]) waiter.resolve(progressGeneration);
    progressWaiters.clear();
  };

  const addIssue = (record, reason) => {
    issues.push({
      operationId: record?.operationId || "<unassigned>",
      fixture: record?.fixture || group,
      operation: record?.operation || "quote-session-drain",
      reason,
    });
    notifyProgress();
  };

  const createProducerLease = (kind, state = "reserved") => {
    let resolve;
    const lease = {
      kind,
      state,
      executable: state === "started",
      settled: false,
      promise: new Promise((resolvePromise) => { resolve = resolvePromise; }),
      resolve: (outcome) => {
        if (lease.settled) return;
        lease.settled = true;
        resolve(outcome);
      },
    };
    return lease;
  };

  const setQueuedSaveProducer = (entry, producer, kind) => {
    if (!entry) return;
    const previous = entry.producerLease;
    const lease = producer && typeof producer === "object" && producer.promise
      ? producer : null;
    if (previous && previous !== lease) previous.resolve({ status: "transferred", to: kind });
    entry.producerLease = lease;
    entry.producer = lease ? lease.promise : (producer || null);
    entry.producerKind = String(kind || lease?.kind || "");
    entry.producerState = lease?.state || (producer ? "started" : "none");
    if (entry.producerKind) entry.lastProducerKind = entry.producerKind;
    entry.transitions?.push({ type: "producer-registered", at: Date.now(), producer: entry.producerKind });
  };

  const releaseQueuedSaveProducer = (entry, outcome) => {
    entry.producerLease?.resolve(outcome);
    entry.producerLease = null;
    entry.producer = null;
    entry.producerKind = "";
    entry.producerState = "terminal";
  };

  const hasExecutableOwnedFlush = (entry) => {
    const state = String(entry?.flushOperation?.state || "");
    const saveStarted = entry?.applicationSaveLease?.state === "started"
      && entry.applicationSaveLease.executable === true
      && entry.applicationSaveLease.settled === false;
    const requestInFlight = Boolean(entry?.persistenceRecord?.terminalPromise)
      && entry.persistenceRecord.done !== true;
    return state === "FLUSH_OPERATION_STARTED"
      && (saveStarted || requestInFlight);
  };

  const trackRequiredJob = (promise, identity) => {
    const job = {
      identity: String(identity || `${group}/required-job-${Date.now()}-${operationSequence + 1}`),
      promise,
      producer: promise,
    };
    pendingRequiredJobs.add(job);
    if (!promise) {
      notifyProgress();
      return job;
    }
    Promise.resolve(promise).catch((error) => {
      addIssue({ operationId: job.identity, fixture: group, operation: "required-job" }, `required job failed: ${error?.message || error}`);
    }).finally(() => {
      pendingRequiredJobs.delete(job);
      notifyProgress();
    });
    return job;
  };

  const settleQueuedSave = (entry, status, reason = "") => {
    if (!entry || entry.status !== "pending") return;
    // A duplicate/replay/wrong-generation claim is sticky: no later correlation, successor,
    // proof, or cleanup may turn this queued save into a success.
    const sticky = entry.ownership?.sticky || [];
    if (sticky.length && status !== "superseded") {
      reason = `sticky request-ownership violation retained (${sticky.map((violation) => violation.verdict).join(", ")}); ${reason}`;
      if (status === "succeeded") status = "failed";
    }
    entry.status = status;
    entry.terminalReason = reason;
    entry.completedAt = Date.now();
    entry.transitions.push({ type: status, at: entry.completedAt, reason });
    if (entry.flushOperation && entry.flushOperation.state !== "FLUSH_OWNERSHIP_ABANDONED") {
      entry.flushOperation.state = "FLUSH_OPERATION_TERMINAL";
      entry.flushOperation.terminalStatus = status;
      entry.flushOperation.terminalAt = entry.completedAt;
    }
    pendingQueuedSaveWork.delete(String(entry.snapshot.timerIdentity));
    updateDocumentWork(entry, status, { reason: String(reason || "") });
    if (status === "failed") {
      addIssue({ operationId: entry.identity, fixture: group, operation: "queued-save" }, reason || "queued quote-session save failed without persistence proof");
    }
    releaseQueuedSaveProducer(entry, { status, reason });
    entry.terminalResolve?.({ status, reason });
    notifyProgress();
  };

  const registerQueuedSaveWork = (snapshot, supersedes = "") => {
    const timerIdentity = String(snapshot?.timerIdentity || "");
    if (!timerIdentity) {
      addIssue({ operationId: "<queued-save>", fixture: group, operation: "queued-save" }, "pending quote-session save timer has no captured options or identity");
      return null;
    }
    let entry = queuedSaveHistory.get(timerIdentity);
    if (!entry) {
      let terminalResolve;
      const terminalPromise = new Promise((resolve) => { terminalResolve = resolve; });
      entry = {
        identity: `${group}/queued-save/${timerIdentity}`,
        snapshot: structuredClone(snapshot),
        status: "pending",
        producer: null,
        producerKind: "",
        producerLease: null,
        terminalPromise,
        terminalResolve,
        transitions: [{ type: supersedes ? "replace" : "create", at: Date.now(), supersedes: String(supersedes || "") }],
        evidence: { postObserved: false, durableReadbackObserved: false },
        // Exclusive request ownership: queued -> awaiting request -> invocation-owned request
        // -> independently proven persistence -> succeeded. History is retained after terminal
        // settlement so replays of this identity stay rejectable.
        ownership: {
          state: "awaiting-request",
          invocationKey: "",
          ownerRecord: null,
          currentAttempt: null,
          claims: [],
          sticky: [],
        },
      };
      queuedSaveHistory.set(timerIdentity, entry);
      pendingQueuedSaveWork.set(timerIdentity, entry);
      if (supersedes) {
        const previous = queuedSaveHistory.get(String(supersedes));
        const previousInvoked = Boolean(previous && (
          previous.dispatchedBy || previous.saveInvocationSnapshot || previous.ownership?.ownerRecord || previous.persistenceRecord
        ));
        if (previous?.status === "pending" && previousInvoked) {
          // Queue replacement cannot retire a save that was already invoked and awaits proof.
          previous.successorIdentity = timerIdentity;
          previous.transitions.push({ type: "successor-created-while-invoked", at: Date.now(), successor: timerIdentity });
        } else if (previous?.status === "pending") {
          previous.successorIdentity = timerIdentity;
          previous.transitions.push({ type: "superseded", at: Date.now(), successor: timerIdentity });
          settleQueuedSave(previous, "superseded", "replaced by a newer queued draft save");
        } else if (previous) {
          previous.successorIdentity = timerIdentity;
          previous.transitions.push({ type: "successor-created", at: Date.now(), successor: timerIdentity });
        }
      }
      notifyProgress();
    } else if (entry.status === "pending") {
      entry.snapshot = structuredClone(snapshot);
    }
    if (
      !snapshot?.options
      || typeof snapshot.options !== "object"
      || typeof snapshot.options.quoteGenerated !== "boolean"
    ) {
      if (entry.status === "pending") {
        settleQueuedSave(entry, "failed", "pending quote-session save timer was captured without required options");
      }
      return entry;
    }
    if (snapshot.active === true && entry.status === "pending" && !entry.producer) {
      const nativeTimerLease = createProducerLease("native-timer", "started");
      entry.nativeTimerLease = nativeTimerLease;
      setQueuedSaveProducer(entry, nativeTimerLease, "native-timer");
      notifyProgress();
    }
    updateDocumentWork(entry, entry.status, { nativeTimerActive: snapshot.active === true });
    return entry;
  };

  const attachQueuedSaveProducer = (entry, producer, record = null) => {
    if (!entry || entry.status !== "pending") return;
    if (record) {
      entry.persistenceRecord = record;
      entry.evidence.postObserved = true;
      entry.applicationSaveProducer = record.terminalPromise;
      entry.applicationSaveLease?.resolve({ status: "transferred", to: "request-response-body-durable-readback" });
      entry.applicationSaveLease = null;
      const nativeTimerStillOwns = entry.snapshot?.active === true
        && entry.nativeTimerLease
        && entry.nativeTimerLease.settled === false;
      if (!nativeTimerStillOwns) {
        setQueuedSaveProducer(entry, record.terminalPromise, "request-response-body-durable-readback");
      }
    } else if (producer) {
      entry.applicationSaveProducer = producer;
      setQueuedSaveProducer(entry, producer, "application-save");
    }
    notifyProgress();
  };

  const createProgressWaiter = () => {
    let resolveWaiter;
    const promise = new Promise((resolve) => { resolveWaiter = resolve; });
    const waiter = {
      promise,
      resolve: (generation) => resolveWaiter({ type: "progress", generation }),
      cancel: () => {
        progressWaiters.delete(waiter);
      },
    };
    progressWaiters.add(waiter);
    return waiter;
  };

  const waitForDrainWaiting = (timeoutMs = 5000, afterCount = 0) => {
    if (drainWaiting && drainWaitCount > afterCount) return Promise.resolve({ waiting: true, count: drainWaitCount });
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject, afterCount, timer: null };
      waiter.timer = setTimeout(() => {
        drainWaitingWaiters.delete(waiter);
        reject(new Error("Required drain did not reach its deferred waiting state."));
      }, Math.max(0, timeoutMs));
      drainWaitingWaiters.add(waiter);
    });
  };

  const notifyDrainWaiting = () => {
    drainWaiting = true;
    drainWaitCount += 1;
    for (const waiter of [...drainWaitingWaiters]) {
      if (drainWaitCount <= waiter.afterCount) continue;
      clearTimeout(waiter.timer);
      drainWaitingWaiters.delete(waiter);
      waiter.resolve({ waiting: true, count: drainWaitCount });
    }
  };

  const readPayload = (request) => {
    try {
      const value = request.postDataJSON();
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  };

  const readClientResult = async (request, operationId) => {
    let page = null;
    try {
      page = request.frame()?.page();
    } catch {
      page = null;
    }
    if (!page || page.isClosed()) return null;
    try {
      await page.waitForFunction(({ key, id }) => (
        Array.isArray(window[key]) && window[key].some((item) => item?.operationId === id)
      ), { key: quoteSessionSaveResultsKey, id: operationId }, { timeout: 10000 });
      return await page.evaluate(({ key, id }) => {
        const item = (window[key] || []).findLast((candidate) => candidate?.operationId === id);
        return item ? {
          nonNull: item.nonNull === true,
          sessionId: String(item.sessionId || ""),
          quoteGenerated: typeof item.quoteGenerated === "boolean" ? item.quoteGenerated : null,
        } : null;
      }, { key: quoteSessionSaveResultsKey, id: operationId });
    } catch {
      return null;
    }
  };

  const advanceSessionCommitClock = (sessionId, updatedAt) => {
    const clock = quoteSessionCommitClock(updatedAt);
    if (!sessionId || clock === null) return;
    const current = sessionCommitClocks.get(sessionId);
    if (current === undefined || clock > current) sessionCommitClocks.set(sessionId, clock);
  };

  const readbackMismatches = (readback, record) => {
    const failures = [];
    if (readback.httpStatus !== 200 || readback.sessionId !== record.expectedSessionId) {
      failures.push("independent durable quote-session readback did not return the exact saved session");
    }
    if (!quoteSessionValueContains(readback.fields, record.expectedFields)) {
      failures.push("independent durable readback lost expected saved fields");
    }
    if (record.kind !== "detail" && readback.quoteGenerated !== record.expectedQuoteGenerated) {
      failures.push("independent durable readback had the wrong quote_generated state");
    }
    if (record.kind !== "detail" && record.queuedSaveIdentity && record.payloadDraftState
      && JSON.stringify(queuedDraftStateComparable(readback.draftState)) !== JSON.stringify(queuedDraftStateComparable(record.payloadDraftState))) {
      failures.push("independent durable readback did not match the queued save request draft state");
    }
    return failures;
  };

  const requestOwnershipRejected = (record) => String(record?.claim?.verdict || "").startsWith("rejected")
    || String(record?.correlationState || "").startsWith("rejected");

  // R2B: reasons a save cannot serve as independently proven persistence. HTTP 200, saved=true,
  // updated_at/commit-clock advancement, a null prior clock, the session id, and any other
  // save's state/files/success are never proof; only this save's own accepted detail proof is.
  const persistenceProofIneligibility = (record) => {
    const reasons = [];
    if (record?.ownProof?.state !== "accepted") {
      reasons.push(`own authenticated state/file persistence proof was not accepted (${record?.ownProof?.state || "absent"}: ${record?.ownProof?.reason || ""})`);
    } else if (record.ownProof.consequential !== true) {
      reasons.push("own proof did not establish complete consequential draft-state/file equality");
    }
    if (requestOwnershipRejected(record)) reasons.push("request-ownership claim was rejected");
    if (record?.transportVerdict && record.transportVerdict.accepted !== true) reasons.push("transport attempt was not a validated invocation attempt");
    const entry = record?.queuedEntry;
    if (entry) {
      if (entry.ownership.currentAttempt !== record) reasons.push("not the queued save's current owned attempt");
      if (entry.ownership.sticky.length) reasons.push("queued save carries a sticky request-ownership violation");
      if (record.correlationState !== "correlated-by-save-invocation-snapshot") reasons.push("invocation correlation was not accepted");
    }
    if (issues.some((issue) => issue.operationId === record?.operationId)) reasons.push("save carries a sticky tracker issue");
    return reasons;
  };

  // R2: an older save A's later readback may legitimately observe a newer same-session save B.
  // R2B: that is accepted only when A has already proven itself (accepted own detail proof of its
  // exact state and files, no sticky failure), B was admitted only after that proof, B passes its
  // own correlation and own proof independently, and A's readback is exactly B's proven
  // representation. Commit clocks are ordering evidence only. Nothing about B launders A.
  const findValidatedReadbackSuccessor = async (record, readback, laterRecords) => {
    const ownIneligibility = persistenceProofIneligibility(record);
    const ownProofClock = quoteSessionCommitClock(record.ownProof?.readback?.updatedAt);
    const readbackClock = quoteSessionCommitClock(readback.updatedAt);
    if (ownIneligibility.length) {
      return {
        successor: null,
        reason: "older save's own persistence proof was not accepted: " + ownIneligibility.join("; "),
        ownProofState: record.ownProof?.state || "absent",
        priorCommitClock: record.priorCommitClock,
        readbackClock,
      };
    }
    const rejected = [];
    for (const candidate of [...laterRecords].reverse()) {
      if (candidate.kind || candidate.method !== "POST" || candidate.persistenceClass === "DIAGNOSTIC_ONLY") continue;
      if (candidate === record || candidate.expectedSessionId !== record.expectedSessionId || !candidate.payloadDraftState) continue;
      if (readback.quoteGenerated !== candidate.expectedQuoteGenerated
        || !quoteSessionValueContains(readback.fields, candidate.expectedFields)
        || JSON.stringify(queuedDraftStateComparable(readback.draftState)) !== JSON.stringify(queuedDraftStateComparable(candidate.payloadDraftState))) continue;
      if (!(candidate.requestSeq > record.ownProof.acceptedSeq)) {
        rejected.push({ operationId: candidate.operationId, reason: "admitted before the older save's own proof was accepted" });
        continue;
      }
      await candidate.terminalPromise;
      const candidateIneligibility = persistenceProofIneligibility(candidate);
      const proven = candidate.ownProof?.readback || null;
      const candidateProofClock = quoteSessionCommitClock(proven?.updatedAt);
      const checks = {
        successorIndependentlyProven: candidateIneligibility.length === 0,
        successorQueuedSaveSucceeded: !candidate.queuedEntry || candidate.queuedEntry.status === "succeeded",
        successorReadbackNotFailed: candidate.readbackVerdict !== "failed",
        readbackIsSuccessorProvenState: Boolean(proven)
          && JSON.stringify(queuedDraftStateComparable(readback.draftState)) === JSON.stringify(queuedDraftStateComparable(proven.draftState)),
        readbackIsSuccessorProvenFiles: Boolean(proven) && Array.isArray(readback.draftFiles) && Array.isArray(proven.draftFiles)
          && JSON.stringify(queuedDraftFilesComparable(readback.draftFiles)) === JSON.stringify(queuedDraftFilesComparable(proven.draftFiles)),
        readbackIsSuccessorProvenStatus: Boolean(proven) && readback.quoteGenerated === proven.quoteGenerated,
        readbackIsSuccessorProvenCommit: Boolean(proven) && readback.updatedAt === proven.updatedAt,
        // Ordering evidence only: contradictory clocks reject, absent clocks neither waive nor reject.
        successorOrderingNotContradicted: ownProofClock === null || candidateProofClock === null || candidateProofClock > ownProofClock,
      };
      if (Object.values(checks).every(Boolean)) {
        return { successor: candidate, ownClock: ownProofClock, successorClock: candidateProofClock, readbackClock };
      }
      rejected.push({ operationId: candidate.operationId, checks, candidateIneligibility });
    }
    return {
      successor: null,
      reason: "no independently proven later same-session save explains the observed readback",
      ownClock: ownProofClock,
      readbackClock,
      rejected,
    };
  };

  const holdOwnProof = (operationId) => {
    let reachedResolve;
    let releaseResolve;
    const hold = {
      operationId: String(operationId || ""),
      reached: new Promise((resolve) => { reachedResolve = resolve; }),
      gate: new Promise((resolve) => { releaseResolve = resolve; }),
      reachedResolve: (value) => reachedResolve(value),
      release: () => releaseResolve(),
    };
    ownProofHolds.set(hold.operationId, hold);
    return hold;
  };

  const createOwnProofState = (record) => {
    let resolveSettled;
    record.ownProof = { state: "pending", reason: "", consequential: false, readback: null, acceptedSeq: 0, settledSeq: 0 };
    record.ownProofSettled = new Promise((resolve) => { resolveSettled = resolve; });
    record.resolveOwnProof = (state, reason = "", detail = {}) => {
      if (record.ownProof.state !== "pending") return record.ownProof;
      Object.assign(record.ownProof, detail, { state, reason: String(reason || "") });
      record.ownProof.settledSeq = ++eventSequence;
      if (state === "accepted") record.ownProof.acceptedSeq = record.ownProof.settledSeq;
      resolveSettled(record.ownProof);
      notifyProgress();
      return record.ownProof;
    };
  };

  const detailReadbackFromBody = (httpStatus, body) => {
    const session = body?.quote_session && typeof body.quote_session === "object" ? body.quote_session : null;
    return {
      httpStatus,
      sessionId: String(session?.session_id || ""),
      quoteGenerated: typeof session?.status?.quote_generated === "boolean" ? session.status.quote_generated : null,
      draftState: session?.draft_state && typeof session.draft_state === "object" ? session.draft_state : null,
      draftFiles: Array.isArray(session?.draft_files) ? session.draft_files : null,
      fields: quoteSessionActualFields(session),
      updatedAt: String(session?.updated_at || ""),
    };
  };

  // Authenticated (the request's own browser-context session cookies), cache-bypassed read of the
  // existing detail boundary. The result reflects durable server state, never the echoed payload.
  const authenticatedDetailRead = async (record, signal) => {
    const origin = new URL(record.request.url()).origin;
    const context = record.page?.context?.() || null;
    const cookies = context ? await context.cookies(origin) : [];
    const headers = { "cache-control": "no-cache", pragma: "no-cache" };
    if (cookies.length) headers.cookie = cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
    const url = `${origin}/api/quote-sessions/${encodeURIComponent(record.expectedSessionId)}?__sqag_smoke_own_proof=${Date.now()}-${records.length}`;
    const response = await fetch(url, { cache: "no-store", headers, signal });
    const text = await response.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = null; }
    return detailReadbackFromBody(response.status, body);
  };

  const ownProofEvaluation = (record, readback) => {
    const exactFailures = [];
    if (readback.httpStatus !== 200 || readback.sessionId !== record.expectedSessionId) exactFailures.push("authenticated detail did not return the exact saved session");
    if (!quoteSessionValueContains(readback.fields, record.expectedFields)) exactFailures.push("authenticated detail lost expected saved fields");
    if (typeof record.expectedQuoteGenerated === "boolean" && readback.quoteGenerated !== record.expectedQuoteGenerated) {
      exactFailures.push("authenticated detail had the wrong quote_generated state");
    }
    const consequentialFailures = [];
    if (record.payloadDraftState && JSON.stringify(queuedDraftStateComparable(readback.draftState)) !== JSON.stringify(queuedDraftStateComparable(record.payloadDraftState))) {
      consequentialFailures.push("authenticated detail draft state did not equal this save's consequential draft state; fields="
        + queuedDraftStateDiffKeys(readback.draftState, record.payloadDraftState).join(","));
    }
    if (Array.isArray(record.payloadDraftFiles) && (!Array.isArray(readback.draftFiles)
      || JSON.stringify(queuedDraftFilesComparable(readback.draftFiles)) !== JSON.stringify(queuedDraftFilesComparable(record.payloadDraftFiles)))) {
      consequentialFailures.push("authenticated detail files did not match this save's file identity/metadata/content; expected="
        + JSON.stringify(queuedDraftFilesComparable(record.payloadDraftFiles)) + "; actual=" + JSON.stringify(queuedDraftFilesComparable(readback.draftFiles)));
    }
    return { exactFailures, consequentialFailures };
  };

  // A validated 403 retry must be backed by the real session refresh it observed: the refresh
  // returned a token, the retry carried exactly that token, and the first attempt did not.
  // Only digests are compared; no credential value is retained or reported.
  const verifyRetryRefreshEvidence = async (record) => {
    const previous = record.retryOf;
    const refresh = record.retryRefresh;
    if (!previous || !refresh) return { passed: false, reason: "retry has no observed first attempt or refresh" };
    const [refreshBody] = await Promise.all([refresh.bodyEvidence, previous.bodyObservedPromise]);
    const header = String(refreshBody?.csrfHeader || "").toLowerCase();
    const checks = {
      refreshReturnedSessionToken: refreshBody?.ok === true && Boolean(header) && Boolean(refreshBody.tokenDigest) && refreshBody.recoveryScopePresent === true,
      retryCarriedRefreshedToken: Boolean(header) && record.credentialDigests[header] === refreshBody?.tokenDigest,
      firstAttemptCarriedStaleToken: Boolean(header) && previous.credentialDigests[header] !== refreshBody?.tokenDigest,
      firstAttemptRealServerRejection: previous.httpStatus === 403 && previous.bodyStatus === "blocked",
    };
    return { passed: Object.values(checks).every(Boolean), checks };
  };

  const rejectTransportAttempt = (record, reason) => {
    record.transportVerdict = { ...(record.transportVerdict || {}), accepted: false, reason: String(reason) };
    const entry = record.queuedEntry;
    if (entry) {
      const violation = { operationId: record.operationId, transportId: record.transportId, verdict: "rejected-invalid-retry", reason: String(reason), requestSeq: record.requestSeq };
      entry.ownership.sticky.push(violation);
      entry.transitions.push({ type: "request-ownership-violation", at: Date.now(), ...violation });
    }
    addIssue(record, `queued save transport attempt rejected: ${reason}`);
  };

  const proveOwnPersistence = async (record) => {
    if (record.ownProof.state !== "pending") return record.ownProof;
    if (requestOwnershipRejected(record)) return record.resolveOwnProof("ineligible", "rejected request-ownership claim cannot mint persistence proof");
    if (record.transportVerdict && record.transportVerdict.accepted !== true) {
      return record.resolveOwnProof("ineligible", "unvalidated transport attempt cannot mint persistence proof");
    }
    if (record.transportFailure || record.httpStatus < 200 || record.httpStatus >= 300 || record.bodyStatus !== "saved"
      || record.responseSessionId !== record.expectedSessionId) {
      return record.resolveOwnProof("ineligible", `HTTP ${record.httpStatus} response was not a successful exact saved result and provides no persistence proof`);
    }
    const abort = new AbortController();
    record.ownProofAbort = abort;
    const aborted = new Promise((resolve) => abort.signal.addEventListener("abort", resolve, { once: true }));
    pendingOwnProofs.add(record);
    const proof = (async () => {
      const hold = ownProofHolds.get(record.operationId);
      if (hold) {
        ownProofHolds.delete(record.operationId);
        hold.reachedResolve({ operationId: record.operationId });
        await Promise.race([hold.gate, aborted]);
      }
      if (abort.signal.aborted) throw new Error("own-proof aborted");
      if (record.transportVerdict?.role === "retry") {
        const evidence = await Promise.race([verifyRetryRefreshEvidence(record), aborted.then(() => null)]);
        if (abort.signal.aborted) throw new Error("own-proof aborted");
        record.retryEvidence = { ...(record.retryEvidence || {}), body: evidence };
        if (!evidence.passed) {
          rejectTransportAttempt(record, "403 retry was not backed by the real refreshed session: " + JSON.stringify(evidence.checks || evidence.reason));
          return record.resolveOwnProof("ineligible", "unvalidated retry cannot mint persistence proof");
        }
      }
      const readback = await authenticatedDetailRead(record, AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]));
      const { exactFailures, consequentialFailures } = ownProofEvaluation(record, readback);
      // Saves bound to a queued identity must prove complete consequential state/files; other
      // required saves keep their existing proof floor but record whether they could be a successor.
      const failures = record.queuedSaveIdentity ? [...exactFailures, ...consequentialFailures] : exactFailures;
      const detail = { readback, consequential: exactFailures.length === 0 && consequentialFailures.length === 0, consequentialFailures };
      if (failures.length) {
        record.resolveOwnProof("failed", failures.join("; "), detail);
        addIssue(record, `own persistence proof failed: ${failures.join("; ")}`);
      } else {
        record.resolveOwnProof("accepted", "exact authenticated durable state/file proof", detail);
      }
      return record.ownProof;
    })();
    record.ownProofProducer = proof;
    try {
      return await proof;
    } catch (error) {
      if (abort.signal.aborted) {
        record.resolveOwnProof("aborted", record.ownProofAbortReason || "own-proof wait aborted");
        addIssue(record, `own persistence proof aborted: ${record.ownProofAbortReason || "page lifecycle"}`);
      } else {
        record.resolveOwnProof("failed", `authenticated detail proof failed: ${error?.message || error}`);
        addIssue(record, `own persistence proof failed: ${error?.message || error}`);
      }
      return record.ownProof;
    } finally {
      pendingOwnProofs.delete(record);
      notifyProgress();
    }
  };

  // A proof wait bound to a queued save must not outlive its page/document: abort it so the
  // save fails with the lifecycle loss instead of leaving an orphaned proof gate.
  const abortOwnProofsForPage = (page, reason) => {
    for (const record of [...pendingOwnProofs]) {
      if (record.page !== page || !record.queuedSaveIdentity) continue;
      record.ownProofAbortReason = String(reason || "page lifecycle ended");
      record.ownProofAbort?.abort();
    }
  };

  const holdDurableReadback = (operationId) => {
    let reachedResolve;
    let releaseResolve;
    const hold = {
      operationId: String(operationId || ""),
      reached: new Promise((resolve) => { reachedResolve = resolve; }),
      gate: new Promise((resolve) => { releaseResolve = resolve; }),
      reachedResolve: (value) => reachedResolve(value),
      release: () => releaseResolve(),
    };
    readbackHolds.set(hold.operationId, hold);
    return hold;
  };

  const durableReadback = async (request, record) => {
    const origin = new URL(request.url()).origin;
    const sessionId = encodeURIComponent(record.expectedSessionId);
    pendingReadbacks.add(record);
    try {
      const hold = readbackHolds.get(record.operationId);
      if (hold) {
        readbackHolds.delete(record.operationId);
        hold.reachedResolve({ operationId: record.operationId });
        await hold.gate;
      }
      const url = `${origin}/api/quote-sessions/${sessionId}?__sqag_smoke_readback=${Date.now()}-${records.length}`;
      const response = await fetch(url, {
        cache: "no-store",
        headers: { "cache-control": "no-cache", pragma: "no-cache" },
        signal: AbortSignal.timeout(15000),
      });
      const text = await response.text();
      let body = null;
      try { body = JSON.parse(text); } catch { body = null; }
      const session = body?.quote_session && typeof body.quote_session === "object" ? body.quote_session : null;
      record.readback = {
        httpStatus: response.status,
        sessionId: String(session?.session_id || ""),
        quoteGenerated: typeof session?.status?.quote_generated === "boolean" ? session.status.quote_generated : null,
        draftState: session?.draft_state && typeof session.draft_state === "object" ? session.draft_state : null,
        draftFiles: Array.isArray(session?.draft_files) ? session.draft_files : null,
        fields: quoteSessionActualFields(session),
        updatedAt: String(session?.updated_at || ""),
      };
      // Only saves observed before this readback returned can explain what it read.
      const laterRecords = records.slice(records.indexOf(record) + 1);
      if (response.status === 200 && record.readback.sessionId === record.expectedSessionId) {
        advanceSessionCommitClock(record.expectedSessionId, record.readback.updatedAt);
      }
      const failures = readbackMismatches(record.readback, record);
      if (!failures.length) {
        record.readbackVerdict = "exact";
        return record.readback;
      }
      const supersession = response.status === 200 && record.readback.sessionId === record.expectedSessionId
        ? await findValidatedReadbackSuccessor(record, record.readback, laterRecords)
        : { successor: null, reason: "readback did not return the exact saved session" };
      if (supersession.successor) {
        record.readbackVerdict = "superseded";
        record.readbackSuccessor = supersession.successor;
        record.readbackSupersession = {
          successorOperationId: supersession.successor.operationId,
          ownCommitClock: supersession.ownClock,
          successorCommitClock: supersession.successorClock,
          readbackCommitClock: supersession.readbackClock,
          supersededMismatches: failures,
        };
        return record.readback;
      }
      record.readbackVerdict = "failed";
      record.readbackSupersessionRejection = { ...supersession, successor: undefined };
      for (const failure of failures) {
        addIssue(record, `${failure}; supersession rejected: ${JSON.stringify(record.readbackSupersessionRejection)}`);
      }
      return record.readback;
    } catch (error) {
      record.readbackVerdict = "failed";
      addIssue(record, `independent durable quote-session readback failed: ${error?.message || error}`);
      return null;
    } finally {
      pendingReadbacks.delete(record);
      notifyProgress();
    }
  };

  const finishResponse = async (record, response) => {
    pendingResponseBodies.add(record);
    let body = null;
    let responseText = "";
    try {
      responseText = await response.text();
      try { body = JSON.parse(responseText); } catch { body = null; }
    } finally {
      pendingResponseBodies.delete(record);
      notifyProgress();
    }
    record.httpStatus = response.status();
    record.bodyStatus = String(body?.status || "");
    const responseSession = body?.quote_session && typeof body.quote_session === "object" ? body.quote_session : null;
    record.responseSessionId = String(responseSession?.session_id || "");
    record.responseQuoteGenerated = typeof responseSession?.status?.quote_generated === "boolean"
      ? responseSession.status.quote_generated : null;
    record.responseUpdatedAt = String(responseSession?.updated_at || "");
    record.resolveBodyObserved?.();
    if (record.kind === "list") {
      pendingRequiredRequests.delete(record);
      notifyProgress();
      if (record.httpStatus !== 200) {
        addIssue(record, `required quote-session list returned HTTP ${record.httpStatus}`);
      }
      const sessions = Array.isArray(body?.quote_sessions) ? body.quote_sessions : [];
      const responseSession = sessions.find((candidate) => String(candidate?.session_id || "") === record.expectedSessionId) || null;
      record.responseSessionId = String(responseSession?.session_id || "");
      record.responseQuoteGenerated = typeof responseSession?.status?.quote_generated === "boolean"
        ? responseSession.status.quote_generated : null;
      if (!responseSession) addIssue(record, "required quote-session list did not contain the exact saved session");
      if (!quoteSessionValueContains(quoteSessionActualFields(responseSession), record.expectedFields)) {
        addIssue(record, "required quote-session list lost expected saved fields");
      }
      return;
    }
    if (record.kind === "detail") {
      pendingRequiredRequests.delete(record);
      notifyProgress();
      if (record.httpStatus !== 200) {
        addIssue(record, `required quote-session detail returned HTTP ${record.httpStatus}`);
      }
      if (!responseSession || record.responseSessionId !== record.expectedSessionId) {
        addIssue(record, "required quote-session detail did not contain the exact session id");
      }
      if (!quoteSessionValueContains(quoteSessionActualFields(responseSession), record.expectedFields)) {
        addIssue(record, "required quote-session detail lost expected saved fields");
      }
      return;
    }
    if (record.persistenceClass === "DIAGNOSTIC_ONLY") return;
    if (requestOwnershipRejected(record)) {
      // A rejected request-ownership claim (duplicate, replay, wrong generation, invalid retry)
      // can never mint persistence proof or successor evidence; its violation is already sticky.
      record.resolveOwnProof("ineligible", "rejected request-ownership claim cannot mint persistence proof");
      pendingRequiredRequests.delete(record);
      pendingSavePromises.delete(record);
      record.readbackVerdict = "ineligible-rejected-claim";
      notifyProgress();
      return;
    }
    // R2B: prove this save's own durable state/files immediately after its response, before any
    // later save can be admitted as the explanation of one of its later readbacks.
    await proveOwnPersistence(record);
    const clientResult = await readClientResult(record.request, record.operationId);
    record.clientResult = clientResult;
    pendingRequiredRequests.delete(record);
    pendingSavePromises.delete(record);
    notifyProgress();
    if (record.httpStatus === 403 && record.transportVerdict?.role === "initial" && record.transportVerdict.accepted === true) {
      // R1B: the real 403 -> refresh -> same-payload retry is one invocation with two transport
      // attempts. The first attempt proves nothing; only a validated final retry may prove.
      const retry = record.invocation?.attempts.find((attempt) => attempt !== record && attempt.transportVerdict?.role === "retry");
      if (retry) await Promise.race([retry.ownProofSettled, retry.terminalPromise]);
      if (retry?.transportVerdict?.accepted === true) {
        record.transportOutcome = "superseded-by-validated-retry";
        record.readbackVerdict = "not-applicable-retried-transport-attempt";
        return;
      }
    }
    if (record.httpStatus < 200 || record.httpStatus >= 300) addIssue(record, `required save returned HTTP ${record.httpStatus}`);
    if (record.bodyStatus !== "saved") addIssue(record, "required save response body status was not saved");
    if (!responseSession || record.responseSessionId !== record.expectedSessionId) {
      addIssue(record, "required save response did not contain the exact session id");
    }
    if (record.expectedQuoteGenerated !== record.responseQuoteGenerated) {
      addIssue(record, "required save response had the wrong quote_generated state");
    }
    if (!clientResult || clientResult.nonNull !== true || clientResult.sessionId !== record.expectedSessionId) {
      addIssue(record, "initiating application save promise did not return the exact saved session");
    }
    if (clientResult?.quoteGenerated !== record.expectedQuoteGenerated) {
      addIssue(record, "initiating application save promise returned the wrong quote_generated state");
    }
    if (record.httpStatus >= 200 && record.httpStatus < 300 && record.bodyStatus === "saved"
      && record.responseSessionId === record.expectedSessionId) {
      advanceSessionCommitClock(record.expectedSessionId, record.responseUpdatedAt);
    }
    const readbackPromise = durableReadback(record.request, record);
    record.readbackProducer = readbackPromise;
    await readbackPromise;
  };

  const queuedSaveMatchesRecord = (entry, record) => {
    const snapshot = entry?.snapshot || {};
    if (snapshot.sessionId !== record.expectedSessionId) return false;
    if (snapshot.options?.quoteGenerated !== record.expectedQuoteGenerated) return false;
    if (!snapshot.draftState || !record.payloadDraftState) return false;
    if (JSON.stringify(queuedDraftStateComparable(snapshot.draftState)) !== JSON.stringify(queuedDraftStateComparable(record.payloadDraftState))) return false;
    if (!Array.isArray(snapshot.draftFiles)) return false;
    if (record.payloadDraftFiles === null) return true;
    if (!Array.isArray(record.payloadDraftFiles)) return false;
    return JSON.stringify(queuedDraftFilesComparable(snapshot.draftFiles)) === JSON.stringify(queuedDraftFilesComparable(record.payloadDraftFiles));
  };

  // Request headers (whatever the configured session-token header is named) are retained only as
  // SHA-256 digests so retry evidence can be compared without holding or reporting any value.
  const credentialHeaderDigests = (headers) => Object.fromEntries(Object.entries(headers || {})
    .filter(([name]) => !name.toLowerCase().startsWith("x-sqag-smoke-"))
    .map(([name, value]) => [name.toLowerCase(), createHash("sha256").update(String(value || "")).digest("hex")]));

  const refreshBetween = (previous, record) => sessionRefreshes.find((refresh) => (
    refresh.pageIdentity === record.pageIdentity
    && refresh.requestSeq > previous.responseSeq
    && refresh.responseSeq > 0
    && refresh.responseSeq < record.requestSeq
    && refresh.responseStatus === 200
  )) || null;

  // R1B: one application invocation (page + operation id) owns at most two transport attempts:
  // an initial attempt and one retry after a real pre-persistence 403 and a real session refresh.
  const registerTransportAttempt = (record) => {
    const key = `${record.pageIdentity}/${record.operationId}`;
    const invocation = transportInvocations.get(key) || null;
    record.invocation = invocation;
    if (record.transportAttempt === 1) {
      if (invocation) {
        record.transportVerdict = { accepted: false, role: "duplicate-initial", reason: "invocation already issued its initial transport attempt" };
        return;
      }
      const created = { key, operationId: record.operationId, pageIdentity: record.pageIdentity, attempts: [record] };
      transportInvocations.set(key, created);
      record.invocation = created;
      record.transportVerdict = { accepted: true, role: "initial" };
      return;
    }
    if (!invocation) {
      record.transportVerdict = { accepted: false, role: "retry", reason: "retry attempt has no initial attempt in the same invocation" };
      return;
    }
    const previous = invocation.attempts[invocation.attempts.length - 1];
    const refresh = previous.responseSeq ? refreshBetween(previous, record) : null;
    const checks = {
      singleRetryAfterInitial: invocation.attempts.length === 1 && record.transportAttempt === 2,
      previousAcceptedInitial: previous.transportVerdict?.accepted === true && previous.transportVerdict.role === "initial",
      previousResponseObserved: Number(previous.responseSeq) > 0,
      previousServerRejected403: previous.responseStatus === 403,
      previousProvidedNoProof: previous.ownProof?.state !== "accepted",
      noOverlappingAttempt: Number(previous.responseSeq) > 0 && previous.responseSeq < record.requestSeq,
      samePage: previous.pageIdentity === record.pageIdentity,
      sameSession: previous.expectedSessionId === record.expectedSessionId,
      samePayload: previous.postData === record.postData,
      sameQueuedIdentity: previous.queuedSaveIdentity === record.queuedSaveIdentity,
      realSessionRefreshBetweenAttempts: Boolean(refresh),
    };
    record.retryEvidence = { checks };
    if (!Object.values(checks).every(Boolean)) {
      record.transportVerdict = {
        accepted: false,
        role: "retry",
        reason: "retry was not a single same-payload attempt after a real 403 and refresh: "
          + Object.entries(checks).filter(([, passed]) => !passed).map(([name]) => name).join(","),
      };
      return;
    }
    record.retryOf = previous;
    record.retryRefresh = refresh;
    invocation.attempts.push(record);
    record.transportVerdict = { accepted: true, role: "retry" };
  };

  // R1B: exclusive request ownership for one queued timer identity. The first real exact-identity
  // POST becomes the owned request (provisionally, until save-start supplies the save-invocation
  // snapshot). Every later claim is either that invocation's one validated retry or a sticky
  // violation; no later claim is ever compared with queue-time state, replaces the owner or its
  // producer, or completes the queued save. Terminal entries keep rejecting replays.
  const admitQueuedSaveClaim = (entry, record) => {
    const claim = { operationId: record.operationId, transportId: record.transportId, transportAttempt: record.transportAttempt, requestSeq: record.requestSeq, comparedSnapshot: "none" };
    const reject = (verdict, reason) => {
      record.correlationState = verdict;
      record.claim = { ...claim, verdict, reason };
      if (entry) {
        entry.ownership.claims.push(record.claim);
        entry.ownership.sticky.push({ operationId: record.operationId, transportId: record.transportId, verdict, reason, requestSeq: record.requestSeq });
        entry.transitions.push({ type: "request-ownership-violation", at: Date.now(), verdict, operationId: record.operationId });
      }
      addIssue(record, `queued save request ownership violation (${verdict}): ${reason}`);
      record.resolveOwnProof("ineligible", "rejected request-ownership claim cannot mint persistence proof");
      notifyProgress();
      return false;
    };
    if (!entry) return reject("rejected-unknown-identity", "claimed queued save identity is unknown");
    if (entry.status !== "pending") return reject("rejected-terminal-replay", `claimed queued save is already terminal (${entry.status})`);
    const ownership = entry.ownership;
    if (!ownership.ownerRecord) {
      if (record.transportVerdict?.accepted !== true || record.transportVerdict.role !== "initial") {
        return reject("rejected-wrong-generation", "first claim was not the initial transport attempt of a fresh invocation: " + String(record.transportVerdict?.reason || ""));
      }
      ownership.ownerRecord = record;
      ownership.currentAttempt = record;
      ownership.invocationKey = record.invocation.key;
      record.queuedEntry = entry;
      if (!entry.saveInvocationSnapshot) {
        // R1: Node can observe the POST before the save-start event carrying the authoritative
        // save-invocation snapshot. Keep this request provisionally owned and judge it against that
        // snapshot when it arrives; never against the stale queue-time snapshot.
        record.correlationState = "awaiting-save-invocation-snapshot";
        ownership.state = "provisional-owned";
        entry.correlationPendingRecord = record;
        entry.correlationEvidence = { order: "post-before-save-start", operationId: record.operationId };
        entry.transitions.push({ type: "post-observed-before-save-start", at: Date.now(), operationId: record.operationId });
      } else if (!queuedSaveMatchesRecord(entry, record)) {
        record.correlationState = "rejected";
        record.claim = { ...claim, comparedSnapshot: "save-invocation", verdict: "rejected-save-invocation-mismatch" };
        ownership.claims.push(record.claim);
        ownership.state = "rejected";
        addIssue(record, "queued save request identity did not match its captured session, state, files, and required status: "
          + JSON.stringify(queuedSaveMismatchSummary(entry, record)));
        record.resolveOwnProof("ineligible", "request did not match its save-invocation snapshot");
        settleQueuedSave(entry, "failed", "correlated POST did not match the captured queued save");
        return false;
      } else {
        record.correlationState = "correlated-by-save-invocation-snapshot";
        ownership.state = "owned";
        entry.correlationEvidence = { order: "save-start-before-post", operationId: record.operationId, correlatedAgainst: "save-invocation-snapshot" };
      }
      record.claim = { ...claim, verdict: "owner", comparedSnapshot: entry.saveInvocationSnapshot ? "save-invocation" : "deferred-save-invocation" };
      ownership.claims.push(record.claim);
      attachQueuedSaveProducer(entry, record.terminalPromise, record);
      return true;
    }
    if (record.invocation?.key !== ownership.invocationKey) {
      return reject(
        record.transportAttempt > 1 ? "rejected-wrong-generation" : "rejected-duplicate-claim",
        `identity is already owned by invocation ${ownership.ownerRecord.operationId}; claimant belongs to a different invocation`,
      );
    }
    if (record.transportVerdict?.accepted !== true || record.transportVerdict.role !== "retry") {
      return reject(
        record.transportAttempt === 1 ? "rejected-duplicate-claim" : "rejected-invalid-retry",
        String(record.transportVerdict?.reason || "claim was not a validated retry of the owning invocation"),
      );
    }
    if (ownership.currentAttempt !== ownership.ownerRecord || record.retryOf !== ownership.ownerRecord) {
      return reject("rejected-invalid-retry", "owning invocation already used its single retry");
    }
    // The owning invocation's validated retry: same invocation, same session, same payload.
    ownership.currentAttempt = record;
    ownership.state = ownership.state === "provisional-owned" ? "provisional-owned-retry" : "owned-retry";
    record.queuedEntry = entry;
    record.correlationState = ownership.ownerRecord.correlationState;
    record.claim = { ...claim, verdict: "retry", comparedSnapshot: "owner-invocation-payload" };
    ownership.claims.push(record.claim);
    entry.transitions.push({ type: "owned-invocation-retry", at: Date.now(), operationId: record.operationId, transportId: record.transportId });
    attachQueuedSaveProducer(entry, record.terminalPromise, record);
    return true;
  };

  const observeRequest = (request) => {
    const pathname = quoteSessionPathForRequest(request);
    if (request.method() !== "POST" || pathname !== "/api/quote-sessions") return;
    if (requestRecords.has(request)) return;
    const headers = request.headers();
    const payload = readPayload(request);
    let requestPage = null;
    try { requestPage = request.frame()?.page() || null; } catch {}
    const rawQueuedSaveIdentity = String(headers[quoteSessionQueuedSaveHeader] || "");
    const queuedSaveIdentity = rawQueuedSaveIdentity && requestPage
      ? normalizeTimerIdentity(requestPage, rawQueuedSaveIdentity) : "";
    const operationId = String(headers[quoteSessionOperationHeader] || `smoke/${group}/save-${++operationSequence}`);
    const record = {
      request,
      method: request.method(),
      path: pathname,
      operationId,
      fixture: String(headers[quoteSessionFixtureHeader] || group),
      operation: String(headers[quoteSessionOperationNameHeader] || operationId),
      persistenceClass: String(headers[quoteSessionPersistenceClassHeader] || "REQUIRED_SUCCESS"),
      correlationToken: String(headers[quoteSessionCorrelationHeader] || ""),
      expectedSessionId: quoteSessionRequestId(request, payload),
      expectedQuoteGenerated: typeof payload?.status?.quote_generated === "boolean" ? payload.status.quote_generated : null,
      expectedFields: quoteSessionExpectedFields(payload),
      payloadDraftState: payload?.draft_state && typeof payload.draft_state === "object" ? payload.draft_state : null,
      payloadDraftFiles: Array.isArray(payload?.draft_files) ? payload.draft_files : null,
      queuedSaveIdentity,
      terminalProducer: "browser-response-or-requestfailed",
      terminalResolve: null,
      httpStatus: null,
      bodyStatus: "",
      responseSessionId: "",
      responseQuoteGenerated: null,
      responseUpdatedAt: "",
      clientResult: null,
      readback: null,
      readbackVerdict: "",
      observedAt: Date.now(),
      priorCommitClock: sessionCommitClocks.get(quoteSessionRequestId(request, payload)) ?? null,
      correlationState: "",
      transportFailure: false,
      done: false,
    };
    const rawTransportAttempt = Number(headers[quoteSessionTransportAttemptHeader] || 1);
    record.transportAttempt = Number.isInteger(rawTransportAttempt) && rawTransportAttempt > 0 ? rawTransportAttempt : 0;
    record.transportId = String(headers[quoteSessionTransportIdHeader] || `${group}/transport-${operationSequence}-${records.length}`);
    record.page = requestPage;
    record.pageIdentity = requestPage ? ensurePageIdentity(requestPage) : `${group}/page-unregistered`;
    record.requestSeq = ++eventSequence;
    record.postData = String(request.postData() || "");
    record.credentialDigests = credentialHeaderDigests(headers);
    record.bodyObservedPromise = new Promise((resolve) => { record.resolveBodyObserved = resolve; });
    createOwnProofState(record);
    record.terminalPromise = new Promise((resolve) => { record.terminalResolve = resolve; });
    requestRecords.set(request, record);
    records.push(record);
    if (record.persistenceClass === "DIAGNOSTIC_ONLY") {
      record.resolveOwnProof("ineligible", "diagnostic request");
    } else {
      pendingRequiredRequests.add(record);
      pendingSavePromises.add(record);
      registerTransportAttempt(record);
      const entry = record.queuedSaveIdentity ? queuedSaveHistory.get(record.queuedSaveIdentity) : null;
      const claimAccepted = record.queuedSaveIdentity ? admitQueuedSaveClaim(entry, record) : true;
      if (!record.queuedSaveIdentity && record.transportVerdict?.accepted !== true) {
        addIssue(record, `required save transport attempt rejected: ${record.transportVerdict?.reason || "unvalidated transport attempt"}`);
      }
      // A rejected claim never participates in equivalent-work matching, so it cannot settle or
      // dequeue any other invocation.
      for (const candidate of claimAccepted ? pendingQueuedSaveWork.values() : []) {
        const matchesCapturedWork = candidate.status === "pending"
          && !candidate.persistenceRecord
          && queuedSaveMatchesRecord(candidate, record);
        const ownsEquivalentHandoff = candidate.status === "pending"
          && !candidate.persistenceRecord
          && candidate.ownedFlushLease
          && !candidate.ownedFlushLease.settled
          && candidate.snapshot?.sessionId === record.expectedSessionId;
        if (matchesCapturedWork || ownsEquivalentHandoff) {
          candidate.equivalentRequestRecord = record;
          candidate.equivalentRequestMatches = matchesCapturedWork;
          if (matchesCapturedWork) candidate.evidence.postObserved = true;
          if (ownsEquivalentHandoff) {
            setQueuedSaveProducer(candidate, record.terminalPromise, "equivalent-request-response-body-durable-readback");
          }
        }
      }
      notifyProgress();
    }
  };

  const observeDetailRequest = (request) => {
    const pathname = quoteSessionPathForRequest(request);
    const match = pathname.match(/^\/api\/quote-sessions\/([^/]+)$/);
    if (request.method() !== "GET" || !match || requestRecords.has(request)) return;
    const headers = request.headers();
    const persistenceClass = String(headers[quoteSessionPersistenceClassHeader] || "DIAGNOSTIC_ONLY");
    if (persistenceClass === "DIAGNOSTIC_ONLY") return;
    const operationId = String(headers[quoteSessionOperationHeader] || `smoke/${group}/detail-${++operationSequence}`);
    const expectedSessionId = quoteSessionRequestId(request);
    const record = {
      kind: "detail",
      request,
      operationId,
      fixture: String(headers[quoteSessionFixtureHeader] || group),
      operation: String(headers[quoteSessionOperationNameHeader] || operationId),
      persistenceClass,
      correlationToken: String(headers[quoteSessionCorrelationHeader] || ""),
      expectedSessionId,
      expectedFields: detailExpectations.get(operationId) || {},
      payloadDraftState: null,
      payloadDraftFiles: null,
      queuedSaveIdentity: "",
      terminalProducer: "browser-response-or-requestfailed",
      terminalResolve: null,
      httpStatus: null,
      bodyStatus: "",
      responseSessionId: "",
      responseQuoteGenerated: null,
      clientResult: null,
      readback: null,
      transportFailure: false,
      done: false,
    };
    record.terminalPromise = new Promise((resolve) => { record.terminalResolve = resolve; });
    detailExpectations.delete(operationId);
    requestRecords.set(request, record);
    records.push(record);
    pendingRequiredRequests.add(record);
    notifyProgress();
  };

  const observeListRequest = (request) => {
    const pathname = quoteSessionPathForRequest(request);
    if (request.method() !== "GET" || pathname !== "/api/quote-sessions" || requestRecords.has(request)) return;
    const headers = request.headers();
    const persistenceClass = String(headers[quoteSessionPersistenceClassHeader] || "DIAGNOSTIC_ONLY");
    if (persistenceClass === "DIAGNOSTIC_ONLY") return;
    const operationId = String(headers[quoteSessionOperationHeader] || `smoke/${group}/list-${++operationSequence}`);
    const record = {
      kind: "list",
      request,
      operationId,
      fixture: String(headers[quoteSessionFixtureHeader] || group),
      operation: String(headers[quoteSessionOperationNameHeader] || operationId),
      persistenceClass,
      correlationToken: String(headers[quoteSessionCorrelationHeader] || ""),
      expectedSessionId: quoteSessionRequestId(request),
      expectedFields: detailExpectations.get(operationId) || {},
      payloadDraftState: null,
      payloadDraftFiles: null,
      queuedSaveIdentity: "",
      terminalProducer: "browser-response-or-requestfailed",
      terminalResolve: null,
      httpStatus: null,
      bodyStatus: "",
      responseSessionId: "",
      responseQuoteGenerated: null,
      clientResult: null,
      readback: null,
      transportFailure: false,
      done: false,
    };
    record.terminalPromise = new Promise((resolve) => { record.terminalResolve = resolve; });
    detailExpectations.delete(operationId);
    requestRecords.set(request, record);
    records.push(record);
    pendingRequiredRequests.add(record);
    notifyProgress();
  };

  // Real session refreshes are the only admissible bridge between a 403 attempt and its retry.
  const observeSessionRefreshRequest = (request) => {
    if (request.method() !== "GET" || quoteSessionPathForRequest(request) !== "/api/session") return;
    let requestPage = null;
    try { requestPage = request.frame()?.page() || null; } catch {}
    const refresh = {
      pageIdentity: requestPage ? ensurePageIdentity(requestPage) : `${group}/page-unregistered`,
      requestSeq: ++eventSequence,
      responseSeq: 0,
      responseStatus: null,
      bodyEvidence: null,
    };
    let resolveBody;
    refresh.bodyEvidence = new Promise((resolve) => { resolveBody = resolve; });
    refresh.resolveBody = resolveBody;
    sessionRefreshByRequest.set(request, refresh);
    sessionRefreshes.push(refresh);
  };

  const observeSessionRefreshResponse = (response) => {
    const refresh = sessionRefreshByRequest.get(response.request());
    if (!refresh || refresh.responseSeq) return;
    refresh.responseSeq = ++eventSequence;
    refresh.responseStatus = response.status();
    response.text().then((text) => {
      let body = null;
      try { body = JSON.parse(text); } catch { body = null; }
      refresh.resolveBody({
        ok: refresh.responseStatus === 200,
        csrfHeader: String(body?.csrf_header || "").toLowerCase(),
        tokenDigest: body?.csrf_token ? createHash("sha256").update(String(body.csrf_token)).digest("hex") : "",
        recoveryScopePresent: Boolean(body?.browser_recovery_scope),
      });
    }, () => refresh.resolveBody({ ok: false, csrfHeader: "", tokenDigest: "", recoveryScopePresent: false }));
  };

  const queuedSaveMismatchSummary = (entry, record) => {
    const snapshot = entry?.snapshot || {};
    const expectedState = queuedDraftStateComparable(snapshot.draftState);
    const actualState = queuedDraftStateComparable(record.payloadDraftState);
    const stateKeys = new Set([...Object.keys(expectedState || {}), ...Object.keys(actualState || {})]);
    const stateDiffKeys = [...stateKeys].filter((key) => (
      JSON.stringify(stableJson(expectedState?.[key])) !== JSON.stringify(stableJson(actualState?.[key]))
    ));
    return {
      sessionMatch: snapshot.sessionId === record.expectedSessionId,
      quoteGeneratedMatch: snapshot.options?.quoteGenerated === record.expectedQuoteGenerated,
      stateDiffKeys,
      expectedFileCount: Array.isArray(snapshot.draftFiles) ? snapshot.draftFiles.length : null,
      actualFileCount: Array.isArray(record.payloadDraftFiles) ? record.payloadDraftFiles.length : null,
      // A POST may legitimately omit already-confirmed files; durable readback still proves them.
      filesRepresentation: record.payloadDraftFiles === null ? "omitted-confirmed-files" : "present",
      filesMatch: record.payloadDraftFiles === null
        ? "not-applicable"
        : Array.isArray(snapshot.draftFiles)
          && JSON.stringify(queuedDraftFilesComparable(snapshot.draftFiles)) === JSON.stringify(queuedDraftFilesComparable(record.payloadDraftFiles)),
      comparedSnapshot: entry?.saveInvocationSnapshot ? "save-invocation" : "queue-time",
    };
  };

  const validateQueuedPersistence = (entry, record) => {
    const snapshot = entry?.snapshot || {};
    const failures = [];
    if (!queuedSaveMatchesRecord(entry, record)) failures.push("request did not match captured session/state/files/status " + JSON.stringify(queuedSaveMismatchSummary(entry, record)));
    if (record.httpStatus < 200 || record.httpStatus >= 300 || record.bodyStatus !== "saved") failures.push("application response was not a successful saved result");
    if (record.responseSessionId !== snapshot.sessionId || record.responseQuoteGenerated !== snapshot.options?.quoteGenerated) failures.push("application response session/status did not match the captured save");
    if (record.clientResult?.nonNull !== true || record.clientResult?.sessionId !== snapshot.sessionId || record.clientResult?.quoteGenerated !== snapshot.options?.quoteGenerated) failures.push("application save result did not match the captured session/status");
    // R2B: this save's own authenticated detail proof is mandatory and content-bound; nothing
    // about a response, a clock, or another save substitutes for it.
    const proof = record.ownProof?.state === "accepted" ? record.ownProof.readback : null;
    if (!proof) {
      failures.push(`own authenticated persistence proof was not accepted (${record.ownProof?.state || "absent"}: ${record.ownProof?.reason || ""})`);
    } else {
      if (proof.quoteGenerated !== snapshot.options?.quoteGenerated) failures.push("own proof did not show the required quote_generated status");
      if (!proof.draftState || JSON.stringify(queuedDraftStateComparable(proof.draftState)) !== JSON.stringify(queuedDraftStateComparable(snapshot.draftState))) {
        failures.push("own proof draft state did not equal the save-invocation draft state");
      }
      // Covers valid confirmed file omission: omitted files must still be durably the save's files.
      if (!Array.isArray(proof.draftFiles) || JSON.stringify(queuedDraftFilesComparable(proof.draftFiles)) !== JSON.stringify(queuedDraftFilesComparable(snapshot.draftFiles))) {
        failures.push("own proof files did not match the save-invocation file identity/metadata/content");
      }
    }
    if (record.readback?.httpStatus !== 200 || record.readback?.sessionId !== snapshot.sessionId) failures.push("durable readback did not return the exact saved session");
    const successor = record.readbackVerdict === "superseded" ? record.readbackSuccessor : null;
    if (!successor) {
      if (record.readback?.quoteGenerated !== snapshot.options?.quoteGenerated) failures.push("durable readback did not preserve required quote_generated status");
      if (!record.readback?.draftState || JSON.stringify(queuedDraftStateComparable(record.readback.draftState)) !== JSON.stringify(queuedDraftStateComparable(snapshot.draftState))) failures.push("durable readback did not match captured draft state");
    }
    // A later readback explained by an independently proven successor must be exactly that
    // successor's proven files; otherwise it must still be this save's files.
    const expectedFiles = successor ? successor.ownProof?.readback?.draftFiles : snapshot.draftFiles;
    if (!Array.isArray(expectedFiles) || !Array.isArray(record.readback?.draftFiles)
      || JSON.stringify(queuedDraftFilesComparable(record.readback.draftFiles)) !== JSON.stringify(queuedDraftFilesComparable(expectedFiles))) failures.push("durable readback did not match captured draft files");
    return failures;
  };

  const completeQueuedPersistenceRecord = (record) => {
    const candidates = [];
    if (record.queuedSaveIdentity) {
      const exact = queuedSaveHistory.get(record.queuedSaveIdentity);
      if (exact) candidates.push(exact);
    }
    for (const entry of pendingQueuedSaveWork.values()) {
      if (!entry.persistenceRecord && entry.equivalentRequestRecord === record && !candidates.includes(entry)) candidates.push(entry);
    }
    for (const entry of candidates) {
      if (entry.status !== "pending") continue;
      // Deferred R1 correlation completes when the save-invocation snapshot arrives.
      if (entry.correlationPendingRecord) continue;
      // R1B: only the owning invocation's current transport attempt can complete its queued save.
      if (entry.ownership?.ownerRecord && entry.ownership.currentAttempt !== record) continue;
      const failures = validateQueuedPersistence(entry, record);
      if (failures.length) {
        settleQueuedSave(entry, "failed", `queued save persistence proof failed: ${failures.join("; ")}`);
      } else {
        entry.evidence.postObserved = true;
        entry.evidence.durableReadbackObserved = true;
        entry.evidence.persistenceRecordId = record.operationId;
        entry.evidence.persistenceTransportId = record.transportId;
        entry.evidence.ownProofAcceptedSeq = record.ownProof?.acceptedSeq || 0;
        if (record.readbackVerdict === "superseded") {
          entry.evidence.readbackSupersession = { ...record.readbackSupersession };
          settleQueuedSave(entry, "succeeded", "validated POST response and fresh commit, durably superseded by a validated later same-session save");
        } else {
          settleQueuedSave(entry, "succeeded", "validated POST response and exact durable readback");
        }
      }
    }
  };

  const observeResponse = (response) => {
    const record = requestRecords.get(response.request());
    if (!record || record.done) return;
    // Synchronous ordering evidence: the response is observed before any later request it causes.
    record.responseSeq = ++eventSequence;
    record.responseStatus = response.status();
    const task = finishResponse(record, response)
      .catch((error) => addIssue(record, `required response/body/save/readback drain failed: ${error?.message || error}`))
      .finally(() => {
        record.resolveBodyObserved?.();
        record.resolveOwnProof?.("ineligible", "response processing ended without persistence proof");
        record.done = true;
        record.terminalResolve?.();
        if (record.persistenceClass !== "DIAGNOSTIC_ONLY") completeQueuedPersistenceRecord(record);
        tasks.delete(task);
        notifyProgress();
      });
    record.responseBodyProducer = task;
    tasks.add(task);
  };

  const observeFailure = (request) => {
    const record = requestRecords.get(request);
    if (!record || record.done) return;
    record.done = true;
    record.transportFailure = true;
    record.resolveBodyObserved?.();
    record.resolveOwnProof?.("ineligible", "transport failure provides no persistence proof");
    record.terminalResolve?.();
    pendingRequiredRequests.delete(record);
    pendingSavePromises.delete(record);
    if (record.persistenceClass !== "DIAGNOSTIC_ONLY") {
      addIssue(record, `required quote-session save request failed: ${request.failure()?.errorText || "transport failure"}`);
      const entry = record.queuedSaveIdentity ? queuedSaveHistory.get(record.queuedSaveIdentity) : null;
      // Only the queued save's own current attempt can fail it; a rejected claimant cannot.
      if (entry?.status === "pending" && entry.ownership?.currentAttempt === record) {
        settleQueuedSave(entry, "failed", "correlated queued save request failed before persistence");
      }
    }
    notifyProgress();
  };

  const normalizeTimerIdentity = (page, browserIdentity) => {
    const pageIdentity = pageIdentities.get(page) || `${group}/page-unregistered`;
    return `${pageIdentity}/${String(browserIdentity || "")}`;
  };

  const normalizeTimerSnapshot = (page, snapshot) => {
    if (!snapshot || typeof snapshot !== "object") return null;
    const browserTimerIdentity = String(snapshot.timerIdentity || "");
    if (!browserTimerIdentity) return null;
    return {
      ...structuredClone(snapshot),
      browserTimerIdentity,
      pageIdentity: pageIdentities.get(page) || `${group}/page-unregistered`,
      timerIdentity: normalizeTimerIdentity(page, browserTimerIdentity),
      supersedesTimerIdentity: snapshot.supersedesTimerIdentity
        ? normalizeTimerIdentity(page, snapshot.supersedesTimerIdentity) : "",
    };
  };

  const failQueuedSave = (entry, reason) => settleQueuedSave(entry, "failed", reason);

  const registerOwnedQueuedSaveFlush = (captured, reason = "tracker-owned queued-save flush") => {
    const entry = captured?.entry
      || queuedSaveHistory.get(String(captured?.snapshot?.timerIdentity || ""));
    if (!entry || entry.status !== "pending") return null;
    if (entry.ownedFlushLease && !entry.ownedFlushLease.settled) return entry.ownedFlushLease;
    const lease = createProducerLease("owned-flush", "reserved");
    entry.ownedFlushLease = lease;
    entry.flushOperation = { state: "FLUSH_OWNERSHIP_RESERVED", identity: entry.identity, at: Date.now(), reason: String(reason) };
    entry.transitions.push({ type: "FLUSH_OWNERSHIP_RESERVED", at: Date.now(), reason: String(reason) });
    notifyProgress();
    return lease;
  };

  const proveQueuedSaveByReadback = async (page, entry) => {
    if (!page || page.isClosed()) {
      failQueuedSave(entry, "queued save settled without a POST and its page closed before durable readback");
      return;
    }
    const outcome = entry.saveOutcome || {};
    const snapshot = entry.snapshot || {};
    if (outcome.sessionId !== snapshot.sessionId) {
      failQueuedSave(entry, "queued save dedupe result did not identify the exact captured session");
      return;
    }
    if (typeof outcome.quoteGenerated === "boolean" && outcome.quoteGenerated !== snapshot.options?.quoteGenerated) {
      failQueuedSave(entry, "queued save dedupe result had a mismatched required quote_generated status");
      return;
    }
    try {
      const readbackPromise = page.evaluate(async (sessionId) => {
        const response = await fetch(`/api/quote-sessions/${encodeURIComponent(sessionId)}?__sqag_smoke_equivalent=${Date.now()}`, {
          cache: "no-store", headers: { "cache-control": "no-cache", pragma: "no-cache" },
        });
        let body = null;
        try { body = await response.json(); } catch {}
        const session = body?.quote_session && typeof body.quote_session === "object" ? body.quote_session : null;
        return {
          httpStatus: response.status,
          sessionId: String(session?.session_id || ""),
          quoteGenerated: typeof session?.status?.quote_generated === "boolean" ? session.status.quote_generated : null,
          draftState: session?.draft_state && typeof session.draft_state === "object" ? session.draft_state : null,
          draftFiles: Array.isArray(session?.draft_files) ? session.draft_files : null,
        };
      }, snapshot.sessionId);
      entry.applicationSaveLease?.resolve({ status: "transferred", to: "durable-readback" });
      entry.applicationSaveLease = null;
      setQueuedSaveProducer(entry, readbackPromise, "durable-readback");
      const readback = await readbackPromise;
      entry.evidence.durableReadbackObserved = true;
      entry.evidence.readback = readback;
      const failures = [];
      if (readback.httpStatus !== 200 || readback.sessionId !== snapshot.sessionId) failures.push("durable readback did not return the exact session");
      if (readback.quoteGenerated !== snapshot.options?.quoteGenerated) failures.push("durable readback had the wrong required status");
      if (!readback.draftState || JSON.stringify(queuedDraftStateComparable(readback.draftState)) !== JSON.stringify(queuedDraftStateComparable(snapshot.draftState))) {
        failures.push("durable readback state did not match captured state; fields=" + queuedDraftStateDiffKeys(readback.draftState, snapshot.draftState).join(","));
      }
      if (!Array.isArray(readback.draftFiles) || JSON.stringify(queuedDraftFilesComparable(readback.draftFiles)) !== JSON.stringify(queuedDraftFilesComparable(snapshot.draftFiles))) {
        failures.push("durable readback files did not match captured file identity/content; expected=" + JSON.stringify(queuedDraftFilesComparable(snapshot.draftFiles)) + "; actual=" + JSON.stringify(queuedDraftFilesComparable(readback.draftFiles)));
      }
      if (failures.length) {
        failQueuedSave(entry, `equivalent queued-save persistence proof failed: ${failures.join("; ")}`);
      } else {
        entry.evidence.equivalentDurableReadback = true;
        settleQueuedSave(entry, "succeeded", "exact application result plus matching durable readback for equivalent persisted state");
      }
    } catch (error) {
      failQueuedSave(entry, `equivalent queued-save durable readback failed: ${error?.message || error}`);
    }
  };

  const handleTimerEvent = async (source, event) => {
    const page = source?.page;
    if (!page) return;
    const type = String(event?.type || "");
    const documentId = String(event?.documentId || event?.snapshot?.documentId || "");
    if (documentId) getDocumentEpisode(page, documentId);
    const rawIdentity = String(event?.snapshot?.timerIdentity || event?.timerIdentity || "");
    const identity = normalizeTimerIdentity(page, rawIdentity);
    if (type === "create" || type === "replace") {
      const snapshot = normalizeTimerSnapshot(page, event.snapshot);
      const supersedes = type === "replace" ? normalizeTimerIdentity(page, event.supersedes) : "";
      registerQueuedSaveWork(snapshot, supersedes);
      return;
    }
    const entry = queuedSaveHistory.get(identity);
    if (!entry || entry.status !== "pending") return;
    entry.transitions.push({ type, at: Date.now(), reason: String(event?.reason || "") });
    if (type === "save-start") {
      const snapshot = normalizeTimerSnapshot(page, event?.snapshot);
      if (!snapshot || snapshot.timerIdentity !== identity || !snapshot.options || typeof snapshot.options.quoteGenerated !== "boolean") {
        failQueuedSave(entry, "queued timer save began without an exact session/state/files/options snapshot");
        return;
      }
      if (event.invocationState !== "application-save-promise-established") {
        failQueuedSave(entry, "ORPHAN_NO_REAL_PRODUCER: timer save-start event arrived before an executable application save promise was established");
        return;
      }
      entry.snapshot = snapshot;
      entry.saveInvocationSnapshot = snapshot;
      entry.saveInvocationObservedAt = Date.now();
      const deferredRecord = entry.correlationPendingRecord || null;
      entry.correlationPendingRecord = null;
      const executableSuccessor = entry.ownedFlushLease || createProducerLease("application-save", "started");
      executableSuccessor.state = "started";
      executableSuccessor.executable = true;
      executableSuccessor.startedAt = Date.now();
      entry.applicationSaveLease = executableSuccessor;
      if (deferredRecord) {
        // Correlate the owning invocation only (its initial attempt and any validated retry). A
        // late save-start never erases a sticky violation already recorded against this identity.
        const invocationAttempts = [...new Set([deferredRecord, entry.ownership?.currentAttempt].filter(Boolean))];
        const mismatched = invocationAttempts.find((attempt) => !queuedSaveMatchesRecord(entry, attempt));
        if (mismatched) {
          for (const attempt of invocationAttempts) attempt.correlationState = "rejected";
          addIssue(mismatched, "queued save request did not match the save-invocation snapshot of its exact queued save: "
            + JSON.stringify(queuedSaveMismatchSummary(entry, mismatched)));
          failQueuedSave(entry, "correlated POST did not match the save-invocation snapshot of its queued save");
          return;
        }
        for (const attempt of invocationAttempts) attempt.correlationState = "correlated-by-save-invocation-snapshot";
        entry.ownership.state = "owned";
        entry.correlationEvidence = { ...(entry.correlationEvidence || {}), correlatedAgainst: "save-invocation-snapshot" };
        // The already-observed request remains the producer; this lease only records the start.
        executableSuccessor.resolve({ status: "transferred", to: "request-response-body-durable-readback" });
        entry.applicationSaveLease = null;
        if (entry.ownedFlushLease) {
          entry.flushOperation = {
            ...(entry.flushOperation || {}),
            state: "FLUSH_OPERATION_STARTED",
            identity: entry.identity,
            startedAt: executableSuccessor.startedAt,
            documentId: String(snapshot.documentId || ""),
          };
        }
        updateDocumentWork(entry, "pending", { producerState: "request-observed-before-save-start" });
        const currentAttempt = entry.ownership?.currentAttempt || deferredRecord;
        if (currentAttempt.done) completeQueuedPersistenceRecord(currentAttempt);
        notifyProgress();
        return;
      }
      if (entry.ownedFlushLease) {
        entry.flushOperation = {
          ...(entry.flushOperation || {}),
          state: "FLUSH_OPERATION_STARTED",
          identity: entry.identity,
          startedAt: executableSuccessor.startedAt,
          documentId: String(snapshot.documentId || ""),
        };
      }
      const nativeTimerStillOwns = snapshot.active === true
        && entry.nativeTimerLease
        && entry.nativeTimerLease.settled === false;
      if (!nativeTimerStillOwns) {
        setQueuedSaveProducer(entry, executableSuccessor, entry.ownedFlushLease ? "owned-flush-operation" : "application-save");
      }
      updateDocumentWork(entry, "pending", {
        producerState: nativeTimerStillOwns ? "native-timer-retained-successor-started" : "started",
      });
      notifyProgress();
      return;
    }
    if (type === "cancel") {
      entry.snapshot.active = false;
      entry.nativeTimerLease?.resolve({ status: "cancelled" });
      const executableFlush = hasExecutableOwnedFlush(entry);
      if (event.reason === "recovery-transition") {
        entry.cancelledForRecovery = true;
        if (executableFlush) {
          entry.flushOperation.ownershipTransfer = "transferred-to-recovery";
          entry.transitions.push({ type: "native-owner-transferred-to-started-flush", at: Date.now(), identity: entry.identity });
          setQueuedSaveProducer(entry, entry.applicationSaveProducer || entry.ownedFlushLease, entry.applicationSaveProducer
            ? "request-response-body-durable-readback" : "owned-flush-operation");
          notifyProgress();
        } else {
          failQueuedSave(entry, "ORPHAN_NO_REAL_PRODUCER: recovery cancellation removed native timer ownership while owned flush was only reserved or absent");
        }
      } else if (executableFlush) {
        entry.cancelledForApplication = true;
        entry.flushOperation.ownershipTransfer = "transferred-to-started-flush";
        entry.transitions.push({ type: "native-owner-transferred-to-started-flush", at: Date.now(), identity: entry.identity });
        setQueuedSaveProducer(entry, entry.applicationSaveProducer || entry.ownedFlushLease, entry.applicationSaveProducer
          ? "request-response-body-durable-readback" : "owned-flush-operation");
        notifyProgress();
      } else {
        failQueuedSave(entry, "ORPHAN_NO_REAL_PRODUCER: queued save timer was cancelled without an executable successor or persistence");
      }
      return;
    }
    if (type === "fire") {
      entry.dispatchedBy = "fire";
      entry.snapshot.active = false;
      entry.nativeTimerLease?.resolve({ status: "fired" });
      const callbackLease = createProducerLease("native-timer-callback", "started");
      entry.timerCallbackLease = callbackLease;
      setQueuedSaveProducer(entry, callbackLease, "native-timer-callback");
      updateDocumentWork(entry, "pending", { producerState: "timer-callback-started" });
      notifyProgress();
      return;
    }
    if (type === "flush") {
      entry.dispatchedBy = "flush";
      entry.snapshot.active = false;
      if (!entry.ownedFlushLease || !hasExecutableOwnedFlush(entry)) {
        failQueuedSave(entry, "ORPHAN_NO_REAL_PRODUCER: queued save timer was flushed before an executable owned flush operation started");
        return;
      }
      entry.flushOperation.ownershipTransfer = "transferred-to-started-flush";
      setQueuedSaveProducer(entry, entry.applicationSaveProducer || entry.ownedFlushLease, entry.applicationSaveProducer
        ? "request-response-body-durable-readback" : "owned-flush-operation");
      notifyProgress();
      return;
    }
    if (type !== "save-outcome") return;
    entry.saveOutcome = {
      kind: String(event.outcomeKind || ""),
      sessionId: String(event.sessionId || ""),
      quoteGenerated: typeof event.quoteGenerated === "boolean" ? event.quoteGenerated : null,
      reason: String(event.reason || ""),
    };
    if (entry.saveOutcome.kind === "null") {
      failQueuedSave(entry, "real saveQuoteSessionDraftState returned null before persistence");
      return;
    }
    if (entry.saveOutcome.kind === "rejected") {
      failQueuedSave(entry, `real saveQuoteSessionDraftState rejected: ${entry.saveOutcome.reason || "unknown rejection"}`);
      return;
    }
    if (entry.saveOutcome.sessionId !== entry.snapshot.sessionId) {
      failQueuedSave(entry, "real queued save result did not identify the captured session");
      return;
    }
    if (typeof entry.saveOutcome.quoteGenerated === "boolean"
      && entry.saveOutcome.quoteGenerated !== entry.snapshot.options?.quoteGenerated) {
      failQueuedSave(entry, "real queued save result had mismatched quote_generated status: expected " + String(entry.snapshot.options?.quoteGenerated) + ", received " + String(entry.saveOutcome.quoteGenerated));
      return;
    }
    if (entry.persistenceRecord) {
      await entry.persistenceRecord.terminalPromise;
      if (entry.status === "pending") completeQueuedPersistenceRecord(entry.persistenceRecord);
      if (entry.status === "pending") failQueuedSave(entry, "queued POST completed without validated durable persistence proof");
      return;
    }
    await proveQueuedSaveByReadback(page, entry);
  };

  const ensurePageIdentity = (page) => {
    if (!pageIdentities.has(page)) pageIdentities.set(page, `${group}/page-${++pageSequence}`);
    attachedPages.add(page);
    return pageIdentities.get(page);
  };

  const documentEpisodeKey = (pageIdentity, documentId) => `${pageIdentity}/${String(documentId || "<unknown-document>")}`;

  const updateDocumentWork = (entry, state, detail = {}) => {
    const snapshot = entry?.snapshot || {};
    if (!snapshot.pageIdentity || !snapshot.documentId) return null;
    const key = documentEpisodeKey(snapshot.pageIdentity, snapshot.documentId);
    let episode = documentEpisodes.get(key);
    if (!episode) {
      episode = {
        identity: key,
        pageIdentity: snapshot.pageIdentity,
        documentId: String(snapshot.documentId),
        state: "observed",
        work: new Map(),
        deliveryFailures: [],
        lifecycle: [],
        zeroProof: null,
        unknownRequiredWork: false,
        lastObservedAt: Date.now(),
      };
      documentEpisodes.set(key, episode);
    }
    episode.work.set(String(snapshot.timerIdentity), {
      timerIdentity: String(snapshot.timerIdentity),
      state,
      at: Date.now(),
      ...detail,
    });
    episode.lastObservedAt = Date.now();
    if (state === "pending" || state === "unknown" || state === "failed") episode.zeroProof = null;
    return episode;
  };

  const retireDocumentEpisode = (episode, reason) => {
    if (!episode || episode.lifecycle.some((item) => item.reason === reason)) return;
    episode.lifecycle.push({ type: "document-lost", reason, at: Date.now() });
    episode.current = false;
    episode.lastObservedAt = Date.now();
    const unresolvedEntries = [...pendingQueuedSaveWork.values()].filter((entry) => (
      entry.status === "pending"
      && entry.snapshot.pageIdentity === episode.pageIdentity
      && String(entry.snapshot.documentId || "") === episode.documentId
    ));
    for (const entry of unresolvedEntries) {
      failQueuedSave(entry, `${reason}; required work remained unresolved when its originating document disappeared`);
      updateDocumentWork(entry, "unknown", { reason });
    }
    const unresolvedKnownWork = [...episode.work.values()].some((item) => ["pending", "unknown", "failed"].includes(item.state));
    const provenTerminalWork = episode.work.size > 0 && [...episode.work.values()].every((item) => (
      ["succeeded", "superseded"].includes(item.state)
    ));
    if (episode.unknownRequiredWork || unresolvedKnownWork) {
      episode.state = "lost-with-unknown-or-required-work";
      return;
    }
    if (provenTerminalWork) {
      episode.state = "closed-after-proven-completion";
      return;
    }
    if (episode.zeroProof) {
      episode.state = "closed-with-proven-zero-work";
      return;
    }
    episode.unknownRequiredWork = true;
    episode.state = "lost-with-unknown-required-work";
    addIssue({
      operationId: `${episode.identity}/lifecycle`,
      fixture: group,
      operation: "document-lifecycle",
    }, `UNKNOWN_REQUIRED_WORK: ${reason}; no independent zero or completion proof existed for ${episode.identity}`);
  };

  const getDocumentEpisode = (page, documentId) => {
    const pageIdentity = ensurePageIdentity(page);
    const normalizedDocumentId = String(documentId || "");
    if (!normalizedDocumentId) return null;
    const key = documentEpisodeKey(pageIdentity, normalizedDocumentId);
    const priorKey = currentDocumentByPage.get(page);
    if (priorKey && priorKey !== key) {
      const prior = documentEpisodes.get(priorKey);
      retireDocumentEpisode(prior, "document was replaced by a committed navigation");
    }
    let episode = documentEpisodes.get(key);
    if (!episode) {
      episode = {
        identity: key,
        pageIdentity,
        documentId: normalizedDocumentId,
        state: "observed",
        work: new Map(),
        deliveryFailures: [],
        lifecycle: [],
        zeroProof: null,
        unknownRequiredWork: false,
        lastObservedAt: Date.now(),
      };
      documentEpisodes.set(key, episode);
    }
    episode.current = true;
    episode.lastObservedAt = Date.now();
    currentDocumentByPage.set(page, key);
    return episode;
  };

  const recordTimerEventDeliveryFailure = (page, event, reason) => {
    const pageIdentity = ensurePageIdentity(page);
    const documentId = String(event?.documentId || event?.snapshot?.documentId || "");
    const episode = getDocumentEpisode(page, documentId);
    const type = String(event?.type || "unknown");
    const rawIdentity = String(event?.snapshot?.timerIdentity || event?.timerIdentity || "");
    const timerIdentity = rawIdentity ? normalizeTimerIdentity(page, rawIdentity) : "";
    const failureKey = `${pageIdentity}/${documentId || "<unknown-document>"}/${type}/${rawIdentity}`;
    if (observedTimerEventFailures.has(failureKey)) return;
    observedTimerEventFailures.add(failureKey);
    const failure = { identity: timerIdentity, documentId, type, reason: String(reason || "binding failure"), at: Date.now() };
    if (episode) {
      episode.deliveryFailures.push(failure);
      episode.unknownRequiredWork = true;
      episode.zeroProof = null;
      episode.state = "observation-failed";
      if (timerIdentity) episode.work.set(timerIdentity, { timerIdentity, state: "unknown", source: "timer-event-delivery-failure", at: failure.at });
    }
    addIssue({
      operationId: timerIdentity || `${pageIdentity}/timer-event/${type}`,
      fixture: group,
      operation: "queued-save-timer-event",
    }, `UNKNOWN_REQUIRED_WORK: actual timer-event delivery failed for ${timerIdentity || "unidentified work"} in ${episode?.identity || `${pageIdentity}/<unknown-document>`}: ${failure.reason}`);
  };

  const faultNextTimerEventDelivery = (page, type = "create") => {
    timerEventFaults.set(page, { type: String(type || "create"), armed: true });
  };

  const holdTimerEventAcknowledgement = (page, type) => {
    let observedResolve;
    let releaseResolve;
    const hold = {
      type: String(type || "create"),
      held: false,
      eventIdentity: "",
      observed: new Promise((resolve) => { observedResolve = resolve; }),
      released: new Promise((resolve) => { releaseResolve = resolve; }),
      observe: (eventIdentity) => {
        hold.held = true;
        hold.eventIdentity = String(eventIdentity || "");
        observedResolve({ identity: hold.eventIdentity, type: hold.type });
      },
      release: () => releaseResolve(),
    };
    timerEventAckHolds.set(page, hold);
    return hold;
  };

  // Deterministically delays Node's handling of one real timer event (without touching the page
  // or application timing) so controls can order it against the application's real POST.
  const holdTimerEventHandling = (page, type) => {
    let observedResolve;
    let releaseResolve;
    const hold = {
      type: String(type || "save-start"),
      held: false,
      eventIdentity: "",
      observed: new Promise((resolve) => { observedResolve = resolve; }),
      released: new Promise((resolve) => { releaseResolve = resolve; }),
      observe: (eventIdentity) => {
        hold.held = true;
        hold.eventIdentity = String(eventIdentity || "");
        observedResolve({ identity: hold.eventIdentity, type: hold.type });
      },
      release: () => releaseResolve(),
    };
    timerEventHandlingHolds.set(page, hold);
    return hold;
  };

  const markPageWorkUnresolved = (page, reason) => {
    const pageIdentity = pageIdentities.get(page);
    if (!pageIdentity) return;
    const currentKey = currentDocumentByPage.get(page);
    if (currentKey) retireDocumentEpisode(documentEpisodes.get(currentKey), reason);
    for (const entry of pendingQueuedSaveWork.values()) {
      if (entry.snapshot.pageIdentity === pageIdentity && entry.status === "pending") {
        failQueuedSave(entry, reason);
      }
    }
  };

  const install = async (context) => {
    await context.exposeBinding(timerEventBinding, async (source, event) => {
      const page = source?.page;
      if (!page) throw new Error("timer event source page is unavailable");
      ensurePageIdentity(page);
      const fault = timerEventFaults.get(page);
      if (fault?.armed && fault.type === String(event?.type || "")) {
        fault.armed = false;
        recordTimerEventDeliveryFailure(page, event, "deterministic smoke fault rejected the actual exposed timer-event binding");
        throw new Error("deterministic smoke fault at timer-event binding");
      }
      const handlingHold = timerEventHandlingHolds.get(page);
      if (handlingHold && handlingHold.type === String(event?.type || "") && !handlingHold.held) {
        const rawIdentity = String(event?.snapshot?.timerIdentity || event?.timerIdentity || "");
        handlingHold.observe(rawIdentity ? normalizeTimerIdentity(page, rawIdentity) : "");
        await handlingHold.released;
        timerEventHandlingHolds.delete(page);
      }
      try {
        await handleTimerEvent(source, event);
      } catch (error) {
        recordTimerEventDeliveryFailure(page, event, error?.message || error);
        throw error;
      }
      const hold = timerEventAckHolds.get(page);
      if (hold && hold.type === String(event?.type || "") && !hold.held) {
        const rawIdentity = String(event?.snapshot?.timerIdentity || event?.timerIdentity || "");
        hold.observe(rawIdentity ? normalizeTimerIdentity(page, rawIdentity) : "");
        await hold.released;
        timerEventAckHolds.delete(page);
      }
      return { acknowledged: true };
    });
    await context.addInitScript((settings) => {
      const pendingOperations = [];
      const transportTags = [];
      let transportSequence = 0;
      let sequence = 0;
      const readScope = () => {
        try { return JSON.parse(sessionStorage.getItem(settings.operationStorageKey) || "{}"); } catch { return {}; }
      };
      const nextOperationId = (kind) => `${settings.group}/${Date.now()}-${++sequence}/${kind}`;
      window[settings.resultStorageKey] = [];
      const cloneOptions = (value) => {
        try {
          return JSON.parse(JSON.stringify(value && typeof value === "object" ? value : {}));
        } catch {
          return null;
        }
      };
      const captureEffectiveSaveOptions = (value) => {
        const requestedOptions = cloneOptions(value);
        const options = cloneOptions(requestedOptions);
        if (options && typeof options.quoteGenerated === "boolean") {
          const hasFreshExports = typeof window.quoteSessionHasFreshOutputExports === "function"
            && Boolean(window.quoteSessionHasFreshOutputExports());
          options.quoteGenerated = Boolean(options.quoteGenerated && hasFreshExports);
        }
        return { options, requestedOptions };
      };
      const stableValue = (value) => {
        if (Array.isArray(value)) return value.map(stableValue);
        if (value && typeof value === "object") {
          return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
        }
        return value;
      };
      const comparableDraftState = (value) => {
        const comparable = cloneOptions(value);
        delete comparable.savedAt;
        delete comparable.activeAppView;
        delete comparable.activeSidePanel;
        return stableValue(comparable);
      };
      const stableStringify = (value) => JSON.stringify(stableValue(value));
      const documentId = (() => {
        try { return crypto.randomUUID(); } catch { return `${Date.now()}-${Math.random().toString(36).slice(2)}`; }
      })();
      const timerState = window.__sqagSmokeQuoteSessionTimerState || {
        pending: null,
        last: null,
        queueCaptureDepth: 0,
        queueOptions: null,
        saveCalls: [],
        saveCurrentCalls: [],
        queuedSaveOperations: [],
        apiTrace: [],
        timerSequence: 0,
        replaceCandidate: null,
      };
      for (const key of ["saveCalls", "saveCurrentCalls", "queuedSaveOperations", "apiTrace", "eventDeliveryFailures", "pendingEventPromises"]) {
        if (!Array.isArray(timerState[key])) timerState[key] = [];
      }
      timerState.pending ??= null;
      timerState.last ??= null;
      timerState.queueCaptureDepth = Number(timerState.queueCaptureDepth || 0);
      timerState.documentId = documentId;
      timerState.timerSequence = Number(timerState.timerSequence || 0);
      window.__sqagSmokeQuoteSessionTimerState = timerState;
      const trackEventPromise = (promise) => {
        timerState.lastEventPromise = promise;
        timerState.pendingEventPromises.push(promise);
        promise.then(() => {
          const index = timerState.pendingEventPromises.indexOf(promise);
          if (index >= 0) timerState.pendingEventPromises.splice(index, 1);
        });
        return promise;
      };
      const emitTimerEvent = (event) => {
        const eventIdentity = String(event?.snapshot?.timerIdentity || event?.timerIdentity || "");
        const detail = {
          type: String(event?.type || ""),
          timerIdentity: eventIdentity,
          at: Date.now(),
        };
        const binding = window[settings.timerEventBinding];
        if (typeof binding !== "function") {
          timerState.eventDeliveryFailures.push({ ...detail, reason: "tracker event binding is unavailable" });
          return trackEventPromise(Promise.resolve(false));
        }
        try {
          return trackEventPromise(Promise.resolve(binding({ ...event, documentId: timerState.documentId }))
            .then(() => true, (error) => {
              timerState.eventDeliveryFailures.push({
                ...detail,
                reason: String(error?.message || error || "tracker event binding rejected"),
              });
              return false;
            }));
        } catch (error) {
          timerState.eventDeliveryFailures.push({
            ...detail,
            reason: String(error?.message || error || "tracker event binding threw"),
          });
          return trackEventPromise(Promise.resolve(false));
        }
      };
      window.__sqagSmokeEmitQueuedSaveEvent = emitTimerEvent;
      if (!window.__sqagSmokeQuoteSessionTimerHooksInstalled) {
        const nativeSetTimeout = window.setTimeout.bind(window);
        const nativeClearTimeout = window.clearTimeout.bind(window);
        window.setTimeout = (handler, delay, ...args) => {
          let timerId = null;
          const wrappedHandler = (...callbackArgs) => {
            const current = window.__sqagSmokeQuoteSessionTimerState;
            const fired = current?.pending && String(current.pending.nativeTimerId) === String(timerId)
              ? current.pending : null;
            if (fired) {
              fired.active = false;
              fired.firedAt = Date.now();
              current.last = fired;
              current.pending = null;
              const previousIdentity = current.activeTimerIdentity || "";
              current.activeTimerIdentity = fired.timerIdentity;
              emitTimerEvent({ type: "fire", snapshot: cloneOptions(fired) });
              try { return handler(...callbackArgs); }
              finally { current.activeTimerIdentity = previousIdentity; }
            }
            return handler(...callbackArgs);
          };
          timerId = nativeSetTimeout(wrappedHandler, delay, ...args);
          const current = window.__sqagSmokeQuoteSessionTimerState;
          if (current?.queueCaptureDepth > 0) {
            let persistedSessionId = "";
            try {
              persistedSessionId = String(JSON.parse(localStorage.getItem("swooshz_quote_session_v1") || "{}").quoteSessionId || "");
            } catch {
              persistedSessionId = "";
            }
            const capturedOptions = captureEffectiveSaveOptions(current.queueOptions);
            const snapshot = {
              timerIdentity: `${settings.group}/doc-${current.documentId}/timer-${++current.timerSequence}`,
              nativeTimerId: String(timerId),
              documentId: current.documentId,
              options: capturedOptions.options,
              requestedOptions: capturedOptions.requestedOptions,
              sessionId: String(window.state?.quoteSessionId || persistedSessionId),
              draftState: typeof window.currentQuoteSessionDraftState === "function"
                ? cloneOptions(window.currentQuoteSessionDraftState()) : null,
              draftFiles: typeof window.sessionFileRecordsFromDraft === "function"
                ? cloneOptions(window.sessionFileRecordsFromDraft()) : null,
              active: true,
              queuedAt: Date.now(),
              clearedAt: 0,
              firedAt: 0,
              flushedAt: 0,
              cancelledForRecovery: false,
            };
            const supersedes = current.replaceCandidate?.timerIdentity || current.pending?.timerIdentity || "";
            snapshot.supersedesTimerIdentity = supersedes;
            current.replaceCandidate = null;
            current.pending = snapshot;
            current.last = snapshot;
            emitTimerEvent({ type: supersedes ? "replace" : "create", supersedes, snapshot: cloneOptions(snapshot) });
          }
          return timerId;
        };
        window.clearTimeout = (timerId, ...args) => {
          const current = window.__sqagSmokeQuoteSessionTimerState;
          if (current?.pending && String(current.pending.nativeTimerId) === String(timerId)) {
            const cancelled = current.pending;
            cancelled.active = false;
            cancelled.clearedAt = Date.now();
            current.last = cancelled;
            current.pending = null;
            if (current.queueCaptureDepth > 0) {
              current.replaceCandidate = cancelled;
          } else if (current.flushIntentIdentity === cancelled.timerIdentity) {
            cancelled.flushedAt = Date.now();
            emitTimerEvent({ type: "flush", snapshot: cloneOptions(cancelled) });
          } else if (typeof state !== "undefined" && state.isRecoveryScopeTransitioning === true) {
            cancelled.cancelledForRecovery = true;
            const recoveryContinuation = current.recoveryFlushContinuations?.[cancelled.timerIdentity];
            if (typeof recoveryContinuation === "function") {
              delete current.recoveryFlushContinuations[cancelled.timerIdentity];
              try {
                current.recoveryContinuationPromises ||= Object.create(null);
                current.recoveryContinuationPromises[cancelled.timerIdentity] = Promise.resolve(recoveryContinuation());
              } catch (error) {
                current.recoveryContinuationErrors ||= Object.create(null);
                current.recoveryContinuationErrors[cancelled.timerIdentity] = String(error?.message || error);
              }
            }
            emitTimerEvent({ type: "cancel", reason: "recovery-transition", snapshot: cloneOptions(cancelled) });
            } else {
              emitTimerEvent({ type: "cancel", reason: "application-clear", snapshot: cloneOptions(cancelled) });
            }
          }
          return nativeClearTimeout(timerId, ...args);
        };
        window.__sqagSmokeQuoteSessionTimerHooksInstalled = true;
      }
      window.__sqagSmokeGetPendingQuoteSessionDraftSave = () => {
        const current = window.__sqagSmokeQuoteSessionTimerState || {};
        return {
          documentId: String(current.documentId || ""),
          pending: current.pending ? cloneOptions(current.pending) : null,
          last: current.last ? cloneOptions(current.last) : null,
        };
      };
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init = {}) => {
        let url;
        try { url = new URL(typeof input === "string" ? input : input.url, window.location.href); } catch { return nativeFetch(input, init); }
        if (!url.pathname.startsWith("/api/")) return nativeFetch(input, init);
        let payload = null;
        try { payload = typeof init.body === "string" ? JSON.parse(init.body) : null; } catch { payload = null; }
        const method = String(init.method || input.method || "GET").toUpperCase();
        const scope = readScope();
        const sessionId = typeof payload?.session_id === "string"
          ? payload.session_id
          : (url.pathname.match(/^\/api\/quote-sessions\/([^/]+)/)?.[1] || String(scope.expectedSessionId || ""));
        const isQuoteSessionPost = method === "POST" && url.pathname === "/api/quote-sessions";
        // An application postJson invocation registered its transport attempt by exact body.
        const transportTagIndex = isQuoteSessionPost && typeof init.body === "string"
          ? transportTags.findIndex((tag) => tag.body === init.body)
          : -1;
        const transportTag = transportTagIndex >= 0 ? transportTags.splice(transportTagIndex, 1)[0] : null;
        const pendingIndex = isQuoteSessionPost && !transportTag
          ? pendingOperations.findIndex((item) => item.sessionId === sessionId || !item.sessionId)
          : -1;
        const pending = pendingIndex >= 0 ? pendingOperations.splice(pendingIndex, 1)[0] : null;
        const scopedOperation = !transportTag && !pending && scope.operationId && Number(scope.calls || 0) === 0
          ? String(scope.operationId) : "";
        const operationId = transportTag?.operationId || pending?.operationId || scopedOperation || nextOperationId(`${method.toLowerCase()}-api`);
        if (scopedOperation) {
          scope.calls = 1;
          sessionStorage.setItem(settings.operationStorageKey, JSON.stringify(scope));
        }
        const headers = new Headers(init.headers || (typeof Request !== "undefined" && input instanceof Request ? input.headers : undefined));
        headers.set(settings.operationHeader, operationId);
        const queuedSaveIdentity = transportTag ? transportTag.queuedSaveIdentity : pending?.queuedSaveIdentity;
        if (queuedSaveIdentity) headers.set(settings.queuedSaveHeader, String(queuedSaveIdentity));
        if (isQuoteSessionPost) {
          if (transportTag) headers.set(settings.transportAttemptHeader, String(transportTag.attempt));
          else if (!headers.has(settings.transportAttemptHeader)) headers.set(settings.transportAttemptHeader, "1");
          headers.set(settings.transportIdHeader, `${timerState.documentId}/transport-${++transportSequence}`);
        }
        headers.set(settings.fixtureHeader, String(scope.fixture || settings.group));
        headers.set(settings.operationNameHeader, String(scope.operation || operationId));
        headers.set(settings.sessionHeader, sessionId);
        if (scope.correlationToken) headers.set(settings.correlationHeader, String(scope.correlationToken));
        headers.set(settings.persistenceClassHeader, method === "POST" && url.pathname === "/api/quote-sessions"
          ? String(scope.persistenceClass || "REQUIRED_SUCCESS")
          : method === "GET" && (url.pathname === "/api/quote-sessions" || /^\/api\/quote-sessions\/[^/]+$/.test(url.pathname))
            ? String(scope.persistenceClass || "DIAGNOSTIC_ONLY")
            : "DIAGNOSTIC_ONLY");
        if (
          (method === "POST" && url.pathname === "/api/quote-sessions")
          || (method === "GET" && /^\/api\/quote-sessions\/[^/]+$/.test(url.pathname))
        ) timerState.apiTrace.push({ method, path: url.pathname, operationId, at: Date.now() });
        return nativeFetch(input, { ...init, headers });
      };

      const installSaveCapture = () => {
        const original = window.saveCurrentQuoteSession;
        if (typeof original === "function" && original.__sqagSmokeCaptured !== true) {
          const wrapped = async function (...args) {
            const scope = readScope();
            const callNumber = Number(scope.calls || 0) + 1;
            scope.calls = callNumber;
            sessionStorage.setItem(settings.operationStorageKey, JSON.stringify(scope));
            const options = args[0] && typeof args[0] === "object" ? args[0] : {};
            const sessionId = String(options.sessionId || window.state?.quoteSessionId || "");
            const timerState = window.__sqagSmokeQuoteSessionTimerState;
            const queuedCandidates = timerState.queuedSaveOperations.map((item) => ({
              identity: String(item.identity || ""),
              snapshotSessionId: String(item.snapshot?.sessionId || ""),
              requestStarted: item.requestStarted === true,
              sessionMatch: item.snapshot?.sessionId === sessionId,
              quoteGeneratedMatch: item.options?.quoteGenerated === options.quoteGenerated,
              draftStateMatch: !options.draftState || stableStringify(comparableDraftState(item.snapshot.draftState)) === stableStringify(comparableDraftState(options.draftState)),
              draftFilesMatch: !Array.isArray(options.draftFiles) || stableStringify(item.snapshot.draftFiles) === stableStringify(options.draftFiles),
            }));
            timerState.saveCurrentCalls.push({
              sessionId,
              activeTimerIdentity: String(timerState.activeTimerIdentity || ""),
              flushIntentIdentity: String(timerState.flushIntentIdentity || ""),
              queuedCandidates,
              startedAt: Date.now(),
            });
            const queuedSaveMatches = timerState.queuedSaveOperations.filter((item) => (
              item.requestStarted !== true
              && (!sessionId || item.snapshot?.sessionId === sessionId)
              && item.options?.quoteGenerated === options.quoteGenerated
              && (!options.draftState || stableStringify(comparableDraftState(item.snapshot.draftState)) === stableStringify(comparableDraftState(options.draftState)))
              && (!Array.isArray(options.draftFiles) || stableStringify(item.snapshot.draftFiles) === stableStringify(options.draftFiles))
            ));
            const queuedSaveOperation = queuedSaveMatches.length === 1 ? queuedSaveMatches[0] : null;
            if (queuedSaveOperation) queuedSaveOperation.requestStarted = true;
            const operationId = scope.operationId && callNumber === 1
              ? String(scope.operationId) : nextOperationId("save");
            const queuedSaveIdentity = String(queuedSaveOperation?.identity || "");
            pendingOperations.push({ operationId, sessionId, queuedSaveIdentity });
            try {
              const result = await original.apply(this, args);
              window[settings.resultStorageKey].push({
                operationId,
                nonNull: Boolean(result && typeof result === "object"),
                sessionId: String(result?.session_id || ""),
                quoteGenerated: typeof result?.status?.quote_generated === "boolean" ? result.status.quote_generated : null,
              });
              return result;
            } catch (error) {
              window[settings.resultStorageKey].push({ operationId, nonNull: false, sessionId: "", quoteGenerated: null });
              throw error;
            }
          };
          Object.defineProperty(wrapped, "__sqagSmokeCaptured", { value: true });
          window.saveCurrentQuoteSession = wrapped;
        }
        const draftSave = window.saveQuoteSessionDraftState;
        if (typeof draftSave === "function" && draftSave.__sqagSmokeCaptured !== true) {
          const wrappedDraftSave = async function (...args) {
            const timerState = window.__sqagSmokeQuoteSessionTimerState;
            const capturedOptions = captureEffectiveSaveOptions(args[0] && typeof args[0] === "object" ? args[0] : {});
            const options = capturedOptions.options;
            const requestedOptions = capturedOptions.requestedOptions;
            const timerIdentity = String(timerState.activeTimerIdentity || timerState.flushIntentIdentity || "");
            const priorSnapshot = timerIdentity
              ? (timerState.pending?.timerIdentity === timerIdentity
                ? timerState.pending
                : (timerState.last?.timerIdentity === timerIdentity ? timerState.last : {}))
              : {};
            const saveSnapshot = timerIdentity ? {
              ...cloneOptions(priorSnapshot),
              timerIdentity,
              options,
              requestedOptions,
              sessionId: String(options.sessionId || window.state?.quoteSessionId || priorSnapshot.sessionId || ""),
              draftState: cloneOptions(options.draftState || window.currentQuoteSessionDraftState?.()),
              draftFiles: cloneOptions(Array.isArray(options.draftFiles)
                ? options.draftFiles
                : (typeof window.sessionFileRecordsFromDraft === "function" ? window.sessionFileRecordsFromDraft() : [])),
            } : null;
            const queuedSaveOperation = timerIdentity
              ? { identity: timerIdentity, requestStarted: false, options, requestedOptions, snapshot: saveSnapshot }
              : null;
            if (queuedSaveOperation) timerState.queuedSaveOperations.push(queuedSaveOperation);
            timerState.saveCalls.push({ options, requestedOptions, timerIdentity, startedAt: Date.now() });
            let savePromise;
            try {
              savePromise = draftSave.apply(this, args);
            } catch (error) {
              if (timerIdentity) await emitTimerEvent({
                type: "save-start",
                timerIdentity,
                snapshot: saveSnapshot,
                invocationState: "threw-before-promise",
              });
              throw error;
            }
            if (timerIdentity) {
              await emitTimerEvent({
                type: "save-start",
                timerIdentity,
                snapshot: saveSnapshot,
                invocationState: "application-save-promise-established",
              });
            }
            try {
              const result = await savePromise;
              if (timerIdentity) await emitTimerEvent({
                type: "save-outcome", timerIdentity, outcomeKind: result ? "value" : "null",
                sessionId: String(result?.session_id || ""),
                quoteGenerated: typeof result?.status?.quote_generated === "boolean" ? result.status.quote_generated : null,
              });
              return result;
            } catch (error) {
              if (timerIdentity) await emitTimerEvent({ type: "save-outcome", timerIdentity, outcomeKind: "rejected", reason: String(error?.message || error) });
              throw error;
            } finally {
              if (queuedSaveOperation) {
                const index = timerState.queuedSaveOperations.indexOf(queuedSaveOperation);
                if (index >= 0) timerState.queuedSaveOperations.splice(index, 1);
              }
            }
          };
          Object.defineProperty(wrappedDraftSave, "__sqagSmokeCaptured", { value: true });
          window.saveQuoteSessionDraftState = wrappedDraftSave;
        }
        const queueSave = window.queueQuoteSessionDraftStateSave;
        if (typeof queueSave === "function" && queueSave.__sqagSmokeCaptured !== true) {
          const wrappedQueueSave = function (...args) {
            const options = args[0] && typeof args[0] === "object" ? cloneOptions(args[0]) : {};
            const current = window.__sqagSmokeQuoteSessionTimerState;
            current.queueOptions = options;
            current.queueCaptureDepth += 1;
            try {
              return queueSave.apply(this, args);
            } finally {
              current.queueCaptureDepth -= 1;
              current.queueOptions = null;
            }
          };
          Object.defineProperty(wrappedQueueSave, "__sqagSmokeCaptured", { value: true });
          window.queueQuoteSessionDraftStateSave = wrappedQueueSave;
        }
        const clearSaveTimer = window.clearQuoteSessionDraftSaveTimer;
        if (typeof clearSaveTimer === "function" && clearSaveTimer.__sqagSmokeCaptured !== true) {
          const wrappedClearSaveTimer = function (...args) {
            return clearSaveTimer.apply(this, args);
          };
          Object.defineProperty(wrappedClearSaveTimer, "__sqagSmokeCaptured", { value: true });
          window.clearQuoteSessionDraftSaveTimer = wrappedClearSaveTimer;
        }
        // postJson reuses one payload object for its initial POST and its single post-403 retry,
        // so the payload object identifies the invocation; each call is one transport attempt.
        const postTransport = window.fetchPostJsonResponse;
        if (typeof postTransport === "function" && postTransport.__sqagSmokeCaptured !== true) {
          const transportInvocations = new WeakMap();
          const wrappedPostTransport = function (url, payload, ...rest) {
            let target = "";
            try { target = new URL(String(url), window.location.href).pathname; } catch { target = ""; }
            if (target !== "/api/quote-sessions" || !payload || typeof payload !== "object") {
              return postTransport.call(this, url, payload, ...rest);
            }
            let invocation = transportInvocations.get(payload);
            if (!invocation) {
              const sessionId = typeof payload.session_id === "string" ? payload.session_id : "";
              const pendingIndex = pendingOperations.findIndex((item) => item.sessionId === sessionId || !item.sessionId);
              const pending = pendingIndex >= 0 ? pendingOperations.splice(pendingIndex, 1)[0] : null;
              const scope = readScope();
              const scopedOperation = !pending && scope.operationId && Number(scope.calls || 0) === 0
                ? String(scope.operationId) : "";
              if (scopedOperation) {
                scope.calls = 1;
                sessionStorage.setItem(settings.operationStorageKey, JSON.stringify(scope));
              }
              invocation = {
                operationId: pending?.operationId || scopedOperation || nextOperationId("post-api"),
                queuedSaveIdentity: String(pending?.queuedSaveIdentity || ""),
                attempts: 0,
              };
              transportInvocations.set(payload, invocation);
            }
            invocation.attempts += 1;
            const tag = {
              body: JSON.stringify(payload),
              operationId: invocation.operationId,
              queuedSaveIdentity: invocation.queuedSaveIdentity,
              attempt: invocation.attempts,
            };
            transportTags.push(tag);
            const releaseTag = () => {
              const index = transportTags.indexOf(tag);
              if (index >= 0) transportTags.splice(index, 1);
            };
            try {
              const result = postTransport.call(this, url, payload, ...rest);
              Promise.resolve(result).then(releaseTag, releaseTag);
              return result;
            } catch (error) {
              releaseTag();
              throw error;
            }
          };
          Object.defineProperty(wrappedPostTransport, "__sqagSmokeCaptured", { value: true });
          window.fetchPostJsonResponse = wrappedPostTransport;
        }
      };
      window.__sqagSmokeInstallQuoteSessionCapture = installSaveCapture;
      document.addEventListener("DOMContentLoaded", installSaveCapture, { once: true });
      window.addEventListener("load", installSaveCapture, { once: true });
      setTimeout(installSaveCapture, 0);
    }, {
      group,
      operationStorageKey: quoteSessionOperationStorageKey,
      resultStorageKey: quoteSessionSaveResultsKey,
      operationHeader: quoteSessionOperationHeader,
      fixtureHeader: quoteSessionFixtureHeader,
      operationNameHeader: quoteSessionOperationNameHeader,
      sessionHeader: quoteSessionSessionHeader,
      correlationHeader: quoteSessionCorrelationHeader,
      persistenceClassHeader: quoteSessionPersistenceClassHeader,
      queuedSaveHeader: quoteSessionQueuedSaveHeader,
      transportAttemptHeader: quoteSessionTransportAttemptHeader,
      transportIdHeader: quoteSessionTransportIdHeader,
      timerEventBinding,
    });
    const attachPage = (page) => {
      ensurePageIdentity(page);
      page.on("request", observeRequest);
      page.on("request", observeDetailRequest);
      page.on("request", observeListRequest);
      page.on("request", observeSessionRefreshRequest);
      page.on("response", observeResponse);
      page.on("response", observeSessionRefreshResponse);
      page.on("requestfailed", observeFailure);
      page.on("requestfailed", (request) => sessionRefreshByRequest.get(request)?.resolveBody({ ok: false, csrfHeader: "", tokenDigest: "", recoveryScopePresent: false }));
      page.on("close", () => {
        abortOwnProofsForPage(page, "page closed while a queued save's own-proof wait was outstanding");
        markPageWorkUnresolved(page, "page closed with unresolved queued quote-session save work");
      });
      page.on("framenavigated", (frame) => {
        if (frame === page.mainFrame()) {
          abortOwnProofsForPage(page, "document navigated while a queued save's own-proof wait was outstanding");
          const currentKey = currentDocumentByPage.get(page);
          if (currentKey) retireDocumentEpisode(documentEpisodes.get(currentKey), "document navigated with unresolved queued quote-session save work");
          markPageWorkUnresolved(page, "document navigated with unresolved queued quote-session save work");
        }
      });
    };
    for (const page of context.pages()) attachPage(page);
    context.on("page", attachPage);
  };

  const setPageOperation = async (page, { fixture, operation, operationId, expectedSessionId, persistenceClass = "REQUIRED_SUCCESS" }) => {
    const correlationToken = `${group}/correlation/${Date.now()}-${++operationSequence}`;
    await page.evaluate(({ key, value }) => sessionStorage.setItem(key, JSON.stringify(value)), {
      key: quoteSessionOperationStorageKey,
      value: { fixture, operation, operationId, expectedSessionId, persistenceClass, correlationToken, calls: 0 },
    });
    await page.evaluate(() => window.__sqagSmokeInstallQuoteSessionCapture?.());
    return correlationToken;
  };

  const requiredDetailReadback = async (page, sessionId, expectedFields = {}) => {
    const operationId = `${group}/dashboard-detail-${Date.now()}-${++operationSequence}`;
    detailExpectations.set(operationId, expectedFields);
    await setPageOperation(page, {
      fixture: group,
      operation: "dashboard-detail-readback",
      operationId,
      expectedSessionId: sessionId,
      persistenceClass: "REQUIRED_SUCCESS",
    });
    const detail = await page.evaluate(async (safeSessionId) => {
      const response = await fetch(`/api/quote-sessions/${encodeURIComponent(safeSessionId)}`);
      const body = await response.json();
      return { httpStatus: response.status, body };
    }, sessionId);
    await drain();
    if (detail.httpStatus !== 200 || !detail.body?.quote_session) {
      throw new Error(`Dashboard quote-session detail readback failed: ${JSON.stringify(detail)}.`);
    }
    return detail.body;
  };

  const requiredDashboardRefresh = async (page, sessionId, expectedFields = {}) => {
    const operationId = `${group}/dashboard-list-${Date.now()}-${++operationSequence}`;
    detailExpectations.set(operationId, expectedFields);
    await setPageOperation(page, {
      fixture: group,
      operation: "dashboard-list-refresh",
      operationId,
      expectedSessionId: sessionId,
      persistenceClass: "REQUIRED_SUCCESS",
    });
    const dashboard = await page.evaluate(async (safeSessionId) => {
      await loadQuoteDashboard({ showLoading: false, preserveViewState: true });
      return {
        session: (state.quoteSessions || []).find((candidate) => candidate?.session_id === safeSessionId) || null,
      };
    }, sessionId);
    await drain();
    if (!dashboard.session || dashboard.session.session_id !== sessionId) {
      throw new Error(`Dashboard quote-session list refresh lost the exact session: ${JSON.stringify(dashboard)}.`);
    }
    return dashboard.session;
  };

  const setPageDiagnostic = async (page, operation, expectedSessionId = "") => {
    const operationId = typeof operation === "string" ? `${group}/${operation}-${Date.now()}-${++operationSequence}` : String(operation.operationId || `${group}/diagnostic-${Date.now()}-${++operationSequence}`);
    const operationName = typeof operation === "string" ? operation : String(operation.operation || operationId);
    const correlationToken = `${group}/diagnostic-correlation/${Date.now()}-${++operationSequence}`;
    await page.evaluate(({ key, value }) => sessionStorage.setItem(key, JSON.stringify(value)), {
      key: quoteSessionOperationStorageKey,
      value: { fixture: group, operation: operationName, operationId, expectedSessionId, correlationToken, persistenceClass: "DIAGNOSTIC_ONLY", calls: 0 },
    });
    await page.evaluate(() => window.__sqagSmokeInstallQuoteSessionCapture?.());
    return { operationId, correlationToken };
  };

  const readQueuedSaveTimer = async (page, deadlineRun = null) => {
    if (!page || page.isClosed()) return null;
    const awaitOperation = (phase, operation) => deadlineRun ? deadlineRun.await(phase, operation) : operation;
    await awaitOperation("timer-event-delivery-acknowledgement", page.evaluate(async () => {
      const pending = [...(window.__sqagSmokeQuoteSessionTimerState?.pendingEventPromises || [])];
      await Promise.all(pending);
    }));
    return awaitOperation("page-timer-state-query", page.evaluate(() => {
      if (typeof window.__sqagSmokeGetPendingQuoteSessionDraftSave !== "function") {
        throw new Error("Quote-session timer instrumentation was not installed.");
      }
      return {
        ...window.__sqagSmokeGetPendingQuoteSessionDraftSave(),
        eventDeliveryFailures: (window.__sqagSmokeQuoteSessionTimerState?.eventDeliveryFailures || []).map((failure) => ({ ...failure })),
      };
    }));
  };

  const syncQueuedSaveTimer = async (page, deadlineRun = null) => {
    if (!page || page.isClosed()) return null;
    const state = await readQueuedSaveTimer(page, deadlineRun);
    const pageIdentity = ensurePageIdentity(page);
    const episode = getDocumentEpisode(page, state?.documentId);
    for (const failure of state?.eventDeliveryFailures || []) {
      recordTimerEventDeliveryFailure(page, {
        type: failure.type,
        timerIdentity: failure.timerIdentity,
        documentId: state?.documentId,
      }, failure.reason || "unknown binding failure");
    }
    const snapshot = state?.pending?.active ? normalizeTimerSnapshot(page, state.pending) : null;
    let entry = snapshot ? registerQueuedSaveWork(snapshot, snapshot.supersedesTimerIdentity) : null;
    const last = state?.last;
    if (last?.timerIdentity) {
      const lastIdentity = normalizeTimerIdentity(page, last.timerIdentity);
      const prior = queuedSaveHistory.get(lastIdentity);
      if (
        prior?.status === "pending"
        && !last.active
        && last.clearedAt
        && !last.firedAt
        && !last.flushedAt
        && !last.cancelledForRecovery
        && !prior.cancelledForApplication
        && !prior.ownedFlushLease
      ) {
        failQueuedSave(prior, "queued save timer disappeared without a flush, firing, successor, or persistence proof");
      }
    }
    if (snapshot && entry?.status === "pending" && state.documentId && snapshot.documentId !== state.documentId) {
      failQueuedSave(entry, "queued save timer document identity did not match its captured page snapshot");
    }
    if (episode && !snapshot) {
      const pendingForDocument = [...pendingQueuedSaveWork.values()].some((candidate) => (
        candidate.snapshot.pageIdentity === pageIdentity
        && String(candidate.snapshot.documentId || "") === episode.documentId
        && candidate.status === "pending"
      ));
      const unresolvedEpisodeWork = [...episode.work.values()].some((item) => ["pending", "unknown", "failed"].includes(item.state));
      if (!episode.unknownRequiredWork && !pendingForDocument && !unresolvedEpisodeWork) {
        episode.zeroProof = {
          at: Date.now(),
          source: "successful-page-timer-reconciliation",
          noActiveNativeTimer: true,
          outstandingKnownWork: 0,
        };
        episode.state = episode.work.size ? "proven-complete" : "proven-zero-work";
        episode.lastObservedAt = episode.zeroProof.at;
      }
    }
    return entry;
  };

  const capturePendingQueuedSave = async (page) => {
    const state = await readQueuedSaveTimer(page);
    if (!state?.pending) return null;
    const rawSnapshot = state.pending;
    const snapshot = normalizeTimerSnapshot(page, rawSnapshot);
    const entry = snapshot ? registerQueuedSaveWork(snapshot, snapshot.supersedesTimerIdentity) : null;
    const failCapture = (reason) => {
      if (entry?.status === "pending") failQueuedSave(entry, reason);
      else if (!entry) {
        addIssue({ operationId: rawSnapshot.timerIdentity || "<queued-save>", fixture: group, operation: "queued-save" }, reason);
      }
      throw new Error(`Pending quote-session save timer was not capturable: ${JSON.stringify(state)}.`);
    };
    if (
      rawSnapshot.active !== true
      || !rawSnapshot.options
      || typeof rawSnapshot.options !== "object"
      || typeof rawSnapshot.options.quoteGenerated !== "boolean"
      || !rawSnapshot.timerIdentity
      || !rawSnapshot.sessionId
      || !rawSnapshot.draftState
      || !Array.isArray(rawSnapshot.draftFiles)
    ) {
      return failCapture("pending quote-session save timer was captured without exact identity, session, state, files, and required options");
    }
    if (!snapshot || !entry || entry.status !== "pending") {
      throw new Error("Pending quote-session save timer could not be registered as live required work.");
    }
    return { snapshot, entry };
  };

  const unproducedQueuedSaveWork = () => [...pendingQueuedSaveWork.values()].find((entry) => (
    !entry.producer || entry.producer === entry.terminalPromise
  )) || null;

  const startCapturedQueuedSave = async (page, captured, { cancelNativeTimer = true } = {}) => {
    if (!captured?.snapshot?.browserTimerIdentity || !captured?.snapshot?.options) {
      throw new Error("Captured queued save has no browser timer identity or options.");
    }
    await syncQueuedSaveTimer(page);
    const timerStateBeforeFlush = await readQueuedSaveTimer(page);
    const pendingBeforeFlush = timerStateBeforeFlush?.pending?.active ? timerStateBeforeFlush.pending : null;
    if (pendingBeforeFlush && pendingBeforeFlush.timerIdentity !== captured.snapshot.browserTimerIdentity) {
      return { status: "replaced", currentTimerIdentity: String(pendingBeforeFlush.timerIdentity || "") };
    }
    const lastBeforeFlush = timerStateBeforeFlush?.last;
    const cancelledForRecovery = !pendingBeforeFlush
      && lastBeforeFlush?.timerIdentity === captured.snapshot.browserTimerIdentity
      && lastBeforeFlush.cancelledForRecovery === true;
    if (!pendingBeforeFlush && !cancelledForRecovery) {
      return { status: "unavailable", reason: "captured timer is no longer live and has no recovery-transition flush authority" };
    }
    const entry = captured.entry || queuedSaveHistory.get(String(captured.snapshot.timerIdentity || ""));
    if (!entry || entry.status !== "pending") {
      return { status: "unavailable", reason: "captured queued-save obligation is already terminal" };
    }
    registerOwnedQueuedSaveFlush(captured);
    const started = await page.evaluate(async ({ browserTimerIdentity, options, cancelNativeTimer: shouldCancelNativeTimer }) => {
      const timerState = window.__sqagSmokeQuoteSessionTimerState;
      if (!timerState) return { status: "unavailable", reason: "timer instrumentation is missing" };
      const pending = timerState.pending?.active ? timerState.pending : null;
      if (pending && pending.timerIdentity !== browserTimerIdentity) {
        return { status: "replaced", currentTimerIdentity: String(pending.timerIdentity || "") };
      }
      const last = timerState.last;
      if (!pending && !(last?.timerIdentity === browserTimerIdentity && last.cancelledForRecovery === true)) {
        return { status: "unavailable", reason: "captured timer is no longer live and has no recovery-transition flush authority" };
      }
      const previousIdentity = String(timerState.activeTimerIdentity || "");
      timerState.activeTimerIdentity = browserTimerIdentity;
      let savePromise;
      try {
        savePromise = saveQuoteSessionDraftState(options);
      } catch (error) {
        timerState.activeTimerIdentity = previousIdentity;
        return { status: "threw", reason: String(error?.message || error) };
      }
      timerState.activeTimerIdentity = previousIdentity;
      timerState.flushPromises ||= Object.create(null);
      timerState.flushPromises[browserTimerIdentity] = Promise.resolve(savePromise).then(
        (result) => ({
          kind: result ? "value" : "null",
          sessionId: String(result?.session_id || ""),
          quoteGenerated: typeof result?.status?.quote_generated === "boolean" ? result.status.quote_generated : null,
        }),
        (error) => ({ kind: "rejected", reason: String(error?.message || error) }),
      );
      const startAcknowledged = await timerState.lastEventPromise;
      if (startAcknowledged !== true) return { status: "delivery-failed", timerIdentity: browserTimerIdentity };
      if (pending && shouldCancelNativeTimer) {
        timerState.flushIntentIdentity = browserTimerIdentity;
        clearQuoteSessionDraftSaveTimer();
        timerState.flushIntentIdentity = "";
      }
      return {
        status: "started",
        timerIdentity: browserTimerIdentity,
        executableSuccessorEstablished: true,
        nativeTimerRetained: Boolean(pending && !shouldCancelNativeTimer),
      };
    }, { browserTimerIdentity: captured.snapshot.browserTimerIdentity, options: captured.snapshot.options, cancelNativeTimer });
    await syncQueuedSaveTimer(page);
    if (started.status !== "started" && entry.status === "pending") {
      if (entry.ownedFlushLease?.state === "reserved") {
        if (entry.nativeTimerLease && !entry.nativeTimerLease.settled) {
          entry.ownedFlushLease.resolve({ status: "abandoned-before-start" });
          entry.ownedFlushLease = null;
          entry.flushOperation = { ...(entry.flushOperation || {}), state: "FLUSH_OWNERSHIP_ABANDONED", at: Date.now() };
        } else {
          failQueuedSave(entry, "ORPHAN_NO_REAL_PRODUCER: owned flush reservation failed before a native timer or executable successor remained");
        }
      }
    }
    return started;
  };

  const startRecoveryFlushOnActualCancellation = async (page, captured) => {
    if (!captured?.snapshot?.browserTimerIdentity || !captured?.snapshot?.options) {
      throw new Error("Recovery flush continuation has no captured browser timer identity or options.");
    }
    const entry = captured.entry || queuedSaveHistory.get(String(captured.snapshot.timerIdentity || ""));
    if (!entry || entry.status !== "pending") return { status: "unavailable", reason: "captured obligation is not pending" };
    registerOwnedQueuedSaveFlush(captured, "F2 executable recovery-save continuation bound to actual timer cancellation");
    const armed = await page.evaluate(({ browserTimerIdentity, options }) => {
      const timerState = window.__sqagSmokeQuoteSessionTimerState;
      if (!timerState?.pending?.active || timerState.pending.timerIdentity !== browserTimerIdentity) {
        return { status: "unavailable", reason: "exact native timer is not active" };
      }
      timerState.recoveryFlushContinuations ||= Object.create(null);
      timerState.recoveryFlushContinuations[browserTimerIdentity] = () => {
        const priorIdentity = String(timerState.activeTimerIdentity || "");
        timerState.activeTimerIdentity = browserTimerIdentity;
        let savePromise;
        try {
          savePromise = saveQuoteSessionDraftState(options);
        } finally {
          timerState.activeTimerIdentity = priorIdentity;
        }
        timerState.flushPromises ||= Object.create(null);
        timerState.flushPromises[browserTimerIdentity] = Promise.resolve(savePromise).then(
          (result) => ({
            kind: result ? "value" : "null",
            sessionId: String(result?.session_id || ""),
            quoteGenerated: typeof result?.status?.quote_generated === "boolean" ? result.status.quote_generated : null,
          }),
          (error) => ({ kind: "rejected", reason: String(error?.message || error) }),
        );
        return timerState.flushPromises[browserTimerIdentity];
      };
      return {
        status: "armed",
        timerIdentity: browserTimerIdentity,
        actualNativeTimerActive: true,
        callbackBoundToActualClearTimeout: true,
      };
    }, { browserTimerIdentity: captured.snapshot.browserTimerIdentity, options: captured.snapshot.options });
    if (armed.status === "armed") {
      entry.recoveryContinuation = {
        state: "executable-continuation-established",
        identity: entry.identity,
        browserTimerIdentity: captured.snapshot.browserTimerIdentity,
        at: Date.now(),
      };
      entry.transitions.push({ type: "recovery-executable-continuation-established", at: Date.now(), identity: entry.identity });
      notifyProgress();
    }
    return armed;
  };

  const outstandingOperations = () => {
    const operations = [];
    for (const task of tasks) operations.push({ identity: `${group}/response-task`, state: "response-processing", producer: task });
    for (const record of pendingRequiredRequests) operations.push({ identity: record.operationId, state: "request-pending", producer: record.terminalProducer ? record.terminalPromise : null });
    for (const record of pendingResponseBodies) operations.push({ identity: `${record.operationId}/response-body`, state: "response-body-pending", producer: record.responseBodyProducer || null });
    for (const record of pendingReadbacks) operations.push({ identity: `${record.operationId}/durable-readback`, state: "readback-pending", producer: record.readbackProducer || null });
    for (const record of pendingOwnProofs) operations.push({ identity: `${record.operationId}/own-proof`, state: "own-proof-pending", producer: record.ownProofProducer || null });
    for (const record of pendingSavePromises) operations.push({ identity: `${record.operationId}/application-save`, state: "application-save-pending", producer: record.terminalProducer ? record.terminalPromise : null });
    for (const job of pendingRequiredJobs) operations.push({ identity: job.identity, state: "required-job-pending", producer: job.producer });
    for (const entry of pendingQueuedSaveWork.values()) operations.push({
      identity: entry.identity,
      timerIdentity: String(entry.snapshot.timerIdentity || ""),
      documentIdentity: documentEpisodeKey(entry.snapshot.pageIdentity || "<unknown-page>", entry.snapshot.documentId || "<unknown-document>"),
      state: entry.status,
      producerKind: entry.producerKind || entry.lastProducerKind || "",
      producerState: entry.producerState || "none",
      flushState: entry.flushOperation?.state || "none",
      producer: entry.producer === entry.terminalPromise ? null : entry.producer,
    });
    return operations;
  };

  const reconcileBrowserTimers = async (page, deadlineRun = null) => {
    const pages = page ? [page] : [...attachedPages];
    for (const candidate of pages) {
      if (!candidate || candidate.isClosed()) continue;
      const synchronization = syncQueuedSaveTimer(candidate, deadlineRun);
      await synchronization;
    }
  };

  const armFinalReconciliationBarrier = () => {
    let reachedResolve;
    let releaseResolve;
    const reached = new Promise((resolve) => { reachedResolve = resolve; });
    const gate = new Promise((resolve) => { releaseResolve = resolve; });
    finalReconciliationBarriers.push({ reachedResolve, gate });
    return { reached, release: releaseResolve };
  };

  const pauseAtFinalReconciliation = async (deadlineRun = null) => {
    const barrier = finalReconciliationBarriers.shift();
    if (!barrier) return;
    barrier.reachedResolve({ generation: progressGeneration });
    if (deadlineRun) await deadlineRun.await("final-zero-barrier", barrier.gate);
    else await barrier.gate;
  };

  const drain = async (timeoutMs = 30000, page = null) => {
    const startedAt = performance.now();
    const budgetMs = Math.max(0, Number.isFinite(Number(timeoutMs)) ? Number(timeoutMs) : 0);
    const deadlineAt = startedAt + budgetMs;
    const deadlineIdentity = `${group}/drain-${++drainInvocationSequence}`;
    const deadlineError = new Error(`required drain absolute deadline expired (${deadlineIdentity})`);
    let resolveDeadline;
    let deadlineTimer;
    const deadlineSignal = new Promise((resolve) => { resolveDeadline = resolve; });
    const drainRun = {
      identity: deadlineIdentity,
      budgetMs,
      startedAt,
      deadlineAt,
      phase: "initializing",
      expired: false,
      completed: false,
      await: async (phase, operation) => {
        drainRun.phase = String(phase || "unspecified-await");
        activeDrainRuns.set(deadlineIdentity, {
          identity: deadlineIdentity,
          budgetMs,
          startedAtMonotonicMs: startedAt,
          deadlineMonotonicMs: deadlineAt,
          phase: drainRun.phase,
        });
        if (drainRun.expired) throw deadlineError;
        if (deadlineAt - performance.now() <= 0) {
          expireDrain();
          throw deadlineError;
        }
        const pending = Promise.resolve(operation).then(
          (value) => ({ type: "value", value }),
          (error) => ({ type: "error", error }),
        );
        const outcome = await Promise.race([
          pending,
          deadlineSignal.then(() => ({ type: "deadline" })),
        ]);
        if (outcome.type === "deadline" || drainRun.expired) throw deadlineError;
        if (outcome.type === "error") throw outcome.error;
        if (deadlineAt - performance.now() <= 0) {
          expireDrain();
          throw deadlineError;
        }
        return outcome.value;
      },
    };
    activeDrainRuns.set(deadlineIdentity, {
      identity: deadlineIdentity,
      budgetMs,
      startedAtMonotonicMs: startedAt,
      deadlineMonotonicMs: deadlineAt,
      phase: drainRun.phase,
    });
    const evidenceForOutstanding = () => outstandingOperations().map((operation) => ({
      identity: operation.identity,
      timerIdentity: operation.timerIdentity || "",
      documentIdentity: operation.documentIdentity || "",
      state: operation.state || "pending",
      producerKind: operation.producerKind || "",
      producerState: operation.producerState || (operation.producer ? "started" : "none"),
      flushState: operation.flushState || "",
    }));
    const finishDrain = (outcome, extra = {}) => {
      if (drainRun.expired) return false;
      if (deadlineAt - performance.now() <= 0) {
        expireDrain();
        return false;
      }
      drainRun.completed = true;
      activeDrainRuns.delete(deadlineIdentity);
      if (deadlineTimer) clearTimeout(deadlineTimer);
      drainReports.push({
        identity: deadlineIdentity,
        outcome,
        budgetMs,
        startedAtMonotonicMs: startedAt,
        deadlineMonotonicMs: deadlineAt,
        finishedAtMonotonicMs: performance.now(),
        phase: drainRun.phase,
        outstanding: evidenceForOutstanding(),
        ...extra,
      });
      return outcome === "success";
    };
    const expireDrain = () => {
      if (drainRun.expired || drainRun.completed) return;
      drainRun.expired = true;
      activeDrainRuns.delete(deadlineIdentity);
      const outstanding = evidenceForOutstanding();
      const report = {
        identity: deadlineIdentity,
        outcome: "deadline-failure",
        budgetMs,
        startedAtMonotonicMs: startedAt,
        deadlineMonotonicMs: deadlineAt,
        expiredAtMonotonicMs: performance.now(),
        phase: drainRun.phase,
        outstanding,
        deadlineUnchanged: true,
      };
      drainReports.push(report);
      addIssue(
        { operationId: deadlineIdentity, fixture: group, operation: "required-drain-deadline" },
        `required drain absolute deadline expired; deadlineIdentity=${deadlineIdentity}; budgetMs=${budgetMs}; phase=${drainRun.phase}; outstanding=${JSON.stringify(outstanding)}`,
      );
      resolveDeadline({ type: "deadline", report });
    };
    deadlineTimer = setTimeout(expireDrain, budgetMs);
    while (true) {
      const waiter = createProgressWaiter();
      const generationBeforeSnapshot = progressGeneration;
      try {
        drainRun.phase = "browser-reconciliation";
        await reconcileBrowserTimers(page, drainRun);
      } catch (error) {
        waiter.cancel();
        if (error === deadlineError || drainRun.expired) return false;
        addIssue({ operationId: "<drain>", fixture: group, operation: "required-drain" }, `browser timer reconciliation failed: ${error?.message || error}`);
        return finishDrain("failure", { reason: "browser timer reconciliation failed" });
      }
      const generationAfterSnapshot = progressGeneration;
      const outstanding = outstandingOperations();
      const missingProducer = outstanding.filter((operation) => !operation.producer);
      if (missingProducer.length) {
        waiter.cancel();
        addIssue(
          { operationId: "<drain>", fixture: group, operation: "required-drain" },
          `required operation has no terminal completion producer: ${missingProducer.map((operation) => operation.identity).join(", ")}`,
        );
        return finishDrain("failure", { reason: "required operation had no executable producer" });
      }
      if (!outstanding.length) {
        try {
          drainRun.phase = "final-zero-barrier";
          await pauseAtFinalReconciliation(drainRun);
          drainRun.phase = "final-zero-reconciliation";
          await reconcileBrowserTimers(page, drainRun);
        } catch (error) {
          waiter.cancel();
          if (error === deadlineError || drainRun.expired) return false;
          addIssue({ operationId: "<drain>", fixture: group, operation: "required-drain" }, `final browser timer reconciliation failed: ${error?.message || error}`);
          return finishDrain("failure", { reason: "final-zero reconciliation failed" });
        }
        const finalOutstanding = outstandingOperations();
        if (progressGeneration !== generationAfterSnapshot || finalOutstanding.length) {
          waiter.cancel();
          continue;
        }
        waiter.cancel();
        return finishDrain(issues.length === 0 ? "success" : "failure", { reason: issues.length ? "sticky tracker issues remain" : "zero-work barrier completed" });
      }
      if (progressGeneration !== generationBeforeSnapshot && generationAfterSnapshot === generationBeforeSnapshot) {
        waiter.cancel();
        continue;
      }
      if (progressGeneration !== generationAfterSnapshot) {
        waiter.cancel();
        continue;
      }
      notifyDrainWaiting();
      try {
        await drainRun.await("progress-wait", waiter.promise);
      } catch (error) {
        waiter.cancel();
        drainWaiting = false;
        if (error === deadlineError || drainRun.expired) return false;
        addIssue({ operationId: "<drain>", fixture: group, operation: "required-drain" }, `required drain progress wait failed: ${error?.message || error}`);
        return finishDrain("failure", { reason: "progress wait failed" });
      }
      drainWaiting = false;
    }
  };

  const assert = async () => {
    const drained = await drain();
    if (!drained || issues.length) {
      throw new Error(`Quote-session drain contract failed: ${JSON.stringify(issues.slice(0, 12))}`);
    }
    return summary();
  };

  const queuedSaveStatus = (identity) => queuedSaveHistory.get(String(identity || "")) || null;

  const summary = () => ({
    requiredSaveCount: records.filter((record) => record.persistenceClass !== "DIAGNOSTIC_ONLY").length,
    requiredSaveStatuses: records.filter((record) => record.persistenceClass !== "DIAGNOSTIC_ONLY").map((record) => record.httpStatus),
    diagnosticRequestCount: records.filter((record) => record.persistenceClass === "DIAGNOSTIC_ONLY").length,
    issues: [...issues],
    queuedSaveStates: [...queuedSaveHistory.values()].map((entry) => ({
      identity: entry.identity,
      timerIdentity: entry.snapshot.timerIdentity,
      status: entry.status,
      producerKind: entry.producerKind || entry.lastProducerKind || "",
      hasProducer: Boolean(entry.producer),
      producerState: entry.producerState || "none",
      successorIdentity: entry.successorIdentity || "",
      flushOperation: entry.flushOperation ? { ...entry.flushOperation } : null,
      transitions: entry.transitions.map((transition) => ({ ...transition })),
      terminalReason: entry.terminalReason || "",
      postObserved: entry.evidence.postObserved === true,
      durableReadbackObserved: entry.evidence.durableReadbackObserved === true,
      saveInvocationObserved: Boolean(entry.saveInvocationSnapshot),
      correlation: entry.correlationEvidence ? { ...entry.correlationEvidence } : null,
      readbackSupersession: entry.evidence.readbackSupersession ? { ...entry.evidence.readbackSupersession } : null,
      requestOwnership: entry.ownership ? {
        state: entry.ownership.state,
        ownerOperationId: entry.ownership.ownerRecord?.operationId || "",
        currentTransportId: entry.ownership.currentAttempt?.transportId || "",
        claims: entry.ownership.claims.map((claim) => ({ ...claim })),
        sticky: entry.ownership.sticky.map((violation) => ({ ...violation })),
      } : null,
    })),
    drainWaitCount,
    drainRuns: drainReports.map((report) => ({ ...report, outstanding: report.outstanding.map((item) => ({ ...item })) })),
    activeDrainRuns: [...activeDrainRuns.values()].map((run) => ({ ...run })),
    documentEpisodes: [...documentEpisodes.values()].map((episode) => ({
      identity: episode.identity,
      pageIdentity: episode.pageIdentity,
      documentId: episode.documentId,
      state: episode.state,
      current: episode.current === true,
      zeroProof: episode.zeroProof ? { ...episode.zeroProof } : null,
      unknownRequiredWork: episode.unknownRequiredWork === true,
      work: [...episode.work.values()].map((work) => ({ ...work })),
      deliveryFailures: episode.deliveryFailures.map((failure) => ({ ...failure })),
      lifecycle: episode.lifecycle.map((event) => ({ ...event })),
    })),
    outstanding: {
      requiredRequests: [...pendingRequiredRequests].length,
      requiredResponseBodies: [...pendingResponseBodies].length,
      requiredDurableReadbacks: [...pendingReadbacks].length,
      requiredOwnProofs: [...pendingOwnProofs].length,
      requiredSavePromises: [...pendingSavePromises].length,
      nonterminalRequiredJobs: [...pendingRequiredJobs].length,
      queuedRequiredSaves: [...pendingQueuedSaveWork.values()].map((entry) => entry.identity),
      progressGeneration,
    },
  });

  return {
    install,
    setPageOperation,
    setPageDiagnostic,
    waitForDrainWaiting,
    requiredDetailReadback,
    requiredDashboardRefresh,
    trackRequiredJob,
    readQueuedSaveTimer,
    syncQueuedSaveTimer,
    capturePendingQueuedSave,
    unproducedQueuedSaveWork,
    attachQueuedSaveProducer,
    registerOwnedQueuedSaveFlush: registerOwnedQueuedSaveFlush,
    startRecoveryFlushOnActualCancellation,
    faultNextTimerEventDelivery,
    holdTimerEventAcknowledgement,
    holdTimerEventHandling,
    holdDurableReadback,
    holdOwnProof,
    documentEpisodes,
    drainReports,
    activeDrainRuns,
    queuedSaveStatus,
    startCapturedQueuedSave,
    armFinalReconciliationBarrier,
    drain,
    assert,
    summary,
    records,
    issues,
  };
}

async function flushRequiredQuoteSessionSaves(page, tracker, capturedOverride = null) {
  const captured = capturedOverride || await tracker.capturePendingQueuedSave(page);
  if (captured) {
    const start = await tracker.startCapturedQueuedSave(page, captured);
    if (start.status === "replaced") {
      const successor = await tracker.capturePendingQueuedSave(page);
      if (successor) return flushRequiredQuoteSessionSaves(page, tracker, successor);
      const drainedAfterSupersession = await tracker.drain(30000, page);
      if (!drainedAfterSupersession) {
        throw new Error(`Captured queued save was superseded but required successor work did not prove persistence: ${JSON.stringify(tracker.summary())}.`);
      }
      return { status: "superseded", timerIdentity: captured.snapshot.timerIdentity };
    }
    if (start.status !== "started") {
      throw new Error(`Captured queued save could not be safely flushed: ${JSON.stringify(start)}.`);
    }
    const outcome = await page.evaluate(async (identity) => {
      const timerState = window.__sqagSmokeQuoteSessionTimerState || {};
      const pending = [quoteSessionInitialSavePromise, quoteSessionDraftSavePromise].filter(Boolean);
      await Promise.all(pending);
      return await timerState.flushPromises?.[identity];
    }, captured.snapshot.browserTimerIdentity);
    const drained = await tracker.drain(30000, page);
    if (!drained) {
      throw new Error(`Required queued save did not reach validated durable persistence (${outcome?.kind || "unknown outcome"}): ${JSON.stringify(tracker.summary())}.`);
    }
    return { status: "succeeded", timerIdentity: captured.snapshot.timerIdentity, outcome };
  }
  await page.evaluate(async () => {
    const pending = [quoteSessionInitialSavePromise, quoteSessionDraftSavePromise].filter(Boolean);
    await Promise.all(pending);
  });
  const drained = await tracker.drain(30000, page);
  if (!drained) throw new Error(`Required quote-session work did not prove durable persistence: ${JSON.stringify(tracker.summary())}.`);
  return { status: "drained" };
}

function pythonCommand() {
  if (process.env.PYTHON) return process.env.PYTHON;
  if (process.platform !== "win32") return "python3";
  const bundled = path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe");
  if (fsSync.existsSync(bundled)) return bundled;
  return "python";
}

async function healthOk(url = baseUrl) {
  try {
    const response = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(1200) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(timeoutMs = 15000, url = baseUrl) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await healthOk(url)) return true;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

function startServer({
  host = options.host,
  port = options.port,
  dataRoot = process.env.QUOTE_DATA_ROOT || quoteDataRoot,
  syntheticRoot = null,
  logRoot = null,
} = {}) {
  const server = spawn(
    pythonCommand(),
    ["webapp/server.py", "--host", host, "--port", String(port)],
    {
      cwd: root,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
        APP_MODE: "local",
        QUOTE_DATA_ROOT: dataRoot,
        ...(syntheticRoot ? {
          QUOTE_OUTPUT_ROOT: path.join(syntheticRoot, "output"),
          QUOTE_TMP_ROOT: path.join(syntheticRoot, "tmp"),
          SQAG_LOCAL_PRICING_REFERENCES_ROOT: path.join(syntheticRoot, "pricing-references"),
          QUOTE_LOG_ROOT: logRoot,
        } : {}),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  const output = [];
  let endpointResolve;
  let endpointReject;
  let endpointFound = false;
  let endpointBuffer = "";
  const endpointPromise = port === 0 ? new Promise((resolve, reject) => {
    endpointResolve = resolve;
    endpointReject = reject;
  }) : null;
  let closeResolve;
  const closePromise = new Promise((resolve) => { closeResolve = resolve; });
  server.once("error", (error) => {
    if (!endpointFound && endpointReject) endpointReject(error);
  });
  server.once("close", (code, signal) => {
    closeResolve({ code, signal });
    if (!endpointFound && endpointReject) {
      endpointReject(new Error(`The isolated smoke server exited before reporting its endpoint (code=${code}, signal=${signal || ""}).`));
    }
  });
  const collect = (chunk) => {
    const text = String(chunk);
    output.push(text);
    if (output.join("").length > 8000) output.shift();
    if (!endpointFound && endpointResolve) {
      endpointBuffer = `${endpointBuffer}${text}`.slice(-2048);
      const match = endpointBuffer.match(/(?:^|\r?\n)SQAG_SERVER_ENDPOINT=(https?:\/\/[^\s]+)/);
      if (match) {
        endpointFound = true;
        endpointResolve(match[1]);
      }
    }
  };
  server.stdout.on("data", collect);
  server.stderr.on("data", collect);
  return { server, output, closePromise, endpointPromise };
}

async function stopServer(serverInfo, { force = false } = {}) {
  if (!serverInfo || (!force && options.keepServer)) return;
  if (serverInfo.server.exitCode === null && serverInfo.server.signalCode === null) {
    serverInfo.server.kill();
  }
  await serverInfo.closePromise;
}

async function screenshot(page, name) {
  if (!options.screenshots) return "";
  await fs.mkdir(outputDir, { recursive: true });
  const filePath = path.join(outputDir, name);
  await page.screenshot({ path: filePath, fullPage: false });
  return filePath;
}

async function expectQuoteSessionDeleteButtonFocused(page) {
  await page.waitForFunction(() => document.activeElement?.id === "confirmQuoteSessionDeleteButton", null, { timeout: 15000 });
}

async function expectTopbarPrimaryAction(page, expectedAction) {
  const dashboardVisible = await page.locator("#backToDashboardButton").isVisible();
  const newQuoteVisible = await page.locator("#newQuoteButton").isVisible();
  const settingsVisible = await page.locator("#settingsButton").isVisible();
  if (!settingsVisible) {
    throw new Error("Pricing Reference topbar action should stay visible.");
  }
  if (expectedAction === "dashboard" && (!dashboardVisible || newQuoteVisible)) {
    throw new Error(`Expected Dashboard-only topbar action, found ${JSON.stringify({ dashboardVisible, newQuoteVisible })}.`);
  }
  if (expectedAction === "new-quote" && (!newQuoteVisible || dashboardVisible)) {
    throw new Error(`Expected New Quote-only topbar action, found ${JSON.stringify({ dashboardVisible, newQuoteVisible })}.`);
  }
}

async function dashboardPanelActionMetrics(page, label) {
  await page.locator("#dashboardSelectedSessionPanel").scrollIntoViewIfNeeded();
  const panelBox = await page.locator("#dashboardSelectedSessionPanel").boundingBox();
  const actionBox = await page.locator("#dashboardSelectedSessionPanel .dashboard-selected-actions").boundingBox();
  const firstActionBox = await page.locator("#dashboardSelectedSessionPanel .dashboard-selected-actions button, #dashboardSelectedSessionPanel .dashboard-selected-actions a").first().boundingBox();
  const actionPositions = await page.locator("#dashboardSelectedSessionPanel").evaluate((panel) => {
    const panelRect = panel.getBoundingClientRect();
    const actions = panel.querySelector(".dashboard-selected-actions");
    const actionRect = actions?.getBoundingClientRect();
    const modifyButton = actions?.querySelector('[data-dashboard-panel-action="modify-session"]');
    const deleteButton = actions?.querySelector('[data-dashboard-panel-action="delete-session"], [data-dashboard-panel-action="delete-selected"]');
    const clearButton = actions?.querySelector('[data-dashboard-panel-action="clear-selection"]');
    const modifyRect = modifyButton?.getBoundingClientRect();
    const deleteRect = deleteButton?.getBoundingClientRect();
    const clearRect = clearButton?.getBoundingClientRect();
    return {
      actionX: actionRect ? Math.round(actionRect.x - panelRect.x) : null,
      actionWidth: actionRect ? Math.round(actionRect.width) : null,
      actionTop: actionRect ? Math.round(actionRect.y - panelRect.y) : null,
      modifyTop: modifyRect ? Math.round(modifyRect.y - panelRect.y) : null,
      modifyHeight: modifyRect ? Math.round(modifyRect.height) : null,
      deleteTop: deleteRect ? Math.round(deleteRect.y - panelRect.y) : null,
      deleteHeight: deleteRect ? Math.round(deleteRect.height) : null,
      clearTop: clearRect ? Math.round(clearRect.y - panelRect.y) : null,
      clearHeight: clearRect ? Math.round(clearRect.height) : null,
    };
  });
  if (!panelBox || !actionBox || !firstActionBox) {
    throw new Error(`Dashboard ${label} action footer was not measurable.`);
  }
  if (actionPositions.deleteTop === null || actionPositions.clearTop === null) {
    throw new Error(`Dashboard ${label} shared delete/clear actions were not measurable.`);
  }
  const deleteClearGap = actionPositions.clearTop - actionPositions.deleteTop;
  if (deleteClearGap < 38 || deleteClearGap > 54) {
    throw new Error(`Dashboard ${label} delete/clear spacing is unexpected: ${deleteClearGap}px.`);
  }
  const modifyDeleteGap = actionPositions.modifyTop === null ? null : actionPositions.deleteTop - actionPositions.modifyTop;
  const deleteClearActualGap = actionPositions.deleteHeight === null
    ? null
    : actionPositions.clearTop - (actionPositions.deleteTop + actionPositions.deleteHeight);
  const modifyDeleteActualGap = actionPositions.modifyHeight === null
    ? null
    : actionPositions.deleteTop - (actionPositions.modifyTop + actionPositions.modifyHeight);
  if (modifyDeleteActualGap !== null && deleteClearActualGap !== null && Math.abs(modifyDeleteActualGap - deleteClearActualGap) > 4) {
    throw new Error(`Dashboard ${label} modify/delete gap differs from delete/clear gap: ${modifyDeleteActualGap}px vs ${deleteClearActualGap}px.`);
  }
  const bottomGap = Math.round((panelBox.y + panelBox.height) - (actionBox.y + actionBox.height));
  if (bottomGap < 8 || bottomGap > 36) {
    throw new Error(`Dashboard ${label} action footer is not bottom anchored: ${bottomGap}px gap.`);
  }
  return {
    bottomGap,
    actionViewportTop: Math.round(actionBox.y),
    firstActionTop: Math.round(firstActionBox.y),
    deleteClearGap,
    modifyDeleteGap,
    deleteClearActualGap,
    modifyDeleteActualGap,
    ...actionPositions,
  };
}

async function verifyMobileHeaderOrder(page) {
  await page.setViewportSize({ width: 520, height: 720 });
  const metrics = await page.evaluate(() => {
    const visibleRect = (selector) => {
      const element = document.querySelector(selector);
      if (!element || element.hidden) return null;
      const style = window.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden") return null;
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return {
        top: Math.round(rect.top),
        bottom: Math.round(rect.bottom),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        text: element.textContent?.trim() || "",
      };
    };
    return {
      auth: visibleRect("#topbarAuthState"),
      dashboard: visibleRect("#backToDashboardButton"),
      pricing: visibleRect("#settingsButton"),
      privacy: visibleRect(".topbar-privacy-link"),
    };
  });
  if (!metrics.dashboard || !metrics.pricing || !metrics.privacy) {
    throw new Error(`Mobile header actions were not measurable: ${JSON.stringify(metrics)}.`);
  }
  const actionTop = Math.min(metrics.dashboard.top, metrics.pricing.top);
  const actionBottom = Math.max(metrics.dashboard.bottom, metrics.pricing.bottom);
  if (metrics.auth && metrics.auth.bottom > actionTop + 4) {
    throw new Error(`Mobile auth status should appear before dashboard/pricing actions: ${JSON.stringify(metrics)}.`);
  }
  if (actionBottom > metrics.privacy.top + 4) {
    throw new Error(`Mobile Privacy Notice should appear below dashboard/pricing actions: ${JSON.stringify(metrics)}.`);
  }
  if (metrics.dashboard.width < 120 || metrics.pricing.width < 120) {
    throw new Error(`Mobile dashboard/pricing actions should remain prominent: ${JSON.stringify(metrics)}.`);
  }
}

async function verifyMobileBasisLegendAndOutputCards(page) {
  await page.setViewportSize({ width: 520, height: 720 });
  await page.evaluate(() => {
    selectPricingReferenceOptionValue(firstPricingReferenceOptionValue());
    state.quoteBasis = {};
    state.quoteBasisSections = [{
      id: "graphics",
      title: "Graphics",
      lines: [
        { tag: "Include", text: "Printed wall graphics", confidence: 92, quantity: 12, unit: "sqm" },
        { tag: "Confirm", text: "Confirm counter finish", confidence: 71, quantity: 1, unit: "lot" },
        { tag: "Custom", text: "AI proposed curved counter", confidence: 88, custom_confirmed: true, quantity: 1, unit: "set" },
        { tag: "Exclude", text: "Exclude back-room storage", confidence: 93, quantity: 1, unit: "lot" },
      ],
    }];
    state.aiFailed = false;
    state.draftSource = "edited";
    updateQuoteBasisCard("edited");
    setSidePanel("basis", { force: true });
  });
  await page.locator(".basis-tag-legend").waitFor({ state: "visible", timeout: 15000 });
  const basisLineCounts = await page.locator("#basisReviewSurface .quote-basis-source .pricing-reference-line-count").evaluateAll((items) => (
    items.map((item) => (item.textContent || "").replace(/\s+/g, " ").trim())
  ));
  if (basisLineCounts.length !== 2
    || basisLineCounts[0] !== "4 review lines"
    || basisLineCounts[1] !== "2 output lines") {
    throw new Error(`Quote Basis review/output counts are incorrect: ${JSON.stringify(basisLineCounts)}.`);
  }
  const legendMetrics = await page.locator(".basis-tag-legend").evaluate((legend) => {
    const items = Array.from(legend.querySelectorAll(".basis-tag-legend-item")).map((item) => {
      const rect = item.getBoundingClientRect();
      const text = item.querySelector("span")?.getBoundingClientRect();
      return {
        width: Math.round(rect.width),
        left: Math.round(rect.left),
        textWidth: text ? Math.round(text.width) : 0,
        text: item.textContent?.trim() || "",
      };
    });
    return {
      columns: window.getComputedStyle(legend).gridTemplateColumns,
      width: Math.round(legend.getBoundingClientRect().width),
      items,
    };
  });
  if (!legendMetrics.items.some((item) => item.text.includes("Include"))
    || !legendMetrics.items.some((item) => item.text.includes("AI Proposal"))
    || !legendMetrics.items.some((item) => item.text.includes("92%"))) {
    throw new Error(`Mobile basis legend is missing expected labels: ${JSON.stringify(legendMetrics)}.`);
  }
  if (legendMetrics.items.some((item) => item.width < 260 || item.textWidth < 140)) {
    throw new Error(`Mobile basis legend items are too cramped: ${JSON.stringify(legendMetrics)}.`);
  }

  await page.evaluate(() => {
    state.basisConfirmed = true;
    state.outputRows = [
      normalizeOutputRow({
        section: "Graphics",
        description: "[ sqm of printed wall graphics ]",
        quantity: 12,
        unit: "sqm",
        price_mode: "Priced",
        unit_price_override: 45,
        catalog_unit_price: 45,
        pricing_keyword: "graphics-vinyl-printed-graphics",
        pricing_reference_description: "[ sqm of printed wall graphics ]",
        amount: 540,
      }),
      {
        section: "Custom",
        description: "Curved service counter",
        quantity: 1,
        unit: "set",
        price_mode: "Included",
        unit_price_override: "Included",
        catalog_unit_price: "",
        amount: 0,
      },
    ];
    state.lineItems = outputRowsToLineItems();
    renderPricingMatches(state.outputRows);
    setSidePanel("output", { force: true });
  });
  await page.locator("#pricingMatchesBody tr").first().waitFor({ state: "visible", timeout: 15000 });
  const renderedDescription = (await page.locator('#pricingMatchesBody tr:first-child [data-output-label="Description"]').innerText()).trim();
  if (renderedDescription !== "sqm of printed wall graphics") {
    throw new Error(`Output should show the customer-facing bracket-free description, found ${JSON.stringify(renderedDescription)}.`);
  }
  const manualDescription = "Premium reception counter with lockable storage";
  const descriptionCell = page.locator('#pricingMatchesBody tr:first-child [data-output-edit-field="description"]');
  await descriptionCell.click();
  const descriptionEditor = page.locator('[data-output-editor-field="description"]');
  await descriptionEditor.waitFor({ state: "visible", timeout: 15000 });
  await descriptionEditor.fill(manualDescription);
  await page.locator('#pricingMatchesBody tr:first-child [data-output-edit-field="quantity"]').click();
  await page.waitForFunction((expected) => (
    state.outputRows[0]?.description === expected
    && document.querySelector('#pricingMatchesBody tr:first-child [data-output-label="Description"]')?.textContent?.trim() === expected
  ), manualDescription, { timeout: 15000 });
  await page.evaluate(() => clearQuoteSessionDraftSaveTimer());

  const editEvidence = await page.evaluate((expected) => {
    renderPricingMatches(state.outputRows);
    renderPricingMatches(state.outputRows);
    const snapshot = snapshotOutputRows(state.outputRows);
    const lineItems = outputRowsToLineItems(state.outputRows);
    const payload = buildPayload();
    return {
      stateDescription: state.outputRows[0]?.description || "",
      renderedDescription: document.querySelector('#pricingMatchesBody tr:first-child [data-output-label="Description"]')?.textContent?.trim() || "",
      pricingKeyword: state.outputRows[0]?.pricing_keyword || "",
      catalogUnitPrice: state.outputRows[0]?.catalog_unit_price,
      snapshotDescription: snapshot[0]?.description || "",
      lineItemDescription: lineItems[0]?.description || "",
      lineItemPricingKeyword: lineItems[0]?.pricing_keyword || "",
      payloadDescription: payload.line_items?.[0]?.description || "",
      stable: state.outputRows[0]?.description === expected,
    };
  }, manualDescription);
  if (!editEvidence.stable
    || editEvidence.stateDescription !== manualDescription
    || editEvidence.renderedDescription !== manualDescription
    || editEvidence.snapshotDescription !== manualDescription
    || editEvidence.lineItemDescription !== manualDescription
    || editEvidence.payloadDescription !== manualDescription
    || editEvidence.pricingKeyword !== "graphics-vinyl-printed-graphics"
    || editEvidence.lineItemPricingKeyword !== "graphics-vinyl-printed-graphics"
    || editEvidence.catalogUnitPrice !== 45) {
    throw new Error(`Manual Output description did not survive edit, render, snapshot, or export payload creation: ${JSON.stringify(editEvidence)}.`);
  }

  const quoteRestoreEvidence = await page.evaluate(async (expected) => {
    const snapshot = buildSessionSnapshot();
    snapshot.quoteSessionId = "quote-manual-description-restore";
    snapshot.quoteSessionDraftSaveStarted = true;
    const restored = await applyQuoteSessionSnapshot(snapshot, {
      forceQuoteView: true,
      sessionId: snapshot.quoteSessionId,
    });
    return {
      restored,
      description: state.outputRows[0]?.description || "",
      renderedDescription: document.querySelector('#pricingMatchesBody tr:first-child [data-output-label="Description"]')?.textContent?.trim() || "",
      pricingKeyword: state.outputRows[0]?.pricing_keyword || "",
      matchesExpected: state.outputRows[0]?.description === expected,
    };
  }, manualDescription);
  if (!quoteRestoreEvidence.restored
    || !quoteRestoreEvidence.matchesExpected
    || quoteRestoreEvidence.renderedDescription !== manualDescription
    || quoteRestoreEvidence.pricingKeyword !== "graphics-vinyl-printed-graphics") {
    throw new Error(`Quote-session restore changed a manual Output description: ${JSON.stringify(quoteRestoreEvidence)}.`);
  }

  await page.evaluate(() => saveSessionState());
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#pricingMatchesBody tr").first().waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
  const browserRestoreEvidence = await page.evaluate((expected) => {
    const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
    return {
      stateDescription: state.outputRows[0]?.description || "",
      renderedDescription: document.querySelector('#pricingMatchesBody tr:first-child [data-output-label="Description"]')?.textContent?.trim() || "",
      savedDescription: saved.outputRows?.[0]?.description || "",
      pricingKeyword: state.outputRows[0]?.pricing_keyword || "",
      matchesExpected: state.outputRows[0]?.description === expected,
    };
  }, manualDescription);
  if (!browserRestoreEvidence.matchesExpected
    || browserRestoreEvidence.renderedDescription !== manualDescription
    || browserRestoreEvidence.savedDescription !== manualDescription
    || browserRestoreEvidence.pricingKeyword !== "graphics-vinyl-printed-graphics") {
    throw new Error(`Browser recovery changed a manual Output description: ${JSON.stringify(browserRestoreEvidence)}.`);
  }
  const outputMetrics = await page.locator("#pricingMatchesBody tr").first().evaluate((row) => {
    const cells = Array.from(row.querySelectorAll("td")).map((cell) => ({
      label: cell.getAttribute("data-output-label") || "",
      display: window.getComputedStyle(cell).display,
      before: window.getComputedStyle(cell, "::before").content,
      width: Math.round(cell.getBoundingClientRect().width),
    }));
    const rowRect = row.getBoundingClientRect();
    return {
      rowDisplay: window.getComputedStyle(row).display,
      rowWidth: Math.round(rowRect.width),
      tableHeadDisplay: window.getComputedStyle(document.querySelector(".output-match-table thead")).display,
      cells,
    };
  });
  const expectedLabels = ["Section", "Description", "Quantity", "Unit", "Unit price", "Amount", "Delete"];
  if (outputMetrics.rowDisplay !== "grid" || outputMetrics.tableHeadDisplay !== "none") {
    throw new Error(`Mobile output rows should render as cards: ${JSON.stringify(outputMetrics)}.`);
  }
  if (expectedLabels.some((label) => !outputMetrics.cells.some((cell) => cell.label === label && cell.before.includes(label)))) {
    throw new Error(`Mobile output card labels were missing: ${JSON.stringify(outputMetrics)}.`);
  }
  if (outputMetrics.rowWidth > 500 || outputMetrics.cells.some((cell) => cell.width > 500)) {
    throw new Error(`Mobile output card overflows the viewport: ${JSON.stringify(outputMetrics)}.`);
  }
  await page.locator('#pricingMatchesBody tr:first-child [data-output-edit-field="unit_price_override"]').click();
  await page.locator('[data-output-editor-field="unit_price_override"]').waitFor({ state: "visible", timeout: 15000 });
  await page.locator('[data-output-included-action="true"]').waitFor({ state: "visible", timeout: 15000 });
  await page.keyboard.press("Escape");
  await page.locator('#pricingMatchesBody tr:first-child [data-output-delete-row]').click();
  await page.locator("#outputDeleteModal").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#cancelOutputDeleteButton").click();
  await page.locator("#outputDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
}

async function prepareRefreshRecoveryQuote(page) {
  await page.evaluate(async () => {
    const reference = state.pricingReferences.find((item) => Array.isArray(item.items) && item.items.length)
      || state.pricingReferences[0];
    if (!reference) throw new Error("Refresh recovery fixture needs a pricing reference.");
    state.pricingReferenceId = reference.id;
    state.pricingReferenceSource = reference.source || "bundled";
    syncSelectedPricingReference();

    [
      [elements.clientName, "Refresh Recovery Client"],
      [elements.clientAttention, "Test Contact"],
      [elements.clientTitle, "Project Manager"],
      [elements.clientAddress, "1 Test Street"],
      [elements.projectTitle, "Refresh Recovery Quote"],
      [elements.showName, "Refresh Recovery Expo"],
      [elements.quoteDate, "2026-07-14"],
      [elements.projectNumber, "RECOVERY-001"],
      [elements.headerDetails, "Refresh Recovery Company"],
      [elements.quoteCompanyName, "Refresh Recovery Company"],
      [elements.acceptanceText, "Accepted for testing"],
      [elements.companySignatory, "Test Signatory"],
      [elements.companyTitle, "Director"],
      [elements.companyDateLabel, "Date"],
      [elements.personLabel, "Name"],
      [elements.stampLabel, "Stamp"],
      [elements.dateLabel, "Date"],
    ].forEach(([input, value]) => setInputValue(input, value));

    state.headerLogo = {
      name: "refresh-recovery-logo.png",
      type: "image/png",
      size: 68,
      data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII=",
    };
    renderHeaderLogoPreview();
    state.images = [{
      name: "refresh-recovery-render.png",
      type: "image/png",
      size: 68,
      data_url: state.headerLogo.data_url,
    }];
    state.quoteSessionId = state.quoteSessionId || newClientQuoteSessionId();
    state.quoteSessionDraftSaveStarted = true;
    state.activeAppView = "quote";
    state.aiFailed = false;
    state.basisConfirmed = false;
    state.blockingClarificationQuestions = [];
    state.downloadFile = null;
    state.pdfFile = null;
    state.outputRows = [];
    state.originalOutputRows = [];
    state.outputErrors = [];
    const recoveryLineItem = normalizeLineItem({
      section: "Floor Design",
      description: "sqm refresh recovery flooring",
      quantity: 1,
      unit: "sqm",
      price_mode: "Priced",
      unit_price: 50,
      unit_price_override: 50,
      catalog_unit_price: 50,
      amount: 50,
      pricing_keyword: "floor-design-refresh-recovery-flooring",
    });
    state.lineItems = [synchronizeOwnedOutputRowPrice(recoveryLineItem, 50, { force: true })];
    state.quoteBasisSections = [{
      id: "floor-design",
      title: "Floor Design",
      lines: [{
        tag: "Include",
        text: "sqm refresh recovery flooring",
        quantity: 1,
        unit: "sqm",
        include: true,
        pricing_keyword: "floor-design-refresh-recovery-flooring",
      }],
    }];
    state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
    setWorkflowStage("basis_review");
    showQuoteFlow();
    setSidePanel("basis", { force: true });
    syncControlStates();
    await persistSessionFiles(sessionFileRecordsFromDraft());
  });
}

async function verifyConfirmBasisSurvivesImmediateRefresh(page) {
  await prepareRefreshRecoveryQuote(page);
  const confirmReadiness = await page.evaluate(() => ({
    busy: appIsBusy(),
    blockReason: basisConfirmBlockReason(),
    missing: missingDetailFields(),
    lineItems: state.lineItems.length,
    workflowStage: state.workflowStage,
    activePanel: state.activeSidePanel,
  }));
  if (confirmReadiness.busy || confirmReadiness.blockReason || confirmReadiness.missing.length || !confirmReadiness.lineItems) {
    throw new Error("Confirm Basis refresh fixture is not ready: " + JSON.stringify(confirmReadiness));
  }
  let normalizeCount = 0;
  let releaseFirstRequest;
  let releaseSecondRequest;
  let firstRequestStartedResolve;
  let secondRequestStartedResolve;
  const firstRequestStarted = new Promise((resolve) => { firstRequestStartedResolve = resolve; });
  const secondRequestStarted = new Promise((resolve) => { secondRequestStartedResolve = resolve; });
  const normalizePattern = "**/api/line-items/normalize";

  await page.route(normalizePattern, async (route) => {
    normalizeCount += 1;
    const requestPayload = route.request().postDataJSON();
    if (normalizeCount === 1) {
      firstRequestStartedResolve();
      await new Promise((resolve) => { releaseFirstRequest = resolve; });
      try {
        await route.abort("aborted");
      } catch {
        // Navigation may already have disposed the interrupted request.
      }
      return;
    }
    secondRequestStartedResolve();
    await new Promise((resolve) => { releaseSecondRequest = resolve; });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "normalized",
        line_items: Array.isArray(requestPayload.line_items) ? requestPayload.line_items : [],
      }),
    });
  });

  try {
    await page.evaluate(() => { confirmBasis(); });
    await firstRequestStarted;
    await page.locator("#excelGeneratingTitle", { hasText: "Preparing Output" }).waitFor({ state: "visible", timeout: 15000 });
    const savedBeforeRefresh = await page.evaluate(() => JSON.parse(
      window.localStorage.getItem("swooshz_quote_session_v1") || "{}"
    ));
    if (savedBeforeRefresh.activeJob?.type !== "confirm_basis") {
      throw new Error("Confirm Basis did not persist its pending recovery operation before the request completed.");
    }

    const reload = page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(100);
    if (typeof releaseFirstRequest !== "function") {
      throw new Error("Confirm Basis request was not paused before refresh.");
    }
    releaseFirstRequest();
    await reload;
    await secondRequestStarted;
    await page.locator("#excelGeneratingTitle", { hasText: "Preparing Output" }).waitFor({ state: "visible", timeout: 15000 });
    releaseSecondRequest();

    try {
      await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    } catch (error) {
      const diagnostic = await page.evaluate(() => {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return {
          basisConfirmed: state.basisConfirmed,
          workflowStage: state.workflowStage,
          activePanel: state.activeSidePanel,
          preparingOutput: state.isPreparingOutput,
          lineItems: state.lineItems.length,
          outputRows: state.outputRows.length,
          activeJob: saved.activeJob || null,
          blockedText: document.querySelector("#blockedActionText")?.textContent || "",
        };
      });
      throw new Error((error?.message || "Output did not open.") + " Diagnostic: " + JSON.stringify(diagnostic));
    }
    await page.waitForFunction(() => {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return state.basisConfirmed === true
        && state.activeSidePanel === "output"
        && !saved.activeJob;
    }, null, { timeout: 15000 });
    if (normalizeCount !== 2) {
      throw new Error("Confirm Basis refresh should resume exactly once; requests observed: " + normalizeCount + ".");
    }
  } finally {
    if (typeof releaseFirstRequest === "function") releaseFirstRequest();
    if (typeof releaseSecondRequest === "function") releaseSecondRequest();
    await page.unroute(normalizePattern);
  }
}

async function verifyGenerationLoadingModalSurvivesRefresh(page) {
  const cases = [
    { type: "generate", viewPdf: false, title: "Regenerating Excel", finalizingTitle: "Finalizing Excel", readyTitle: "Excel ready", action: "excel", button: "#sideDownloadButton" },
    { type: "generate_pdf", viewPdf: true, title: "Generating PDF", finalizingTitle: "Finalizing PDF", readyTitle: "PDF ready", action: "pdf", button: "#sideViewPdfButton" },
  ];

  for (const testCase of cases) {
    let finishJob = false;
    let postCount = 0;
    let releaseFirstPost;
    let releaseFinalSave;
    let finalSaveStartedResolve;
    const finalSaveStarted = new Promise((resolve) => { finalSaveStartedResolve = resolve; });
    let firstPostStartedResolve;
    let secondPostStartedResolve;
    const firstPostStarted = new Promise((resolve) => { firstPostStartedResolve = resolve; });
    const secondPostStarted = new Promise((resolve) => { secondPostStartedResolve = resolve; });
    const postedJobIds = [];
    const postPattern = "**/api/jobs";
    const getPattern = "**/api/jobs/job-*";
    const savePattern = "**/api/quote-sessions";
    const quoteSessionId = await currentQuoteSessionId(page);

    await page.route(postPattern, async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      postCount += 1;
      const payload = route.request().postDataJSON();
      postedJobIds.push(payload.job_id || "");
      if (postCount === 1) {
        firstPostStartedResolve();
        await new Promise((resolve) => { releaseFirstPost = resolve; });
        try {
          await route.abort("aborted");
        } catch {
          // Navigation may already have disposed the interrupted request.
        }
        return;
      }
      secondPostStartedResolve();
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: payload.job_id,
          type: payload.type,
          status: "running",
          created_at: "2026-07-14T00:00:00Z",
        }),
      });
    });

    await page.route(getPattern, async (route) => {
      const jobId = new URL(route.request().url()).pathname.split("/").pop() || "";
      if (!postedJobIds.includes(jobId)) {
        await route.continue();
        return;
      }
      const files = [{
        name: "quotation.xlsx",
        url: "/api/quote-sessions/" + quoteSessionId + "/files/quotation.xlsx",
      }];
      if (testCase.viewPdf) {
        files.push({
          name: "quotation.pdf",
          url: "/api/quote-sessions/" + quoteSessionId + "/files/quotation.pdf",
        });
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(finishJob
          ? {
              job_id: jobId,
              type: testCase.type,
              status: "completed",
              result: {
                status: "completed",
                files,
                pricing_matches: [],
                quote_session: { session_id: quoteSessionId },
              },
            }
          : {
              job_id: jobId,
              type: testCase.type,
              status: "running",
              created_at: "2026-07-14T00:00:00Z",
            }),
      });
    });

    try {
      await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 15000 });
      await page.locator(testCase.button + ":not([disabled])").click();
      await firstPostStarted;
      const savedBeforeRefresh = await page.evaluate(() => JSON.parse(
        window.localStorage.getItem("swooshz_quote_session_v1") || "{}"
      ));
      if (savedBeforeRefresh.activeJob?.type !== testCase.type || savedBeforeRefresh.activeJob?.phase !== "starting") {
        throw new Error("Export click did not persist the pending " + testCase.type + " operation before the server response.");
      }
      if (!postedJobIds[0]) {
        throw new Error("Export click did not send a client-generated job id.");
      }

      const reload = page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForTimeout(100);
      if (typeof releaseFirstPost !== "function") {
        throw new Error("Initial " + testCase.type + " request was not paused before refresh.");
      }
      releaseFirstPost();
      await reload;
      await secondPostStarted;
      await page.locator("#excelGeneratingModal").waitFor({ state: "visible", timeout: 15000 });
      const restoredTitle = (await page.locator("#excelGeneratingTitle").innerText()).trim();
      if (restoredTitle !== testCase.title) {
        throw new Error("Expected refreshed " + testCase.type + " overlay title " + testCase.title + ", found " + restoredTitle + ".");
      }
      if (postCount !== 2 || postedJobIds[0] !== postedJobIds[1]) {
        throw new Error("Refresh should retry " + testCase.type + " with one stable idempotency key: " + JSON.stringify(postedJobIds) + ".");
      }
      const activePanel = (await page.locator(".rail-button.is-active").innerText()).trim();
      if (activePanel !== "Output") {
        throw new Error("Recovered " + testCase.type + " should remain tied to Output, found " + activePanel + ".");
      }
      await page.keyboard.press("Escape");
      await page.locator("#excelGeneratingModal").waitFor({ state: "visible", timeout: 15000 });
      await page.locator("#excelGeneratingModal > .modal-backdrop").click({ position: { x: 5, y: 5 } });
      await page.locator("#excelGeneratingModal").waitFor({ state: "visible", timeout: 15000 });

      let finalSaveSeen = false;
      await page.route(savePattern, async (route) => {
        if (route.request().method() !== "POST" || finalSaveSeen) {
          await route.fallback();
          return;
        }
        finalSaveSeen = true;
        finalSaveStartedResolve();
        await new Promise((resolve) => { releaseFinalSave = resolve; });
        await route.fallback();
      });

      finishJob = true;
      await Promise.race([
        finalSaveStarted,
        page.waitForTimeout(15000).then(() => {
          throw new Error("Recovered " + testCase.type + " did not reach its final quote-session save.");
        }),
      ]);
      await page.waitForFunction((expectedTitle) => {
        const modal = document.querySelector("#excelGeneratingModal");
        return modal
          && !modal.hidden
          && modal.classList.contains("is-open")
          && !modal.classList.contains("is-ready")
          && document.querySelector("#excelGeneratingTitle")?.textContent?.trim() === expectedTitle;
      }, testCase.finalizingTitle, { timeout: 15000 });
      const activeDuringFinalization = await page.evaluate(() => JSON.parse(
        window.localStorage.getItem("swooshz_quote_session_v1") || "{}"
      ).activeJob || null);
      if (activeDuringFinalization?.type !== testCase.type) {
        throw new Error("Recovered " + testCase.type + " cleared its active job before finalization completed.");
      }
      releaseFinalSave();
      await page.waitForFunction(() => {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return !saved.activeJob;
      }, null, { timeout: 15000 });
      await page.locator("#excelGeneratingModal.is-ready").waitFor({ state: "visible", timeout: 15000 });
      const readyTitle = (await page.locator("#excelGeneratingTitle").innerText()).trim();
      const readyAction = await page.locator("#excelGeneratingActionButton").getAttribute("data-export-action");
      if (readyTitle !== testCase.readyTitle || readyAction !== testCase.action) {
        throw new Error("Recovered " + testCase.type + " result was not attached to its restored splash.");
      }
      if (testCase.type === "generate") {
        await page.keyboard.press("Escape");
      } else {
        await page.locator("#excelGeneratingModal > .modal-backdrop").click({ position: { x: 5, y: 5 } });
      }
      await page.locator("#excelGeneratingModal").waitFor({ state: "hidden", timeout: 15000 });
    } finally {
      if (typeof releaseFirstPost === "function") releaseFirstPost();
      if (typeof releaseFinalSave === "function") releaseFinalSave();
      await page.unroute(savePattern);
      await page.unroute(postPattern);
      await page.unroute(getPattern);
    }
  }
}
async function verifyGenerationTerminalRecoveryAfterRefresh(page) {
  const cases = [
    { id: "job-refresh-excel-completed", type: "generate", viewPdf: false, title: "Regenerating Excel", terminalStatus: "completed" },
    { id: "job-refresh-pdf-completed", type: "generate_pdf", viewPdf: true, title: "Generating PDF", terminalStatus: "completed" },
    { id: "job-refresh-excel-needs-review", type: "generate", viewPdf: false, title: "Regenerating Excel", terminalStatus: "needs_review" },
    { id: "job-refresh-excel-blocked", type: "generate", viewPdf: false, title: "Regenerating Excel", terminalStatus: "blocked" },
    { id: "job-refresh-excel-failed", type: "generate", viewPdf: false, title: "Regenerating Excel", terminalStatus: "failed" },
  ];

  for (const testCase of cases) {
    let finishJob = false;
    let duplicateGenerationStarts = 0;
    const startRoutePattern = "**/api/jobs";
    await page.route(startRoutePattern, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      duplicateGenerationStarts += 1;
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ status: "failed" }) });
    });
    const routePattern = `**/api/jobs/${testCase.id}*`;
    await page.route(routePattern, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(finishJob
          ? {
              job_id: testCase.id,
              type: testCase.type,
              status: testCase.terminalStatus,
              result: {
                status: testCase.terminalStatus === "needs_review" ? "needs_confirmation" : testCase.terminalStatus,
                ...(testCase.terminalStatus === "blocked" ? { errors: ["Synthetic blocked state for refresh recovery smoke."] } : {}),
                ...(testCase.terminalStatus === "failed" ? { message: "Synthetic failed state for refresh recovery smoke." } : {}),
                ...(testCase.terminalStatus === "completed" ? {
                  files: [
                    { name: "quotation.xlsx", url: `/api/jobs/${testCase.id}/files/quotation.xlsx` },
                    ...(testCase.viewPdf ? [{ name: "quotation.pdf", url: `/api/jobs/${testCase.id}/files/quotation.pdf` }] : []),
                  ],
                } : {}),
              },
            }
          : {
              job_id: testCase.id,
              type: testCase.type,
              status: "running",
              created_at: "2026-01-01T00:00:00Z",
            }),
      });
    });

    try {
      await page.evaluate((activeJob) => {
        state.activeAppView = "quote";
        state.activeSidePanel = "output";
        state.workflowStage = "generating";
        state.activeJob = {
          ...activeJob,
          browserRecoveryScope: state.browserRecoveryScope,
          startedAt: new Date().toISOString(),
        };
        saveSessionState();
      }, { id: testCase.id, type: testCase.type, viewPdf: testCase.viewPdf, phase: "running" });
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.locator("#excelGeneratingModal").waitFor({ state: "visible", timeout: 15000 });
      const restoredTitle = (await page.locator("#excelGeneratingTitle").innerText()).trim();
      if (restoredTitle !== testCase.title) {
        throw new Error(`Expected refreshed ${testCase.type} overlay title ${testCase.title}, found ${restoredTitle}.`);
      }
      const restoredJobId = await page.evaluate(() => {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return saved.activeJob?.id || "";
      });
      if (restoredJobId !== testCase.id) {
        throw new Error(`Expected refreshed ${testCase.type} job ${testCase.id}, found ${restoredJobId}.`);
      }
      if (duplicateGenerationStarts !== 0) {
        throw new Error(`Refresh created ${duplicateGenerationStarts} duplicate ${testCase.type} job(s).`);
      }

      finishJob = true;
      if (testCase.terminalStatus === "completed") {
        await page.locator("#excelGeneratingModal.is-ready").waitFor({ state: "visible", timeout: 15000 });
        await page.locator("#excelGeneratingCloseButton").click();
        await page.locator("#excelGeneratingModal").waitFor({ state: "hidden", timeout: 15000 });
      } else {
        await page.locator("#excelGeneratingModal").waitFor({ state: "hidden", timeout: 15000 });
      }
      await page.waitForFunction(() => {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return !saved.activeJob;
      }, null, { timeout: 15000 });
    } finally {
      await page.unroute(routePattern);
      await page.unroute(startRoutePattern);
    }
  }

  const interruptedJob = { id: "job-refresh-interrupted", type: "generate", viewPdf: false, phase: "running" };
  const interruptedStartRoutePattern = "**/api/jobs";
  let interruptedDuplicateStarts = 0;
  await page.addInitScript((jobId) => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = typeof input === "string" ? input : input?.url || "";
      if (url.includes(`/api/jobs/${jobId}`)) {
        return Promise.reject(new TypeError("Synthetic interrupted job polling"));
      }
      return nativeFetch(input, init);
    };
  }, interruptedJob.id);
  await page.route(interruptedStartRoutePattern, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    interruptedDuplicateStarts += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ status: "failed" }) });
  });
  try {
    await page.evaluate((activeJob) => {
      state.activeAppView = "quote";
      state.activeSidePanel = "output";
      state.workflowStage = "generating";
      state.activeJob = {
        ...activeJob,
        browserRecoveryScope: state.browserRecoveryScope,
        startedAt: new Date().toISOString(),
      };
      saveSessionState();
    }, interruptedJob);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#excelGeneratingModal").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#excelGeneratingModal").waitFor({ state: "hidden", timeout: 30000 });
    await page.waitForFunction((jobId) => {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return saved.activeJob?.id === jobId;
    }, interruptedJob.id, { timeout: 15000 });
    if (interruptedDuplicateStarts !== 0) {
      throw new Error(`Interrupted refresh created ${interruptedDuplicateStarts} duplicate generation job(s).`);
    }
  } finally {
    await page.unroute(interruptedStartRoutePattern);
    await page.evaluate(() => {
      hideExcelGeneratingModal();
      clearActiveJob();
    });
  }
}

async function verifyExpiredQuoteJobsDoNotResume(page) {
  const staleStartedAt = new Date(Date.now() - 31 * 60 * 1000).toISOString();
  const freshStartedAt = new Date().toISOString();
  const futureStartedAt = new Date(Date.now() + 2 * 60 * 1000).toISOString();
  const cases = [
    ...["draft", "basis_chat", "generate", "generate_pdf", "confirm_basis"].map((type, index) => ({
      label: `stale starting ${type}`,
      type,
      phase: "starting",
      id: type === "confirm_basis" ? `operation-stale-start-${index}` : `job-stale-start-${type}-${index}`,
      startedAt: staleStartedAt,
    })),
    ...["draft", "generate", "generate_pdf"].map((type, index) => ({
      label: `stale running ${type}`,
      type,
      phase: "running",
      id: `job-stale-running-${type}-${index}`,
      startedAt: staleStartedAt,
    })),
    { label: "future dated draft", type: "draft", phase: "running", id: "job-future-draft-1234", startedAt: futureStartedAt },
    { label: "missing timestamp", type: "draft", phase: "starting", id: "job-missing-time-1234" },
    { label: "empty timestamp", type: "generate", phase: "starting", id: "job-empty-time-123456", startedAt: "" },
    { label: "malformed timestamp", type: "generate_pdf", phase: "running", id: "job-bad-time-12345678", startedAt: "not-a-date" },
    { label: "non-string timestamp", type: "basis_chat", phase: "starting", id: "job-number-time-12345", startedAt: Date.now() },
    { label: "impossible timestamp", type: "confirm_basis", phase: "running", id: "operation-impossible-date", startedAt: "2026-02-31T00:00:00Z" },
    { label: "fresh wrong scope", type: "draft", phase: "running", id: "job-wrong-scope-fresh", startedAt: freshStartedAt, wrongScope: true },
    { label: "stale wrong scope", type: "generate", phase: "starting", id: "job-wrong-scope-stale", startedAt: staleStartedAt, wrongScope: true },
  ];

  const observedRequests = [];
  const jobsPattern = "**/api/jobs*";
  const normalizePattern = "**/api/line-items/normalize";
  await page.route(jobsPattern, async (route) => {
    const request = route.request();
    observedRequests.push(`${request.method()} ${new URL(request.url()).pathname}`);
    if (request.method() === "POST") {
      const payload = request.postDataJSON();
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: payload.job_id || "job-unexpected-recovery",
          type: payload.type || "draft",
          status: "running",
          created_at: new Date().toISOString(),
        }),
      });
      return;
    }
    const jobId = new URL(request.url()).pathname.split("/").pop() || "job-unexpected-recovery";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ job_id: jobId, status: "failed", result: { status: "failed" } }),
    });
  });
  await page.route(normalizePattern, async (route) => {
    observedRequests.push(`${route.request().method()} /api/line-items/normalize`);
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "normalized", line_items: payload.line_items || [] }),
    });
  });

  try {
    for (const testCase of cases) {
      const before = await page.evaluate((candidate) => {
        const storageKey = "swooshz_quote_session_v1";
        const saved = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
        const currentScope = state.browserRecoveryScope;
        const quoteState = JSON.stringify({
          quoteBasis: saved.quoteBasis || {},
          quoteBasisSections: saved.quoteBasisSections || [],
          lineItems: saved.lineItems || [],
          outputRows: saved.outputRows || [],
          originalOutputRows: saved.originalOutputRows || [],
          basisConfirmed: Boolean(saved.basisConfirmed),
          downloadFile: saved.downloadFile || null,
          pdfFile: saved.pdfFile || null,
        });
        saved.activeAppView = "quote";
        saved.activeSidePanel = candidate.type === "draft" || candidate.type === "basis_chat" || candidate.type === "confirm_basis" ? "basis" : "output";
        saved.workflowStage = candidate.type === "draft" ? "analyzing" : candidate.type === "confirm_basis" ? "basis_review" : "generating";
        saved.restorableOverlay = candidate.type === "basis_chat" ? "basis_chat" : "";
        saved.activeJob = {
          id: candidate.id,
          type: candidate.type,
          phase: candidate.phase,
          browserRecoveryScope: candidate.wrongScope ? `${currentScope}-other` : currentScope,
          ...(Object.prototype.hasOwnProperty.call(candidate, "startedAt") ? { startedAt: candidate.startedAt } : {}),
          ...(candidate.type === "basis_chat" ? { text: "Synthetic stale basis revision" } : {}),
          ...(candidate.type === "generate_pdf" ? { viewPdf: true } : {}),
        };
        window.localStorage.setItem(storageKey, JSON.stringify(saved));
        if (candidate.type !== "basis_chat" && !candidate.wrongScope && typeof candidate.startedAt === "string" && candidate.startedAt) {
          state.activeJob = saved.activeJob;
          saveSessionState();
        }
        return { quoteState, currentScope };
      }, testCase);
      const requestCountBefore = observedRequests.length;

      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
      await page.waitForFunction(() => {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return !state.activeJob && !saved.activeJob;
      }, null, { timeout: 15000 });
      await page.waitForTimeout(150);

      const after = await page.evaluate(() => {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return {
          quoteState: JSON.stringify({
            quoteBasis: saved.quoteBasis || {},
            quoteBasisSections: saved.quoteBasisSections || [],
            lineItems: saved.lineItems || [],
            outputRows: saved.outputRows || [],
            originalOutputRows: saved.originalOutputRows || [],
            basisConfirmed: Boolean(saved.basisConfirmed),
            downloadFile: saved.downloadFile || null,
            pdfFile: saved.pdfFile || null,
          }),
          activeJob: saved.activeJob || null,
          busy: Boolean(state.isAnalysisRunning || state.isGenerating || state.isPreparingOutput),
          workflowStage: state.workflowStage,
          exportOverlayHidden: Boolean(document.querySelector("#excelGeneratingModal")?.hidden),
          basisChatOverlayHidden: Boolean(document.querySelector("#basisChatOverlay")?.hidden),
        };
      });

      const caseRequests = observedRequests.slice(requestCountBefore);
      if (caseRequests.length) {
        throw new Error(`${testCase.label} resumed unexpectedly: ${JSON.stringify(caseRequests)}.`);
      }
      if (after.activeJob || after.busy || !after.exportOverlayHidden || !after.basisChatOverlayHidden) {
        throw new Error(`${testCase.label} left unsafe recovery state: ${JSON.stringify(after)}.`);
      }
      if (["analyzing", "generating"].includes(after.workflowStage)) {
        throw new Error(`${testCase.label} restored an unsafe running stage ${after.workflowStage}.`);
      }
      if (after.quoteState !== before.quoteState) {
        throw new Error(`${testCase.label} mutated saved quote data during stale cleanup.`);
      }
    }
  } finally {
    await page.unroute(normalizePattern);
    await page.unroute(jobsPattern);
  }
}

async function verifyPricingReferenceSelectionCommitsOnCustomerNext(page) {
  const pendingValue = "local::pending-refresh-reference";
  const applied = await page.evaluate(async () => {
    const current = currentPricingReference();
    if (!current) throw new Error("Pricing-reference regression needs an applied reference.");
    state.quoteBasisSections = [{
      id: "retained-basis",
      title: "Retained basis",
      lines: [{ tag: "Include", text: "Retained basis line", include: true }],
    }];
    state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
    state.lineItems = [normalizeLineItem({
      section: "Retained basis",
      description: "Retained basis line",
      quantity: 1,
      unit: "lot",
      unit_price: 100,
      amount: 100,
    })];
    state.images = await Promise.all(state.images.map((image) => ensureContentFingerprint(image)));
    captureOriginalAnalysisSnapshot({ source: "playwright-pricing-reference-regression" });
    state.outputRows = [normalizeOutputRow({
      section: "Retained basis",
      description: "Retained output row",
      quantity: 1,
      unit: "lot",
      unit_price: 100,
      amount: 100,
    })];
    state.originalOutputRows = snapshotOutputRows(state.outputRows);
    state.basisConfirmed = true;
    setWorkflowStage("completed");
    setSidePanel("customer", { force: true });
    saveSessionState();
    await saveQuoteSessionDraftState({ quoteGenerated: true });
    state.pricingReferences.push({
      ...current,
      id: "pending-refresh-reference",
      label: "Pending Refresh Reference",
      source: "local",
      currency: "USD",
      tax: { label: "VAT", rate: 0.2 },
    });
    renderProfileOptions();
    return {
      value: pricingReferenceSelectValue(currentPricingReference()),
      pricingReferenceId: state.pricingReferenceId,
      currency: selectedPricingReferenceCurrency(),
      tax: selectedPricingReferenceTaxText(),
      basisCount: state.quoteBasisSections.length,
      outputCount: state.outputRows.length,
    };
  });
  if (!applied.basisCount || !applied.outputCount) {
    throw new Error("Pricing-reference regression fixture did not seed retained basis and Output state.");
  }

  await page.locator("#profileSelect").selectOption(pendingValue);
  const pendingState = await page.evaluate(() => ({
    pricingReferenceId: state.pricingReferenceId,
    basisCount: state.quoteBasisSections.length,
    outputCount: state.outputRows.length,
  }));
  if (pendingState.pricingReferenceId !== applied.pricingReferenceId || pendingState.basisCount !== applied.basisCount || pendingState.outputCount !== applied.outputCount) {
    throw new Error("Changing the pricing-reference dropdown applied or cleared quote state before Customer Next.");
  }
  const previewBasis = {
    currency: await page.locator("#customerDetailsPanel [data-reference-basis-currency]").innerText(),
    tax: await page.locator("#customerDetailsPanel [data-reference-basis-tax]").innerText(),
  };
  if (previewBasis.currency !== "USD" || previewBasis.tax !== "VAT 20%") {
    throw new Error("Reference basis cards did not preview the pending pricing reference: " + JSON.stringify(previewBasis));
  }

  await page.locator("#sideBackButton").click();
  await page.locator("#imageIntake.is-active").waitFor({ state: "visible", timeout: 15000 });
  await page.locator('button[data-side-panel="customer"]').click();
  await page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
  const resetAfterLeaving = {
    value: await page.locator("#profileSelect").inputValue(),
    currency: await page.locator("#customerDetailsPanel [data-reference-basis-currency]").innerText(),
    tax: await page.locator("#customerDetailsPanel [data-reference-basis-tax]").innerText(),
  };
  if (resetAfterLeaving.value !== applied.value || resetAfterLeaving.currency !== applied.currency || resetAfterLeaving.tax !== applied.tax) {
    throw new Error("Leaving Customer without Next did not restore the applied pricing reference: " + JSON.stringify(resetAfterLeaving));
  }

  await page.locator("#profileSelect").selectOption(pendingValue);

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
  const restored = await page.evaluate(() => ({
    pricingReferenceId: state.pricingReferenceId,
    selectedValue: elements.profileSelect.value,
    basisCount: state.quoteBasisSections.length,
    outputCount: state.outputRows.length,
  }));
  if (restored.pricingReferenceId !== applied.pricingReferenceId || restored.selectedValue !== applied.value || restored.basisCount !== applied.basisCount || restored.outputCount !== applied.outputCount) {
    throw new Error("Refresh persisted the pending pricing reference or lost retained quote state: " + JSON.stringify(restored));
  }

  await page.locator("#sideNextButton", { hasText: "Next: Quote Company" }).click();
  await page.locator("#quoteCompanyPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
  if (await page.locator("#quoteDependencyConfirmModal").isVisible()) {
    throw new Error("Unchanged pricing reference opened the destructive dependency warning.");
  }
  await page.locator("#sideNextButton", { hasText: "Next: Quote Basis" }).click();
  await page.locator("#quoteBasisPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
  const retainedAfterNoChange = await page.evaluate(() => ({
    basisCount: state.quoteBasisSections.length,
    outputCount: state.outputRows.length,
    analysisConfirmVisible: !elements.analysisConfirmModal.hidden,
  }));
  if (!retainedAfterNoChange.basisCount || !retainedAfterNoChange.outputCount || retainedAfterNoChange.analysisConfirmVisible) {
    throw new Error("Unchanged inputs did not preserve existing Basis and Output: " + JSON.stringify(retainedAfterNoChange));
  }

  await page.evaluate(() => {
    setSidePanel("customer", { force: true });
    const current = currentPricingReference();
    state.pricingReferences.push({
      ...current,
      id: "pending-refresh-reference",
      label: "Pending Refresh Reference",
      source: "local",
      currency: "USD",
      tax: { label: "VAT", rate: 0.2 },
    });
    renderProfileOptions();
  });
  await page.locator("#profileSelect").selectOption(pendingValue);
  await page.locator("#sideNextButton", { hasText: "Next: Quote Company" }).click();
  await page.locator("#quoteDependencyConfirmModal").waitFor({ state: "visible", timeout: 15000 });
  const warningText = await page.locator("#quoteDependencyConfirmText").innerText();
  if (!warningText.includes("clear the current Quote Basis and Output")) {
    throw new Error("Dependency warning did not explain the destructive scope: " + warningText);
  }
  await page.keyboard.press("Escape");
  await page.locator("#quoteDependencyConfirmModal").waitFor({ state: "hidden", timeout: 15000 });
  const cancelled = await page.evaluate(() => ({
    panel: state.activeSidePanel,
    pricingReferenceId: state.pricingReferenceId,
    basisCount: state.quoteBasisSections.length,
    outputCount: state.outputRows.length,
  }));
  if (cancelled.panel !== "customer" || cancelled.pricingReferenceId !== applied.pricingReferenceId || !cancelled.basisCount || !cancelled.outputCount) {
    throw new Error("Cancelling the dependency warning changed quote state: " + JSON.stringify(cancelled));
  }

  await page.locator("#sideNextButton", { hasText: "Next: Quote Company" }).click();
  await page.locator("#quoteDependencyConfirmModal").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#confirmQuoteDependencyChangeButton").click();
  await page.locator("#quoteCompanyPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
  const committed = await page.evaluate(() => ({
    pricingReferenceId: state.pricingReferenceId,
    source: state.pricingReferenceSource,
    basisCount: state.quoteBasisSections.length,
    outputCount: state.outputRows.length,
    analysisConfirmVisible: !elements.analysisConfirmModal.hidden,
    dashboardSession: state.quoteSessions.find((session) => session.session_id === state.quoteSessionId) || null,
  }));
  if (committed.pricingReferenceId !== "pending-refresh-reference" || committed.source !== "local" || committed.basisCount !== 0 || committed.outputCount !== 0) {
    throw new Error("Confirmed pricing-reference change did not invalidate old generated state: " + JSON.stringify(committed));
  }
  if (committed.analysisConfirmVisible) {
    throw new Error("Confirmed pricing-reference invalidation unexpectedly opened Start Analysis.");
  }
  if (committed.dashboardSession?.status?.quote_generated) {
    throw new Error("Dashboard latest state still reported a generated quote after dependency invalidation.");
  }

  let releaseSave;
  let saveStarted = false;
  const savePattern = "**/api/quote-sessions";
  await page.route(savePattern, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    saveStarted = true;
    await new Promise((resolve) => { releaseSave = resolve; });
    await route.fallback();
  });
  try {
    await page.locator("#sideNextButton", { hasText: "Start Analysis" }).click();
    await page.locator("#analysisConfirmModal").waitFor({ state: "visible", timeout: 2000 });
    if (saveStarted) {
      throw new Error("Start Analysis waited for a draft save before opening its confirmation dialog.");
    }
    await page.locator("#analysisConfirmCancelButton").click();
    await page.locator("#analysisConfirmModal").waitFor({ state: "hidden", timeout: 15000 });
  } finally {
    if (typeof releaseSave === "function") releaseSave();
    await page.unroute(savePattern);
  }
}
async function installMockProfiles(page, options = {}) {
  await page.route("**/api/settings/pricing-references/synthetic-exhibition-fixture-pricing**", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        pricing_reference: {
          id: "synthetic-exhibition-fixture-pricing",
          label: "Synthetic Exhibition Fixture Pricing",
          description: "Test-only pricing reference for the Playwright smoke.",
          source: "local",
          schema_version: 1,
          currency: "SGD",
          tax: { label: "GST", rate: 0.09 },
          item_count: 1,
          items: [{
            id: "synthetic-floor-needle-punch-carpet",
            section: "Floor Design",
            description: "Needle punch carpet in colour",
            unit_hint: "sqm",
            internal_cost: 10,
            markup_multiplier: 1.5,
            remarks: "Synthetic smoke fixture row",
          }],
        },
      }),
    });
  });
  await page.route("**/api/profiles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profiles: [{
          id: "synthetic-exhibition-fixture-template",
          label: "Synthetic Exhibition Fixture Template",
          description: "Test-only profile for the Playwright smoke.",
          default_pricing_reference: "synthetic-exhibition-fixture-pricing",
          default_quote_detail_preset: "synthetic-fixture-default",
          quote_detail_presets: [{
            id: "synthetic-fixture-default",
            name: "Synthetic Fallback Quote Company",
            details: {
              company: {
                name: "Synthetic Fallback Quote Company Pte Ltd",
                header_details: "Synthetic Fallback Quote Company Pte Ltd\n1 Synthetic Way\nSingapore 000001",
                logo_data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
              },
              quote_text: {
                payment_terms: ["70% synthetic deposit upon confirmation."],
                cheque_payee: "Synthetic Fallback Quote Company Pte Ltd",
              },
              signature: {
                company_signatory: "Synthetic Signatory",
                company_title: "Synthetic Title",
                company_date_label: "Date:",
              },
            },
          }],
        }],
        pricing_references: [{
          id: "synthetic-exhibition-fixture-pricing",
          label: "Synthetic Exhibition Fixture Pricing",
          source: "local",
          currency: "SGD",
          tax: { label: "GST", rate: 0.09 },
          item_count: 1,
          digest_sha256: "sha256:2685fa5d3f208d9df578a3dbed4fc2d14fb44c0d2f5d87b9e991a1409719a9b7",
        }],
        company_profiles: Array.isArray(options.companyProfiles) ? options.companyProfiles : [],
        default_profile_id: "synthetic-exhibition-fixture-template",
        default_pricing_reference_id: "synthetic-exhibition-fixture-pricing",
        company_id: "default",
        workspace: {
          company: { id: "default", slug: "default", display_name: "Quote Generator Workspace" },
          workspace: { id: "default", slug: "default", display_name: "Quote Generator Workspace" },
          runtime_dependencies: {},
        },
      }),
    });
  });
}

async function saveSmokePricingReference(page, internalCost) {
  return page.evaluate(async ({ internalCost }) => postJson("/api/settings/pricing-references", {
    id: "synthetic-exhibition-fixture-pricing",
    label: "Synthetic Exhibition Fixture Pricing",
    source: "local",
    currency: "SGD",
    tax: { label: "GST", rate: 0.09 },
    items: [{
      id: "synthetic-floor-needle-punch-carpet",
      section: "Floor Design",
      description: "Needle punch carpet in colour",
      unit_hint: "sqm",
      internal_cost: internalCost,
      markup_multiplier: 1.5,
      remarks: "Synthetic smoke fixture row",
    }],
    update_existing: true,
    editing_reference_id: "synthetic-exhibition-fixture-pricing",
  }), { internalCost });
}

async function verifyServerPricingReferenceReviewDurability(page) {
  let sessionId = "";
  const isolatedContext = await page.context().browser().newContext({ viewport: { width: 1365, height: 768 } });
  const expectedDigest = "sha256:2685fa5d3f208d9df578a3dbed4fc2d14fb44c0d2f5d87b9e991a1409719a9b7";
  try {
    page = await isolatedContext.newPage();
    await installMockProfiles(page);
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    const savedA = await saveSmokePricingReference(page, 10);
    if (!savedA.ok || savedA.data?.status !== "saved") {
      throw new Error(`Could not establish smoke pricing catalogue A: ${JSON.stringify(savedA)}.`);
    }
    const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
    if (await emptyNewQuoteButton.isVisible()) await emptyNewQuoteButton.click();
    else await page.locator("#newQuoteButton:not([disabled])").click();
    await seedQuoteDraftFromTestFixture(page);
    await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
    await page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await page.waitForFunction(() => Boolean(state.quoteSessionId && state.quoteCommercialSnapshot?.pricing_basis?.digest), null, { timeout: 15000 });

    const established = await page.evaluate(async () => {
      state.quoteBasisSections = normalizeQuoteBasisSections([{
        id: "smoke-floor",
        title: "Floor Design",
        lines: [{
          tag: "Include",
          text: "Needle punch carpet in colour",
          include: true,
          quantity: 2,
          unit: "sqm",
          pricing_keyword: "synthetic-floor-needle-punch-carpet",
        }],
      }]);
      state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
      state.lineItems = [normalizeLineItem({
        section: "Floor Design",
        quantity: 2,
        unit: "sqm",
        description: "Needle punch carpet in colour",
        pricing_keyword: "synthetic-floor-needle-punch-carpet",
      })];
      const normalized = await refreshLineItemsFromServer();
      if (!normalized.ok) throw new Error(`Catalogue A normalization failed: ${JSON.stringify(normalized.data)}.`);
      captureOriginalAnalysisSnapshot({ source: "playwright-server-review-durability" });
      refreshOutputRowsFromLineItems();
      state.originalOutputRows = snapshotOutputRows(state.outputRows);
      state.basisConfirmed = true;
      setWorkflowStage("completed");
      setSidePanel("output", { force: true });
      const saved = await saveQuoteSessionDraftState({ quoteGenerated: true });
      if (!saved?.session_id) throw new Error("Catalogue A quote session was not saved.");
      return {
        sessionId: state.quoteSessionId,
        lifecycle: state.quoteCommercialLifecycle,
        snapshot: state.quoteCommercialSnapshot,
        lineItem: state.lineItems[0],
        output: JSON.stringify(state.outputRows),
      };
    });
    sessionId = established.sessionId;
    if (
      established.lifecycle !== "NEW_UNINITIALISED"
      || established.snapshot?.pricing_basis?.digest !== expectedDigest
      || established.lineItem?.catalog_unit_price !== 15
      || established.lineItem?.pricing_basis_amount !== 30
      || established.lineItem?.approved_quote_amount !== 30
    ) {
      throw new Error(`Catalogue A quote was not established with the expected authority: ${JSON.stringify(established)}.`);
    }

    const savedB = await saveSmokePricingReference(page, 11);
    if (!savedB.ok || savedB.data?.status !== "saved") {
      throw new Error(`Could not switch smoke pricing catalogue to B: ${JSON.stringify(savedB)}.`);
    }
    const mismatch = await page.evaluate(async () => postJson(
      "/api/line-items/normalize",
      buildLineItemNormalizePayload(),
    ));
    if (mismatch.ok || mismatch.status !== 400) {
      throw new Error(`Catalogue B should reject the saved A basis: ${JSON.stringify(mismatch)}.`);
    }
    const review = mismatch.data?.quoteCommercialReview;
    if (
      !review
      || review.schema !== "swooshz.quote-commercial-review.v1"
      || review.version !== 1
      || review.status !== "REVIEW_REQUIRED"
      || review.reason_code !== "pricing_reference_digest_mismatch"
      || review.blocked_identity?.id !== "synthetic-exhibition-fixture-pricing"
      || review.blocked_identity?.source !== "local"
    ) {
      throw new Error(`Catalogue B rejection did not return the canonical review: ${JSON.stringify(mismatch)}.`);
    }
    const adopted = await page.evaluate(() => {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return {
        review: state.quoteCommercialReview,
        persistedReview: saved.quoteCommercialReview,
        snapshot: state.quoteCommercialSnapshot,
        output: JSON.stringify(state.outputRows),
      };
    });
    if (
      JSON.stringify(stableJson(adopted.review)) !== JSON.stringify(stableJson(review))
      || JSON.stringify(stableJson(adopted.persistedReview)) !== JSON.stringify(stableJson(review))
      || adopted.snapshot?.pricing_basis?.digest !== expectedDigest
      || adopted.output !== established.output
    ) {
      throw new Error(`The client did not durably adopt the server review while preserving A: ${JSON.stringify(adopted)}.`);
    }
    const persisted = await dashboardQuoteSessionDetail(page, sessionId);
    const persistedDraft = persisted.quote_session?.draft_state || {};
    if (
      persisted.status !== "ok"
      || JSON.stringify(stableJson(persistedDraft.quoteCommercialReview)) !== JSON.stringify(stableJson(review))
      || persistedDraft.quoteDetails?.commercial_snapshot?.pricing_basis?.digest !== expectedDigest
    ) {
      throw new Error(`The server session did not persist the review and saved A basis: ${JSON.stringify(persisted)}.`);
    }

    const restoredA = await saveSmokePricingReference(page, 10);
    if (!restoredA.ok || restoredA.data?.status !== "saved") {
      throw new Error(`Could not restore smoke pricing catalogue A: ${JSON.stringify(restoredA)}.`);
    }
    const refreshedCatalogue = await page.evaluate(async () => {
      await loadProfiles();
      return {
        review: state.quoteCommercialReview,
        snapshot: state.quoteCommercialSnapshot,
        selected: currentPricingReference()?.id || "",
      };
    });
    if (
      JSON.stringify(stableJson(refreshedCatalogue.review)) !== JSON.stringify(stableJson(review))
      || refreshedCatalogue.snapshot?.pricing_basis?.digest !== expectedDigest
      || refreshedCatalogue.selected !== "synthetic-exhibition-fixture-pricing"
    ) {
      throw new Error(`Catalogue refresh silently recovered the review: ${JSON.stringify(refreshedCatalogue)}.`);
    }
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    await page.waitForFunction(() => state.quoteSessionRestoreBusy === false, null, { timeout: 15000 });
    const reloaded = await page.evaluate(() => ({
      review: state.quoteCommercialReview,
      snapshot: state.quoteCommercialSnapshot,
      output: JSON.stringify(state.outputRows),
    }));
    if (
      JSON.stringify(stableJson(reloaded.review)) !== JSON.stringify(stableJson(review))
      || reloaded.snapshot?.pricing_basis?.digest !== expectedDigest
      || reloaded.output !== established.output
    ) {
      throw new Error(`Reload did not preserve the server-issued review: ${JSON.stringify(reloaded)}.`);
    }
    const blockedAfterRestore = await page.evaluate(async () => postJson(
      "/api/line-items/normalize",
      buildLineItemNormalizePayload(),
    ));
    if (!blockedAfterRestore.data?.quoteCommercialReview || blockedAfterRestore.ok) {
      throw new Error(`Catalogue A should remain review-locked after reload: ${JSON.stringify(blockedAfterRestore)}.`);
    }
    const blockedGeneration = await page.evaluate(async () => postJson("/api/jobs", {
      type: "generate",
      payload: buildPayload(),
    }));
    if (!blockedGeneration.data?.quoteCommercialReview || blockedGeneration.ok) {
      throw new Error(`Generation should remain review-locked after catalogue restoration: ${JSON.stringify(blockedGeneration)}.`);
    }

    await page.locator('[data-side-panel="customer"]:not([disabled])').click();
    await page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#profileSelect").click();
    const afterPointerOpen = await page.evaluate(() => ({
      review: state.quoteCommercialReview,
      intent: state.pricingReferenceSelectionIntent,
      selectedValue: elements.profileSelect.value,
    }));
    if (
      JSON.stringify(stableJson(afterPointerOpen.review)) !== JSON.stringify(stableJson(review))
      || afterPointerOpen.intent !== null
      || afterPointerOpen.selectedValue !== "local::synthetic-exhibition-fixture-pricing"
    ) {
      throw new Error(`Opening the one-reference selector changed recovery state: ${JSON.stringify(afterPointerOpen)}.`);
    }
    await page.locator("#profileSelect").press("Escape");
    await page.locator("#profileSelect").focus();
    await page.locator("#profileSelect").press("Enter");
    const afterKeyboardReselection = await page.evaluate(() => ({
      review: state.quoteCommercialReview,
      intent: state.pricingReferenceSelectionIntent,
      selectedValue: elements.profileSelect.value,
    }));
    if (
      JSON.stringify(stableJson(afterKeyboardReselection.review)) !== JSON.stringify(stableJson(review))
      || JSON.stringify(stableJson(afterKeyboardReselection.intent)) !== JSON.stringify(stableJson({
        id: "synthetic-exhibition-fixture-pricing",
        source: "local",
      }))
      || afterKeyboardReselection.selectedValue !== "local::synthetic-exhibition-fixture-pricing"
    ) {
      throw new Error(`Ordinary keyboard reselection did not establish explicit intent: ${JSON.stringify(afterKeyboardReselection)}.`);
    }
    await page.locator("#sideNextButton", { hasText: "Next: Quote Company" }).click();
    await page.locator("#quoteCompanyPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    const recovered = await page.evaluate(async () => {
      const normalized = await refreshLineItemsFromServer();
      return {
        ok: normalized.ok,
        status: normalized.data?.status,
        review: state.quoteCommercialReview,
        lifecycle: state.quoteCommercialLifecycle,
        snapshot: state.quoteCommercialSnapshot,
      };
    });
    if (
      !recovered.ok
      || recovered.status !== "normalized"
      || recovered.review !== null
      || recovered.lifecycle !== "RECOVERED"
      || recovered.snapshot?.pricing_basis?.digest !== expectedDigest
    ) {
      throw new Error(`Committed Customer reselection did not recover the quote: ${JSON.stringify(recovered)}.`);
    }
  } finally {
    sessionId = sessionId || await page.evaluate(() => state.quoteSessionId).catch(() => "");
    if (sessionId) {
      await page.evaluate(async (id) => deleteQuoteSessionRecord(id), sessionId).catch(() => {});
    }
    await page.evaluate(() => clearSessionState()).catch(() => {});
    await isolatedContext.close().catch(() => {});
  }
}

async function verifyFreshPricingAuthorityInitializesBeforeCustomer(page) {
  let sessionId = "";
  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
    if (await emptyNewQuoteButton.isVisible()) await emptyNewQuoteButton.click();
    else await page.locator("#newQuoteButton:not([disabled])").click();
    await page.locator("#imageIntake.is-active").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#imageInput").setInputFiles({
      name: "v3-authority-render.png",
      mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64"),
    });
    await page.locator("#fileList .file-item", { hasText: "v3-authority-render.png" }).waitFor({ state: "visible", timeout: 15000 });
    await page.waitForFunction(() => {
      try {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "null");
        return Boolean(saved && saved.activeAppView === "quote" && saved.quoteCommercialLifecycle === "NEW_UNINITIALISED");
      } catch {
        return false;
      }
    }, null, { timeout: 15000 });
    const beforeCustomer = await page.evaluate(() => {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "null");
      return {
        stateSnapshot: state.quoteCommercialSnapshot,
        persistedSnapshot: saved?.quoteDetails?.commercial_snapshot || null,
        lifecycle: state.quoteCommercialLifecycle,
        sessionId: state.quoteSessionId,
      };
    });
    if (beforeCustomer.stateSnapshot || beforeCustomer.persistedSnapshot || beforeCustomer.sessionId) {
      throw new Error(`Fresh authority was serialized before Customer initialization: ${JSON.stringify(beforeCustomer)}.`);
    }

    await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
    await page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await page.waitForFunction(() => Boolean(
      state.pricingReferenceId
      && state.pricingReferenceSource
      && state.quoteCommercialSnapshot?.pricing_basis?.id
      && state.quoteCommercialSnapshot?.pricing_basis?.digest
    ), null, { timeout: 15000 });
    const afterCustomer = await page.evaluate(() => {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "null");
      return {
        lifecycle: state.quoteCommercialLifecycle,
        review: state.quoteCommercialReview,
        pricingReferenceId: state.pricingReferenceId,
        pricingReferenceSource: state.pricingReferenceSource,
        selectedValue: elements.profileSelect.value,
        snapshot: state.quoteCommercialSnapshot,
        persistedSnapshot: saved?.quoteDetails?.commercial_snapshot || null,
        sessionId: state.quoteSessionId,
      };
    });
    const expectedValue = "local::synthetic-exhibition-fixture-pricing";
    if (
      afterCustomer.lifecycle !== "NEW_UNINITIALISED"
      || afterCustomer.review
      || afterCustomer.pricingReferenceId !== "synthetic-exhibition-fixture-pricing"
      || afterCustomer.pricingReferenceSource !== "local"
      || afterCustomer.selectedValue !== expectedValue
      || afterCustomer.snapshot?.pricing_basis?.id !== "synthetic-exhibition-fixture-pricing"
      || afterCustomer.snapshot?.pricing_basis?.source !== "local"
      || afterCustomer.snapshot?.pricing_basis?.currency !== "SGD"
      || afterCustomer.snapshot?.pricing_basis?.digest !== "sha256:2685fa5d3f208d9df578a3dbed4fc2d14fb44c0d2f5d87b9e991a1409719a9b7"
      || afterCustomer.persistedSnapshot?.pricing_basis?.id !== "synthetic-exhibition-fixture-pricing"
      || afterCustomer.persistedSnapshot?.pricing_basis?.digest !== afterCustomer.snapshot?.pricing_basis?.digest
    ) {
      throw new Error(`Fresh authority did not initialize and persist the configured reference at Customer: ${JSON.stringify(afterCustomer)}.`);
    }
    sessionId = afterCustomer.sessionId;
  } finally {
    await page.evaluate(async () => {
      clearQuoteSessionDraftSaveTimer();
      const pendingSaves = [quoteSessionInitialSavePromise, quoteSessionDraftSavePromise].filter(Boolean);
      await Promise.all(pendingSaves.map((pendingSave) => pendingSave.catch(() => null)));
    }).catch(() => {});
    sessionId = sessionId || await page.evaluate(() => state.quoteSessionId).catch(() => "");
    if (sessionId) {
      await page.evaluate(async (id) => {
        await deleteQuoteSessionRecord(id);
      }, sessionId).catch(() => {});
    }
    await page.evaluate(() => clearSessionState()).catch(() => {});
  }
}

async function verifySqag212AnalysisConfirmationPriceSaveReloadAndExports(page, tracker = null, verificationOptions = {}) {
  let stage = "opening the isolated browser session";
  let sessionId = "";
  let browserPage = null;
  const isolatedContext = await page.context().browser().newContext({ viewport: { width: 1365, height: 900 } });
  const g3LifecycleOnly = args.includes("--sqag212-g3-lifecycle-only") || verificationOptions.g3LifecycleOnly === true;
  const g3ControlsOnly = args.includes("--sqag212-g3-controls-only") || verificationOptions.g3ControlsOnly === true;
  const deferTrackerUntilSessionStable = g3LifecycleOnly || g3ControlsOnly;
  if (tracker && !deferTrackerUntilSessionStable) await tracker.install(isolatedContext);
  const draftJobIds = new Set();
  let capturedDraft = null;
  let lastJobPostType = "";
  let draftPollCount = 0;
  const pageErrors = [];
  const apiFailures = [];
  const jobResponses = [];
  const quoteSessionResponses = [];
  const listenerTasks = new Set();
  const listenerFailures = [];
  const unexpectedRequestFailures = [];
  let diagnosticRelay = null;
  const observedDiagnosticNavigations = [];
  let drainSummary = null;
  let drainControls = null;
  let timerFlushControls = null;
  let browserNegativeControl = null;
  let drainListenerTasks = async (timeoutMs = 30000) => {
    const deadline = Date.now() + timeoutMs;
    while (listenerTasks.size) {
      const remainingMs = deadline - Date.now();
      if (remainingMs <= 0) throw new Error("Asynchronous response listeners remained outstanding.");
      const snapshot = [...listenerTasks];
      let timer;
      const completed = await Promise.race([
        Promise.allSettled(snapshot).then(() => true),
        new Promise((resolve) => { timer = setTimeout(() => resolve(false), remainingMs); }),
      ]);
      clearTimeout(timer);
      if (!completed && listenerTasks.size) throw new Error("Asynchronous response listeners remained outstanding.");
    }
    if (listenerFailures.length) throw new Error(`Asynchronous response listener failed: ${JSON.stringify(listenerFailures.slice(0, 8))}`);
    if (unexpectedRequestFailures.length) throw new Error(`Unexpected request failure observed: ${JSON.stringify(unexpectedRequestFailures.slice(0, 8))}`);
  };
  const renderName = "sqag212-synthetic-render.png";
  const renderBytes = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64");
  const profileFixtureDir = path.join(root, "tests", "fixtures", "quote-generator", "profiles", "synthetic-exhibition-fixture-template");
  const layoutBytes = await fs.readFile(path.join(profileFixtureDir, "quotation-layout.xlsx"));
  const layoutRules = JSON.parse(await fs.readFile(path.join(profileFixtureDir, "layout-rules.json"), "utf8"));
  const companyProfile = {
    id: "sqag212-company-profile",
    label: "SQAG 212 Synthetic Quote Co Profile",
    description: "Synthetic local profile for the SQAG #212 browser regression.",
    defaults: {
      company: { name: "SQAG 212 Synthetic Quote Co", header_details: "SQAG 212 Synthetic Quote Co\\n1 Synthetic Street" },
      quote_text: { payment_terms: ["Payment upon confirmation."] },
      signature: {},
    },
    pack: {
      quotation_layout: {
        filename: "quotation-layout.xlsx",
        data_url: `data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,${layoutBytes.toString("base64")}`,
      },
      layout_rules: layoutRules,
    },
  };
  try {
    browserPage = await isolatedContext.newPage();
    diagnosticRelay = await createSqag212LoopbackRelay(baseUrl);
    browserPage.on("pageerror", (error) => pageErrors.push(error.message));
    const scheduleListenerTask = (label, work) => {
      const task = (async () => {
        try {
          await work();
        } catch (error) {
          listenerFailures.push({ label, error: error?.message || String(error) });
        } finally {
          listenerTasks.delete(task);
        }
      })();
      listenerTasks.add(task);
    };
    browserPage.on("response", (response) => {
      const pathName = new URL(response.url()).pathname;
      if (pathName === "/api/quote-sessions" || pathName.startsWith("/api/quote-sessions/")) {
        quoteSessionResponses.push({ method: response.request().method(), status: response.status() });
      }
      if (pathName.startsWith("/api/jobs")) {
        scheduleListenerTask("job-response", async () => {
          const body = JSON.parse(await response.text());
          jobResponses.push({
            method: response.request().method(),
            httpStatus: response.status(),
            type: String(body.type || ""),
            status: String(body.status || ""),
            resultStatus: String(body.result?.status || ""),
            errors: Array.isArray(body.errors) ? body.errors.slice(0, 3) : Array.isArray(body.result?.errors) ? body.result.errors.slice(0, 3) : [],
            error_reference: String(body.error_reference || body.result?.error_reference || ""),
          });
        });
      }
      if (response.status() >= 400 && pathName.startsWith("/api/")) {
        scheduleListenerTask("api-error-response", async () => {
          const body = JSON.parse(await response.text());
          apiFailures.push({
            status: response.status(),
            method: response.request().method(),
            path: pathName,
            state: String(body.status || ""),
            errors: Array.isArray(body.errors) ? body.errors.slice(0, 3) : [],
            error_reference: String(body.error_reference || ""),
          });
        });
      }
    });
    const observeRequestFailure = (request) => {
      unexpectedRequestFailures.push({
        method: request.method(),
        path: quoteSessionPathForRequest(request),
        operationId: String(request.headers()[quoteSessionOperationHeader] || ""),
        sessionId: quoteSessionRequestId(request),
        errorText: request.failure()?.errorText || "transport failure",
      });
    };
    browserPage.on("requestfailed", observeRequestFailure);

    const createBarrier = () => {
      let resolve;
      const promise = new Promise((fulfil) => { resolve = fulfil; });
      return { promise, resolve };
    };
    const waitForBarrier = async (barrier, label, timeoutMs = 15000) => {
      let timer;
      try {
        return await Promise.race([
          barrier.promise,
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error(`${label} timed out.`)), timeoutMs);
          }),
        ]);
      } finally {
        if (timer) clearTimeout(timer);
      }
    };
    const runHeldDiagnosticRequest = async ({
      page: diagnosticPage,
      operationId,
      sessionId: diagnosticSessionId,
      method,
      pathName,
      correlationToken,
      dispatch,
      validateUpstream,
      durableReadback,
      navigate = true,
      suppressRequestFailure = false,
    }) => {
      if (!diagnosticRelay) throw new Error("SQAG #212 diagnostic relay is not available.");
      const registrationHandle = diagnosticRelay.register({
        method,
        pathName,
        operationId,
        sessionId: diagnosticSessionId,
        correlationToken,
        validateUpstream: async (upstream, transaction) => {
          try {
            upstream.body = JSON.parse(upstream.bodyText || "{}");
          } catch {
            upstream.body = null;
          }
          validateUpstream(upstream);
          if (durableReadback) await durableReadback(upstream, transaction);
        },
        hold: true,
      });
      const failureBarrier = createBarrier();
      let navigationInitiatedAt = 0;
      let navigationCommittedAt = 0;
      let observedFailure = null;
      const onRequest = (request) => diagnosticRelay.bindBrowserRequest(request);
      const onRequestFailed = (request) => {
        if (request !== registrationHandle.registration.browserRequest) return;
        const failure = {
          method: request.method(),
          path: quoteSessionPathForRequest(request),
          operationId: String(request.headers()[quoteSessionOperationHeader] || ""),
          sessionId: quoteSessionRequestId(request),
          correlationToken: String(request.headers()[quoteSessionCorrelationHeader] || ""),
          errorText: request.failure()?.errorText || "",
          observedAt: Date.now(),
        };
        observedFailure = failure;
        if (!suppressRequestFailure) failureBarrier.resolve(failure);
      };
      diagnosticPage.on("request", onRequest);
      diagnosticPage.on("requestfailed", onRequestFailed);
      let transaction = null;
      try {
        await dispatch();
        transaction = await Promise.race([
          registrationHandle.ready,
          new Promise((_, reject) => setTimeout(() => reject(new Error(`${method} ${pathName} diagnostic relay did not complete upstream validation.`)), 15000)),
        ]);
        const registration = registrationHandle.registration;
        if (!registration.browserRequest || !transaction.applicationRequest) {
          throw new Error(`The ${method} ${pathName} diagnostic request was not bound to both Playwright and application identities.`);
        }
        const visible = await diagnosticPage.evaluate(() => ({
          sessionId: String(window.__sqagNegativeSessionId || ""),
          settled: window.__sqagNegativeFetchSettled === true,
        }));
        if (visible.sessionId !== diagnosticSessionId || visible.settled) {
          throw new Error(`The diagnostic ${method} request did not prove visible identity plus outstanding work: ${JSON.stringify(visible)}.`);
        }
        if (!navigate) {
          registrationHandle.release();
          await diagnosticPage.waitForFunction(() => window.__sqagNegativeFetchSettled === true, null, { timeout: 15000 });
          if (observedFailure) throw new Error(`The positive diagnostic control unexpectedly emitted requestfailed: ${JSON.stringify(observedFailure)}.`);
          return {
            method,
            path: pathName,
            operationId,
            sessionId: diagnosticSessionId,
            upstreamStatus: transaction.upstreamResponse.status,
            upstreamBodyStatus: String(transaction.upstreamResponse.body?.status || ""),
            requestFailedObserved: false,
            relayClientCloseObserved: false,
            navigationInitiated: false,
            navigationCommitted: false,
            sameCorrelatedRequest: true,
          };
        }

        navigationInitiatedAt = Date.now();
        const navigation = diagnosticPage.goto(diagnosticRelay.baseUrl, { waitUntil: "commit", timeout: 15000 });
        const clientClosePromise = registrationHandle.clientClose;
        await navigation;
        navigationCommittedAt = Date.now();
        const failure = await Promise.race([
          failureBarrier.promise,
          new Promise((resolve) => setTimeout(() => resolve(null), 2000)),
        ]);
        const clientClose = await Promise.race([
          clientClosePromise,
          new Promise((resolve) => setTimeout(() => resolve(null), 2000)),
        ]);
        if (suppressRequestFailure) {
          if (observedFailure) {
            throw new Error(`Missing requestfailed observation was not accepted: exact event=${JSON.stringify(observedFailure)}.`);
          }
          throw new Error("Missing requestfailed observation did not fail the navigation oracle.");
        }
        if (!failure || !failure.errorText) {
          throw new Error(`The exact Playwright Request did not emit real requestfailed evidence for ${method} ${pathName}: ${JSON.stringify({
            failure,
            transaction: {
              clientCloseObserved: transaction.clientCloseObserved,
              clientCloseAt: transaction.clientCloseAt,
              responseCompleted: transaction.responseCompleted,
              hold: transaction.hold,
              released: transaction.registration.released,
              upstreamStatus: transaction.upstreamResponse?.status,
            },
            navigationInitiatedAt,
            navigationCommittedAt,
          })}.`);
        }
        if (
          failure.method !== method
          || failure.path !== pathName
          || failure.operationId !== operationId
          || failure.sessionId !== diagnosticSessionId
          || failure.correlationToken !== correlationToken
          || failure.observedAt < navigationInitiatedAt
        ) {
          throw new Error(`The real requestfailed evidence was not bound to the exact navigation request: ${JSON.stringify(failure)}.`);
        }
        if (
          !clientClose
          || !transaction.clientCloseObserved
          || transaction.clientCloseAt < navigationInitiatedAt
        ) {
          throw new Error(`The relay did not observe client closure during the held ${method} response after navigation: ${JSON.stringify({ clientClose, transaction })}.`);
        }
        const outcome = {
          method,
          path: pathName,
          operationId,
          sessionId: diagnosticSessionId,
          correlationToken,
          upstreamStatus: transaction.upstreamResponse.status,
          upstreamBodyStatus: String(transaction.upstreamResponse.body?.status || ""),
          requestFailedObserved: true,
          relayClientCloseObserved: true,
          navigationInitiated: navigationInitiatedAt > 0,
          navigationCommitted: navigationCommittedAt > 0,
          sameCorrelatedRequest: true,
          requestFailureText: failure.errorText,
        };
        observedDiagnosticNavigations.push(outcome);
        return outcome;
      } finally {
        diagnosticPage.off("request", onRequest);
        diagnosticPage.off("requestfailed", onRequestFailed);
        if (!transaction?.clientCloseObserved && navigate && !diagnosticPage.isClosed()) {
          await diagnosticPage.close().catch(() => {});
        }
        registrationHandle.unregister();
      }
    };
    const runQuoteSessionNavigationAbortNegativeControl = async (payload, diagnosticSessionId) => {
      if (!tracker) return { skipped: true };
      const diagnosticPage = await isolatedContext.newPage();
      const postOperationId = `sqag212/negative-control-held-save-post/${Date.now()}`;
      const detailOperationId = `sqag212/negative-control-held-detail-get/${Date.now()}`;
      const outcomes = [];
      let missingObservationRejected = false;
      try {
        await diagnosticPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await diagnosticPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        const postOperation = await tracker.setPageDiagnostic(diagnosticPage, { operationId: postOperationId, operation: "negative-control-held-save-post" }, diagnosticSessionId);
        const postPayload = JSON.parse(JSON.stringify(payload));
        postPayload.session_id = diagnosticSessionId;
        postPayload.status = { ...(postPayload.status || {}), quote_generated: false };
        const post = await runHeldDiagnosticRequest({
          page: diagnosticPage,
          operationId: postOperationId,
          sessionId: diagnosticSessionId,
          method: "POST",
          pathName: "/api/quote-sessions",
          correlationToken: postOperation.correlationToken,
          dispatch: async () => diagnosticPage.evaluate((value) => {
            window.__sqagNegativeSessionId = value.session_id;
            window.__sqagNegativeFetchSettled = false;
            const headers = { "content-type": "application/json" };
            if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
            window.__sqagNegativeFetchPromise = fetch("/api/quote-sessions", {
              method: "POST",
              headers,
              body: JSON.stringify(value),
            }).then(async (response) => ({
              status: response.status,
              body: await response.text(),
            })).catch((error) => ({ error: error?.message || String(error) })).finally(() => {
              window.__sqagNegativeFetchSettled = true;
            });
          }, postPayload),
          validateUpstream: (response) => {
            if (response.status < 200 || response.status >= 300 || response.body?.status !== "saved") {
              throw new Error(`Held save POST did not complete durable work successfully: ${JSON.stringify(response)}.`);
            }
            if (String(response.body?.quote_session?.session_id || "") !== diagnosticSessionId) {
              throw new Error("Held save POST returned a different durable session identity.");
            }
          },
          durableReadback: async () => {
            const response = await fetch(`${baseUrl}/api/quote-sessions/${encodeURIComponent(diagnosticSessionId)}?__sqag_smoke_relay_readback=${Date.now()}`, {
              headers: { "cache-control": "no-cache", pragma: "no-cache" },
            });
            const body = await response.json();
            if (response.status !== 200 || String(body?.quote_session?.session_id || "") !== diagnosticSessionId) {
              throw new Error(`Held save POST durable readback failed: ${JSON.stringify({ status: response.status, body })}.`);
            }
          },
        });
        outcomes.push(post);

        await diagnosticPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await diagnosticPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        const detailOperation = await tracker.setPageDiagnostic(diagnosticPage, { operationId: detailOperationId, operation: "negative-control-held-detail-get" }, diagnosticSessionId);
        const detail = await runHeldDiagnosticRequest({
          page: diagnosticPage,
          operationId: detailOperationId,
          sessionId: diagnosticSessionId,
          method: "GET",
          pathName: `/api/quote-sessions/${encodeURIComponent(diagnosticSessionId)}`,
          correlationToken: detailOperation.correlationToken,
          dispatch: async () => diagnosticPage.evaluate((id) => {
            window.__sqagNegativeSessionId = id;
            window.__sqagNegativeFetchSettled = false;
            const headers = {};
            if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
            window.__sqagNegativeFetchPromise = fetch(`/api/quote-sessions/${encodeURIComponent(id)}`, { headers })
              .then(async (response) => ({
                status: response.status,
                body: await response.text(),
              })).catch((error) => ({ error: error?.message || String(error) })).finally(() => {
                window.__sqagNegativeFetchSettled = true;
              });
          }, diagnosticSessionId),
          validateUpstream: (response) => {
            if (response.status !== 200 || !response.body?.quote_session) {
              throw new Error(`Held detail GET did not complete durable readback successfully: ${JSON.stringify(response)}.`);
            }
            if (String(response.body?.quote_session?.session_id || "") !== diagnosticSessionId) {
              throw new Error("Held detail GET returned a different durable session identity.");
            }
          },
          durableReadback: async () => {
            const response = await fetch(`${baseUrl}/api/quote-sessions/${encodeURIComponent(diagnosticSessionId)}?__sqag_smoke_relay_detail_readback=${Date.now()}`, {
              headers: { "cache-control": "no-cache", pragma: "no-cache" },
            });
            const body = await response.json();
            if (response.status !== 200 || String(body?.quote_session?.session_id || "") !== diagnosticSessionId) {
              throw new Error(`Held detail GET durable readback failed: ${JSON.stringify({ status: response.status, body })}.`);
            }
          },
        });
        outcomes.push(detail);

        await diagnosticPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await diagnosticPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        const positiveOperation = await tracker.setPageDiagnostic(diagnosticPage, { operationId: `sqag212/positive-control-held-detail/${Date.now()}`, operation: "positive-control-held-detail" }, diagnosticSessionId);
        const positive = await runHeldDiagnosticRequest({
          page: diagnosticPage,
          operationId: positiveOperation.operationId,
          sessionId: diagnosticSessionId,
          method: "GET",
          pathName: `/api/quote-sessions/${encodeURIComponent(diagnosticSessionId)}`,
          correlationToken: positiveOperation.correlationToken,
          navigate: false,
          dispatch: async () => diagnosticPage.evaluate((id) => {
            window.__sqagNegativeSessionId = id;
            window.__sqagNegativeFetchSettled = false;
            window.__sqagNegativeFetchPromise = fetch(`/api/quote-sessions/${encodeURIComponent(id)}`)
              .then(async (response) => ({ status: response.status, body: await response.text() }))
              .finally(() => { window.__sqagNegativeFetchSettled = true; });
          }, diagnosticSessionId),
          validateUpstream: (response) => {
            if (response.status !== 200 || !response.body?.quote_session) throw new Error("Positive held detail control did not receive a valid response.");
          },
        });
        outcomes.push(positive);

        await diagnosticPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await diagnosticPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        const suppressedOperation = await tracker.setPageDiagnostic(diagnosticPage, { operationId: `sqag212/missing-requestfailed/${Date.now()}`, operation: "missing-requestfailed-observation" }, diagnosticSessionId);
        try {
          await runHeldDiagnosticRequest({
            page: diagnosticPage,
            operationId: suppressedOperation.operationId,
            sessionId: diagnosticSessionId,
            method: "GET",
            pathName: `/api/quote-sessions/${encodeURIComponent(diagnosticSessionId)}`,
            correlationToken: suppressedOperation.correlationToken,
            suppressRequestFailure: true,
            dispatch: async () => diagnosticPage.evaluate((id) => {
              window.__sqagNegativeSessionId = id;
              window.__sqagNegativeFetchSettled = false;
              window.__sqagNegativeFetchPromise = fetch(`/api/quote-sessions/${encodeURIComponent(id)}`)
                .then(async (response) => ({ status: response.status, body: await response.text() }))
                .finally(() => { window.__sqagNegativeFetchSettled = true; });
            }, diagnosticSessionId),
            validateUpstream: (response) => {
              if (response.status !== 200 || !response.body?.quote_session) throw new Error("Suppressed requestfailed control did not receive a valid response.");
            },
          });
        } catch (error) {
          if (!String(error?.message || error).includes("Missing requestfailed observation")) throw error;
          missingObservationRejected = true;
        }
        if (!missingObservationRejected) throw new Error("The missing requestfailed observation variant unexpectedly passed.");

        await diagnosticPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await diagnosticPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        const cleanupOperation = await tracker.setPageDiagnostic(diagnosticPage, { operationId: `sqag212/negative-control-cleanup/${Date.now()}`, operation: "negative-control-cleanup" }, diagnosticSessionId);
        const cleanup = await diagnosticPage.evaluate(async (id) => {
          const headers = {};
          if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
          const response = await fetch(`/api/quote-sessions/${encodeURIComponent(id)}`, { method: "DELETE", headers });
          return { status: response.status, body: await response.text() };
        }, diagnosticSessionId);
        if (![200, 404].includes(cleanup.status)) throw new Error(`Negative-control diagnostic session cleanup failed: ${JSON.stringify(cleanup)}.`);
        await tracker.drain(30000, diagnosticPage);
        return {
          skipped: false,
          outcomes,
          cleanupStatus: cleanup.status,
          missingObservationRejected,
          noNavigationPositive: "PASS",
        };
      } finally {
        await diagnosticPage.close();
      }
    };

    const runSqag212DrainControls = async (savedSessionId) => {
      const c5Page = await isolatedContext.newPage();
      const operationId = `sqag212/c5-held-detail/${Date.now()}`;
      let registrationHandle = null;
      try {
        await c5Page.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await c5Page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        const correlationToken = await tracker.setPageOperation(c5Page, {
          fixture: "sqag212",
          operation: "c5-held-required-detail",
          operationId,
          expectedSessionId: savedSessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        registrationHandle = diagnosticRelay.register({
          method: "GET",
          pathName: `/api/quote-sessions/${encodeURIComponent(savedSessionId)}`,
          operationId,
          sessionId: savedSessionId,
          correlationToken,
          validateUpstream: (upstream) => {
            try {
              upstream.body = JSON.parse(upstream.bodyText || "{}");
            } catch {
              upstream.body = null;
            }
            if (
              upstream.status !== 200
              || String(upstream.body?.quote_session?.session_id || "") !== savedSessionId
            ) {
              throw new Error(`C5 held detail upstream validation failed: ${JSON.stringify(upstream)}.`);
            }
          },
          hold: true,
        });
        const requestPromise = c5Page.evaluate(async (id) => {
          const response = await fetch(`/api/quote-sessions/${encodeURIComponent(id)}`);
          return { status: response.status, body: await response.json() };
        }, savedSessionId);
        const transaction = await registrationHandle.ready;
        const trackerRecord = tracker.records.find((record) => record.operationId === operationId);
        if (!trackerRecord) throw new Error("C5 did not observe the real required detail request before drain start.");
        const drainPromise = tracker.drain(30000, c5Page);
        try {
          await tracker.waitForDrainWaiting(5000);
        } catch (error) {
          throw new Error(`C5 drain did not reach deferred waiting state: ${error?.message || error}; tracker=${JSON.stringify(tracker.summary())}.`);
        }
        registrationHandle.release();
        const response = await requestPromise;
        await drainPromise;
        if (response.status !== 200 || String(response.body?.quote_session?.session_id || "") !== savedSessionId) {
          throw new Error(`C5 held detail request did not complete successfully: ${JSON.stringify(response)}.`);
        }
        return {
          operationId,
          releaseExecuted: true,
          requestCompleted: true,
          drainCompleted: true,
          outstanding: tracker.summary().outstanding,
          upstreamStatus: transaction.upstreamResponse.status,
        };
      } finally {
        registrationHandle?.release();
        registrationHandle?.unregister();
        await c5Page.close();
      }
    };


    const runSqag212DrainRaceControls = async (savedSessionId) => {
      const sortChoices = await browserPage.evaluate(() => ({
        original: String(state.outputSortMode || "pricing_reference"),
        quoteGenerated: Boolean(state.downloadFile && downloadFileIsFresh(state.downloadFile)),
      }));
      if (sortChoices.quoteGenerated) {
        throw new Error("F4 drain-race controls must run before generated outputs exist.");
      }
      const sortA = sortChoices.original === "name" ? "category" : "name";
      const sortB = sortA === "category" ? "name" : "category";
      const applySort = async (value) => browserPage.evaluate((sort) => {
        state.outputSortMode = sort;
        if (elements.outputSortMode) elements.outputSortMode.value = sort;
        return currentQuoteSessionDraftState();
      }, value);
      const validateQueuedRecord = (record, captured, expectedSort, label) => {
        if (
          !record
          || record.httpStatus < 200
          || record.httpStatus >= 300
          || record.bodyStatus !== "saved"
          || record.expectedSessionId !== savedSessionId
          || record.expectedQuoteGenerated !== false
          || record.responseSessionId !== savedSessionId
          || record.responseQuoteGenerated !== false
          || record.clientResult?.nonNull !== true
          || record.clientResult?.sessionId !== savedSessionId
          || record.clientResult?.quoteGenerated !== false
          || record.payloadDraftState?.outputSortMode !== expectedSort
          || record.readback?.httpStatus !== 200
          || record.readback?.sessionId !== savedSessionId
          || record.readback?.quoteGenerated !== false
          || record.readback?.draftState?.outputSortMode !== expectedSort
          || (Array.isArray(record.payloadDraftFiles)
            && JSON.stringify(queuedDraftFilesComparable(record.payloadDraftFiles)) !== JSON.stringify(queuedDraftFilesComparable(captured.snapshot.draftFiles)))
          || JSON.stringify(queuedDraftFilesComparable(record.readback?.draftFiles)) !== JSON.stringify(queuedDraftFilesComparable(captured.snapshot.draftFiles))
          || JSON.stringify(queuedDraftStateComparable(record.payloadDraftState)) !== JSON.stringify(queuedDraftStateComparable(captured.snapshot.draftState))
          || JSON.stringify(queuedDraftStateComparable(record.readback?.draftState)) !== JSON.stringify(queuedDraftStateComparable(captured.snapshot.draftState))
        ) {
          throw new Error(label + " did not validate its POST, client result, and durable readback: " + JSON.stringify({
            expectedSort,
            captured: captured.snapshot,
            record: record && {
              httpStatus: record.httpStatus,
              bodyStatus: record.bodyStatus,
              responseSessionId: record.responseSessionId,
              responseQuoteGenerated: record.responseQuoteGenerated,
              clientResult: record.clientResult,
              payloadSort: record.payloadDraftState?.outputSortMode,
              readback: record.readback,
            },
          }) + ".");
        }
      };

      const firstOperationId = "sqag212/f4-timer-during-wait/" + Date.now();
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "f4-timer-during-deferred-drain",
        operationId: firstOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      let releaseHeldJob = null;
      const heldJob = new Promise((resolve) => { releaseHeldJob = resolve; });
      tracker.trackRequiredJob(heldJob, firstOperationId + "/held-prior-job");
      const waitCountBefore = tracker.summary().drainWaitCount;
      const drainPromise = tracker.drain(30000, browserPage);
      const firstWait = await tracker.waitForDrainWaiting(5000, waitCountBefore);
      await applySort(sortA);
      await browserPage.evaluate(() => {
        const draftState = currentQuoteSessionDraftState();
        const draftFiles = sessionFileRecordsFromDraft();
        queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000, draftState, draftFiles });
      });
      await tracker.syncQueuedSaveTimer(browserPage);
      const captured = await tracker.capturePendingQueuedSave(browserPage);
      if (!captured || captured.snapshot.draftState?.outputSortMode !== sortA) {
        throw new Error("F4 could not capture its real pending timer during deferred drain: " + JSON.stringify(captured?.snapshot || null) + ".");
      }
      const timerWait = await tracker.waitForDrainWaiting(5000, firstWait.count);
      releaseHeldJob();
      const afterJobWait = await tracker.waitForDrainWaiting(5000, timerWait.count);
      let drainFinishedEarly = false;
      drainPromise.then(() => { drainFinishedEarly = true; });
      if (drainFinishedEarly) {
        throw new Error("F4 drain returned while the newly registered timer was still pending after the prior job completed.");
      }
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "f4-timer-during-deferred-drain",
        operationId: firstOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      let flush;
      try {
        flush = await flushRequiredQuoteSessionSaves(browserPage, tracker, captured);
      } catch (error) {
        const browserSaveCapture = await browserPage.evaluate(() => {
          const timerState = window.__sqagSmokeQuoteSessionTimerState || {};
          return {
            saveCalls: timerState.saveCalls.map((item) => ({
              timerIdentity: item.timerIdentity,
              sessionId: String(item.snapshot?.sessionId || ""),
              quoteGenerated: item.options?.quoteGenerated,
              draftFileCount: Array.isArray(item.snapshot?.draftFiles) ? item.snapshot.draftFiles.length : null,
            })),
            saveCurrentCalls: timerState.saveCurrentCalls,
            queuedSaveOperations: timerState.queuedSaveOperations?.map((item) => ({
              identity: item.identity,
              sessionId: String(item.snapshot?.sessionId || ""),
              quoteGenerated: item.options?.quoteGenerated,
              requestStarted: item.requestStarted,
            })),
          };
        }).catch(() => null);
        const relatedRecords = tracker.records.filter((record) => record.expectedSessionId === savedSessionId).map((record) => ({
          operationId: record.operationId,
          method: record.method,
          queuedSaveIdentity: record.queuedSaveIdentity,
          expectedQuoteGenerated: record.expectedQuoteGenerated,
          payloadDraftKeys: Object.keys(record.payloadDraftState || {}),
          payloadDraftFileCount: Array.isArray(record.payloadDraftFiles) ? record.payloadDraftFiles.length : null,
          payloadSortMode: record.payloadDraftState?.outputSortMode,
          readbackDraftFileCount: Array.isArray(record.readback?.draftFiles) ? record.readback.draftFiles.length : null,
          readbackSortMode: record.readback?.draftState?.outputSortMode,
          readbackDiffKeys: record.readback?.draftState
            ? queuedDraftStateDiffKeys(record.readback.draftState, captured.snapshot.draftState)
            : [],
          readbackDiffValues: record.readback?.draftState
            ? queuedDraftStateDiffKeys(record.readback.draftState, captured.snapshot.draftState).flatMap((key) => (
              queuedDraftStateLeafDiffs(record.readback.draftState?.[key], captured.snapshot.draftState?.[key], key)
            ))
            : [],
          clientResult: record.clientResult,
          httpStatus: record.httpStatus,
          bodyStatus: record.bodyStatus,
          responseQuoteGenerated: record.responseQuoteGenerated,
        }));
        const timerEntry = tracker.queuedSaveStatus(captured.snapshot.timerIdentity);
        throw new Error("F4 first timer flush failed: " + String(error?.message || error)
          + "; timerEntry=" + JSON.stringify(timerEntry && {
            status: timerEntry.status,
            saveOutcome: timerEntry.saveOutcome,
            snapshotSortMode: timerEntry.snapshot.draftState?.outputSortMode,
            transitions: timerEntry.transitions,
            evidence: timerEntry.evidence,
          })
          + "; relatedRequests=" + JSON.stringify(relatedRecords.filter((record) => record.operationId === firstOperationId))
          + "; browserSaveCapture=" + JSON.stringify(browserSaveCapture));
      }
      const drainPassed = await drainPromise;
      const firstRecords = tracker.records.filter((record) => record.operationId === firstOperationId && record.method === "POST");
      const firstStatus = tracker.queuedSaveStatus(captured.snapshot.timerIdentity);
      if (!drainPassed || firstRecords.length !== 1 || firstStatus?.status !== "succeeded") {
        throw new Error("F4 deferred drain did not remain pending through exactly one durable timer save: " + JSON.stringify({
          drainPassed, flush, firstStatus: firstStatus && { status: firstStatus.status, evidence: firstStatus.evidence },
          records: firstRecords.length, summary: tracker.summary(),
        }) + ".");
      }
      validateQueuedRecord(firstRecords[0], captured, sortA, "F4 deferred-wait timer save");

      await tracker.drain(30000, browserPage);
      const drainCountBeforeBarrier = tracker.summary().drainWaitCount;
      const barrier = tracker.armFinalReconciliationBarrier();
      const finalDrainPromise = tracker.drain(30000, browserPage);
      await barrier.reached;
      const zeroSnapshot = tracker.summary().outstanding;
      if (
        zeroSnapshot.requiredRequests
        || zeroSnapshot.requiredResponseBodies
        || zeroSnapshot.requiredDurableReadbacks
        || zeroSnapshot.requiredSavePromises
        || zeroSnapshot.nonterminalRequiredJobs
        || zeroSnapshot.queuedRequiredSaves.length
      ) {
        barrier.release();
        throw new Error("F4 final-zero barrier was reached with outstanding work: " + JSON.stringify(zeroSnapshot) + ".");
      }
      const secondOperationId = "sqag212/f4-timer-during-final-zero/" + Date.now();
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "f4-timer-during-final-zero-reconciliation",
        operationId: secondOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await applySort(sortB);
      await browserPage.evaluate(() => {
        const draftState = currentQuoteSessionDraftState();
        const draftFiles = sessionFileRecordsFromDraft();
        queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000, draftState, draftFiles });
      });
      await tracker.syncQueuedSaveTimer(browserPage);
      const finalCaptured = await tracker.capturePendingQueuedSave(browserPage);
      if (!finalCaptured || finalCaptured.snapshot.draftState?.outputSortMode !== sortB) {
        barrier.release();
        throw new Error("F4 final-zero barrier did not capture the concurrently registered timer: " + JSON.stringify(finalCaptured?.snapshot || null) + ".");
      }
      barrier.release();
      const finalTimerWait = await tracker.waitForDrainWaiting(5000, drainCountBeforeBarrier);
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "f4-timer-during-final-zero-reconciliation",
        operationId: secondOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      let finalDrainFinishedEarly = false;
      finalDrainPromise.then(() => { finalDrainFinishedEarly = true; });
      if (finalDrainFinishedEarly) {
        throw new Error("F4 final reconciliation returned success without waiting for the timer created inside its zero-work barrier.");
      }
      const finalFlush = await flushRequiredQuoteSessionSaves(browserPage, tracker, finalCaptured);
      const finalDrainPassed = await finalDrainPromise;
      const secondRecords = tracker.records.filter((record) => record.operationId === secondOperationId && record.method === "POST");
      const finalStatus = tracker.queuedSaveStatus(finalCaptured.snapshot.timerIdentity);
      if (!finalDrainPassed || secondRecords.length !== 1 || finalStatus?.status !== "succeeded") {
        throw new Error("F4 final-zero drain missed or duplicated its concurrently registered timer: " + JSON.stringify({
          finalDrainPassed, finalFlush, finalStatus: finalStatus && { status: finalStatus.status, evidence: finalStatus.evidence },
          records: secondRecords.length, summary: tracker.summary(),
        }) + ".");
      }
      validateQueuedRecord(secondRecords[0], finalCaptured, sortB, "F4 final-zero timer save");
      return {
        timerDuringDeferredWait: {
          priorJobSettled: true,
          drainStayedPendingUntilTimerPersistence: true,
          exactlyOnePost: firstRecords.length === 1,
          durableReadback: firstRecords[0].readback.httpStatus,
          quoteGenerated: firstRecords[0].readback.quoteGenerated,
          drainWaitCount: afterJobWait.count,
        },
        timerDuringFinalReconciliation: {
          zeroOutstandingBeforeRegistration: true,
          drainReconciledAndWaited: true,
          exactlyOnePost: secondRecords.length === 1,
          durableReadback: secondRecords[0].readback.httpStatus,
          quoteGenerated: secondRecords[0].readback.quoteGenerated,
          finalDrainWaitCount: finalTimerWait.count,
        },
      };
    };

    const runSqag212TrackerFailureControls = async () => {
      const orphanTracker = createQuoteSessionDrainTracker("sqag212-c5-orphan");
      orphanTracker.trackRequiredJob(null, "sqag212-c5-orphan/no-producer");
      const orphanDrained = await orphanTracker.drain(1000);
      const orphanIssue = orphanTracker.issues.find((issue) => issue.reason.includes("no terminal completion producer"));

      const deadlineTracker = createQuoteSessionDrainTracker("sqag212-c5-deadline");
      deadlineTracker.trackRequiredJob(new Promise(() => {}), "sqag212-c5-deadline/never-settles");
      const deadlineDrained = await deadlineTracker.drain(50);
      const deadlineIssue = deadlineTracker.issues.find((issue) => issue.reason.includes("deadline expired"));
      if (orphanDrained || !orphanIssue || deadlineDrained || !deadlineIssue) {
        throw new Error(`C5 tracker failure controls were incomplete: ${JSON.stringify({ orphanDrained, orphanIssue, deadlineDrained, deadlineIssue })}.`);
      }
      return {
        orphanProducerFailure: true,
        orphanOutstandingIdentity: orphanIssue.operationId,
        deadlineFailure: true,
        deadlineOutstandingIdentity: deadlineIssue.operationId,
      };
    };

    const runSqag212ThreeFindingControls = async (seedSessionId) => {
      let returnT1 = null;
      let returnT2 = null;
      let l1SessionSequence = 0;
      const openFixture = async (name, targetSessionId) => {
        const fixtureTracker = createQuoteSessionDrainTracker(name);
        const fixtureContext = await isolatedContext.browser().newContext({ viewport: { width: 1365, height: 900 } });
        try {
          await fixtureTracker.install(fixtureContext);
          const fixturePage = await fixtureContext.newPage();
          await fixturePage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
          await fixturePage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
          if (!await fixtureTracker.drain(15000, fixturePage)) {
            throw new Error(name + " ordinary installation did not reach a zero-work baseline: " + JSON.stringify(fixtureTracker.summary()) + ".");
          }
          const restoredSeed = await fixturePage.evaluate(async ({ sourceSessionId, targetId }) => {
            const detailedSession = await loadQuoteSessionDetail(sourceSessionId);
            let draftState = detailedSession?.draft_state && typeof detailedSession.draft_state === "object"
              ? detailedSession.draft_state : {};
            if (!Object.keys(draftState).length) return { restored: false, reason: "seed session has no saved draft state" };
            const draftFiles = Array.isArray(detailedSession?.draft_files) ? detailedSession.draft_files : [];
            if (draftFiles.length) {
              await persistSessionFiles(draftFiles);
              draftState = mergeDashboardDraftImagesWithAvailablePayloads(draftState, draftFiles);
            }
            draftState = mergeDashboardDraftSummaryDetails(draftState, detailedSession || {});
            draftState = hydrateDashboardDraftImagePayloads(draftState, sourceSessionId);
            const restored = await applyQuoteSessionSnapshot({
              ...draftState,
              quoteSessionId: targetId,
              quoteSessionDraftSaveStarted: true,
            }, {
              sessionId: targetId,
              forceQuoteView: true,
              restoreFurthestPanel: true,
            });
            return {
              restored,
              sessionId: String(state.quoteSessionId || ""),
              draftFiles: draftFiles.length,
            };
          }, { sourceSessionId: seedSessionId, targetId: targetSessionId });
          if (!restoredSeed.restored || restoredSeed.sessionId !== targetSessionId) {
            throw new Error(name + " could not restore the persisted synthetic seed into its isolated session: " + JSON.stringify(restoredSeed) + ".");
          }
          if (!await fixtureTracker.drain(15000, fixturePage)) {
            throw new Error(name + " restored seed produced unaccounted quote-session work: " + JSON.stringify(fixtureTracker.summary()) + ".");
          }
          return { tracker: fixtureTracker, context: fixtureContext, page: fixturePage };
        } catch (error) {
          await fixtureContext.close();
          throw error;
        }
      };
      const prepareSession = async (fixture, sessionId, operationId, operation) => {
        await fixture.tracker.setPageOperation(fixture.page, {
          fixture: "sqag212",
          operation,
          operationId,
          expectedSessionId: sessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        await fixture.page.evaluate((id) => {
          state.quoteSessionId = id;
          state.quoteSessionDraftSaveStarted = true;
          state.outputSortMode = state.outputSortMode === "name" ? "category" : "name";
          saveSessionState();
        }, sessionId);
      };
      const cleanupSession = async (page, sessionId) => {
        const cleanupPage = browserPage && !browserPage.isClosed() ? browserPage : page;
        if (!cleanupPage || cleanupPage.isClosed() || !sessionId) return;
        const cleanup = await cleanupPage.evaluate(async (id) => {
          const headers = {};
          if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
          const response = await fetch("/api/quote-sessions/" + encodeURIComponent(id), { method: "DELETE", headers });
          return { status: response.status, body: await response.text() };
        }, sessionId);
        if (![200, 404].includes(cleanup.status)) {
          throw new Error("Three-finding synthetic session cleanup failed: " + JSON.stringify(cleanup) + ".");
        }
      };
      const queuedRecordIsDurable = (record, captured) => Boolean(
        record
        && record.httpStatus >= 200
        && record.httpStatus < 300
        && record.bodyStatus === "saved"
        && record.expectedSessionId === captured.snapshot.sessionId
        && record.responseSessionId === captured.snapshot.sessionId
        && record.clientResult?.nonNull === true
        && record.clientResult?.sessionId === captured.snapshot.sessionId
        && record.readback?.httpStatus === 200
        && record.readback?.sessionId === captured.snapshot.sessionId
        && record.readback?.quoteGenerated === captured.snapshot.options.quoteGenerated
        && JSON.stringify(queuedDraftStateComparable(record.readback?.draftState))
          === JSON.stringify(queuedDraftStateComparable(captured.snapshot.draftState))
        && JSON.stringify(queuedDraftFilesComparable(record.readback?.draftFiles))
          === JSON.stringify(queuedDraftFilesComparable(captured.snapshot.draftFiles))
      );
      const waitForDrainPhase = async (fixtureTracker, phase, timeoutMs) => {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          const run = fixtureTracker.summary().activeDrainRuns.find((candidate) => candidate.phase === phase);
          if (run) return run;
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        throw new Error(`Drain did not enter expected ${phase} phase within ${timeoutMs} ms.`);
      };

      const runL1DeliveryFailureControl = async (name, lifecycleLoss) => {
        const sessionId = `quote-sqag212-l1-${Date.now()}-${++l1SessionSequence}`;
        const fixture = await openFixture(name, sessionId);
        let cleanupPage = fixture.page;
        try {
          const operationId = `sqag212/${name}/${Date.now()}`;
          await prepareSession(fixture, sessionId, operationId, name);
          fixture.tracker.faultNextTimerEventDelivery(fixture.page, "create");
          await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
          const browserEvidence = await fixture.page.evaluate(async () => {
            const timerState = window.__sqagSmokeQuoteSessionTimerState || {};
            const acknowledgement = await timerState.lastEventPromise;
            const timer = window.__sqagSmokeGetPendingQuoteSessionDraftSave?.();
            const failure = (timerState.eventDeliveryFailures || []).find((item) => item.type === "create") || null;
            return {
              acknowledgement,
              documentId: String(timer?.documentId || ""),
              timerIdentity: String(timer?.pending?.timerIdentity || ""),
              nativeTimerActive: timer?.pending?.active === true && Boolean(timer.pending.nativeTimerId),
              failure,
            };
          });
          const pageEpisode = fixture.tracker.summary().documentEpisodes.find((episode) => (
            episode.documentId === browserEvidence.documentId
          ));
          const normalizedIdentity = pageEpisode
            ? `${pageEpisode.pageIdentity}/${browserEvidence.timerIdentity}` : "";
          const noNodeQueuedRecord = Boolean(normalizedIdentity)
            && fixture.tracker.queuedSaveStatus(normalizedIdentity) === null;
          const noCorrelatedPost = fixture.tracker.records.filter((record) => (
            record.operationId === operationId && record.method === "POST"
          )).length === 0;
          const failureObservedOutsideDocument = Boolean(pageEpisode?.unknownRequiredWork)
            && pageEpisode?.work.some((item) => item.timerIdentity === normalizedIdentity && item.state === "unknown")
            && fixture.tracker.issues.some((issue) => issue.operation === "queued-save-timer-event" && issue.operationId === normalizedIdentity);
          if (browserEvidence.acknowledgement !== false
            || !browserEvidence.nativeTimerActive
            || !browserEvidence.failure
            || browserEvidence.failure.timerIdentity !== browserEvidence.timerIdentity
            || !noNodeQueuedRecord
            || !noCorrelatedPost
            || !failureObservedOutsideDocument) {
            throw new Error(`${name} did not prove a real timer binding failure and retain its observed unknown work without a Node queued-save record: ${JSON.stringify({
              browserEvidence, noNodeQueuedRecord, noCorrelatedPost, pageEpisode, summary: fixture.tracker.summary(),
            })}.`);
          }

          let navigationCommitted = false;
          if (lifecycleLoss === "page-close") {
            await fixture.page.close();
          } else if (lifecycleLoss === "navigation") {
            fixture.page.on("framenavigated", (frame) => {
              if (frame === fixture.page.mainFrame()) navigationCommitted = true;
            });
            const destination = new URL(diagnosticRelay.baseUrl);
            destination.searchParams.set("sqag212_l1", name);
            await fixture.page.goto(destination.href, { waitUntil: "commit" });
          }
          const drained = await fixture.tracker.drain(1000, fixture.page);
          const noFalseGreen = !drained
            && fixture.tracker.issues.length > 0
            && fixture.tracker.documentEpisodes.get(pageEpisode.identity)?.unknownRequiredWork === true
            && fixture.tracker.records.filter((record) => record.operationId === operationId && record.method === "POST").length === 0;
          if (!noFalseGreen || (lifecycleLoss === "navigation" && !navigationCommitted)) {
            throw new Error(`${name} allowed a false empty-state drain after ${lifecycleLoss}: ${JSON.stringify({
              drained, navigationCommitted, noFalseGreen, summary: fixture.tracker.summary(),
            })}.`);
          }
          if (lifecycleLoss !== "open") {
            cleanupPage = await fixture.context.newPage();
            await cleanupPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
            await cleanupPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
          }
          await cleanupSession(cleanupPage, sessionId);
          return {
            result: "PASS",
            lifecycleLoss,
            nativeTimerObserved: browserEvidence.nativeTimerActive,
            deliveryFailureObserved: true,
            documentEpisodeIdentity: pageEpisode.identity,
            timerIdentity: normalizedIdentity,
            noNodeQueuedRecord,
            drainedFalse: true,
            noCorrelatedPost,
            navigationCommitted: lifecycleLoss === "navigation" ? navigationCommitted : null,
          };
        } finally {
          await fixture.context.close();
        }
      };

      const t1SessionId = "quote-sqag212-t1-" + Date.now();
      const t1 = await openFixture("sqag212-t1-default-timer", t1SessionId);
      let t1Barrier = null;
      let t1FinalDrain = null;
      try {
        const defaultTimerState = await t1.tracker.readQueuedSaveTimer(t1.page);
        const trueZero = defaultTimerState?.pending === null
          && !t1.tracker.summary().outstanding.queuedRequiredSaves.length
          && t1.tracker.summary().outstanding.requiredRequests === 0
          && t1.tracker.summary().outstanding.requiredSavePromises === 0;
        if (!trueZero) throw new Error("T1 default installation did not prove a true zero-work state: " + JSON.stringify(t1.tracker.summary()) + ".");

        const nativeOperationId = "sqag212/t1-default-native-timer/" + Date.now();
        await prepareSession(t1, t1SessionId, nativeOperationId, "t1-default-native-timer");
        await t1.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 900 }));
        await t1.tracker.syncQueuedSaveTimer(t1.page);
        const nativeCaptured = await t1.tracker.capturePendingQueuedSave(t1.page);
        if (!nativeCaptured || nativeCaptured.entry.producerKind !== "native-timer") {
          throw new Error("T1 ordinary installation did not register its real native timer producer: " + JSON.stringify(nativeCaptured?.entry && {
            status: nativeCaptured.entry.status,
            producerKind: nativeCaptured.entry.producerKind,
          }) + ".");
        }
        const nativeProducerRegistered = nativeCaptured.entry.producerKind === "native-timer"
          && nativeCaptured.entry.producer !== nativeCaptured.entry.terminalPromise;
        const defaultState = await t1.page.evaluate(() => ({
          visibilityToggle: typeof window.__sqagSmokeQuoteSessionTimerState?.trackQueuedSaves,
          pending: Boolean(window.__sqagSmokeGetPendingQuoteSessionDraftSave?.().pending?.active),
        }));
        const nativeWaitCount = t1.tracker.summary().drainWaitCount;
        let nativeDrainFinishedEarly = false;
        const nativeDrain = t1.tracker.drain(20000, t1.page);
        nativeDrain.then(() => { nativeDrainFinishedEarly = true; });
        const nativeWaiting = await t1.tracker.waitForDrainWaiting(5000, nativeWaitCount);
        const pendingNativeTimer = await t1.tracker.readQueuedSaveTimer(t1.page);
        await Promise.resolve();
        if (
          defaultState.visibilityToggle !== "undefined"
          || !defaultState.pending
          || !pendingNativeTimer?.pending?.active
          || nativeDrainFinishedEarly
          || nativeWaiting.count <= nativeWaitCount
          || nativeCaptured.entry.producer === nativeCaptured.entry.terminalPromise
        ) {
          throw new Error("T1 negative default-visibility control allowed a pending native timer to coexist with drain completion: " + JSON.stringify({
            defaultState, pending: pendingNativeTimer?.pending, nativeDrainFinishedEarly, nativeWaiting,
          }) + ".");
        }
        const nativeDrainPassed = await nativeDrain;
        const nativeRecord = t1.tracker.records.find((record) => record.operationId === nativeOperationId && record.method === "POST");
        const nativeEntry = t1.tracker.queuedSaveStatus(nativeCaptured.snapshot.timerIdentity);
        const nativeChecks = {
          drainPassed: nativeDrainPassed,
          nativeTimerProducer: nativeProducerRegistered,
          terminalStatus: nativeEntry?.status === "succeeded",
          exactlyOnePost: t1.tracker.records.filter((record) => record.operationId === nativeOperationId && record.method === "POST").length === 1,
          completeResponse: nativeRecord?.httpStatus >= 200 && nativeRecord?.httpStatus < 300 && nativeRecord?.bodyStatus === "saved",
          independentDurableReadback: queuedRecordIsDurable(nativeRecord, nativeCaptured),
        };
        if (Object.values(nativeChecks).some((passed) => !passed)) {
          throw new Error("T1 default native timer did not complete through response and durable readback: " + JSON.stringify({ nativeChecks, summary: t1.tracker.summary() }) + ".");
        }

        if (!await t1.tracker.drain(15000, t1.page)) {
          throw new Error("T1 native timer did not leave the tracker at a clean zero-work state before final-zero control.");
        }
        const finalOperationId = "sqag212/t1-default-final-zero-flush/" + Date.now();
        const finalWaitCount = t1.tracker.summary().drainWaitCount;
        t1Barrier = t1.tracker.armFinalReconciliationBarrier();
        t1FinalDrain = t1.tracker.drain(20000, t1.page);
        await t1Barrier.reached;
        await prepareSession(t1, t1SessionId, finalOperationId, "t1-default-final-zero-flush");
        await t1.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await t1.tracker.syncQueuedSaveTimer(t1.page);
        const finalCaptured = await t1.tracker.capturePendingQueuedSave(t1.page);
        if (!finalCaptured || finalCaptured.entry.producerKind !== "native-timer") {
          t1Barrier.release();
          throw new Error("T1 final-zero registration did not create a default native timer producer.");
        }
        t1Barrier.release();
        const finalWaiting = await t1.tracker.waitForDrainWaiting(5000, finalWaitCount);
        const finalTimerState = await t1.tracker.readQueuedSaveTimer(t1.page);
        let finalDrainFinishedEarly = false;
        t1FinalDrain.then(() => { finalDrainFinishedEarly = true; });
        await Promise.resolve();
        if (!finalTimerState?.pending?.active || finalDrainFinishedEarly || finalWaiting.count <= finalWaitCount) {
          throw new Error("T1 final-zero reconciliation missed newly created required native timer work: " + JSON.stringify({
            finalTimerState, finalDrainFinishedEarly, finalWaiting,
          }) + ".");
        }
        const finalFlush = await flushRequiredQuoteSessionSaves(t1.page, t1.tracker, finalCaptured);
        const finalDrainPassed = await t1FinalDrain;
        const finalRecord = t1.tracker.records.find((record) => record.operationId === finalOperationId && record.method === "POST");
        const finalEntry = t1.tracker.queuedSaveStatus(finalCaptured.snapshot.timerIdentity);
        const flushChecks = {
          flushStarted: finalFlush.status === "succeeded",
          finalDrainPassed,
          terminalStatus: finalEntry?.status === "succeeded",
          exactlyOnePost: t1.tracker.records.filter((record) => record.operationId === finalOperationId && record.method === "POST").length === 1,
          completeResponse: finalRecord?.httpStatus >= 200 && finalRecord?.httpStatus < 300 && finalRecord?.bodyStatus === "saved",
          independentDurableReadback: queuedRecordIsDurable(finalRecord, finalCaptured),
          noNativeTimerRemains: (await t1.tracker.readQueuedSaveTimer(t1.page))?.pending === null,
        };
        if (Object.values(flushChecks).some((passed) => !passed)) {
          throw new Error("T1 default queued-timer flush did not complete through response and durable readback: " + JSON.stringify({ flushChecks, summary: t1.tracker.summary() }) + ".");
        }
        returnT1 = {
          T1_RESULT: "PASS",
          T1_NEGATIVE_DEFAULT_INSTRUMENTATION_TIMER_VISIBILITY: "PASS",
          T1_POSITIVE_TRUE_ZERO_DEFAULT_INSTALLATION: "PASS",
          T1_POSITIVE_DEFAULT_TIMER_NATIVE_COMPLETION: "PASS",
          T1_POSITIVE_DEFAULT_TIMER_FLUSH_COMPLETION: "PASS",
          native: nativeChecks,
          finalZeroFlush: flushChecks,
        };
      } finally {
        t1Barrier?.release();
        await cleanupSession(t1.page, t1SessionId);
        await t1.context.close();
      }

      const t2SessionId = "quote-sqag212-t2-" + Date.now();
      const t2 = await openFixture("sqag212-t2-sticky-options", t2SessionId);
      try {
        const operationA = "sqag212/t2-missing-options-a/" + Date.now();
        await prepareSession(t2, t2SessionId, operationA, "t2-missing-options-a");
        await t2.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await t2.tracker.syncQueuedSaveTimer(t2.page);
        const capturedA = await t2.tracker.capturePendingQueuedSave(t2.page);
        if (!capturedA) throw new Error("T2 could not capture required timer A before injecting the missing-options boundary.");
        await t2.page.evaluate(() => {
          const timerState = window.__sqagSmokeQuoteSessionTimerState;
          if (timerState?.pending) timerState.pending.options = null;
        });
        let missingOptionsRejected = false;
        try {
          await t2.tracker.capturePendingQueuedSave(t2.page);
        } catch (error) {
          if (!String(error?.message || error).includes("not capturable")) throw error;
          missingOptionsRejected = true;
        }
        const entryA = t2.tracker.queuedSaveStatus(capturedA.snapshot.timerIdentity);
        const issueABeforeB = t2.tracker.issues.find((issue) => issue.operationId === entryA?.identity);
        const aFailedBeforeB = missingOptionsRejected && entryA?.status === "failed" && Boolean(issueABeforeB);
        if (!aFailedBeforeB) {
          throw new Error("T2 did not record timer A's sticky failure before replacement B existed: " + JSON.stringify({
            missingOptionsRejected, entryA: entryA && { status: entryA.status, terminalReason: entryA.terminalReason }, issues: t2.tracker.issues,
          }) + ".");
        }

        const operationB = "sqag212/t2-replacement-b/" + Date.now();
        await t2.tracker.setPageOperation(t2.page, {
          fixture: "sqag212",
          operation: "t2-replacement-b",
          operationId: operationB,
          expectedSessionId: t2SessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        await t2.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await t2.tracker.syncQueuedSaveTimer(t2.page);
        const capturedB = await t2.tracker.capturePendingQueuedSave(t2.page);
        if (!capturedB || capturedB.snapshot.timerIdentity === capturedA.snapshot.timerIdentity) {
          throw new Error("T2 did not create a distinct valid replacement timer B.");
        }
        const aStillFailedAfterReplacement = entryA.status === "failed"
          && t2.tracker.issues.some((issue) => issue.operationId === entryA.identity)
          && entryA.successorIdentity === capturedB.snapshot.timerIdentity;
        const startB = await t2.tracker.startCapturedQueuedSave(t2.page, capturedB);
        if (startB.status !== "started") throw new Error("T2 real replacement B did not start: " + JSON.stringify(startB) + ".");
        await capturedB.entry.terminalPromise;
        const recordB = t2.tracker.records.find((record) => record.operationId === operationB && record.method === "POST");
        const bPersisted = capturedB.entry.status === "succeeded" && queuedRecordIsDurable(recordB, capturedB);
        const finalDrain = await t2.tracker.drain(1000, t2.page);
        const aRemainsFailed = entryA.status === "failed"
          && t2.tracker.issues.some((issue) => issue.operationId === entryA.identity)
          && !finalDrain;
        if (!aStillFailedAfterReplacement || !bPersisted || !aRemainsFailed) {
          throw new Error("T2 replacement B erased timer A's failure or failed to persist independently: " + JSON.stringify({
            aStillFailedAfterReplacement, bPersisted, aRemainsFailed,
            entryA: { status: entryA.status, successorIdentity: entryA.successorIdentity },
            entryB: { status: capturedB.entry.status, evidence: capturedB.entry.evidence },
            recordB: recordB && { httpStatus: recordB.httpStatus, bodyStatus: recordB.bodyStatus, readback: recordB.readback },
            summary: t2.tracker.summary(),
          }) + ".");
        }
        const healthyAOperation = "sqag212/t2-healthy-a/" + Date.now();
        await prepareSession(t2, t2SessionId, healthyAOperation, "t2-healthy-a");
        await t2.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await t2.tracker.syncQueuedSaveTimer(t2.page);
        const healthyA = await t2.tracker.capturePendingQueuedSave(t2.page);
        const healthyBOperation = "sqag212/t2-healthy-b/" + Date.now();
        await t2.tracker.setPageOperation(t2.page, {
          fixture: "sqag212",
          operation: "t2-healthy-b",
          operationId: healthyBOperation,
          expectedSessionId: t2SessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        await t2.page.evaluate(() => {
          state.outputSortMode = "pricing_reference";
          saveSessionState();
          queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 });
        });
        await t2.tracker.syncQueuedSaveTimer(t2.page);
        const healthyB = await t2.tracker.capturePendingQueuedSave(t2.page);
        const healthySupersession = Boolean(
          healthyA
          && healthyB
          && healthyA.entry.status === "superseded"
          && healthyA.entry.successorIdentity === healthyB.snapshot.timerIdentity
        );
        const healthyBStart = healthyB
          ? await t2.tracker.startCapturedQueuedSave(t2.page, healthyB)
          : { status: "unavailable" };
        if (healthyBStart.status !== "started") {
          throw new Error("T2 healthy replacement B did not start after accounting for A: " + JSON.stringify(healthyBStart) + ".");
        }
        await healthyB.entry.terminalPromise;
        const healthyBRecord = t2.tracker.records.find((record) => record.operationId === healthyBOperation && record.method === "POST");
        const healthyReplacementPersisted = healthySupersession
          && healthyB.entry.status === "succeeded"
          && queuedRecordIsDurable(healthyBRecord, healthyB);
        const stickyDrainAfterHealthyReplacement = await t2.tracker.drain(1000, t2.page);
        if (!healthyReplacementPersisted || stickyDrainAfterHealthyReplacement
          || entryA.status !== "failed"
          || !t2.tracker.issues.some((issue) => issue.operationId === entryA.identity)) {
          throw new Error("T2 healthy A-to-B replacement or original sticky failure was not preserved: " + JSON.stringify({
            healthySupersession, healthyReplacementPersisted, stickyDrainAfterHealthyReplacement,
            healthyA: healthyA && { status: healthyA.entry.status, successorIdentity: healthyA.entry.successorIdentity },
            healthyB: healthyB && { status: healthyB.entry.status, evidence: healthyB.entry.evidence },
            originalA: { status: entryA.status, successorIdentity: entryA.successorIdentity },
          }) + ".");
        }
        returnT2 = {
          T2_RESULT: "PASS",
          T2_NEGATIVE_MISSING_OPTIONS_THEN_SUCCESSFUL_REPLACEMENT: "PASS",
          T2_POSITIVE_HEALTHY_A_SUPERSEDED_BY_B: healthyReplacementPersisted ? "PASS" : "FAIL",
          T2_POSITIVE_B_SUCCESS_REMAINS_INDEPENDENT_OF_FAILED_A: "PASS",
          issueBeforeB: true,
          replacementBPostAndDurableReadback: true,
          finalDrainStillFailedOnA: true,
        };
      } finally {
        await cleanupSession(t2.page, t2SessionId);
        await t2.context.close();
      }

      const t3NegativeSessionId = "quote-sqag212-t3n-" + Date.now();
      const t3Negative = await openFixture("sqag212-t3-orphaned-recovery-cancel", t3NegativeSessionId);
      let t3NegativeResult = false;
      try {
        const operationId = "sqag212/t3-orphaned-cancel/" + Date.now();
        await prepareSession(t3Negative, t3NegativeSessionId, operationId, "t3-orphaned-recovery-cancel");
        await t3Negative.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await t3Negative.tracker.syncQueuedSaveTimer(t3Negative.page);
        const captured = await t3Negative.tracker.capturePendingQueuedSave(t3Negative.page);
        if (!captured || captured.entry.producerKind !== "native-timer") {
          throw new Error("T3 negative control did not begin with a real native timer producer.");
        }
        const waitCountBeforeCancel = t3Negative.tracker.summary().drainWaitCount;
        await t3Negative.page.evaluate(() => {
          state.isRecoveryScopeTransitioning = true;
          clearQuoteSessionDraftSaveTimer();
        });
        await t3Negative.tracker.syncQueuedSaveTimer(t3Negative.page);
        const orphanEntry = t3Negative.tracker.queuedSaveStatus(captured.snapshot.timerIdentity);
        const orphanIssue = t3Negative.tracker.issues.find((issue) => issue.operationId === orphanEntry?.identity);
        const immediateOrphan = orphanEntry?.status === "failed"
          && !orphanEntry.producer
          && orphanEntry.producer !== orphanEntry.terminalPromise
          && Boolean(orphanIssue)
          && t3Negative.tracker.summary().drainWaitCount === waitCountBeforeCancel;
        const orphanDrain = await t3Negative.tracker.drain(1000, t3Negative.page);
        t3NegativeResult = immediateOrphan && !orphanDrain
          && t3Negative.tracker.summary().drainWaitCount === waitCountBeforeCancel;
        if (!t3NegativeResult) {
          throw new Error("T3 recovery cancellation with only bookkeeping did not fail immediately before a drain wait: " + JSON.stringify({
            immediateOrphan, orphanDrain, waitCountBeforeCancel, entry: orphanEntry && { status: orphanEntry.status, producerKind: orphanEntry.producerKind, terminalReason: orphanEntry.terminalReason }, orphanIssue,
          }) + ".");
        }
      } finally {
        await t3Negative.context.close();
      }

      const l2NegativeSessionId = "quote-sqag212-l2-reserved-" + Date.now();
      const l2Negative = await openFixture("sqag212-l2-owned-flush-reserved-only", l2NegativeSessionId);
      let releaseL2Purge = () => {};
      let releaseL2Navigation = () => {};
      let l2NegativeResult = false;
      try {
        const operationId = "sqag212/l2-reserved-only/" + Date.now();
        await prepareSession(l2Negative, l2NegativeSessionId, operationId, "l2-reserved-only-no-successor");
        await l2Negative.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await l2Negative.tracker.syncQueuedSaveTimer(l2Negative.page);
        const captured = await l2Negative.tracker.capturePendingQueuedSave(l2Negative.page);
        if (!captured || captured.entry.producerKind !== "native-timer") {
          throw new Error("L2 negative did not begin with a captured native required timer.");
        }
        const lease = l2Negative.tracker.registerOwnedQueuedSaveFlush(captured, "L2 negative inert reservation without executable work");
        const reservationOnly = lease?.state === "reserved"
          && captured.entry.flushOperation?.state === "FLUSH_OWNERSHIP_RESERVED"
          && captured.entry.producerKind === "native-timer"
          && captured.entry.producer === captured.entry.nativeTimerLease.promise;
        if (!reservationOnly) {
          throw new Error("L2 negative reservation incorrectly counted as an operational producer: " + JSON.stringify({
            leaseState: lease?.state,
            flushState: captured.entry.flushOperation?.state,
            producerKind: captured.entry.producerKind,
          }) + ".");
        }
        const baseline = await l2Negative.page.evaluate(() => ({
          recoveryScope: String(currentBrowserRecoveryScope() || ""),
          csrfToken: String(state.csrfToken || ""),
          csrfHeaderName: String(state.csrfHeaderName || ""),
        }));
        if (!baseline.recoveryScope || !baseline.csrfToken) {
          throw new Error("L2 negative lacks the current recovery scope or CSRF state required for a real recovery transition.");
        }
        await l2Negative.page.evaluate(() => {
          const originalPurge = purgeBrowserRecoveryState;
          window.__sqagL2PurgeReached = false;
          window.__sqagL2ReleasePurge = null;
          purgeBrowserRecoveryState = async (...args) => {
            await originalPurge(...args);
            window.__sqagL2PurgeReached = true;
            await new Promise((resolve) => { window.__sqagL2ReleasePurge = resolve; });
          };
        });
        await l2Negative.page.route("**/*", async (route) => {
          if (route.request().isNavigationRequest() && route.request().frame() === l2Negative.page.mainFrame()) {
            await new Promise((resolve) => { releaseL2Navigation = resolve; });
            await route.abort();
            return;
          }
          await route.fallback();
        });
        const waitCountBeforeCancel = l2Negative.tracker.summary().drainWaitCount;
        await l2Negative.page.evaluate((context) => {
          window.__sqagL2RecoveryTransition = applySessionData({
            csrf_token: context.csrfToken,
            csrf_header: context.csrfHeaderName,
            browser_recovery_scope: context.recoveryScope + "-sqag212-l2-negative",
          });
        }, baseline);
        await l2Negative.page.waitForFunction(() => window.__sqagL2PurgeReached === true, null, { timeout: 15000 });
        const cancellationState = await l2Negative.page.evaluate(() => {
          const timer = window.__sqagSmokeGetPendingQuoteSessionDraftSave?.();
          return {
            transitioning: state.isRecoveryScopeTransitioning === true,
            pending: timer?.pending || null,
            last: timer?.last || null,
          };
        });
        await l2Negative.tracker.syncQueuedSaveTimer(l2Negative.page);
        const entry = l2Negative.tracker.queuedSaveStatus(captured.snapshot.timerIdentity);
        const orphanIssue = l2Negative.tracker.issues.find((issue) => (
          issue.operationId === entry?.identity && issue.reason.includes("ORPHAN_NO_REAL_PRODUCER")
        ));
        const noCorrelatedPost = l2Negative.tracker.records.filter((record) => (
          record.operationId === operationId && record.method === "POST"
        )).length === 0;
        const immediateOrphan = entry?.status === "failed"
          && entry.terminalReason.includes("ORPHAN_NO_REAL_PRODUCER")
          && entry.transitions.some((transition) => transition.type === "FLUSH_OWNERSHIP_RESERVED")
          && entry.flushOperation?.terminalStatus === "failed"
          && !entry.producer
          && Boolean(orphanIssue)
          && cancellationState.transitioning === true
          && cancellationState.pending === null
          && cancellationState.last?.cancelledForRecovery === true
          && noCorrelatedPost
          && l2Negative.tracker.summary().drainWaitCount === waitCountBeforeCancel;
        const drainPassed = await l2Negative.tracker.drain(1000, l2Negative.page);
        l2NegativeResult = immediateOrphan && !drainPassed
          && l2Negative.tracker.summary().drainWaitCount === waitCountBeforeCancel;
        if (!l2NegativeResult) {
          throw new Error("L2 reserved-only recovery cancellation did not fail immediately as an orphan before a deadline wait: " + JSON.stringify({
            immediateOrphan, drainPassed, waitCountBeforeCancel, cancellationState,
            entry: entry && { status: entry.status, terminalReason: entry.terminalReason, flushOperation: entry.flushOperation, transitions: entry.transitions },
            orphanIssue, noCorrelatedPost, summary: l2Negative.tracker.summary(),
          }) + ".");
        }
        await cleanupSession(l2Negative.page, l2NegativeSessionId);
      } finally {
        if (!l2Negative.page.isClosed()) await l2Negative.page.evaluate(() => window.__sqagL2ReleasePurge?.()).catch(() => {});
        releaseL2Purge();
        releaseL2Navigation();
        await l2Negative.context.close();
      }

      const t3PositiveSessionId = "quote-sqag212-t3p-" + Date.now();
      const t3Positive = await openFixture("sqag212-t3-owned-recovery-flush", t3PositiveSessionId);
      let releaseT3Post = () => {};
      let t3PostReachedResolve;
      const t3PostReached = new Promise((resolve) => { t3PostReachedResolve = resolve; });
      let releaseT3PostResolve;
      const t3PostGate = new Promise((resolve) => { releaseT3PostResolve = resolve; });
      releaseT3Post = () => releaseT3PostResolve();
      let t3RouteError = null;
      let t3RouteDoneResolve;
      const t3RouteDone = new Promise((resolve) => { t3RouteDoneResolve = resolve; });
      let transferredProducer = false;
      let nativeTimerRemainedReal = false;
      let t3DrainPendingAtGate = false;
      let persisted = false;
      let t3PositiveEvidence = null;
      try {
        const operationId = "sqag212/t3-owned-recovery-flush/" + Date.now();
        await prepareSession(t3Positive, t3PositiveSessionId, operationId, "t3-owned-recovery-flush");
        await t3Positive.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        await t3Positive.tracker.syncQueuedSaveTimer(t3Positive.page);
        const captured = await t3Positive.tracker.capturePendingQueuedSave(t3Positive.page);
        if (!captured || captured.entry.producerKind !== "native-timer") {
          throw new Error("T3 positive control did not begin with the native timer producer.");
        }
        const ownedFlush = t3Positive.tracker.registerOwnedQueuedSaveFlush(captured, "T3 registered real save handoff before recovery cancellation");
        if (!ownedFlush || ownedFlush.settled) throw new Error("T3 could not register the real owned flush producer.");
        const reservationRemainedNonOperational = ownedFlush.state === "reserved"
          && captured.entry.flushOperation?.state === "FLUSH_OWNERSHIP_RESERVED"
          && captured.entry.producerKind === "native-timer"
          && captured.entry.producer === captured.entry.nativeTimerLease.promise;
        if (!reservationRemainedNonOperational) {
          throw new Error("T3 flush reservation incorrectly replaced the native timer producer: " + JSON.stringify({
            leaseState: ownedFlush.state,
            flushState: captured.entry.flushOperation?.state,
            producerKind: captured.entry.producerKind,
          }) + ".");
        }
        await t3Positive.page.route("**/api/quote-sessions", async (route) => {
          try {
            const response = await route.fetch();
            t3PostReachedResolve({ status: response.status() });
            await t3PostGate;
            await route.fulfill({ response });
          } catch (error) {
            t3RouteError = String(error?.message || error);
          } finally {
            t3RouteDoneResolve();
          }
        });
        const start = await t3Positive.tracker.startCapturedQueuedSave(t3Positive.page, captured, { cancelNativeTimer: false });
        if (start.status !== "started" || start.nativeTimerRetained !== true
          || ownedFlush.state !== "started" || ownedFlush.executable !== true) {
          throw new Error("T3 did not establish an executable real save while preserving the native timer until transfer: " + JSON.stringify({
            start, leaseState: ownedFlush.state, executable: ownedFlush.executable,
          }) + ".");
        }
        const heldPost = await Promise.race([
          t3PostReached,
          new Promise((_, reject) => setTimeout(() => reject(new Error("T3 real application POST did not reach the held response boundary.")), 10000)),
        ]);
        const nativeTimerBeforeTransfer = await t3Positive.tracker.readQueuedSaveTimer(t3Positive.page);
        const nativeTimerDebug = await t3Positive.page.evaluate(() => ({
          timer: (() => {
            const timer = window.__sqagSmokeGetPendingQuoteSessionDraftSave?.();
            return {
              pending: timer?.pending ? {
                timerIdentity: timer.pending.timerIdentity,
                active: timer.pending.active,
                nativeTimerId: timer.pending.nativeTimerId,
              } : null,
              last: timer?.last ? {
                timerIdentity: timer.last.timerIdentity,
                active: timer.last.active,
                cancelledForRecovery: timer.last.cancelledForRecovery,
              } : null,
            };
          })(),
          apiTrace: (window.__sqagSmokeQuoteSessionTimerState?.apiTrace || []).map(({ method, path }) => ({ method, path })),
          saveCalls: (window.__sqagSmokeQuoteSessionTimerState?.saveCalls || []).length,
          saveCurrentCalls: (window.__sqagSmokeQuoteSessionTimerState?.saveCurrentCalls || []).length,
          flushPromiseKeys: Object.keys(window.__sqagSmokeQuoteSessionTimerState?.flushPromises || {}),
        }));
        nativeTimerRemainedReal = nativeTimerBeforeTransfer?.pending?.active === true
          && captured.entry.nativeTimerLease.settled === false
          && t3Positive.tracker.records.some((record) => record.operationId === operationId && record.method === "POST");
        if (!nativeTimerRemainedReal || heldPost.status < 200 || heldPost.status >= 300) {
          throw new Error("T3 did not retain its real native timer and real POST at the held response boundary: " + JSON.stringify({
            nativeTimerRemainedReal,
            heldPost,
            timerStateFromBrowser: nativeTimerDebug,
            nativeTimerLease: { settled: captured.entry.nativeTimerLease?.settled, state: captured.entry.nativeTimerLease?.state },
            postRecord: t3Positive.tracker.records.filter((record) => record.operationId === operationId && record.method === "POST").map((record) => ({ upstreamStatus: record.upstreamStatus, httpStatus: record.httpStatus, lifecycle: record.lifecycle })),
          }) + ".");
        }
        const waitCountBeforeTransfer = t3Positive.tracker.summary().drainWaitCount;
        const heldDrain = t3Positive.tracker.drain(15000, t3Positive.page);
        await t3Positive.tracker.waitForDrainWaiting(5000, waitCountBeforeTransfer);
        await t3Positive.page.evaluate(() => {
          state.isRecoveryScopeTransitioning = true;
          clearQuoteSessionDraftSaveTimer();
          state.isRecoveryScopeTransitioning = false;
        });
        await t3Positive.tracker.syncQueuedSaveTimer(t3Positive.page);
        transferredProducer = captured.entry.flushOperation?.state === "FLUSH_OPERATION_STARTED"
          && captured.entry.flushOperation?.ownershipTransfer === "transferred-to-recovery"
          && captured.entry.producerState === "started"
          && captured.entry.producer !== captured.entry.terminalPromise
          && captured.entry.status === "pending";
        if (!transferredProducer) {
          throw new Error("T3 recovery cancellation did not transfer to the already-started real save: " + JSON.stringify({
            status: captured.entry.status, producerKind: captured.entry.producerKind,
            producerState: captured.entry.producerState,
            flushOperation: captured.entry.flushOperation,
          }) + ".");
        }
        let heldDrainFinishedEarly = false;
        heldDrain.then(() => { heldDrainFinishedEarly = true; });
        await Promise.resolve();
        t3DrainPendingAtGate = !heldDrainFinishedEarly;
        if (!t3DrainPendingAtGate) throw new Error("T3 drain completed while the real successor response remained held.");
        releaseT3Post();
        await captured.entry.terminalPromise;
        if (t3RouteError) throw new Error("T3 held real save response could not complete: " + t3RouteError + ".");
        const record = t3Positive.tracker.records.find((candidate) => candidate.operationId === operationId && candidate.method === "POST");
        persisted = captured.entry.status === "succeeded" && queuedRecordIsDurable(record, captured);
        const drainPassed = await heldDrain;
        if (!persisted || !drainPassed || t3Positive.tracker.issues.length) {
          throw new Error("T3 started recovery flush did not transfer, remain accountable while held, and prove durable work: " + JSON.stringify({
            persisted, drainPassed, heldDrainFinishedEarly, entry: { status: captured.entry.status, evidence: captured.entry.evidence },
            record: record && { httpStatus: record.httpStatus, bodyStatus: record.bodyStatus, readback: record.readback },
            summary: t3Positive.tracker.summary(),
          }) + ".");
        }
        t3PositiveEvidence = {
          T1_RESULT: returnT1.T1_RESULT,
          T1_NEGATIVE_DEFAULT_INSTRUMENTATION_TIMER_VISIBILITY: returnT1.T1_NEGATIVE_DEFAULT_INSTRUMENTATION_TIMER_VISIBILITY,
          T1_POSITIVE_TRUE_ZERO_DEFAULT_INSTALLATION: returnT1.T1_POSITIVE_TRUE_ZERO_DEFAULT_INSTALLATION,
          T1_POSITIVE_DEFAULT_TIMER_NATIVE_COMPLETION: returnT1.T1_POSITIVE_DEFAULT_TIMER_NATIVE_COMPLETION,
          T1_POSITIVE_DEFAULT_TIMER_FLUSH_COMPLETION: returnT1.T1_POSITIVE_DEFAULT_TIMER_FLUSH_COMPLETION,
          L2_POSITIVE_NATIVE_TIMER_REMAINS_REAL_PRODUCER: nativeTimerRemainedReal ? "PASS" : "FAIL",
          T2_RESULT: returnT2.T2_RESULT,
          T2_NEGATIVE_MISSING_OPTIONS_THEN_SUCCESSFUL_REPLACEMENT: returnT2.T2_NEGATIVE_MISSING_OPTIONS_THEN_SUCCESSFUL_REPLACEMENT,
          T2_POSITIVE_HEALTHY_A_SUPERSEDED_BY_B: returnT2.T2_POSITIVE_HEALTHY_A_SUPERSEDED_BY_B,
          T2_POSITIVE_B_SUCCESS_REMAINS_INDEPENDENT_OF_FAILED_A: returnT2.T2_POSITIVE_B_SUCCESS_REMAINS_INDEPENDENT_OF_FAILED_A,
          T3_RESULT: "PASS",
          T3_NEGATIVE_RECOVERY_CANCEL_WITH_BOOKKEEPING_ONLY_PRODUCER: t3NegativeResult ? "PASS" : "FAIL",
          L2_NEGATIVE_OWNED_FLUSH_LEASE_WITHOUT_EXECUTABLE_WORK: l2NegativeResult ? "PASS" : "FAIL",
          T3_POSITIVE_CANCEL_TRANSFERS_TO_STARTED_REAL_WORK: "PASS",
          T3_POSITIVE_NATIVE_TIMER_PRODUCER: "PASS",
          L2_POSITIVE_OWNED_FLUSH_RESERVATION_IS_NOT_PRODUCER: reservationRemainedNonOperational ? "PASS" : "FAIL",
          L2_POSITIVE_FLUSH_OWNERSHIP_TRANSFERS_TO_STARTED_REAL_SAVE: transferredProducer ? "PASS" : "FAIL",
          L2_POSITIVE_STARTED_FLUSH_HELD_THEN_COMPLETES: persisted ? "PASS" : "FAIL",
          T1: returnT1,
          T2: returnT2,
          T3: {
            negativeImmediateOrphan: t3NegativeResult,
            cancelTransferredToOwnedFlush: transferredProducer,
            reservationRemainedNonOperational,
            nativeTimerRemainedReal,
            drainHeldPending: t3DrainPendingAtGate,
            realSaveAndReadback: persisted,
          },
        };
      } finally {
        releaseT3Post();
        await Promise.race([t3RouteDone, new Promise((resolve) => setTimeout(resolve, 1000))]);
        await t3Positive.page.unroute("**/api/quote-sessions").catch(() => {});
        await cleanupSession(t3Positive.page, t3PositiveSessionId);
        await t3Positive.context.close();
      }

      const l1PageClose = await runL1DeliveryFailureControl(
        "l1-negative-delivery-failure-page-close",
        "page-close",
      );
      const l1Navigation = await runL1DeliveryFailureControl(
        "l1-negative-delivery-failure-document-navigation",
        "navigation",
      );
      const l1OpenPage = await runL1DeliveryFailureControl(
        "l1-positive-delivery-failure-page-open",
        "open",
      );

      const l1CleanCloseSessionId = `quote-sqag212-l1-clean-close-${Date.now()}`;
      const l1CleanClose = await openFixture("sqag212-l1-clean-close-zero", l1CleanCloseSessionId);
      let l1CleanCloseZeroProof = false;
      let l1CleanClosePassed = false;
      try {
        const episodeBeforeClose = l1CleanClose.tracker.summary().documentEpisodes.find((episode) => episode.current);
        l1CleanCloseZeroProof = Boolean(episodeBeforeClose?.zeroProof)
          && episodeBeforeClose.zeroProof.noActiveNativeTimer === true
          && episodeBeforeClose.zeroProof.outstandingKnownWork === 0;
        if (!l1CleanCloseZeroProof) {
          throw new Error("L1 clean-close positive lacked independent successful zero-work reconciliation: " + JSON.stringify(episodeBeforeClose) + ".");
        }
        await l1CleanClose.page.close();
        l1CleanClosePassed = await l1CleanClose.tracker.drain(1000, l1CleanClose.page);
        const closedEpisode = l1CleanClose.tracker.documentEpisodes.get(episodeBeforeClose.identity);
        if (!l1CleanClosePassed || l1CleanClose.tracker.issues.length
          || closedEpisode?.state !== "closed-with-proven-zero-work"
          || closedEpisode.unknownRequiredWork) {
          throw new Error("L1 clean page close after proven zero work was not accepted: " + JSON.stringify({
            l1CleanClosePassed,
            closedEpisode: l1CleanClose.tracker.summary().documentEpisodes.find((episode) => episode.identity === episodeBeforeClose.identity),
            summary: l1CleanClose.tracker.summary(),
          }) + ".");
        }
        const cleanupPage = await l1CleanClose.context.newPage();
        await cleanupPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await cleanupPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        await cleanupSession(cleanupPage, l1CleanCloseSessionId);
      } finally {
        await l1CleanClose.context.close();
      }

      const l1CompletedNavigationSessionId = `quote-sqag212-l1-completed-navigation-${Date.now()}`;
      const l1CompletedNavigation = await openFixture("sqag212-l1-completed-work-navigation", l1CompletedNavigationSessionId);
      let l1CompletedPersistence = false;
      let l1CompletedNavigationPassed = false;
      let l1CompletedNavigationCommitted = false;
      try {
        const operationId = `sqag212/l1-completed-work-navigation/${Date.now()}`;
        await prepareSession(l1CompletedNavigation, l1CompletedNavigationSessionId, operationId, "l1-completed-work-navigation");
        await l1CompletedNavigation.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        const captured = await l1CompletedNavigation.tracker.capturePendingQueuedSave(l1CompletedNavigation.page);
        if (!captured) throw new Error("L1 completed-navigation positive did not observe its real required timer.");
        await flushRequiredQuoteSessionSaves(l1CompletedNavigation.page, l1CompletedNavigation.tracker, captured);
        const record = l1CompletedNavigation.tracker.records.find((candidate) => (
          candidate.operationId === operationId && candidate.method === "POST"
        ));
        l1CompletedPersistence = queuedRecordIsDurable(record, captured)
          && captured.entry.status === "succeeded"
          && captured.entry.evidence?.durableReadbackObserved === true;
        if (!l1CompletedPersistence) {
          throw new Error("L1 completed-navigation positive lacked real POST and durable readback before navigation: " + JSON.stringify({
            entry: { status: captured.entry.status, evidence: captured.entry.evidence },
            record: record && { httpStatus: record.httpStatus, bodyStatus: record.bodyStatus, readback: record.readback },
          }) + ".");
        }
        const priorEpisode = l1CompletedNavigation.tracker.documentEpisodes.get(
          `${captured.snapshot.pageIdentity}/${captured.snapshot.documentId}`,
        );
        l1CompletedNavigation.page.on("framenavigated", (frame) => {
          if (frame === l1CompletedNavigation.page.mainFrame()) l1CompletedNavigationCommitted = true;
        });
        const destination = new URL(diagnosticRelay.baseUrl);
        destination.searchParams.set("sqag212_l1_completed", operationId);
        await l1CompletedNavigation.page.goto(destination.href, { waitUntil: "commit" });
        l1CompletedNavigationPassed = await l1CompletedNavigation.tracker.drain(5000, l1CompletedNavigation.page);
        const retiredEpisode = l1CompletedNavigation.tracker.documentEpisodes.get(priorEpisode.identity);
        if (!l1CompletedNavigationCommitted || !l1CompletedNavigationPassed
          || retiredEpisode?.state !== "closed-after-proven-completion"
          || retiredEpisode.unknownRequiredWork
          || l1CompletedNavigation.tracker.issues.length) {
          throw new Error("L1 completed-work navigation did not preserve completion proof: " + JSON.stringify({
            navigationCommitted: l1CompletedNavigationCommitted,
            drainPassed: l1CompletedNavigationPassed,
            retiredEpisode: l1CompletedNavigation.tracker.summary().documentEpisodes.find((episode) => episode.identity === priorEpisode.identity),
            summary: l1CompletedNavigation.tracker.summary(),
          }) + ".");
        }
        await cleanupSession(l1CompletedNavigation.page, l1CompletedNavigationSessionId);
      } finally {
        await l1CompletedNavigation.context.close();
      }

      const l3AckSessionId = `quote-sqag212-l3-normal-ack-${Date.now()}`;
      const l3Ack = await openFixture("sqag212-l3-normal-event-ack", l3AckSessionId);
      let l3NormalAckPassed = false;
      let l3NormalAckBounded = false;
      let l3NormalAckPersistence = false;
      let hold = null;
      try {
        const operationId = `sqag212/l3-normal-event-ack/${Date.now()}`;
        await prepareSession(l3Ack, l3AckSessionId, operationId, "l3-normal-event-ack");
        hold = l3Ack.tracker.holdTimerEventAcknowledgement(l3Ack.page, "create");
        await l3Ack.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 350 }));
        await hold.observed;
        const capturedEntry = l3Ack.tracker.queuedSaveStatus(hold.eventIdentity);
        const waitCount = l3Ack.tracker.summary().drainWaitCount;
        const drainPromise = l3Ack.tracker.drain(10000, l3Ack.page);
        const activeDrain = await waitForDrainPhase(l3Ack.tracker, "timer-event-delivery-acknowledgement", 3000);
        hold.release();
        const drained = await drainPromise;
        const report = l3Ack.tracker.summary().drainRuns.at(-1);
        const record = l3Ack.tracker.records.find((candidate) => candidate.operationId === operationId && candidate.method === "POST");
        l3NormalAckPassed = drained && l3Ack.tracker.issues.length === 0;
        l3NormalAckBounded = report?.identity === activeDrain.identity
          && report.budgetMs === 10000
          && report.outcome === "success";
        l3NormalAckPersistence = capturedEntry?.status === "succeeded"
          && queuedRecordIsDurable(record, { snapshot: capturedEntry.snapshot });
        if (!l3NormalAckPassed || !l3NormalAckBounded || !l3NormalAckPersistence
          || l3Ack.tracker.summary().drainWaitCount <= waitCount) {
          throw new Error("L3 normal event acknowledgement did not complete and persist within the original drain budget: " + JSON.stringify({
            drained, activeDrain, report, l3NormalAckPersistence, summary: l3Ack.tracker.summary(),
          }) + ".");
        }
        await cleanupSession(l3Ack.page, l3AckSessionId);
      } finally {
        hold?.release();
        await l3Ack.context.close();
      }

      const l3NativeSessionId = `quote-sqag212-l3-native-completion-${Date.now()}`;
      const l3Native = await openFixture("sqag212-l3-native-timer-completion", l3NativeSessionId);
      let l3NativePassed = false;
      let l3NativeBounded = false;
      let l3NativePersistence = false;
      try {
        const operationId = `sqag212/l3-native-timer-completion/${Date.now()}`;
        await prepareSession(l3Native, l3NativeSessionId, operationId, "l3-native-timer-completion");
        await l3Native.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 600 }));
        const captured = await l3Native.tracker.capturePendingQueuedSave(l3Native.page);
        if (!captured || captured.entry.producerKind !== "native-timer") {
          throw new Error("L3 native-completion positive did not register the actual timer as its producer.");
        }
        const drainPromise = l3Native.tracker.drain(10000, l3Native.page);
        l3NativePassed = await drainPromise;
        const report = l3Native.tracker.summary().drainRuns.at(-1);
        const record = l3Native.tracker.records.find((candidate) => candidate.operationId === operationId && candidate.method === "POST");
        l3NativeBounded = report?.budgetMs === 10000 && report.outcome === "success";
        l3NativePersistence = captured.entry.status === "succeeded" && queuedRecordIsDurable(record, captured);
        if (!l3NativePassed || !l3NativeBounded || !l3NativePersistence) {
          throw new Error("L3 native timer did not complete through the real application save and durable readback within budget: " + JSON.stringify({
            l3NativePassed, report, l3NativePersistence, record: record && { httpStatus: record.httpStatus, bodyStatus: record.bodyStatus, readback: record.readback },
            summary: l3Native.tracker.summary(),
          }) + ".");
        }
        await cleanupSession(l3Native.page, l3NativeSessionId);
      } finally {
        await l3Native.context.close();
      }

      const l3HeldAckSessionId = `quote-sqag212-l3-held-ack-${Date.now()}`;
      const l3HeldAck = await openFixture("sqag212-l3-held-event-ack", l3HeldAckSessionId);
      let l3HeldAckFailure = null;
      let l3HeldAckRetained = false;
      let l3HeldAckDeadlineIdentity = "";
      let l3HeldAckBudgetMs = 0;
      let l3HeldAckOutstandingIdentity = "";
      let l3HeldAckOutstandingState = "";
      const heldAck = l3HeldAck.tracker.holdTimerEventAcknowledgement(l3HeldAck.page, "create");
      try {
        const operationId = `sqag212/l3-held-event-ack/${Date.now()}`;
        await prepareSession(l3HeldAck, l3HeldAckSessionId, operationId, "l3-held-event-ack");
        await l3HeldAck.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        const observedEvent = await heldAck.observed;
        const entry = l3HeldAck.tracker.queuedSaveStatus(observedEvent.identity);
        if (!entry || entry.status !== "pending" || entry.producerKind !== "native-timer") {
          throw new Error("L3 held-ack negative did not register the actual required timer before holding its acknowledgement: " + JSON.stringify({
            event: observedEvent, entry: entry && { identity: entry.identity, status: entry.status, producerKind: entry.producerKind },
          }) + ".");
        }
        const drainPromise = l3HeldAck.tracker.drain(100, l3HeldAck.page);
        const heldRun = await waitForDrainPhase(l3HeldAck.tracker, "timer-event-delivery-acknowledgement", 3000);
        const drainPassed = await drainPromise;
        const summary = l3HeldAck.tracker.summary();
        const report = summary.drainRuns.find((candidate) => candidate.identity === heldRun.identity);
        const issue = summary.issues.find((candidate) => candidate.operationId === heldRun.identity
          && candidate.operation === "required-drain-deadline");
        const outstanding = report?.outstanding.find((candidate) => candidate.identity === entry.identity);
        l3HeldAckDeadlineIdentity = report?.identity || "";
        l3HeldAckBudgetMs = report?.budgetMs || 0;
        l3HeldAckOutstandingIdentity = outstanding?.identity || "";
        l3HeldAckOutstandingState = outstanding?.state || "";
        l3HeldAckFailure = !drainPassed && report?.outcome === "deadline-failure"
          && report.phase === "timer-event-delivery-acknowledgement"
          && report.deadlineUnchanged === true
          && report.identity === heldRun.identity
          && report.budgetMs === 100
          && outstanding?.identity === entry.identity
          && outstanding?.timerIdentity === entry.snapshot.timerIdentity
          && outstanding?.state === "pending"
          && Boolean(issue);
        if (!l3HeldAckFailure) {
          throw new Error("L3 held event acknowledgement was not bounded by its own absolute deadline with outstanding identity/state evidence: " + JSON.stringify({
            drainPassed, heldRun, report, outstanding, issue, summary,
          }) + ".");
        }
        heldAck.release();
        await l3HeldAck.page.evaluate(() => clearQuoteSessionDraftSaveTimer());
        l3HeldAckRetained = l3HeldAck.tracker.issues.some((candidate) => candidate.operationId === heldRun.identity)
          && l3HeldAck.tracker.drainReports.find((candidate) => candidate.identity === heldRun.identity)?.outcome === "deadline-failure";
        if (!l3HeldAckRetained) {
          throw new Error("L3 late event acknowledgement erased the first invocation's sticky deadline result.");
        }
        await cleanupSession(l3HeldAck.page, l3HeldAckSessionId);
      } finally {
        heldAck.release();
        await l3HeldAck.context.close();
      }

      const l3FinalInsideSessionId = `quote-sqag212-l3-final-zero-inside-${Date.now()}`;
      const l3FinalInside = await openFixture("sqag212-l3-final-zero-arrival-inside-budget", l3FinalInsideSessionId);
      let l3FinalInsideResult = false;
      let l3FinalInsideDeadlineIdentity = "";
      let l3FinalInsideBudgetMs = 0;
      let l3FinalInsidePersistence = false;
      let l3FinalInsideBarrier = null;
      try {
        const operationId = `sqag212/l3-final-zero-inside/${Date.now()}`;
        l3FinalInsideBarrier = l3FinalInside.tracker.armFinalReconciliationBarrier();
        const waitCount = l3FinalInside.tracker.summary().drainWaitCount;
        const drainPromise = l3FinalInside.tracker.drain(10000, l3FinalInside.page);
        await l3FinalInsideBarrier.reached;
        const initialRun = l3FinalInside.tracker.summary().activeDrainRuns[0];
        await prepareSession(l3FinalInside, l3FinalInsideSessionId, operationId, "l3-final-zero-inside");
        await l3FinalInside.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        const captured = await l3FinalInside.tracker.capturePendingQueuedSave(l3FinalInside.page);
        if (!captured) throw new Error("L3 final-zero inside-budget case failed to capture arriving required work.");
        const arrivalRun = l3FinalInside.tracker.summary().activeDrainRuns.find((run) => run.identity === initialRun?.identity);
        const unchangedBeforeRelease = arrivalRun?.budgetMs === 10000
          && arrivalRun?.deadlineMonotonicMs === initialRun?.deadlineMonotonicMs;
        l3FinalInsideBarrier.release();
        const flush = await flushRequiredQuoteSessionSaves(l3FinalInside.page, l3FinalInside.tracker, captured);
        l3FinalInsideResult = await drainPromise;
        const report = l3FinalInside.tracker.summary().drainRuns.find((candidate) => candidate.identity === initialRun?.identity);
        const record = l3FinalInside.tracker.records.find((candidate) => candidate.operationId === operationId && candidate.method === "POST");
        l3FinalInsideDeadlineIdentity = report?.identity || "";
        l3FinalInsideBudgetMs = report?.budgetMs || 0;
        l3FinalInsidePersistence = flush.status === "succeeded" && queuedRecordIsDurable(record, captured);
        if (!unchangedBeforeRelease || !l3FinalInsideResult || !l3FinalInsidePersistence
          || report?.outcome !== "success" || report?.budgetMs !== 10000
          || l3FinalInside.tracker.summary().drainRuns.filter((candidate) => candidate.identity === initialRun?.identity).length !== 1
          || l3FinalInside.tracker.summary().drainWaitCount < waitCount) {
          throw new Error("L3 final-zero arrival inside budget reset the drain or failed to await real durable work: " + JSON.stringify({
            initialRun, arrivalRun, unchangedBeforeRelease, report, flush, l3FinalInsidePersistence,
            summary: l3FinalInside.tracker.summary(),
          }) + ".");
        }
        await cleanupSession(l3FinalInside.page, l3FinalInsideSessionId);
      } finally {
        l3FinalInsideBarrier?.release();
        await l3FinalInside.context.close();
      }

      const l3FinalLateSessionId = `quote-sqag212-l3-final-zero-late-${Date.now()}`;
      const l3FinalLate = await openFixture("sqag212-l3-final-zero-arrival-held-past-budget", l3FinalLateSessionId);
      let l3FinalLateFailure = false;
      let l3FinalLateDeadlineIdentity = "";
      let l3FinalLateBudgetMs = 0;
      let l3FinalLateOutstandingIdentity = "";
      let l3FinalLateOutstandingState = "";
      let l3FinalLatePersistence = false;
      let l3FinalLateBarrier = null;
      let releaseL3FinalPost = () => {};
      let l3FinalPostReachedResolve;
      const l3FinalPostReached = new Promise((resolve) => { l3FinalPostReachedResolve = resolve; });
      let releaseL3FinalPostResolve;
      const l3FinalPostGate = new Promise((resolve) => { releaseL3FinalPostResolve = resolve; });
      releaseL3FinalPost = () => releaseL3FinalPostResolve();
      let l3FinalRouteError = null;
      let l3FinalRouteDoneResolve;
      const l3FinalRouteDone = new Promise((resolve) => { l3FinalRouteDoneResolve = resolve; });
      try {
        const operationId = `sqag212/l3-final-zero-late/${Date.now()}`;
        l3FinalLateBarrier = l3FinalLate.tracker.armFinalReconciliationBarrier();
        const drainPromise = l3FinalLate.tracker.drain(1200, l3FinalLate.page);
        await l3FinalLateBarrier.reached;
        const initialRun = l3FinalLate.tracker.summary().activeDrainRuns[0];
        await prepareSession(l3FinalLate, l3FinalLateSessionId, operationId, "l3-final-zero-late");
        await l3FinalLate.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        const captured = await l3FinalLate.tracker.capturePendingQueuedSave(l3FinalLate.page);
        if (!captured) throw new Error("L3 final-zero late case failed to capture arriving required work.");
        const arrivalRun = l3FinalLate.tracker.summary().activeDrainRuns.find((run) => run.identity === initialRun?.identity);
        const unchangedBeforeRelease = arrivalRun?.budgetMs === 1200
          && arrivalRun?.deadlineMonotonicMs === initialRun?.deadlineMonotonicMs;
        await l3FinalLate.page.route("**/api/quote-sessions", async (route) => {
          try {
            const response = await route.fetch();
            l3FinalPostReachedResolve({ status: response.status() });
            await l3FinalPostGate;
            await route.fulfill({ response });
          } catch (error) {
            l3FinalRouteError = String(error?.message || error);
          } finally {
            l3FinalRouteDoneResolve();
          }
        });
        l3FinalLateBarrier.release();
        const start = await l3FinalLate.tracker.startCapturedQueuedSave(l3FinalLate.page, captured);
        if (start.status !== "started") throw new Error("L3 late final-zero case did not start the actual successor save: " + JSON.stringify(start) + ".");
        const post = await Promise.race([
          l3FinalPostReached,
          new Promise((_, reject) => setTimeout(() => reject(new Error("L3 final-zero held save did not reach its real POST response boundary.")), 10000)),
        ]);
        if (post.status < 200 || post.status >= 300) throw new Error("L3 final-zero held save had no successful upstream response: " + JSON.stringify(post) + ".");
        const drainPassed = await drainPromise;
        const report = l3FinalLate.tracker.summary().drainRuns.find((candidate) => candidate.identity === initialRun?.identity);
        const outstanding = report?.outstanding.find((candidate) => candidate.identity === captured.entry.identity);
        l3FinalLateDeadlineIdentity = report?.identity || "";
        l3FinalLateBudgetMs = report?.budgetMs || 0;
        l3FinalLateOutstandingIdentity = outstanding?.identity || "";
        l3FinalLateOutstandingState = outstanding?.state || "";
        l3FinalLateFailure = !drainPassed && unchangedBeforeRelease
          && report?.outcome === "deadline-failure"
          && report.deadlineUnchanged === true
          && report.identity === initialRun?.identity
          && report.budgetMs === 1200
          && outstanding?.identity === captured.entry.identity
          && outstanding?.timerIdentity === captured.snapshot.timerIdentity
          && Boolean(outstanding?.state)
          && l3FinalLate.tracker.issues.some((candidate) => candidate.operationId === initialRun?.identity);
        if (!l3FinalLateFailure) {
          releaseL3FinalPost();
          throw new Error("L3 final-zero arrival held past the original budget did not fail that same drain with retained work: " + JSON.stringify({
            drainPassed, initialRun, arrivalRun, unchangedBeforeRelease, report, outstanding, summary: l3FinalLate.tracker.summary(),
          }) + ".");
        }
        releaseL3FinalPost();
        await captured.entry.terminalPromise;
        if (l3FinalRouteError) throw new Error("L3 late real save response could not complete: " + l3FinalRouteError + ".");
        const record = l3FinalLate.tracker.records.find((candidate) => candidate.operationId === operationId && candidate.method === "POST");
        l3FinalLatePersistence = captured.entry.status === "succeeded" && queuedRecordIsDurable(record, captured);
        const firstReport = l3FinalLate.tracker.drainReports.find((candidate) => candidate.identity === initialRun?.identity);
        const firstIssue = l3FinalLate.tracker.issues.find((candidate) => candidate.operationId === initialRun?.identity);
        if (!l3FinalLatePersistence || firstReport?.outcome !== "deadline-failure" || !firstIssue) {
          throw new Error("L3 late persistence changed the original deadline failure or lacked durable request/readback evidence: " + JSON.stringify({
            l3FinalLatePersistence, firstReport, firstIssue, summary: l3FinalLate.tracker.summary(),
          }) + ".");
        }
        await l3FinalLate.page.unroute("**/api/quote-sessions");
        await cleanupSession(l3FinalLate.page, l3FinalLateSessionId);
      } finally {
        l3FinalLateBarrier?.release();
        releaseL3FinalPost();
        await Promise.race([l3FinalRouteDone, new Promise((resolve) => setTimeout(resolve, 1000))]);
        await l3FinalLate.page.unroute("**/api/quote-sessions").catch(() => {});
        await l3FinalLate.context.close();
      }

      // R1/R2: save correlation ordering and readback supersession controls. Every positive uses
      // the real queue, real save invocation, real fetch/POST, real response, and durable readback.
      const withTimeout = (promise, ms, label) => Promise.race([
        promise,
        new Promise((_, reject) => setTimeout(() => reject(new Error(typeof label === "function" ? label() : label)), ms)),
      ]);
      const waitForTrackedPost = async (fixtureTracker, operationId, timeoutMs = 10000) => {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          const record = fixtureTracker.records.find((candidate) => candidate.operationId === operationId && candidate.method === "POST");
          if (record) return record;
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        throw new Error(`Required POST ${operationId} was not observed within ${timeoutMs} ms.`);
      };
      const waitForCondition = async (predicate, timeoutMs, label) => {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          if (predicate()) return;
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        throw new Error(label);
      };
      const draftStatesEqual = (left, right) => Boolean(left && right)
        && JSON.stringify(queuedDraftStateComparable(left)) === JSON.stringify(queuedDraftStateComparable(right));
      const draftFilesEqual = (left, right) => Array.isArray(left) && Array.isArray(right)
        && JSON.stringify(queuedDraftFilesComparable(left)) === JSON.stringify(queuedDraftFilesComparable(right));
      const postsFor = (fixtureTracker, operationId) => fixtureTracker.records.filter((candidate) => (
        candidate.operationId === operationId && candidate.method === "POST"
      ));
      const saveInvocationDurable = (record, entry) => {
        const saveSnapshot = entry?.saveInvocationSnapshot;
        return Boolean(
          record && saveSnapshot
          && record.httpStatus >= 200 && record.httpStatus < 300
          && record.bodyStatus === "saved"
          && record.responseSessionId === saveSnapshot.sessionId
          && record.clientResult?.nonNull === true
          && record.clientResult?.sessionId === saveSnapshot.sessionId
          && record.readback?.httpStatus === 200
          && record.readback?.sessionId === saveSnapshot.sessionId
          && record.readback?.quoteGenerated === saveSnapshot.options?.quoteGenerated
          && record.readbackVerdict === "exact"
          && draftStatesEqual(record.payloadDraftState, saveSnapshot.draftState)
          && draftStatesEqual(record.readback?.draftState, saveSnapshot.draftState)
          && draftFilesEqual(record.readback?.draftFiles, saveSnapshot.draftFiles)
        );
      };
      const startQueuedFlush = async (fixture, sessionId, operationId, operation) => {
        await prepareSession(fixture, sessionId, operationId, operation);
        await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        const captured = await fixture.tracker.capturePendingQueuedSave(fixture.page);
        if (!captured) throw new Error(`${operation} did not capture its real queued save.`);
        const start = await fixture.tracker.startCapturedQueuedSave(fixture.page, captured);
        if (start.status !== "started") throw new Error(`${operation} did not start its real queued save: ${JSON.stringify(start)}.`);
        return captured;
      };
      // A successor save driven only by the real native timer. It deliberately avoids harness timer
      // capture, which waits on every pending timer event (including a held older save's outcome).
      const runNativeTimerSave = async (fixture, sessionId, operationId, operation, { awaitTerminal = true } = {}) => {
        await prepareSession(fixture, sessionId, operationId, operation);
        await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 300 }));
        const record = await waitForTrackedPost(fixture.tracker, operationId, 15000);
        const entry = fixture.tracker.queuedSaveStatus(record.queuedSaveIdentity);
        if (!entry) throw new Error(`${operation} POST was not correlated to a real queued timer save.`);
        if (awaitTerminal) await withTimeout(entry.terminalPromise, 20000, `${operation} native timer save did not settle.`);
        return { record, entry };
      };
      const fulfilUnpersistedSave = async (route) => {
        // Negative control only: answer "saved" with the unchanged stored session, without writing.
        const request = route.request();
        let payload = {};
        try { payload = request.postDataJSON() || {}; } catch {}
        const origin = new URL(request.url()).origin;
        const stored = await fetch(`${origin}/api/quote-sessions/${encodeURIComponent(String(payload.session_id || ""))}?__sqag_smoke_r2_unpersisted=${Date.now()}`, {
          cache: "no-store",
          signal: AbortSignal.timeout(15000),
        });
        const storedBody = await stored.json();
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "saved", quote_session: storedBody?.quote_session || null }),
        });
      };

      const runR1OrderingCase = async (fixture, sessionId, name, { order, evolveState }) => {
        const operationId = `sqag212/${name}/${Date.now()}`;
        let handlingHold = null;
        try {
          await prepareSession(fixture, sessionId, operationId, name);
          if (order === "post-before-save-start") {
            handlingHold = fixture.tracker.holdTimerEventHandling(fixture.page, "save-start");
          } else {
            await fixture.page.evaluate(() => {
              const innerFetch = window.fetch;
              let release;
              window.__sqagR1PostGate = new Promise((resolve) => { release = resolve; });
              window.__sqagR1ReleasePost = () => release();
              window.__sqagR1PostGateReached = false;
              window.fetch = (input, init = {}) => {
                let url = null;
                try { url = new URL(typeof input === "string" ? input : input.url, window.location.href); } catch {}
                const method = String(init.method || input?.method || "GET").toUpperCase();
                if (url && method === "POST" && url.pathname === "/api/quote-sessions" && window.__sqagR1PostGate) {
                  const gate = window.__sqagR1PostGate;
                  window.__sqagR1PostGate = null;
                  window.__sqagR1PostGateReached = true;
                  window.fetch = innerFetch;
                  return gate.then(() => innerFetch(input, init));
                }
                return innerFetch(input, init);
              };
            });
          }
          await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 400 }));
          const captured = await fixture.tracker.capturePendingQueuedSave(fixture.page);
          if (!captured || captured.entry.producerKind !== "native-timer") {
            throw new Error(`${name} did not register its real native timer producer.`);
          }
          const queueSnapshot = structuredClone(captured.snapshot);
          const entry = captured.entry;
          if (evolveState) {
            // Legitimate post-queue, pre-fire state evolution; the save must be judged on save-time state.
            await fixture.page.evaluate(() => { state.outputSortMode = "pricing_reference"; });
          }
          const ordering = {};
          let record;
          if (order === "post-before-save-start") {
            await withTimeout(handlingHold.observed, 10000, `${name} native timer save-start was not delivered.`);
            record = await waitForTrackedPost(fixture.tracker, operationId);
            ordering.postObservedWhileSaveStartUndelivered = !entry.saveInvocationSnapshot
              && record.correlationState === "awaiting-save-invocation-snapshot"
              && entry.status === "pending";
            // With evolution the stale queue-time snapshot must differ from the real POST (the old
            // false rejection); without evolution the queue-time snapshot equals the POST.
            ordering.queueSnapshotVersusPost = evolveState
              ? !draftStatesEqual(queueSnapshot.draftState, record.payloadDraftState)
              : draftStatesEqual(queueSnapshot.draftState, record.payloadDraftState);
            await withTimeout(record.terminalPromise, 15000, `${name} real POST response/readback did not complete.`);
            ordering.responseCompletedBeforeSaveStart = !entry.saveInvocationSnapshot && entry.status === "pending";
            handlingHold.release();
          } else {
            await fixture.page.waitForFunction(() => window.__sqagR1PostGateReached === true, null, { timeout: 10000 });
            await waitForCondition(() => Boolean(entry.saveInvocationSnapshot), 10000, `${name} save-start was not handled before the POST.`);
            ordering.saveStartHandledBeforePost = postsFor(fixture.tracker, operationId).length === 0;
            await fixture.page.evaluate(() => window.__sqagR1ReleasePost());
            record = await waitForTrackedPost(fixture.tracker, operationId);
          }
          const drained = await fixture.tracker.drain(15000, fixture.page);
          const saveSnapshot = entry.saveInvocationSnapshot;
          const checks = {
            ...ordering,
            drained,
            noIssues: fixture.tracker.issues.length === 0,
            succeeded: entry.status === "succeeded",
            nativeTimerFired: entry.dispatchedBy === "fire",
            observedOrder: entry.correlationEvidence?.order === order,
            correlatedAgainstSaveInvocation: record.correlationState === "correlated-by-save-invocation-snapshot"
              && entry.correlationEvidence?.correlatedAgainst === "save-invocation-snapshot",
            exactIdentity: record.queuedSaveIdentity === entry.snapshot.timerIdentity,
            queueToSaveState: evolveState
              ? !draftStatesEqual(queueSnapshot.draftState, saveSnapshot?.draftState)
              : draftStatesEqual(queueSnapshot.draftState, saveSnapshot?.draftState),
            saveStateEqualsPost: draftStatesEqual(saveSnapshot?.draftState, record.payloadDraftState),
            durableReadback: saveInvocationDurable(record, entry),
            exactlyOnePost: postsFor(fixture.tracker, operationId).length === 1,
            // R1B: the single real POST is the exclusive owner of its queued identity.
            exclusiveRequestOwnership: entry.ownership?.ownerRecord === record
              && entry.ownership.currentAttempt === record
              && entry.ownership.claims.length === 1
              && entry.ownership.sticky.length === 0
              && record.transportAttempt === 1,
            // R2B: persistence was proven by this save's own authenticated state/file detail read.
            independentOwnProof: record.ownProof?.state === "accepted" && record.ownProof.consequential === true
              && entry.evidence.persistenceTransportId === record.transportId,
          };
          if (Object.values(checks).some((passed) => !passed)) {
            throw new Error(`${name} did not correlate the real save by its save-invocation snapshot: ${JSON.stringify({
              checks,
              filesRepresentation: record.payloadDraftFiles === null ? "omitted-confirmed-files" : "present",
              summary: fixture.tracker.summary(),
            })}.`);
          }
          return {
            result: "PASS",
            order,
            evolvedState: evolveState,
            timerIdentity: entry.snapshot.timerIdentity,
            operationId,
            postFilesRepresentation: record.payloadDraftFiles === null ? "omitted-confirmed-files" : "present",
            checks,
          };
        } finally {
          handlingHold?.release();
        }
      };

      const r1PositiveSessionId = `quote-sqag212-r1-positive-${Date.now()}`;
      const r1Positive = await openFixture("sqag212-r1-save-correlation-ordering", r1PositiveSessionId);
      let r1PostBeforeEvolved;
      let r1PostBeforeSame;
      let r1SaveStartBefore;
      try {
        r1PostBeforeEvolved = await runR1OrderingCase(r1Positive, r1PositiveSessionId, "r1-post-before-save-start-evolved", { order: "post-before-save-start", evolveState: true });
        r1PostBeforeSame = await runR1OrderingCase(r1Positive, r1PositiveSessionId, "r1-post-before-save-start-same-state", { order: "post-before-save-start", evolveState: false });
        r1SaveStartBefore = await runR1OrderingCase(r1Positive, r1PositiveSessionId, "r1-save-start-before-post-evolved", { order: "save-start-before-post", evolveState: true });
        await cleanupSession(r1Positive.page, r1PositiveSessionId);
      } finally {
        await r1Positive.context.close();
      }

      const r1WrongSessionId = `quote-sqag212-r1-wrong-timer-${Date.now()}`;
      const r1Wrong = await openFixture("sqag212-r1-wrong-timer-post", r1WrongSessionId);
      let r1WrongTimer;
      try {
        const operationA = `sqag212/r1-wrong-timer-source-a/${Date.now()}`;
        const capturedA = await startQueuedFlush(r1Wrong, r1WrongSessionId, operationA, "r1-wrong-timer-source-a");
        await withTimeout(capturedA.entry.terminalPromise, 15000, "R1 wrong-timer source save did not settle.");
        const recordA = postsFor(r1Wrong.tracker, operationA)[0];
        if (capturedA.entry.status !== "succeeded" || !recordA) {
          throw new Error("R1 wrong-timer control source save did not persist: " + JSON.stringify(r1Wrong.tracker.summary()) + ".");
        }
        const operationX = `sqag212/r1-wrong-timer-target-x/${Date.now()}`;
        await prepareSession(r1Wrong, r1WrongSessionId, operationX, "r1-wrong-timer-target-x");
        await r1Wrong.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 }));
        const capturedX = await r1Wrong.tracker.capturePendingQueuedSave(r1Wrong.page);
        if (!capturedX || capturedX.snapshot.timerIdentity === capturedA.snapshot.timerIdentity) {
          throw new Error("R1 wrong-timer control did not create a distinct target timer.");
        }
        const replayOperation = `sqag212/r1-wrong-timer-replay/${Date.now()}`;
        await r1Wrong.tracker.setPageOperation(r1Wrong.page, {
          fixture: "sqag212",
          operation: "r1-wrong-timer-replay",
          operationId: replayOperation,
          expectedSessionId: r1WrongSessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        // A real POST carrying timer A's generation, presented under timer X's identity.
        const replay = await r1Wrong.page.evaluate(async ({ header, identity, body }) => {
          const headers = { "content-type": "application/json", [header]: identity };
          if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
          const response = await fetch("/api/quote-sessions", { method: "POST", headers, body });
          // Consume the body so the real response completes for every observer.
          const text = await response.text();
          return { status: response.status, bodyBytes: text.length };
        }, { header: quoteSessionQueuedSaveHeader, identity: capturedX.snapshot.browserTimerIdentity, body: recordA.request.postData() });
        const replayRecord = await waitForTrackedPost(r1Wrong.tracker, replayOperation);
        // The replay is not an application save, so its client-result wait runs to its own timeout.
        await withTimeout(replayRecord.terminalPromise, 30000, () => "R1 wrong-timer replay did not complete: " + JSON.stringify({
          replay,
          httpStatus: replayRecord.httpStatus,
          bodyStatus: replayRecord.bodyStatus,
          correlationState: replayRecord.correlationState,
          readbackVerdict: replayRecord.readbackVerdict,
          done: replayRecord.done,
        }));
        const deferredBeforeSaveStart = replayRecord.correlationState === "awaiting-save-invocation-snapshot";
        const startX = await r1Wrong.tracker.startCapturedQueuedSave(r1Wrong.page, capturedX);
        await withTimeout(capturedX.entry.terminalPromise, 15000, "R1 wrong-timer target did not settle.");
        const drained = await r1Wrong.tracker.drain(3000, r1Wrong.page);
        const checks = {
          replayWasRealSuccessfulPost: replay.status === 200 && replayRecord.httpStatus === 200,
          replayCarriedTargetIdentity: replayRecord.queuedSaveIdentity === capturedX.snapshot.timerIdentity,
          deferredBeforeSaveStart,
          targetSaveStarted: startX.status === "started",
          targetFailed: capturedX.entry.status === "failed"
            && String(capturedX.entry.terminalReason || "").includes("save-invocation snapshot"),
          replayRejected: replayRecord.correlationState === "rejected"
            && r1Wrong.tracker.issues.some((issue) => issue.operationId === replayOperation),
          drainFailed: drained === false,
        };
        if (Object.values(checks).some((passed) => !passed)) {
          throw new Error("R1 wrong-timer POST was not rejected: " + JSON.stringify({ checks, summary: r1Wrong.tracker.summary() }) + ".");
        }
        r1WrongTimer = { result: "PASS", checks, targetTimerIdentity: capturedX.snapshot.timerIdentity, sourceTimerIdentity: capturedA.snapshot.timerIdentity };
        await cleanupSession(r1Wrong.page, r1WrongSessionId);
      } finally {
        await r1Wrong.context.close();
      }

      const r2PositiveSessionId = `quote-sqag212-r2-positive-${Date.now()}`;
      const r2Positive = await openFixture("sqag212-r2-readback-supersession", r2PositiveSessionId);
      let r2ReadbackBeforeB;
      let r2Supersedes;
      try {
        const readbackFirstA = `sqag212/r2-readback-first-a/${Date.now()}`;
        const firstA = await startQueuedFlush(r2Positive, r2PositiveSessionId, readbackFirstA, "r2-readback-first-a");
        await withTimeout(firstA.entry.terminalPromise, 15000, "R2 readback-first A did not settle.");
        const readbackFirstB = `sqag212/r2-readback-first-b/${Date.now()}`;
        const firstB = await startQueuedFlush(r2Positive, r2PositiveSessionId, readbackFirstB, "r2-readback-first-b");
        await withTimeout(firstB.entry.terminalPromise, 15000, "R2 readback-first B did not settle.");
        const firstRecordA = postsFor(r2Positive.tracker, readbackFirstA)[0];
        const firstRecordB = postsFor(r2Positive.tracker, readbackFirstB)[0];
        const readbackFirstChecks = {
          drained: await r2Positive.tracker.drain(15000, r2Positive.page),
          noIssues: r2Positive.tracker.issues.length === 0,
          aSucceeded: firstA.entry.status === "succeeded",
          bSucceeded: firstB.entry.status === "succeeded",
          aExact: firstRecordA?.readbackVerdict === "exact" && draftStatesEqual(firstRecordA.readback?.draftState, firstRecordA.payloadDraftState),
          bExact: firstRecordB?.readbackVerdict === "exact",
          aIndependentOwnProof: firstRecordA?.ownProof?.state === "accepted" && firstRecordA.ownProof.consequential === true,
          bIndependentOwnProof: firstRecordB?.ownProof?.state === "accepted" && firstRecordB.ownProof.consequential === true,
          aProvenBeforeBAdmitted: firstRecordB?.requestSeq > firstRecordA?.ownProof?.acceptedSeq,
        };
        if (Object.values(readbackFirstChecks).some((passed) => !passed)) {
          throw new Error("R2 A-readback-before-B positive failed: " + JSON.stringify({ readbackFirstChecks, summary: r2Positive.tracker.summary() }) + ".");
        }
        r2ReadbackBeforeB = { result: "PASS", checks: readbackFirstChecks };

        const supersededA = `sqag212/r2-superseded-a/${Date.now()}`;
        const readbackHold = r2Positive.tracker.holdDurableReadback(supersededA);
        let capturedA;
        try {
          const olderA = await runNativeTimerSave(r2Positive, r2PositiveSessionId, supersededA, "r2-superseded-a", { awaitTerminal: false });
          capturedA = { entry: olderA.entry, snapshot: olderA.entry.snapshot };
          await withTimeout(readbackHold.reached, 15000, "R2 A readback did not reach its hold after A's response.");
          const supersedingB = `sqag212/r2-superseding-b/${Date.now()}`;
          const successorB = await runNativeTimerSave(r2Positive, r2PositiveSessionId, supersedingB, "r2-superseding-b");
          const capturedB = { entry: successorB.entry, snapshot: successorB.entry.snapshot };
          readbackHold.release();
          await withTimeout(capturedA.entry.terminalPromise, 15000, "R2 superseded A did not settle.");
          const recordA = postsFor(r2Positive.tracker, supersededA)[0];
          const recordB = postsFor(r2Positive.tracker, supersedingB)[0];
          const checks = {
            drained: await r2Positive.tracker.drain(15000, r2Positive.page),
            noIssues: r2Positive.tracker.issues.length === 0,
            distinctIdentities: capturedA.snapshot.timerIdentity !== capturedB.snapshot.timerIdentity,
            aSucceeded: capturedA.entry.status === "succeeded",
            bSucceeded: capturedB.entry.status === "succeeded",
            aOwnCommitSaved: recordA?.httpStatus === 200 && recordA.bodyStatus === "saved",
            aSuperseded: recordA?.readbackVerdict === "superseded"
              && recordA.readbackSupersession?.successorOperationId === recordB?.operationId,
            bLaterIssued: r2Positive.tracker.records.indexOf(recordB) > r2Positive.tracker.records.indexOf(recordA),
            bExact: recordB?.readbackVerdict === "exact",
            aReadbackIsB: draftStatesEqual(recordA?.readback?.draftState, recordB?.payloadDraftState)
              && !draftStatesEqual(recordA?.readback?.draftState, recordA?.payloadDraftState),
            causalCommitOrder: recordA?.readbackSupersession?.successorCommitClock > recordA?.readbackSupersession?.ownCommitClock
              && recordA?.readbackSupersession?.readbackCommitClock === recordA?.readbackSupersession?.successorCommitClock,
            filesPreserved: draftFilesEqual(recordA?.readback?.draftFiles, capturedA.snapshot.draftFiles),
            // R2B: A proved its own state/files before B was admitted, B proved itself, and A's
            // later readback is exactly B's proven representation.
            aIndependentlyProvenFirst: recordA?.ownProof?.state === "accepted" && recordA.ownProof.consequential === true
              && recordB?.requestSeq > recordA.ownProof.acceptedSeq
              && draftStatesEqual(recordA.ownProof.readback?.draftState, recordA.payloadDraftState),
            bIndependentlyProven: recordB?.ownProof?.state === "accepted" && recordB.ownProof.consequential === true,
            aReadbackIsBProvenRepresentation: draftStatesEqual(recordA?.readback?.draftState, recordB?.ownProof?.readback?.draftState)
              && draftFilesEqual(recordA?.readback?.draftFiles, recordB?.ownProof?.readback?.draftFiles)
              && recordA?.readback?.updatedAt === recordB?.ownProof?.readback?.updatedAt,
          };
          if (Object.values(checks).some((passed) => !passed)) {
            throw new Error("R2 valid A-to-B supersession was not recognised: " + JSON.stringify({ checks, summary: r2Positive.tracker.summary() }) + ".");
          }
          r2Supersedes = { result: "PASS", checks, supersession: recordA.readbackSupersession };
        } finally {
          readbackHold.release();
        }
        await cleanupSession(r2Positive.page, r2PositiveSessionId);
      } finally {
        await r2Positive.context.close();
      }

      const runR2NegativeCase = async (name, { withSuccessor }) => {
        const sessionId = `quote-sqag212-${name}-${Date.now()}`;
        const fixture = await openFixture(`sqag212-${name}`, sessionId);
        let readbackHold = null;
        try {
          const baselineOperation = `sqag212/${name}-baseline/${Date.now()}`;
          const baseline = await startQueuedFlush(fixture, sessionId, baselineOperation, `${name}-baseline`);
          await withTimeout(baseline.entry.terminalPromise, 15000, `${name} baseline save did not settle.`);
          if (baseline.entry.status !== "succeeded") throw new Error(`${name} baseline save did not persist.`);
          let unpersistedArmed = true;
          await fixture.page.route("**/api/quote-sessions", async (route) => {
            if (route.request().method() === "POST" && unpersistedArmed) {
              unpersistedArmed = false;
              await fulfilUnpersistedSave(route);
              return;
            }
            await route.continue();
          });
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          if (withSuccessor) readbackHold = fixture.tracker.holdDurableReadback(operationA);
          const olderA = await runNativeTimerSave(fixture, sessionId, operationA, `${name}-a`, { awaitTerminal: false });
          const capturedA = { entry: olderA.entry, snapshot: olderA.entry.snapshot };
          let recordB = null;
          let capturedB = null;
          if (withSuccessor) {
            await withTimeout(readbackHold.reached, 15000, `${name} A readback did not reach its hold.`);
            const operationB = `sqag212/${name}-b/${Date.now()}`;
            const successorB = await runNativeTimerSave(fixture, sessionId, operationB, `${name}-b`);
            capturedB = { entry: successorB.entry };
            recordB = successorB.record;
            readbackHold.release();
          }
          await withTimeout(capturedA.entry.terminalPromise, 15000, `${name} A did not settle.`);
          const recordA = postsFor(fixture.tracker, operationA)[0];
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const checks = {
            aClaimedSaved: recordA?.httpStatus === 200 && recordA.bodyStatus === "saved",
            aOwnProofFailed: recordA?.ownProof?.state === "failed",
            aFailed: capturedA.entry.status === "failed",
            aReadbackFailed: recordA?.readbackVerdict === "failed",
            aIssueRetained: fixture.tracker.issues.some((issue) => issue.operationId === operationA),
            drainFailed: drained === false,
          };
          if (withSuccessor) {
            checks.bSucceeded = capturedB?.entry.status === "succeeded" && recordB?.readbackVerdict === "exact";
            checks.aReadbackObservedB = draftStatesEqual(recordA?.readback?.draftState, recordB?.payloadDraftState);
            checks.launderingRejectedForUnprovenCommit = String(recordA?.readbackSupersessionRejection?.reason || "")
              .includes("own persistence proof was not accepted");
          } else {
            // Either the unpersisted save's missing own proof or the absence of any successor rejects it.
            checks.supersessionRejected = /own persistence proof was not accepted|no independently proven later/.test(String(recordA?.readbackSupersessionRejection?.reason || ""));
          }
          if (Object.values(checks).some((passed) => !passed)) {
            throw new Error(`${name} did not keep the unpersisted older save failed: ${JSON.stringify({ checks, summary: fixture.tracker.summary() })}.`);
          }
          await fixture.page.unroute("**/api/quote-sessions");
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks, rejection: recordA.readbackSupersessionRejection };
        } finally {
          readbackHold?.release();
          await fixture.page.unroute("**/api/quote-sessions").catch(() => {});
          await fixture.context.close();
        }
      };
      const r2NotPersisted = await runR2NegativeCase("r2-older-save-not-persisted", { withSuccessor: false });
      const r2NoLaundering = await runR2NegativeCase("r2-successor-cannot-launder", { withSuccessor: true });
      const r1Passed = [r1PostBeforeEvolved, r1WrongTimer, r1SaveStartBefore, r1PostBeforeSame].every((item) => item?.result === "PASS");
      const r2Passed = [r2NotPersisted, r2Supersedes, r2ReadbackBeforeB, r2NoLaundering].every((item) => item?.result === "PASS");

      // Window separation: the R1/R2 controls issue many real quote-session saves from the same
      // loopback client. Let the server's existing 60-second quote-session rate-limit window fully
      // elapse (plus a fixed margin) so later controls such as F4 run with their normal budget.
      // The limiter, client identity, and server process are unchanged.
      const r1r2RateWindowSeparation = { windowMs: 60000, marginMs: 3000, lastRequestAt: Date.now() };
      await new Promise((resolve) => setTimeout(resolve, r1r2RateWindowSeparation.windowMs + r1r2RateWindowSeparation.marginMs));
      r1r2RateWindowSeparation.releasedAt = Date.now();
      r1r2RateWindowSeparation.elapsedMs = r1r2RateWindowSeparation.releasedAt - r1r2RateWindowSeparation.lastRequestAt;
      if (r1r2RateWindowSeparation.elapsedMs <= r1r2RateWindowSeparation.windowMs) {
        throw new Error("R1/R2 rate-window separation did not exceed the limiter window: " + JSON.stringify(r1r2RateWindowSeparation) + ".");
      }

      // R1B/R2B: exclusive request ownership and independent own-proof controls. Every request is
      // a real browser request to the real server. Negatives use real adversarial browser requests
      // or real storage-loss transports; no tracker record or success is fabricated.
      const separateQuoteSessionRateWindow = async (label) => {
        // Same accepted pacing as the R1/R2 separation: let the unchanged 60-second quote-session
        // limiter window fully elapse so each control group runs within its normal budget.
        const evidence = { label, windowMs: 60000, marginMs: 3000, lastRequestAt: Date.now() };
        await new Promise((resolve) => setTimeout(resolve, evidence.windowMs + evidence.marginMs));
        evidence.releasedAt = Date.now();
        evidence.elapsedMs = evidence.releasedAt - evidence.lastRequestAt;
        if (evidence.elapsedMs <= evidence.windowMs) {
          throw new Error(`${label} rate-window separation did not exceed the limiter window: ${JSON.stringify(evidence)}.`);
        }
        return evidence;
      };
      // The app accepts quote-<3..64 safe characters>; keep synthetic ids inside that bound.
      const boundedSessionId = (name, role) => `quote-s212-${String(name).slice(0, 40)}-${role}${Date.now().toString(36)}`;
      const ownershipTrackers = [];
      const openOwnershipFixture = async (name, sessionId) => {
        const fixture = await openFixture(name, sessionId);
        ownershipTrackers.push(fixture.tracker);
        return fixture;
      };
      const failedChecks = (checks) => Object.entries(checks).filter(([, passed]) => !passed).map(([name]) => name);
      const requireChecks = (name, checks, fixture) => {
        if (failedChecks(checks).length) {
          throw new Error(`${name} failed ${JSON.stringify(failedChecks(checks))}: ${JSON.stringify({ checks, summary: fixture.tracker.summary() })}.`);
        }
      };
      const newPostsFor = (fixtureTracker, operationId, afterCount) => postsFor(fixtureTracker, operationId).slice(afterCount);
      const stickyReason = (entry) => String(entry?.terminalReason || "").includes("sticky request-ownership violation");
      // A real browser POST claiming a queued identity, optionally as an invocation's transport attempt.
      const sendClaimingPost = async (fixture, { operationId, operation, sessionId, browserTimerIdentity, body, transportAttempt = 0 }) => {
        const before = postsFor(fixture.tracker, operationId).length;
        await fixture.tracker.setPageOperation(fixture.page, {
          fixture: "sqag212",
          operation,
          operationId,
          expectedSessionId: sessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        const response = await fixture.page.evaluate(async ({ queuedHeader, identity, attemptHeader, attempt, payload }) => {
          const headers = { "content-type": "application/json", [queuedHeader]: identity };
          if (attempt) headers[attemptHeader] = String(attempt);
          if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
          const reply = await fetch("/api/quote-sessions", { method: "POST", headers, body: payload });
          const text = await reply.text();
          return { status: reply.status, bodyBytes: text.length };
        }, {
          queuedHeader: quoteSessionQueuedSaveHeader,
          identity: browserTimerIdentity,
          attemptHeader: quoteSessionTransportAttemptHeader,
          attempt: transportAttempt,
          payload: body,
        });
        await waitForCondition(() => newPostsFor(fixture.tracker, operationId, before).length > 0, 10000, `${operation} claiming POST was not observed.`);
        const record = newPostsFor(fixture.tracker, operationId, before)[0];
        await withTimeout(record.terminalPromise, 30000, `${operation} claiming POST did not complete.`);
        return { response, record };
      };
      // A real queued save whose first POST meets a stale cached session token, so the real server
      // answers 403 before persistence and the application refreshes the session and retries.
      const runStaleTokenSave = async (fixture, sessionId, operationId, operation, { mutateRetryPayload = false } = {}) => {
        await prepareSession(fixture, sessionId, operationId, operation);
        if (mutateRetryPayload) {
          await fixture.page.evaluate(() => {
            const inner = window.fetchPostJsonResponse;
            let quoteSessionCalls = 0;
            const adversarial = function (url, payload, ...rest) {
              let target = "";
              try { target = new URL(String(url), window.location.href).pathname; } catch { target = ""; }
              if (target === "/api/quote-sessions" && payload && typeof payload === "object") {
                quoteSessionCalls += 1;
                if (quoteSessionCalls === 2 && payload.draft_state && typeof payload.draft_state === "object") {
                  // Adversarial control: the same invocation's retry transport carries a changed payload.
                  payload.draft_state = {
                    ...payload.draft_state,
                    outputSortMode: payload.draft_state.outputSortMode === "pricing_reference" ? "name" : "pricing_reference",
                  };
                  window.fetchPostJsonResponse = inner;
                }
              }
              return inner.call(this, url, payload, ...rest);
            };
            Object.defineProperty(adversarial, "__sqagSmokeCaptured", { value: true });
            window.fetchPostJsonResponse = adversarial;
          });
        }
        await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 400 }));
        const captured = await fixture.tracker.capturePendingQueuedSave(fixture.page);
        if (!captured || captured.entry.producerKind !== "native-timer") {
          throw new Error(`${operation} did not register its real native timer producer.`);
        }
        await fixture.page.evaluate(() => { state.csrfToken = `sqag212-r1b-stale-${Date.now()}`; });
        await withTimeout(captured.entry.terminalPromise, 30000, `${operation} did not settle.`);
        return { captured, entry: captured.entry, attempts: postsFor(fixture.tracker, operationId) };
      };

      const runR1BSecondPostNegative = async () => {
        const name = "r1b-second-post-pending-correlation";
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        let handlingHold = null;
        try {
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          await prepareSession(fixture, sessionId, operationA, `${name}-a`);
          handlingHold = fixture.tracker.holdTimerEventHandling(fixture.page, "save-start");
          await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 400 }));
          const captured = await fixture.tracker.capturePendingQueuedSave(fixture.page);
          if (!captured || captured.entry.producerKind !== "native-timer") throw new Error(`${name} did not register its real native timer producer.`);
          const entry = captured.entry;
          const queueSnapshot = structuredClone(captured.snapshot);
          // Legitimate state evolution Q -> E after queueing and before the real native timer fires.
          await fixture.page.evaluate(() => { state.outputSortMode = "pricing_reference"; });
          await withTimeout(handlingHold.observed, 10000, `${name} native timer save-start was not delivered.`);
          const owner = await waitForTrackedPost(fixture.tracker, operationA);
          await withTimeout(owner.terminalPromise, 20000, `${name} owner POST did not complete.`);
          const beforeDuplicate = {
            nativeTimerFired: entry.dispatchedBy === "fire",
            ownerCarriesEvolvedState: !draftStatesEqual(queueSnapshot.draftState, owner.payloadDraftState),
            invocationCorrelationPending: !entry.saveInvocationSnapshot
              && owner.correlationState === "awaiting-save-invocation-snapshot"
              && entry.correlationPendingRecord === owner,
            ownerProvisionallyOwned: entry.ownership.ownerRecord === owner && entry.ownership.state === "provisional-owned",
          };
          const stalePayload = JSON.parse(owner.postData || "{}");
          stalePayload.draft_state = queueSnapshot.draftState;
          const operationDuplicate = `sqag212/${name}-duplicate/${Date.now()}`;
          const { response, record: duplicate } = await sendClaimingPost(fixture, {
            operationId: operationDuplicate,
            operation: `${name}-duplicate`,
            sessionId,
            browserTimerIdentity: captured.snapshot.browserTimerIdentity,
            body: JSON.stringify(stalePayload),
          });
          const duplicateMatchesQueueTimeState = duplicate.expectedSessionId === queueSnapshot.sessionId
            && duplicate.expectedQuoteGenerated === queueSnapshot.options?.quoteGenerated
            && draftStatesEqual(duplicate.payloadDraftState, queueSnapshot.draftState)
            && (duplicate.payloadDraftFiles === null || draftFilesEqual(duplicate.payloadDraftFiles, queueSnapshot.draftFiles));
          const whilePending = {
            duplicateWasRealBrowserPost: response.status === 200 && duplicate.httpStatus === 200,
            noTerminalSuccess: entry.status === "pending",
            originalRemainsOwned: entry.ownership.ownerRecord === owner
              && entry.ownership.currentAttempt === owner
              && entry.persistenceRecord === owner,
            producerNotOverwritten: entry.producer !== duplicate.terminalPromise,
            duplicateRejected: duplicate.correlationState === "rejected-duplicate-claim",
            noQueueTimeFallback: duplicateMatchesQueueTimeState && duplicate.claim?.comparedSnapshot === "none",
            duplicateMintedNoProof: duplicate.ownProof?.state === "ineligible" && !duplicate.readback,
            stickyDuplicateRetained: entry.ownership.sticky.some((violation) => violation.operationId === operationDuplicate),
          };
          handlingHold.release();
          await withTimeout(entry.terminalPromise, 20000, `${name} queued save did not settle after save-start.`);
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const checks = {
            ...beforeDuplicate,
            ...whilePending,
            lateSaveStartCorrelatedOriginalOnly: Boolean(entry.saveInvocationSnapshot)
              && owner.correlationState === "correlated-by-save-invocation-snapshot"
              && duplicate.correlationState === "rejected-duplicate-claim",
            aNotSuccessful: entry.status === "failed" && stickyReason(entry),
            stickyStillRetained: entry.ownership.sticky.some((violation) => violation.operationId === operationDuplicate),
            drainFailed: drained === false,
          };
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks, timerIdentity: entry.snapshot.timerIdentity };
        } finally {
          handlingHold?.release();
          await fixture.context.close();
        }
      };

      const runR1BConcurrentDuplicate = async () => {
        const name = "r1b-concurrent-same-payload-duplicate";
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        let releaseOwner = null;
        let ownerHeldResolve;
        const ownerHeld = new Promise((resolve) => { ownerHeldResolve = resolve; });
        let holdArmed = true;
        try {
          await fixture.page.route("**/api/quote-sessions", async (route) => {
            if (route.request().method() === "POST" && holdArmed) {
              holdArmed = false;
              ownerHeldResolve();
              await new Promise((resolve) => { releaseOwner = resolve; });
            }
            await route.fallback();
          });
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          await prepareSession(fixture, sessionId, operationA, `${name}-a`);
          await fixture.page.evaluate(() => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 300 }));
          const captured = await fixture.tracker.capturePendingQueuedSave(fixture.page);
          if (!captured) throw new Error(`${name} did not capture its real queued save.`);
          const entry = captured.entry;
          await withTimeout(ownerHeld, 15000, `${name} owner POST was not held in flight.`);
          const owner = await waitForTrackedPost(fixture.tracker, operationA);
          // Same invocation id, same identity, byte-identical payload, while the owner is in flight.
          const { response, record: duplicate } = await sendClaimingPost(fixture, {
            operationId: operationA,
            operation: `${name}-duplicate`,
            sessionId,
            browserTimerIdentity: captured.snapshot.browserTimerIdentity,
            body: owner.postData,
          });
          const whileInFlight = {
            duplicateReachedServer: response.status === 200,
            duplicateOverlappedOwnerInFlight: duplicate.requestSeq > owner.requestSeq && !owner.responseSeq,
            samePayloadBytes: duplicate.postData === owner.postData,
            sameInvocationClaimed: duplicate.operationId === owner.operationId && duplicate.transportAttempt === 1,
            duplicateTransportRejected: duplicate.transportVerdict?.accepted === false,
            duplicateRejected: duplicate.correlationState === "rejected-duplicate-claim",
            duplicateMintedNoProof: duplicate.ownProof?.state === "ineligible",
            originalRemainsOwned: entry.ownership.ownerRecord === owner
              && entry.ownership.currentAttempt === owner
              && entry.persistenceRecord === owner,
            noTerminalSuccess: entry.status === "pending",
            stickyDuplicateRetained: entry.ownership.sticky.some((violation) => violation.transportId === duplicate.transportId),
          };
          releaseOwner?.();
          await withTimeout(entry.terminalPromise, 20000, `${name} queued save did not settle.`);
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const checks = {
            ...whileInFlight,
            aNotSuccessful: entry.status === "failed" && stickyReason(entry),
            drainFailed: drained === false,
          };
          requireChecks(name, checks, fixture);
          await fixture.page.unroute("**/api/quote-sessions");
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks };
        } finally {
          releaseOwner?.();
          await fixture.page.unroute("**/api/quote-sessions").catch(() => {});
          await fixture.context.close();
        }
      };

      const runR1BRealForbiddenRetry = async () => {
        const name = "r1b-real-403-refresh-same-payload-retry";
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        try {
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          const { entry, attempts } = await runStaleTokenSave(fixture, sessionId, operationA, `${name}-a`);
          const [first, retry] = attempts;
          const drained = await fixture.tracker.drain(15000, fixture.page);
          const checks = {
            drained,
            noIssues: fixture.tracker.issues.length === 0,
            succeeded: entry.status === "succeeded",
            nativeTimerFired: entry.dispatchedBy === "fire",
            twoTransportAttemptsOneInvocation: attempts.length === 2 && Boolean(retry)
              && first.invocation === retry.invocation && first.invocation.attempts.length === 2,
            attemptOrdinals: first?.transportAttempt === 1 && retry?.transportAttempt === 2,
            firstRealServer403BeforePersistence: first?.httpStatus === 403 && first.bodyStatus === "blocked",
            firstProvidesNoPersistenceProof: first?.ownProof?.state === "ineligible" && !first.readback
              && first.transportOutcome === "superseded-by-validated-retry",
            sameSession: first?.expectedSessionId === sessionId && retry?.expectedSessionId === sessionId,
            samePayload: Boolean(first?.postData) && first.postData === retry?.postData,
            sameQueuedIdentity: first?.queuedSaveIdentity === entry.snapshot.timerIdentity && retry?.queuedSaveIdentity === entry.snapshot.timerIdentity,
            realSessionRefreshBetweenAttempts: retry?.retryEvidence?.checks?.realSessionRefreshBetweenAttempts === true,
            retryUsedRefreshedSession: retry?.retryEvidence?.body?.passed === true,
            singleValidatedRetry: retry?.transportVerdict?.accepted === true && retry.transportVerdict.role === "retry",
            noOverlappingAcceptedRequest: first?.responseSeq > 0 && first.responseSeq < retry?.requestSeq,
            oneInvocationOwnsIdentity: entry.ownership.ownerRecord === first
              && entry.ownership.currentAttempt === retry
              && entry.ownership.claims.length === 2
              && entry.ownership.sticky.length === 0,
            onlyFinalAttemptProvedPersistence: retry?.ownProof?.state === "accepted" && retry.ownProof.consequential === true
              && retry.readbackVerdict === "exact"
              && entry.evidence.persistenceTransportId === retry.transportId,
          };
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks, retryEvidence: { structural: retry.retryEvidence.checks, refresh: retry.retryEvidence.body?.checks } };
        } finally {
          await fixture.context.close();
        }
      };

      const runR1BWrongPayloadRetry = async () => {
        const name = "r1b-wrong-payload-retry";
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        try {
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          const { entry, attempts } = await runStaleTokenSave(fixture, sessionId, operationA, `${name}-a`, { mutateRetryPayload: true });
          const [first, retry] = attempts;
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const checks = {
            firstRealServer403: first?.httpStatus === 403 && first.bodyStatus === "blocked",
            retryClaimedSameInvocation: retry?.operationId === first?.operationId && retry?.transportAttempt === 2,
            retryChangedPayload: Boolean(retry) && retry.postData !== first.postData,
            retryReachedServer: retry?.httpStatus === 200,
            retryTransportRejected: retry?.transportVerdict?.accepted === false
              && String(retry.transportVerdict.reason || "").includes("samePayload"),
            retryClaimRejected: retry?.correlationState === "rejected-invalid-retry",
            retryMintedNoProof: retry?.ownProof?.state === "ineligible" && !retry.readback,
            firstNotTreatedAsRetried: first?.transportOutcome !== "superseded-by-validated-retry",
            ownerAttemptRetained: entry.ownership.currentAttempt === first,
            aFailed: entry.status === "failed" && stickyReason(entry),
            drainFailed: drained === false,
          };
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks };
        } finally {
          await fixture.context.close();
        }
      };

      const runR1BWrongGeneration = async () => {
        const name = "r1b-wrong-generation";
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        let readbackHold = null;
        try {
          const operationP = `sqag212/${name}-older-generation/${Date.now()}`;
          const capturedP = await startQueuedFlush(fixture, sessionId, operationP, `${name}-older-generation`);
          await withTimeout(capturedP.entry.terminalPromise, 15000, `${name} older generation did not settle.`);
          const recordP = postsFor(fixture.tracker, operationP)[0];
          if (capturedP.entry.status !== "succeeded" || !recordP) throw new Error(`${name} older generation did not persist.`);
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          readbackHold = fixture.tracker.holdDurableReadback(operationA);
          const olderA = await runNativeTimerSave(fixture, sessionId, operationA, `${name}-a`, { awaitTerminal: false });
          const entryA = olderA.entry;
          const recordA = olderA.record;
          await withTimeout(readbackHold.reached, 15000, `${name} A readback did not reach its hold.`);
          const aProvedItselfFirst = recordA.ownProof?.state === "accepted";
          // Older invocation P, presented as a retry, carrying P's generation, claiming A's identity.
          const { response, record: wrong } = await sendClaimingPost(fixture, {
            operationId: operationP,
            operation: `${name}-claim`,
            sessionId,
            browserTimerIdentity: entryA.snapshot.browserTimerIdentity,
            body: recordP.postData,
            transportAttempt: 2,
          });
          readbackHold.release();
          await withTimeout(entryA.terminalPromise, 20000, `${name} A did not settle.`);
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const rejection = recordA.readbackSupersessionRejection || {};
          const checks = {
            wrongGenerationWasRealBrowserPost: response.status === 200,
            wrongGenerationCarriedOlderGeneration: draftStatesEqual(wrong.payloadDraftState, recordP.payloadDraftState)
              && !draftStatesEqual(wrong.payloadDraftState, recordA.payloadDraftState),
            wrongGenerationRejected: wrong.correlationState === "rejected-wrong-generation",
            wrongGenerationMintedNoProof: wrong.ownProof?.state === "ineligible",
            aProvedItselfFirst,
            aReadbackObservedWrongGeneration: draftStatesEqual(recordA.readback?.draftState, recordP.payloadDraftState),
            // The claim made A sticky, so no successor may explain A's readback at all; the claim is
            // also independently ineligible (rejected, no proof) if it were ever weighed.
            wrongGenerationNotSuccessorEvidence: recordA.readbackVerdict === "failed" && !recordA.readbackSuccessor
              && String(rejection.reason || "").includes("sticky request-ownership violation")
              && (rejection.rejected || []).every((item) => item.operationId !== operationP || item.checks?.successorIndependentlyProven === false),
            olderGenerationOutcomeUnchanged: capturedP.entry.status === "succeeded",
            aFailed: entryA.status === "failed" && stickyReason(entryA),
            drainFailed: drained === false,
          };
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks };
        } finally {
          readbackHold?.release();
          await fixture.context.close();
        }
      };

      const runR1BTerminalReplay = async () => {
        const name = "r1b-terminal-replay";
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        try {
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          const capturedA = await startQueuedFlush(fixture, sessionId, operationA, `${name}-a`);
          await withTimeout(capturedA.entry.terminalPromise, 15000, `${name} A did not settle.`);
          const recordA = postsFor(fixture.tracker, operationA)[0];
          const drainedBeforeReplay = await fixture.tracker.drain(15000, fixture.page);
          const statusBeforeReplay = capturedA.entry.status;
          if (!drainedBeforeReplay || statusBeforeReplay !== "succeeded" || !recordA) {
            throw new Error(`${name} A did not complete before replay: ${JSON.stringify(fixture.tracker.summary())}.`);
          }
          const replay = await sendClaimingPost(fixture, {
            operationId: operationA,
            operation: `${name}-exact-replay`,
            sessionId,
            browserTimerIdentity: capturedA.snapshot.browserTimerIdentity,
            body: recordA.postData,
          });
          const extraRetry = await sendClaimingPost(fixture, {
            operationId: operationA,
            operation: `${name}-extra-retry`,
            sessionId,
            browserTimerIdentity: capturedA.snapshot.browserTimerIdentity,
            body: recordA.postData,
            transportAttempt: 3,
          });
          const drainedAfterReplay = await fixture.tracker.drain(3000, fixture.page);
          const checks = {
            completedBeforeReplay: drainedBeforeReplay && statusBeforeReplay === "succeeded",
            replaysWereRealBrowserPosts: replay.response.status === 200 && extraRetry.response.status === 200,
            exactReplayRejected: replay.record.correlationState === "rejected-terminal-replay" && replay.record.postData === recordA.postData,
            extraRetryRejected: extraRetry.record.correlationState === "rejected-terminal-replay"
              && extraRetry.record.transportVerdict?.accepted === false,
            identityHistoryRetained: fixture.tracker.queuedSaveStatus(capturedA.snapshot.timerIdentity) === capturedA.entry
              && capturedA.entry.ownership.claims.length === 3,
            replaysMintedNoProof: replay.record.ownProof?.state === "ineligible" && extraRetry.record.ownProof?.state === "ineligible",
            replayViolationsSticky: capturedA.entry.ownership.sticky.length === 2,
            terminalOutcomeNotRewritten: capturedA.entry.status === "succeeded",
            drainFailedAfterReplay: drainedAfterReplay === false,
          };
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks };
        } finally {
          await fixture.context.close();
        }
      };

      const r1bSecondPost = await runR1BSecondPostNegative();
      const r1bConcurrentDuplicate = await runR1BConcurrentDuplicate();
      const r1bRealForbiddenRetry = await runR1BRealForbiddenRetry();
      const r1bWrongPayload = await runR1BWrongPayloadRetry();
      const r1bWrongGeneration = await runR1BWrongGeneration();
      const r1bTerminalReplay = await runR1BTerminalReplay();
      const singlePostOwnershipCase = (...cases) => ({
        result: cases.every((item) => item?.result === "PASS"
          && item.checks?.exclusiveRequestOwnership === true
          && item.checks?.independentOwnProof === true) ? "PASS" : "FAIL",
        cases: cases.map((item) => ({ result: item?.result, order: item?.order, evolvedState: item?.evolvedState, checks: item?.checks })),
      });
      const r1bPostBeforeSaveStart = singlePostOwnershipCase(r1PostBeforeEvolved, r1PostBeforeSame);
      const r1bSaveStartBeforePost = singlePostOwnershipCase(r1SaveStartBefore);
      const r1bCases = {
        R1B_NEGATIVE_SECOND_POST_STALE_QUEUE_STATE_CANNOT_BYPASS_PENDING_INVOCATION_CORRELATION: r1bSecondPost,
        R1B_POSITIVE_SINGLE_POST_POST_BEFORE_SAVE_START: r1bPostBeforeSaveStart,
        R1B_POSITIVE_SINGLE_POST_SAVE_START_BEFORE_POST: r1bSaveStartBeforePost,
        R1B_POSITIVE_REAL_403_REFRESH_SAME_PAYLOAD_RETRY: r1bRealForbiddenRetry,
        R1B_NEGATIVE_CONCURRENT_SAME_PAYLOAD_DUPLICATE: r1bConcurrentDuplicate,
        R1B_NEGATIVE_WRONG_PAYLOAD: r1bWrongPayload,
        R1B_NEGATIVE_WRONG_GENERATION: r1bWrongGeneration,
        R1B_NEGATIVE_TERMINAL_REPLAY: r1bTerminalReplay,
      };
      const r1bRateWindowSeparation = await separateQuoteSessionRateWindow("R1B");

      const runR2BUnprovenANegative = async (name, { baseline, storageLoss }) => {
        // baseline "diagnostic": the session exists durably but this tracker holds no prior commit
        // clock for it; "tracked": a tracked prior save gives A a known prior clock.
        const fixtureSessionId = boundedSessionId(name, "f");
        const sessionId = baseline === "diagnostic" ? boundedSessionId(name, "s") : fixtureSessionId;
        const fixture = await openOwnershipFixture(`sqag212-${name}`, fixtureSessionId);
        let readbackHold = null;
        try {
          if (baseline === "diagnostic") {
            await fixture.tracker.setPageDiagnostic(fixture.page, { operation: `${name}-baseline`, operationId: `sqag212/${name}-baseline/${Date.now()}` }, sessionId);
            const saved = await fixture.page.evaluate(async (id) => {
              clearQuoteSessionDraftSaveTimer();
              state.quoteSessionId = id;
              state.quoteSessionDraftSaveStarted = true;
              saveSessionState();
              const session = await saveCurrentQuoteSession({
                sessionId: id,
                quoteGenerated: false,
                includeDraftState: true,
                includeDraftFiles: true,
                draftState: currentQuoteSessionDraftState(),
                draftFiles: sessionFileRecordsFromDraft(),
              });
              return String(session?.session_id || "");
            }, sessionId);
            if (saved !== sessionId) throw new Error(`${name} diagnostic baseline did not persist.`);
          } else {
            const baselineOperation = `sqag212/${name}-baseline/${Date.now()}`;
            const tracked = await startQueuedFlush(fixture, sessionId, baselineOperation, `${name}-baseline`);
            await withTimeout(tracked.entry.terminalPromise, 15000, `${name} baseline did not settle.`);
            if (tracked.entry.status !== "succeeded") throw new Error(`${name} tracked baseline did not persist.`);
          }
          let armed = true;
          await fixture.page.route("**/api/quote-sessions", async (route) => {
            if (route.request().method() !== "POST" || !armed) {
              await route.continue();
              return;
            }
            armed = false;
            if (storageLoss === "unpersisted") {
              await fulfilUnpersistedSave(route);
              return;
            }
            // Real server write whose durable draft state is not A's consequential state.
            const payload = JSON.parse(route.request().postData() || "{}");
            if (payload.draft_state && typeof payload.draft_state === "object") {
              payload.draft_state = {
                ...payload.draft_state,
                outputSortMode: payload.draft_state.outputSortMode === "pricing_reference" ? "name" : "pricing_reference",
              };
            }
            await route.continue({ postData: JSON.stringify(payload) });
          });
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          readbackHold = fixture.tracker.holdDurableReadback(operationA);
          const olderA = await runNativeTimerSave(fixture, sessionId, operationA, `${name}-a`, { awaitTerminal: false });
          const recordA = olderA.record;
          const entryA = olderA.entry;
          await withTimeout(readbackHold.reached, 15000, `${name} A readback did not reach its hold.`);
          const operationB = `sqag212/${name}-b/${Date.now()}`;
          const successorB = await runNativeTimerSave(fixture, sessionId, operationB, `${name}-b`);
          readbackHold.release();
          await withTimeout(entryA.terminalPromise, 15000, `${name} A did not settle.`);
          const recordB = successorB.record;
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const responseClock = quoteSessionCommitClock(recordA.responseUpdatedAt);
          const checks = {
            aResponseLookedSaved: recordA.httpStatus === 200 && recordA.bodyStatus === "saved"
              && recordA.responseSessionId === sessionId
              && recordA.responseQuoteGenerated === recordA.expectedQuoteGenerated
              && responseClock !== null,
            aOwnProofFailedOnState: recordA.ownProof?.state === "failed" && String(recordA.ownProof.reason).includes("draft state"),
            bIndependentlyProven: successorB.entry.status === "succeeded"
              && recordB.ownProof?.state === "accepted" && recordB.ownProof.consequential === true,
            aReadbackObservedB: draftStatesEqual(recordA.readback?.draftState, recordB.payloadDraftState),
            bCannotLaunderA: !recordA.readbackSuccessor
              && String(recordA.readbackSupersessionRejection?.reason || "").includes("own persistence proof was not accepted"),
            aFailed: entryA.status === "failed",
            drainFailed: drained === false,
          };
          if (baseline === "diagnostic") checks.aPriorCommitClockNull = recordA.priorCommitClock === null;
          else {
            checks.aPriorCommitClockKnown = recordA.priorCommitClock !== null;
            checks.aResponseTimestampAdvanced = responseClock !== null && responseClock > recordA.priorCommitClock;
          }
          requireChecks(name, checks, fixture);
          await fixture.page.unroute("**/api/quote-sessions");
          await cleanupSession(fixture.page, sessionId);
          if (sessionId !== fixtureSessionId) await cleanupSession(fixture.page, fixtureSessionId);
          return { result: "PASS", checks, rejection: recordA.readbackSupersessionRejection?.reason || "" };
        } finally {
          readbackHold?.release();
          await fixture.page.unroute("**/api/quote-sessions").catch(() => {});
          await fixture.context.close();
        }
      };

      const corruptDataUrl = (dataUrl) => {
        const separator = String(dataUrl || "").indexOf(",");
        const header = separator >= 0 ? dataUrl.slice(0, separator) : "";
        if (!/;base64$/i.test(header)) return "";
        const bytes = Buffer.concat([Buffer.from(dataUrl.slice(separator + 1), "base64"), Buffer.from("\n%sqag212-r2b-corrupted-bytes\n")]);
        return `${header},${bytes.toString("base64")}`;
      };
      const runR2BFileNegative = async (name, { storageLoss, successorFiles }) => {
        const fixtureSessionId = boundedSessionId(name, "f");
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, fixtureSessionId);
        let readbackHold = null;
        try {
          let armed = true;
          await fixture.page.route("**/api/quote-sessions", async (route) => {
            if (route.request().method() !== "POST" || !armed) {
              await route.continue();
              return;
            }
            armed = false;
            // Adversarial storage loss: the real server receives A's state with missing or corrupt files.
            const payload = JSON.parse(route.request().postData() || "{}");
            const files = Array.isArray(payload.draft_files) ? payload.draft_files : [];
            payload.draft_files = storageLoss === "missing"
              ? []
              : files.map((file, index) => (index === 0 ? { ...file, data_url: corruptDataUrl(file.data_url) || file.data_url } : file));
            await route.continue({ postData: JSON.stringify(payload) });
          });
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          readbackHold = fixture.tracker.holdDurableReadback(operationA);
          // A is the first save of a fresh session, so it must carry and persist its own files.
          const olderA = await runNativeTimerSave(fixture, sessionId, operationA, `${name}-a`, { awaitTerminal: false });
          const recordA = olderA.record;
          const entryA = olderA.entry;
          await withTimeout(readbackHold.reached, 15000, `${name} A readback did not reach its hold.`);
          const aFiles = Array.isArray(recordA.payloadDraftFiles) ? recordA.payloadDraftFiles : [];
          const successorPayloadFiles = successorFiles === "empty"
            ? []
            : aFiles.map((file) => ({ ...file, name: `r2b-successor-${file.name || "reference"}` }));
          const operationB = `sqag212/${name}-b/${Date.now()}`;
          await prepareSession(fixture, sessionId, operationB, `${name}-b`);
          await fixture.page.evaluate((draftFiles) => queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 300, draftFiles }), successorPayloadFiles);
          const recordB = await waitForTrackedPost(fixture.tracker, operationB, 15000);
          const entryB = fixture.tracker.queuedSaveStatus(recordB.queuedSaveIdentity);
          if (!entryB) throw new Error(`${name} B POST was not correlated to a real queued save.`);
          await withTimeout(entryB.terminalPromise, 20000, `${name} B did not settle.`);
          readbackHold.release();
          await withTimeout(entryA.terminalPromise, 15000, `${name} A did not settle.`);
          const drained = await fixture.tracker.drain(3000, fixture.page);
          const bProvenFiles = recordB.ownProof?.readback?.draftFiles;
          const checks = {
            aRequiredNonemptyIdentifiableFiles: aFiles.length > 0
              && queuedDraftFilesComparable(aFiles).every((file) => file.session_file_key && file.content_fingerprint),
            aStatePersisted: !String(recordA.ownProof?.reason || "").includes("draft state"),
            aFileDurabilityFailed: recordA.ownProof?.state === "failed" && String(recordA.ownProof.reason).includes("files"),
            bSucceededIndependently: entryB.status === "succeeded"
              && recordB.ownProof?.state === "accepted" && recordB.ownProof.consequential === true,
            bFilesDifferFromA: Array.isArray(bProvenFiles) && !draftFilesEqual(bProvenFiles, aFiles)
              && (successorFiles === "empty"
                ? bProvenFiles.length === 0 && Array.isArray(recordB.payloadDraftFiles) && recordB.payloadDraftFiles.length === 0
                : bProvenFiles.length === aFiles.length && bProvenFiles.length > 0),
            aReadbackObservedBFiles: draftFilesEqual(recordA.readback?.draftFiles, bProvenFiles),
            bCannotSubstituteForA: !recordA.readbackSuccessor
              && String(recordA.readbackSupersessionRejection?.reason || "").includes("own persistence proof was not accepted"),
            aFailed: entryA.status === "failed",
            drainFailed: drained === false,
          };
          requireChecks(name, checks, fixture);
          await fixture.page.unroute("**/api/quote-sessions");
          await cleanupSession(fixture.page, sessionId);
          await cleanupSession(fixture.page, fixtureSessionId);
          return { result: "PASS", storageLoss, successorFiles, checks };
        } finally {
          readbackHold?.release();
          await fixture.page.unroute("**/api/quote-sessions").catch(() => {});
          await fixture.context.close();
        }
      };

      const runR2BNullClockPositive = async () => {
        const name = "r2b-null-prior-clock-proven-a";
        const fixtureSessionId = boundedSessionId(name, "f");
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, fixtureSessionId);
        try {
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          const saved = await runNativeTimerSave(fixture, sessionId, operationA, `${name}-a`);
          const recordA = saved.record;
          const drained = await fixture.tracker.drain(15000, fixture.page);
          const proof = recordA.ownProof?.readback;
          const checks = {
            drained,
            noIssues: fixture.tracker.issues.length === 0,
            priorCommitClockNull: recordA.priorCommitClock === null,
            aCarriedNonemptyFiles: Array.isArray(recordA.payloadDraftFiles) && recordA.payloadDraftFiles.length > 0,
            ownStateProof: recordA.ownProof?.state === "accepted" && recordA.ownProof.consequential === true
              && draftStatesEqual(proof?.draftState, recordA.payloadDraftState),
            ownFileProof: draftFilesEqual(proof?.draftFiles, recordA.payloadDraftFiles),
            succeeded: saved.entry.status === "succeeded" && saved.entry.evidence.persistenceTransportId === recordA.transportId,
            exactLaterReadback: recordA.readbackVerdict === "exact",
          };
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          await cleanupSession(fixture.page, fixtureSessionId);
          return { result: "PASS", checks };
        } finally {
          await fixture.context.close();
        }
      };

      const runR2BProofWaitLifecycle = async (lifecycle) => {
        const name = `r2b-proof-wait-${lifecycle}`;
        const sessionId = boundedSessionId(name, "s");
        const fixture = await openOwnershipFixture(`sqag212-${name}`, sessionId);
        let hold = null;
        try {
          const operationA = `sqag212/${name}-a/${Date.now()}`;
          hold = fixture.tracker.holdOwnProof(operationA);
          const olderA = await runNativeTimerSave(fixture, sessionId, operationA, `${name}-a`, { awaitTerminal: false });
          const recordA = olderA.record;
          const entryA = olderA.entry;
          await withTimeout(hold.reached, 15000, `${name} own-proof wait was not reached.`);
          const proofWaitOutstanding = fixture.tracker.summary().outstanding.requiredOwnProofs === 1;
          let checks;
          if (lifecycle === "deadline") {
            const drained = await fixture.tracker.drain(1500, fixture.page);
            const report = [...fixture.tracker.drainReports].reverse().find((item) => item.outcome === "deadline-failure");
            const atDeadline = {
              drainFailedAtAbsoluteDeadline: drained === false && Boolean(report),
              deadlineBudgetUnchanged: report?.budgetMs === 1500 && report.deadlineUnchanged === true,
              proofWaitLifecycleAccounted: (report?.outstanding || []).some((item) => (
                item.identity === `${operationA}/own-proof` && item.state === "own-proof-pending" && item.producerState !== "none"
              )),
              aNotCompletedByDeadline: entryA.status === "pending",
            };
            hold.release();
            await withTimeout(entryA.terminalPromise, 20000, `${name} A did not settle after the proof wait was released.`);
            const drainedAfter = await fixture.tracker.drain(3000, fixture.page);
            checks = {
              ...atDeadline,
              lateProofNeverExtendedDeadline: recordA.ownProof?.state === "accepted",
              deadlineFailureRetained: fixture.tracker.issues.some((issue) => issue.operationId === report?.identity),
              laterDrainStillFailed: drainedAfter === false,
            };
          } else {
            if (lifecycle === "close") await fixture.page.close();
            else await fixture.page.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
            await withTimeout(recordA.ownProofSettled, 10000, `${name} own-proof wait survived the ${lifecycle}.`);
            await withTimeout(entryA.terminalPromise, 10000, `${name} A did not settle after the ${lifecycle}.`);
            // The navigated-away save's client-result wait may run to its own 10-second bound.
            const drained = await fixture.tracker.drain(30000, lifecycle === "close" ? null : fixture.page);
            const report = fixture.tracker.drainReports.at(-1);
            checks = {
              proofWaitAborted: recordA.ownProof?.state === "aborted",
              aFailed: entryA.status === "failed",
              noOrphanedProofGate: fixture.tracker.summary().outstanding.requiredOwnProofs === 0,
              drainTerminatedWithoutDeadline: drained === false && report?.outcome === "failure",
            };
          }
          checks.proofWaitWasOutstanding = proofWaitOutstanding;
          requireChecks(name, checks, fixture);
          await cleanupSession(fixture.page, sessionId);
          return { result: "PASS", checks };
        } finally {
          hold?.release();
          await fixture.context.close();
        }
      };

      const r2bNullClockNegative = await runR2BUnprovenANegative("r2b-null-prior-clock-unpersisted-a", { baseline: "diagnostic", storageLoss: "unpersisted" });
      const r2bAdvancedClockNegative = await runR2BUnprovenANegative("r2b-advanced-timestamp-unpersisted-state", { baseline: "tracked", storageLoss: "state" });
      const r2bMissingFilesNegative = await runR2BFileNegative("r2b-missing-a-files-different-b-files", { storageLoss: "missing", successorFiles: "different" });
      const r2bCorruptFilesNegative = await runR2BFileNegative("r2b-corrupt-a-files-empty-b-files", { storageLoss: "corrupt", successorFiles: "empty" });
      const r2bNullClockPositive = await runR2BNullClockPositive();
      const r2bProofWaitClose = await runR2BProofWaitLifecycle("close");
      const r2bProofWaitNavigation = await runR2BProofWaitLifecycle("navigation");
      const r2bProofWaitDeadline = await runR2BProofWaitLifecycle("deadline");
      const r2bFileNegative = {
        result: [r2bMissingFilesNegative, r2bCorruptFilesNegative].every((item) => item?.result === "PASS") ? "PASS" : "FAIL",
        missingAFilesThenDifferentBFiles: r2bMissingFilesNegative,
        corruptAFilesThenEmptyBFiles: r2bCorruptFilesNegative,
      };
      const r2bNoLaundering = {
        result: [r2NoLaundering, r2bNullClockNegative, r2bAdvancedClockNegative, r2bMissingFilesNegative, r2bCorruptFilesNegative]
          .every((item) => item?.result === "PASS") ? "PASS" : "FAIL",
        unpersistedStateWithKnownClock: r2NoLaundering,
        unpersistedStateWithNullClock: r2bNullClockNegative?.result,
        advancedTimestampWithoutState: r2bAdvancedClockNegative?.result,
        missingFiles: r2bMissingFilesNegative?.result,
        corruptFiles: r2bCorruptFilesNegative?.result,
      };
      const r2bSupersedes = {
        result: r2Supersedes?.result === "PASS"
          && r2Supersedes.checks?.aIndependentlyProvenFirst === true
          && r2Supersedes.checks?.bIndependentlyProven === true
          && r2Supersedes.checks?.aReadbackIsBProvenRepresentation === true ? "PASS" : "FAIL",
        checks: r2Supersedes?.checks,
        supersession: r2Supersedes?.supersession,
      };
      const r2bReadbackBeforeB = {
        result: r2ReadbackBeforeB?.result === "PASS"
          && r2ReadbackBeforeB.checks?.aIndependentOwnProof === true
          && r2ReadbackBeforeB.checks?.bIndependentOwnProof === true ? "PASS" : "FAIL",
        checks: r2ReadbackBeforeB?.checks,
      };
      const r2bCases = {
        R2B_NEGATIVE_NULL_PRIOR_CLOCK_UNPERSISTED_A_THEN_VALID_B: r2bNullClockNegative,
        R2B_NEGATIVE_ADVANCED_TIMESTAMP_WITHOUT_A_STATE_PERSISTENCE: r2bAdvancedClockNegative,
        R2B_NEGATIVE_B_FILES_CANNOT_SUBSTITUTE_FOR_A_FILE_DURABILITY: r2bFileNegative,
        R2B_NEGATIVE_B_SUCCESS_CANNOT_SUPPLY_A_MISSING_STATE_OR_FILE_PROOF: r2bNoLaundering,
        R2B_POSITIVE_A_INDEPENDENTLY_PROVEN_THEN_B_SUPERSEDES_BEFORE_A_READBACK: r2bSupersedes,
        R2B_POSITIVE_A_READBACK_BEFORE_B: r2bReadbackBeforeB,
        R2B_POSITIVE_A_PROVEN_WITH_NULL_PRIOR_CLOCK_USING_CONSEQUENTIAL_STATE_FILE_EVIDENCE: r2bNullClockPositive,
        R2B_ADVERSARIAL_PROOF_WAIT_PAGE_CLOSE: r2bProofWaitClose,
        R2B_ADVERSARIAL_PROOF_WAIT_NAVIGATION: r2bProofWaitNavigation,
        R2B_ADVERSARIAL_PROOF_WAIT_ABSOLUTE_DEADLINE: r2bProofWaitDeadline,
      };
      const r2bRateWindowSeparation = await separateQuoteSessionRateWindow("R2B");
      const ownershipStatuses = ownershipTrackers.flatMap((fixtureTracker) => fixtureTracker.records
        .filter((record) => record.method === "POST").map((record) => record.httpStatus));
      const r1bR2bLimiterEvidence = {
        result: ownershipStatuses.includes(429) ? "FAIL" : "PASS",
        quoteSessionPostCount: ownershipStatuses.length,
        rateLimitedCount: ownershipStatuses.filter((status) => status === 429).length,
      };
      const r1bPassed = Object.values(r1bCases).every((item) => item?.result === "PASS");
      const r2bPassed = Object.values(r2bCases).every((item) => item?.result === "PASS");

      return {
        R1B_RESULT: r1bPassed ? "PASS" : "FAIL",
        ...r1bCases,
        R1B_RATE_WINDOW_SEPARATION: r1bRateWindowSeparation,
        R2B_RESULT: r2bPassed ? "PASS" : "FAIL",
        ...r2bCases,
        R2B_RATE_WINDOW_SEPARATION: r2bRateWindowSeparation,
        R1B_R2B_NO_LIMITER_SELF_INTERFERENCE: r1bR2bLimiterEvidence,
        ...(t3PositiveEvidence || {}),
        R1_RESULT: r1Passed ? "PASS" : "FAIL",
        R1_NEGATIVE_POST_BEFORE_SAVE_START_WITH_QUEUE_STATE_EVOLUTION: r1PostBeforeEvolved,
        R1_NEGATIVE_WRONG_TIMER_POST_STILL_REJECTED: r1WrongTimer,
        R1_POSITIVE_SAVE_START_BEFORE_POST: r1SaveStartBefore,
        R1_POSITIVE_POST_BEFORE_SAVE_START_SAME_SAVE_STATE: r1PostBeforeSame,
        R2_RESULT: r2Passed ? "PASS" : "FAIL",
        R2_NEGATIVE_OLDER_SAVE_ACTUALLY_NOT_PERSISTED: r2NotPersisted,
        R2_POSITIVE_A_PERSISTS_THEN_B_VALIDLY_SUPERSEDES_BEFORE_A_READBACK: r2Supersedes,
        R2_POSITIVE_A_READBACK_BEFORE_B: r2ReadbackBeforeB,
        R2_NEGATIVE_B_SUCCESS_CANNOT_LAUNDER_A_FAILURE: r2NoLaundering,
        R1_R2_RATE_WINDOW_SEPARATION: r1r2RateWindowSeparation,
        L1_RESULT: "PASS",
        L1_NEGATIVE_DELIVERY_FAILURE_THEN_PAGE_CLOSE: l1PageClose,
        L1_NEGATIVE_DELIVERY_FAILURE_THEN_DOCUMENT_NAVIGATION: l1Navigation,
        L1_POSITIVE_DELIVERY_FAILURE_PAGE_REMAINS_OPEN: l1OpenPage,
        L1_POSITIVE_CLEAN_PAGE_CLOSE_WITH_PROVEN_ZERO_WORK: l1CleanClosePassed ? "PASS" : "FAIL",
        L1_POSITIVE_NAVIGATION_AFTER_REQUIRED_WORK_COMPLETES: l1CompletedNavigationPassed && l1CompletedPersistence ? "PASS" : "FAIL",
        L2_RESULT: l2NegativeResult && transferredProducer && nativeTimerRemainedReal && persisted ? "PASS" : "FAIL",
        L3_RESULT: l3HeldAckFailure && l3NormalAckPassed && l3NativePassed && l3FinalInsideResult && l3FinalLateFailure ? "PASS" : "FAIL",
        L3_NEGATIVE_HELD_EVENT_ACK_BOUNDED_BY_ABSOLUTE_DEADLINE: l3HeldAckFailure ? "PASS" : "FAIL",
        L3_POSITIVE_NORMAL_EVENT_ACK_WITHIN_BUDGET: l3NormalAckPassed && l3NormalAckBounded && l3NormalAckPersistence ? "PASS" : "FAIL",
        L3_POSITIVE_REQUIRED_TIMER_NATIVE_COMPLETION_WITHIN_BUDGET: l3NativePassed && l3NativeBounded && l3NativePersistence ? "PASS" : "FAIL",
        L3_POSITIVE_FINAL_ZERO_ARRIVAL_STILL_BOUNDED: l3FinalInsideResult && l3FinalLateFailure && l3FinalLatePersistence ? "PASS" : "FAIL",
        L1: {
          negativePageClose: l1PageClose,
          negativeDocumentNavigation: l1Navigation,
          positiveDeliveryFailurePageOpen: l1OpenPage,
          positiveCleanClose: { passed: l1CleanClosePassed, independentZeroProof: l1CleanCloseZeroProof },
          positiveCompletedNavigation: { passed: l1CompletedNavigationPassed, committed: l1CompletedNavigationCommitted, persisted: l1CompletedPersistence },
        },
        L3: {
          heldAcknowledgement: {
            deadlineIdentity: l3HeldAckDeadlineIdentity,
            budgetMs: l3HeldAckBudgetMs,
            outstandingIdentity: l3HeldAckOutstandingIdentity,
            outstandingState: l3HeldAckOutstandingState,
            lateReleaseRetainedFailure: l3HeldAckRetained,
          },
          normalAcknowledgement: { passed: l3NormalAckPassed, deadlineIdentity: l3Ack.tracker.drainReports.at(-1)?.identity || "", durable: l3NormalAckPersistence },
          nativeCompletion: { passed: l3NativePassed, durable: l3NativePersistence },
          finalZeroInsideBudget: { passed: l3FinalInsideResult, deadlineIdentity: l3FinalInsideDeadlineIdentity, budgetMs: l3FinalInsideBudgetMs, durable: l3FinalInsidePersistence },
          finalZeroPastBudget: {
            failedOnOriginalDeadline: l3FinalLateFailure,
            deadlineIdentity: l3FinalLateDeadlineIdentity,
            budgetMs: l3FinalLateBudgetMs,
            outstandingIdentity: l3FinalLateOutstandingIdentity,
            outstandingState: l3FinalLateOutstandingState,
            latePersistenceDidNotEraseFailure: l3FinalLatePersistence,
          },
        },
        equivalentPathReview: {
          L1: ["delivery-failure", "event-buffer-and-ack", "page-owner", "document-generation", "page-close", "context-close", "committed-navigation", "new-document-installation", "disable-detach", "final-zero", "closed-page-skip"],
          L2: ["flush-reservation", "flush-start", "flush-failure", "flush-completion", "flush-abandonment", "native-timer-producer", "timer-cancellation", "replacement-transfer", "save-request-body-readback", "producer-cleanup", "orphan-detection", "F2-recovery-caller"],
          L3: ["event-promises", "browser-state-sync", "page-state-queries", "producer-reconciliation", "progress-waits", "final-zero-barrier", "final-zero-reconciliation", "multi-page-traversal"],
        },
      };
    };


    const runSqag212RecoveryNullControl = async (savedSessionId) => {
      const recoveryTracker = createQuoteSessionDrainTracker("sqag212-f2-recovery-null");
      const recoveryContext = await isolatedContext.browser().newContext({ viewport: { width: 1365, height: 900 } });
      let recoveryPage = null;
      let releaseNavigation = null;
      let navigationSeenResolve = null;
      const navigationSeen = new Promise((resolve) => { navigationSeenResolve = resolve; });
      let navigationAbortedResolve = null;
      const navigationAborted = new Promise((resolve) => { navigationAbortedResolve = resolve; });
      try {
        await recoveryTracker.install(recoveryContext);
        recoveryPage = await recoveryContext.newPage();
        await recoveryPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await recoveryPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        if (!await recoveryTracker.drain(15000, recoveryPage)) {
          throw new Error("F2 recovery-null page did not reach a clean baseline: " + JSON.stringify(recoveryTracker.summary()) + ".");
        }
        const recoveryBaseline = await recoveryPage.evaluate(() => ({
          recoveryScope: String(currentBrowserRecoveryScope() || ""),
          csrfToken: String(state.csrfToken || ""),
          csrfHeaderName: String(state.csrfHeaderName || ""),
        }));
        if (!recoveryBaseline.recoveryScope || !recoveryBaseline.csrfToken) {
          throw new Error("F2 recovery-null control lacks a real current recovery scope or CSRF state: " + JSON.stringify(recoveryBaseline) + ".");
        }
        const operationId = "sqag212/f2-recovery-transition-null/" + Date.now();
        await recoveryTracker.setPageOperation(recoveryPage, {
          fixture: "sqag212",
          operation: "f2-recovery-transition-null-save",
          operationId,
          expectedSessionId: savedSessionId,
          persistenceClass: "REQUIRED_SUCCESS",
        });
        await recoveryPage.evaluate((id) => {
          state.quoteSessionId = id;
          state.quoteSessionDraftSaveStarted = true;
          const timerState = window.__sqagSmokeQuoteSessionTimerState;
          timerState.saveCurrentCalls.length = 0;
          timerState.apiTrace.length = 0;
          queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 });
        }, savedSessionId);
        const captured = await recoveryTracker.capturePendingQueuedSave(recoveryPage);
        if (!captured || captured.snapshot.sessionId !== savedSessionId) {
          throw new Error("F2 did not capture the exact pending timer before recovery transition: " + JSON.stringify(captured?.snapshot || null) + ".");
        }
        const recoveryContinuation = await recoveryTracker.startRecoveryFlushOnActualCancellation(recoveryPage, captured);
        if (recoveryContinuation.status !== "armed" || recoveryContinuation.actualNativeTimerActive !== true) {
          throw new Error("F2 could not establish the executable recovery-save continuation before the real transition: " + JSON.stringify(recoveryContinuation) + ".");
        }
        await recoveryPage.evaluate(() => {
          const originalPurge = purgeBrowserRecoveryState;
          window.__sqagF2PurgeReached = false;
          window.__sqagF2ReleasePurge = null;
          purgeBrowserRecoveryState = async (...args) => {
            await originalPurge(...args);
            window.__sqagF2PurgeReached = true;
            await new Promise((resolve) => { window.__sqagF2ReleasePurge = resolve; });
          };
        });
        await recoveryPage.route("**/*", async (route) => {
          const request = route.request();
          if (request.isNavigationRequest() && request.frame() === recoveryPage.mainFrame()) {
            navigationSeenResolve(request.url());
            await new Promise((resolve) => { releaseNavigation = resolve; });
            await route.abort();
            navigationAbortedResolve(request.url());
            return;
          }
          await route.fallback();
        });
        await recoveryPage.evaluate(({ csrfToken, csrfHeaderName, recoveryScope }) => {
          window.__sqagF2RecoveryTransition = applySessionData({
            csrf_token: csrfToken,
            csrf_header: csrfHeaderName,
            browser_recovery_scope: recoveryScope + "-sqag212-next",
          });
        }, recoveryBaseline);
        await recoveryPage.waitForFunction(() => window.__sqagF2PurgeReached === true, null, { timeout: 15000 });
        const transition = await recoveryPage.evaluate(() => ({
            transitioning: state.isRecoveryScopeTransitioning === true,
            sessionId: String(state.quoteSessionId || ""),
            timer: window.__sqagSmokeGetPendingQuoteSessionDraftSave?.(),
          }));
        if (!transition.transitioning || transition.sessionId !== savedSessionId) {
          throw new Error("F2 did not reproduce a real recovery-scope transition while preserving in-memory session identity: " + JSON.stringify(transition) + ".");
        }

        const recoveryOutcome = await recoveryPage.evaluate(async (identity) => {
          const timerState = window.__sqagSmokeQuoteSessionTimerState || {};
          return timerState.flushPromises?.[identity] || null;
        }, captured.snapshot.browserTimerIdentity);
        let flushError = "";
        try {
          await flushRequiredQuoteSessionSaves(recoveryPage, recoveryTracker, captured);
        } catch (error) {
          flushError = String(error?.message || error);
        }
        const repeatedDrain = await recoveryTracker.drain(1000, recoveryPage);
        const browserEvidence = await recoveryPage.evaluate(() => {
          const timerState = window.__sqagSmokeQuoteSessionTimerState || {};
          return {
            sessionId: String(state.quoteSessionId || ""),
            transitioning: state.isRecoveryScopeTransitioning === true,
            saveCurrentCalls: timerState.saveCurrentCalls.length,
            apiTrace: timerState.apiTrace.map((entry) => ({ method: entry.method, path: entry.path })),
          };
        });
        const entry = recoveryTracker.queuedSaveStatus(captured.snapshot.timerIdentity);
        const failure = recoveryTracker.issues.find((issue) => issue.operationId === entry?.identity);
        const postObserved = browserEvidence.apiTrace.some((request) => request.method === "POST" && request.path === "/api/quote-sessions");
        const durableReadbackObserved = browserEvidence.apiTrace.some((request) => request.method === "GET" && request.path.startsWith("/api/quote-sessions/" + savedSessionId));
        if (
          !flushError
          || repeatedDrain
          || entry?.status !== "failed"
          || !String(entry.terminalReason || "").includes("returned null")
          || recoveryOutcome?.kind !== "null"
          || !failure
          || browserEvidence.sessionId !== savedSessionId
          || browserEvidence.transitioning !== true
          || browserEvidence.saveCurrentCalls !== 0
          || postObserved
          || durableReadbackObserved
        ) {
          throw new Error("F2 recovery-transition null result did not fail sticky with zero POST/readback evidence: " + JSON.stringify({
            flushError, repeatedDrain, entry: entry && { status: entry.status, reason: entry.terminalReason },
            recoveryOutcome, failure, browserEvidence, postObserved, durableReadbackObserved, summary: recoveryTracker.summary(),
          }) + ".");
        }
        await recoveryPage.evaluate(() => window.__sqagF2ReleasePurge?.());
        const navigationUrl = await Promise.race([
          navigationSeen,
          new Promise((_, reject) => setTimeout(() => reject(new Error("F2 recovery transition did not initiate its real reload navigation.")), 8000)),
        ]);
        if (typeof releaseNavigation !== "function") {
          throw new Error("F2 recovery navigation was observed without an abort release handle.");
        }
        releaseNavigation();
        releaseNavigation = null;
        const abortedUrl = await Promise.race([
          navigationAborted,
          new Promise((_, reject) => setTimeout(() => reject(new Error("F2 recovery reload request did not reach the controlled abort point.")), 5000)),
        ]);
        if (abortedUrl !== navigationUrl) {
          throw new Error("F2 controlled reload abort did not match the observed navigation.");
        }
        return {
          flushResult: "FAIL",
          stickyFailure: "YES",
          requiredWorkNotSilentlySatisfied: "YES",
          executableRecoveryContinuationStarted: "YES",
          actualApplicationSaveReturnedNull: "YES",
          postObserved: "NO",
          durableReadbackObserved: "NO",
          preservedSessionId: browserEvidence.sessionId,
          failureReason: entry.terminalReason,
        };
      } finally {
        if (recoveryPage && !recoveryPage.isClosed()) {
          await recoveryPage.evaluate(() => window.__sqagF2ReleasePurge?.()).catch(() => {});
        }
        if (typeof releaseNavigation === "function") releaseNavigation();
        await recoveryContext.close();
      }
    };

    const setSyntheticDraftPrice = async (price) => browserPage.evaluate((value) => {
      const current = state.outputRows?.[0];
      if (!current) throw new Error("C6 requires an existing output row.");
      const priced = synchronizeOwnedOutputRowPrice(current, value, { force: true });
      state.outputRows[0] = recalculateOutputRow(priced);
      renderPricingMatches(state.outputRows);
      saveSessionState();
      return currentQuoteSessionDraftState();
    }, price);

    const runSqag212TimerFlushControls = async (savedSessionId) => {
      const pendingPrice = 19.75;
      const operationId = `sqag212/c6-pending-timer/${Date.now()}`;
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "c6-pending-timer",
        operationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await setSyntheticDraftPrice(pendingPrice);
      await browserPage.evaluate(() => {
        queueQuoteSessionDraftStateSave({ quoteGenerated: false });
      });
      const captured = await tracker.capturePendingQueuedSave(browserPage);
      if (!captured || captured.snapshot.options.quoteGenerated !== false) {
        throw new Error(`C6 did not retain the real pending timer options: ${JSON.stringify(captured?.snapshot || null)}.`);
      }
      await flushRequiredQuoteSessionSaves(browserPage, tracker);
      const c6Records = tracker.records.filter((record) => record.operationId === operationId && record.persistenceClass !== "DIAGNOSTIC_ONLY");
      if (c6Records.length !== 1) {
        throw new Error(`C6 pending timer flush dispatched ${c6Records.length} real application saves instead of exactly one.`);
      }
      const c6Record = c6Records[0];
      await tracker.syncQueuedSaveTimer(browserPage);
      const c6TimerState = tracker.queuedSaveStatus(captured.snapshot.timerIdentity);
      const c6BrowserTimer = await tracker.readQueuedSaveTimer(browserPage);
      const persistedPrice = Number(c6Record.readback?.fields?.draft_state?.outputRows?.[0]?.unit_price_override);
      const c6Checks = {
        successfulSavedResponse: c6Record.httpStatus >= 200 && c6Record.httpStatus < 300 && c6Record.bodyStatus === "saved",
        exactResponseIdentity: c6Record.responseSessionId === savedSessionId,
        falseGeneratedStatus: c6Record.responseQuoteGenerated === false && c6Record.clientResult?.quoteGenerated === false && c6Record.readback?.quoteGenerated === false,
        successfulClientResult: c6Record.clientResult?.nonNull === true && c6Record.clientResult?.sessionId === savedSessionId,
        timerSucceeded: c6TimerState?.status === "succeeded",
        postAndReadbackEvidence: c6TimerState?.evidence?.postObserved === true && c6TimerState?.evidence?.durableReadbackObserved === true,
        exactlyOneFlush: c6TimerState?.transitions?.filter((transition) => transition.type === "flush").length === 1,
        timerCleared: c6BrowserTimer?.pending === null,
        expectedPriceInPostAndReadback: c6Record.payloadDraftState?.outputRows?.[0]?.unit_price_override === pendingPrice
          && c6Record.readback?.draftState?.outputRows?.[0]?.unit_price_override === pendingPrice
          && persistedPrice === pendingPrice,
        durableDraftPresent: Boolean(c6Record.readback?.draftState),
        postMatchesCapturedState: JSON.stringify(queuedDraftStateComparable(c6Record.payloadDraftState))
          === JSON.stringify(queuedDraftStateComparable(captured.snapshot.draftState)),
        readbackMatchesCapturedState: JSON.stringify(queuedDraftStateComparable(c6Record.readback?.draftState))
          === JSON.stringify(queuedDraftStateComparable(captured.snapshot.draftState)),
        postFilesOmittedOrMatchesCaptured: c6Record.payloadDraftFiles === null
          || (Array.isArray(c6Record.payloadDraftFiles)
            && JSON.stringify(queuedDraftFilesComparable(c6Record.payloadDraftFiles))
              === JSON.stringify(queuedDraftFilesComparable(captured.snapshot.draftFiles))),
        readbackMatchesCapturedFiles: JSON.stringify(queuedDraftFilesComparable(c6Record.readback?.draftFiles))
          === JSON.stringify(queuedDraftFilesComparable(captured.snapshot.draftFiles)),
      };
      if (Object.values(c6Checks).some((passed) => !passed)) {
        throw new Error(`C6 real pending timer flush did not persist the changed draft: ${JSON.stringify({ c6Checks, httpStatus: c6Record.httpStatus, bodyStatus: c6Record.bodyStatus, responseSessionId: c6Record.responseSessionId, clientResult: c6Record.clientResult, persistedPrice, timerStatus: c6TimerState?.status, timerEvidence: c6TimerState?.evidence, timerTransitions: c6TimerState?.transitions, pendingTimer: c6BrowserTimer?.pending, postPrice: c6Record.payloadDraftState?.outputRows?.[0]?.unit_price_override, readbackPrice: c6Record.readback?.draftState?.outputRows?.[0]?.unit_price_override })}.`);
      }

      const c6PostCountBeforeFinalReconciliation = tracker.records.filter((record) => record.operationId === operationId && record.method === "POST").length;
      const c6Barrier = tracker.armFinalReconciliationBarrier();
      const c6FinalDrain = tracker.drain(30000, browserPage);
      await c6Barrier.reached;
      let c6FinalTimer = null;
      try {
        await tracker.syncQueuedSaveTimer(browserPage);
        c6FinalTimer = await tracker.readQueuedSaveTimer(browserPage);
      } finally {
        c6Barrier.release();
      }
      const c6FinalDrainPassed = await c6FinalDrain;
      const c6PostCountAfterFinalReconciliation = tracker.records.filter((record) => record.operationId === operationId && record.method === "POST").length;
      if (!c6FinalDrainPassed || c6FinalTimer?.pending !== null || c6PostCountBeforeFinalReconciliation !== 1 || c6PostCountAfterFinalReconciliation !== 1) {
        throw new Error("C6 positive control did not stay at one POST with no pending timer through final reconciliation: " + JSON.stringify({
          c6FinalDrainPassed,
          c6FinalTimer,
          c6PostCountBeforeFinalReconciliation,
          c6PostCountAfterFinalReconciliation,
          summary: tracker.summary(),
        }) + ".");
      }

      const clearOnlyTracker = createQuoteSessionDrainTracker("sqag212-c6-clear-only");
      const clearOnlyContext = await isolatedContext.browser().newContext({ viewport: { width: 1365, height: 900 } });
      let clearOnlyPage = null;
      let clearOnlyCapture = null;
      let clearOnlyDrained = true;
      let clearOnlyIssue = null;
      try {
        await clearOnlyTracker.install(clearOnlyContext);
        clearOnlyPage = await clearOnlyContext.newPage();
        await clearOnlyPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await clearOnlyPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        if (!await clearOnlyTracker.drain(15000, clearOnlyPage)) {
          throw new Error(`C6 isolated clear-only page had unrelated outstanding work: ${JSON.stringify(clearOnlyTracker.summary())}.`);
        }
        await clearOnlyPage.evaluate((id) => {
          state.quoteSessionId = id;
          state.quoteSessionDraftSaveStarted = true;
          queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 });
        }, savedSessionId);
        clearOnlyCapture = await clearOnlyTracker.capturePendingQueuedSave(clearOnlyPage);
        await clearOnlyPage.evaluate(() => clearQuoteSessionDraftSaveTimer());
        clearOnlyDrained = await clearOnlyTracker.drain(1000, clearOnlyPage);
        clearOnlyIssue = clearOnlyTracker.issues.find((issue) => issue.reason.includes("ORPHAN_NO_REAL_PRODUCER"));
        const secondDrainPassed = await clearOnlyTracker.drain(1000, clearOnlyPage);
        const stickyFailure = clearOnlyCapture.entry?.status === "failed"
          && String(clearOnlyCapture.entry.terminalReason || "").includes("ORPHAN_NO_REAL_PRODUCER");
        if (clearOnlyDrained || secondDrainPassed || !stickyFailure || !clearOnlyIssue) {
          throw new Error(`C6 clear-only control unexpectedly satisfied or forgot pending work: ${JSON.stringify({
            clearOnlyDrained, secondDrainPassed, stickyFailure, clearOnlyIssue, summary: clearOnlyTracker.summary(),
          })}.`);
        }
      } finally {
        await clearOnlyContext.close();
      }

      let missingOptionsRejected = false;
      const missingOptionsTracker = createQuoteSessionDrainTracker("sqag212-c6-missing-options");
      const missingOptionsContext = await isolatedContext.browser().newContext({ viewport: { width: 1365, height: 900 } });
      try {
        await missingOptionsTracker.install(missingOptionsContext);
        const missingOptionsPage = await missingOptionsContext.newPage();
        await missingOptionsPage.goto(diagnosticRelay.baseUrl, { waitUntil: "domcontentloaded" });
        await missingOptionsPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
        if (!await missingOptionsTracker.drain(15000, missingOptionsPage)) {
          throw new Error(`C6 isolated missing-options page had unrelated outstanding work: ${JSON.stringify(missingOptionsTracker.summary())}.`);
        }
        await missingOptionsPage.evaluate((id) => {
          state.quoteSessionId = id;
          state.quoteSessionDraftSaveStarted = true;
          queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 });
          const timerState = window.__sqagSmokeQuoteSessionTimerState;
          if (timerState?.pending) timerState.pending.options = null;
        }, savedSessionId);
        try {
          await missingOptionsTracker.capturePendingQueuedSave(missingOptionsPage);
        } catch (error) {
          if (!String(error?.message || error).includes("not capturable")) throw error;
          missingOptionsRejected = true;
        } finally {
          await missingOptionsPage.evaluate(() => clearQuoteSessionDraftSaveTimer());
        }
        const missingOptionsDrained = await missingOptionsTracker.drain(1000, missingOptionsPage);
        if (!missingOptionsRejected || missingOptionsDrained || !missingOptionsTracker.issues.length) {
          throw new Error(`C6 missing pending-save options were not sticky: ${JSON.stringify({ missingOptionsRejected, missingOptionsDrained, summary: missingOptionsTracker.summary() })}.`);
        }
      } finally {
        await missingOptionsContext.close();
      }

      const joinPrice = 20.25;
      const joinOperationId = `sqag212/c6-equivalent-join/${Date.now()}`;
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "c6-equivalent-in-flight-join",
        operationId: joinOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await setSyntheticDraftPrice(joinPrice);
      let joinRelease = null;
      let joinPostCount = 0;
      let joinPostSeenResolve;
      const joinPostSeen = new Promise((resolve) => { joinPostSeenResolve = resolve; });
      await browserPage.route("**/api/quote-sessions", async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback();
          return;
        }
        joinPostCount += 1;
        if (joinPostCount === 1) {
          joinPostSeenResolve();
          await new Promise((resolve) => { joinRelease = resolve; });
        }
        await route.fallback();
      });
      try {
        const firstJoin = browserPage.evaluate(() => saveQuoteSessionDraftState({ quoteGenerated: false }));
        await Promise.race([
          joinPostSeen,
          new Promise((_, reject) => setTimeout(() => reject(new Error("C6 equivalent-join POST was not observed.")), 15000)),
        ]);
        const secondJoin = browserPage.evaluate(() => saveQuoteSessionDraftState({ quoteGenerated: false }));
        if (typeof joinRelease !== "function") throw new Error("C6 equivalent-join release authority was not established.");
        joinRelease();
        await Promise.all([firstJoin, secondJoin]);
      } finally {
        if (typeof joinRelease === "function") joinRelease();
        await browserPage.unroute("**/api/quote-sessions");
      }
      await tracker.drain(30000, browserPage);
      const joinRecords = tracker.records.filter((record) => record.operationId === joinOperationId && record.persistenceClass !== "DIAGNOSTIC_ONLY");
      if (joinPostCount !== 1 || joinRecords.length !== 1) {
        throw new Error(`C6 equivalent in-flight work did not join without a duplicate POST: ${JSON.stringify({ joinPostCount, joinRecords: joinRecords.length })}.`);
      }

      const priorPrice = 20.5;
      const changedAfterPriorPrice = 20.75;
      const changedOperationId = `sqag212/c6-changed-after-prior/${Date.now()}`;
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "c6-changed-draft-after-prior-save",
        operationId: changedOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await setSyntheticDraftPrice(priorPrice);
      let changedPostCount = 0;
      let releasePrior = null;
      let firstChangedPostSeenResolve;
      const firstChangedPostSeen = new Promise((resolve) => { firstChangedPostSeenResolve = resolve; });
      let secondChangedPostSeenResolve;
      const secondChangedPostSeen = new Promise((resolve) => { secondChangedPostSeenResolve = resolve; });
      await browserPage.route("**/api/quote-sessions", async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback();
          return;
        }
        changedPostCount += 1;
        if (changedPostCount === 1) {
          firstChangedPostSeenResolve();
          await new Promise((resolve) => { releasePrior = resolve; });
        } else if (changedPostCount === 2) {
          secondChangedPostSeenResolve();
        }
        await route.fallback();
      });
      try {
        const priorSave = browserPage.evaluate(() => saveQuoteSessionDraftState({ quoteGenerated: false }));
        await Promise.race([
          firstChangedPostSeen,
          new Promise((_, reject) => setTimeout(() => reject(new Error("C6 prior-save POST was not observed.")), 15000)),
        ]);
        await setSyntheticDraftPrice(changedAfterPriorPrice);
        const changedSave = browserPage.evaluate(() => {
          window.__sqagC6SecondSaveStarted = true;
          return saveQuoteSessionDraftState({ quoteGenerated: false });
        });
        await browserPage.waitForFunction(() => window.__sqagC6SecondSaveStarted === true, null, { timeout: 5000 });
        if (typeof releasePrior !== "function") throw new Error("C6 changed-after-prior release authority was not established.");
        releasePrior();
        await Promise.race([
          secondChangedPostSeen,
          new Promise((_, reject) => setTimeout(() => reject(new Error("C6 changed draft did not dispatch a follow-up POST.")), 15000)),
        ]);
        await Promise.all([priorSave, changedSave]);
      } finally {
        if (typeof releasePrior === "function") releasePrior();
        await browserPage.unroute("**/api/quote-sessions");
      }
      await tracker.drain(30000, browserPage);
      const changedRecords = tracker.records.filter((record) => (
        record.persistenceClass !== "DIAGNOSTIC_ONLY"
        && record.expectedSessionId === savedSessionId
        && Number(record.payloadDraftState?.outputRows?.[0]?.unit_price_override) >= priorPrice
      ));
      const finalRecord = changedRecords.at(-1);
      if (
        changedPostCount !== 2
        || changedRecords.length < 2
        || Number(finalRecord?.readback?.fields?.draft_state?.outputRows?.[0]?.unit_price_override) !== changedAfterPriorPrice
      ) {
        throw new Error(`C6 changed draft did not persist after prior save completion: ${JSON.stringify({
          changedPostCount,
          changedRecords: changedRecords.map((record) => ({ operationId: record.operationId, price: record.payloadDraftState?.outputRows?.[0]?.unit_price_override, readback: record.readback })),
        })}.`);
      }
      await setSyntheticDraftPrice(changedAfterPriorPrice);
      await flushRequiredQuoteSessionSaves(browserPage, tracker);
      const restoreOperationId = `sqag212/c6-restore-original/${Date.now()}`;
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "c6-restore-original-price",
        operationId: restoreOperationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await setSyntheticDraftPrice(18.25);
      await browserPage.evaluate(() => saveQuoteSessionDraftState({ quoteGenerated: false }));
      await tracker.drain(30000, browserPage);
      return {
        pendingTimer: {
          exactlyOneRealSave: true,
          pendingOptionsPreserved: true,
          timerFlushTransitions: c6TimerState.transitions.filter((transition) => transition.type === "flush").length,
          durableReadback: c6Record.readback.httpStatus,
          noPendingTimerAfterFinalReconciliation: c6FinalTimer.pending === null,
          postCountAfterFinalReconciliation: c6PostCountAfterFinalReconciliation,
          persistedPrice: pendingPrice,
        },
        clearOnlyFails: true,
        missingOptionsRejected,
        equivalentInFlightJoin: { postCount: joinPostCount, recordCount: joinRecords.length },
        changedDraftAfterPriorSave: { postCount: changedPostCount, finalPrice: changedAfterPriorPrice },
      };
    };


    const runSqag212GeneratedTimerReplacementControl = async (savedSessionId) => {
      await flushRequiredQuoteSessionSaves(browserPage, tracker);
      const preflightId = "sqag212/f3-generated-status-preflight/" + Date.now();
      await tracker.setPageDiagnostic(browserPage, {
        operationId: preflightId,
        operation: "f3-generated-status-preflight",
      }, savedSessionId);
      const persistedBefore = await browserPage.evaluate(async (id) => {
        const response = await fetch("/api/quote-sessions/" + encodeURIComponent(id) + "?__sqag_f3_before=" + Date.now(), {
          cache: "no-store",
          headers: { "cache-control": "no-cache", pragma: "no-cache" },
        });
        const body = await response.json();
        const session = body?.quote_session || {};
        return {
          httpStatus: response.status,
          sessionId: String(session.session_id || ""),
          quoteGenerated: session.status?.quote_generated === true,
          xlsxExists: session.exports?.xlsx?.exists === true,
          draftState: session.draft_state || null,
        };
      }, savedSessionId);
      await tracker.drain(30000, browserPage);
      if (
        persistedBefore.httpStatus !== 200
        || persistedBefore.sessionId !== savedSessionId
        || persistedBefore.quoteGenerated !== true
        || persistedBefore.xlsxExists !== true
      ) {
        throw new Error("F3 generated-state control requires the real persisted generated session: " + JSON.stringify(persistedBefore) + ".");
      }
      const sortChoice = await browserPage.evaluate(() => ({
        original: String(state.outputSortMode || "pricing_reference"),
        fresh: Boolean(state.downloadFile && downloadFileIsFresh(state.downloadFile)),
      }));
      if (!sortChoice.fresh) throw new Error("F3 generated-state control lost its fresh browser output before timer setup.");
      const nextSort = sortChoice.original === "name" ? "category" : "name";
      const operationId = "sqag212/f3-replaced-generated-timer/" + Date.now();
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "f3-replaced-queued-save-generated-status",
        operationId,
        expectedSessionId: savedSessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await browserPage.evaluate(() => {
        const timerState = window.__sqagSmokeQuoteSessionTimerState;
        timerState.apiTrace.length = 0;
        timerState.saveCurrentCalls.length = 0;
        queueQuoteSessionDraftStateSave({ quoteGenerated: false, delay: 60000 });
      });
      const capturedA = await tracker.capturePendingQueuedSave(browserPage);
      if (!capturedA || capturedA.snapshot.options.quoteGenerated !== false) {
        throw new Error("F3 did not capture timer A with its original false status option.");
      }
      const drainWaitCount = tracker.summary().drainWaitCount;
      const drainPromise = tracker.drain(30000, browserPage);
      const firstWait = await tracker.waitForDrainWaiting(5000, drainWaitCount);
      await browserPage.evaluate((sort) => {
        state.outputSortMode = sort;
        if (elements.outputSortMode) elements.outputSortMode.value = sort;
        queueQuoteSessionDraftStateSave({ quoteGenerated: true, delay: 60000 });
      }, nextSort);
      await tracker.syncQueuedSaveTimer(browserPage);
      await browserPage.evaluate(async () => {
        await window.__sqagSmokeQuoteSessionTimerState?.lastEventPromise;
      });
      const capturedB = await tracker.capturePendingQueuedSave(browserPage);
      if (
        !capturedB
        || capturedB.snapshot.timerIdentity === capturedA.snapshot.timerIdentity
        || capturedB.snapshot.options.quoteGenerated !== true
        || capturedB.snapshot.draftState?.outputSortMode !== nextSort
      ) {
        throw new Error("F3 replacement B did not retain a new timer identity, changed draft, and quoteGenerated=true options: " + JSON.stringify({
          capturedA: capturedA.snapshot,
          capturedB: capturedB?.snapshot || null,
        }) + ".");
      }
      const secondWait = await tracker.waitForDrainWaiting(5000, firstWait.count);
      const attemptA = await tracker.startCapturedQueuedSave(browserPage, capturedA);
      const browserTimer = await tracker.readQueuedSaveTimer(browserPage);
      let drainFinishedBeforeB = false;
      drainPromise.then(() => { drainFinishedBeforeB = true; });
      const statusA = tracker.queuedSaveStatus(capturedA.snapshot.timerIdentity);
      if (
        attemptA.status !== "replaced"
        || browserTimer?.pending?.timerIdentity !== capturedB.snapshot.browserTimerIdentity
        || browserTimer?.pending?.options?.quoteGenerated !== true
        || browserTimer?.pending?.draftState?.outputSortMode !== nextSort
        || statusA?.status !== "superseded"
        || statusA?.successorIdentity !== capturedB.snapshot.timerIdentity
        || drainFinishedBeforeB
      ) {
        throw new Error("Flushing stale timer A disturbed replacement B or let the drain pass early: " + JSON.stringify({
          attemptA,
          browserTimer,
          statusA: statusA && { status: statusA.status, successorIdentity: statusA.successorIdentity },
          drainFinishedBeforeB,
          secondWait,
        }) + ".");
      }
      const flushB = await flushRequiredQuoteSessionSaves(browserPage, tracker, capturedB);
      const drainPassed = await drainPromise;
      const records = tracker.records.filter((record) => record.operationId === operationId && record.method === "POST");
      const statusB = tracker.queuedSaveStatus(capturedB.snapshot.timerIdentity);
      const browserEvidence = await browserPage.evaluate(() => ({
        apiTrace: (window.__sqagSmokeQuoteSessionTimerState?.apiTrace || []).map((entry) => ({ method: entry.method, path: entry.path })),
        saveCurrentCalls: (window.__sqagSmokeQuoteSessionTimerState?.saveCurrentCalls || []).length,
        timer: window.__sqagSmokeGetPendingQuoteSessionDraftSave?.(),
      }));
      const record = records[0];
      if (
        !drainPassed
        || records.length !== 1
        || browserEvidence.saveCurrentCalls !== 1
        || browserEvidence.apiTrace.filter((entry) => entry.method === "POST" && entry.path === "/api/quote-sessions").length !== 1
        || statusB?.status !== "succeeded"
        || statusB?.evidence?.postObserved !== true
        || statusB?.evidence?.durableReadbackObserved !== true
        || record?.responseQuoteGenerated !== true
        || record?.clientResult?.quoteGenerated !== true
        || record?.readback?.quoteGenerated !== true
        || record?.readback?.httpStatus !== 200
        || record?.payloadDraftState?.outputSortMode !== nextSort
        || record?.readback?.draftState?.outputSortMode !== nextSort
        || JSON.stringify(queuedDraftStateComparable(record?.payloadDraftState)) !== JSON.stringify(queuedDraftStateComparable(capturedB.snapshot.draftState))
        || JSON.stringify(queuedDraftStateComparable(record?.readback?.draftState)) !== JSON.stringify(queuedDraftStateComparable(capturedB.snapshot.draftState))
        || (record?.payloadDraftFiles !== null
          && JSON.stringify(queuedDraftFilesComparable(record?.payloadDraftFiles)) !== JSON.stringify(queuedDraftFilesComparable(capturedB.snapshot.draftFiles)))
        || JSON.stringify(queuedDraftFilesComparable(record?.readback?.draftFiles)) !== JSON.stringify(queuedDraftFilesComparable(capturedB.snapshot.draftFiles))
        || record?.expectedSessionId !== savedSessionId
        || record?.responseSessionId !== savedSessionId
        || record?.httpStatus < 200
        || record?.httpStatus >= 300
        || record?.bodyStatus !== "saved"
      ) {
        throw new Error("F3 replacement B did not prove exactly one generated-status POST and matching durable readback: " + JSON.stringify({
          drainPassed, flushB, records: records.length, statusA: statusA && statusA.status,
          statusB: statusB && { status: statusB.status, evidence: statusB.evidence },
          browserEvidence, record: record && {
            httpStatus: record.httpStatus,
            bodyStatus: record.bodyStatus,
            responseSessionId: record.responseSessionId,
            responseQuoteGenerated: record.responseQuoteGenerated,
            clientResult: record.clientResult,
            payloadDraftState: record.payloadDraftState,
            readback: record.readback,
          },
        }) + ".");
      }
      return {
        aToBReplacement: true,
        staleAWasNotDispatched: statusA.evidence.postObserved !== true,
        drainWaitedForB: true,
        exactlyOnePost: records.length,
        quoteGenerated: record.readback.quoteGenerated,
        changedDraftField: "outputSortMode",
        changedDraftValue: nextSort,
        durableReadback: record.readback.httpStatus,
        timerCleared: browserEvidence.timer?.pending === null,
      };
    };

    await installMockProfiles(browserPage, { companyProfiles: [companyProfile] });
    await browserPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await browserPage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await browserPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });

    stage = "saving the synthetic pricing reference";
    const savedPricing = await saveSmokePricingReference(browserPage, 10);
    if (!savedPricing.ok || !["saved", "unchanged"].includes(savedPricing.data?.status)) {
      throw new Error(`Could not prepare the synthetic pricing reference for SQAG #212: ${JSON.stringify(savedPricing.data)}.`);
    }
    const savedCompanyProfile = await browserPage.evaluate(async (profile) => postJson("/api/settings/profiles", profile), companyProfile);
    if (!savedCompanyProfile.ok || savedCompanyProfile.data?.status !== "saved") {
      throw new Error(`Could not prepare the synthetic company profile for SQAG #212: ${JSON.stringify(savedCompanyProfile.data)}.`);
    }
    const emptyNewQuoteButton = browserPage.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
    if (await emptyNewQuoteButton.isVisible()) await emptyNewQuoteButton.click();
    else await browserPage.locator("#newQuoteButton:not([disabled])").click();
    await browserPage.locator("#imageIntake.is-active").waitFor({ state: "visible", timeout: 15000 });
    const initial = await browserPage.evaluate(() => ({
      snapshot: state.quoteCommercialSnapshot,
      review: state.quoteCommercialReview,
      basisConfirmed: state.basisConfirmed,
      lifecycle: state.quoteCommercialLifecycle,
    }));
    if (initial.snapshot || initial.review !== null || initial.basisConfirmed || initial.lifecycle !== "NEW_UNINITIALISED") {
      throw new Error(`SQAG #212 browser flow did not begin with a fresh quote: ${JSON.stringify(initial)}.`);
    }

    stage = "uploading the synthetic render";
    await browserPage.locator("#imageInput").setInputFiles({
      name: renderName,
      mimeType: "image/png",
      buffer: renderBytes,
    });
    await browserPage.locator("#fileList .file-item", { hasText: renderName }).waitFor({ state: "visible", timeout: 15000 });
    await browserPage.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
    await browserPage.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await browserPage.waitForFunction(() => state.pricingReferenceId === "synthetic-exhibition-fixture-pricing", null, { timeout: 15000 });
    stage = "entering customer and quote company details";
    await browserPage.locator("#clientNameEditor").fill("SQAG 212 Synthetic Client");
    await browserPage.locator("#clientAttentionEditor").fill("Synthetic Contact");
    await browserPage.locator("#clientTitleEditor").fill("Project Manager");
    await browserPage.locator("#clientAddressEditor").fill("1 Synthetic Street\nSingapore 000001");
    await browserPage.locator("#projectTitleEditor").fill("SQAG 212 Render Quote");
    await browserPage.locator("#showName").fill("SQAG 212 Synthetic Show");
    await browserPage.locator("#quoteDate").fill("2026-09-24");
    await browserPage.locator("#projectNumberEditor").fill("SQAG-212-001");
    await browserPage.locator("#sideNextButton", { hasText: "Next: Quote Company" }).click();
    await browserPage.locator("#quoteCompanyPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await browserPage.locator("#presetSelect").selectOption("company:sqag212-company-profile");
    await browserPage.waitForFunction(() => state.selectedPresetValue === "company:sqag212-company-profile", null, { timeout: 15000 });
    await browserPage.locator("#quoteCompanyNameEditor").fill("SQAG 212 Synthetic Quote Co");
    await browserPage.locator("#headerDetailsEditor").fill("SQAG 212 Synthetic Quote Co\n1 Synthetic Street");
    await browserPage.locator("#termsHeadingEditor").fill("Commercial Terms");
    await browserPage.locator("#paymentTermsEditor").fill("Payment upon confirmation.");
    await browserPage.locator("#notesHeadingEditor").fill("Notes");
    await browserPage.locator("#acceptanceTextEditor").fill("We accept this quotation.");
    await browserPage.locator("#companySignatoryEditor").fill("Synthetic Signatory");
    await browserPage.locator("#companyTitleEditor").fill("Director");
    await browserPage.locator("#companyDateLabelEditor").fill("Date:");
    await browserPage.locator("#personLabelEditor").fill("Authorised person");
    await browserPage.locator("#stampLabelEditor").fill("Company stamp");
    await browserPage.locator("#dateLabelEditor").fill("Signed date:");

    stage = "running analysis from the uploaded render";
    await browserPage.route("**/api/jobs**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (request.method() === "POST" && url.pathname === "/api/jobs") {
        const body = request.postDataJSON();
        lastJobPostType = String(body.type || "");
        if (body.type !== "draft") {
          await route.fallback();
          return;
        }
        capturedDraft = body;
        const jobId = String(body.job_id || "");
        if (jobId) draftJobIds.add(jobId);
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: jobId,
            type: "draft",
            status: "running",
            created_at: "2026-09-24T00:00:00Z",
          }),
        });
        return;
      }
      const jobId = url.pathname.split("/").pop() || "";
      if (request.method() === "GET" && draftJobIds.has(jobId)) {
        draftPollCount += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: jobId,
            type: "draft",
            status: "completed",
            result: {
              source: "openai",
              analysis_mode: "standard",
              analysis_findings: [{ text: "Synthetic render confirms a compact exhibition booth footprint.", confidence_pct: 99 }],
              quote_basis: { "sqag212-floor-design": "Confirm: Needle punch carpet in colour" },
              quote_basis_sections: [{
                id: "sqag212-floor-design",
                title: "Floor Design",
                lines: [{
                  id: "sqag212-floor-line",
                  tag: "Confirm",
                  text: "Needle punch carpet in colour",
                  include: true,
                  quantity: 2,
                  unit: "sqm",
                  pricing_keyword: "synthetic-floor-needle-punch-carpet",
                }],
              }],
              line_items: [{
                section: "Floor Design",
                quantity: 2,
                unit: "sqm",
                description: "Needle punch carpet in colour",
                pricing_keyword: "synthetic-floor-needle-punch-carpet",
                source_basis_line_id: "sqag212-floor-line",
              }],
              project: { booth_width: "3", booth_depth: "3", booth_size: "3m x 3m", dimension_source: "analysis" },
            },
          }),
        });
        return;
      }
      await route.fallback();
    });

    await browserPage.locator("#sideNextButton", { hasText: "Start Analysis" }).click();
    await browserPage.locator("#analysisConfirmModal").waitFor({ state: "visible", timeout: 15000 });
    await browserPage.locator("#analysisConfirmStartButton").click();
    await browserPage.locator("#analysisConfirmModal").waitFor({ state: "hidden", timeout: 15000 });
    await browserPage.waitForFunction(() => state.workflowStage === "basis_review" && !state.isAnalysisRunning, null, { timeout: 30000 });
    if (
      !capturedDraft
      || capturedDraft.type !== "draft"
      || !capturedDraft.payload?.images?.some((image) => image.name === renderName && image.type === "image/png")
    ) {
      throw new Error("SQAG #212 analysis did not use the uploaded synthetic render.");
    }
    await browserPage.locator("#quoteBasisPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    stage = "explicitly accepting the analyzed basis line";
    await browserPage.locator('#basisReviewSurface [data-basis-section="sqag212-floor-design"][data-basis-line-index="0"][data-basis-tag="Include"]').click();
    await browserPage.waitForFunction(() => (
      state.quoteBasisSections[0]?.lines[0]?.tag === "Include"
      && elements.sideNextButton.getAttribute("aria-disabled") !== "true"
    ), null, { timeout: 15000 });
    stage = "confirming the analyzed quotation basis";
    await browserPage.locator("#sideNextButton", { hasText: "Confirm Quotation Basis" }).click();
    await browserPage.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 30000 });
    stage = "editing the output price";
    await browserPage.locator('#pricingMatchesBody td[data-output-edit-field="unit_price_override"][data-output-row="0"]').click();
    const priceEditor = browserPage.locator('input[data-output-editor-field="unit_price_override"][data-output-row="0"]');
    await priceEditor.waitFor({ state: "visible", timeout: 15000 });
    await priceEditor.fill("18.25");
    await priceEditor.press("Enter");
    await browserPage.waitForFunction(() => (
      state.outputRows[0]?.pricing_authority?.variant === "manual"
      && Number(state.outputRows[0]?.unit_price_override) === 18.25
      && state.quoteCommercialReview === null
    ), null, { timeout: 15000 });
    sessionId = await currentQuoteSessionId(browserPage);
    if (!sessionId) throw new Error("SQAG #212 quote was not saved after basis confirmation.");
    if (deferTrackerUntilSessionStable) {
      await tracker.install(isolatedContext);
      await browserPage.reload({ waitUntil: "domcontentloaded" });
      await browserPage.waitForFunction(() => state.isBooting === false, null, { timeout: 30000 });
      await browserPage.waitForFunction(() => state.quoteSessionRestoreBusy === false, null, { timeout: 30000 });
      await browserPage.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 30000 });
      if (!await tracker.drain(30000, browserPage)) {
        throw new Error("G3 focused tracker installation did not reach a clean persisted-session baseline: " + JSON.stringify(tracker.summary()) + ".");
      }
      if (g3LifecycleOnly) {
        drainControls = {
          ...(await runSqag212TrackerFailureControls()),
          threeFindingHarness: await runSqag212ThreeFindingControls(sessionId),
          recoveryTransitionNull: await runSqag212RecoveryNullControl(sessionId),
        };
        drainSummary = await tracker.assert();
        return {
          quoteSessionPostStatuses: quoteSessionResponses
            .filter((response) => response.method === "POST")
            .map((response) => response.status),
          quoteSessionResponseCount: quoteSessionResponses.length,
          quoteSessionDrain: drainSummary,
          drainControls,
          timerFlushControls: null,
          browserNegativeControl: null,
          unexpectedRequestFailures,
          listenerFailures,
          focusedG3LifecycleControlsPassed: true,
          equivalentPathReview: drainControls.threeFindingHarness.equivalentPathReview,
        };
      }
    }
    stage = "awaiting the initiating application save and positive drain control";
    const positiveOperationId = "sqag212/positive-control-save/" + Date.now();
    const initiatingTimer = await tracker.capturePendingQueuedSave(browserPage);
    if (initiatingTimer) {
      await tracker.setPageOperation(browserPage, {
        fixture: "sqag212",
        operation: "initiating-pending-draft-timer",
        operationId: "sqag212/initiating-pending-draft-timer/" + Date.now(),
        expectedSessionId: sessionId,
        persistenceClass: "REQUIRED_SUCCESS",
      });
      await flushRequiredQuoteSessionSaves(browserPage, tracker, initiatingTimer);
    }
    await tracker.setPageOperation(browserPage, {
      fixture: "sqag212",
      operation: "positive-control-save",
      operationId: positiveOperationId,
      expectedSessionId: sessionId,
    });
    const positiveSave = await browserPage.evaluate(async () => {
      clearQuoteSessionDraftSaveTimer();
      const priorSaves = [quoteSessionInitialSavePromise, quoteSessionDraftSavePromise].filter(Boolean);
      await Promise.all(priorSaves);
      saveSessionState();
      const session = await saveCurrentQuoteSession({
        sessionId: state.quoteSessionId,
        quoteGenerated: false,
        includeDraftState: true,
        includeDraftFiles: true,
        draftState: currentQuoteSessionDraftState(),
        draftFiles: sessionFileRecordsFromDraft(),
      });
      return {
        sessionId: String(session?.session_id || ""),
        quoteGenerated: session?.status?.quote_generated === true,
      };
    });
    if (positiveSave.sessionId !== sessionId || positiveSave.quoteGenerated) {
      throw new Error("Positive control initiating save did not return the exact draft session: " + JSON.stringify(positiveSave) + ".");
    }
    await flushRequiredQuoteSessionSaves(browserPage, tracker);
    const positiveRecord = tracker.records.find((record) => record.operationId === positiveOperationId && record.persistenceClass !== "DIAGNOSTIC_ONLY");
    if (!positiveRecord) throw new Error("Positive control did not capture its initiating quote-session save request.");
    if (
      positiveRecord.clientResult?.sessionId !== positiveSave.sessionId
      || positiveRecord.clientResult?.quoteGenerated !== false
    ) {
      throw new Error("Positive control request and initiating application promise did not agree: " + JSON.stringify({
        positiveSave,
        clientResult: positiveRecord.clientResult,
      }) + ".");
    }
    const savedDetail = await tracker.requiredDetailReadback(browserPage, sessionId, positiveRecord.expectedFields);
    const savedDraft = savedDetail.quote_session?.draft_state || {};
    const savedRow = savedDraft.outputRows?.[0] || {};
    if (
      Number(savedRow.unit_price_override) !== 18.25
      || savedDraft.quoteCommercialReview != null
      || !savedDraft.quoteDetails?.commercial_snapshot?.pricing_basis?.digest
    ) {
      throw new Error(`Positive control durable detail readback lost the edited quote state: ${JSON.stringify(savedDetail)}.`);
    }
    const dashboardExpectedFields = Object.fromEntries(
      Object.entries(positiveRecord.expectedFields).filter(([key]) => key !== "draft_state"),
    );
    const dashboardSession = await tracker.requiredDashboardRefresh(browserPage, sessionId, dashboardExpectedFields);
    if (dashboardSession.session_id !== sessionId) {
      throw new Error(`Dashboard refresh returned a different quote session: ${JSON.stringify(dashboardSession)}.`);
    }
    if (verificationOptions.skipFocusedControls !== true) {
      drainControls = {
        ...(await runSqag212TrackerFailureControls()),
        threeFindingHarness: await runSqag212ThreeFindingControls(sessionId),
        asyncProgress: await runSqag212DrainControls(sessionId),
        ...(await runSqag212DrainRaceControls(sessionId)),
        recoveryTransitionNull: await runSqag212RecoveryNullControl(sessionId),
      };
      timerFlushControls = await runSqag212TimerFlushControls(sessionId);
      await flushRequiredQuoteSessionSaves(browserPage, tracker);
      await drainListenerTasks();
      drainSummary = await tracker.assert();
      const negativePayload = await browserPage.evaluate(() => currentQuoteSessionPayload({
        sessionId: state.quoteSessionId,
        quoteGenerated: false,
        includeDraftState: true,
        draftState: currentQuoteSessionDraftState(),
        draftFiles: sessionFileRecordsFromDraft(),
      }));
      const negativeSessionId = `quote-sqag212-negative-${Date.now()}`;
      stage = "proving exact held save and detail requests abort only on navigation";
      browserNegativeControl = await runQuoteSessionNavigationAbortNegativeControl(negativePayload, negativeSessionId);
      if (
        browserNegativeControl.skipped
        || browserNegativeControl.outcomes?.length !== 3
        || browserNegativeControl.outcomes.slice(0, 2).some((outcome) => (
          outcome.upstreamStatus !== 200
          || outcome.requestFailedObserved !== true
          || outcome.relayClientCloseObserved !== true
          || outcome.navigationInitiated !== true
          || outcome.navigationCommitted !== true
          || outcome.sameCorrelatedRequest !== true
          || outcome.requestFailureText !== "net::ERR_ABORTED"
        ))
        || browserNegativeControl.outcomes[0]?.method !== "POST"
        || browserNegativeControl.outcomes[1]?.method !== "GET"
        || browserNegativeControl.outcomes[2]?.upstreamStatus !== 200
        || browserNegativeControl.outcomes[2]?.requestFailedObserved !== false
        || browserNegativeControl.outcomes[2]?.relayClientCloseObserved !== false
        || browserNegativeControl.outcomes[2]?.navigationInitiated !== false
        || browserNegativeControl.outcomes[2]?.navigationCommitted !== false
        || browserNegativeControl.missingObservationRejected !== true
        || browserNegativeControl.noNavigationPositive !== "PASS"
      ) {
        throw new Error(`SQAG #212 real navigation-failure control was incomplete: ${JSON.stringify({ browserNegativeControl, observedDiagnosticNavigations })}.`);
      }
      await drainListenerTasks();
      await tracker.assert();
    } else {
      await flushRequiredQuoteSessionSaves(browserPage, tracker);
      await drainListenerTasks();
      drainSummary = await tracker.assert();
    }

    const savedState = await browserPage.evaluate(() => ({
      snapshot: state.quoteCommercialSnapshot,
      review: state.quoteCommercialReview,
      basisConfirmed: state.basisConfirmed,
    }));
    stage = "restoring the saved quote after reload";
    await browserPage.reload({ waitUntil: "domcontentloaded" });
    await browserPage.waitForFunction(() => state.isBooting === false, null, { timeout: 30000 });
    await browserPage.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 30000 });
    await browserPage.waitForFunction(() => state.quoteSessionRestoreBusy === false, null, { timeout: 30000 });
    const restoredState = await browserPage.evaluate(() => {
      const payload = buildPayload();
      const draft = payload.quote_session?.draft_state || {};
      return {
        stateSnapshot: state.quoteCommercialSnapshot,
        stateReview: state.quoteCommercialReview,
        stateBasisConfirmed: state.basisConfirmed,
        restoredPrice: state.outputRows[0]?.unit_price_override,
        restoredAuthority: state.outputRows[0]?.pricing_authority?.variant,
        payloadSnapshot: draft.quoteDetails?.commercial_snapshot || null,
        payloadReview: draft.quoteCommercialReview,
        payloadPrice: payload.line_items?.[0]?.unit_price_override,
        payloadAuthority: payload.line_items?.[0]?.pricing_authority?.variant,
      };
    });
    if (
      JSON.stringify(restoredState.stateSnapshot) !== JSON.stringify(savedState.snapshot)
      || restoredState.stateReview !== null
      || restoredState.stateBasisConfirmed !== savedState.basisConfirmed
      || restoredState.stateBasisConfirmed !== true
      || restoredState.payloadReview !== null
      || JSON.stringify(restoredState.payloadSnapshot) !== JSON.stringify(savedState.snapshot)
      || Number(restoredState.restoredPrice) !== 18.25
      || restoredState.restoredAuthority !== "manual"
      || Number(restoredState.payloadPrice) !== 18.25
      || restoredState.payloadAuthority !== "manual"
    ) {
      throw new Error(`SQAG #212 save/reload/buildPayload lost commercial state: ${JSON.stringify(restoredState)}.`);
    }

    const exportReadiness = await browserPage.evaluate(() => ({
      buttonDisabled: elements.sideDownloadButton.getAttribute("aria-disabled"),
      viewPdfDisabled: elements.sideViewPdfButton.getAttribute("aria-disabled"),
      validation: outputRowsValid(),
      commercialReviewRequired: quoteCommercialReviewRequired(),
      isGenerating: state.isGenerating,
      isPreparingOutput: state.isPreparingOutput,
      basisConfirmed: state.basisConfirmed,
      aiFailed: state.aiFailed,
      missingDetailFields: missingDetailFields(),
      lineItemCount: state.lineItems.length,
      workflowStage: state.workflowStage,
    }));
    if (exportReadiness.buttonDisabled === "true") {
      throw new Error(`SQAG #212 export controls were disabled after restore: ${JSON.stringify(exportReadiness)}.`);
    }

    stage = "generating and checking the XLSX";
    await browserPage.locator("#sideDownloadButton").click();
    await browserPage.waitForFunction(() => state.isGenerating || elements.resultStatus?.textContent !== "No job yet", null, { timeout: 5000 });
    await browserPage.waitForFunction(() => !state.isGenerating, null, { timeout: 60000 });
    const xlsxReady = await browserPage.evaluate(() => Boolean(state.downloadFile && downloadFileIsFresh(state.downloadFile)));
    if (!xlsxReady) throw new Error(`XLSX generation did not produce a fresh file. Readiness: ${JSON.stringify(exportReadiness)}. API failures: ${JSON.stringify(apiFailures)}.`);
    const xlsxResult = await browserPage.evaluate(async (id) => {
      const response = await fetch(`/api/quote-sessions/${encodeURIComponent(id)}`);
      const detail = await response.json();
      return detail.quote_session?.exports?.xlsx || {};
    }, sessionId);
    if (xlsxResult.filename !== "quotation.xlsx" || xlsxResult.exists !== true || !xlsxResult.url) {
      throw new Error(`SQAG #212 did not generate a current XLSX after reload: ${JSON.stringify(xlsxResult)}.`);
    }
    const xlsxResponse = await browserPage.request.get(new URL(xlsxResult.url, baseUrl).href);
    const xlsxBytes = await xlsxResponse.body();
    if (!xlsxResponse.ok() || xlsxBytes.length < 4 || xlsxBytes[0] !== 0x50 || xlsxBytes[1] !== 0x4b) {
      throw new Error("SQAG #212 XLSX download was not a valid ZIP-based workbook.");
    }

    if (verificationOptions.skipFocusedControls !== true) {
      timerFlushControls.generatedTimerReplacement = await runSqag212GeneratedTimerReplacementControl(sessionId);
    }

    if (g3ControlsOnly) {
      stage = "finishing the focused SQAG #212 G3 controls before the known Windows PDF boundary";
      await flushRequiredQuoteSessionSaves(browserPage, tracker);
      await drainListenerTasks();
      drainSummary = await tracker.assert();
      return {
        quoteSessionPostStatuses: quoteSessionResponses
          .filter((response) => response.method === "POST")
          .map((response) => response.status),
        quoteSessionResponseCount: quoteSessionResponses.length,
        quoteSessionDrain: drainSummary,
        drainControls,
        timerFlushControls,
        browserNegativeControl,
        unexpectedRequestFailures,
        listenerFailures,
        focusedG3ControlsPassed: true,
        pdfParitySkipped: "focused Windows run stops before the known LibreOffice/Excel PDF boundary",
      };
    }

    stage = "generating and checking the explicit PDF";
    const pdfReadiness = await browserPage.evaluate(() => ({
      buttonDisabled: elements.sideViewPdfButton.getAttribute("aria-disabled"),
      validation: outputRowsValid(),
      commercialReviewRequired: quoteCommercialReviewRequired(),
      basisConfirmed: state.basisConfirmed,
      aiFailed: state.aiFailed,
      missingDetailFields: missingDetailFields(),
      lineItemCount: state.lineItems.length,
      workflowStage: state.workflowStage,
    }));
    if (pdfReadiness.buttonDisabled === "true") {
      throw new Error(`SQAG #212 PDF control was disabled after XLSX generation: ${JSON.stringify(pdfReadiness)}.`);
    }
    await browserPage.locator("#sideViewPdfButton").click();
    await browserPage.waitForFunction(() => state.isGenerating || elements.resultStatus?.textContent !== "Completed", null, { timeout: 5000 });
    await browserPage.waitForFunction(() => !state.isGenerating, null, { timeout: 60000 });
    const pdfReady = await browserPage.evaluate(() => Boolean(state.pdfFile && pdfFileIsFresh(state.pdfFile)));
    if (!pdfReady) throw new Error(`Explicit PDF generation did not produce a fresh file. Readiness: ${JSON.stringify(pdfReadiness)}. API failures: ${JSON.stringify(apiFailures)}.`);
    const pdfResult = await browserPage.evaluate(async (id) => {
      const response = await fetch(`/api/quote-sessions/${encodeURIComponent(id)}`);
      const detail = await response.json();
      return detail.quote_session?.exports?.pdf || {};
    }, sessionId);
    if (pdfResult.filename !== "quotation.pdf" || pdfResult.exists !== true || !pdfResult.url) {
      throw new Error(`SQAG #212 explicit PDF action did not generate a current PDF: ${JSON.stringify(pdfResult)}.`);
    }
    const pdfResponse = await browserPage.request.get(new URL(pdfResult.url, baseUrl).href);
    const pdfBytes = await pdfResponse.body();
    if (!pdfResponse.ok() || pdfBytes.subarray(0, 4).toString("ascii") !== "%PDF") {
      throw new Error("SQAG #212 explicit PDF download did not contain a PDF document.");
    }
    stage = "draining all required work before navigation and cleanup";
    await flushRequiredQuoteSessionSaves(browserPage, tracker);
    await drainListenerTasks();
    const finalJobState = await browserPage.evaluate(() => {
      const phase = String(state.activeJob?.phase || state.activeJob?.status || "").toLowerCase();
      return {
        isGenerating: state.isGenerating,
        isPreparingOutput: state.isPreparingOutput,
        nonterminalRequiredJobs: Boolean(state.activeJob && !["completed", "failed", "error", "cancelled", "succeeded"].includes(phase)),
      };
    });
    if (finalJobState.isGenerating || finalJobState.isPreparingOutput || finalJobState.nonterminalRequiredJobs) {
      throw new Error(`Required Dashboard generation work was not terminal before cleanup: ${JSON.stringify(finalJobState)}.`);
    }
    drainSummary = await tracker.assert();
    return {
      quoteSessionPostStatuses: quoteSessionResponses
        .filter((response) => response.method === "POST")
        .map((response) => response.status),
      quoteSessionResponseCount: quoteSessionResponses.length,
      quoteSessionDrain: drainSummary,
      drainControls,
      timerFlushControls,
      browserNegativeControl,
      workbookExport: {
        filename: xlsxResult.filename,
        downloadStatus: xlsxResponse.status(),
        magic: xlsxBytes.subarray(0, 4).toString("hex"),
      },
      pdfExport: {
        filename: pdfResult.filename,
        downloadStatus: pdfResponse.status(),
        magic: pdfBytes.subarray(0, 4).toString("ascii"),
      },
      focusedControlsDelegated: verificationOptions.skipFocusedControls === true,
      unexpectedRequestFailures,
      listenerFailures,
    };
  } catch (error) {
    let browserState = null;
    if (browserPage) {
      browserState = await browserPage.evaluate(() => ({
        workflowStage: state.workflowStage,
        activeSidePanel: state.activeSidePanel,
        analysisRunning: state.isAnalysisRunning,
        activeJobType: state.activeJob?.type || "",
      activeJobPhase: state.activeJob?.phase || "",
      basisSectionCount: state.quoteBasisSections.length,
      outputRowCount: state.outputRows.length,
      generationStatus: elements.resultStatus?.textContent || "",
      generating: state.isGenerating,
      preparingOutput: state.isPreparingOutput,
      outputValidation: outputRowsValid(),
      downloadDisabled: elements.sideDownloadButton?.getAttribute("aria-disabled") || "",
      downloadFilePresent: Boolean(state.downloadFile),
      downloadFileFresh: Boolean(state.downloadFile && downloadFileIsFresh(state.downloadFile)),
      downloadFileStatus: state.downloadFile?.status || "",
      })).catch(() => null);
    }
    const diagnostic = {
      lastJobPostType,
      draftPollCount,
      drainControls,
      timerFlushControls,
      draftRequestCaptured: Boolean(capturedDraft),
      uploadedRenderIncluded: Boolean(capturedDraft?.payload?.images?.some((image) => image.name === renderName && image.type === "image/png")),
      browserState,
      pageErrors,
      apiFailures,
      jobResponses,
    };
    throw new Error(`SQAG #212 browser flow failed during ${stage}: ${error?.message || error}; ${JSON.stringify(diagnostic)}`, { cause: error });
  } finally {
    if (browserPage) {
      await tracker.drain();
      await drainListenerTasks();
      await browserPage.unroute("**/api/jobs**");
      sessionId = sessionId || await currentQuoteSessionId(browserPage);
      if (sessionId) {
        const cleanup = await browserPage.evaluate(async (id) => {
          const headers = {};
          if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
          const response = await fetch(`/api/quote-sessions/${encodeURIComponent(id)}`, { method: "DELETE", headers });
          return { status: response.status, body: await response.text() };
        }, sessionId);
        if (![200, 404].includes(cleanup.status)) {
          throw new Error(`SQAG #212 quote-session cleanup failed: ${JSON.stringify(cleanup)}.`);
        }
      }
      await browserPage.evaluate(() => clearSessionState());
    }
    if (diagnosticRelay) {
      await diagnosticRelay.close().catch(() => {});
      diagnosticRelay = null;
    }
    await isolatedContext.close();
  }
}

async function runSqag212RegressionInIsolatedServer(page, downstreamServerInfo, downstreamUrl, verificationOptions = {}) {
  const syntheticRoot = path.join(os.tmpdir(), `playwright-sqag212-${process.pid}`);
  const isolatedDataRoot = syntheticRoot;
  const isolatedLogRoot = path.join(root, "_logs", "server", `playwright-smoke-sqag212-${process.pid}`);
  const previousBaseUrl = baseUrl;
  let serverInfo = null;
  let isolatedUrl = "";
  let traffic = null;
  const tracker = createQuoteSessionDrainTracker("sqag212");
  try {
    await fs.rm(syntheticRoot, { recursive: true, force: true });
    await fs.rm(isolatedLogRoot, { recursive: true, force: true });
    serverInfo = startServer({
      host: options.host,
      port: 0,
      dataRoot: isolatedDataRoot,
      syntheticRoot,
      logRoot: isolatedLogRoot,
    });
    isolatedUrl = await serverInfo.endpointPromise;
    if (!isolatedUrl || isolatedUrl === downstreamUrl) {
      throw new Error("SQAG #212 isolated server did not receive a distinct loopback endpoint.");
    }
    if (path.resolve(isolatedDataRoot) === path.resolve(process.env.QUOTE_DATA_ROOT || quoteDataRoot)) {
      throw new Error("SQAG #212 isolated data root overlaps the downstream smoke data root.");
    }
    if (
      downstreamServerInfo?.server.pid
      && serverInfo.server.pid
      && downstreamServerInfo.server.pid === serverInfo.server.pid
    ) {
      throw new Error("SQAG #212 server process is shared with the downstream smoke process.");
    }
    baseUrl = isolatedUrl;
    if (!(await waitForHealth(15000, isolatedUrl))) {
      const serverOutput = serverInfo.output.join("").trim();
      throw new Error(`Could not start the isolated SQAG #212 server.${serverOutput ? `\n\n${serverOutput}` : ""}`);
    }
    traffic = await verifySqag212AnalysisConfirmationPriceSaveReloadAndExports(page, tracker, verificationOptions);
    if (!traffic.quoteSessionPostStatuses.some((status) => status >= 200 && status < 300)) {
      throw new Error(`SQAG #212 isolated flow did not record a successful quote-session POST: ${JSON.stringify(traffic)}.`);
    }
    if (traffic.quoteSessionPostStatuses.includes(429)) {
      throw new Error(`SQAG #212 isolated flow hit the quote-session rate limit: ${JSON.stringify(traffic)}.`);
    }
  } finally {
    baseUrl = previousBaseUrl;
    try {
      await stopServer(serverInfo, { force: true });
    } finally {
      await fs.rm(syntheticRoot, { recursive: true, force: true });
      await fs.rm(isolatedLogRoot, { recursive: true, force: true });
    }
  }

  if (fsSync.existsSync(syntheticRoot) || fsSync.existsSync(isolatedLogRoot)) {
    throw new Error("SQAG #212 isolated synthetic storage was not cleaned up.");
  }
  if (await healthOk(isolatedUrl)) {
    throw new Error("SQAG #212 isolated server remained reachable after cleanup.");
  }
  return {
    isolatedServerPid: serverInfo.server.pid || null,
    downstreamServerPid: downstreamServerInfo?.server.pid || null,
    isolatedPort: new URL(isolatedUrl).port,
    downstreamPort: new URL(downstreamUrl).port,
    isolatedProcessAndDataRoot: true,
    quoteSessionPostStatuses: traffic.quoteSessionPostStatuses,
    quoteSessionResponseCount: traffic.quoteSessionResponseCount,
    quoteSessionDrain: traffic.quoteSessionDrain,
    ...(args.includes("--sqag212-g3-lifecycle-only")
      || args.includes("--sqag212-g3-controls-only")
      || verificationOptions.g3LifecycleOnly === true
      || verificationOptions.g3ControlsOnly === true
      ? { focusedG3Controls: traffic.drainControls || null }
      : {}),
    browserNegativeControl: traffic.browserNegativeControl,
    workbookExport: traffic.workbookExport || null,
    pdfExport: traffic.pdfExport || null,
    focusedControlsDelegated: traffic.focusedControlsDelegated === true,
    isolatedServerStopped: true,
    isolatedStorageCleaned: true,
  };
}

function verifyDownstreamQuoteSessionAccounting(responses, baselineCount) {
  const downstreamResponses = responses.slice(baselineCount);
  const downstreamPosts = downstreamResponses.filter((response) => response.method === "POST");
  const firstPost = downstreamPosts[0];
  if (!firstPost) {
    throw new Error("Downstream smoke fixtures did not issue a quote-session POST after the isolated SQAG #212 prelude.");
  }
  if (firstPost.status === 429) {
    throw new Error("The first downstream quote-session POST was rate limited after the isolated SQAG #212 prelude.");
  }
  const firstSuccessfulPost = downstreamPosts.find((response) => response.status >= 200 && response.status < 300);
  if (!firstSuccessfulPost) {
    throw new Error("Downstream smoke fixtures did not successfully save a quote session after the isolated SQAG #212 prelude.");
  }
  return {
    responseCountBeforePrelude: baselineCount,
    firstPostStatusAfterPrelude: firstPost.status,
    firstSuccessfulPostStatusAfterPrelude: firstSuccessfulPost.status,
    downstreamPostStatuses: downstreamPosts.map((response) => response.status),
    downstreamRateLimitedResponseCount: downstreamResponses.filter((response) => response.status === 429).length,
  };
}

async function verifyRun639PricingAuthorityRestorationAndPresentation(page) {
  const result = await page.evaluate(() => {
    const original = {
      pricingReferences: state.pricingReferences,
      pricingReferenceId: state.pricingReferenceId,
      pricingReferenceSource: state.pricingReferenceSource,
    };
    const reference = {
      id: "synthetic-exhibition-fixture-pricing",
      source: "local",
      currency: "SGD",
      digest_sha256: "sha256:2685fa5d3f208d9df578a3dbed4fc2d14fb44c0d2f5d87b9e991a1409719a9b7",
      items: [{
        id: "synthetic-floors-synthetic-carpet-tile",
        section: "Synthetic Floors",
        description: "sqm synthetic carpet tile",
        unit_hint: "sqm",
        sale_unit_price: 14.4,
      }],
    };
    const item = reference.items[0];
    try {
      state.pricingReferences = [reference];
      state.pricingReferenceId = reference.id;
      state.pricingReferenceSource = reference.source;
      const catalogContext = {
        source_basis_line_id: "",
        section: item.section,
        description: `[ ${item.description} ]`,
        unit: item.unit_hint,
        pricing_keyword: item.id,
      };
      const catalogAuthority = buildPricingAuthority("catalog", catalogContext, {
        reference,
        catalogItem: item,
        price: item.sale_unit_price,
      });
      const rawCatalog = {
        ...catalogContext,
        description: item.description,
        quantity: 2,
        pricing_reference_description: item.description,
        catalog_description: item.description,
        pricing_authority: catalogAuthority,
        unit_price_override: 999,
        effective_unit_price: 999,
        catalog_unit_price: 999,
      };
      const normalizedCatalog = normalizeOutputRow(rawCatalog);
      const restoredCatalog = normalizeRestoredPricingRow({ ...rawCatalog, unit_price_override: 999 }, normalizeOutputRow);
      if (
        normalizedCatalog.pricing_authority?.variant !== "catalog"
        || normalizedCatalog.effective_unit_price !== 14.4
        || normalizedCatalog.amount !== 28.8
        || restoredCatalog.pricing_authority?.variant !== "catalog"
        || restoredCatalog.effective_unit_price !== 14.4
        || restoredCatalog.amount !== 28.8
      ) {
        throw new Error(`Catalog authority did not survive presentation/restoration: ${JSON.stringify({ normalizedCatalog, restoredCatalog })}.`);
      }
      const staleCatalog = normalizeOutputRow({
        ...rawCatalog,
        pricing_authority: { ...catalogAuthority, catalog_item_id: "missing-item" },
      });
      if (staleCatalog.pricing_authority?.variant !== "historical" || effectiveOutputUnitPrice(staleCatalog) !== null) {
        throw new Error(`Stale catalog authority was not fail-closed: ${JSON.stringify(staleCatalog)}.`);
      }

      const manualRow = {
        source_basis_line_id: "",
        section: "Custom",
        description: "Operator-approved custom row",
        quantity: 1,
        unit: "lot",
        pricing_keyword: "",
      };
      const manualAuthority = buildPricingAuthority("manual", manualRow, { reference, price: 77 });
      for (const override of [77, 999, "0x10", [999]]) {
        const restored = normalizeRestoredPricingRow({
          ...manualRow,
          pricing_authority: manualAuthority,
          unit_price_override: override,
          effective_unit_price: override,
          pricing_basis_amount: 999,
        }, normalizeOutputRow);
        if (
          restored.pricing_authority?.variant !== "manual"
          || restored.unit_price_override !== 77
          || restored.effective_unit_price !== 77
          || restored.amount !== 77
        ) {
          throw new Error(`Manual restoration was overridden by numeric residue ${JSON.stringify(override)}: ${JSON.stringify(restored)}.`);
        }
      }
      const explicitEdit = synchronizeOwnedOutputRowPrice(
        { ...manualRow, unit_price_override: "77" },
        "77",
        { force: true },
      );
      if (explicitEdit.pricing_authority?.variant !== "manual" || explicitEdit.unit_price_override !== 77) {
        throw new Error(`Explicit manual price creation did not use the strict authority boundary: ${JSON.stringify(explicitEdit)}.`);
      }
      const rejectedEdit = synchronizeOwnedOutputRowPrice(
        { ...manualRow, unit_price_override: "0x10" },
        "0x10",
        { force: true },
      );
      if (Object.prototype.hasOwnProperty.call(rejectedEdit, "pricing_authority")) {
        throw new Error(`Invalid explicit manual price created authority: ${JSON.stringify(rejectedEdit)}.`);
      }
      const legacy = normalizeRestoredPricingRow({
        ...manualRow,
        unit_price_override: 999,
        effective_unit_price: 999,
        pricing_basis_amount: 999,
      }, normalizeOutputRow);
      if (
        legacy.pricing_authority?.variant !== "historical"
        || effectiveOutputUnitPrice(legacy) !== null
        || legacy.amount !== ""
      ) {
        throw new Error(`Legacy numeric residue was trusted during restoration: ${JSON.stringify(legacy)}.`);
      }
      const includedAuthority = buildPricingAuthority("included", { ...manualRow, price_mode: "Included" }, { reference });
      const included = normalizeRestoredPricingRow({
        ...manualRow,
        price_mode: "Included",
        display_price: "Included",
        pricing_authority: includedAuthority,
        unit_price_override: 999,
        effective_unit_price: 999,
      }, normalizeOutputRow);
      if (
        included.pricing_authority?.variant !== "included"
        || included.amount !== 0
        || included.unit_price_override !== ""
        || Object.prototype.hasOwnProperty.call(included, "effective_unit_price")
      ) {
        throw new Error(`Included restoration was not preserved: ${JSON.stringify(included)}.`);
      }
      if (
        pricingAuthorityVersionIsValid(true)
        || pricingAuthorityNumber("1٢") !== null
        || pricingAuthorityNumber("77%") !== null
        || canonicalPricingAuthorityText("a\u001cb") !== "a b"
        || canonicalPricingAuthorityText("a\ufeffb") !== "a b"
        || canonicalPricingAuthorityText("e\u0301  item") !== "é item"
      ) {
        throw new Error("Browser authority schema, decimal, or whitespace contract diverged.");
      }
      return { catalogPrice: normalizedCatalog.effective_unit_price, catalogAmount: normalizedCatalog.amount };
    } finally {
      state.pricingReferences = original.pricingReferences;
      state.pricingReferenceId = original.pricingReferenceId;
      state.pricingReferenceSource = original.pricingReferenceSource;
    }
  });
  if (result.catalogPrice !== 14.4 || result.catalogAmount !== 28.8) {
    throw new Error(`Run-639 browser authority smoke returned an unexpected result: ${JSON.stringify(result)}.`);
  }
}

async function verifyRecoveredTemplateOwnerFailsClosed(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
  const result = await page.evaluate(() => {
    const original = {
      profiles: structuredClone(state.profiles),
      companyProfiles: structuredClone(state.companyProfiles),
      profileId: state.profileId,
      defaultProfileId: state.defaultProfileId,
      quoteCommercialLifecycle: state.quoteCommercialLifecycle,
      selectedPresetValue: state.selectedPresetValue,
      presetSelectValue: elements.presetSelect.value,
      lastSelection: window.localStorage.getItem(LAST_SELECTION_STORAGE_KEY),
    };
    const ownerBProfiles = [{
      id: "owner-b",
      label: "Owner B",
      default_quote_detail_preset: "shared",
      quote_detail_presets: [{ id: "shared", name: "Owner B Shared", details: {} }],
    }];
    try {
      state.profiles = ownerBProfiles;
      state.companyProfiles = [];
      state.profileId = "owner-a";
      state.defaultProfileId = "owner-a";
      state.quoteCommercialLifecycle = "RECOVERED";
      state.selectedPresetValue = "profile:owner-a:shared";
      elements.presetSelect.value = "profile:owner-b:shared";
      window.localStorage.setItem(LAST_SELECTION_STORAGE_KEY, JSON.stringify({
        browserRecoveryScope: currentBrowserRecoveryScope(),
        presetValue: "profile:owner-b:shared",
      }));

      renderPresetOptions();
      const afterRender = {
        selected: state.selectedPresetValue,
        domValue: elements.presetSelect.value,
      };
      loadDefaultProfilePreset();
      const afterDefault = {
        selected: state.selectedPresetValue,
        domValue: elements.presetSelect.value,
      };
      loadConfiguredProfilePreset();
      const afterConfigured = {
        selected: state.selectedPresetValue,
        domValue: elements.presetSelect.value,
      };
      let generationPayload = null;
      let generationPayloadError = "";
      try {
        generationPayload = buildPayload({ includeDraftContext: false });
      } catch (error) {
        generationPayloadError = String(error?.message || error);
      }
      const recoveredGenerationProfileId = generationProfileIdForPayload();
      const recoveredSessionProfileId = currentQuoteSessionPayload().quote_company_profile?.id || "";
      const recoveredSnapshot = buildSessionSnapshot();

      window.localStorage.removeItem(LAST_SELECTION_STORAGE_KEY);
      state.quoteCommercialLifecycle = "NEW_UNINITIALISED";
      state.selectedPresetValue = "";
      elements.presetSelect.value = "";
      state.profileId = "owner-b";
      state.defaultProfileId = "owner-b";
      renderPresetOptions();
      loadDefaultProfilePreset({ preferLastSelection: false });
      const normalNewQuote = {
        selected: state.selectedPresetValue,
        domValue: elements.presetSelect.value,
        generationProfileId: generationProfileIdForPayload(),
      };
      return {
        afterRender,
        afterDefault,
        afterConfigured,
        generationProfileId: recoveredGenerationProfileId,
        generationPayloadId: generationPayload?.profile_id || "",
        recoveredSessionProfileId,
        generationPayloadError,
        recoveredSnapshotSelected: recoveredSnapshot.selectedPresetValue,
        normalNewQuote,
      };
    } finally {
      state.profiles = original.profiles;
      state.companyProfiles = original.companyProfiles;
      state.profileId = original.profileId;
      state.defaultProfileId = original.defaultProfileId;
      state.quoteCommercialLifecycle = original.quoteCommercialLifecycle;
      state.selectedPresetValue = original.selectedPresetValue;
      elements.presetSelect.value = original.presetSelectValue;
      if (original.lastSelection === null) window.localStorage.removeItem(LAST_SELECTION_STORAGE_KEY);
      else window.localStorage.setItem(LAST_SELECTION_STORAGE_KEY, original.lastSelection);
    }
  });
  for (const phase of ["afterRender", "afterDefault", "afterConfigured"]) {
    if (result[phase].selected !== "profile:owner-a:shared" || result[phase].domValue !== "") {
      throw new Error(`Recovered template owner changed during ${phase}: ${JSON.stringify(result)}.`);
    }
  }
  if (result.generationProfileId || result.generationPayloadId || result.recoveredSessionProfileId || result.generationPayloadError) {
    throw new Error(`Recovered missing template owner was not fail-closed for generation: ${JSON.stringify(result)}.`);
  }
  if (result.recoveredSnapshotSelected !== "profile:owner-a:shared") {
    throw new Error(`Recovered template owner identity was not preserved in the session snapshot: ${JSON.stringify(result)}.`);
  }
  if (
    result.normalNewQuote.selected !== "profile:owner-b:shared"
    || result.normalNewQuote.domValue !== "profile:owner-b:shared"
    || result.normalNewQuote.generationProfileId !== "profile:owner-b"
  ) {
    throw new Error(`Normal new-quote profile selection did not remain functional: ${JSON.stringify(result)}.`);
  }
}

async function currentQuoteSessionId(page) {
  return page.evaluate(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return String(saved.quoteSessionId || "");
    } catch {
      return "";
    }
  });
}

async function verifyBrowserRecoveryScopeIsolation(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
  const seeded = await page.evaluate(async () => {
    clearQuoteSessionDraftSaveTimer();
    state.isBooting = true;
    state.activeAppView = "quote";
    state.quoteSessionId = "quote-workspace-a-private";
    state.quoteSessionDraftSaveStarted = true;
    elements.clientName.value = "Workspace A Private Customer";
    state.images = [{
      name: "workspace-a-private.png",
      type: "image/png",
      size: 1,
      session_file_key: "workspace-a-private-file",
      data_url: "data:image/png;base64,AA==",
    }];
    const snapshot = buildSessionSnapshot();
    snapshot.browserRecoveryScope = "workspace-a-recovery-scope";
    window.localStorage.setItem("swooshz_quote_session_v1", JSON.stringify(snapshot));
    const db = await openSessionFileDb();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readwrite");
      transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).put({
        name: "workspace-a-private.png",
        type: "image/png",
        size: 1,
        session_file_key: "workspace-a-private-file",
        file_role: "reference",
        data_url: "data:image/png;base64,AA==",
        browserRecoveryScope: "workspace-a-recovery-scope",
      });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error || new Error("Could not seed foreign session file"));
      transaction.onabort = () => reject(transaction.error || new Error("Foreign session file seeding aborted"));
    });
    return {
      currentScope: state.browserRecoveryScope || "",
      seededScope: snapshot.browserRecoveryScope,
    };
  });
  if (!seeded.currentScope || seeded.currentScope === seeded.seededScope) {
    throw new Error(`Browser recovery test requires distinct current and seeded scopes, found ${JSON.stringify(seeded)}.`);
  }

  const recoveryPage = await page.context().newPage();
  const recoveryProblems = [];
  recoveryPage.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) recoveryProblems.push(`${message.type()}: ${message.text()}`);
  });
  recoveryPage.on("pageerror", (error) => recoveryProblems.push(`pageerror: ${error.message}`));
  recoveryPage.on("response", (response) => {
    if (response.status() >= 400) recoveryProblems.push(`${response.status()} ${response.url()}`);
  });
  let result;
  try {
    await recoveryPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await recoveryPage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await recoveryPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    result = await recoveryPage.evaluate(async () => {
      const db = await openSessionFileDb();
      const indexedDbCount = await new Promise((resolve, reject) => {
        const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readonly");
        const request = transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).count();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not count session files"));
      });
      return {
        currentScope: state.browserRecoveryScope || "",
        clientName: elements.clientName.value,
        quoteSessionId: state.quoteSessionId,
        storedSnapshot: window.localStorage.getItem("swooshz_quote_session_v1"),
        indexedDbCount,
      };
    });
  } finally {
    await recoveryPage.close();
  }
  if (result.currentScope !== seeded.currentScope) {
    throw new Error(`Recovery page booted with an unexpected scope: ${JSON.stringify({ seeded, result })}.`);
  }
  if (recoveryProblems.length) {
    throw new Error(`Recovery page reported failures: ${JSON.stringify(recoveryProblems)}.`);
  }
  if (result.clientName.includes("Workspace A") || result.quoteSessionId === "quote-workspace-a-private") {
    throw new Error(`Mismatched browser recovery state crossed scope: ${JSON.stringify(result)}.`);
  }
  if (result.storedSnapshot !== null || result.indexedDbCount !== 0) {
    throw new Error(`Mismatched browser recovery state was not purged: ${JSON.stringify(result)}.`);
  }
}

async function verifyStaleTabMutationIsRejected(page) {
  const stalePage = await page.context().newPage();
  const unexpectedProblems = [];
  stalePage.on("console", (message) => {
    const text = message.text();
    const expectedRejection = message.type() === "error"
      && text.includes("Failed to load resource")
      && text.includes("403");
    if (["error", "warning"].includes(message.type()) && !expectedRejection) unexpectedProblems.push(message.type() + ": " + text);
  });
  stalePage.on("pageerror", (error) => unexpectedProblems.push("pageerror: " + error.message));
  stalePage.on("response", (response) => {
    const expectedRejection = response.status() === 403 && response.url().endsWith("/api/quote-sessions");
    if (response.status() >= 400 && !expectedRejection) {
      unexpectedProblems.push(response.status() + " " + response.url());
    }
  });

  try {
    await stalePage.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await stalePage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await stalePage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    const initialSession = await stalePage.evaluate(async () => {
      const response = await fetch("/api/session");
      return response.json();
    });
    if (!initialSession.browser_recovery_scope || !initialSession.csrf_token) {
      throw new Error("Stale-tab regression requires a complete initial browser session.");
    }

    const nextScope = initialSession.browser_recovery_scope + "-next-workspace";
    const nextSession = {
      ...initialSession,
      browser_recovery_scope: nextScope,
      csrf_token: "next-workspace-csrf-token-0123456789abcdef",
    };
    const interceptedPosts = [];
    await stalePage.route("**/api/quote-sessions", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      interceptedPosts.push({
        headers: route.request().headers(),
        payload: JSON.parse(route.request().postData() || "{}"),
      });
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          status: "blocked",
          errors: ["Missing or invalid local session token."],
        }),
      });
    });
    await stalePage.route("**/api/session", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(nextSession),
      });
    });

    const staleSessionId = "quote-stale-workspace-a-private";
    const navigation = stalePage.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 15000 });
    await stalePage.evaluate((sessionId) => {
      clearQuoteSessionDraftSaveTimer();
      state.activeAppView = "quote";
      state.quoteSessionId = sessionId;
      state.quoteSessionDraftSaveStarted = true;
      elements.clientName.value = "Workspace A Private Customer";
      saveSessionState();
      void saveQuoteSessionDraftState({ quoteGenerated: false }).catch(() => {});
    }, staleSessionId);
    await navigation;
    await stalePage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await stalePage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });

    const result = await stalePage.evaluate(async () => {
      const db = await openSessionFileDb();
      const indexedDbCount = await new Promise((resolve, reject) => {
        const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readonly");
        const request = transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).count();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not count stale-tab session files"));
      });
      return {
        currentScope: state.browserRecoveryScope,
        isTransitioning: state.isRecoveryScopeTransitioning,
        quoteSessionId: state.quoteSessionId,
        storedSnapshot: window.localStorage.getItem("swooshz_quote_session_v1"),
        indexedDbCount,
      };
    });

    if (interceptedPosts.length !== 1) {
      throw new Error("Stale tab must issue exactly one rejected mutation: " + JSON.stringify(interceptedPosts));
    }
    const stalePost = interceptedPosts[0];
    if (stalePost.payload.session_id !== staleSessionId) {
      throw new Error("Stale-tab regression did not exercise the intended private draft: " + JSON.stringify(stalePost.payload));
    }
    const csrfHeader = String(initialSession.csrf_header || "").toLowerCase();
    if (!csrfHeader || stalePost.headers[csrfHeader] !== initialSession.csrf_token) {
      throw new Error("Stale-tab mutation did not carry the original session-bound CSRF token.");
    }
    if (
      result.currentScope !== nextScope
      || result.isTransitioning
      || result.quoteSessionId === staleSessionId
      || result.storedSnapshot !== null
      || result.indexedDbCount !== 0
    ) {
      throw new Error("Stale-tab scope transition did not purge before reload: " + JSON.stringify(result));
    }
    if (unexpectedProblems.length) {
      throw new Error("Stale-tab regression reported unexpected failures: " + JSON.stringify(unexpectedProblems));
    }
  } finally {
    await stalePage.close();
  }
}

async function dashboardQuoteSessionDetail(page, sessionId) {
  return page.evaluate(async (safeSessionId) => {
    const response = await fetch(`/api/quote-sessions/${encodeURIComponent(safeSessionId)}`);
    const body = await response.json();
    return { ...body, status: response.ok ? "ok" : "error" };
  }, sessionId);
}

async function createDashboardSmokeSession(page, suffix, options = {}) {
  return page.evaluate(async ({ suffix, sessionIdPrefix, customerName, projectName }) => {
    const sessionResponse = await fetch("/api/session");
    if (!sessionResponse.ok) throw new Error(`Session bootstrap failed: ${sessionResponse.status}`);
    const session = await sessionResponse.json();
    if (!await applySessionData(session)) throw new Error("Session bootstrap state could not be applied.");

    clearQuoteSessionDraftSaveTimer();
    state.quoteSessionId = "";
    state.lastGenerationRunId = "";
    state.lastGenerationRunSessionId = "";
    state.quoteSessionDraftSaveStarted = false;
    state.quoteSessionRestoredSessionId = "";
    state.quoteSessionRestoredDraftKey = "";
    state.quoteCommercialLifecycle = "NEW_UNINITIALISED";
    state.quoteCommercialSnapshot = null;
    state.quoteCommercialReview = null;
    state.quoteCommercialPreservedQuoteText = {};
    state.quoteCommercialRecoveryError = "";
    state.pricingReferenceSelectionIntent = null;
    state.images = [];
    state.headerLogo = null;
    state.quoteBasis = {};
    state.quoteBasisSections = [];
    state.lineItems = [];
    state.outputRows = [];
    state.originalOutputRows = [];
    state.outputErrors = [];
    state.analysisFindings = [];
    state.blockingClarificationQuestions = [];
    state.originalAnalysisSnapshot = null;
    state.basisConfirmed = false;
    state.aiFailed = false;
    state.draftSource = "";
    state.activeJob = null;
    state.downloadFile = null;
    state.pdfFile = null;
    state.outputRevision = 0;
    state.downloadFileRevision = -1;
    state.pdfFileRevision = -1;
    resetQuoteCommercialTouched();

    const generatorProfileId = "default";
    if (!await loadProfiles()) throw new Error("Synthetic fixture profiles could not be loaded.");
    const profile = state.profiles.find((item) => item.id === "synthetic-exhibition-fixture-template");
    const reference = state.pricingReferences.find((item) => (
      item.id === "synthetic-exhibition-fixture-pricing" && item.source === "local"
    ));
    if (!profile || !reference) throw new Error("Synthetic fixture profile or pricing reference is unavailable.");

    state.profileId = profile.id;
    state.pricingReferenceId = reference.id;
    state.pricingReferenceSource = reference.source;
    renderProfileOptions();
    renderPresetOptions();
    if (!selectPricingReferenceOptionValue(pricingReferenceSelectValue(reference))) {
      throw new Error("Synthetic fixture pricing reference could not be selected.");
    }
    const presetValue = profilePresetOptionValue(profile.id, "synthetic-fixture-default");
    if (!selectPresetValue(presetValue)) throw new Error("Synthetic fixture quote-company preset could not be selected.");
    loadSelectedPreset({ silent: true, allowOwnedInitialization: true });
    state.profileId = generatorProfileId;
    state.selectedPresetValue = profilePresetOptionValue(generatorProfileId, "default");

    const safeSuffix = String(suffix || "session").replace(/[^A-Za-z0-9_-]/g, "-");
    const safePrefix = String(sessionIdPrefix || `quote-playwright-bulk-${safeSuffix}`).replace(/[^A-Za-z0-9_-]/g, "-");
    const customerOverride = customerName === undefined ? "Marina Bay Product Launch" : String(customerName).trim();
    const projectOverride = projectName === undefined ? "Orchard Road Pop-up Booth" : String(projectName).trim();
    const customer = customerOverride || "Untitled customer";
    const project = projectOverride || "Untitled quote";
    state.quoteCommercialLifecycle = "EXISTING";
    resetQuoteCommercialFieldsToSelectedPricingReference({ markOwned: true });
    applyQuoteDetails({
      quote_date: new Date().toISOString().slice(0, 10),
      project_number: `SMOKE-${safeSuffix.toUpperCase()}`,
      client: {
        name: customer,
        attention: "Synthetic Contact",
        title: "Synthetic Manager",
        address: "1 Synthetic Way\nSingapore 000001",
      },
      project: {
        title: project,
        show_name: "Synthetic Exhibition Fixture",
        booth_width: "6",
        booth_depth: "6",
        booth_size: "6m x 6m",
        dimension_source: "analysis",
      },
    }, { partial: true });
    if (state.headerLogo?.data_url) state.headerLogo = await ensureContentFingerprint(state.headerLogo);
    state.images = [await ensureContentFingerprint({
      name: "test-workspace-reference.pdf",
      type: "application/pdf",
      size: 24,
      data_url: "data:application/pdf;base64,JVBERi0xLjQKJVRlc3QK",
    })];
    state.quoteBasisSections = normalizeQuoteBasisSections([{
      id: "smoke-floor",
      title: "Floor Design",
      lines: [{
        tag: "Include",
        text: "Needle punch carpet in colour",
        include: true,
        quantity: 2,
        unit: "sqm",
        pricing_keyword: "synthetic-floor-needle-punch-carpet",
      }],
    }]);
    state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
    const smokeLineItem = normalizeLineItem({
      section: "Floor Design",
      description: "Needle punch carpet in colour",
      quantity: 2,
      unit: "sqm",
      pricing_keyword: "synthetic-floor-needle-punch-carpet",
      price_mode: "Priced",
      unit_price_override: 15,
      catalog_unit_price: 15,
    });
    state.lineItems = [synchronizeOwnedOutputRowPrice(smokeLineItem, 15, { force: true })];
    refreshOutputRowsFromLineItems();
    state.originalOutputRows = snapshotOutputRows(state.outputRows);
    state.basisConfirmed = true;
    state.activeSidePanel = "basis";
    state.workflowStage = "basis_review";
    state.quoteSessionDraftSaveStarted = true;

    const expectedPricing = {
      currency: "SGD",
      source: "local",
      id: "synthetic-exhibition-fixture-pricing",
      digest: "sha256:2685fa5d3f208d9df578a3dbed4fc2d14fb44c0d2f5d87b9e991a1409719a9b7",
    };
    const referenceBasis = pricingReferenceAuthorityBasis(reference);
    if (!referenceBasis || Object.keys(expectedPricing).some((key) => referenceBasis[key] !== expectedPricing[key])) {
      throw new Error("Synthetic fixture pricing authority does not match the required identity.");
    }
    const detailsBeforeInitialization = collectQuoteDetails();
    const initializedSnapshot = quoteCommercialSnapshotForDetails(detailsBeforeInitialization, {
      lifecycle: "EXISTING",
      origin: "explicit_initialization",
      reference,
      replacePricingAuthority: true,
    });
    if (!initializedSnapshot) throw new Error("Synthetic fixture commercial snapshot could not be initialized.");
    state.quoteCommercialSnapshot = initializedSnapshot;
    state.quoteCommercialReview = null;
    const assertFixturePayload = (payload, label, expectedGenerated, isGenerationPayload = false) => {
      const sessionPayload = payload?.quote_session || payload;
      const draftState = sessionPayload?.draft_state;
      const details = draftState?.quoteDetails;
      const snapshot = details?.commercial_snapshot;
      const sessionPricing = sessionPayload?.pricing_reference || {};
      const summary = sessionPayload?.customer_summary || {};
      const normalizedSnapshot = normalizeQuoteCommercialSnapshot(snapshot, "EXISTING", details);
      const customerValues = [summary.customer_name, details?.client?.name];
      const projectValues = [summary.project_name, details?.project?.title];
      if (isGenerationPayload) {
        customerValues.push(payload?.client?.name);
        projectValues.push(payload?.project?.title);
      }
      const pricingBasis = normalizedSnapshot?.pricing_basis || {};
      const topPricing = payload?.pricing_reference || {};
      if (
        sessionPayload?.status?.quote_generated !== expectedGenerated
        || draftState?.quoteCommercialLifecycle !== "EXISTING"
        || draftState?.quoteCommercialReview !== null && draftState?.quoteCommercialReview !== undefined
        || !normalizedSnapshot
        || snapshot.origin !== "explicit_initialization"
        || sessionPricing.id !== expectedPricing.id
        || sessionPricing.source !== expectedPricing.source
        || draftState?.pricingReferenceId !== expectedPricing.id
        || draftState?.pricingReferenceSource !== expectedPricing.source
        || pricingBasis.currency !== expectedPricing.currency
        || pricingBasis.source !== expectedPricing.source
        || pricingBasis.id !== expectedPricing.id
        || pricingBasis.digest !== expectedPricing.digest
        || customerValues.some((value) => value !== customer)
        || projectValues.some((value) => value !== project)
      ) {
        throw new Error(`${label} does not preserve the canonical commercial, pricing, customer, or project contract.`);
      }
      if (isGenerationPayload && (
        payload.pricing_reference_id !== expectedPricing.id
        || payload.pricing_reference_source !== expectedPricing.source
        || topPricing.id !== expectedPricing.id
        || topPricing.source !== expectedPricing.source
        || topPricing.currency !== expectedPricing.currency
        || topPricing.digest_sha256 !== expectedPricing.digest
      )) {
        throw new Error(`${label} does not carry the exact pricing authority identity.`);
      }
    };

    const candidateSessionId = safeQuoteSessionId(`${safePrefix}-${Date.now()}`);
    const sessionId = candidateSessionId || newClientQuoteSessionId();
    state.quoteSessionId = sessionId;
    const draftFiles = sessionFileRecordsFromDraft();
    const initialDraftState = currentQuoteSessionDraftState();
    const initialPayload = currentQuoteSessionPayload({
      sessionId,
      quoteGenerated: false,
      includeDraftState: true,
      includeDraftFiles: true,
      draftState: initialDraftState,
      draftFiles,
    });
    if (initialPayload.status?.quote_generated !== false) {
      throw new Error("Synthetic fixture initial session must begin with quote_generated=false.");
    }
    assertFixturePayload(initialPayload, "Initial synthetic fixture session", false);
    const initialSaved = await saveCurrentQuoteSession({
      sessionId,
      quoteGenerated: false,
      includeDraftState: true,
      includeDraftFiles: true,
      draftState: initialDraftState,
      draftFiles,
    });
    if (
      !initialSaved
      || initialSaved.session_id !== sessionId
      || initialSaved.status?.quote_generated !== false
    ) {
      throw new Error("Synthetic fixture initial non-generated quote session was not persisted.");
    }

    const generationPayload = buildPayload({ viewPdf: false });
    assertFixturePayload(generationPayload, "Canonical synthetic generation payload", false, true);
    const generationJobId = newClientJobId();
    const started = await startJob("generate", generationPayload, { jobId: generationJobId });
    if (!started.ok) throw new Error("Canonical synthetic generation job was rejected before execution.");
    const acceptedJobId = String(started.data?.job_id || generationJobId).trim();
    const polled = await pollJob(acceptedJobId);
    if (
      !polled.ok
      || polled.data?.status !== "completed"
      || polled.data?.result?.status !== "completed"
    ) {
      throw new Error(`Canonical synthetic generation did not reach terminal success: ${polled.data?.status || "unknown"}.`);
    }
    const resultSessionId = safeQuoteSessionId(polled.data.result.quote_session?.session_id || "");
    if (resultSessionId && resultSessionId !== sessionId) {
      throw new Error("Canonical synthetic generation returned a different quote session.");
    }

    const detailResponse = await fetch(`/api/quote-sessions/${encodeURIComponent(sessionId)}`);
    const detailData = await detailResponse.json().catch(() => ({}));
    if (!detailResponse.ok) throw new Error("Generated synthetic quote session could not be re-read.");
    const persisted = detailData.quote_session || {};
    assertFixturePayload({ quote_session: persisted }, "Persisted generated synthetic session", true);
    const xlsx = persisted.exports?.xlsx || {};
    if (
      xlsx.filename !== "quotation.xlsx"
      || xlsx.exists !== true
      || xlsx.missing === true
      || xlsx.stale === true
      || !String(xlsx.url || "").trim()
    ) {
      throw new Error("Persisted generated synthetic session does not expose a current quotation.xlsx artifact.");
    }
    const pdf = persisted.exports?.pdf || {};
    if (pdf.exists === true || pdf.filename === "quotation.pdf") {
      throw new Error("Synthetic dashboard fixture unexpectedly generated a PDF.");
    }
    return sessionId;
  }, {
    suffix,
    sessionIdPrefix: options.sessionIdPrefix || "",
    customerName: Object.prototype.hasOwnProperty.call(options, "customerName") ? options.customerName : undefined,
    projectName: Object.prototype.hasOwnProperty.call(options, "projectName") ? options.projectName : undefined,
  });
}

async function verifyConcurrentInitialDraftSaveUsesSingleSession(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
  if (await emptyNewQuoteButton.isVisible()) {
    await emptyNewQuoteButton.click();
  } else {
    await page.locator("#newQuoteButton:not([disabled])").click();
  }
  await seedQuoteDraftFromTestFixture(page);
  const raceResult = await page.evaluate(async () => {
    setSidePanel("customer", { force: true });
    state.quoteSessionId = "";
    state.quoteSessionDraftSaveStarted = true;
    saveSessionState();
    const beforeData = await fetch("/api/quote-sessions").then((response) => response.json());
    const beforeIds = new Set((beforeData.quote_sessions || []).map((session) => session.session_id));
    const responses = await Promise.all([
      saveQuoteSessionDraftState({ quoteGenerated: false }),
      saveQuoteSessionDraftState({ quoteGenerated: false }),
    ]);
    const afterData = await fetch("/api/quote-sessions").then((response) => response.json());
    const afterIds = (afterData.quote_sessions || []).map((session) => session.session_id);
    const createdIds = afterIds.filter((sessionId) => !beforeIds.has(sessionId));
    await Promise.all(createdIds.map((sessionId) => deleteQuoteSessionRecord(sessionId)));
    window.localStorage.removeItem("swooshz_quote_session_v1");
    return {
      createdIds,
      responseIds: responses.map((session) => session?.session_id || ""),
      stateSessionId: state.quoteSessionId,
    };
  });
  const uniqueCreatedIds = new Set(raceResult.createdIds.filter(Boolean));
  const uniqueResponseIds = new Set(raceResult.responseIds.filter(Boolean));
  if (raceResult.createdIds.length !== 1 || uniqueCreatedIds.size !== 1 || uniqueResponseIds.size !== 1) {
    throw new Error(`Concurrent initial draft saves should create one session, found ${JSON.stringify(raceResult)}.`);
  }
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
}

async function verifyInitialDraftSaveReservesSessionIdBeforeNetwork(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
  if (await emptyNewQuoteButton.isVisible()) {
    await emptyNewQuoteButton.click();
  } else {
    await page.locator("#newQuoteButton:not([disabled])").click();
  }
  await seedQuoteDraftFromTestFixture(page);
  const reservation = await page.evaluate(async () => {
    setSidePanel("customer", { force: true });
    state.quoteSessionId = "";
    state.quoteSessionDraftSaveStarted = false;
    saveSessionState();
    const savePromise = startQuoteSessionDraftSaveAfterCustomerStep();
    const savedDuringStart = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
    const reservedSessionId = String(savedDuringStart.quoteSessionId || "");
    const stateSessionIdDuringStart = String(state.quoteSessionId || "");
    const session = await savePromise;
    const createdSessionId = session?.session_id || state.quoteSessionId || "";
    await deleteQuoteSessionRecord(createdSessionId);
    window.localStorage.removeItem("swooshz_quote_session_v1");
    return {
      reservedSessionId,
      stateSessionIdDuringStart,
      createdSessionId,
    };
  });
  if (!reservation.reservedSessionId || !reservation.stateSessionIdDuringStart) {
    throw new Error(`Initial draft save should reserve a client session id before the network response, found ${JSON.stringify(reservation)}.`);
  }
  if (reservation.createdSessionId !== reservation.reservedSessionId) {
    throw new Error(`Initial draft save should use the reserved session id, found ${JSON.stringify(reservation)}.`);
  }
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
}

async function verifyDashboardNewQuoteDoesNotSaveHiddenDraft(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
  if (await emptyNewQuoteButton.isVisible()) {
    await emptyNewQuoteButton.click();
  } else {
    await page.locator("#newQuoteButton:not([disabled])").click();
  }
  await seedQuoteDraftFromTestFixture(page);
  const beforeIds = await page.evaluate(async () => {
    const beforeData = await fetch("/api/quote-sessions").then((response) => response.json());
    const ids = (beforeData.quote_sessions || []).map((session) => session.session_id);
    setSidePanel("customer", { force: true });
    state.quoteSessionId = "";
    state.quoteSessionDraftSaveStarted = true;
    saveSessionState();
    showDashboard({ load: false });
    return ids;
  });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#newQuoteButton:not([disabled])").click();
  await seedQuoteDraftFromTestFixture(page);
  await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
  await page.waitForFunction(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return Boolean(saved.quoteSessionId);
    } catch {
      return false;
    }
  }, null, { timeout: 15000 });
  await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).click();
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  const result = await page.evaluate(async (beforeIds) => {
    const beforeSet = new Set(beforeIds);
    const afterData = await fetch("/api/quote-sessions").then((response) => response.json());
    const afterIds = (afterData.quote_sessions || []).map((session) => session.session_id);
    const createdIds = afterIds.filter((sessionId) => !beforeSet.has(sessionId));
    await Promise.all(createdIds.map((sessionId) => deleteQuoteSessionRecord(sessionId)));
    window.localStorage.removeItem("swooshz_quote_session_v1");
    return { createdIds, afterIds };
  }, beforeIds);
  if (result.createdIds.length !== 1) {
    throw new Error(`Dashboard New Quote should not save a hidden stale draft, found ${JSON.stringify(result)}.`);
  }
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
}

async function verifyDashboardClearsStaleSessionsBeforeRefresh(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const staleCount = await page.evaluate(() => {
    const staleSession = (suffix) => ({
      session_id: `quote-stale-${suffix}`,
      created_at: "2026-06-23T00:00:00Z",
      updated_at: "2026-06-23T00:00:00Z",
      customer_summary: { customer_name: "Stale Customer", project_name: `Stale Project ${suffix}` },
      status: { quote_generated: false, xlsx_exported: false, pdf_exported: false },
      exports: {},
      has_draft_state: true,
    });
    state.quoteSessionLoadError = "";
    state.dashboardStatusFilter = "all";
    state.dashboardSearch = "";
    state.dashboardPageIndex = 0;
    if (elements.dashboardStatusFilter) elements.dashboardStatusFilter.value = "all";
    if (elements.dashboardSearchInput) elements.dashboardSearchInput.value = "";
    state.quoteSessions = [staleSession("a"), staleSession("b")];
    state.dashboardActiveSessionId = "quote-stale-a";
    state.dashboardSelectedSessionIds = [];
    renderQuoteDashboard();
    return document.querySelectorAll(".dashboard-session-card").length;
  });
  if (staleCount !== 2) {
    throw new Error(`Expected two injected stale dashboard rows before refresh, found ${staleCount}.`);
  }
  let releaseDashboardFetch;
  const releaseFetch = new Promise((resolve) => {
    releaseDashboardFetch = resolve;
  });
  let resolveStarted;
  const started = new Promise((resolve) => {
    resolveStarted = resolve;
  });
  await page.route("**/api/quote-sessions", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    resolveStarted();
    await releaseFetch;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ quote_sessions: [] }),
    });
  });
  await page.evaluate(() => showDashboard());
  await started;
  await page.waitForTimeout(100);
  const staleCountDuringRefresh = await page.locator(".dashboard-session-card").count();
  releaseDashboardFetch();
  await page.waitForFunction(() => {
    const emptyState = document.querySelector("#dashboardEmptyState");
    return emptyState && !emptyState.hidden;
  }, null, { timeout: 15000 });
  await page.unroute("**/api/quote-sessions");
  if (staleCountDuringRefresh !== 0) {
    throw new Error(`Dashboard should clear stale rows before refreshed sessions load, found ${staleCountDuringRefresh}.`);
  }
}

async function startSyntheticDeepSeekProvider() {
  const requests = [];
  const replacements = new Map([
    ["make the selected panel blue", "Selected panel painted blue"],
    ["update this panel so it has a satin navy finish", "Selected panel updated so it has a satin navy finish"],
    ["revise the selected line so the panel is coated in cobalt blue", "Revised selected line so the panel is coated in cobalt blue"],
  ]);
  const server = createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      try {
        const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        const prompt = (payload.messages || []).map((message) => String(message?.content || "")).join("\n");
        const editRequest = [...replacements.keys()].find((candidate) => prompt.includes(candidate));
        if (request.method !== "POST" || request.url !== "/chat/completions" || !editRequest) {
          response.writeHead(400, { "content-type": "application/json" });
          response.end(JSON.stringify({ error: { message: "Unexpected synthetic provider request." } }));
          return;
        }
        requests.push({ editRequest, model: String(payload.model || "") });
        const content = JSON.stringify({
          intent: "proposal",
          proposal: {
            message: `Synthetic server-backed proposal for: ${editRequest}`,
            replacement_line: { text: replacements.get(editRequest), confidence: 93 },
            quote_basis_sections: [{ id: "provider-rewrite", lines: [{ text: "must be ignored" }] }],
            line_items: [{ description: "provider rewrite must be ignored" }],
          },
        });
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({
          id: `synthetic-${requests.length}`,
          choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
        }));
      } catch (error) {
        response.writeHead(500, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: { message: String(error?.message || error) } }));
      }
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Synthetic DeepSeek provider did not bind to loopback.");
  return {
    endpoint: `http://127.0.0.1:${address.port}`,
    requests,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}

function isolatedLoadedAppEnvironment(runRoot, overrides = {}) {
  const inheritedNames = process.platform === "win32"
    ? ["PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PLAYWRIGHT_BROWSERS_PATH"]
    : ["PATH", "PLAYWRIGHT_BROWSERS_PATH"];
  const env = Object.fromEntries(inheritedNames.filter((name) => process.env[name]).map((name) => [name, process.env[name]]));
  return {
    ...env,
    PYTHONUNBUFFERED: "1",
    APP_MODE: "local",
    AUTH_MODE: "local",
    USER_TYPE: "admin",
    SQAG_DISABLE_DOTENV: "true",
    SQAG_STORAGE_MODE: "local",
    SQAG_ARTIFACT_STORAGE_MODE: "local",
    AI_PROVIDER: "none",
    AI_BASIS_LINE_PROVIDER: "none",
    AI_BASIS_ANSWER_PROVIDER: "none",
    AI_PRICING_IMPORT_PROVIDER: "none",
    OPENAI_API_KEY: "",
    DEEPSEEK_API_KEY: "",
    QUOTE_DATA_ROOT: path.join(runRoot, "quote-data"),
    QUOTE_OUTPUT_ROOT: path.join(runRoot, "output"),
    QUOTE_TMP_ROOT: path.join(runRoot, "tmp"),
    SQAG_LOCAL_PRICING_REFERENCES_ROOT: path.join(runRoot, "pricing"),
    QUOTE_LOG_ROOT: path.join(runRoot, "server-log"),
    TEMP: path.join(runRoot, "process-temp"),
    TMP: path.join(runRoot, "process-temp"),
    ...overrides,
  };
}

async function startIsolatedLoadedAppServer(runRoot, environmentOverrides = {}) {
  const env = isolatedLoadedAppEnvironment(runRoot, environmentOverrides);
  await Promise.all([
    fs.mkdir(env.TEMP, { recursive: true }),
    fs.mkdir(path.join(runRoot, "browser-log"), { recursive: true }),
  ]);
  const child = spawn(pythonCommand(), ["webapp/server.py", "--host", "127.0.0.1", "--port", "0"], {
    cwd: root,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  const exitPromise = new Promise((resolve) => {
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  const output = [];
  let buffered = "";
  let resolveEndpoint;
  const endpointPromise = new Promise((resolve) => { resolveEndpoint = resolve; });
  const collect = (chunk) => {
    const text = String(chunk);
    output.push(text);
    if (output.join("").length > 12000) output.shift();
    buffered += text;
    const match = buffered.match(/(?:^|\r?\n)SQAG_SERVER_ENDPOINT=(http:\/\/127\.0\.0\.1:\d+)/);
    if (match) resolveEndpoint(match[1]);
  };
  child.stdout.on("data", collect);
  child.stderr.on("data", collect);
  const serverInfo = { child, endpoint: "", exitPromise, output, runRoot };
  const startupTimeout = new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error("Isolated loaded-app server startup exceeded 15 seconds.")), 15000);
    timer.unref?.();
  });
  const earlyExit = exitPromise.then(({ code, signal }) => {
    throw new Error(`Isolated loaded-app server exited before bind (exitCode=${code}, signalCode=${signal}).`);
  });
  try {
    const endpoint = await Promise.race([endpointPromise, startupTimeout, earlyExit]);
    serverInfo.endpoint = endpoint;
    const health = await fetch(`${endpoint}/api/health`, { signal: AbortSignal.timeout(3000) });
    if (!health.ok) throw new Error(`Isolated loaded-app health check returned ${health.status}.`);
    return serverInfo;
  } catch (error) {
    await stopIsolatedLoadedAppServer(serverInfo).catch(() => {});
    throw error;
  }
}

async function stopIsolatedLoadedAppServer(serverInfo) {
  if (!serverInfo) return { exitCode: null, signalCode: null };
  const deadline = Date.now() + 5000;
  if (serverInfo.child.exitCode === null && serverInfo.child.signalCode === null) serverInfo.child.kill();
  const waitUntil = (limit) => new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), Math.max(0, limit - Date.now()));
    timer.unref?.();
  });
  let exited = await Promise.race([serverInfo.exitPromise, waitUntil(Math.min(deadline, Date.now() + 4000))]);
  if (!exited) {
    serverInfo.child.kill("SIGKILL");
    exited = await Promise.race([serverInfo.exitPromise, waitUntil(deadline)]);
  }
  const exitCode = serverInfo.child.exitCode ?? exited?.code ?? null;
  const signalCode = serverInfo.child.signalCode ?? exited?.signal ?? null;
  if (exitCode === null && signalCode === null) {
    throw new Error("Isolated loaded-app child did not expose an exitCode or signalCode after bounded cleanup.");
  }
  return { exitCode, signalCode };
}

async function run573LoadedAppOnce(runIndex) {
  const parent = await fs.mkdtemp(path.join(os.tmpdir(), `sqag-run573-loaded-${runIndex}-`));
  let serverInfo = null;
  let providerInfo = null;
  let browser = null;
  try {
    providerInfo = await startSyntheticDeepSeekProvider();
    serverInfo = await startIsolatedLoadedAppServer(parent, {
      AI_BASIS_LINE_PROVIDER: "deepseek",
      DEEPSEEK_API_KEY: "synthetic-loopback-provider-key",
      DEEPSEEK_BASE_URL: providerInfo.endpoint,
      DEEPSEEK_BASIS_LINE_MODEL: "synthetic-basis-line-model",
    });
    baseUrl = serverInfo.endpoint;
    browser = await chromium.launch({ headless: !options.headed });
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });

    const parityPage = await context.newPage();
    await parityPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await parityPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    let basisRestorationParity;
    try {
      basisRestorationParity = await parityPage.evaluate(async (index) => {
      const admittedBasis = {
        graphics: "  leading\r\n\rtrailing\t  ",
        "custom-only": "Custom value\rSecond line\r\n\r\n  tail  ",
      };
      const expectedBasis = {
        graphics: "  leading\n\ntrailing\t  ",
        "custom-only": "Custom value\nSecond line\n\n  tail  ",
      };
      const snapshot = {
        version: QUOTE_SESSION_STATE_VERSION,
        activeAppView: "quote",
        quoteBasis: admittedBasis,
        quoteBasisSections: [],
        workflowStage: "quote_basis",
      };
      for (let cycle = 1; cycle <= 2; cycle += 1) {
        const restored = await applyQuoteSessionSnapshot(snapshot, { forceQuoteView: true });
        if (!restored || JSON.stringify(state.quoteBasis) !== JSON.stringify(expectedBasis) || state.quoteBasisSections.length !== 0) {
          throw new Error(`Basis-only restoration cycle ${cycle} changed canonical authority.`);
        }
      }
      let collisionRejected = false;
      try {
        canonicalQuoteBasisSections([
          { id: " same ", title: "One", lines: [{ text: "First" }] },
          { id: "same", title: "Two", lines: [{ text: "Second" }] },
        ]);
      } catch (error) {
        collisionRejected = /colliding identities/.test(String(error?.message || error));
      }
      if (!collisionRejected) throw new Error("Browser accepted a server-colliding section identity.");
      const whitespaceIdentity = canonicalQuoteBasisSections([
        { id: "\u0085", title: "Whitespace Identity", lines: [{ text: "kept" }] },
      ]);
      if (whitespaceIdentity[0]?.id !== "whitespace-identity") {
        throw new Error("Browser section identity normalization diverges from the server.");
      }
      for (const [characterName, character, expectedCollision] of [
        ["FEFF", "\ufeff", false],
        ["NEL", "\u0085", true],
        ["FILE_SEPARATOR", "\u001c", true],
        ["NBSP", "\u00a0", true],
      ]) {
        for (const field of ["basis_order", "category_order", "item_order"]) {
          const wrapped = `${character}${field}${character}`;
          for (const entries of [
            [[field, "invalid"], [wrapped, "2"]],
            [[wrapped, "2"], [field, "invalid"]],
            [[field, "2"], [wrapped, "invalid"]],
            [[wrapped, "invalid"], [field, "2"]],
          ]) {
            let rejected = false;
            try {
              canonicalizePrimaryOrderFields(Object.fromEntries(entries));
            } catch (error) {
              rejected = /colliding keys/.test(String(error?.message || error));
            }
            if (rejected !== expectedCollision) {
              throw new Error(`Browser/server ${characterName} parity diverged for ${field}.`);
            }
          }
          const standalone = canonicalizePrimaryOrderFields({ [wrapped]: "2" });
          const expectedKey = expectedCollision ? field : wrapped;
          if (standalone[expectedKey] !== (expectedCollision ? 2 : "2")) {
            throw new Error(`Browser/server standalone ${characterName} parity diverged for ${field}.`);
          }
        }
      }
      const run586LosslessText = "\r\n  lead\t  middle  \rtrail  \r\n";
      const run586ExpectedText = "\n  lead\t  middle  \ntrail  \n";
      state.quoteBasisSections = canonicalQuoteBasisSections([{
        id: "run586-target",
        title: "Furniture",
        basis_order: "0003",
        lines: [{ id: "run586-target-line", tag: "Include", text: "Bistro chair", quantity: 1, unit: "nos" }],
      }, {
        id: "run586-untouched",
        title: "Untouched",
        section_order: "0002",
        lines: [{ id: "run586-untouched-line", tag: "Exclude", text: run586LosslessText }],
      }]);
      state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
      state.lineItems = [];
      state.basisChat = {
        ...state.basisChat,
        scope: "line",
        sectionId: "run586-target",
        lineIndex: 0,
        line: "Bistro chair",
        quantity: "1",
        unit: "nos",
        proposal: null,
      };
      const run586Proposal = buildSelectedLineFragmentReplacementProposal("quantity to 2");
      if (!run586Proposal) throw new Error("Run-586 loaded-app quantity proposal was not built.");
      if (
        run586Proposal._origin?._originVersion !== BASIS_CHAT_PROPOSAL_ORIGIN_VERSION
        || !Object.isFrozen(run586Proposal._origin)
        || !Object.isFrozen(run586Proposal._origin.quoteBasisSections)
        || !Object.isFrozen(run586Proposal._origin.quoteBasisSections[0].lines[0])
      ) throw new Error("Run-599 local proposal origin was not recursively frozen.");
      const run586Authority = beginLocalBasisChatAuthority("local_fragment", run586Proposal._origin);
      setBasisChatProposal(run586Proposal, run586Authority.lineage, run586Authority.token);
      applyBasisChatProposal();
      const assertRun586CanonicalState = (label, sections, basis) => {
        const target = sections.find((section) => section.id === "run586-target");
        const untouched = sections.find((section) => section.id === "run586-untouched");
        if (
          target?.lines?.[0]?.quantity !== 2
          || target?.basis_order !== 3
          || untouched?.lines?.[0]?.text !== run586ExpectedText
          || untouched?.section_order !== 2
          || JSON.stringify(basis) !== JSON.stringify(quoteBasisFromSections(sections))
        ) {
          throw new Error(`Run-586 ${label} lost canonical basis state.`);
        }
      };
      assertRun586CanonicalState("authoritative mutation", state.quoteBasisSections, state.quoteBasis);
      const run586Snapshot = buildSessionSnapshot();
      assertRun586CanonicalState("session snapshot", run586Snapshot.quoteBasisSections, run586Snapshot.quoteBasis);
      const run586Payload = buildPayload();
      assertRun586CanonicalState("generation payload", run586Payload.quote_basis_sections, run586Payload.quote_basis);
      if (!await applyQuoteSessionSnapshot(run586Snapshot, { forceQuoteView: true })) {
        throw new Error("Run-586 loaded-app snapshot restoration failed.");
      }
      assertRun586CanonicalState("snapshot restoration", state.quoteBasisSections, state.quoteBasis);

      const run589Requests = [
        ["make the selected panel blue", "Selected panel painted blue"],
        ["update this panel so it has a satin navy finish", "Selected panel updated so it has a satin navy finish"],
        ["revise the selected line so the panel is coated in cobalt blue", "Revised selected line so the panel is coated in cobalt blue"],
      ];
      const run589ReferenceId = `run589-server-backed-reference-${index}`;
      const run589ReferenceSave = await postJson("/api/settings/pricing-references", {
        id: run589ReferenceId,
        label: `Run 589 Server-backed Reference ${index}`,
        source: "local",
        currency: "SGD",
        tax: { label: "GST", rate: 0.09 },
        items: [{
          id: "run589-selected-panel",
          section: "Target",
          description: "Existing bound line item",
          unit_hint: "nos",
          internal_cost: 10,
          markup_multiplier: 2,
          match_terms: ["selected panel"],
          object_families: ["panel"],
        }],
        update_existing: true,
        editing_reference_id: run589ReferenceId,
      });
      if (!run589ReferenceSave.ok || !["saved", "unchanged"].includes(run589ReferenceSave.data?.status)) {
        throw new Error(`Run-589 pricing reference save failed: ${JSON.stringify(run589ReferenceSave.data)}.`);
      }
      await loadProfiles();
      const run589Reference = state.pricingReferences.find((item) => item.id === run589ReferenceId && item.source === "local");
      if (!run589Reference) throw new Error("Run-589 pricing reference was not available to the loaded app.");
      state.pricingReferenceId = run589Reference.id;
      state.pricingReferenceSource = run589Reference.source;
      renderProfileOptions();
      if (!selectPricingReferenceOptionValue(pricingReferenceSelectValue(run589Reference))) {
        throw new Error("Run-589 pricing reference could not be selected.");
      }
      applyQuoteDetails({
        quote_date: "2026-09-17",
        project_number: `RUN589-${index}`,
        client: { name: "Run 589 Client", attention: "Synthetic Contact", title: "Manager", address: "1 Synthetic Street\nSingapore 000001" },
        project: { title: "Server-backed Basis Proof", show_name: "Run 589 Loaded App", booth_width: "3", booth_depth: "3", booth_size: "3m x 3m", dimension_source: "analysis" },
        company: { name: "Run 589 Quote Company", header_details: "Run 589 Quote Company\n1 Synthetic Street" },
        quote_text: { acceptance_text: "We accept this synthetic quotation.", person_label: "Authorised person", stamp_label: "Company stamp", date_label: "Signed date:" },
        signature: { company_signatory: "Synthetic Signatory", company_title: "Director", company_date_label: "Date:" },
      }, { partial: true });
      state.headerLogo = await ensureContentFingerprint({
        name: "run589-synthetic-logo.png",
        type: "image/png",
        size: 68,
        data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      });
      const run589ExpectedUnrelatedText = "\n  lead\t  middle  \ntrail  \n";
      const run589InitialSections = () => canonicalQuoteBasisSections([{
        id: "run589-target",
        title: "Target",
        basis_order: "0003",
        section_order: "0002",
        lines: [{
          id: "run589-target-line",
          tag: "Confirm",
          text: "Selected panel",
          quantity: 1,
          unit: "nos",
          confidence_pct: 81,
          source_line_item_id: "run589-source-target",
          pricing_keyword: "run589-selected-panel",
          category_order: "0004",
          item_order: "0005",
          admitted_metadata: { origin: "synthetic", sequence: [2, 1] },
        }, {
          id: "run589-duplicate-one",
          tag: "Confirm",
          text: "Selected panel",
          admitted_metadata: { duplicate: 1 },
        }],
      }, {
        id: "run589-unrelated",
        title: "Unrelated",
        basis_order: "0007",
        section_order: "0008",
        lines: [{
          id: "run589-unrelated-line",
          tag: "Exclude",
          text: "\r\n  lead\t  middle  \rtrail  \r\n",
          source_line_item_id: "run589-source-unrelated",
          admitted_metadata: { keep: true, nested: ["a", "b"] },
          category_order: "0009",
          item_order: "0010",
        }, {
          id: "run589-duplicate-two",
          tag: "Confirm",
          text: "Selected panel",
        }],
      }]);
      const assertRun589State = (label, sections, basis, expectedTargetText, lineItems = null) => {
        const targetSection = sections.find((section) => section.id === "run589-target");
        const unrelatedSection = sections.find((section) => section.id === "run589-unrelated");
        const target = targetSection?.lines?.[0];
        const duplicateOne = targetSection?.lines?.[1];
        const unrelated = unrelatedSection?.lines?.[0];
        const duplicateTwo = unrelatedSection?.lines?.[1];
        if (
          sections.length !== 2
          || targetSection?.lines?.length !== 2
          || unrelatedSection?.lines?.length !== 2
          || target?.text !== expectedTargetText
          || target?.id !== "run589-target-line"
          || target?.source_line_item_id !== "run589-source-target"
          || target?.pricing_keyword !== "run589-selected-panel"
          || target?.category_order !== 4
          || target?.item_order !== 5
          || JSON.stringify(target?.admitted_metadata) !== JSON.stringify({ origin: "synthetic", sequence: [2, 1] })
          || duplicateOne?.id !== "run589-duplicate-one"
          || duplicateOne?.text !== "Selected panel"
          || unrelated?.text !== run589ExpectedUnrelatedText
          || unrelated?.id !== "run589-unrelated-line"
          || unrelated?.source_line_item_id !== "run589-source-unrelated"
          || unrelated?.category_order !== 9
          || unrelated?.item_order !== 10
          || JSON.stringify(unrelated?.admitted_metadata) !== JSON.stringify({ keep: true, nested: ["a", "b"] })
          || duplicateTwo?.id !== "run589-duplicate-two"
          || targetSection?.basis_order !== 3
          || targetSection?.section_order !== 2
          || unrelatedSection?.basis_order !== 7
          || unrelatedSection?.section_order !== 8
          || JSON.stringify(basis) !== JSON.stringify(quoteBasisFromSections(sections))
        ) {
          throw new Error(`Run-589 ${label} lost canonical selected-line state.`);
        }
        if (lineItems) {
          const existing = lineItems[0];
          if (
            lineItems.length !== 1
            || existing?.description !== "Existing bound line item"
            || existing?.pricing_keyword !== "run589-selected-panel"
            || Number(existing?.category_order) !== 4
            || Number(existing?.item_order) !== 5
          ) {
            throw new Error(`Run-589 ${label} accepted a provider line-item rewrite or lost pricing bindings.`);
          }
        }
      };
      const run589Proofs = [];
      for (const [editRequest, expectedTargetText] of run589Requests) {
        state.images = [{
          name: "run589-synthetic-render.png",
          type: "image/png",
          size: 68,
          data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        }];
        state.quoteBasisSections = run589InitialSections();
        state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
        state.lineItems = [normalizeLineItem({
          section: "Target",
          description: "Existing bound line item",
          quantity: 1,
          unit: "nos",
          pricing_keyword: "run589-selected-panel",
          category_order: 4,
          item_order: 5,
        })];
        state.basisChat = {
          ...state.basisChat,
          scope: "line",
          sectionId: "run589-target",
          field: "run589-target",
          lineIndex: 0,
          line: "Confirm: Selected panel",
          quantity: 1,
          unit: "nos",
          quantityLabel: "1 nos",
          proposal: null,
        };
        if (parseLiteralReplacementCommand(editRequest) || buildSelectedLineFragmentReplacementProposal(editRequest)) {
          throw new Error(`Run-589 request did not bypass local proposal shortcuts: ${editRequest}`);
        }
        const proposalOrigin = basisChatProposalOrigin();
        invalidateBasisChatAuthority(basisChatRuntimeToken());
        const lineage = newBasisChatLineage("server");
        const runtimeToken = mintBasisChatRuntimeAuthority("running", proposalOrigin, lineage);
        installBasisChatOwner("running", proposalOrigin, lineage, runtimeToken);
        state.basisChat.busyOwnerId = lineage.clientOperationId;
        const operation = canonicalBasisChatOperation({
          _operationVersion: BASIS_CHAT_OPERATION_VERSION,
          id: lineage.requestedJobId,
          type: "basis_chat",
          phase: "starting",
          startedAt: new Date().toISOString(),
          browserRecoveryScope: currentBrowserRecoveryScope(),
          text: editRequest,
          proposalOrigin,
          lineage,
        });
        state.activeJob = operation;
        bindBasisChatRuntimeOperation(runtimeToken, operation);
        if (!setBasisChatBusy(true, runtimeToken)) throw new Error("Run-589 could not acquire basis-chat controls.");
        if (!operation) throw new Error("Run-599 could not capture immutable proposal origin.");
        const started = await startJob("basis_chat", basisChatPayload(editRequest), { jobId: lineage.requestedJobId });
        if (!started.ok) throw new Error(`Run-589 server-backed job did not start: ${JSON.stringify(started.data)}.`);
        const runningOperation = bindBasisChatServerOperation(operation, started.data, runtimeToken);
        const polled = await pollJob(runningOperation.lineage.serverJobId);
        if (!polled.ok || polled.data?.status !== "completed") {
          throw new Error(`Run-589 server-backed job did not complete: ${JSON.stringify(polled.data)}.`);
        }
        const rawProposal = polled.data?.result?.proposal;
        if (!rawProposal) throw new Error("Run-589 server-backed job returned no raw proposal.");
        assertRun589State(
          "raw server proposal",
          rawProposal.quote_basis_sections,
          rawProposal.quote_basis,
          expectedTargetText,
          rawProposal.line_items,
        );
        const normalizedProposal = normalizeServerBasisChatProposal(rawProposal, runningOperation, polled.data, runtimeToken);
        let proposalOnlyNormalizationRejected = false;
        try {
          normalizeServerBasisChatProposal(rawProposal);
        } catch (_error) {
          proposalOnlyNormalizationRejected = true;
        }
        if (!proposalOnlyNormalizationRejected) throw new Error("Run-599 proposal-only normalization did not fail closed.");
        let missingOriginRejected = false;
        try {
          canonicalTargetOnlyBasisChatProposal(rawProposal);
        } catch (_error) {
          missingOriginRejected = true;
        }
        if (!missingOriginRejected) throw new Error("Run-599 target-only admission created a missing origin.");
        let conflictingOriginRejected = false;
        try {
          canonicalTargetOnlyBasisChatProposal({
            ...rawProposal,
            _origin: { ...runningOperation.proposalOrigin, outputRevision: runningOperation.proposalOrigin.outputRevision + 1 },
          }, runningOperation.proposalOrigin, runningOperation.lineage, runtimeToken);
        } catch (_error) {
          conflictingOriginRejected = true;
        }
        if (!conflictingOriginRejected) throw new Error("Run-599 accepted a conflicting supplied origin.");
        if (
          normalizedProposal._origin?._originVersion !== BASIS_CHAT_PROPOSAL_ORIGIN_VERSION
          || !Object.isFrozen(normalizedProposal._origin)
          || !Object.isFrozen(normalizedProposal._origin.quoteBasisSections)
        ) throw new Error("Run-599 server proposal origin was not recursively frozen.");
        assertRun589State(
          "normalized server proposal",
          normalizedProposal.quoteBasisSections,
          normalizedProposal.quoteBasis,
          expectedTargetText,
          normalizedProposal.lineItems,
        );
        const origin = normalizedProposal._origin;
        const originalSessionId = state.quoteSessionId;
        const originalRevision = state.outputRevision;
        const originalBasis = state.quoteBasis;
        const originalSections = state.quoteBasisSections;
        const staleCases = [
          ["session", () => { state.quoteSessionId = `${originalSessionId || "quote-run589"}-stale`; }, () => { state.quoteSessionId = originalSessionId; }],
          ["revision", () => { state.outputRevision = originalRevision + 1; }, () => { state.outputRevision = originalRevision; }],
          ["map", () => { state.quoteBasis = { ...originalBasis, "run589-target": "Confirm: stale map" }; }, () => { state.quoteBasis = originalBasis; }],
          ["basis", () => { state.quoteBasisSections = cloneQuoteBasisSections(originalSections); state.quoteBasisSections[1].lines[0].metadata.keep = false; state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections); }, () => { state.quoteBasisSections = originalSections; state.quoteBasis = originalBasis; }],
          ["target", () => { state.quoteBasisSections = cloneQuoteBasisSections(originalSections); state.quoteBasisSections[0].lines[0].text = "Different selected target"; state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections); }, () => { state.quoteBasisSections = originalSections; state.quoteBasis = originalBasis; }],
        ];
        for (const [label, mutate, restore] of staleCases) {
          mutate();
          if (basisChatOriginIsCurrent(origin)) throw new Error(`Run-593 stale ${label} proposal was accepted.`);
          restore();
        }
        setBasisChatProposal(normalizedProposal, runningOperation.lineage, runtimeToken);
        state.outputRevision = originalRevision + 1;
        applyBasisChatProposal();
        if (JSON.stringify(state.quoteBasisSections) !== JSON.stringify(originalSections)) {
          throw new Error("Run-593 stale proposal mutated authoritative basis state.");
        }
        state.outputRevision = originalRevision;
        const validOrigin = basisChatProposalOrigin();
        const validAuthority = beginLocalBasisChatAuthority("local_fragment", validOrigin);
        const validProposal = canonicalTargetOnlyBasisChatProposal(rawProposal, validOrigin, validAuthority.lineage, validAuthority.token);
        setBasisChatProposal(validProposal, validAuthority.lineage, validAuthority.token);
        applyBasisChatProposal();
        assertRun589State("authoritative state", state.quoteBasisSections, state.quoteBasis, expectedTargetText, state.lineItems);
        const snapshot = buildSessionSnapshot();
        assertRun589State("session snapshot", snapshot.quoteBasisSections, snapshot.quoteBasis, expectedTargetText, snapshot.lineItems);
        const generationPayload = buildPayload();
        assertRun589State(
          "generation payload",
          generationPayload.quote_basis_sections,
          generationPayload.quote_basis,
          expectedTargetText,
          generationPayload.line_items,
        );
        if (!await applyQuoteSessionSnapshot(snapshot, { forceQuoteView: true })) {
          throw new Error("Run-589 loaded-app snapshot restoration failed.");
        }
        assertRun589State("snapshot restoration", state.quoteBasisSections, state.quoteBasis, expectedTargetText, state.lineItems);
        run589Proofs.push({ editRequest, expectedTargetText });
      }
      return {
        cycles: 2,
        collisionRejected,
        whitespaceIdentity: whitespaceIdentity[0].id,
        run586BasisMutation: { quantity: 2, basisOrder: 3, sectionOrder: 2, text: run586ExpectedText },
        run589ServerBackedProofs: run589Proofs,
      };
      }, runIndex);
    } catch (error) {
      const serverLogRoot = path.join(parent, "server-log");
      const logNames = await fs.readdir(serverLogRoot).catch(() => []);
      const logExcerpts = await Promise.all(logNames.slice(-5).map(async (name) => {
        const content = await fs.readFile(path.join(serverLogRoot, name), "utf8").catch(() => "");
        return `${name}:\n${content.slice(-4000)}`;
      }));
      throw new Error([
        String(error?.message || error),
        `Synthetic provider calls: ${JSON.stringify(providerInfo.requests)}`,
        ...logExcerpts,
      ].filter(Boolean).join("\n"));
    }
    await parityPage.close();
    if (providerInfo.requests.length !== 3) {
      throw new Error(`Run-589 expected three synthetic provider calls, received ${providerInfo.requests.length}.`);
    }
    basisRestorationParity.run589SyntheticProviderRequests = providerInfo.requests.map(({ editRequest, model }) => ({ editRequest, model }));

    const prepared = await page.evaluate(async (index) => {
      const referenceId = `run573-override-pricing-${index}`;
      const saved = await postJson("/api/settings/pricing-references", {
        id: referenceId,
        label: `Run 573 Override Pricing ${index}`,
        source: "local",
        currency: "SGD",
        tax: { label: "GST", rate: 0.09 },
        items: [{
          id: "catalog-control-row",
          section: "Control",
          description: "Catalog control row",
          unit_hint: "nos",
          internal_cost: 10,
          markup_multiplier: 2,
          match_terms: ["catalog control row"],
          object_families: ["control"],
        }],
        update_existing: true,
        editing_reference_id: referenceId,
      });
      if (!saved.ok || !["saved", "unchanged"].includes(saved.data?.status)) {
        throw new Error(`Exact pricing reference save failed: ${JSON.stringify(saved.data)}.`);
      }
      const detailResponse = await fetch(`/api/settings/pricing-references/${encodeURIComponent(referenceId)}?source=local`);
      const detail = await detailResponse.json();
      if (!detailResponse.ok) throw new Error(`Exact server-side pricing readback failed: ${detailResponse.status}.`);
      const authority = detail.pricing_reference || {};
      if (!authority.digest_sha256 || authority.id !== referenceId || authority.source !== "local") {
        throw new Error(`Exact server-side pricing authority is incomplete: ${JSON.stringify(authority)}.`);
      }

      await loadProfiles();
      const reference = state.pricingReferences.find((item) => item.id === referenceId && item.source === "local");
      if (!reference || reference.digest_sha256 !== authority.digest_sha256) {
        throw new Error("Loaded-app pricing authority differs from the exact server readback.");
      }
      state.pricingReferenceId = reference.id;
      state.pricingReferenceSource = reference.source;
      renderProfileOptions();
      if (!selectPricingReferenceOptionValue(pricingReferenceSelectValue(reference))) {
        throw new Error("Loaded-app pricing reference could not be selected.");
      }
      if (state.profiles.length) {
        state.profileId = state.profiles[0].id;
        renderPresetOptions();
        loadSelectedPreset({ silent: true, allowOwnedInitialization: true });
      }
      applyQuoteDetails({
        quote_date: "2026-09-16",
        project_number: `RUN573-${index}`,
        client: { name: "Run 573 Client", attention: "Test Contact", title: "Manager", address: "1 Test Street\nSingapore 000001" },
        project: { title: "Override-only Quote", show_name: "Loaded App Proof", booth_width: "3", booth_depth: "3", booth_size: "3m x 3m", dimension_source: "analysis" },
        company: { name: "Run 573 Quote Company", header_details: "Run 573 Quote Company\n1 Test Street" },
        quote_text: { acceptance_text: "We accept this quotation." },
        signature: { company_signatory: "Test Signatory", company_title: "Director", company_date_label: "Date:", person_label: "Authorised person", stamp_label: "Company stamp", date_label: "Signed date:" },
      }, { partial: true });
      state.headerLogo = await ensureContentFingerprint({
        name: "run573-logo.png",
        type: "image/png",
        size: 68,
        data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
      });
      state.images = [await ensureContentFingerprint({
        name: "run573-render.png",
        type: "image/png",
        size: 68,
        data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
      })];
      const losslessText = "\r\n  lead\t  middle  \rtrail  \r\n";
      const expectedLosslessText = "\n  lead\t  middle  \ntrail  \n";
      state.quoteBasisSections = canonicalQuoteBasisSections([
        {
          id: "run573-override",
          title: "Custom",
          section_order: "0002",
          basis_order: 4,
          lines: [{ id: "run573-override-line", tag: "Custom", text: "Custom override-only fabrication\nPreserve this second line", include: true, custom_pricing: true, custom_confirmed: true, quantity: 2, unit: "nos" }],
        },
        {
          id: "run580-lossless",
          title: "Lossless",
          section_order: 1,
          basis_order: "0003",
          lines: [{ id: "run580-lossless-line", tag: "Exclude", text: losslessText }],
        },
      ]);
      state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
      const persistenceProjection = quoteBasisPersistenceProjection();
      if (
        persistenceProjection.quote_basis_sections[1]?.lines?.[0]?.text !== expectedLosslessText
        || persistenceProjection.quote_basis_sections[0]?.section_order !== 2
        || persistenceProjection.quote_basis_sections[0]?.basis_order !== 4
        || persistenceProjection.quote_basis_sections[1]?.section_order !== 1
        || persistenceProjection.quote_basis_sections[1]?.basis_order !== 3
        || JSON.stringify(persistenceProjection.quote_basis) !== JSON.stringify(quoteBasisFromSections(persistenceProjection.quote_basis_sections))
      ) {
        throw new Error("Generation basis serialization is not lossless or internally consistent.");
      }
      state.lineItems = [normalizeLineItem({
        section: "Custom",
        description: "Custom override-only fabrication",
        quantity: 2,
        unit: "nos",
        pricing_keyword: "override-only-not-in-catalog",
        source_basis_line_id: "run573-override-line",
        price_mode: "Priced",
        unit_price_override: 37,
      })];
      state.quoteCommercialLifecycle = "NEW_UNINITIALISED";
      state.quoteCommercialSnapshot = null;
      const normalized = await postJson("/api/line-items/normalize", {
        profile_id: generationProfileIdForPayload(),
        quote_exchange_rate: 1,
        pricing_reference_id: reference.id,
        pricing_reference_source: reference.source,
        pricing_reference: { id: reference.id, source: reference.source, currency: authority.currency, digest_sha256: authority.digest_sha256 },
        project: { booth_width: "3", booth_depth: "3", booth_size: "3m x 3m", dimension_source: "analysis" },
        quote_basis: canonicalQuoteBasisForPersistence(),
        quote_basis_sections: cloneQuoteBasisSections(state.quoteBasisSections),
        line_items: state.lineItems.map(normalizeLineItem),
      });
      if (normalized.ok && Array.isArray(normalized.data?.line_items)) {
        state.lineItems = normalized.data.line_items.map(normalizeLineItem);
      }
      if (!normalized.ok || state.lineItems[0]?.approved_quote_amount !== 74) {
        throw new Error(`Override-only normalization failed: ${JSON.stringify(normalized.data)}.`);
      }
      state.quoteCommercialSnapshot = quoteCommercialSnapshotForDetails(collectQuoteDetails(), {
        lifecycle: "NEW_UNINITIALISED",
        origin: "explicit_initialization",
        reference,
        replacePricingAuthority: true,
      });
      captureOriginalAnalysisSnapshot({ quote_basis_sections: state.quoteBasisSections, source: "run573-loaded-app" });
      const capturedSections = cloneQuoteBasisSections(state.quoteBasisSections);
      state.quoteBasisSections[1].lines[0].text = "temporary lossy reset probe";
      state.quoteBasis = quoteBasisFromSections(state.quoteBasisSections);
      resetQuoteBasisToOriginal();
      if (JSON.stringify(state.quoteBasisSections) !== JSON.stringify(capturedSections)) {
        throw new Error("Clone/reset did not preserve canonical basis sections.");
      }
      const resetProjection = quoteBasisPersistenceProjection();
      const resetLossless = resetProjection.quote_basis_sections.find((section) => section.id === "run580-lossless");
      if (
        resetLossless?.lines?.[0]?.text !== expectedLosslessText
        || resetLossless?.section_order !== 1
        || resetLossless?.basis_order !== 3
      ) {
        throw new Error("Reset state-writing projection lost canonical text or ordering fields.");
      }
      refreshOutputRowsFromLineItems();
      state.originalOutputRows = snapshotOutputRows(state.outputRows);
      state.basisConfirmed = true;
      state.quoteSessionDraftSaveStarted = true;
      setWorkflowStage("completed");
      setSidePanel("output", { force: true });
      await handleGenerate();
      if (!state.downloadFile || !downloadFileIsFresh(state.downloadFile)) {
        throw new Error(`Genuine handleGenerate() did not publish a current XLSX: ${JSON.stringify({ status: state.workflowStage, file: state.downloadFile, review: state.quoteCommercialReview, missing: missingDetailFields(), messages: elements.messageList?.textContent, result: elements.resultStatus?.textContent, rows: state.outputRows })}.`);
      }
      const sessionId = state.quoteSessionId;
      const sessionResponse = await fetch(`/api/quote-sessions/${encodeURIComponent(sessionId)}`);
      const sessionBody = await sessionResponse.json();
      if (!sessionResponse.ok) throw new Error(`Generated session readback failed: ${sessionResponse.status}.`);
      const persisted = sessionBody.quote_session || {};
      const xlsx = persisted.exports?.xlsx || {};
      const persistedPricing = persisted.draft_state?.quoteDetails?.commercial_snapshot?.pricing_basis || {};
      if (persistedPricing.id !== authority.id || persistedPricing.source !== authority.source || persistedPricing.digest !== authority.digest_sha256) {
        throw new Error("Persisted generated session does not retain the exact server-side pricing authority.");
      }
      const persistedSections = persisted.draft_state?.quoteBasisSections || [];
      const persistedLossless = persistedSections.find((section) => section.id === "run580-lossless");
      if (
        persistedLossless?.lines?.[0]?.text !== expectedLosslessText
        || JSON.stringify(persisted.draft_state?.quoteBasis || {}) !== JSON.stringify(quoteBasisFromSections(persistedSections))
      ) {
        throw new Error("Generated session did not preserve the lossless basis projection.");
      }
      return { sessionId, authorityDigest: authority.digest_sha256, xlsx };
    }, runIndex);

    const verifyProtectedXlsx = async (label) => {
      const download = await page.request.get(new URL(prepared.xlsx.url, baseUrl).href);
      if (download.status() !== 200) throw new Error(`${label} protected XLSX retrieval returned ${download.status()}.`);
      const bytes = await download.body();
      const checksum = createHash("sha256").update(bytes).digest("hex");
      if (bytes.length !== prepared.xlsx.size_bytes || checksum !== prepared.xlsx.sha256) {
        throw new Error(`${label} downloaded XLSX bytes do not match the published size/checksum.`);
      }
      if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b || !bytes.includes(Buffer.from("[Content_Types].xml"))) {
        throw new Error(`${label} artifact is not a genuine XLSX ZIP package.`);
      }
      return { checksum, sizeBytes: bytes.length };
    };
    const initialDownload = await verifyProtectedXlsx("Initial");
    const cycles = [];
    for (let cycle = 1; cycle <= 2; cycle += 1) {
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
      await page.waitForFunction(() => !appIsBusy(), null, { timeout: 15000 });
      const restored = await page.evaluate(async ({ sessionId, expectedSha, expectedSize }) => {
        const didRestore = await modifyDashboardQuote(sessionId);
        if (!didRestore) throw new Error(`Could not restore ${sessionId}.`);
        const restoredProjection = quoteBasisPersistenceProjection();
        const generationProjection = buildPayload();
        const restoredLossless = restoredProjection.quote_basis_sections.find((section) => section.id === "run580-lossless");
        const generatedLossless = generationProjection.quote_basis_sections.find((section) => section.id === "run580-lossless");
        if (
          restoredLossless?.lines?.[0]?.text !== "\n  lead\t  middle  \ntrail  \n"
          || restoredLossless?.section_order !== 1
          || restoredLossless?.basis_order !== 3
          || JSON.stringify(generatedLossless) !== JSON.stringify(restoredLossless)
        ) {
          throw new Error("Reloaded generation input lost canonical basis state.");
        }
        const before = state.downloadFile ? { ...state.downloadFile } : null;
        const saved = await saveCurrentQuoteSession({
          quoteGenerated: true,
          includeDraftState: true,
          includeDraftFiles: false,
          draftState: currentQuoteSessionDraftState(),
        });
        const detail = await loadQuoteSessionDetail(sessionId);
        const xlsx = detail?.exports?.xlsx || {};
        return {
          saved: Boolean(saved),
          fresh: Boolean(state.downloadFile && downloadFileIsFresh(state.downloadFile)),
          beforeSha: before?.sha256 || "",
          sha: xlsx.sha256 || "",
          size: xlsx.size_bytes || 0,
          stale: xlsx.stale,
          url: xlsx.url || "",
          expectedSha,
          expectedSize,
        };
      }, { sessionId: prepared.sessionId, expectedSha: prepared.xlsx.sha256, expectedSize: prepared.xlsx.size_bytes });
      if (!restored.saved || !restored.fresh || restored.stale === true || restored.sha !== prepared.xlsx.sha256 || restored.size !== prepared.xlsx.size_bytes || !restored.url) {
        throw new Error(`Unchanged restoration/save cycle ${cycle} did not preserve current publication: ${JSON.stringify(restored)}.`);
      }
      cycles.push({ cycle, ...await verifyProtectedXlsx(`Cycle ${cycle}`) });
    }
    await context.close();
    return { run: runIndex, sessionId: prepared.sessionId, checksum: initialDownload.checksum, sizeBytes: initialDownload.sizeBytes, pricingDigest: prepared.authorityDigest, basisRestorationParity, cycles };
  } finally {
    if (browser) await browser.close().catch(() => {});
    let exit = { exitCode: null, signalCode: null };
    try {
      exit = await stopIsolatedLoadedAppServer(serverInfo);
    } finally {
      try {
        if (providerInfo) await providerInfo.close();
      } finally {
        await fs.rm(parent, { recursive: true, force: true });
      }
    }
    if (serverInfo && exit.exitCode === null && exit.signalCode === null) {
      throw new Error("Loaded-app child cleanup state was not inspectable.");
    }
  }
}

async function run573LoadedAppTwice() {
  const runs = [];
  for (let index = 1; index <= 2; index += 1) runs.push(await run573LoadedAppOnce(index));
  console.log(JSON.stringify({ status: "ok", mode: "run573-loaded-app-twice", runs }, null, 2));
}

async function main() {
  if (args.includes("--run573-loaded-app")) {
    await run573LoadedAppTwice();
    return;
  }
  const sqag212Only = args.includes("--sqag212-only")
    || args.includes("--sqag212-g3-controls-only")
    || args.includes("--sqag212-g3-lifecycle-only");
  let serverInfo = null;
  const hasExistingServer = sqag212Only ? false : await healthOk();
  if (!sqag212Only && !hasExistingServer) {
    await fs.rm(quoteDataRoot, { recursive: true, force: true });
    serverInfo = startServer();
    if (!(await waitForHealth())) {
      const serverOutput = serverInfo.output.join("").trim();
      await stopServer(serverInfo);
      throw new Error(`Could not start webapp at ${baseUrl}.${serverOutput ? `\n\n${serverOutput}` : ""}`);
    }
  }

  const browser = await chromium.launch({ headless: !options.headed });
  const context = await browser.newContext({ viewport: { width: 1365, height: 768 } });
  const page = await context.newPage();
  const consoleProblems = [];
  const networkProblems = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`);
  });
  page.on("pageerror", (error) => consoleProblems.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) networkProblems.push(`${response.status()} ${response.url()}`);
  });
  const mainQuoteSessionResponses = [];
  context.on("response", (response) => {
    const pathName = new URL(response.url()).pathname;
    if (pathName === "/api/quote-sessions" || pathName.startsWith("/api/quote-sessions/")) {
      mainQuoteSessionResponses.push({ method: response.request().method(), status: response.status() });
    }
  });
  let sqag212Isolation = null;

  try {
    if (sqag212Only) {
      sqag212Isolation = await runSqag212RegressionInIsolatedServer(page, serverInfo, baseUrl);
      console.log(JSON.stringify({
        status: "ok",
        regression: args.includes("--sqag212-g3-lifecycle-only")
          ? "sqag212-g3-lifecycle-producer-deadline-controls"
          : args.includes("--sqag212-g3-controls-only")
            ? "sqag212-g3-controls-before-pdf"
            : "sqag212-analysis-confirmation-save-reload-xlsx-pdf",
        isolation: sqag212Isolation,
      }, null, 2));
      return;
    }
    await installMockProfiles(page);
    await verifyRecoveredTemplateOwnerFailsClosed(page);
    await verifyFreshPricingAuthorityInitializesBeforeCustomer(page);
    const downstreamResponseBaseline = mainQuoteSessionResponses.length;
    const downstreamUrl = baseUrl;
    const focusedG3Isolation = await runSqag212RegressionInIsolatedServer(
      page,
      serverInfo,
      downstreamUrl,
      { g3ControlsOnly: true },
    );
    const focusedCases = focusedG3Isolation.focusedG3Controls?.threeFindingHarness || {};
    const requiredLifecycleCases = [
      "L1_NEGATIVE_DELIVERY_FAILURE_THEN_PAGE_CLOSE",
      "L1_NEGATIVE_DELIVERY_FAILURE_THEN_DOCUMENT_NAVIGATION",
      "L1_POSITIVE_DELIVERY_FAILURE_PAGE_REMAINS_OPEN",
      "L1_POSITIVE_CLEAN_PAGE_CLOSE_WITH_PROVEN_ZERO_WORK",
      "L1_POSITIVE_NAVIGATION_AFTER_REQUIRED_WORK_COMPLETES",
      "L2_NEGATIVE_OWNED_FLUSH_LEASE_WITHOUT_EXECUTABLE_WORK",
      "L2_POSITIVE_FLUSH_OWNERSHIP_TRANSFERS_TO_STARTED_REAL_SAVE",
      "L2_POSITIVE_NATIVE_TIMER_REMAINS_REAL_PRODUCER",
      "L2_POSITIVE_STARTED_FLUSH_HELD_THEN_COMPLETES",
      "L3_NEGATIVE_HELD_EVENT_ACK_BOUNDED_BY_ABSOLUTE_DEADLINE",
      "L3_POSITIVE_NORMAL_EVENT_ACK_WITHIN_BUDGET",
      "L3_POSITIVE_REQUIRED_TIMER_NATIVE_COMPLETION_WITHIN_BUDGET",
      "L3_POSITIVE_FINAL_ZERO_ARRIVAL_STILL_BOUNDED",
      "R1_NEGATIVE_POST_BEFORE_SAVE_START_WITH_QUEUE_STATE_EVOLUTION",
      "R1_NEGATIVE_WRONG_TIMER_POST_STILL_REJECTED",
      "R1_POSITIVE_SAVE_START_BEFORE_POST",
      "R1_POSITIVE_POST_BEFORE_SAVE_START_SAME_SAVE_STATE",
      "R2_NEGATIVE_OLDER_SAVE_ACTUALLY_NOT_PERSISTED",
      "R2_POSITIVE_A_PERSISTS_THEN_B_VALIDLY_SUPERSEDES_BEFORE_A_READBACK",
      "R2_POSITIVE_A_READBACK_BEFORE_B",
      "R2_NEGATIVE_B_SUCCESS_CANNOT_LAUNDER_A_FAILURE",
      "R1B_NEGATIVE_SECOND_POST_STALE_QUEUE_STATE_CANNOT_BYPASS_PENDING_INVOCATION_CORRELATION",
      "R1B_POSITIVE_SINGLE_POST_POST_BEFORE_SAVE_START",
      "R1B_POSITIVE_SINGLE_POST_SAVE_START_BEFORE_POST",
      "R1B_POSITIVE_REAL_403_REFRESH_SAME_PAYLOAD_RETRY",
      "R1B_NEGATIVE_CONCURRENT_SAME_PAYLOAD_DUPLICATE",
      "R1B_NEGATIVE_WRONG_PAYLOAD",
      "R1B_NEGATIVE_WRONG_GENERATION",
      "R1B_NEGATIVE_TERMINAL_REPLAY",
      "R2B_NEGATIVE_NULL_PRIOR_CLOCK_UNPERSISTED_A_THEN_VALID_B",
      "R2B_NEGATIVE_ADVANCED_TIMESTAMP_WITHOUT_A_STATE_PERSISTENCE",
      "R2B_NEGATIVE_B_FILES_CANNOT_SUBSTITUTE_FOR_A_FILE_DURABILITY",
      "R2B_NEGATIVE_B_SUCCESS_CANNOT_SUPPLY_A_MISSING_STATE_OR_FILE_PROOF",
      "R2B_POSITIVE_A_INDEPENDENTLY_PROVEN_THEN_B_SUPERSEDES_BEFORE_A_READBACK",
      "R2B_POSITIVE_A_READBACK_BEFORE_B",
      "R2B_POSITIVE_A_PROVEN_WITH_NULL_PRIOR_CLOCK_USING_CONSEQUENTIAL_STATE_FILE_EVIDENCE",
      "R2B_ADVERSARIAL_PROOF_WAIT_PAGE_CLOSE",
      "R2B_ADVERSARIAL_PROOF_WAIT_NAVIGATION",
      "R2B_ADVERSARIAL_PROOF_WAIT_ABSOLUTE_DEADLINE",
      "R1B_R2B_NO_LIMITER_SELF_INTERFERENCE",
    ];
    const lifecycleCasePassed = (evidence) => evidence === "PASS" || evidence?.result === "PASS";
    if (
      focusedCases.L1_RESULT !== "PASS"
      || focusedCases.L2_RESULT !== "PASS"
      || focusedCases.L3_RESULT !== "PASS"
      || focusedCases.R1_RESULT !== "PASS"
      || focusedCases.R2_RESULT !== "PASS"
      || focusedCases.R1B_RESULT !== "PASS"
      || focusedCases.R2B_RESULT !== "PASS"
      || requiredLifecycleCases.some((caseName) => !lifecycleCasePassed(focusedCases[caseName]))
    ) {
      throw new Error(`SQAG #212 focused lifecycle controls did not report every required L1-L3, R1-R2, and R1B-R2B case: ${JSON.stringify(focusedCases)}.`);
    }
    const functionalIsolation = await runSqag212RegressionInIsolatedServer(
      page,
      serverInfo,
      downstreamUrl,
      { skipFocusedControls: true },
    );
    if (
      functionalIsolation.pdfExport?.filename !== "quotation.pdf"
      || functionalIsolation.pdfExport?.magic !== "%PDF"
      || functionalIsolation.pdfExport?.downloadStatus !== 200
      || functionalIsolation.workbookExport?.filename !== "quotation.xlsx"
      || functionalIsolation.workbookExport?.downloadStatus !== 200
      || functionalIsolation.workbookExport?.magic !== "504b0304"
    ) {
      throw new Error(`SQAG #212 separated functional smoke did not prove real workbook and PDF downloads: ${JSON.stringify(functionalIsolation)}.`);
    }
    sqag212Isolation = {
      ...functionalIsolation,
      browserNegativeControl: focusedG3Isolation.browserNegativeControl,
      focusedG3Controls: focusedG3Isolation.focusedG3Controls,
      focusedG3Isolation: {
        isolatedServerPid: focusedG3Isolation.isolatedServerPid,
        isolatedPort: focusedG3Isolation.isolatedPort,
        isolatedServerStopped: focusedG3Isolation.isolatedServerStopped,
        isolatedStorageCleaned: focusedG3Isolation.isolatedStorageCleaned,
      },
      functionalSmokePdfAndWorkbookParity: true,
    };
    if (mainQuoteSessionResponses.length !== downstreamResponseBaseline) {
      throw new Error("SQAG #212 quote-session traffic reached the downstream smoke process during its isolated prelude.");
    }
    sqag212Isolation.downstreamResponseCountAtPreludeStart = downstreamResponseBaseline;
    sqag212Isolation.downstreamResponseCountAtPreludeEnd = mainQuoteSessionResponses.length;
    await verifyRun639PricingAuthorityRestorationAndPresentation(page);
    await verifyServerPricingReferenceReviewDurability(page);
    if (args.includes("--recovery-only")) {
      await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
      await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
      await verifyConfirmBasisSurvivesImmediateRefresh(page);
      await verifyGenerationLoadingModalSurvivesRefresh(page);
      await verifyGenerationTerminalRecoveryAfterRefresh(page);
      await verifyExpiredQuoteJobsDoNotResume(page);
      await verifyPricingReferenceSelectionCommitsOnCustomerNext(page);
      sqag212Isolation.downstreamRequestAccounting = verifyDownstreamQuoteSessionAccounting(
        mainQuoteSessionResponses,
        downstreamResponseBaseline,
      );
      console.log(JSON.stringify({
        status: "ok",
        mode: "recovery-only",
        sqag212Isolation,
        consoleProblems,
        networkProblems,
      }, null, 2));
      return;
    }
    await verifyBrowserRecoveryScopeIsolation(page);
    await verifyStaleTabMutationIsRejected(page);
    await verifyConcurrentInitialDraftSaveUsesSingleSession(page);
    await verifyInitialDraftSaveReservesSessionIdBeforeNetwork(page);
    await verifyDashboardNewQuoteDoesNotSaveHiddenDraft(page);
    await verifyDashboardClearsStaleSessionsBeforeRefresh(page);
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    const statusFilterLabels = await page.locator("#dashboardStatusFilter option").evaluateAll((options) => (
      options.map((option) => option.textContent?.trim())
    ));
    if (JSON.stringify(statusFilterLabels) !== JSON.stringify(["All", "Draft", "Draft Modified", "Generated"])) {
      throw new Error(`Unexpected dashboard status filters: ${JSON.stringify(statusFilterLabels)}`);
    }
    await expectTopbarPrimaryAction(page, "new-quote");
    await page.waitForFunction(() => {
      const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
      const emptyState = document.querySelector("#dashboardEmptyState");
      const sessionList = document.querySelector("#dashboardSessionsList");
      return !/Loading sessions/i.test(countText)
        && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
    }, null, { timeout: 15000 });
    const dashboardShot = await screenshot(page, "dashboard.png");
    const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
    if (await emptyNewQuoteButton.isVisible()) {
      await emptyNewQuoteButton.click();
    } else {
      await page.locator("#newQuoteButton:not([disabled])").click();
    }
    await page.locator("#imageIntake").waitFor({ state: "visible" });
    await expectTopbarPrimaryAction(page, "dashboard");
    const homeShot = await screenshot(page, "home.png");

    await page.locator("#topbarBrandButton", { hasText: "Swooshz Quote Generator" }).click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardEmptyState").waitFor({ state: "visible", timeout: 15000 });
    const blankDraftRows = await page.locator(".dashboard-session-card").count();
    if (blankDraftRows !== 0) {
      throw new Error(`Blank quote draft should be discarded on dashboard return, found ${blankDraftRows} dashboard rows.`);
    }
    await page.locator("#newQuoteButton:not([disabled])").click();
    await page.locator("#imageIntake").waitFor({ state: "visible" });
    await expectTopbarPrimaryAction(page, "dashboard");

    await seedQuoteDraftFromTestFixture(page);
    const preCustomerQuoteSessionId = await currentQuoteSessionId(page);
    if (preCustomerQuoteSessionId) {
      throw new Error(`Expected dashboard draft saving to wait until Next: Customer, found ${preCustomerQuoteSessionId}.`);
    }
    await page.setViewportSize({ width: 520, height: 720 });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForFunction(() => window.scrollY > 40, null, { timeout: 15000 });
    await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.waitForTimeout(50);
    const postNextScrollY = await page.evaluate(() => window.scrollY);
    if (postNextScrollY > 2) {
      throw new Error(`Next: Customer should reset the page scroll to the top, found scrollY=${postNextScrollY}.`);
    }
    await page.setViewportSize({ width: 1365, height: 768 });
    if (!(await page.locator("#showName").inputValue()).trim()) {
      await page.locator("#showName").fill("Synthetic Expo");
    }
    await page.waitForFunction(() => {
      try {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return Boolean(saved.quoteSessionId);
      } catch {
        return false;
      }
    }, null, { timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="quote_company"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="quote_company"]').click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
    const seededPresetValue = await page.locator("#presetSelect").inputValue();
    if (seededPresetValue !== "profile:synthetic-exhibition-fixture-template:synthetic-fixture-default") {
      throw new Error(`Expected seeded setup to select the synthetic fixture preset, found ${seededPresetValue}.`);
    }
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#quoteCompanyPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "dashboard");
    const refreshedActiveRailTexts = await page.locator(".rail-button.is-active").evaluateAll((buttons) => (
      buttons.map((button) => button.textContent?.trim() || "")
    ));
    if (refreshedActiveRailTexts.length !== 1 || refreshedActiveRailTexts[0] !== "Quote Company") {
      throw new Error(`Expected refresh to restore the last quote menu, found ${JSON.stringify(refreshedActiveRailTexts)}.`);
    }
    await page.evaluate(() => {
      state.outputRows = [normalizeOutputRow({
        section: "Refresh regression",
        description: "Completed quote row",
        quantity: 1,
        unit: "lot",
        unit_price: 100,
        amount: 100,
      })];
      state.originalOutputRows = state.outputRows.map((row) => ({ ...row }));
      state.basisConfirmed = true;
      setWorkflowStage("completed");
      setSidePanel("customer", { force: true });
      saveSessionState();
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    const completedQuoteRefreshPanel = await page.locator(".rail-button.is-active").innerText();
    if (completedQuoteRefreshPanel.trim() !== "Customer") {
      throw new Error(`Completed quote refresh should preserve Customer, found ${completedQuoteRefreshPanel}.`);
    }
    const restoredQuoteSessionId = await currentQuoteSessionId(page);
    if (!restoredQuoteSessionId) {
      throw new Error("Expected refresh recovery to keep the current quote session id.");
    }
    await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "new-quote");
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.evaluate((storageKey) => {
      const saved = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
      saved.quoteSessionId = "quote-unrelated-regression";
      saved.activeAppView = "dashboard";
      window.localStorage.setItem(storageKey, JSON.stringify(saved));
    }, "swooshz_quote_session_v1");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    const unrelatedLocalQuoteSessionId = await currentQuoteSessionId(page);
    if (unrelatedLocalQuoteSessionId === restoredQuoteSessionId) {
      throw new Error(`Expected Modify quote regression to use a non-current browser draft, found ${unrelatedLocalQuoteSessionId}.`);
    }
    await page.locator(`.dashboard-session-card[data-quote-session-id="${restoredQuoteSessionId}"]`).click();
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).waitFor({ timeout: 15000 });
    const restoredDetailBeforeModify = await dashboardQuoteSessionDetail(page, restoredQuoteSessionId);
    const restoredUpdatedAtBeforeModify = restoredDetailBeforeModify.quote_session?.updated_at || "";
    if (!restoredUpdatedAtBeforeModify) {
      throw new Error("Expected dashboard session detail to expose updated_at before Modify quote.");
    }
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "dashboard");
    const modifiedQuoteSessionId = await currentQuoteSessionId(page);
    if (modifiedQuoteSessionId !== restoredQuoteSessionId) {
      throw new Error(`Expected Modify quote to restore saved dashboard session ${restoredQuoteSessionId}, found ${modifiedQuoteSessionId}.`);
    }
    const restoredActiveRailTexts = await page.locator(".rail-button.is-active").evaluateAll((buttons) => (
      buttons.map((button) => button.textContent?.trim() || "")
    ));
    if (restoredActiveRailTexts.length !== 1 || restoredActiveRailTexts[0] !== "Output") {
      throw new Error(`Expected Modify quote to open the furthest completed panel Output, found ${JSON.stringify(restoredActiveRailTexts)}.`);
    }
    const restoredFiles = await page.locator("#fileList .file-item").evaluateAll((items) => (
      items.map((item) => item.textContent?.trim() || "")
    ));
    if (restoredFiles.length !== 1 || !restoredFiles[0].includes(TEST_REFERENCE_FILE_NAME)) {
      throw new Error(`Expected refresh to preserve the seeded PDF reference, found ${JSON.stringify(restoredFiles)}.`);
    }
    await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "new-quote");
    const restoredCardAfterCleanReturn = page.locator(`.dashboard-session-card[data-quote-session-id="${restoredQuoteSessionId}"]`);
    await restoredCardAfterCleanReturn.waitFor({ state: "visible", timeout: 15000 });
    const restoredDetailAfterCleanReturn = await dashboardQuoteSessionDetail(page, restoredQuoteSessionId);
    const restoredUpdatedAtAfterCleanReturn = restoredDetailAfterCleanReturn.quote_session?.updated_at || "";
    if (restoredUpdatedAtAfterCleanReturn !== restoredUpdatedAtBeforeModify) {
      throw new Error(`Modify -> Dashboard without edits should not rewrite or reorder the session: ${restoredUpdatedAtBeforeModify} -> ${restoredUpdatedAtAfterCleanReturn}.`);
    }
    await restoredCardAfterCleanReturn.click();
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).waitFor({ timeout: 15000 });
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "dashboard");
    await page.locator('.rail-button[data-side-panel="quote_company"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="quote_company"]').click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible", timeout: 15000 });
    const restoredPresetValue = await page.locator("#presetSelect").inputValue();
    if (restoredPresetValue !== "profile:synthetic-exhibition-fixture-template:synthetic-fixture-default") {
      throw new Error(`Expected refresh to preserve company preset, found ${restoredPresetValue}.`);
    }
    const presetSelectBox = await page.locator("#presetSelect").boundingBox();
    if (!presetSelectBox || presetSelectBox.width < 200) {
      throw new Error("Company preset dropdown is unexpectedly narrow.");
    }
    await page.locator('.rail-button[data-side-panel="customer"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="customer"]').click();
    await page.mouse.move(4, 4);
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
    await page.locator(".workspace-pane-scroll").evaluate((element) => {
      element.scrollTop = 0;
    });
    const pricingSummary = await page.locator("#selectedPricingReferenceSummary").innerText();
    if (!pricingSummary.includes("Managed in Settings")) {
      throw new Error(`Unexpected pricing reference summary: ${pricingSummary}`);
    }
    const quoteCurrencyValue = await page.locator("#quoteCurrency").inputValue();
    const quoteTaxValue = await page.locator("#quoteTaxLabel").inputValue();
    const quoteRateValue = await page.locator("#quoteTaxRate").inputValue();
    const quoteExchangeRateValue = await page.locator("#quoteExchangeRate").inputValue();
    if (!quoteCurrencyValue) {
      throw new Error("Quote currency field did not have a selected value.");
    }
    if (!/GST|VAT/i.test(quoteTaxValue)) {
      throw new Error(`Unexpected quote tax value: ${quoteTaxValue}`);
    }
    if (!quoteRateValue) {
      throw new Error("Quote tax rate field did not have a value.");
    }
    if (quoteExchangeRateValue !== "1") {
      throw new Error(`Expected same-currency exchange rate to default to 1, found ${quoteExchangeRateValue}.`);
    }
    const currencyBox = await page.locator("#quoteCurrency").boundingBox();
    const exchangeRateBox = await page.locator("#quoteExchangeRate").boundingBox();
    const taxBox = await page.locator("#quoteTaxLabel").boundingBox();
    const taxRateBox = await page.locator("#quoteTaxRate").boundingBox();
    if (!currencyBox || !exchangeRateBox || !taxBox || !taxRateBox) {
      throw new Error("Quote commercial fields were not measurable.");
    }
    if (Math.abs(currencyBox.y - exchangeRateBox.y) > 8 || Math.abs(taxBox.y - taxRateBox.y) > 8 || taxBox.y <= currencyBox.y) {
      throw new Error(`Quote commercial fields are not in the expected 2x2 order: ${JSON.stringify({ currencyBox, exchangeRateBox, taxBox, taxRateBox })}`);
    }
    const selectedPricingValue = await page.locator("#profileSelect").inputValue();
    if (!selectedPricingValue) {
      throw new Error("Pricing reference select did not have a selected value.");
    }
    const profileSelectBox = await page.locator("#profileSelect").boundingBox();
    if (!profileSelectBox || profileSelectBox.width < 200) {
      throw new Error("Pricing reference dropdown is unexpectedly narrow.");
    }
    if (presetSelectBox.width > profileSelectBox.width) {
      throw new Error(`Company preset dropdown width ${presetSelectBox.width} should stay no wider than pricing reference dropdown width ${profileSelectBox.width}.`);
    }
    const customerPricingShot = await screenshot(page, "customer-pricing.png");
    await page.locator("#settingsButton").click();
    await page.locator("#pricingReferenceModal").waitFor({ state: "visible" });
    await page.getByRole("heading", { name: "Pricing Reference Settings" }).waitFor();
    await page.locator("#pricingReferenceImportTab").click();
    await page.locator("#pricingReferenceImportPanel").waitFor({ state: "visible" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#pricingReferenceModal").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#pricingReferenceImportPanel").waitFor({ state: "visible" });
    if ((await page.locator("#pricingReferenceImportTab").getAttribute("aria-selected")) !== "true") {
      throw new Error("Pricing Reference refresh should preserve the Import tab instead of returning to Manage.");
    }
    await page.keyboard.press("Escape");
    await page.locator("#pricingReferenceModal").waitFor({ state: "hidden" });
    await page.locator("#quoteDate").waitFor({ state: "visible" });
    const dateBoldButton = page.locator('[data-date-format-command="bold"]');
    await page.locator("#quoteDate").focus();
    await dateBoldButton.waitFor({ state: "visible" });
    await dateBoldButton.click();
    if ((await dateBoldButton.getAttribute("aria-pressed")) !== "true") {
      throw new Error("Quote date bold formatting did not toggle on.");
    }
    await page.mouse.move(4, 4);
    const activeRailTexts = await page.locator(".rail-button.is-active").evaluateAll((buttons) => (
      buttons.map((button) => button.textContent?.trim() || "")
    ));
    if (activeRailTexts.length !== 1 || activeRailTexts[0] !== "Customer") {
      throw new Error(`Expected only Customer rail item to be active, found ${JSON.stringify(activeRailTexts)}.`);
    }
    const customerShot = await screenshot(page, "customer.png");

    const footerBox = await page.locator(".workspace-pane-footer").boundingBox();
    const viewport = page.viewportSize();
    if (!footerBox || !viewport || footerBox.y + footerBox.height > viewport.height + 1) {
      throw new Error("Workspace footer is not inside the current viewport.");
    }

    await page.setViewportSize({ width: 520, height: 720 });
    await page.evaluate(() => window.scrollTo(0, 0));
    const mobileScrollExtent = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
    if (mobileScrollExtent < 80) {
      throw new Error(`Mobile layout did not create enough page scroll distance: ${mobileScrollExtent}.`);
    }
    const scrollPaneBox = await page.locator(".workspace-pane-scroll").boundingBox();
    if (!scrollPaneBox) {
      throw new Error("Workspace scroll pane was not visible in mobile layout.");
    }
    const mobileScrollBefore = await page.evaluate(() => window.scrollY);
    const paneScrollBefore = await page.locator(".workspace-pane-scroll").evaluate((element) => element.scrollTop);
    await page.mouse.move(scrollPaneBox.x + scrollPaneBox.width / 2, scrollPaneBox.y + Math.min(scrollPaneBox.height / 2, 260));
    await page.mouse.wheel(0, 520);
    await page.waitForTimeout(100);
    let mobileScrollAfter = await page.evaluate(() => window.scrollY);
    let paneScrollAfter = await page.locator(".workspace-pane-scroll").evaluate((element) => element.scrollTop);
    if (mobileScrollAfter <= mobileScrollBefore + 10 && paneScrollAfter <= paneScrollBefore + 10) {
      await page.evaluate(() => {
        if (document.activeElement instanceof HTMLElement) {
          document.activeElement.blur();
        }
        document.body.focus();
      });
      await page.keyboard.press("PageDown");
      await page.waitForTimeout(100);
      mobileScrollAfter = await page.evaluate(() => window.scrollY);
      paneScrollAfter = await page.locator(".workspace-pane-scroll").evaluate((element) => element.scrollTop);
    }
    if (mobileScrollAfter <= mobileScrollBefore + 10 && paneScrollAfter <= paneScrollBefore + 10) {
      throw new Error(`Mobile layout did not scroll from wheel or keyboard input: page ${mobileScrollBefore} -> ${mobileScrollAfter}, pane ${paneScrollBefore} -> ${paneScrollAfter}.`);
    }

    const bodyText = await page.locator("body").innerText();
    if (!bodyText.includes("Upload") || !bodyText.includes("Customer") || !bodyText.includes("Quote date")) {
      throw new Error("Rendered page did not include the expected workspace text.");
    }

    await page.setViewportSize({ width: 1365, height: 768 });
    await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).waitFor({ state: "visible", timeout: 15000 });
    const currentDashboardSessionId = await currentQuoteSessionId(page);
    if (!currentDashboardSessionId) {
      throw new Error("Expected the current quote session id before returning to the dashboard.");
    }
    await page.locator("#backToDashboardButton").click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "new-quote");
    await page.locator("#quoteDashboardPanel").evaluate((element) => {
      element.scrollTop = 0;
    });
    const currentDashboardCard = page.locator(`.dashboard-session-card[data-quote-session-id="${currentDashboardSessionId}"]`);
    await currentDashboardCard.waitFor({ state: "visible", timeout: 15000 });
    const savedStepPills = await currentDashboardCard.locator(".dashboard-status-pill.is-progress").allTextContents();
    if (!savedStepPills.some((text) => /Saved at Output/i.test(text))) {
      throw new Error(`Expected dashboard card to show saved step pill for the current draft, found ${JSON.stringify(savedStepPills)}.`);
    }
    const modifiedDateMetrics = await currentDashboardCard.locator(".dashboard-session-meta-zone div").nth(1).locator("dd").evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const lineRects = Array.from(range.getClientRects()).filter((rect) => rect.width > 1 && rect.height > 1);
      range.detach();
      return {
        text: element.textContent?.trim() || "",
        lines: lineRects.length,
        height: Math.round(element.getBoundingClientRect().height),
        lineHeight: Number.parseFloat(window.getComputedStyle(element).lineHeight) || 0,
      };
    });
    if (modifiedDateMetrics.lines > 1 || modifiedDateMetrics.height > Math.ceil(modifiedDateMetrics.lineHeight * 1.35)) {
      throw new Error(`Dashboard modified timestamp should stay on one line, found ${JSON.stringify(modifiedDateMetrics)}.`);
    }
    const currentDashboardDetail = await dashboardQuoteSessionDetail(page, currentDashboardSessionId);
    const currentDraftState = currentDashboardDetail.quote_session?.draft_state || {};
    if (!Array.isArray(currentDraftState.images) || !currentDraftState.images.some((image) => image.name === TEST_REFERENCE_FILE_NAME)) {
      throw new Error(`Expected dashboard session draft state to include the reference PDF metadata, found ${JSON.stringify(currentDraftState.images || [])}.`);
    }
    if (JSON.stringify(currentDraftState).includes("data:application/pdf")) {
      throw new Error("Dashboard session draft state should not store raw PDF data URLs.");
    }
    let duplicateDetailRequestCount = 0;
    let releaseInitialDuplicateRequest = null;
    let markFirstDuplicateRequestPaused = null;
    const firstDuplicateRequestPaused = new Promise((resolve) => {
      markFirstDuplicateRequestPaused = resolve;
    });
    let recoveryPhaseArmed = false;
    let recoveryDetailRequestCount = 0;
    let releaseRecoveryDuplicateRequest = null;
    let markRecoveryDuplicateRequestPaused = null;
    let recoveryRequestPauseReached = false;
    let recoveryHandshakeFailed = false;
    const recoveryDuplicateRequestPaused = new Promise((resolve) => {
      markRecoveryDuplicateRequestPaused = resolve;
    });
    const awaitFirstDuplicateRequestPaused = () => new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("First duplicate detail request pause was not reached.")), 15000);
      firstDuplicateRequestPaused.then(() => {
        clearTimeout(timeout);
        resolve();
      }, (error) => {
        clearTimeout(timeout);
        reject(error);
      });
    });
    const awaitRecoveryDuplicateRequestPaused = () => new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        recoveryHandshakeFailed = true;
        recoveryPhaseArmed = false;
        if (typeof releaseRecoveryDuplicateRequest === "function") releaseRecoveryDuplicateRequest();
        reject(new Error("Recovery duplicate detail request pause was not reached."));
      }, 15000);
      recoveryDuplicateRequestPaused.then(() => {
        clearTimeout(timeout);
        resolve();
      }, (error) => {
        clearTimeout(timeout);
        reject(error);
      });
    });
    const duplicateInterruptionDiagnostics = async (firstRequestPauseReached) => {
      const operation = await page.evaluate(() => {
        try {
          return JSON.parse(window.localStorage.getItem("swooshz_dashboard_operation_v1") || "null");
        } catch {
          return null;
        }
      }).catch(() => null);
      const appState = await page.evaluate(() => ({
        appBusy: Boolean(state.isAnalysisRunning || state.isGenerating || state.isPreparingOutput || state.quoteSessionRestoreBusy || state.quoteSessionDashboardBusy),
        restoreBusy: Boolean(state.quoteSessionRestoreBusy),
      })).catch(() => ({ appBusy: false, restoreBusy: false }));
      const duplicateOperationContractValid = Boolean(
        operation
        && operation.type === "duplicate"
        && operation.sourceSessionId === currentDashboardSessionId
        && typeof operation.targetSessionId === "string"
        && operation.targetSessionId.length > 0
        && operation.targetSessionId !== currentDashboardSessionId
      );
      return {
        duplicate_detail_request_count: duplicateDetailRequestCount,
        duplicate_operation_present: Boolean(operation),
        duplicate_operation_type_expected: duplicateOperationContractValid,
        dashboard_loading_modal_visible: await page.locator("#dashboardLoadingModal").isVisible().catch(() => false),
        dashboard_loading_title_expected: await page.locator("#dashboardLoadingTitle", { hasText: "Duplicating quote" }).isVisible().catch(() => false),
        app_busy: appState.appBusy,
        restore_busy: appState.restoreBusy,
        first_request_pause_reached: firstRequestPauseReached,
        recovery_detail_request_count: recoveryDetailRequestCount,
        recovery_phase_armed: recoveryPhaseArmed,
        recovery_request_pause_reached: recoveryRequestPauseReached,
        first_request_release_authority_present: typeof releaseInitialDuplicateRequest === "function",
        recovery_request_release_authority_present: typeof releaseRecoveryDuplicateRequest === "function",
        recovery_handshake_failed: recoveryHandshakeFailed,
      };
    };
    const failDuplicateInterruption = async (message, firstRequestPauseReached) => {
      console.error(JSON.stringify(await duplicateInterruptionDiagnostics(firstRequestPauseReached)));
      throw new Error(message);
    };
    const duplicateDetailPattern = `**/api/quote-sessions/${currentDashboardSessionId}`;
    const duplicateDetailRoute = async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      duplicateDetailRequestCount += 1;
      if (duplicateDetailRequestCount === 1) {
        await new Promise((resolve) => {
          releaseInitialDuplicateRequest = resolve;
          markFirstDuplicateRequestPaused();
        });
        await route.abort("aborted");
        return;
      }
      if (recoveryPhaseArmed && !recoveryHandshakeFailed && recoveryDetailRequestCount === 0) {
        recoveryDetailRequestCount += 1;
        await new Promise((resolve) => {
          releaseRecoveryDuplicateRequest = resolve;
          markRecoveryDuplicateRequestPaused();
        });
        await route.continue();
        return;
      }
      await route.continue();
    };
    await page.route(duplicateDetailPattern, duplicateDetailRoute);
    let duplicatedTargetCard;
    try {
    await currentDashboardCard.click();
    await page.locator('[data-dashboard-panel-action="duplicate-session"]', { hasText: "Duplicate Quote" }).click();
    let firstRequestPauseReached = false;
    try {
      await awaitFirstDuplicateRequestPaused();
      firstRequestPauseReached = true;
    } catch {
      if (typeof releaseInitialDuplicateRequest === "function") releaseInitialDuplicateRequest();
      await failDuplicateInterruption("Duplicate detail request pause handshake was not reached.", false);
    }
    const interruptedDuplicateOperation = await page.evaluate(() => JSON.parse(
      window.localStorage.getItem("swooshz_dashboard_operation_v1") || "null"
    ));
    const duplicateOperationIdentityValid = Boolean(
      interruptedDuplicateOperation
      && interruptedDuplicateOperation.type === "duplicate"
      && interruptedDuplicateOperation.sourceSessionId === currentDashboardSessionId
      && typeof interruptedDuplicateOperation.targetSessionId === "string"
      && interruptedDuplicateOperation.targetSessionId.length > 0
      && interruptedDuplicateOperation.targetSessionId !== currentDashboardSessionId
    );
    if (!duplicateOperationIdentityValid) {
      await failDuplicateInterruption("Duplicate interruption persisted-operation contract failed.", firstRequestPauseReached);
    }
    const dashboardLoadingModalVisible = await page.locator("#dashboardLoadingModal").isVisible().catch(() => false);
    const dashboardLoadingTitleExpected = await page.locator("#dashboardLoadingTitle", { hasText: "Duplicating quote" }).isVisible().catch(() => false);
    if (!dashboardLoadingModalVisible || !dashboardLoadingTitleExpected) {
      await failDuplicateInterruption("Duplicate interruption loading contract failed.", firstRequestPauseReached);
    }
    recoveryPhaseArmed = true;
    const duplicateReload = page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(100);
    if (typeof releaseInitialDuplicateRequest !== "function") {
      throw new Error("Initial duplicate detail request was not paused before refresh.");
    }
    releaseInitialDuplicateRequest();
    await duplicateReload;
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await awaitRecoveryDuplicateRequestPaused();
    recoveryRequestPauseReached = true;
    const recoveryDuplicateOperation = await page.evaluate(() => JSON.parse(
      window.localStorage.getItem("swooshz_dashboard_operation_v1") || "null"
    ));
    const recoveryDuplicateOperationContractValid = Boolean(
      recoveryDuplicateOperation
      && recoveryDuplicateOperation.type === "duplicate"
      && recoveryDuplicateOperation.sourceSessionId === currentDashboardSessionId
      && typeof recoveryDuplicateOperation.targetSessionId === "string"
      && recoveryDuplicateOperation.targetSessionId.length > 0
      && recoveryDuplicateOperation.targetSessionId !== currentDashboardSessionId
      && recoveryDuplicateOperation.targetSessionId === interruptedDuplicateOperation.targetSessionId
    );
    if (!recoveryDuplicateOperationContractValid) {
      throw new Error("Post-reload duplicate operation contract failed.");
    }
    const recoveryLoadingModalVisible = await page.locator("#dashboardLoadingModal").isVisible();
    const recoveryLoadingTitle = page.locator("#dashboardLoadingTitle");
    const recoveryLoadingTitleVisible = await recoveryLoadingTitle.isVisible();
    const recoveryLoadingTitleText = recoveryLoadingTitleVisible ? (await recoveryLoadingTitle.innerText()).trim() : "";
    if (!recoveryLoadingModalVisible || !recoveryLoadingTitleVisible || recoveryLoadingTitleText !== "Duplicating quote") {
      throw new Error("Post-reload duplicate loading contract failed.");
    }
    if (typeof releaseRecoveryDuplicateRequest !== "function") {
      throw new Error("Recovery duplicate detail request was not held.");
    }
    recoveryPhaseArmed = false;
    releaseRecoveryDuplicateRequest();
    releaseRecoveryDuplicateRequest = null;
    duplicatedTargetCard = page.locator(`.dashboard-session-card[data-quote-session-id="${interruptedDuplicateOperation.targetSessionId}"]`);
    await duplicatedTargetCard.waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.waitForFunction(() => !window.localStorage.getItem("swooshz_dashboard_operation_v1"));
    if (await duplicatedTargetCard.count() !== 1) {
      throw new Error(`Duplicate refresh should create exactly one target ${interruptedDuplicateOperation.targetSessionId}.`);
    }
    } catch (error) {
      recoveryPhaseArmed = false;
      recoveryHandshakeFailed = true;
      if (typeof releaseInitialDuplicateRequest === "function") releaseInitialDuplicateRequest();
      if (typeof releaseRecoveryDuplicateRequest === "function") releaseRecoveryDuplicateRequest();
      throw error;
    } finally {
      recoveryPhaseArmed = false;
      await page.unroute(duplicateDetailPattern, duplicateDetailRoute);
    }
    await duplicatedTargetCard.click();
    await page.locator('[data-dashboard-panel-action="delete-session"]').click();
    await page.locator("#confirmQuoteSessionDeleteButton").click();
    await duplicatedTargetCard.waitFor({ state: "detached", timeout: 15000 });

    await currentDashboardCard.click();
    await page.keyboard.press("Delete");
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const keyboardSingleDeleteTitle = await page.locator("#quoteSessionDeleteTitle").innerText();
    if (keyboardSingleDeleteTitle !== "Delete quote session?") {
      throw new Error(`Unexpected keyboard single delete confirmation title: ${keyboardSingleDeleteTitle}`);
    }
    const keyboardSingleDeleteCopy = await page.locator("#quoteSessionDeleteText").innerText();
    if (keyboardSingleDeleteCopy !== "This removes the local dashboard record and any saved local exports for this quote session. This cannot be undone.") {
      throw new Error(`Unexpected keyboard single delete confirmation copy: ${keyboardSingleDeleteCopy}`);
    }
    await page.locator("#cancelQuoteSessionDeleteButton").click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel").waitFor({ state: "visible", timeout: 15000 });
    const normalModeCheckboxVisible = await currentDashboardCard.locator(".dashboard-session-select-control").isVisible();
    if (normalModeCheckboxVisible) {
      throw new Error("Row checkbox should stay hidden until Select mode is enabled.");
    }
    const singlePanelActionMetrics = await dashboardPanelActionMetrics(page, "single selection");
    const dashboardSingleSelectedShot = await screenshot(page, "dashboard-single-selected.png");
    await createDashboardSmokeSession(page, "reference-c", {
      sessionIdPrefix: "quote-4f-search-row",
      customerName: "Internal Test Exhibition Booth",
      projectName: "Four Foxtrot Smoke Search",
    });
    await page.getByRole("button", { name: "Clear selected session", exact: true }).click();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSearchInput").fill("Four Foxtrot Smoke Search");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const characterSearchRows = await page.locator(".dashboard-session-card").count();
    if (characterSearchRows !== 1) {
      const matchingCards = await page.locator(".dashboard-session-card").evaluateAll((cards) => cards.map((card) => ({
        id: card.getAttribute("data-quote-session-id"),
        text: card.innerText,
      })));
      throw new Error("Expected search for Four Foxtrot Smoke Search to match only the REF QUOTE-4F row, found " + characterSearchRows + ": " + JSON.stringify(matchingCards) + ".");
    }
    const characterSearchText = await page.locator(".dashboard-session-card").first().innerText();
    if (!characterSearchText.includes("REF QUOTE-4F")) {
      throw new Error(`Search for Four Foxtrot Smoke Search did not return the visible quote reference row: ${characterSearchText}`);
    }
    await page.locator("#dashboardSearchInput").fill("");
    await createDashboardSmokeSession(page, "alpha", { sessionIdPrefix: "quote-7a-playwright-alpha" });
    await createDashboardSmokeSession(page, "beta", { sessionIdPrefix: "quote-2c-hidden-3" });
    await createDashboardSmokeSession(page, "gamma", { sessionIdPrefix: "quote-bulk-extra-1" });
    await createDashboardSmokeSession(page, "delta", { sessionIdPrefix: "quote-bulk-extra-2" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSearchInput").fill("7a");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const referenceSearchTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!referenceSearchTexts.some((text) => text.includes("REF QUOTE-7A"))) {
      throw new Error(`Search for 7a did not return the visible quote reference row among ${referenceSearchTexts.length} visible cards.`);
    }
    await page.locator("#dashboardSearchInput").fill("7a");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const digitSearchTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!digitSearchTexts.some((text) => text.includes("REF QUOTE-7A"))) {
      throw new Error(`Repeated search for 7a did not return the visible quote reference row among ${digitSearchTexts.length} visible cards.`);
    }
    await page.locator("#dashboardSearchInput").fill("");
    await createDashboardSmokeSession(page, "untitled-visible", {
      sessionIdPrefix: "quote-2c-untitled-visible",
      customerName: "",
      projectName: "",
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSearchInput").fill("untitled customer");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const untitledCustomerTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!untitledCustomerTexts.some((text) => text.includes("Untitled customer") && text.includes("REF QUOTE-2C"))) {
      throw new Error(`Search for Untitled customer did not return the visible REF QUOTE-2C fallback row: ${JSON.stringify(untitledCustomerTexts)}`);
    }
    await page.locator("#dashboardSearchInput").fill("untitled quote");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const untitledProjectTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!untitledProjectTexts.some((text) => text.includes("Untitled quote") && text.includes("REF QUOTE-2C"))) {
      throw new Error(`Search for Untitled quote did not return the visible REF QUOTE-2C fallback row: ${JSON.stringify(untitledProjectTexts)}`);
    }
    await page.locator("#dashboardSearchInput").fill("Marina Bay Product Launch");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const visibleBulkRows = await page.locator(".dashboard-session-card").count();
    if (visibleBulkRows < 2) {
      throw new Error(`Expected at least two bulk smoke rows, found ${visibleBulkRows}.`);
    }
    const filteredFirstCard = page.locator(".dashboard-session-card").first();
    const selectModeButton = page.locator("#dashboardSelectModeButton", { hasText: "Select" });
    const selectModeBox = await selectModeButton.boundingBox();
    if (!selectModeBox || selectModeBox.height > 40 || selectModeBox.width > 100) {
      throw new Error(`Select mode control is too large: ${JSON.stringify(selectModeBox)}.`);
    }
    await selectModeButton.click();
    await page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" }).waitFor({ state: "visible", timeout: 15000 });
    await filteredFirstCard.locator(".dashboard-session-select-control").waitFor({ state: "visible", timeout: 15000 });
    const firstCardTopBeforeBulk = await filteredFirstCard.evaluate((element) => element.getBoundingClientRect().top);
    await filteredFirstCard.click();
    const firstCardTopAfterBulk = await filteredFirstCard.evaluate((element) => element.getBoundingClientRect().top);
    if (Math.abs(firstCardTopAfterBulk - firstCardTopBeforeBulk) > 12) {
      throw new Error(`Bulk panel selection shifted the session list: ${firstCardTopBeforeBulk} -> ${firstCardTopAfterBulk}.`);
    }
    const rowCheckboxBox = await filteredFirstCard.locator("[data-dashboard-select]").boundingBox();
    if (!rowCheckboxBox || rowCheckboxBox.width > 18 || rowCheckboxBox.height > 18) {
      throw new Error(`Row checkbox is too large: ${JSON.stringify(rowCheckboxBox)}.`);
    }
    await page.locator(".dashboard-session-card.is-selected").first().waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: "SELECTED SESSION" }).waitFor({ state: "visible", timeout: 15000 });
    const selectAllButton = page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" });
    const selectAllBox = await selectAllButton.boundingBox();
    if (!selectAllBox || selectAllBox.height > 40 || selectAllBox.width > 180) {
      throw new Error(`Select all control is too large: ${JSON.stringify(selectAllBox)}.`);
    }
    await selectAllButton.click();
    await page.locator(".dashboard-bulk-selection-summary", { hasText: `${visibleBulkRows} quote sessions selected` }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: `${visibleBulkRows} quote sessions selected` }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: "Bulk selection" }).waitFor({ state: "visible", timeout: 15000 });
    const bulkPanelActionMetrics = await dashboardPanelActionMetrics(page, "bulk selection");
    if (Math.abs(bulkPanelActionMetrics.bottomGap - singlePanelActionMetrics.bottomGap) > 8) {
      throw new Error(`Dashboard action footer bottom moved between single and bulk states: ${singlePanelActionMetrics.bottomGap}px -> ${bulkPanelActionMetrics.bottomGap}px.`);
    }
    if (Math.abs(bulkPanelActionMetrics.actionX - singlePanelActionMetrics.actionX) > 4) {
      throw new Error(`Dashboard action footer x offset moved between single and bulk states: ${singlePanelActionMetrics.actionX}px -> ${bulkPanelActionMetrics.actionX}px.`);
    }
    if (Math.abs(bulkPanelActionMetrics.actionWidth - singlePanelActionMetrics.actionWidth) > 4) {
      throw new Error(`Dashboard action footer width changed between single and bulk states: ${singlePanelActionMetrics.actionWidth}px -> ${bulkPanelActionMetrics.actionWidth}px.`);
    }
    if (Math.abs(bulkPanelActionMetrics.deleteClearGap - singlePanelActionMetrics.deleteClearGap) > 4) {
      throw new Error(`Dashboard delete/clear spacing changed between single and bulk states: ${singlePanelActionMetrics.deleteClearGap}px -> ${bulkPanelActionMetrics.deleteClearGap}px.`);
    }
    await page.setViewportSize({ width: 520, height: 720 });
    const mobileBulkPanelActionMetrics = await dashboardPanelActionMetrics(page, "mobile bulk selection");
    if (mobileBulkPanelActionMetrics.bottomGap < 8 || mobileBulkPanelActionMetrics.bottomGap > 36) {
      throw new Error(`Mobile bulk action footer is not bottom anchored: ${mobileBulkPanelActionMetrics.bottomGap}px gap.`);
    }
    const dashboardSelectedMobileShot = await screenshot(page, "dashboard-selected-mobile.png");
    await page.setViewportSize({ width: 1365, height: 768 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: "Bulk selection" }).waitFor({ state: "visible", timeout: 15000 });
    const bulkExtraBlocks = await page.locator(".dashboard-bulk-breakdown, .dashboard-bulk-value-card").count();
    if (bulkExtraBlocks !== 0) {
      throw new Error("Bulk panel should not show status breakdown or combined value blocks.");
    }
    const bulkPanelText = await page.locator("#dashboardSelectedSessionPanel").innerText();
    if (bulkPanelText.includes("Status breakdown") || bulkPanelText.includes("Combined Value")) {
      throw new Error("Bulk panel copy still includes removed status or combined value sections.");
    }
    const selectAllChecked = await page.locator("#dashboardSelectModeButton").evaluate((button) => button.classList.contains("is-all-selected"));
    if (!selectAllChecked) {
      throw new Error("Select all control did not enter the checked visual state after selecting all visible rows.");
    }
    await selectAllButton.click();
    const selectedAfterDeselectAll = await page.locator(".dashboard-session-card.is-selected").count();
    if (selectedAfterDeselectAll !== 0) {
      throw new Error(`Select all toggle did not deselect visible rows: ${selectedAfterDeselectAll} remain selected.`);
    }
    await page.locator("#dashboardSelectedSessionPanel").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSelectModeButton", { hasText: "Select" }).waitFor({ state: "visible", timeout: 15000 });
    const returnedToNoneMode = await page.locator("#dashboardSelectModeButton").evaluate((button) => (
      !button.classList.contains("is-all-selected")
        && !button.classList.contains("is-selecting")
        && button.getAttribute("aria-pressed") === "false"
    ));
    if (!returnedToNoneMode) {
      throw new Error("Select all toggle did not return the control to the original Select mode.");
    }
    const rowCheckboxVisibleAfterDeselectAll = await filteredFirstCard.locator(".dashboard-session-select-control").isVisible();
    if (rowCheckboxVisibleAfterDeselectAll) {
      throw new Error("Row checkboxes stayed visible after Select all visible was toggled off.");
    }
    await page.locator("#dashboardSelectModeButton", { hasText: "Select" }).click();
    await page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" }).click();
    await page.locator(".dashboard-bulk-selection-summary", { hasText: `${visibleBulkRows} quote sessions selected` }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#quoteDashboardPanel").evaluate((panel) => {
      panel.scrollTop = 0;
    });
    const dashboardSelectedShot = await screenshot(page, "dashboard-selected.png");
    await page.locator("[data-dashboard-select]").first().focus();
    await page.keyboard.press("Delete");
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const keyboardDeleteTitle = await page.locator("#quoteSessionDeleteTitle").innerText();
    if (keyboardDeleteTitle !== "Delete selected quote sessions?") {
      throw new Error(`Unexpected keyboard bulk delete confirmation title: ${keyboardDeleteTitle}`);
    }
    await page.locator("#cancelQuoteSessionDeleteButton").click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator('[data-dashboard-panel-action="delete-selected"]', { hasText: "Delete selected" }).click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const dashboardDeleteModalShot = await screenshot(page, "dashboard-delete-modal.png");
    const bulkDeleteTitle = await page.locator("#quoteSessionDeleteTitle").innerText();
    if (bulkDeleteTitle !== "Delete selected quote sessions?") {
      throw new Error(`Unexpected bulk delete confirmation title: ${bulkDeleteTitle}`);
    }
    const bulkDeleteCopy = await page.locator("#quoteSessionDeleteText").innerText();
    if (bulkDeleteCopy !== "This removes the selected local dashboard records and any saved local exports for those quote sessions. This cannot be undone.") {
      throw new Error(`Unexpected bulk delete confirmation copy: ${bulkDeleteCopy}`);
    }
    await page.locator("#cancelQuoteSessionDeleteButton").click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator('[data-dashboard-panel-action="delete-selected"]', { hasText: "Delete selected" }).click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    await page.locator("#confirmQuoteSessionDeleteButton").click();
    await page.waitForFunction(() => document.querySelectorAll(".dashboard-session-card").length === 0, null, { timeout: 15000 });
    await page.locator("#dashboardSearchInput").fill("");
    await currentDashboardCard.waitFor({ state: "visible", timeout: 15000 });
    await currentDashboardCard.click();
    await page.locator('[data-dashboard-panel-action="delete-session"]', { hasText: "Delete session" }).click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const singleDeleteCopy = await page.locator("#quoteSessionDeleteText").innerText();
    if (singleDeleteCopy !== "This removes the local dashboard record and any saved local exports for this quote session. This cannot be undone.") {
      throw new Error(`Unexpected single delete confirmation copy: ${singleDeleteCopy}`);
    }
    await page.keyboard.press("Enter");
    await currentDashboardCard.waitFor({ state: "detached", timeout: 15000 });
    await page.locator("#newQuoteButton:not([disabled])").click();
    await page.locator("#imageIntake").waitFor({ state: "visible", timeout: 15000 });
    await verifyMobileHeaderOrder(page);
    await verifyMobileBasisLegendAndOutputCards(page);
    await verifyConfirmBasisSurvivesImmediateRefresh(page);
    await verifyGenerationLoadingModalSurvivesRefresh(page);
    await verifyGenerationTerminalRecoveryAfterRefresh(page);
    sqag212Isolation.downstreamRequestAccounting = verifyDownstreamQuoteSessionAccounting(
      mainQuoteSessionResponses,
      downstreamResponseBaseline,
    );

    console.log(JSON.stringify({
      status: "ok",
      url: page.url(),
      screenshots: [
        dashboardShot,
        homeShot,
        customerPricingShot,
        customerShot,
        dashboardSingleSelectedShot,
        dashboardSelectedShot,
        dashboardSelectedMobileShot,
        dashboardDeleteModalShot,
      ].filter(Boolean),
      sqag212Isolation,
      consoleProblems,
      networkProblems,
    }, null, 2));
  } finally {
    await browser.close();
    await stopServer(serverInfo);
  }
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
