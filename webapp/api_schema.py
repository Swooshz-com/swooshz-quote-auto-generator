"""Small fail-closed JSON Schema checker for exactly the subset the Quotation API spec uses.

It refuses unknown keywords instead of pretending to implement full OpenAPI or
JSON Schema validation. Money, media and authority rules are separate checks: a
string pattern cannot establish them. Standard library only.
"""
from datetime import datetime
import json
import math
import re


SUPPORTED_KEYWORDS = frozenset({
    "$ref", "type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
    "uniqueItems", "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum", "enum",
    "const", "anyOf", "oneOf", "format", "examples", "description",
})
_TYPES = {
    "object": lambda value: type(value) is dict,
    "array": lambda value: type(value) is list,
    "string": lambda value: type(value) is str,
    "integer": lambda value: type(value) is int,
    "number": lambda value: type(value) in (int, float) and math.isfinite(value),
    "boolean": lambda value: type(value) is bool,
    "null": lambda value: value is None,
}


class SchemaViolation(ValueError):
    """A value does not match its schema."""


def resolve(root, reference):
    """Follow a local "#/..." reference inside ``root``."""
    if type(reference) is not str or not reference.startswith("#/"):
        raise SchemaViolation("Unsupported reference")
    target = root
    for part in reference.removeprefix("#/").split("/"):
        if type(target) is not dict or part not in target:
            raise SchemaViolation("Unknown reference")
        target = target[part]
    return target


def check(schema, value, *, root):
    """Raise SchemaViolation unless ``value`` matches ``schema``; references resolve in ``root``."""
    if type(schema) is not dict or set(schema) - SUPPORTED_KEYWORDS:
        raise SchemaViolation("Unsupported schema keyword")
    if "$ref" in schema:
        check(resolve(root, schema["$ref"]), value, root=root)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for variant in schema[keyword]:
                try:
                    check(variant, value, root=root)
                    matches += 1
                except SchemaViolation:
                    pass
            if matches == 0 or (keyword == "oneOf" and matches != 1):
                raise SchemaViolation("No unique valid variant")
    kind = schema.get("type")
    if kind and (kind not in _TYPES or not _TYPES[kind](value)):
        raise SchemaViolation("Wrong type")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise SchemaViolation("Wrong constant")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise SchemaViolation("Unknown enum value")
    if kind == "object":
        props = schema["properties"]
        if set(schema["required"]) - value.keys():
            raise SchemaViolation("Missing field")
        if schema.get("additionalProperties") is not False or value.keys() - props.keys():
            raise SchemaViolation("Unknown field or open schema")
        for key, child in value.items():
            check(props[key], child, root=root)
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise SchemaViolation("Array bound")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise SchemaViolation("Duplicate item")
        for child in value:
            check(schema["items"], child, root=root)
    if kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise SchemaViolation("String bound")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaViolation("String pattern")
        if schema.get("format") == "date-time":
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as exc:
                raise SchemaViolation("Invalid timestamp") from exc
        elif "format" in schema and schema["format"] != "binary":
            raise SchemaViolation("Unsupported format")
    if kind in ("number", "integer"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise SchemaViolation("Numeric bound")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise SchemaViolation("Exclusive bound")
