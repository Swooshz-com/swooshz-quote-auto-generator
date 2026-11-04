"""Durable accounting for sends made on behalf of an editable quote."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
import secrets


OPERATIONS = {
    "draft": frozenset({"draft", "draft_high_quality"}),
    "basis_chat": frozenset({"basis_line", "basis_answer"}),
}


class QuoteSpendUnavailable(RuntimeError):
    """A send could not be safely reserved."""


class QuoteSpendReplay(RuntimeError):
    """This exact quote operation already has a durable claim."""


def settle_cost(value):
    if type(value) not in (int, float, Decimal):
        return None
    cost = value if isinstance(value, Decimal) else Decimal(str(value))
    if not cost.is_finite() or not 0 <= cost <= 2**53 - 1:
        return None
    if cost.is_zero():
        return "0"
    # Bound storage and arithmetic even when an upstream response is malformed.
    if len(cost.as_tuple().digits) > 1024 or cost.as_tuple().exponent < -1024:
        return None
    return str(cost)


def _amount(costs, complete):
    known = [Decimal(cost) for cost in costs if cost is not None]
    display = None
    if known:
        with localcontext() as context:
            # Provider costs have bounded scale and magnitude (see settle_cost).
            context.prec = 1042 + len(str(len(known)))
            total = sum(known, Decimal(0))
            display = "< USD 0.01" if 0 < total < Decimal("0.01") else (
                f"USD {total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
            )
    if not costs:
        status = "none" if complete else "unavailable"
    else:
        status = "reported" if complete and len(known) == len(costs) else "partial"
    return {"status": status, "display": display}


def spend_summary(records, *, complete, total_only=False):
    costs = {category: [] for category in OPERATIONS}
    for record in records:
        costs[record["category"]].extend(None if value is False else value for value in record["attempts"])
    summary = {"total": _amount(costs["draft"] + costs["basis_chat"], complete)}
    if not total_only:
        summary.update({category: _amount(values, complete) for category, values in costs.items()})
    return summary


@dataclass(frozen=True)
class QuoteSpendBinding:
    session_id: str
    workspace_id: str
    actor_id: str
    incarnation: str
    category: str
    store: object

    @property
    def identity(self):
        return self.session_id, self.workspace_id, self.actor_id, self.incarnation

    def matches_claim(self, record):
        return (
            record["actor_id"] == self.actor_id
            and record["category"] == self.category
            and record["incarnation"] == self.incarnation
        )

    def claim(self, job_id):
        token = secrets.token_hex(16)
        if not self.store.claim_quote_spend(self, job_id, token):
            return None
        return QuoteSpendOperation(self, job_id, token)


class QuoteSpendOperation:
    def __init__(self, binding, job_id, token):
        self.binding = binding
        self.job_id = job_id
        self.token = token

    def tracks(self, operation):
        return operation in OPERATIONS[self.binding.category]

    def reserve(self):
        def append(attempts):
            attempts.append(False)
            return len(attempts)

        ordinal = self.binding.store.change_quote_spend(self, append)
        if not ordinal:
            raise QuoteSpendUnavailable()
        return ordinal

    def settle(self, ordinal, cost):
        admitted = settle_cost(cost)
        def finish(attempts):
            if 0 < ordinal <= len(attempts) and attempts[ordinal - 1] is False:
                attempts[ordinal - 1] = admitted

        self.binding.store.change_quote_spend(self, finish)
