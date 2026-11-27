"""Quote request and job states and the moves allowed between them.

The future quote application service owns these transitions; the API spec and the
contract tests read them from here. Keys are in display order.
"""
from types import MappingProxyType


REQUEST_TRANSITIONS = MappingProxyType({
    "drafting": ("awaiting_confirmation", "needs_input", "failed", "expired"),
    "awaiting_confirmation": ("drafting", "pricing", "generating", "expired"),
    "pricing": ("awaiting_confirmation", "needs_input", "failed", "expired"),
    "generating": ("completed", "needs_input", "failed", "expired"),
    "completed": ("drafting", "expired"),
    "needs_input": ("drafting", "expired"),
    "failed": ("drafting", "expired"),
    "expired": (),
})
JOB_TRANSITIONS = MappingProxyType({
    "queued": ("running", "failed"),
    "running": ("completed", "failed"),
    "completed": (),
    "failed": (),
})
REQUEST_STATES = tuple(REQUEST_TRANSITIONS)
JOB_STATES = tuple(JOB_TRANSITIONS)
