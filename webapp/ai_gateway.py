"""One explicit, bounded OpenRouter inference boundary; no model/provider fallback."""
from __future__ import annotations
import copy
import dataclasses
from decimal import Decimal, InvalidOperation
from http.client import HTTPException, HTTPResponse, HTTPSConnection, IncompleteRead
import json
import math
import re
import ssl
import time
import urllib.error
import urllib.request
from types import MappingProxyType
from typing import Any
from webapp.ai_response_contracts import ContractViolation, DRAFT_SYNTHESIS
from webapp.image_observation_contract import (
    IMAGE_OBSERVATIONS,
    admit_image_observations,
    is_image_batch_input,
)

OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"
ROUTE_KEYS = MappingProxyType({
    "draft": "DRAFT", "draft_high_quality": "DRAFT_HIGH_QUALITY",
    "basis_line": "BASIS_LINE", "basis_answer": "BASIS_ANSWER",
    "pricing_import": "PRICING_IMPORT", "pricing_metadata": "PRICING_METADATA",
})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 128 * 1024
MAX_CHUNK_FRAMING_BYTES = 128 * 1024
MAX_REQUEST_BYTES = 300 * 1024 * 1024
MODEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._/-]{0,134}")
SUPPORTED_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
PROVIDER_PATTERN = re.compile(r"[a-z0-9][a-z0-9_/-]{0,99}")

# Closed Responses error semantics. Negative semantics override HTTP status;
# messages and arbitrary upstream metadata never participate in retry decisions.
TRANSIENT_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})
TRANSIENT_ERROR_TYPES = frozenset({"provider_overloaded", "provider_unavailable", "timeout", "server"})
NONRETRY_ERROR_TYPES = frozenset({
    "context_length_exceeded", "max_tokens_exceeded", "token_limit_exceeded", "string_too_long",
    "authentication", "permission_denied", "payment_required", "rate_limit_exceeded",
    "invalid_request", "invalid_prompt", "not_found", "precondition_failed", "payload_too_large", "unprocessable",
    "content_policy_violation", "refusal", "invalid_image", "image_too_large", "image_too_small",
    "unsupported_image_format", "image_not_found", "image_download_failed", "unmapped",
})
SUPPORTED_ERROR_CLASSES = frozenset({"invalid_request_error", "server_error", "rate_limit_error", "authentication_error", "permission_error"})
NONRETRY_ERROR_CODES = frozenset({
    "invalid_value", "invalid_parameter", "invalid_type", "invalid_image", "invalid_file", "unsupported_value",
    "context_length_exceeded", "rate_limit_exceeded", "insufficient_quota", "invalid_prompt",
    "image_content_policy_violation", "permission_denied", "invalid_api_key",
})
SCHEMA_SPECIFIC_CODES = frozenset({"unsupported_schema", "invalid_json_schema",
    "json_schema_not_supported", "unsupported_response_format"})
SCHEMA_ERROR_CODES = SCHEMA_SPECIFIC_CODES | {"unsupported_parameter"}
SCHEMA_COMPATIBLE_MARKERS = SCHEMA_ERROR_CODES | {
    "invalid_request", "invalid_request_error", "invalid_parameter", "invalid_type", "invalid_value", "unsupported_value",
}
SCHEMA_ERROR_PARAMS = frozenset({"text", "text.format", "text.format.type", "text.format.name",
                                "text.format.strict", "text.format.schema", "response_format"})


def _refusal_part(part):
    return isinstance(part, dict) and (part.get("type") == "refusal" or "refusal" in part)


def _response_state(data):
    """Identify explicit refusal or an unfinished result in a parsed reply."""
    if not isinstance(data, dict):
        return None
    if data.get("refusal") is not None or data.get("error_type") == "refusal":
        return "response_refused"
    output = data.get("output")
    unfinished_item = False
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if item.get("type") == "refusal" or (isinstance(content, list) and any(map(_refusal_part, content))):
                return "response_refused"
            if item.get("status") not in (None, "completed"):
                unfinished_item = True
    # A failed envelope can still carry a genuine provider error.
    if (unfinished_item or data.get("incomplete_details") is not None
            or data.get("status") in ("incomplete", "in_progress", "queued", "cancelled")):
        return "response_incomplete"
    return None


