"""Shared builders for tests."""
from traceguard import EventStore, RetryPolicy, StoreTools, investigate

NO_SLEEP = RetryPolicy(max_attempts=3, timeout_s=1.0, backoff_s=0.0, sleep=lambda s: None)

T, O = "T1", "O-1"


def rec(type_, event_id, source, *, tenant=T, order=O, at="2026-10-01T10:00:00Z", **kw):
    r = {"tenant_id": tenant, "order_id": order, "event_id": event_id, "source": source,
         "type": type_, "occurred_at": at, "ingested_at": "2026-10-01T10:00:05Z"}
    r.update(kw)
    return r


def order_rec(eid="order-1", amount=12900, cur="USD", **kw):
    return rec("ORDER_CREATED", eid, "checkout", amount_cents=amount, currency=cur, **kw)


def failed_rec(eid, attempt, source="payments", **kw):
    return rec("PAYMENT_FAILED", eid, source, attempt_id=attempt, **kw)


def capture_rec(eid, cid, amount=12900, attempt=None, cur="USD", source="payments", **kw):
    extra = {"attempt_id": attempt} if attempt else {}
    return rec("PAYMENT_CAPTURED", eid, source, capture_id=cid, amount_cents=amount, currency=cur, **extra, **kw)


def refund_rec(eid, rid, cid, amount, cur="USD", **kw):
    return rec("REFUND_ISSUED", eid, "payments", refund_id=rid, capture_id=cid, amount_cents=amount, currency=cur, **kw)


def chargeback_rec(eid, cid, **kw):
    return rec("CHARGEBACK_OPENED", eid, "payments", capture_id=cid, **kw)


def note_rec(eid, text, source="support", **kw):
    return rec("NOTE", eid, source, text=text, **kw)


def run(records, tenant=T, order=O, question="Was this order paid?", model=None, tools=None, policy=NO_SLEEP):
    tools = tools or StoreTools(EventStore(records))
    return investigate(tools, tenant, order, question, model=model, policy=policy)


def codes(out, severity=None):
    return sorted(a["code"] for a in out["anomalies"] if severity is None or a["severity"] == severity)


def decision(out):
    """The parts of an answer that are financial truth (not wording)."""
    return {k: out[k] for k in ("status", "captured_cents", "refunded_cents", "net_paid_cents",
                                "evidence_event_ids", "confidence")}
