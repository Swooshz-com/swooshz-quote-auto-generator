"""One explicit, bounded OpenRouter inference boundary; no model/provider fallback."""
from __future__ import annotations
import copy
import dataclasses
import json
import math
import re
import time
import urllib.error
import urllib.request
from types import MappingProxyType
from typing import Any

OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"
ROUTE_KEYS = MappingProxyType({
    "draft": "DRAFT", "draft_high_quality": "DRAFT_HIGH_QUALITY",
    "basis_line": "BASIS_LINE", "basis_answer": "BASIS_ANSWER",
    "pricing_import": "PRICING_IMPORT", "pricing_metadata": "PRICING_METADATA",
})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
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


def http_error_retryable(status, value):
    if status not in TRANSIENT_HTTP_STATUSES:
        return False
    if not isinstance(value, dict):
        return True
    # No HTTP error is replayed once any output or explicit response refusal/
    # incompleteness is present, even if its outer status looks transient.
    if any(value.get(key) is not None and value.get(key) != [] and value.get(key) != ""
           for key in ("output", "output_text", "refusal", "incomplete_details")):
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
    def __init__(self, reason, *, boundary="request_validation", attempt=0, http_status=None, retryable=False):
        super().__init__("AI request could not be completed.")
        self.reason = reason
        self.retryable = retryable
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


def _finite_float(value):
    parsed = float(value)
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
                    "OPENROUTER_DATA_COLLECTION":"deny", "OPENROUTER_ZDR":"true"}
        if any(reader(key) != value for key,value in expected.items()):
            raise GatewayError("provider_policy")
        raw_timeout = reader("OPENROUTER_REQUEST_TIMEOUT_SECONDS")
        if not isinstance(raw_timeout,str) or not re.fullmatch(r"[0-9]{1,4}",raw_timeout) or not 10 <= int(raw_timeout) <= 1800:
            raise GatewayError("timeout_configuration")
        return cls(tuple(routes),int(raw_timeout))

    def route(self,operation):
        route = next((r for r in self.routes if r.operation == operation),None)
        if route is None: raise GatewayError("operation_unknown")
        return route

    def provider_policy(self, operation):
        provider = self.route(operation).provider
        return {"only":[provider], "order":[provider], "allow_fallbacks":False,
                "require_parameters":True, "data_collection":"deny", "zdr":True}