def _image_schema_rejected(data):
    if not isinstance(data, dict):
        return False
    error = data.get("error")
    if error is not None and not isinstance(error, dict):
        return False
    fields = error if error is not None else {}
    target = fields.get("param")
    markers = tuple(marker for marker in (data.get("error_type"), fields.get("type"), fields.get("code"))
                    if marker is not None)
    # Any contradictory or unknown machine marker prevents a capability
    # diagnosis. Only the safe projection is retained after this decision.
    if any(not isinstance(marker, str) or marker not in SCHEMA_COMPATIBLE_MARKERS for marker in markers):
        return False
    if target is not None:
        return isinstance(target, str) and target in SCHEMA_ERROR_PARAMS
    return any(marker in SCHEMA_SPECIFIC_CODES for marker in markers)


def schema_rejected(data, error, *, strict_image_observations):
    """Classify machine evidence. Synthesis retains its existing status rule."""
    if error.response_state is not None:
        return False
    if strict_image_observations:
        if data is not None:
            return _image_schema_rejected(data)
        return error.image_schema_rejected
    diagnostics = error.diagnostics
    metadata = draft_provider_error_diagnostics(data)
    return (
        diagnostics.get("http_status") in {400, 404, 405, 415, 422}
        or any(metadata.get(key) in SCHEMA_ERROR_CODES
               for key in ("provider_error_code", "provider_error_type", "provider_error_class"))
        or metadata.get("provider_error_param") in SCHEMA_ERROR_PARAMS
        or diagnostics.get("provider_error_code") in SCHEMA_ERROR_CODES
        or diagnostics.get("provider_error_param") in SCHEMA_ERROR_PARAMS
    )


def http_error_retryable(status, value, *, response_state):
    if status not in TRANSIENT_HTTP_STATUSES or response_state is not None:
        return False
    if not isinstance(value, dict):
        return True
    # Even unrecognised output makes an HTTP error ineligible for replay.
    if any(value.get(key) not in (None, [], "") for key in ("output", "output_text")):
        return False
    canonical = value.get("error_type")
    if canonical is not None and (not isinstance(canonical, str) or canonical not in TRANSIENT_ERROR_TYPES):
        return False
    error = value.get("error")
    if not isinstance(error, dict):
        return True
    error_class = error.get("type")
    code = error.get("code")
    if error_class is not None and error_class != "server_error":
        return False
    if code is not None and code != "server_error" and not (type(code) is int and code in TRANSIENT_HTTP_STATUSES):
        return False
    return True

