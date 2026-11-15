"""Wire contracts for AI pricing import and matching metadata."""
from types import MappingProxyType

from webapp.ai_response_contracts import Field, ResponseContract, obj


_OPTIONAL_TEXT = Field("string", nullable=True)
_OPTIONAL_NUMBER = Field("number", nullable=True, decimal_wire=True)
_STRING_LIST = Field("array", item=Field("string"))
_ORDER = Field("integer", positive=True, decimal_wire=True)

PRICING_IMPORT = ResponseContract("pricing_import_v1", obj(
    currency=_OPTIONAL_TEXT,
    items=Field("array", item=obj(
        section=_OPTIONAL_TEXT,
        description=_OPTIONAL_TEXT,
        unit_hint=_OPTIONAL_TEXT,
        internal_cost=_OPTIONAL_NUMBER,
        markup_multiplier=_OPTIONAL_NUMBER,
        remarks=_STRING_LIST,
        aliases=_STRING_LIST,
        category_order=_ORDER,
        item_order=_ORDER,
        source_row_index=Field("integer", nullable=True, positive=True, decimal_wire=True),
        warning=_OPTIONAL_TEXT,
        status=_OPTIONAL_TEXT,
    )),
), exact_decimals=True)

PRICING_METADATA = ResponseContract("pricing_metadata_v1", obj(
    items=Field("array", item=obj(
        id=Field("string", nonblank=True),
        match_terms=_STRING_LIST,
        object_families=_STRING_LIST,
    )),
))

PRICING_RESPONSE_CONTRACTS = MappingProxyType({
    "pricing_import": PRICING_IMPORT,
    "pricing_metadata": PRICING_METADATA,
})
