"""Deterministic explanation writer.

Builds the explanation purely from reconciled facts. Every event id it cites comes from
those facts. Support-note text is never copied into the output.
"""
from __future__ import annotations

from typing import Sequence

from .llm import INTENT_EVIDENCE, INTENT_FAILURE, INTENT_PAID
from .reconcile import (
    BLOCKING, DISPUTED, PAID, PARTIALLY_PAID, PARTIALLY_REFUNDED, REFUNDED, UNKNOWN, UNPAID, Result,
)

_VERDICT = {
    PAID: "Yes, the order was paid in full.",
    UNPAID: "No, the order was not paid.",
    PARTIALLY_PAID: "Only partly: the captured amount is below the order amount.",
    PARTIALLY_REFUNDED: "It was paid, and part of the money was refunded.",
    REFUNDED: "It was paid, and the money was fully refunded.",
    DISPUTED: "It has an open chargeback, so its status is DISPUTED.",
    UNKNOWN: "That cannot be determined reliably from the available evidence.",
}


def _cite(*ids: str) -> str:
    return "[" + ", ".join(ids) + "]"


def build_explanation(res: Result, tenant_id: str, order_id: str, intent: str,
                      tool_notes: Sequence[str] = ()) -> str:
    f = res.facts
    cur = f.currency or ""

    order = []
    if f.expected_cents is not None:
        ids = sorted(e for e, role in f.roles.items() if role == "order definition")
        order.append(f"Checkout expects {f.expected_cents} {cur} cents {_cite(*ids)}.")

    failures = [f"Attempt {a} failed {_cite(e)}." for a, e in f.failed_attempts]
    for a, e, src in f.unverified_failures:
        failures.append(
            f"Source '{src}' reported attempt {a} as failed {_cite(e)}; that is not a payments record, "
            "so it is unverified and does not change the status."
        )

    money = []
    for cid, eid, amount, attempt in f.captures:
        via = f"On attempt {attempt}, capture" if attempt else "Capture"
        money.append(f"{via} {cid} took {amount} cents {_cite(eid)}.")
    for rid, eid, cid, amount in f.refunds:
        money.append(f"Refund {rid} returned {amount} cents of capture {cid} {_cite(eid)}.")
    for eid, cid in f.chargebacks:
        money.append(f"A chargeback is open on capture {cid} {_cite(eid)}; it changes the status, not the totals.")
    if res.captured_cents is not None:
        money.append(f"Captured {res.captured_cents}, refunded {res.refunded_cents}, net paid {res.net_paid_cents} cents.")
    if f.failed_attempts and f.captures:
        money.append("A failed attempt does not cancel a capture on a different attempt.")

    extras = []
    if f.duplicates:
        extras.append("Duplicates not double-counted: "
                      + "; ".join(f"{eid} ({why})" for eid, why in f.duplicates) + ".")
    if f.notes:
        extras.append(f"{len(f.notes)} support note(s) {_cite(*f.notes)} were treated as untrusted text "
                      "and were not used as evidence.")
    blocking = [a for a in res.anomalies if a.severity == BLOCKING]
    if blocking:
        parts = []
        for a in blocking:
            parts.append(f"{a.code}: {a.message}" + (f" {_cite(*a.event_ids)}" if a.event_ids else ""))
        extras.append("Why the status cannot be established: " + " ".join(parts))
    extras.extend(tool_notes)

    if intent == INTENT_FAILURE:
        sections = [[_VERDICT[res.status]], failures, money, order, extras]
    elif intent == INTENT_PAID:
        sections = [[_VERDICT[res.status]], order, failures, money, extras]
    else:
        sections = [[f"Order {order_id} (tenant {tenant_id}) is {res.status}."], order, failures, money, extras]

    if intent == INTENT_EVIDENCE:
        roles = [f"{eid}: {f.roles[eid]}" for eid in res.evidence_event_ids if eid in f.roles]
        if roles:
            sections.append(["Evidence: " + "; ".join(roles) + "."])

    return " ".join(s for block in sections for s in block)
