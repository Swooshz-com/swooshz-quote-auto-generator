"""Draft orchestration, catalogue matching and row pricing for quotes.

Callers admit inputs and supply the draft gateway adapter. They select the
catalogue and validate pricing authority before applying any calculated price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
from typing import Any
import unicodedata

from scripts import pricing_reference_cleanup, pricing_reference_enrichment


@dataclass(frozen=True)
class PreparedDraftMedia:
    single_send: list[dict[str, Any]]
    batches: list[list[tuple[str, dict[str, Any]]]]
    # An empty text layer permits visual observations; unavailable PDF text does not.
    measurement_sources: dict[str, str | None] | None = None


class QuoteDraftRunner:
    """Sequence admitted media through the existing draft gateway adapter."""

    def __init__(self, gateway):
        self.gateway = gateway

    def run(self, prepared: PreparedDraftMedia) -> dict[str, Any]:
        if prepared.single_send:
            budget = self.gateway.new_budget(0)
            budget.next_stage(synthesis=True)
            try:
                data, fingerprint = self.gateway.send_single(prepared.single_send, budget)
            finally:
                if budget.by_model:
                    self.gateway.record_usage(budget)
            return self.gateway.normalize_response(data, fingerprint)

        budget = self.gateway.new_budget(len(prepared.batches))
        plan = self.gateway.prepare_batches(prepared.batches)
        try:
            observations = self.gateway.observe_batches(prepared, plan, budget)
            return self.gateway.synthesize(plan, observations, budget)
        finally:
            self.gateway.record_usage(budget)


def clean_text(value: Any) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", str(value or "")).strip())


def parse_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def round_commercial_cents(value: Any) -> float | None:
    """Round commercial values to cents with explicit decimal half-up semantics."""
    number = parse_float_or_none(value)
    if number is None or not math.isfinite(number):
        return None
    try:
        rounded = Decimal(str(number)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return float(rounded) if rounded else 0.0


def row_basis_amount(quantity: float, unit_price: float) -> float | None:
    """Multiply the admitted quantity and unit price, then round the line."""
    return round_commercial_cents(quantity * unit_price)


def row_quote_amount(basis_amount: float, exchange_rate: float) -> float | None:
    """Convert an already rounded line using the caller's validated rate."""
    return round_commercial_cents(basis_amount * exchange_rate)


def quote_commercial_historical_effective_unit_price(
    row: dict[str, Any],
    *,
    quantity: float | None = None,
) -> float | None:
    override = parse_float_or_none(row.get("unit_price_override"))
    if override is not None:
        return override
    effective = parse_float_or_none(row.get("effective_unit_price"))
    if effective is not None:
        return effective
    basis_amount = parse_float_or_none(row.get("pricing_basis_amount"))
    resolved_quantity = quantity if quantity is not None else parse_float_or_none(row.get("quantity"))
    if basis_amount is not None and resolved_quantity is not None and resolved_quantity > 0:
        return round(basis_amount / resolved_quantity, 6)
    return None


CATALOG_TEXT_COMPARISON_TOKEN_ALIASES = pricing_reference_cleanup.import_word_replacements()


def comparable_catalog_description_key(value: Any) -> str:
    tokens = re.findall(r"[a-z0-9]+", clean_customer_quote_line_text(value).casefold())
    return " ".join(
        CATALOG_TEXT_COMPARISON_TOKEN_ALIASES.get(token, token)
        for token in tokens
        if token not in {"inch", "inches"}
    )


def comparable_catalog_description_key_without_leading_unit(value: Any) -> str:
    key = comparable_catalog_description_key(value)
    return re.sub(r"^(?:m2|sqm|m length|m run|m|nos|no|lot|sets?)\s+", "", key).strip()


def bracketed_catalog_reference_parts(value: Any) -> tuple[str, str] | None:
    text = clean_customer_quote_line_text(value)
    match = re.match(r"^\[\s*(?P<reference>.+?)\s*\](?:\s*-\s*(?P<detail>.*))?$", text)
    if not match:
        return None
    reference = clean_customer_quote_line_text(match.group("reference"))
    detail = clean_customer_quote_line_text(match.group("detail") or "")
    return (reference, detail) if reference else None


def catalog_reference_values(item: dict[str, Any]) -> list[str]:
    return [
        clean_text(value)
        for value in (
            item.get("pricing_reference_description"),
            item.get("catalog_description"),
            item.get("description"),
        )
        if clean_text(value)
    ]


def bracketed_reference_matches_catalog_item(value: Any, item: dict[str, Any]) -> bool:
    bracketed = bracketed_catalog_reference_parts(value)
    if not bracketed:
        return False
    reference, _detail = bracketed
    reference_key = comparable_catalog_description_key_without_leading_unit(reference)
    return bool(reference_key) and any(
        comparable_catalog_description_key_without_leading_unit(candidate) == reference_key
        for candidate in catalog_reference_values(item)
    )


def catalog_spatial_context_parts(value: Any, *, allow_chain: bool = False) -> tuple[str, str] | None:
    """Split an unambiguous spatial description from its subject.

    This recognises grammar, not catalogue objects. Negation, coordination and
    punctuation leave the existing conservative family checks in charge.
    """
    text = clean_customer_quote_line_text(value)
    scope_text = re.sub(r"^(?:sqm|m|nos?|pcs?|lot|sets?)\.\s+", "", text, flags=re.IGNORECASE)
    # A single final full stop ends this phrase; ellipses and sentence breaks do not.
    scope_text = re.sub(r"(?<!\.)\.$", "", scope_text)
    if re.search(r"\.(?:\s|$)|\s[-\u2013\u2014]\s", scope_text):
        return None
    if re.search(r"\b(?:no|not|never|nowhere|without|neither|nor|except|excluding|excluded|removed|omitted|deleted|absent|and|or|only)\b|\b[a-z]+n['\u2019]t\b|[,;:!?()\[\]/&+]", text, re.IGNORECASE):
        return None
    relations = list(re.finditer(r"\b(?:in front of|next to|at|around|near|beside|behind)\b", text, re.IGNORECASE))
    if not relations or (not allow_chain and len(relations) != 1):
        return None
    if any(not text[left.end():right.start()].strip() for left, right in zip(relations, relations[1:])):
        return None
    relation = relations[0]
    primary, context = text[:relation.start()].strip(), text[relation.end():].strip()
    return (primary, context) if primary and context else None