def safe_draft_error_param(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 100:
        return ""
    if value in {"model", "input", "reasoning", "reasoning.effort"}:
        return value
    if re.fullmatch(r"input(?:\[0\]|\.0)(?:\.(?:role|content)(?:(?:\[[0-9]{1,2}\]|\.[0-9]{1,2})(?:\.(?:type|text|image_url|detail|filename|file_data))?)?)?", value):
        return value
    return ""


def draft_provider_error_diagnostics(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    canonical = value.get("error_type")
    if isinstance(canonical, str) and canonical in TRANSIENT_ERROR_TYPES | NONRETRY_ERROR_TYPES:
        result["provider_error_type"] = canonical
    error = value.get("error")
    if not isinstance(error, dict):
        return result
    for key, allowed in {
        "type": SUPPORTED_ERROR_CLASSES,
        "code": NONRETRY_ERROR_CODES | {"server_error"},
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

class BoundedHttpsTransport:
    def __init__(self, *, opener=None):
        self._opener = opener if opener is not None else urllib.request.build_opener(_NoRedirect())

    def send(self, body, *, credential, timeout):
        request = urllib.request.Request(OPENROUTER_RESPONSES_URL,data=body,
            headers={"Authorization":"Bearer "+credential,"Content-Type":"application/json","Accept":"application/json"},method="POST")
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            metadata = {}
            parsed = None
            try:
                raw = exc.read(128 * 1024 + 1)
                if len(raw) <= 128 * 1024:
                    parsed = _load_json(raw.decode("utf-8"))
                    metadata = draft_provider_error_diagnostics(parsed)
            except (AttributeError, OSError, ValueError, TypeError, UnicodeError, RecursionError):
                # Diagnostics are optional; the failed HTTP response remains a failure.
                metadata = {}
            finally:
                exc.close()
            error = GatewayError("http_error", boundary="provider_http", http_status=status,
                                 retryable=http_error_retryable(status, parsed))
            error.diagnostics.update(metadata)
            raise error from None
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            error = GatewayError("connection_failed",boundary="provider_transport",retryable=True)
            error.diagnostics["transport_timeout"] = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
            raise error from None
        # Once a response has begun, never replay it after a read/shape/partial failure.
        try:
            with response:
                if response.status != 200 or response.geturl() != OPENROUTER_RESPONSES_URL:
                    raise GatewayError("redirect_or_status",boundary="provider_http")
                if response.headers.get_content_type().lower() != "application/json":
                    raise GatewayError("response_mime",boundary="provider_response_json")
                length=response.headers.get("Content-Length")
                if length is not None and (not re.fullmatch(r"[0-9]{1,12}",length) or int(length)>MAX_RESPONSE_BYTES):
                    raise GatewayError("response_size",boundary="provider_response_shape")
                raw=response.read(MAX_RESPONSE_BYTES+1)
                if len(raw)>MAX_RESPONSE_BYTES:
                    raise GatewayError("response_size",boundary="provider_response_shape")
                if length is not None and len(raw)!=int(length):
                    raise GatewayError("response_partial",boundary="provider_response_shape")
                result=_load_json(raw.decode("utf-8"))
        except GatewayError:
            raise
        except (ValueError,TypeError,UnicodeError,RecursionError,OSError,TimeoutError,urllib.error.URLError):
            raise GatewayError("response_invalid",boundary="provider_response_json") from None
        return result


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


def reported_response_cost_usd(response, model):
    """Admit only the optional USD cost for this request's configured model."""
    if not isinstance(response, dict) or response.get("model") != model:
        return None
    usage = response.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if type(cost) in (int, float) and 0 <= cost <= 2**53 - 1:
        return cost
    return None


def safe_response_diagnostics(data):
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
    result.update(output_item_present=bool(items), output_text_present=bool(texts),
                  refusal_present=data.get("refusal") is not None or any(
                      isinstance(item, dict) and (item.get("type") == "refusal" or any(
                          isinstance(part, dict) and (part.get("type") == "refusal" or "refusal" in part)
                          for part in (item.get("content") if isinstance(item.get("content"), list) else [])))
                      for item in items))
    incomplete = data.get("incomplete_details")
    if isinstance(incomplete, dict) and isinstance(incomplete.get("reason"), str) and incomplete["reason"] in {"max_output_tokens", "content_filter"}:
        result["incomplete_reason"] = incomplete["reason"]
    if isinstance(data.get("status"), str) and data["status"] in {"completed", "failed", "incomplete", "in_progress", "queued", "cancelled"}:
        result["responses_status"] = data["status"]
    result.update(draft_provider_error_diagnostics(data))
    return result


def admit_response(data,route):
    if not isinstance(data,dict) or not isinstance(data.get("status"),str):
        raise GatewayError("response_shape",boundary="provider_response_shape")
    if data.get("error") is not None or data.get("error_type") is not None or data["status"]!="completed" or data.get("refusal") is not None or data.get("incomplete_details") is not None:
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
        if item.get("type")=="refusal" or item.get("status") not in (None,"completed"):
            raise GatewayError("response_refused",boundary="provider_response_contract")
        if item.get("type")!="message" or item.get("role")!="assistant" or not isinstance(item.get("content"),list):
            raise GatewayError("response_shape",boundary="provider_response_shape")
        for part in item["content"]:
            if not isinstance(part,dict):
                raise GatewayError("response_shape",boundary="provider_response_shape")
            if part.get("type")=="refusal" or "refusal" in part:
                raise GatewayError("response_refused",boundary="provider_response_contract")
            if part.get("type")!="output_text" or not isinstance(part.get("text"),str):
                raise GatewayError("response_shape",boundary="provider_response_shape")
            if not part["text"].strip():
                raise GatewayError("response_empty",boundary="provider_response_contract")
            texts.append(part["text"])
    if len(texts)!=1 or ("output_text" in data and data["output_text"]!=texts[0]):
        raise GatewayError("response_mixed",boundary="provider_response_contract")
    # The caller still performs canonical JSON and business admission.
    result={"status":"completed","output":copy.deepcopy(output),"model":route.model}
    usage=admitted_usage(data.get("usage"))
    if usage is not None: result["usage"]=usage
    return result


class AiGateway:
    """Own one immutable operation configuration, credential access, sends and admission."""
    def __init__(self, configuration, *, credential_reader, transport=None, clock=time.perf_counter, sleep=time.sleep, telemetry=None, operation_owner=None):
        self.configuration=configuration
        self.operation_owner=operation_owner
        self._credential_reader=credential_reader
        self._transport=transport if transport is not None else BoundedHttpsTransport()
        self._clock,self._sleep=clock,sleep
        self._telemetry=telemetry

    def request(self,operation,envelope, *, send_budget=None):
        route=self.configuration.route(operation)
        if not isinstance(envelope,dict) or set(envelope)-{"model","input","reasoning","max_output_tokens"} or envelope.get("model")!=route.model or envelope.get("reasoning")!={"effort":route.effort} or not isinstance(envelope.get("input"),list) or not envelope["input"]:
            raise GatewayError("envelope_invalid")
        body=copy.deepcopy(envelope)
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
        for attempt in range(1,len(delays)+2):
            start=self._clock()
            data = None
            if send_budget is not None:
                send_budget.claim(operation, route.model)
            try:
                data=self._transport.send(encoded,credential=credential,timeout=self.configuration.timeout_seconds)
                result=admit_response(data,route)
            except GatewayError as exc:
                exc.diagnostics.update(safe_response_diagnostics(data))
                usage = admitted_usage(data.get("usage")) if isinstance(data, dict) else None
                if usage is not None:
                    exc.diagnostics.update({key:value for key,value in usage.items() if key != "output_tokens_details"})
                exc.diagnostics["attempt_number"]=attempt
                if send_budget is not None:
                    send_budget.finish(usage, cost_usd=reported_response_cost_usd(data, route.model))
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
                send_budget.finish(usage, cost_usd=reported_response_cost_usd(data, route.model))
            if self._telemetry is not None:
                self._telemetry(operation,attempt,"success",self._clock()-start,{"usage":usage, "retry_pending":False})
            return result
