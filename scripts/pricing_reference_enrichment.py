#!/usr/bin/env python3
"""Import-time matching metadata for pricing reference catalog rows."""

from __future__ import annotations

import re
from typing import Any

# The web app uses the package import and the catalogue CLI imports from scripts/.
if __package__:
    from . import pricing_reference_cleanup
else:
    import pricing_reference_cleanup

TEXT_FIXES = pricing_reference_cleanup.import_word_replacements()

STOP_TERMS = {
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "height",
    "including",
    "of",
    "or",
    "per",
    "proposal",
    "ready",
    "rental",
    "the",
    "with",
}

UNIT_PREFIX_RE = re.compile(
    r"^(?:m2|sqm|m\.?\s*length|m\.?\s*run|m\.?|nos?\.?|no\.|sets?|lot\.?)\s+(?:of\s+|rental\s+of\s+)?",
    flags=re.IGNORECASE,
)

TOKEN_ALIASES = {
    "lcd": "led",
    "m2": "sqm",
    "prints": "printed",
    "printing": "printed",
    **TEXT_FIXES,
}


def normalized_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\xa0", " ")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip()


def token(value: str) -> str:
    normalized = TOKEN_ALIASES.get(value.lower(), value.lower())
    if normalized.endswith("ies") and len(normalized) > 4:
        normalized = f"{normalized[:-3]}y"
    elif normalized.endswith("s") and len(normalized) > 3 and not normalized.endswith("ss"):
        normalized = normalized[:-1]
    return TOKEN_ALIASES.get(normalized, normalized)


def tokens_for_text(value: Any) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", normalized_text(value).lower()):
        next_token = token(raw)
        if (len(next_token) <= 2 and next_token != "tv") or next_token in STOP_TERMS:
            continue
        tokens.add(next_token)
    return tokens


def ordered_tokens_for_text(value: Any) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[a-z0-9]+", normalized_text(value).lower()):
        next_token = token(raw)
        if (len(next_token) <= 2 and next_token != "tv") or next_token in STOP_TERMS:
            continue
        if next_token not in tokens:
            tokens.append(next_token)
    return tokens


def add_unique(values: list[str], value: Any, *, limit: int | None = None) -> None:
    text = normalized_text(value).lower()
    if not text:
        return
    if limit is not None and len(text) > limit:
        return
    if text not in values:
        values.append(text)


def stripped_unit_phrase(value: Any) -> str:
    return normalized_text(UNIT_PREFIX_RE.sub("", normalized_text(value)))


def phrase_candidates(value: Any) -> list[str]:
    source = stripped_unit_phrase(value)
    if not source:
        return []
    phrases: list[str] = []
    for part in re.split(r"[;/,]", source):
        cleaned = stripped_unit_phrase(part)
        lowered = cleaned.lower()
        if len(tokens_for_text(lowered)) >= 2:
            add_unique(phrases, lowered, limit=80)
    lowered_source = source.lower()
    if len(tokens_for_text(lowered_source)) >= 2:
        add_unique(phrases, lowered_source, limit=100)
    return phrases


def clean_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = re.split(r"[|;\n]", str(value or ""))
    result: list[str] = []
    for raw in raw_values:
        add_unique(result, raw)
    return result


def family_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    phrases = phrase_candidates(value)
    if not phrases:
        phrases = [stripped_unit_phrase(value)]
    for phrase in phrases:
        tokens = ordered_tokens_for_text(phrase)
        if len(tokens) >= 2:
            add_unique(candidates, "_".join(tokens[:5]), limit=80)
        elif len(tokens) == 1:
            add_unique(candidates, tokens[0], limit=80)
    return candidates


def enrich_pricing_reference_item(item: dict[str, Any]) -> dict[str, Any]:
    section = normalized_text(item.get("reference_section") or item.get("section"))
    section_key = section.lower()
    aliases = [
        alias
        for alias in clean_string_list(item.get("aliases"))
        if not alias.lower().startswith(section_key)
    ]
    source_values: list[Any] = [
        section,
        item.get("description"),
        item.get("unit_hint"),
        *clean_string_list(item.get("remarks")),
        *aliases,
    ]
    content_tokens: set[str] = set()
    for value in source_values:
        content_tokens.update(tokens_for_text(value))
    match_terms: list[str] = clean_string_list(item.get("match_terms"))
    for value in source_values:
        for phrase in phrase_candidates(value):
            add_unique(match_terms, phrase, limit=100)
    for term in sorted(content_tokens):
        if term not in STOP_TERMS and not term.isdigit():
            add_unique(match_terms, term, limit=80)

    item["match_terms"] = match_terms[:36]
    object_families = clean_string_list(item.get("object_families"))
    if not object_families:
        for value in [
            item.get("description"),
            *aliases,
        ]:
            for candidate in family_candidates(value):
                add_unique(object_families, candidate, limit=80)
    item["object_families"] = object_families[:12]
    return item


def enrich_pricing_reference_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        if isinstance(item, dict):
            enrich_pricing_reference_item(item)
    return items
