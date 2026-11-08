"""Deterministic reconciliation engine.

Pure function of validated `Event`s. No I/O, no clock, no randomness and no
language model: the same set of events always yields the same result, in any order.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import (
    CHARGEBACK_OPENED,
    NOTE,
    ORDER_CREATED,
    PAYMENT_CAPTURED,
    PAYMENT_FAILED,
    REFUND_ISSUED,
    Event,
    is_trusted,
)

PAID = "PAID"
UNPAID = "UNPAID"
PARTIALLY_PAID = "PARTIALLY_PAID"
PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
REFUNDED = "REFUNDED"
DISPUTED = "DISPUTED"
UNKNOWN = "UNKNOWN"
STATUSES = (PAID, UNPAID, PARTIALLY_PAID, PARTIALLY_REFUNDED, REFUNDED, DISPUTED, UNKNOWN)

BLOCKING = "BLOCKING"  # integrity failure: forces UNKNOWN
WARNING = "WARNING"    # something was ignored or odd: lowers confidence
INFO = "INFO"          # worth knowing, no effect on confidence
_RANK = {BLOCKING: 0, WARNING: 1, INFO: 2}

# Heuristic only. It labels notes for humans; it never gates any financial logic.
_SUSPICIOUS = re.compile(
    r"ignore (all )?(previous|prior|above)|system override|disregard|"
    r"do not mention|mark (this|the) order|you are now|new instructions",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Anomaly:
    code: str
    severity: str
    message: str
    event_ids: tuple = ()

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "event_ids": list(self.event_ids),
        }

    def sort_key(self) -> tuple:
        return (_RANK[self.severity], self.code, self.event_ids, self.message)


@dataclass
class Facts:
    """Everything the explanation writer may cite. All lists are sorted for determinism."""

    expected_cents: Optional[int] = None
    currency: Optional[str] = None
    failed_attempts: list = field(default_factory=list)       # (attempt_id, event_id)
    captures: list = field(default_factory=list)              # (capture_id, event_id, amount, attempt_id)
    refunds: list = field(default_factory=list)               # (refund_id, event_id, capture_id, amount)
    chargebacks: list = field(default_factory=list)           # (event_id, capture_id)
    duplicates: list = field(default_factory=list)            # (event_id, description)
    notes: list = field(default_factory=list)                 # event ids of support notes
    unverified_failures: list = field(default_factory=list)   # (attempt_id, event_id, source)
    roles: dict = field(default_factory=dict)                 # event_id -> role


@dataclass
class Result:
    status: str
    captured_cents: Optional[int]
    refunded_cents: Optional[int]
    net_paid_cents: Optional[int]
    anomalies: list
    evidence_event_ids: list
    confidence: str
    facts: Facts

    def to_output(self, explanation: str) -> dict:
        return {
            "status": self.status,
            "captured_cents": self.captured_cents,
            "refunded_cents": self.refunded_cents,
            "net_paid_cents": self.net_paid_cents,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "evidence_event_ids": list(self.evidence_event_ids),
            "explanation": explanation,
            "confidence": self.confidence,
        }


def _group(events: Iterable[Event], key) -> dict:
    groups: dict = defaultdict(list)
    for e in events:
        groups[key(e)].append(e)
    return {k: sorted(groups[k], key=lambda e: e.sort_key()) for k in sorted(groups)}


def reconcile(tenant_id: str, order_id: str, events: Iterable[Event], extra_anomalies=()) -> Result:
    """Reconcile one order.

    `events` may include events of other orders in the same tenant (the store hands those
    over when they share an event/capture/refund id with this order, so reuse of a
    transaction id across orders is detected). They are never added to this order's totals.
    Events of other tenants are discarded outright (rule 09).
    """
    anomalies: list = list(extra_anomalies)
    facts = Facts()
    evs = sorted((e for e in events if e.tenant_id == tenant_id), key=lambda e: e.sort_key())

    # --- 1. event identity: collapse identical duplicates, flag disagreements (rules 04, 05)
    unique: list = []
    for eid, group in _group(evs, lambda e: e.event_id).items():
        distinct: dict = {}
        for e in group:
            distinct.setdefault(e.identity(), e)
        if len(distinct) > 1:
            relevant = any(is_trusted(e) for e in distinct.values())
            anomalies.append(Anomaly(
                "EVENT_ID_CONFLICT",
                BLOCKING if relevant else WARNING,
                "records sharing this event_id disagree on substantive fields",
                (eid,),
            ))
            continue
        if len(group) > 1:
            facts.duplicates.append((eid, f"{len(group)} identical copies, counted once"))
        unique.append(next(iter(distinct.values())))

    own = [e for e in unique if e.order_id == order_id]
    others = [e for e in unique if e.order_id != order_id and is_trusted(e)]

    # --- 2. source trust (rules 01, 02): only checkout defines orders, only payments moves money
    trusted: list = []
    for e in own:
        if e.type == NOTE:
            facts.notes.append(e.event_id)
            if e.text and _SUSPICIOUS.search(e.text):
                anomalies.append(Anomaly(
                    "NOTE_SUSPICIOUS", INFO,
                    "support note contains instruction-like text; treated as untrusted data and ignored",
                    (e.event_id,),
                ))
        elif is_trusted(e):
            trusted.append(e)
        elif e.type == PAYMENT_FAILED:
            facts.unverified_failures.append((e.attempt_id, e.event_id, e.source))
            anomalies.append(Anomaly(
                "UNVERIFIED_FAILURE_SIGNAL", INFO,
                f"failure reported by non-payments source '{e.source}'; not verified payment evidence",
                (e.event_id,),
            ))
        else:
            anomalies.append(Anomaly(
                "UNTRUSTED_SOURCE", WARNING,
                f"{e.type} from source '{e.source}' ignored: only trusted sources may establish this fact",
                (e.event_id,),
            ))
    pool = sorted(trusted + others, key=lambda e: e.sort_key())

    # --- 3. order definition (rule 01)
    order_defs = [e for e in trusted if e.type == ORDER_CREATED]
    expected: Optional[int] = None
    currency: Optional[str] = None
    if not order_defs:
        anomalies.append(Anomaly("MISSING_ORDER_EVIDENCE", BLOCKING,
                                 "no valid checkout record defines this order's amount and currency"))
    else:
        definitions = {(e.amount_cents, e.currency) for e in order_defs}
        if len(definitions) > 1:
            anomalies.append(Anomaly("ORDER_CONFLICT", BLOCKING,
                                     "checkout records define this order with different amounts or currencies",
                                     tuple(sorted(e.event_id for e in order_defs))))
        else:
            expected, currency = next(iter(definitions))
            facts.expected_cents, facts.currency = expected, currency
            for e in order_defs:
                facts.roles[e.event_id] = "order definition"

    # --- 4. captures (rules 03, 06, 10)
    valid_caps: dict = {}
    cap_events = [e for e in pool if e.type == PAYMENT_CAPTURED]
    for cid, group in _group(cap_events, lambda e: e.capture_id).items():
        signatures = {(e.order_id, e.amount_cents, e.currency, e.attempt_id) for e in group}
        if len(signatures) > 1:
            anomalies.append(Anomaly(
                "CAPTURE_ID_CONFLICT", BLOCKING,
                f"capture {cid} is reused by records that disagree on order, amount, currency or attempt",
                tuple(sorted(e.event_id for e in group)),
            ))
            continue
        if group[0].order_id != order_id:
            continue
        valid_caps[cid] = group[0]
        facts.roles[group[0].event_id] = f"capture {cid}"
        for extra in group[1:]:
            facts.duplicates.append((extra.event_id, f"capture {cid} re-sent under a new event id, counted once"))

    captured = sum(e.amount_cents for e in valid_caps.values())
    for cid, e in valid_caps.items():
        if currency is not None and e.currency != currency:
            anomalies.append(Anomaly("CURRENCY_MISMATCH", BLOCKING,
                                     f"capture {cid} is in {e.currency} but the order is in {currency}",
                                     (e.event_id,)))
    if expected is not None and captured > expected:
        anomalies.append(Anomaly("OVER_CAPTURE", BLOCKING,
                                 f"captured {captured} exceeds the expected order amount {expected}",
                                 tuple(sorted(e.event_id for e in valid_caps.values()))))

    failed = [e for e in trusted if e.type == PAYMENT_FAILED]
    for e in failed:
        facts.roles[e.event_id] = f"failed attempt {e.attempt_id}"
    cap_attempts = {e.attempt_id for e in valid_caps.values() if e.attempt_id}
    clash = sorted({e.attempt_id for e in failed} & cap_attempts)
    if clash:
        ids = [e.event_id for e in failed if e.attempt_id in clash]
        ids += [e.event_id for e in valid_caps.values() if e.attempt_id in clash]
        anomalies.append(Anomaly("ATTEMPT_CONTRADICTION", BLOCKING,
                                 f"attempt(s) {', '.join(clash)} are recorded as both failed and captured",
                                 tuple(sorted(ids))))

    # --- 5. refunds (rules 06, 07)
    valid_refunds: dict = {}
    ref_events = [e for e in pool if e.type == REFUND_ISSUED]
    for rid, group in _group(ref_events, lambda e: e.refund_id).items():
        signatures = {(e.order_id, e.capture_id, e.amount_cents, e.currency) for e in group}
        if len(signatures) > 1:
            anomalies.append(Anomaly(
                "REFUND_ID_CONFLICT", BLOCKING,
                f"refund {rid} is reused by records that disagree on order, capture, amount or currency",
                tuple(sorted(e.event_id for e in group)),
            ))
            continue
        if group[0].order_id != order_id:
            continue
        valid_refunds[rid] = group[0]
        for extra in group[1:]:
            facts.duplicates.append((extra.event_id, f"refund {rid} re-sent under a new event id, counted once"))

    refunded = 0
    per_capture: dict = defaultdict(list)
    for rid, e in valid_refunds.items():
        cap = valid_caps.get(e.capture_id)
        if cap is None:
            anomalies.append(Anomaly("INVALID_REFUND_REFERENCE", BLOCKING,
                                     f"refund {rid} references capture {e.capture_id}, which is not a valid capture of this order",
                                     (e.event_id,)))
            continue
        if e.currency != cap.currency:
            anomalies.append(Anomaly("CURRENCY_MISMATCH", BLOCKING,
                                     f"refund {rid} is in {e.currency} but capture {cap.capture_id} is in {cap.currency}",
                                     (e.event_id, cap.event_id)))
            continue
        per_capture[e.capture_id].append(e)
        refunded += e.amount_cents
        facts.roles[e.event_id] = f"refund {rid} of capture {e.capture_id}"
        facts.refunds.append((rid, e.event_id, e.capture_id, e.amount_cents))
    for cid, refs in per_capture.items():
        cap = valid_caps[cid]
        if sum(r.amount_cents for r in refs) > cap.amount_cents:
            anomalies.append(Anomaly("OVER_REFUND", BLOCKING,
                                     f"refunds against capture {cid} exceed its captured amount {cap.amount_cents}",
                                     tuple(sorted([r.event_id for r in refs] + [cap.event_id]))))

    # --- 6. chargebacks
    valid_chargebacks: list = []
    for e in (x for x in trusted if x.type == CHARGEBACK_OPENED):
        cap = valid_caps.get(e.capture_id)
        if cap is None:
            anomalies.append(Anomaly("INVALID_CHARGEBACK_REFERENCE", BLOCKING,
                                     f"chargeback references capture {e.capture_id}, which is not a valid capture of this order",
                                     (e.event_id,)))
        elif e.currency is not None and e.currency != cap.currency:
            anomalies.append(Anomaly("CURRENCY_MISMATCH", BLOCKING,
                                     f"chargeback is in {e.currency} but capture {cap.capture_id} is in {cap.currency}",
                                     (e.event_id, cap.event_id)))
        else:
            valid_chargebacks.append(e)
            facts.roles[e.event_id] = f"open chargeback on capture {e.capture_id}"
            facts.chargebacks.append((e.event_id, e.capture_id))

    # --- 7. status, in the mandated order of precedence
    blocking = any(a.severity == BLOCKING for a in anomalies)
    if blocking:
        status = UNKNOWN
    elif valid_chargebacks:
        status = DISPUTED
    elif not valid_caps and failed:
        status = UNPAID
    elif captured > 0 and refunded == captured:
        status = REFUNDED
    elif 0 < refunded < captured:
        status = PARTIALLY_REFUNDED
    elif expected is not None and captured == expected:
        status = PAID
    elif expected is not None and 0 < captured < expected:
        status = PARTIALLY_PAID
    else:
        status = UNKNOWN
        anomalies.append(Anomaly("NO_PAYMENT_EVIDENCE", BLOCKING,
                                 "no valid capture and no verified payment failure: payment state cannot be established"))

    # --- 8. assemble facts, evidence, confidence
    for e in failed:
        facts.failed_attempts.append((e.attempt_id, e.event_id))
    for cid, e in valid_caps.items():
        facts.captures.append((cid, e.event_id, e.amount_cents, e.attempt_id))
    facts.failed_attempts.sort()
    facts.captures.sort()
    facts.refunds.sort()
    facts.chargebacks.sort()
    facts.duplicates.sort()
    facts.notes.sort()
    facts.unverified_failures.sort(key=lambda t: (str(t[0]), t[1], t[2]))

    evidence = set(facts.roles)  # only records that established a fact; ignored duplicates are not evidence
    if status == UNKNOWN:
        for a in anomalies:
            if a.severity == BLOCKING:
                evidence.update(a.event_ids)
    anomalies.sort(key=lambda a: a.sort_key())

    if status == UNKNOWN:
        totals = (None, None, None)
        confidence = "LOW"
    else:
        totals = (captured, refunded, captured - refunded)
        weak = status in (UNPAID, PARTIALLY_PAID) or any(a.severity == WARNING for a in anomalies)
        confidence = "MEDIUM" if weak else "HIGH"

    return Result(status, *totals, anomalies, sorted(evidence), confidence, facts)