def safe_section_id(value: Any, fallback: str = "section") -> str:
    text = clean_text(value).lower()
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", text):
        return text
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or fallback


def safe_catalog_item_id(value: Any, fallback: str = "item") -> str:
    text = clean_text(value).lower()
    candidate = safe_section_id(text, "")
    return candidate or safe_section_id(fallback, "item")


def clean_basis_section_title(value: Any) -> str:
    text = clean_text(value).replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s*-\s*quote\s+basis\s+to\s+confirm\s*$", "", text, flags=re.IGNORECASE).strip()


def normalize_catalog_section(value: Any) -> str:
    text = clean_basis_section_title(value)
    return text or "General"


PROVENANCE_PHRASE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s+taken\s+from\s+quotation\s+title\s*:?\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+from\s+quotation\s+title\s*:?\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+visible\s+in\s+image(?:s)?\s*(?:at\s+)?", re.IGNORECASE), " "),
    (re.compile(r"\s+as\s+seen\s+in\s+render\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+as\s+per\s+image\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+based\s+on\s+uploaded\s+reference\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+ai\s+detected\s*:?\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+assumed\s+from\s+image\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+suggested\s+by\s+image\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+from\s+reference\s+image\s*", re.IGNORECASE), " "),
    (re.compile(r"\s+appears\s+to\s+be\s+", re.IGNORECASE), " "),
    (re.compile(r"\s+likely\s+", re.IGNORECASE), " "),
)


