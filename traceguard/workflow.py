"""Investigation workflow: interpret -> call tools -> validate -> reconcile -> explain.

The model may only influence `intent` (emphasis of the explanation). Status, totals,
anomalies, evidence and confidence all come from `reconcile`.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .explain import build_explanation
from .llm import INTENT_GENERAL, VALID_INTENTS, ModelProvider, RuleBasedModel
from .models import (
    CHARGEBACK_OPENED, NOTE, ORDER_CREATED, PAYMENT_CAPTURED, PAYMENT_FAILED, REFUND_ISSUED,
    SRC_CHECKOUT, SRC_PAYMENTS, Event, RecordError, describe_raw, parse_event,
)
from .reconcile import BLOCKING, INFO, WARNING, Anomaly, reconcile
from .tools import RetryPolicy, Tools, call_with_retry

_PAYMENT_TYPES = (PAYMENT_FAILED, PAYMENT_CAPTURED, REFUND_ISSUED, CHARGEBACK_OPENED)

# tool -> (record types it may legitimately return, is its failure blocking?)
_TOOL_SPEC = {
    "get_order": ((ORDER_CREATED,), True),
    "get_payment_events": (_PAYMENT_TYPES, True),
    "get_support_notes": ((NOTE,), False),
}


def interpret(model: ModelProvider, question: str) -> str:
    """Ask the model for an intent; anything invalid or failing degrades to GENERAL."""
    try:
        intent = model.interpret(question)
    except Exception:  # noqa: BLE001 - a broken model must not break the investigation
        return INTENT_GENERAL
    return intent if isinstance(intent, str) and intent in VALID_INTENTS else INTENT_GENERAL


def _rejection_severity(raw: dict, err: RecordError, tool: str, order_id: str) -> str:
    """Decide whether a malformed record makes the financial answer unreliable.

    A malformed record only blocks when it claims to come from the source that is
    allowed to establish that kind of fact (or its source is unreadable), because then
    it might have been real evidence. Malformed noise from other sources cannot be
    allowed to force UNKNOWN, or anyone able to write a note could do it.
    """
    info = describe_raw(raw)
    if not _TOOL_SPEC[tool][1]:                     # notes tool: never evidence
        return WARNING
    if info["order_id"] not in (None, order_id):    # linked record of another order
        return WARNING
    src, typ = info["source"], info["type"]
    if err.code == "UNSUPPORTED_TYPE":
        return BLOCKING if src in (None, SRC_PAYMENTS) else WARNING
    if typ == NOTE or typ == PAYMENT_FAILED:
        return WARNING
    if typ == ORDER_CREATED:
        return BLOCKING if src in (None, SRC_CHECKOUT) else WARNING
    return BLOCKING if src in (None, SRC_PAYMENTS) else WARNING


def _ingest(tool: str, raw_records, tenant_id: str, order_id: str, anomalies: list) -> List[Event]:
    allowed, _ = _TOOL_SPEC[tool]
    events: List[Event] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            anomalies.append(Anomaly("TOOL_BAD_RECORD", WARNING, f"{tool} returned a non-object record; dropped"))
            continue
        info = describe_raw(raw)
        if info["tenant_id"] != tenant_id:
            anomalies.append(Anomaly("TOOL_FOREIGN_TENANT", WARNING,
                                     f"{tool} returned a record outside tenant {tenant_id}; dropped",
                                     (info["event_id"],) if info["event_id"] else ()))
            continue
        try:
            ev = parse_event(raw)
        except RecordError as err:
            ids = (info["event_id"],) if info["event_id"] else ()
            anomalies.append(Anomaly("MALFORMED_RECORD", _rejection_severity(raw, err, tool, order_id),
                                     f"{err.code}: {err.message}", ids))
            continue
        if ev.type not in allowed:
            anomalies.append(Anomaly("TOOL_UNEXPECTED_RECORD", WARNING,
                                     f"{tool} returned a {ev.type} record, which it should not; dropped",
                                     (ev.event_id,)))
            continue
        events.append(ev)
    return events


def investigate_with_trace(tools: Tools, tenant_id: str, order_id: str, question: str,
                           model: Optional[ModelProvider] = None,
                           policy: Optional[RetryPolicy] = None) -> Tuple[dict, list]:
    model = model or RuleBasedModel()
    policy = policy or RetryPolicy()
    intent = interpret(model, question)

    anomalies: list = []
    events: List[Event] = []
    trace: list = []
    tool_notes: list = []

    for name, (_, blocking) in _TOOL_SPEC.items():
        outcome = call_with_retry(lambda n=name: getattr(tools, n)(tenant_id, order_id), policy)
        trace.append({"tool": name, "ok": outcome.ok, "attempts": outcome.attempts, "error": outcome.error})
        if outcome.ok and not isinstance(outcome.data, list):
            outcome.ok, outcome.error = False, "response was not a list of records"
            anomalies.append(Anomaly("TOOL_BAD_RESPONSE", BLOCKING if blocking else WARNING,
                                     f"{name} returned a malformed response"))
            tool_notes.append(f"{name} returned a malformed response and was ignored.")
            continue
        if not outcome.ok:
            anomalies.append(Anomaly("TOOL_FAILURE", BLOCKING if blocking else WARNING,
                                     f"{name} failed after {outcome.attempts} attempt(s): {outcome.error}"))
            tool_notes.append(f"{name} was unavailable after {outcome.attempts} attempt(s), so its data is missing.")
            continue
        if outcome.attempts > 1:
            tool_notes.append(f"{name} succeeded on attempt {outcome.attempts} after a transient failure.")
        events.extend(_ingest(name, outcome.data, tenant_id, order_id, anomalies))

    result = reconcile(tenant_id, order_id, events, anomalies)
    explanation = build_explanation(result, tenant_id, order_id, intent, tool_notes)
    return result.to_output(explanation), trace


def investigate(tools: Tools, tenant_id: str, order_id: str, question: str,
                model: Optional[ModelProvider] = None, policy: Optional[RetryPolicy] = None) -> dict:
    return investigate_with_trace(tools, tenant_id, order_id, question, model, policy)[0]
