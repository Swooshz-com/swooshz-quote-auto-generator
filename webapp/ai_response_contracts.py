"""Canonical fresh AI wire contracts, independent of saved/history formats.

The immutable field tree owns schema emission, local structure and instructions.
Business identity, measurement, catalogue and pricing guards remain in server.py.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math


BASIS_TAGS = frozenset({"Include", "Confirm", "Custom", "Exclude"})


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class ContractViolation(ValueError):
    def __init__(self, reason="schema_shape", missing_path=""):
        super().__init__("AI response does not match its contract.")
        self.reason = reason
        self.missing_path = missing_path


@dataclass(frozen=True)
class Field:
    kind: str
    fields: tuple[tuple[str, "Field"], ...] = ()
    item: "Field | None" = None
    nullable: bool = False
    enum: tuple[str, ...] = ()
    instruction: str = ""
    missing_reason: str = ""
    minimum: int | None = None
    maximum: int | None = None
    nonblank: bool = False
    positive: bool = False

    def schema(self):
        result = {"type": [self.kind, "null"] if self.nullable else self.kind}
        if self.kind == "object":
            result.update(properties={key: field.schema() for key, field in self.fields},
                          required=[key for key, _ in self.fields], additionalProperties=False)
        elif self.kind == "array":
            result["items"] = self.item.schema()
        if self.enum:
            result["enum"] = [*self.enum, *([None] if self.nullable else [])]
        return result

    def instructions(self, path="draft"):
        line = f"{path}: {self.kind}" + (" or null" if self.nullable else "")
        if self.enum:
            line += "; allowed values " + ", ".join(self.enum)
        if self.kind == "object":
            line += "; requires " + ", ".join(key for key, _ in self.fields) + "; no other fields"
        if self.instruction:
            line += "; " + self.instruction
        if self.minimum is not None:
            line += f"; minimum {self.minimum}"
        if self.maximum is not None:
            line += f"; maximum {self.maximum}"
        if self.nonblank:
            line += "; nonblank"
        if self.positive:
            line += "; positive"
        children = [field.instructions(f"{path}.{key}") for key, field in self.fields]
        if self.item:
            children.append(self.item.instructions(path + "[]"))
        return ". ".join([line, *children])

    def inspect(self, value, path, missing):
        """Check supplied values before allowing any missing-field repair."""
        if value is None and self.nullable:
            return
        valid = {"object": lambda: isinstance(value, dict),
                 "array": lambda: isinstance(value, list),
                 "string": lambda: isinstance(value, str),
                 "integer": lambda: type(value) is int,
                 "number": lambda: finite_number(value)}[self.kind]()
        if not valid or (self.enum and value not in self.enum):
            raise ContractViolation()
        if ((self.minimum is not None and value < self.minimum)
                or (self.maximum is not None and value > self.maximum)
                or (self.positive and value <= 0)
                or (self.nonblank and not value.strip())):
            raise ContractViolation()
        if self.kind == "object":
            if set(value) - {key for key, _ in self.fields}:
                raise ContractViolation()
            for key, field in self.fields:
                child = f"{path}.{key}" if path else key
                if key not in value:
                    missing.append(ContractViolation(field.missing_reason or "schema_shape",
                        child if field.missing_reason else ""))
                else:
                    field.inspect(value[key], child, missing)
        elif self.kind == "array":
            for index, entry in enumerate(value):
                self.item.inspect(entry, f"{path}[{index}]", missing)

    def adapt(self, value):
        """Nullable optional wire fields retain the application's absence semantics."""
        if self.kind == "object":
            return {key: field.adapt(value[key]) for key, field in self.fields
                    if key in value and not (field.nullable and value[key] is None)}
        if self.kind == "array":
            return [self.item.adapt(entry) for entry in value]
        return value


def obj(**fields):
    return Field("object", fields=tuple(fields.items()))


@dataclass(frozen=True)
class ResponseContract:
    name: str
    root: Field

    def text_format(self):
        return {"format": {"type": "json_schema", "name": self.name, "strict": True,
                           "schema": self.root.schema()}}

    def field_instructions(self):
        return ("Fresh response contract " + self.name + ". " + self.root.instructions()
                + ". Use [] for empty arrays. Null means genuinely absent optional data; never invent measurements.")

    def matches_text_format(self, value):
        try:
            return json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(self.text_format(), sort_keys=True)
        except (TypeError, ValueError, RecursionError):
            return False

    def admit(self, value):
        missing = []
        self.root.inspect(value, "", missing)
        if missing:
            # A missing non-repairable field always wins over a repairable one.
            raise next((error for error in missing if not error.missing_path), missing[0])
        return self.root.adapt(value)

    def parse(self, text):
        return self.admit(self.parse_json(text))

    def parse_json(self, text):
        """Exact JSON only; callers must still run this contract's admission."""
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ContractViolation("schema_json")
                result[key] = value
            return result

        def invalid(_):
            raise ContractViolation("schema_json")

        def number(token):
            value = float(token)
            if not math.isfinite(value):
                raise ContractViolation("schema_json")
            return value

        try:
            value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid, parse_float=number)
            return value
        except (ValueError, TypeError, RecursionError, OverflowError) as exc:
            if isinstance(exc, ContractViolation):
                raise
            raise ContractViolation("schema_json") from None


DRAFT_SYNTHESIS = ResponseContract("draft_synthesis_v1", obj(
    project=obj(booth_width=Field("number", positive=True), booth_depth=Field("number", positive=True)),
    quote_basis_sections=Field("array", item=obj(
        id=Field("string", nonblank=True), title=Field("string", nonblank=True),
        lines=Field("array", missing_reason="basis_rows_shape", item=obj(
            id=Field("string", nonblank=True), tag=Field("string", enum=tuple(sorted(BASIS_TAGS))),
            text=Field("string", missing_reason="basis_row_text", nonblank=True),
            confidence_pct=Field("integer", instruction="integer from 0 to 100",
                                 missing_reason="confidence_missing", minimum=0, maximum=100),
            quantity=Field("number", nullable=True, positive=True), unit=Field("string", nullable=True, nonblank=True),
            pricing_keyword=Field("string", nullable=True),
        )),
    )),
    line_items=Field("array", item=obj(
        section=Field("string", nonblank=True), quantity=Field("number", positive=True), unit=Field("string", nonblank=True),
        description=Field("string", missing_reason="item_row_shape", nonblank=True),
        pricing_keyword=Field("string"), source_basis_line_id=Field("string", nonblank=True),
        # Both fields have active prompt/normaliser consumers. No amounts/authority.
        price_mode=Field("string", nullable=True, enum=("Priced", "Included")),
        display_price=Field("string", nullable=True, enum=("Included",)),
    )),
    analysis_findings=Field("array", item=obj(
        text=Field("string", missing_reason="supplementary_row_shape", nonblank=True),
        id=Field("string", nullable=True),
        confidence_pct=Field("integer", nullable=True, minimum=0, maximum=100),
    )),
))