class GatewayError(RuntimeError):
    def __init__(self, reason, *, boundary="request_validation", attempt=0, http_status=None,
                 retryable=False, response_state=None):
        super().__init__("AI request could not be completed.")
        self.reason = reason
        self.retryable = retryable
        self.response_state = response_state
        self.image_schema_rejected = False
        self.diagnostics = {"failure_boundary": boundary, "attempt_number": attempt}
        if type(http_status) is int and 100 <= http_status <= 599:
            self.diagnostics.update(http_status=http_status, http_status_class=f"{http_status // 100}xx")


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError("duplicate_key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("invalid_number")


class _ProviderFloat(float):
    def __new__(cls, value):
        parsed = super().__new__(cls, value)
        parsed.lexeme = value
        return parsed


def _finite_float(value):
    parsed = _ProviderFloat(value)
    if not math.isfinite(parsed):
        raise ValueError("invalid_number")
    return parsed


def _load_json(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant, parse_float=_finite_float)

@dataclasses.dataclass(frozen=True, repr=False)
class GatewayRoute:
    operation: str
    model: str
    effort: str
    provider: str

@dataclasses.dataclass(frozen=True, repr=False)
class GatewayConfiguration:
    routes: tuple[GatewayRoute, ...]
    timeout_seconds: int
    zdr: bool
    draft_batch_effort: str | None = None
    draft_schema_enabled: bool = False
    draft_image_schema_enabled: bool = False

    @classmethod
    def capture(cls, reader):
        routes = []
        for operation,suffix in ROUTE_KEYS.items():
            model = reader("OPENROUTER_"+suffix+"_MODEL")
            effort = reader("OPENROUTER_"+suffix+"_REASONING_EFFORT")
            if not isinstance(model,str) or not MODEL_PATTERN.fullmatch(model) or model.startswith("openrouter/") or not isinstance(effort,str) or effort not in SUPPORTED_REASONING_EFFORTS:
                raise GatewayError("routing_configuration")
            try:
                raw = reader("OPENROUTER_"+suffix+"_PROVIDER_ONLY")
                if not isinstance(raw,str) or len(raw)>256: raise ValueError()
                providers = _load_json(raw)
            except (ValueError,TypeError,RecursionError):
                raise GatewayError("provider_policy") from None
            if not isinstance(providers,list) or len(providers)!=1 or not isinstance(providers[0],str) or not PROVIDER_PATTERN.fullmatch(providers[0]):
                raise GatewayError("provider_policy")
            routes.append(GatewayRoute(operation,model,effort,providers[0]))
        expected = {"OPENROUTER_REQUIRE_PARAMETERS":"true", "OPENROUTER_ALLOW_FALLBACKS":"false",
                    "OPENROUTER_DATA_COLLECTION":"deny"}
        if any(reader(key) != value for key,value in expected.items()):
            raise GatewayError("provider_policy")
        raw_zdr = reader("OPENROUTER_ZDR")
        if raw_zdr not in ("true", "false"):
            raise GatewayError("provider_policy")
        raw_timeout = reader("OPENROUTER_REQUEST_TIMEOUT_SECONDS")
        if not isinstance(raw_timeout,str) or not re.fullmatch(r"[0-9]{1,4}",raw_timeout) or not 10 <= int(raw_timeout) <= 1800:
            raise GatewayError("timeout_configuration")
        batch_effort = reader("OPENROUTER_DRAFT_BATCH_REASONING_EFFORT")
        if batch_effort is not None and batch_effort != "" and (
                not isinstance(batch_effort, str) or batch_effort not in SUPPORTED_REASONING_EFFORTS):
            raise GatewayError("routing_configuration")
        schema_enabled = reader("OPENROUTER_DRAFT_SCHEMA_ENABLED")
        if schema_enabled not in (None, "", "false", "true"):
            raise GatewayError("response_schema_configuration")
        image_schema_enabled = reader("OPENROUTER_DRAFT_IMAGE_SCHEMA_ENABLED")
        if image_schema_enabled not in (None, "", "false", "true"):
            raise GatewayError("response_schema_configuration")
        return cls(tuple(routes),int(raw_timeout),raw_zdr == "true", batch_effort or None,
                   schema_enabled == "true", image_schema_enabled == "true")

    def route(self,operation):
        route = next((r for r in self.routes if r.operation == operation),None)
        if route is None: raise GatewayError("operation_unknown")
        return route

    def provider_policy(self, operation):
        provider = self.route(operation).provider
        return {"only":[provider], "order":[provider], "allow_fallbacks":False,
                "require_parameters":True, "data_collection":"deny", "zdr":self.zdr}


def safe_draft_error_param(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 100:
        return ""
    if value in {"model", "input", "reasoning", "reasoning.effort"} | SCHEMA_ERROR_PARAMS:
        return value
    if re.fullmatch(r"input(?:\[0\]|\.0)(?:\.(?:role|content)(?:(?:\[[0-9]{1,2}\]|\.[0-9]{1,2})(?:\.(?:type|text|image_url|detail|filename|file_data))?)?)?", value):
        return value
    return ""


def draft_provider_error_diagnostics(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    canonical = value.get("error_type")
    if isinstance(canonical, str) and canonical in TRANSIENT_ERROR_TYPES | NONRETRY_ERROR_TYPES | SCHEMA_ERROR_CODES:
        result["provider_error_type"] = canonical
    error = value.get("error")
    if not isinstance(error, dict):
        return result
    for key, allowed in {
        "type": SUPPORTED_ERROR_CLASSES | SCHEMA_ERROR_CODES,
        "code": NONRETRY_ERROR_CODES | SCHEMA_ERROR_CODES | {"server_error"},
    }.items():
        if isinstance(error.get(key), str) and error[key] in allowed:
            result["provider_error_class" if key == "type" else "provider_error_code"] = error[key]
    param = safe_draft_error_param(error.get("param"))
    if param:
        result["provider_error_param"] = param
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

# HTTP field tokens and quoted chunk extensions, restricted to a bounded line.
_HTTP_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_CHUNK_EXTENSION = rb'[ \t]*;[ \t]*' + _HTTP_TOKEN + (
    rb'(?:[ \t]*=[ \t]*(?:' + _HTTP_TOKEN + rb'|"(?:[\t !#-\[\]-~\x80-\xff]|\\[\t -~\x80-\xff])*"))?'
)
_CHUNK_LINE = re.compile(rb"([0-9a-fA-F]+)(?:" + _CHUNK_EXTENSION + rb")*\r\n")
_FIELD_LINE = re.compile(rb"(" + _HTTP_TOKEN + rb"):[\t -~\x80-\xff]*\r\n")

_STATUS_LINE = re.compile(rb"HTTP/1\.[0-9] ([1-5][0-9]{2})(?: [\t -~\x80-\xff]*)?\r\n")
_MAX_CONTINUE_RESPONSES = 100


class _ResponseHeadReader:
    """Require intact status/header lines while http.client parses and bounds them."""

    def __init__(self, stream):
        self.stream = stream
        self.started = False
        self.status = None
        self.continues = 0

    def readline(self, size):
        try:
            # Buffered readline can raise on a timeout even after consuming bytes.
            # Peek first so a timeout mid-status is never a pre-response failure.
            if not self.started:
                self.started = bool(self.stream.peek(1))
                if not self.started:
                    return b""
            line = self.stream.readline(size)
        except OSError:
            if not self.started:
                raise
            raise GatewayError("response_partial", boundary="provider_response_shape",
                               http_status=self.status) from None
        if len(line) == size:
            # Preserve http.client's line limit and its framing failure path.
            return line
        if not line.endswith(b"\r\n"):
            raise GatewayError("response_partial", boundary="provider_response_shape",
                               http_status=self.status)
        if self.status is None:
            match = _STATUS_LINE.fullmatch(line)
            if match is None:
                raise GatewayError("response_framing", boundary="provider_response_shape")
            self.status = int(match[1])
            if self.status == 100:
                self.continues += 1
                # Python 3.12 bounds each head but can read unlimited interim heads.
                if self.continues >= _MAX_CONTINUE_RESPONSES:
                    raise GatewayError("response_framing", boundary="provider_response_shape", http_status=100)
        elif line == b"\r\n":
            # http.client reads another status after a complete 100 Continue.
            self.status = None
        elif _FIELD_LINE.fullmatch(line) is None:
            raise GatewayError("response_framing", boundary="provider_response_shape",
                               http_status=self.status)
        return line


class _ProviderHttpResponse(HTTPResponse):
    def begin(self):
        stream = self.fp
        self.fp = _ResponseHeadReader(stream)
        try:
            super().begin()
        finally:
            self.fp = stream


class _ProviderHttpsConnection(HTTPSConnection):
    response_class = _ProviderHttpResponse

    def connect(self):
        super().connect()
        # A close-delimited body needs TLS close_notify. Surface unexpected
        # socket EOF as an SSL error before it can look like a complete read.
        self.sock.suppress_ragged_eofs = False


class _ProviderHttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        super().__init__()
        # Keep OpenSSL from treating an unexpected EOF as close_notify.
        self._context.options &= ~getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)

    def https_open(self, req):
        return self.do_open(_ProviderHttpsConnection, req, context=self._context)


class BoundedHttpsTransport:
    @staticmethod
    def _header_values(headers, name):
        if headers is None:
            return []
        if hasattr(headers, "get_all"):
            return headers.get_all(name, [])
        value = headers.get(name)
        return [] if value is None else [value]

    @staticmethod
    def _read_chunked(stream, limit):
        # http.client accepts EOF in place of the final trailer terminator.
        # The stream is positioned at the first chunk after parsing the headers.
        remaining_framing = MAX_CHUNK_FRAMING_BYTES

        def framing_line():
            nonlocal remaining_framing
            line = stream.readline(min(8192, remaining_framing) + 1)
            remaining_framing -= len(line)
            if remaining_framing < 0 or len(line) > 8192 or not line.endswith(b"\r\n"):
                raise IncompleteRead(b"")
            return line

        body = bytearray()
        while True:
            match = _CHUNK_LINE.fullmatch(framing_line())
            if match is None:
                raise IncompleteRead(b"")
            size = int(match[1], 16)
            if not size:
                break
            wanted = min(size, limit + 1 - len(body))
            chunk = stream.read(wanted)
            if len(chunk) != wanted:
                raise IncompleteRead(b"")
            body.extend(chunk)
            if len(body) > limit:
                return body
            if stream.read(2) != b"\r\n":
                raise IncompleteRead(b"")
            remaining_framing -= 2
        while (line := framing_line()) != b"\r\n":
            field = _FIELD_LINE.fullmatch(line)
            if field is None or field[1].lower() in {b"content-length", b"transfer-encoding"}:
                raise IncompleteRead(b"")
        return body

    @classmethod
    def _read_body(cls, response, limit, *, http_status=None):
        headers = response.headers
        lengths = cls._header_values(headers, "Content-Length")
        encodings = cls._header_values(headers, "Transfer-Encoding")
        wire = response.fp if isinstance(response, urllib.error.HTTPError) else response
        if len(lengths) > 1 or (encodings and (
            lengths or len(encodings) != 1 or not isinstance(encodings[0], str)
            or encodings[0].strip(" \t").lower() != "chunked"
            or not isinstance(wire, HTTPResponse) or wire.version != 11
        )):
            raise GatewayError("response_framing", boundary="provider_response_shape", http_status=http_status)
        declared = None
        if lengths:
            length = lengths[0].strip(" \t") if isinstance(lengths[0], str) else ""
            if not re.fullmatch(r"[0-9]{1,12}", length):
                raise GatewayError("response_framing", boundary="provider_response_shape", http_status=http_status)
            declared = int(length)
        try:
            if encodings:
                if wire.fp is None:
                    raise IncompleteRead(b"")
                raw = cls._read_chunked(wire.fp, limit)
            else:
                raw = response.read(limit + 1)
        except (HTTPException, OSError):
            raise GatewayError("response_partial", boundary="provider_response_shape",
                               http_status=http_status) from None
        # A short read is incomplete. At the sentinel the rest may be unread.
        if declared is not None and len(raw) < min(declared, limit + 1):
            raise GatewayError("response_partial", boundary="provider_response_shape", http_status=http_status)
        if len(raw) > limit or (declared is not None and declared > limit):
            raise GatewayError("response_size", boundary="provider_response_shape", http_status=http_status)
        if declared is not None and len(raw) != declared:
            raise GatewayError("response_partial", boundary="provider_response_shape", http_status=http_status)
        return raw

    def __init__(self, *, opener=None):
        self._opener = opener if opener is not None else urllib.request.build_opener(
            _NoRedirect(), _ProviderHttpsHandler(),
        )

    def send(self, body, *, credential, timeout):
        request = urllib.request.Request(OPENROUTER_RESPONSES_URL,data=body,
            headers={"Authorization":"Bearer "+credential,"Content-Type":"application/json","Accept":"application/json"},method="POST")
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # urllib exposes complete response heads through either return path.
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = GatewayError("connection_failed", boundary="provider_transport", retryable=True)
            error.diagnostics["transport_timeout"] = isinstance(exc, TimeoutError) or isinstance(
                getattr(exc, "reason", None), TimeoutError,
            )
            raise error from None
        except HTTPException:
            raise GatewayError("response_framing", boundary="provider_response_shape") from None

        http_error = isinstance(response, urllib.error.HTTPError)
        status = response.code if http_error else None
        limit = MAX_ERROR_BODY_BYTES if http_error else MAX_RESPONSE_BYTES
        try:
            with response:
                if not http_error:
                    if response.status != 200 or response.geturl() != OPENROUTER_RESPONSES_URL:
                        raise GatewayError("redirect_or_status", boundary="provider_http")
                    if response.headers.get_content_type().lower() != "application/json":
                        raise GatewayError("response_mime", boundary="provider_response_json")
                raw = self._read_body(response, limit, http_status=status)
                try:
                    parsed = _load_json(raw.decode("utf-8"))
                except (ValueError, RecursionError):
                    if not http_error:
                        raise GatewayError("response_invalid", boundary="provider_response_json") from None
                    # A fully framed HTTP error may have no JSON diagnostics.
                    parsed = None
        except (TypeError, OSError):
            raise GatewayError("response_invalid", boundary="provider_response_json",
                               http_status=status) from None

        if not http_error:
            return parsed
        response_state = _response_state(parsed)
        error = GatewayError("http_error", boundary="provider_http", http_status=status,
                             retryable=http_error_retryable(status, parsed, response_state=response_state),
                             response_state=response_state)
        error.image_schema_rejected = status >= 400 and response_state is None and _image_schema_rejected(parsed)
        error.diagnostics.update(draft_provider_error_diagnostics(parsed))
        error.diagnostics.update(_response_state_diagnostics(parsed, response_state))
        # Keep the billing fields out of diagnostics and business output.
        if isinstance(parsed, dict):
            error.reported_cost = (parsed.get("model"), _response_cost(parsed))
        raise error from None


def admitted_usage(value):
    if value is None: return None
    if not isinstance(value,dict): return None
    result={}
    for key in ("input_tokens","output_tokens","total_tokens"):
        item=value.get(key)
        if item is not None:
            if type(item) is not int or not 0<=item<=2**53-1: return None
            result[key]=item
    if all(k in result for k in ("input_tokens","output_tokens","total_tokens")) and result["input_tokens"]+result["output_tokens"]!=result["total_tokens"]:
        return None
    details=value.get("output_tokens_details")
    if isinstance(details,dict) and "reasoning_tokens" in details:
        reasoning=details["reasoning_tokens"]
        if type(reasoning) is not int or not 0<=reasoning<=result.get("output_tokens",2**53-1): return None
        result["output_tokens_details"]={"reasoning_tokens":reasoning}
    return result or None


def _response_cost(response):
    usage = response.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, _ProviderFloat):
        try:
            cost = Decimal(cost.lexeme)
        except InvalidOperation:
            return None
    if isinstance(cost, Decimal) and not cost.is_finite():
        return None
    if type(cost) in (int, float, Decimal) and 0 <= cost <= 2**53 - 1:
        return cost
    return None


def reported_response_cost_usd(response, model):
    """Admit only the optional USD cost for this request's configured model."""
    if isinstance(response, dict) and response.get("model") == model:
        return _response_cost(response)
    return None


def _response_state_diagnostics(data, response_state):
    if not isinstance(data, dict):
        return {}
    result = {"refusal_present": True} if response_state == "response_refused" else {}
    incomplete = data.get("incomplete_details")
    if isinstance(incomplete, dict) and incomplete.get("reason") in ("max_output_tokens", "content_filter"):
        result["incomplete_reason"] = incomplete["reason"]
    status = data.get("status")
    if status in ("completed", "failed", "incomplete", "in_progress", "queued", "cancelled"):
        result["responses_status"] = status
    return result


def safe_response_diagnostics(data, response_state):
    if not isinstance(data, dict):
        return {}
    output = data.get("output")
    items = output if isinstance(output, list) else []
    texts = [part.get("text") for item in items if isinstance(item, dict)
             for part in (item.get("content") if isinstance(item.get("content"), list) else [])
             if isinstance(part, dict) and isinstance(part.get("text"), str)]
    combined = "".join(texts)
    result = {"output_item_count": len(items), "output_text_count": len(texts),
              "output_length": len(combined), "output_empty": not bool(combined.strip()),
              "output_contains_json_object_bounds": "{" in combined and "}" in combined}
    result.update(output_item_present=bool(items), output_text_present=bool(texts), refusal_present=False)
    result.update(_response_state_diagnostics(data, response_state))
    result.update(draft_provider_error_diagnostics(data))
    return result


def admit_response(data,route, *, strict_image_observations=False):
    response_state = _response_state(data)
    if response_state is not None:
        raise GatewayError(response_state, boundary="provider_response_contract", response_state=response_state)
    if not isinstance(data,dict) or not isinstance(data.get("status"),str):
        raise GatewayError("response_shape",boundary="provider_response_shape")
    if data.get("error") is not None or data.get("error_type") is not None or data["status"]!="completed":
        raise GatewayError("response_incomplete",boundary="provider_response_contract")
    if data.get("model") != route.model or ("output_text" in data and not isinstance(data["output_text"], str)):
        raise GatewayError("response_model_mismatch",boundary="provider_response_shape")
    output=data.get("output")
    if not isinstance(output,list) or not output:
        raise GatewayError("response_empty",boundary="provider_response_shape")
    texts=[]
    for item in output:
        if not isinstance(item,dict): raise GatewayError("response_shape",boundary="provider_response_shape")
        if item.get("type")=="reasoning": continue
        if item.get("type")!="message" or item.get("role")!="assistant" or not isinstance(item.get("content"),list):
            raise GatewayError("response_shape",boundary="provider_response_shape")
        for part in item["content"]:
            if not isinstance(part,dict):
                raise GatewayError("response_shape",boundary="provider_response_shape")
            if part.get("type")!="output_text" or not isinstance(part.get("text"),str):
                raise GatewayError("response_shape",boundary="provider_response_shape")
            if not part["text"].strip():
                raise GatewayError("response_empty",boundary="provider_response_contract")
            texts.append(part["text"])
    if len(texts)!=1 or ("output_text" in data and data["output_text"]!=texts[0]):
        raise GatewayError("response_mixed",boundary="provider_response_contract")
    if strict_image_observations:
        try:
            admit_image_observations(texts[0])
        except ContractViolation as exc:
            error = GatewayError("response_image_observations", boundary="provider_response_contract")
            error.diagnostics.update(
                response_contract_reason=exc.reason,
                batch_failure_reason={
                    "response_too_large": "batch_output_limit",
                    "schema_shape": "batch_output_coverage",
                }.get(exc.reason, "batch_provider_failure"),
            )
            raise error from None
    # The caller still performs business admission.
    result={"status":"completed","output":copy.deepcopy(output),"model":route.model}
    usage=admitted_usage(data.get("usage"))
    if usage is not None: result["usage"]=usage
    return result


class AiGateway:
    """Own one immutable operation configuration, credential access, sends and admission."""
    def __init__(self, configuration, *, credential_reader, transport=None, clock=time.perf_counter, sleep=time.sleep, telemetry=None, operation_owner=None, quote_spend=None):
        self.configuration=configuration
        self.operation_owner=operation_owner
        self._credential_reader=credential_reader
        self._transport=transport if transport is not None else BoundedHttpsTransport()
        self._clock,self._sleep=clock,sleep
        self._telemetry=telemetry
        self._quote_spend = quote_spend

    def request(self,operation,envelope, *, send_budget=None, draft_batch=False, response_contract=None):
        route=self.configuration.route(operation)
        if draft_batch:
            if draft_batch is not True or operation not in {"draft", "draft_high_quality"}:
                raise GatewayError("envelope_invalid")
            effort = self.configuration.draft_batch_effort
            if effort is not None:
                if not isinstance(effort, str) or effort not in SUPPORTED_REASONING_EFFORTS:
                    raise GatewayError("routing_configuration")
                route = dataclasses.replace(route, effort=effort)
        contract = DRAFT_SYNTHESIS if (self.configuration.draft_schema_enabled
                    and operation in {"draft", "draft_high_quality"} and not draft_batch) else None
        if response_contract != (contract.name if contract else None):
            raise GatewayError("envelope_invalid")
        if not isinstance(envelope,dict) or set(envelope)-{"model","input","reasoning","max_output_tokens","text"} or envelope.get("model")!=route.model or envelope.get("reasoning")!={"effort":route.effort} or not isinstance(envelope.get("input"),list) or not envelope["input"]:
            raise GatewayError("envelope_invalid")
        if ((contract is not None and not contract.matches_text_format(envelope.get("text")))
                or (contract is None and "text" in envelope)):
            raise GatewayError("envelope_invalid")
        strict_image_observations = draft_batch is True and self.configuration.draft_image_schema_enabled
        if strict_image_observations:
            if set(envelope) != {"model", "input", "reasoning"} or not is_image_batch_input(envelope["input"]):
                raise GatewayError("envelope_invalid")
            contract = IMAGE_OBSERVATIONS
        body=copy.deepcopy(envelope)
        if strict_image_observations:
            body["text"] = contract.text_format()
        body["provider"]=self.configuration.provider_policy(operation)
        body["store"]=False
        try:
            encoded=json.dumps(body,allow_nan=False).encode("utf-8")
        except (TypeError,ValueError,UnicodeError,RecursionError):
            raise GatewayError("envelope_invalid") from None
        if len(encoded)>MAX_REQUEST_BYTES: raise GatewayError("request_size")
        credential=self._credential_reader()
        if not isinstance(credential,str) or not credential or len(credential)>4096 or any(c.isspace() or ord(c)<32 or ord(c)>126 for c in credential):
            raise GatewayError("credential_missing")
        delays=(2.0,5.0) if operation in {"basis_line","basis_answer"} else ()
        spend = self._quote_spend
        if spend is not None and not spend.tracks(operation):
            spend = None
        for attempt in range(1,len(delays)+2):
            start=self._clock()
            data = None
            if send_budget is not None:
                send_budget.claim(operation, route.model)
            ordinal = spend.reserve() if spend is not None else None
            cost = None
            try:
                try:
                    data = self._transport.send(encoded, credential=credential, timeout=self.configuration.timeout_seconds)
                    cost = reported_response_cost_usd(data, route.model)
                    result = admit_response(data, route, strict_image_observations=strict_image_observations)
                except GatewayError as exc:
                    billed_model, billed_cost = getattr(exc, "reported_cost", (None, None))
                    if billed_model == route.model:
                        cost = billed_cost
                    raise
                finally:
                    if spend is not None:
                        spend.settle(ordinal, cost)
            except GatewayError as exc:
                if strict_image_observations:
                    exc.diagnostics.setdefault("response_contract_name", contract.name)
                    exc.diagnostics.setdefault("response_contract_reason", exc.reason)
                    exc.diagnostics.setdefault("batch_failure_reason", "batch_provider_failure")
                if (contract is not None
                        and exc.reason not in {"response_partial", "response_size", "response_framing", "redirect_or_status"}
                        and schema_rejected(data, exc,
                                            strict_image_observations=strict_image_observations)):
                    exc.reason = "response_schema_rejected"
                    exc.retryable = False
                    exc.diagnostics.update(failure_boundary="provider_response_schema",
                                           response_contract_reason="response_schema_rejected",
                                           response_contract_name=contract.name)
                exc.diagnostics.update(safe_response_diagnostics(data, exc.response_state))
                usage = admitted_usage(data.get("usage")) if isinstance(data, dict) else None
                if usage is not None:
                    exc.diagnostics.update({key:value for key,value in usage.items() if key != "output_tokens_details"})
                exc.diagnostics["attempt_number"]=attempt
                if send_budget is not None:
                    send_budget.finish(usage, cost_usd=float(cost) if cost is not None else None)
                if self._telemetry is not None:
                    self._telemetry(operation,attempt,"failed",self._clock()-start,{**exc.diagnostics, "usage":usage, "retry_pending":exc.retryable and attempt<=len(delays)})
                if exc.retryable and attempt<=len(delays):
                    self._sleep(delays[attempt-1]); continue
                raise
            except Exception:
                if send_budget is not None:
                    send_budget.finish(None)
                    if self._telemetry is not None:
                        self._telemetry(operation,attempt,"failed",self._clock()-start,{
                            "failure_boundary":"provider_transport", "usage":None, "retry_pending":False,
                        })
                raise
            usage = admitted_usage(result.get("usage"))
            if send_budget is not None:
                send_budget.finish(usage, cost_usd=float(cost) if cost is not None else None)
            if self._telemetry is not None:
                self._telemetry(operation,attempt,"success",self._clock()-start,{"usage":usage, "retry_pending":False})
            return result
