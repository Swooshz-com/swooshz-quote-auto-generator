"""Compare a generated API spec with its released baseline and list the changes that break callers.

Allowed: a new operation, a new optional request field, a new response field, a new enum
value, a higher limit, a looser request bound. Blocked: a removed or renamed operation,
path, parameter, response or field; a request field becoming required (or a response
field optional); a changed type, format, constant or pattern; a removed enum value or
schema variant (a new variant in a response); a tighter request bound or lower limit;
a changed or removed error code status; a changed money rule; a scope losing operations.
Operations marked preview in the baseline are outside the promise. Works for any spec
pair (the v1 API now, the Platform contract later). Standard library only.
"""
from __future__ import annotations

from webapp.api_schema import resolve

_LOWER_BOUNDS = ("minLength", "minItems", "minimum", "exclusiveMinimum")
_UPPER_BOUNDS = ("maxLength", "maxItems", "maximum")


class _Walk:
    """One comparison: both spec roots, the breaks found, and the node pairs already seen."""

    def __init__(self, baseline, current):
        self.baseline, self.current = baseline, current
        self.breaks: list[str] = []
        self._seen: set[tuple[int, int, str]] = set()

    def add(self, where, text):
        self.breaks.append(f"{where}: {text}")

    def schema(self, old, new, side, where):
        old, new = self._resolve(old, self.baseline), self._resolve(new, self.current)
        key = (id(old), id(new), side)
        if key in self._seen:
            return
        self._seen.add(key)
        for keyword in ("type", "format", "const", "pattern"):
            if old.get(keyword) != new.get(keyword):
                self.add(where, f"{keyword} changed")
        if "enum" in old:
            removed = [v for v in old["enum"] if v not in new.get("enum", [])]
            if removed or "enum" not in new:
                self.add(where, "enum values removed: " + ", ".join(map(str, removed or old["enum"])))
        if side == "request":
            for bound in _LOWER_BOUNDS:
                if bound in new and new[bound] > old.get(bound, float("-inf")):
                    self.add(where, f"{bound} tightened")
            for bound in _UPPER_BOUNDS:
                if bound in new and new[bound] < old.get(bound, float("inf")):
                    self.add(where, f"{bound} tightened")
            if new.get("uniqueItems") and not old.get("uniqueItems"):
                self.add(where, "uniqueItems added")
        for keyword in ("anyOf", "oneOf"):
            old_variants, new_variants = old.get(keyword, []), new.get(keyword, [])
            if len(new_variants) < len(old_variants):
                self.add(where, f"{keyword} variant removed")
            elif side == "response" and len(new_variants) > len(old_variants):
                self.add(where, f"{keyword} variant added to a response")
            for index, (left, right) in enumerate(zip(old_variants, new_variants)):
                self.schema(left, right, side, f"{where}/{keyword}[{index}]")
        if "properties" in old:
            new_props = new.get("properties", {})
            for name, child in old["properties"].items():
                if name not in new_props:
                    self.add(where, f"field {name} removed")
                else:
                    self.schema(child, new_props[name], side, f"{where}.{name}")
            old_required, new_required = set(old.get("required", [])), set(new.get("required", []))
            if side == "request":
                for name in sorted(new_required - old_required):
                    self.add(where, f"field {name} became required")
            else:
                for name in sorted((old_required - new_required) & set(new_props)):
                    self.add(where, f"field {name} became optional")
        if "items" in old:
            if "items" not in new:
                self.add(where, "items removed")
            else:
                self.schema(old["items"], new["items"], side, f"{where}[]")

    def _resolve(self, node, root):
        while isinstance(node, dict) and "$ref" in node:
            node = resolve(root, node["$ref"])
        return node if isinstance(node, dict) else {}

    def operation(self, method, path, old, new):
        where = f"{method.upper()} {path}"
        if new.get("x-stability") == "preview":
            self.add(where, "stable operation moved to preview")
        if old.get("operationId") != new.get("operationId"):
            self.add(where, "operationId changed")
        new_params = {(p.get("name"), p.get("in")): p for p in map(self._param_current, new.get("parameters", []))}
        for param in map(self._param_baseline, old.get("parameters", [])):
            key = (param.get("name"), param.get("in"))
            if key not in new_params:
                self.add(where, f"parameter {key[0]} removed")
            else:
                self.schema(param.get("schema", {}), new_params[key].get("schema", {}), "request",
                            f"{where} parameter {key[0]}")
        old_body = old.get("requestBody", {}).get("content", {})
        new_body = new.get("requestBody", {}).get("content", {})
        if new.get("requestBody", {}).get("required") and not old.get("requestBody", {}).get("required"):
            self.add(where, "request body became required")
        for media, content in old_body.items():
            if media not in new_body:
                self.add(where, f"request media type {media} removed")
            else:
                self.schema(content.get("schema", {}), new_body[media].get("schema", {}), "request", f"{where} body")
        new_responses = new.get("responses", {})
        for status, response in old.get("responses", {}).items():
            if status not in new_responses:
                self.add(where, f"response {status} removed")
                continue
            old_content = self._resolve(response, self.baseline).get("content", {})
            new_content = self._resolve(new_responses[status], self.current).get("content", {})
            for media, content in old_content.items():
                if media not in new_content:
                    self.add(where, f"response {status} media type {media} removed")
                else:
                    self.schema(content.get("schema", {}), new_content[media].get("schema", {}), "response",
                                f"{where} {status}")

    def _param_baseline(self, param):
        return self._resolve(param, self.baseline)

    def _param_current(self, param):
        return self._resolve(param, self.current)


def compatibility_breaks(baseline: dict, current: dict) -> list[str]:
    """Every change in ``current`` that breaks a caller of ``baseline``; empty when compatible."""
    walk = _Walk(baseline, current)
    current_paths = current.get("paths", {})
    for path, methods in baseline.get("paths", {}).items():
        for method, operation in methods.items():
            if operation.get("x-stability") == "preview":
                continue
            new = current_paths.get(path, {}).get(method)
            if new is None:
                walk.add(f"{method.upper()} {path}", "operation removed")
            else:
                walk.operation(method, path, operation, new)
    new_status = {code: status for status, codes in current.get("x-error-status-codes", {}).items() for code in codes}
    for status, codes in baseline.get("x-error-status-codes", {}).items():
        for code in codes:
            if new_status.get(code) != status:
                walk.add(f"error {code}", f"status {status} changed to {new_status.get(code, 'removed')}")
    if baseline.get("x-money-policy") != current.get("x-money-policy"):
        walk.add("x-money-policy", "money or rounding rule changed")
    new_limits = current.get("x-limits", {})
    for name, value in baseline.get("x-limits", {}).items():
        if name not in new_limits or new_limits[name] < value:
            walk.add(f"limit {name}", "lowered or removed")
    new_scopes = current.get("x-scopes", {})
    for scope, operations in baseline.get("x-scopes", {}).items():
        lost = sorted(set(operations) - set(new_scopes.get(scope, [])))
        if lost:
            walk.add(f"scope {scope}", "lost operations " + ", ".join(lost))
    return walk.breaks
