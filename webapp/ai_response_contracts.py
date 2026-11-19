"""Canonical fresh AI wire contracts, independent of saved/history formats.

The immutable field tree owns schema emission, local structure and instructions.
Business identity, measurement, catalogue and pricing guards remain in server.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import sys


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


def integer_digit_limit() -> int:
    # Reuse the JSON integer I/O limit so admitted orders remain serialisable.
    return sys.get_int_max_str_digits() or sys.int_info.default_max_str_digits


def _bounded_json_integer(token: str) -> int:
    if len(token.lstrip("-")) > integer_digit_limit():
        raise ContractViolation("schema_json")
    return int(token)


def _bounded_json_decimal(token: str) -> Decimal:
    # The JSON decoder has already recognised the number syntax. Remove
    # insignificant zeros before constructing a bounded Decimal coefficient.
    mantissa, _, exponent = token.lower().partition("e")
    sign = "-" if mantissa.startswith("-") else ""
    whole, _, fraction = mantissa.removeprefix("-").partition(".")
    digits = (whole + fraction).lstrip("0")
    coefficient = digits.rstrip("0")
    if len(coefficient) > integer_digit_limit():
        raise ContractViolation("schema_json")
    # Let Decimal bound the exponent without converting its text to a large int.
    scale = Decimal("1e" + exponent).as_tuple().exponent if exponent else 0
    scale += len(digits) - len(coefficient) - len(fraction)
    return Decimal(f"{sign}{coefficient or '0'}e{scale}")


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
    decimal_wire: bool = False
    # Object only: at least one key group must be fully present and non-null.
    require_any: tuple[tuple[str, ...], ...] = ()
    # Nullable only: a reply from before the field existed may leave it out.
    optional: bool = False

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
            if self.require_any:
                line += "; non-null " + ", or ".join(" and ".join(group) for group in self.require_any)
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
                 "boolean": lambda: type(value) is bool,
                 "integer": lambda: type(value) is int or (
                     self.decimal_wire and isinstance(value, Decimal) and value.is_finite()
                     and value.adjusted() < integer_digit_limit()
                     and value == value.to_integral_value()
                 ),
                 "number": lambda: finite_number(
                     float(value) if self.decimal_wire and isinstance(value, Decimal) else value
                 )}[self.kind]()
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
            if self.require_any and not any(
                    all(value.get(key) is not None for key in group) for group in self.require_any):
                raise ContractViolation()
            for key, field in self.fields:
                child = f"{path}.{key}" if path else key
                if key not in value and field.optional and field.nullable:
                    continue
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
        if self.decimal_wire and isinstance(value, Decimal):
            return int(value) if self.kind == "integer" else float(value)
        return value


def obj(_require_any=(), **fields):
    return Field("object", fields=tuple(fields.items()), require_any=tuple(tuple(group) for group in _require_any))


@dataclass(frozen=True)
class ResponseContract:
    name: str
    root: Field
    exact_decimals: bool = False
    measurement_rule: str = "never invent measurements"

    def text_format(self):
        return {"format": {"type": "json_schema", "name": self.name, "strict": True,
                           "schema": self.root.schema()}}

    def field_instructions(self):
        return ("Fresh response contract " + self.name + ". " + self.root.instructions()
                + ". Use [] for empty arrays. Null means genuinely absent optional data; " + self.measurement_rule + ".")

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
            return json.loads(
                text, object_pairs_hook=unique, parse_constant=invalid,
                parse_int=_bounded_json_integer if self.exact_decimals else int,
                parse_float=_bounded_json_decimal if self.exact_decimals else number,
            )
        except (ValueError, TypeError, RecursionError, OverflowError, InvalidOperation) as exc:
            if isinstance(exc, ContractViolation):
                raise
            raise ContractViolation("schema_json") from None


DRAFT_SYNTHESIS = ResponseContract("draft_synthesis_v1", obj(
    # An area-only reply (#385) is allowed; a project with no size at all is not.
    project=obj(
        (("booth_width", "booth_depth"), ("booth_area_sqm",)),
        booth_width=Field("number", nullable=True, positive=True),
        booth_depth=Field("number", nullable=True, positive=True),
        booth_area_sqm=Field("number", nullable=True, positive=True, optional=True,
                             instruction="floor area in sqm when only an area is known"),
        # Owner (19 Nov 2026): the booth size staff state in Known details, as the AI reads it.
        staff_booth_size=Field("object", nullable=True, optional=True, fields=(
            ("booth_width", Field("number", nullable=True, positive=True)),
            ("booth_depth", Field("number", nullable=True, positive=True)),
            ("booth_area_sqm", Field("number", nullable=True, positive=True)),
            ("stated", Field("string", enum=("clear", "unclear", "none"))),
        ), instruction="booth size stated in staff_confirmed_facts, numbers as written"),
    ),
    quote_basis_sections=Field("array", item=obj(
        id=Field("string", nonblank=True), title=Field("string", nonblank=True),
        lines=Field("array", missing_reason="basis_rows_shape", item=obj(
            id=Field("string", nonblank=True), tag=Field("string", enum=tuple(sorted(BASIS_TAGS))),
            text=Field("string", missing_reason="basis_row_text", nonblank=True),
            confidence_pct=Field("integer", instruction="integer from 0 to 100",
                                 missing_reason="confidence_missing", minimum=0, maximum=100),
            quantity=Field("number", nullable=True, positive=True), unit=Field("string", nullable=True, nonblank=True),
            # Owner (18 Nov 2026): the Quote Basis shows "Est." on a quantity the AI predicted rather than read.
            quantity_estimated=Field("boolean", nullable=True,
                                     instruction="true when quantity was predicted rather than stated or counted"),
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
# Owner rule (18 Nov 2026): a quote is a prediction, so physical lines always carry a predicted quantity.
), measurement_rule="null quantity and unit are only for purely informational lines; every physical line "
   "has a positive quantity and unit, predicted from the booth size, drawing scale and typical sizes when not stated")