def clean_customer_quote_line_text(value: Any) -> str:
    text = normalize_customer_unit_text(value)
    for pattern, replacement in PROVENANCE_PHRASE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+(:|,|\.)", r"\1", text)
    text = re.sub(r":\s*([0-9])", r" \1", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ;")
    return clean_text(text)


def normalize_pricing_unit(value: Any) -> str:
    unit = clean_text(value)
    normalized = re.sub(r"\s+", " ", unit.lower()).strip(". ")
    if normalized in {"m2", "m\u00b2", "m^2", "sq m", "sq.m", "sq.m.", "square metre", "square meter", "square metres", "square meters"}:
        return "sqm"
    if normalized in {"m run", "m. run"}:
        return "m run"
    if normalized in {"m length", "m. length"}:
        return "m length"
    if normalized in {"nos", "no", "pc", "pcs", "piece", "pieces", "unit", "units"}:
        return "nos"
    if normalized in {"lot", "lots"}:
        return "lot"
    if normalized in {"set", "sets"}:
        return "sets"
    return unit


def normalize_customer_unit_text(value: Any) -> str:
    text = clean_text(value)
    text = re.sub(r"(?i)(?<![A-Za-z0-9])m\s*(?:2|\^2)(?![A-Za-z0-9])", "sqm", text)
    text = re.sub(r"(?i)(?<![A-Za-z0-9])sq\.?\s*m\.?(?![A-Za-z0-9])", "sqm", text)
    return clean_text(text)


def parse_pricing_number(value: Any) -> float | None:
    try:
        number = float(str(value or "").replace(",", "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def pricing_reference_order_number(value: Any) -> int | None:
    number = parse_pricing_number(value)
    if number is None or number <= 0:
        return None
    return int(number)


def pricing_reference_sale_unit_price(item: dict[str, Any]) -> float | None:
    explicit = parse_pricing_number(item.get("sale_unit_price"))
    if explicit is not None and explicit >= 0:
        return round_commercial_cents(explicit)
    cost = parse_pricing_number(item.get("internal_cost") or item.get("cost"))
    markup = parse_pricing_number(item.get("markup_multiplier") or item.get("markup"))
    if cost is None or cost <= 0 or markup is None or markup <= 0:
        return None
    return round_commercial_cents(cost * markup)


def infer_unit_prefix(description: Any) -> str:
    text = clean_text(description)
    match = re.match(r"(?i)^(m\.?\s*run|m\.?\s*length|m2|sqm|nos?\.?|no\.?|lot\.?|sets?|m\.?)(?=\s|[.;:/,(]|$)", text)
    if not match:
        return ""
    unit = re.sub(r"\s+", " ", match.group(1).lower().replace(".", " ")).strip()
    if unit == "m2":
        return "sqm"
    if unit in {"no", "nos"}:
        return "nos"
    if unit in {"set", "sets"}:
        return "set"
    return unit


CATALOG_INFERENCE_STOP_WORDS = {
    "and",
    "area",
    "areas",
    "booth",
    "component",
    "components",
    "custom",
    "detail",
    "details",
    "finish",
    "finished",
    "for",
    "from",
    "full",
    "hd",
    "height",
    "integrated",
    "lot",
    "mounted",
    "on",
    "or",
    "nos",
    "per",
    "proposal",
    "speaker",
    "sqm",
    "the",
    "use",
    "with",
    "wooden",
}


CATALOG_INFERENCE_TOKEN_ALIASES = {
    "aluminium": "aluminum",
    "grey": "gray",
    "laminate": "laminated",
    "m2": "sqm",
    "printed": "print",
    "printing": "print",
    "prints": "print",
    "timber": "wood",
    "wooden": "wood",
    **pricing_reference_cleanup.import_word_replacements(),
}


CATALOG_DISTINGUISHING_ATTRIBUTE_GROUPS = (
    {"acrylic", "aluminum", "chrome", "fabric", "foam", "glass", "laminate", "laminated", "leather", "metal", "timber", "vinyl", "wood", "wooden"},
    {"black", "blue", "brown", "cyan", "dark", "gray", "green", "grey", "navy", "orange", "pink", "purple", "red", "teal", "white", "yellow"},
    {"circular", "curved", "rectangular", "round", "square"},
)


def catalog_inference_token(value: str) -> str:
    token = CATALOG_INFERENCE_TOKEN_ALIASES.get(value.lower(), value.lower())
    if token.endswith("ies") and len(token) > 4:
        token = f"{token[:-3]}y"
    elif token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        token = token[:-1]
    return CATALOG_INFERENCE_TOKEN_ALIASES.get(token, token)


def catalog_inference_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    for raw_token in re.findall(r"[a-z0-9]+", clean_text(value).lower()):
        token = catalog_inference_token(raw_token)
        if (len(token) <= 2 and not token.isdigit() and token != "tv") or token in CATALOG_INFERENCE_STOP_WORDS:
            continue
        tokens.add(token)
    return tokens


def catalog_item_match_terms(item: dict[str, Any]) -> set[str]:
    values = item.get("match_terms") if isinstance(item.get("match_terms"), list) else []
    tokens: set[str] = set()
    for value in values:
        tokens.update(catalog_inference_tokens(value))
    return tokens


def catalog_item_object_families(item: dict[str, Any]) -> set[str]:
    return {
        clean_text(value).lower()
        for value in (item.get("object_families") if isinstance(item.get("object_families"), list) else [])
        if clean_text(value)
    }


def catalog_item_alias_values(item: dict[str, Any]) -> list[str]:
    aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
    section_values = [
        clean_text(item.get("reference_section") or item.get("section")).lower(),
        clean_text(item.get("section")).lower(),
    ]
    values: list[str] = []
    for alias in aliases:
        text = clean_text(alias)
        lowered = text.lower()
        if not text:
            continue
        if any(section and lowered.startswith(section) for section in section_values):
            continue
        values.append(text)
    return values


def catalog_attribute_conflicts(line_tokens: set[str], item_tokens: set[str]) -> bool:
    for group in CATALOG_DISTINGUISHING_ATTRIBUTE_GROUPS:
        line_attributes = line_tokens & group
        item_attributes = item_tokens & group
        if line_attributes and item_attributes and not (line_attributes & item_attributes):
            return True
    return False


def catalog_item_identity_key(item: dict[str, Any] | None) -> str:
    return clean_text(item.get("id")) if isinstance(item, dict) else ""


def catalog_items_grouped_by_identity_key(
    catalog_items: list[dict[str, Any]] | None,
    selected_item: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    values = [*(catalog_items or []), selected_item]
    for item in values:
        if not isinstance(item, dict):
            continue
        item_id = catalog_item_identity_key(item)
        if item_id:
            grouped.setdefault(item_id, []).append(item)
    return {item_id: grouped[item_id] for item_id in sorted(grouped)}


def catalog_family_tokens(value: Any) -> set[str]:
    tokens = catalog_inference_tokens(value)
    normalized_unit = normalize_pricing_unit(infer_unit_prefix(value))
    if normalized_unit:
        tokens.difference_update(catalog_inference_tokens(normalized_unit))
    return tokens


def catalog_family_token_sequence(value: Any) -> tuple[str, ...]:
    normalized = comparable_catalog_description_key_without_leading_unit(value)
    sequence: list[str] = []
    for raw_token in normalized.split():
        token = catalog_inference_token(raw_token)
        if len(token) <= 2 and not token.isdigit() and token != "tv":
            continue
        sequence.append(token)
    while sequence and normalize_pricing_unit(sequence[-1]) in {"sqm", "nos", "lot", "sets"}:
        sequence.pop()
    return tuple(sequence)


def catalog_family_alternative_sequences(items: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    alternatives = {
        sequence
        for item in items
        for family in catalog_item_object_families(item)
        for sequence in [catalog_family_token_sequence(family)]
        if sequence
    }
    return sorted(alternatives)


def catalog_family_sequence_occurs(detail_sequence: tuple[str, ...], family_sequence: tuple[str, ...]) -> bool:
    width = len(family_sequence)
    return bool(width and any(
        detail_sequence[index:index + width] == family_sequence
        for index in range(len(detail_sequence) - width + 1)
    ))


def catalog_family_sequence_is_subsequence(
    family_sequence: tuple[str, ...],
    selected_sequence: tuple[str, ...],
) -> bool:
    width = len(family_sequence)
    return bool(width and any(
        selected_sequence[index:index + width] == family_sequence
        for index in range(len(selected_sequence) - width + 1)
    ))


def catalog_family_evidence_tokens(
    line_text: Any,
    item: dict[str, Any],
    catalog_items: list[dict[str, Any]] | None,
) -> tuple[set[str], bool, bool, str]:
    """Return independent row-detail tokens, selected-reference evidence, and mismatch state."""
    selected_id = catalog_item_identity_key(item)
    grouped = catalog_items_grouped_by_identity_key(catalog_items, item)
    bracketed = bracketed_catalog_reference_parts(line_text)
    if bracketed:
        reference, detail = bracketed
        reference_key = comparable_catalog_description_key_without_leading_unit(reference)
        matching_ids = {
            item_id
            for item_id, entries in grouped.items()
            if any(
                comparable_catalog_description_key_without_leading_unit(candidate)
                == reference_key
                for entry in entries
                for candidate in catalog_reference_values(entry)
            )
        } if reference_key else set()
        if matching_ids == {selected_id} and selected_id:
            return catalog_family_tokens(detail), True, False, clean_text(detail)
        return set(), False, True, ""

    description_words = comparable_catalog_description_key_without_leading_unit(line_text).split()
    prefixes: list[tuple[int, str]] = []
    for item_id, entries in grouped.items():
        for entry in entries:
            for reference in catalog_reference_values(entry):
                reference_words = comparable_catalog_description_key_without_leading_unit(reference).split()
                if (
                    reference_words
                    and len(reference_words) <= len(description_words)
                    and description_words[:len(reference_words)] == reference_words
                ):
                    prefixes.append((len(reference_words), item_id))
    if prefixes:
        longest = max(length for length, _item_id in prefixes)
        matching_ids = {item_id for length, item_id in prefixes if length == longest}
        if matching_ids == {selected_id} and selected_id:
            # A name in original unbracketed wording is independent evidence.
            # Do not erase it when it is the primary of a spatial description.
            spatial = catalog_spatial_context_parts(line_text)
            detail_text = clean_text(line_text) if spatial else " ".join(description_words[longest:])
            return catalog_family_tokens(detail_text), True, False, detail_text
        return set(), False, True, ""

    occurrences: list[tuple[int, int, str]] = []
    for item_id, entries in grouped.items():
        for entry in entries:
            for reference in catalog_reference_values(entry):
                reference_words = comparable_catalog_description_key_without_leading_unit(reference).split()
                width = len(reference_words)
                if not width or width > len(description_words):
                    continue
                for start in range(len(description_words) - width + 1):
                    if description_words[start:start + width] == reference_words:
                        occurrences.append((width, start, item_id))
    if occurrences:
        longest = max(width for width, _start, _item_id in occurrences)
        matching = [
            (start, item_id)
            for width, start, item_id in occurrences
            if width == longest
        ]
        matching_ids = {item_id for _start, item_id in matching}
        matching_positions = {start for start, _item_id in matching}
        if matching_ids == {selected_id} and len(matching_positions) == 1 and selected_id:
            start = next(iter(matching_positions))
            detail_words = description_words[:start] + description_words[start + longest:]
            detail_text = " ".join(detail_words)
            return catalog_family_tokens(detail_text), True, False, detail_text
        return set(), False, True, ""

    detail_text = comparable_catalog_description_key_without_leading_unit(line_text)
    return catalog_family_tokens(detail_text), False, False, detail_text


def catalog_detail_matches_other_catalog_object_family(
    detail_tokens: set[str],
    item: dict[str, Any],
    catalog_items: list[dict[str, Any]] | None,
    *,
    selected_reference_established: bool = False,
    detail_text: Any = None,
) -> bool:
    full_detail_tokens = (
        set(catalog_family_token_sequence(detail_text))
        if detail_text is not None
        else set(detail_tokens)
    )
    if not (detail_tokens or full_detail_tokens) or not catalog_items:
        return False

    grouped = catalog_items_grouped_by_identity_key(catalog_items, item)
    selected_id = catalog_item_identity_key(item)
    selected_alternatives = {
        frozenset(sequence)
        for sequence in catalog_family_alternative_sequences(grouped.get(selected_id, [item]))
    }

    spatial = catalog_spatial_context_parts(detail_text) if detail_text is not None else None
    if spatial:
        primary, _context = spatial
        primary_tokens = catalog_family_tokens(primary)
        primary_family_tokens = set(catalog_family_token_sequence(primary))
        if any(alternative <= primary_family_tokens for alternative in selected_alternatives):
            # A complete selected family must occur independently before the
            # relation. Apply every existing conflict rule to that primary;
            # only later contextual object evidence is then out of scope.
            return catalog_detail_matches_other_catalog_object_family(
                primary_tokens, item, catalog_items,
                selected_reference_established=selected_reference_established,
                detail_text=primary,
            )

    for candidate_id, entries in grouped.items():
        if candidate_id == selected_id:
            continue
        for family_sequence in catalog_family_alternative_sequences(entries):
            family_tokens = frozenset(family_sequence)
            if family_tokens in selected_alternatives:
                continue
            if family_tokens <= full_detail_tokens:
                return True

            competing_evidence = family_tokens & detail_tokens
            if any(
                alternative <= detail_tokens
                and competing_evidence <= alternative
                for alternative in selected_alternatives
            ):
                continue

            for alternative in selected_alternatives:
                shared_tokens = family_tokens & alternative
                discriminating_detail = (family_tokens - alternative) & detail_tokens
                if (
                    discriminating_detail
                    and shared_tokens
                    and (
                        selected_reference_established
                        or shared_tokens & detail_tokens
                    )
                ):
                    return True
    return False


def catalog_line_contradicts_item(
    line_text: Any,
    item: dict[str, Any],
    *,
    catalog_items: list[dict[str, Any]] | None = None,
) -> bool:
    line_tokens, selected_reference, reference_mismatch, detail_text = catalog_family_evidence_tokens(
        line_text,
        item,
        catalog_items,
    )
    if reference_mismatch:
        return True
    item_tokens: set[str] = set()
    for value in catalog_inference_values(item):
        item_tokens.update(catalog_inference_tokens(value))
    spatial = catalog_spatial_context_parts(detail_text)
    if spatial and item_tokens and catalog_attribute_conflicts(catalog_family_tokens(spatial[0]), item_tokens):
        # Context attributes cannot cancel a conflicting primary-object attribute.
        return True
    if line_tokens and item_tokens and catalog_attribute_conflicts(line_tokens, item_tokens):
        return True
    if catalog_detail_matches_other_catalog_object_family(
        line_tokens,
        item,
        catalog_items,
        selected_reference_established=selected_reference,
        detail_text=detail_text,
    ):
        return True
    if selected_reference or not line_tokens or not item_tokens:
        return False
    overlap = line_tokens & item_tokens
    if len(overlap) >= 2:
        return False
    line_ratio = len(overlap) / max(len(line_tokens), 1)
    item_ratio = len(overlap) / max(min(len(item_tokens), 12), 1)
    return line_ratio < 0.2 and item_ratio < 0.2


def unresolved_detail_contradicts_selected_reference(
    detail: Any,
    line_text: Any,
    item: dict[str, Any],
    *,
    catalog_items: list[dict[str, Any]] | None = None,
) -> bool:
    """Judge an unclaimed row's detail under the basis line's exact selected reference.

    An explicit selection is written as "[ reference ] - detail". The unclaimed source
    row's original wording is that same detail, so it keeps every family and attribute
    check but not the generic low-overlap rule an established reference already waives.
    """
    bracketed = bracketed_catalog_reference_parts(line_text)
    if bracketed and not bracketed_catalog_reference_parts(detail):
        _tokens, established, mismatch, _detail = catalog_family_evidence_tokens(line_text, item, catalog_items)
        if established and not mismatch:
            detail_key = comparable_catalog_description_key_without_leading_unit(detail)
            reference_only = bool(detail_key) and any(
                comparable_catalog_description_key_without_leading_unit(reference) == detail_key
                for reference in catalog_reference_values(item)
            )
            # A reference-only label supplies identity, not a second independent
            # description whose words can contradict that same catalogue item.
            detail = f"[ {bracketed[0]} ]" if reference_only else f"[ {bracketed[0]} ] - {clean_text(detail)}"
    return catalog_line_contradicts_item(detail, item, catalog_items=catalog_items)


def explicit_catalog_keyword_has_usable_overlap(
    line_text: Any,
    item: dict[str, Any],
    *,
    catalog_items: list[dict[str, Any]] | None = None,
) -> bool:
    line_tokens, _selected_reference, reference_mismatch, _detail_text = catalog_family_evidence_tokens(
        line_text,
        item,
        catalog_items,
    )
    if reference_mismatch or catalog_line_contradicts_item(
        line_text,
        item,
        catalog_items=catalog_items,
    ):
        return False
    item_tokens: set[str] = set()
    for value in catalog_inference_values(item):
        item_tokens.update(catalog_inference_tokens(value))
    if not line_tokens or not item_tokens or not (line_tokens & item_tokens):
        return False
    return True


def catalog_inference_values(item: dict[str, Any]) -> list[str]:
    aliases = catalog_item_alias_values(item)
    match_terms = item.get("match_terms") if isinstance(item.get("match_terms"), list) else []
    object_families = item.get("object_families") if isinstance(item.get("object_families"), list) else []
    values = [
        item.get("description"),
        item.get("pricing_reference_description"),
        *aliases,
        *match_terms,
        *object_families,
    ]
    return [clean_text(value) for value in values if clean_text(value)]


def catalog_item_unit_hint(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    return normalize_pricing_unit(
        item.get("unit_hint")
        or infer_unit_prefix(item.get("pricing_reference_description"))
        or infer_unit_prefix(item.get("description"))
    )


def pricing_keyword_looks_like_catalog_id(value: Any) -> bool:
    keyword = clean_text(value).lower()
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+", keyword))


def infer_catalog_item_for_line_item(raw: dict[str, Any], catalog_lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    bracketed = bracketed_catalog_reference_parts(raw.get("description"))
    if bracketed:
        exact_matches = [
            item
            for item in catalog_lookup.values()
            if bracketed_reference_matches_catalog_item(raw.get("description"), item)
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

    query_text = " ".join(
        clean_text(value)
        for value in (raw.get("pricing_keyword"), raw.get("description"))
        if clean_text(value)
    )
    if re.search(r"\b(?:excluded?|excluding|not\s+included|not\s+provided|unless\s+requested|by\s+others|no\s+allowance)\b", query_text, flags=re.IGNORECASE):
        return None
    query_tokens = catalog_inference_tokens(query_text)
    if len(query_tokens) < 3:
        return None

    raw_section_key = safe_section_id(normalize_catalog_section(raw.get("section")) or raw.get("section"), "")
    raw_unit = normalize_pricing_unit(raw.get("unit"))
    unique_items: dict[str, dict[str, Any]] = {}
    for item in catalog_lookup.values():
        item_id = clean_text(item.get("id"))
        if item_id:
            unique_items[item_id] = item

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for item_id, item in unique_items.items():
        item_section_key = safe_section_id(normalize_catalog_section(item.get("section")) or item.get("section"), "")
        item_unit = catalog_item_unit_hint(item)
        section_bonus = 1.5 if raw_section_key and item_section_key == raw_section_key else 0.0
        unit_bonus = 3.0 if raw_unit and item_unit == raw_unit else 0.0
        unit_penalty = -1.5 if raw_unit and item_unit and item_unit != raw_unit else 0.0
        best_score = 0.0
        for value in catalog_inference_values(item):
            value_tokens = catalog_inference_tokens(value)
            if len(value_tokens) < 2:
                continue
            overlap = query_tokens & value_tokens
            value_ratio = len(overlap) / max(len(value_tokens), 1)
            query_ratio = len(overlap) / max(min(len(query_tokens), 12), 1)
            short_context_match = bool(section_bonus and unit_bonus) and len(overlap) >= 2 and value_ratio >= 0.6
            if len(overlap) < 3 and not short_context_match:
                continue
            strong_context_match = bool(section_bonus or unit_bonus) and query_ratio >= 0.6
            if value_ratio < 0.55 and len(overlap) < 4 and not strong_context_match and not short_context_match:
                continue
            score = len(overlap) * 2.0 + value_ratio * 6.0 + query_ratio * 2.0 + section_bonus + unit_bonus + unit_penalty
            best_score = max(best_score, score)
        if best_score >= 9.0:
            scored.append((best_score, item_id, item))

    if not scored:
        return None
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.5:
        tied_item = resolve_tied_catalog_attribute_item(query_text, [item for score, _item_id, item in scored if scored[0][0] - score < 0.5])
        if tied_item:
            return tied_item
        tied_item = resolve_tied_catalog_variant_item(query_text, [item for score, _item_id, item in scored if scored[0][0] - score < 0.5])
        if tied_item:
            return tied_item
        return None
    return scored[0][2]


def catalog_item_variant_value(item: dict[str, Any]) -> float | None:
    text = " ".join(clean_text(value) for value in (item.get("description"), item.get("pricing_reference_description")))
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:\"|\u201d|inch(?:es)?\b|in\b|cm\b|mm\b|w\b|kw\b|l\b)", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = parse_float_or_none(match.group(1))
    if value is None or value <= 0:
        return None
    return value


def catalog_item_variant_group_key(item: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    section_key = safe_section_id(normalize_catalog_section(item.get("section")) or item.get("section"), "")
    unit_key = normalize_pricing_unit(catalog_item_unit_hint(item)).lower()
    tokens = tuple(
        sorted(
            token
            for token in catalog_inference_tokens(item.get("description"))
            if not token.isdigit()
        )
    )
    return (section_key, unit_key, tokens)


def query_variant_preference(query_text: str, variant_values: list[float]) -> float | None:
    query = clean_text(query_text)
    explicit_matches = [
        parse_float_or_none(match)
        for match in re.findall(
            r"\b(\d+(?:\.\d+)?)\s*(?:\"|\u201d|inch(?:es)?\b|in\b|cm\b|mm\b|w\b|kw\b|l\b)",
            query,
            flags=re.IGNORECASE,
        )
    ]
    explicit_values = [value for value in explicit_matches if value is not None and value > 0]
    if explicit_values:
        return explicit_values[0]
    if re.search(r"\b(large|main|hero|feature|prominent|primary)\b", query, flags=re.IGNORECASE):
        return max(variant_values)
    if re.search(r"\b(small|compact|secondary|side|countertop|tabletop)\b", query, flags=re.IGNORECASE):
        return min(variant_values)
    return None


def resolve_tied_catalog_variant_item(query_text: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    variant_groups_by_key: dict[tuple[str, str, tuple[str, ...]], list[tuple[float, dict[str, Any]]]] = {}
    for item in items:
        variant_value = catalog_item_variant_value(item)
        if variant_value is None:
            continue
        variant_groups_by_key.setdefault(catalog_item_variant_group_key(item), []).append((variant_value, item))
    variant_groups = [group for group in variant_groups_by_key.values() if len(group) >= 2]
    if len(variant_groups) != 1:
        return None
    _group_key, variants = next((key, group) for key, group in variant_groups_by_key.items() if len(group) >= 2)
    variant_values = [value for value, _item in variants]
    preferred_value = query_variant_preference(query_text, variant_values)
    if preferred_value is None:
        return None
    variants.sort(key=lambda entry: (abs(entry[0] - preferred_value), entry[0], clean_text(entry[1].get("id"))))
    return variants[0][1]


def resolve_tied_catalog_attribute_item(query_text: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    unique_items = {clean_text(item.get("id")): item for item in items if clean_text(item.get("id"))}
    candidates = list(unique_items.values())
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    query = clean_text(query_text).lower()

    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    def candidate_tokens(item: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        for value in catalog_inference_values(item):
            tokens.update(catalog_inference_tokens(value))
        return tokens

    def candidate_specific_tokens(item: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        aliases = catalog_item_alias_values(item)
        match_terms = item.get("match_terms") if isinstance(item.get("match_terms"), list) else []
        for value in (
            item.get("description"),
            item.get("pricing_reference_description"),
            *aliases,
            *match_terms,
        ):
            tokens.update(catalog_inference_tokens(value))
        return tokens

    def candidate_primary_tokens(item: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        for value in (item.get("description"), item.get("pricing_reference_description")):
            tokens.update(catalog_inference_tokens(value))
        return tokens

    query_tokens = catalog_inference_tokens(query)
    literal_matches = [
        item
        for item in candidates
        if query_tokens & candidate_tokens(item)
    ]
    if len(literal_matches) == 1:
        return literal_matches[0]
    if literal_matches:
        candidates = literal_matches

    finish_preferences: list[str] = []
    if re.search(r"\blaminat(?:e|ed|ion)\b", query, flags=re.IGNORECASE):
        finish_preferences.append("laminated")
    if re.search(r"\bpaint(?:ed|ing)?\b", query, flags=re.IGNORECASE):
        finish_preferences.append("painted")
    if not finish_preferences and re.search(
        r"\b(?:black|blue|brown|cyan|dark|green|grey|gray|navy|orange|pink|purple|red|teal|white|yellow|colour|color|finish|finished)\b",
        query,
        flags=re.IGNORECASE,
    ):
        finish_preferences.append("painted")

    for finish in finish_preferences:
        finish_matches = [
            item
            for item in candidates
            if re.search(rf"\b{re.escape(finish)}\b", clean_text(item.get("description")), flags=re.IGNORECASE)
        ]
        if len(finish_matches) == 1:
            return finish_matches[0]
        if finish_matches:
            candidates = finish_matches

    if len(candidates) == 1:
        return candidates[0]

    scored_candidates: list[tuple[int, int, int, int, int, int, int, str, dict[str, Any]]] = []
    for item in candidates:
        tokens = candidate_tokens(item)
        primary_tokens = candidate_primary_tokens(item)
        specific_tokens = candidate_specific_tokens(item)
        primary_overlap_count = len(query_tokens & primary_tokens)
        primary_extra_item_token_count = len(primary_tokens - query_tokens)
        specific_overlap_count = len(query_tokens & specific_tokens)
        specific_extra_item_token_count = len(specific_tokens - query_tokens)
        overlap_count = len(query_tokens & tokens)
        extra_item_token_count = len(tokens - query_tokens)
        scored_candidates.append((
            primary_overlap_count,
            -primary_extra_item_token_count,
            specific_overlap_count,
            -specific_extra_item_token_count,
            overlap_count,
            -extra_item_token_count,
            -len(tokens),
            clean_text(item.get("id")),
            item,
        ))
    scored_candidates.sort(reverse=True)
    if scored_candidates and (len(scored_candidates) == 1 or scored_candidates[0][:7] > scored_candidates[1][:7]):
        return scored_candidates[0][8]
    return None


def legacy_pricing_catalog_id_aliases(item_id: str, item: dict[str, Any] | None = None) -> set[str]:
    canonical_id = clean_text(item_id).lower()
    aliases: set[str] = set()
    section_values: list[Any] = []
    if isinstance(item, dict):
        section_values.extend((item.get("reference_section"), item.get("section")))
    for section_value in section_values:
        section_slug = safe_section_id(normalize_catalog_section(section_value) or section_value, "")
        if section_slug and canonical_id.startswith(f"{section_slug}-"):
            suffix = canonical_id[len(section_slug) + 1 :]
            if suffix:
                aliases.add(f"{section_slug}.{suffix}")
    if "." in canonical_id:
        aliases.add(safe_catalog_item_id(canonical_id, ""))
    for alias in list(aliases | ({canonical_id} if canonical_id else set())):
        for typo, correct in pricing_reference_cleanup.import_word_replacements().items():
            if correct in alias:
                aliases.add(alias.replace(correct, typo))
            if typo in alias:
                aliases.add(alias.replace(typo, correct))
    aliases.discard(canonical_id)
    aliases.discard("")
    return aliases


def pricing_catalog_runtime_lookup(payload_json: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup = {}
    for item in payload_json.get("items") or []:
        if not isinstance(item, dict):
            continue
        item = pricing_reference_enrichment.enrich_pricing_reference_item(dict(item))
        item_id = clean_text(item.get("id"))
        if not item_id:
            continue
        reference_section = clean_basis_section_title(item.get("reference_section") or item.get("section"))
        raw_description = clean_text(item.get("description"))
        lookup[item_id] = {
            "id": item_id,
            "section": normalize_catalog_section(item.get("section")),
            "reference_section": reference_section or normalize_catalog_section(item.get("section")),
            "category_order": pricing_reference_order_number(item.get("category_order")),
            "item_order": pricing_reference_order_number(item.get("item_order")),
            "unit_hint": clean_text(item.get("unit_hint")),
            "description": clean_customer_quote_line_text(raw_description),
            "pricing_reference_description": raw_description,
            "sale_unit_price": pricing_reference_sale_unit_price(item),
            "aliases": [
                clean_text(alias)
                for alias in (item.get("aliases") if isinstance(item.get("aliases"), list) else [])
                if clean_text(alias)
            ],
            "match_terms": [
                clean_text(term)
                for term in (item.get("match_terms") if isinstance(item.get("match_terms"), list) else [])
                if clean_text(term)
            ],
            "object_families": [
                clean_text(term)
                for term in (item.get("object_families") if isinstance(item.get("object_families"), list) else [])
                if clean_text(term)
            ],
        }
        for alias_id in legacy_pricing_catalog_id_aliases(item_id, lookup[item_id]):
            lookup.setdefault(alias_id, lookup[item_id])
    return lookup


def basis_match_words(value: Any) -> set[str]:
    words: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", clean_text(value).lower()):
        if len(word) < 3:
            continue
        words.add(word)
        if word.endswith("ing") and len(word) > 5:
            words.add(word[:-3])
    return words


def basis_distinguishing_numbers(value: Any) -> set[str]:
    return set(re.findall(r"\b\d+(?:\.\d+)?\b", clean_text(value).lower()))


def item_section_values(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for value in (item.get("reference_section"), item.get("section"), normalize_catalog_section(item.get("section"))):
        title = clean_basis_section_title(value)
        if title and title not in values:
            values.append(title)
    return values


def section_matches_item(section: dict[str, Any], item: dict[str, Any]) -> bool:
    item_keys = set()
    for value in item_section_values(item):
        item_keys.add(safe_section_id(value, "section"))
        item_keys.add(value.lower())
    section_keys = {
        safe_section_id(section.get("id") or section.get("title"), "section"),
        clean_basis_section_title(section.get("title")).lower(),
    }
    if item_keys & section_keys:
        return True
    return False


def line_matches_catalog_description(line: dict[str, Any], description: str) -> bool:
    line_text = clean_customer_quote_line_text(line.get("text"))
    bracketed = bracketed_catalog_reference_parts(line_text)
    if bracketed:
        line_text = bracketed[0]
    description = clean_customer_quote_line_text(description)
    if not line_text or not description:
        return False
    if bracketed:
        return comparable_catalog_description_key(line_text) == comparable_catalog_description_key(description)
    if line_text.lower() == description.lower():
        return True
    line_numbers = basis_distinguishing_numbers(line_text)
    description_numbers = basis_distinguishing_numbers(description)
    if line_numbers and description_numbers and not (line_numbers & description_numbers):
        return False
    line_words = basis_match_words(line_text)
    description_words = basis_match_words(description)
    if not line_words or not description_words:
        return False
    overlap = line_words & description_words
    return len(overlap) / min(len(line_words), len(description_words), 10) >= 0.75


def catalog_match_tokens(value: Any) -> set[str]:
    stop_words = {
        "and",
        "area",
        "approval",
        "approvals",
        "custom",
        "for",
        "final",
        "from",
        "height",
        "lot",
        "meeting",
        "nos",
        "one",
        "organiser",
        "organisers",
        "organizer",
        "organizers",
        "owner",
        "owners",
        "per",
        "rental",
        "requirement",
        "requirements",
        "seat",
        "seating",
        "sqm",
        "subject",
        "the",
        "venue",
        "venues",
        "with",
    }
    tokens: set[str] = set()
    for raw_token in re.findall(r"[a-z0-9]+", clean_text(value).lower()):
        token = catalog_inference_token(raw_token)
        if len(token) <= 2 or token in stop_words:
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 3:
            tokens.add(token[:-1])
    return tokens


def catalog_match_values(item: dict[str, Any]) -> list[str]:
    aliases = catalog_item_alias_values(item)
    match_terms = item.get("match_terms") if isinstance(item.get("match_terms"), list) else []
    object_families = item.get("object_families") if isinstance(item.get("object_families"), list) else []
    values = [
        item.get("description"),
        item.get("pricing_reference_description"),
        *aliases,
        *match_terms,
        *object_families,
    ]
    return [clean_text(value) for value in values if clean_text(value)]


def line_has_exclusion_scope(value: Any) -> bool:
    text = clean_text(value).lower()
    return bool(
        re.search(
            r"\b(?:excluded?|excluding|not\s+included|not\s+provided|unless\s+requested|by\s+others|no\s+allowance)\b",
            text,
        )
    )


def score_catalog_item_for_line(line: dict[str, Any], item: dict[str, Any], section: dict[str, Any] | None = None) -> int:
    line_text = clean_customer_quote_line_text(line.get("text"))
    if line_has_exclusion_scope(line_text):
        return 0
    line_tokens = catalog_match_tokens(line_text)
    if not line_tokens:
        return 0
    item_tokens: set[str] = set()
    phrase_bonus = 0
    best_value_ratio = 0.0
    normalized_line = clean_text(line_text).lower()
    for value in catalog_match_values(item):
        normalized_value = clean_text(value).lower()
        value_tokens = catalog_match_tokens(value)
        item_tokens.update(value_tokens)
        if value_tokens:
            value_overlap = line_tokens & value_tokens
            best_value_ratio = max(best_value_ratio, len(value_overlap) / max(len(value_tokens), 1))
        if normalized_line == normalized_value and value_tokens:
            phrase_bonus = max(phrase_bonus, 20)
        elif len(value_tokens) >= 2 and (normalized_value in normalized_line or normalized_line in normalized_value):
            phrase_bonus = max(phrase_bonus, 10)
    section_bonus = 2 if isinstance(section, dict) and section_matches_item(section, item) else 0
    overlap = line_tokens & item_tokens
    line_ratio = len(overlap) / max(len(line_tokens), 1)
    same_section_literal_match = bool(
        section_bonus
        and (len(overlap) >= 3 or (len(overlap) >= 2 and best_value_ratio >= 0.6))
    )
    if not phrase_bonus and not same_section_literal_match and line_ratio < 0.75:
        return 0
    if section_bonus and len(overlap) < 2 and not phrase_bonus:
        return 0
    return len(overlap) + phrase_bonus + section_bonus


def score_possible_catalog_item_for_line(line: dict[str, Any], item: dict[str, Any], section: dict[str, Any] | None = None) -> int:
    if not isinstance(section, dict) or not section_matches_item(section, item):
        return 0
    line_text = clean_customer_quote_line_text(line.get("text"))
    if line_has_exclusion_scope(line_text):
        return 0
    line_tokens = catalog_match_tokens(line_text)
    if not line_tokens:
        return 0
    item_tokens: set[str] = set()
    best_value_ratio = 0.0
    for value in catalog_match_values(item):
        value_tokens = catalog_match_tokens(value)
        item_tokens.update(value_tokens)
        if value_tokens:
            value_overlap = line_tokens & value_tokens
            best_value_ratio = max(best_value_ratio, len(value_overlap) / max(len(value_tokens), 1))
    overlap = line_tokens & item_tokens
    if not overlap:
        return 0
    raw_unit = normalize_pricing_unit(line.get("unit"))
    item_unit = catalog_item_unit_hint(item)
    unit_bonus = 4 if raw_unit and item_unit and raw_unit == item_unit else 0
    score = len(overlap) * 10 + round(best_value_ratio * 10) + 8 + unit_bonus
    return score if score >= 20 else 0


def best_catalog_item_for_basis_line(
    line: dict[str, Any],
    section: dict[str, Any] | None,
    catalog_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    scored = [
        (score_catalog_item_for_line(line, item, section), item)
        for item in catalog_items
    ]
    scored = [(score, item) for score, item in scored if score >= 3]
    if not scored:
        return None
    scored.sort(key=lambda entry: (-entry[0], clean_text(entry[1].get("id"))))
    top_score = scored[0][0]
    tied_items = [item for score, item in scored if score == top_score]
    if len(tied_items) > 1:
        tied_item = resolve_tied_catalog_attribute_item(clean_customer_quote_line_text(line.get("text")), tied_items)
        if tied_item:
            return tied_item
        tied_item = resolve_tied_catalog_variant_item(clean_customer_quote_line_text(line.get("text")), tied_items)
        if tied_item:
            return tied_item
        return None
    return scored[0][1]


def rank_possible_catalog_items_for_line(
    line: dict[str, Any],
    section: dict[str, Any],
    catalog_items: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    scored = [
        (score_possible_catalog_item_for_line(line, item, section), item)
        for item in catalog_items
    ]
    scored = [(score, item) for score, item in scored if score > 0]
    if not scored:
        return []
    scored.sort(key=lambda entry: (-entry[0], clean_text(entry[1].get("id"))))
    top_score = scored[0][0]
    tied_items = [item for score, item in scored if score == top_score]
    preferred = None
    if len(tied_items) > 1:
        preferred = resolve_tied_catalog_variant_item(clean_customer_quote_line_text(line.get("text")), tied_items)
    if preferred:
        scored.sort(key=lambda entry: (entry[1] is not preferred, -entry[0], clean_text(entry[1].get("id"))))
    return scored
