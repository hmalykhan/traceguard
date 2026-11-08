"""Unit tests for the deterministic rules: valid cases, contradictions, malformed data."""
from tests.helpers import (
    capture_rec, chargeback_rec, codes, failed_rec, note_rec, order_rec, rec, refund_rec, run,
)


def test_paid_with_failed_attempt_and_duplicate_capture_counts_once():
    cap = capture_rec("pay-2", "C1", attempt="A2")
    dup = dict(cap, ingested_at="2026-10-01T11:59:00Z")  # only ingested_at differs
    out = run([order_rec(), failed_rec("pay-1", "A1"), cap, dup])
    assert out["status"] == "PAID"
    assert out["captured_cents"] == 12900 and out["net_paid_cents"] == 12900
    assert out["evidence_event_ids"] == ["order-1", "pay-1", "pay-2"]
    assert out["anomalies"] == [] and out["confidence"] == "HIGH"
    assert "pay-2" in out["explanation"] and "counted once" in out["explanation"]


def test_unpaid_needs_verified_failure_and_no_capture():
    out = run([order_rec(), failed_rec("pay-1", "A1")])
    assert (out["status"], out["captured_cents"], out["refunded_cents"]) == ("UNPAID", 0, 0)
    assert out["confidence"] == "MEDIUM"


def test_no_payment_evidence_is_unknown_with_null_totals():
    out = run([order_rec(), note_rec("n1", "customer says they paid")])
    assert out["status"] == "UNKNOWN"
    assert out["captured_cents"] is None and out["refunded_cents"] is None and out["net_paid_cents"] is None
    assert "NO_PAYMENT_EVIDENCE" in codes(out, "BLOCKING") and out["confidence"] == "LOW"


def test_partially_paid_and_partially_refunded_and_refunded():
    assert run([order_rec(amount=20000), capture_rec("p1", "C1", 5000)])["status"] == "PARTIALLY_PAID"
    base = [order_rec(), capture_rec("p1", "C1", 12900)]
    assert run(base + [refund_rec("r1", "R1", "C1", 3000)])["status"] == "PARTIALLY_REFUNDED"
    full = run(base + [refund_rec("r1", "R1", "C1", 5000), refund_rec("r2", "R2", "C1", 7900)])
    assert (full["status"], full["net_paid_cents"]) == ("REFUNDED", 0)


def test_open_chargeback_outranks_refund_but_does_not_change_totals():
    out = run([order_rec(), capture_rec("p1", "C1"), refund_rec("r1", "R1", "C1", 12900), chargeback_rec("cb1", "C1")])
    assert out["status"] == "DISPUTED"
    assert (out["captured_cents"], out["refunded_cents"], out["net_paid_cents"]) == (12900, 12900, 0)


def test_missing_checkout_record_is_unknown():
    out = run([capture_rec("p1", "C1")])
    assert out["status"] == "UNKNOWN" and "MISSING_ORDER_EVIDENCE" in codes(out)


def test_conflicting_order_definitions_are_an_integrity_error():
    out = run([order_rec("o-a", 12900), order_rec("o-b", 13900), capture_rec("p1", "C1")])
    assert out["status"] == "UNKNOWN" and "ORDER_CONFLICT" in codes(out)


def test_over_capture_and_currency_mismatch_are_unknown():
    over = run([order_rec(amount=5000), capture_rec("p1", "C1", 6000)])
    assert over["status"] == "UNKNOWN" and "OVER_CAPTURE" in codes(over)
    eur = run([order_rec(), capture_rec("p1", "C1", cur="EUR")])
    assert eur["status"] == "UNKNOWN" and "CURRENCY_MISMATCH" in codes(eur)


def test_refund_must_reference_an_existing_capture():
    out = run([order_rec(), capture_rec("p1", "C1"), refund_rec("r1", "R1", "C-NOPE", 100)])
    assert out["status"] == "UNKNOWN" and "INVALID_REFUND_REFERENCE" in codes(out)


def test_refund_cannot_exceed_its_capture_even_across_refund_records():
    out = run([order_rec(), capture_rec("p1", "C1", 12900),
               refund_rec("r1", "R1", "C1", 8000), refund_rec("r2", "R2", "C1", 8000)])
    assert out["status"] == "UNKNOWN" and "OVER_REFUND" in codes(out)


def test_same_event_id_with_different_amount_is_an_integrity_error():
    out = run([order_rec(), capture_rec("p1", "C1", 12900), capture_rec("p1", "C1", 9900)])
    assert out["status"] == "UNKNOWN" and "EVENT_ID_CONFLICT" in codes(out)


def test_conflicting_records_reusing_a_capture_id_are_errors():
    out = run([order_rec(), capture_rec("p1", "C1", 12900), capture_rec("p2", "C1", 8000)])
    assert out["status"] == "UNKNOWN" and "CAPTURE_ID_CONFLICT" in codes(out)


def test_conflicting_refund_id_reuse_is_an_error():
    out = run([order_rec(), capture_rec("p1", "C1"),
               refund_rec("r1", "R1", "C1", 1000), refund_rec("r2", "R1", "C1", 2000)])
    assert out["status"] == "UNKNOWN" and "REFUND_ID_CONFLICT" in codes(out)


def test_same_attempt_failed_and_captured_is_contradictory():
    out = run([order_rec(), failed_rec("pay-1", "A1"), capture_rec("pay-2", "C1", attempt="A1")])
    assert out["status"] == "UNKNOWN" and "ATTEMPT_CONTRADICTION" in codes(out)


def test_support_source_capture_is_not_payment_evidence():
    out = run([order_rec(), capture_rec("sup-1", "C1", source="support")])
    assert out["status"] == "UNKNOWN"  # nothing from payments, so no state can be established
    assert out["captured_cents"] is None and "UNTRUSTED_SOURCE" in codes(out)


def test_checkout_failure_signal_does_not_create_unpaid():
    out = run([order_rec(), failed_rec("chk-1", "A1", source="checkout")])
    assert out["status"] == "UNKNOWN" and "UNVERIFIED_FAILURE_SIGNAL" in codes(out)
    paid = run([order_rec(), failed_rec("chk-1", "A1", source="checkout"), capture_rec("p1", "C1", attempt="A2")])
    assert paid["status"] == "PAID" and "unverified" in paid["explanation"]


def test_malformed_payments_record_blocks_but_malformed_note_does_not():
    bad_cap = capture_rec("p1", "C1", at="not-a-date")
    out = run([order_rec(), bad_cap])
    assert out["status"] == "UNKNOWN" and "MALFORMED_RECORD" in codes(out, "BLOCKING")
    bad_note = note_rec("n1", "hi", at="yesterday")
    ok = run([order_rec(), capture_rec("p1", "C1"), bad_note])
    assert ok["status"] == "PAID" and ok["confidence"] == "MEDIUM"
    assert "MALFORMED_RECORD" in codes(ok, "WARNING")


def test_invalid_values_are_rejected_not_coerced():
    for bad in (dict(amount_cents="12900"), dict(amount_cents=129.5), dict(amount_cents=True),
                dict(amount_cents=-5), dict(currency="usd"), dict(currency=None)):
        out = run([order_rec(), dict(capture_rec("p1", "C1"), **bad)])
        assert out["status"] == "UNKNOWN", bad


def test_unsupported_type_from_payments_blocks_from_elsewhere_warns():
    base = [order_rec(), capture_rec("p1", "C1")]
    blocked = run(base + [rec("PAYMENT_REVERSED", "p2", "payments", capture_id="C1")])
    assert blocked["status"] == "UNKNOWN" and "MALFORMED_RECORD" in codes(blocked, "BLOCKING")
    warned = run(base + [rec("SOMETHING_ELSE", "x1", "support")])
    assert warned["status"] == "PAID" and warned["confidence"] == "MEDIUM"


def test_tenant_collision_never_combines_data():
    t1 = [order_rec(amount=12900), capture_rec("p1", "C1", 12900)]
    t2 = [order_rec(amount=500, tenant="T2"), failed_rec("p1", "A1", tenant="T2")]
    assert run(t1 + t2, tenant="T1")["status"] == "PAID"
    assert run(t1 + t2, tenant="T2")["status"] == "UNPAID"
    assert run(t1 + t2, tenant="T3")["status"] == "UNKNOWN"


def test_engine_itself_discards_foreign_tenant_events_even_if_a_tool_leaks_them():
    # Defence in depth: bypass the store/workflow and hand reconcile() a mixed pool directly.
    from traceguard.models import parse_event
    from traceguard.reconcile import reconcile
    mine = [parse_event(r) for r in (order_rec(), capture_rec("p1", "C1"))]
    leaked = [parse_event(r) for r in (order_rec("order-1", 1, tenant="T2"),   # same event_id, other tenant
                                       capture_rec("p1", "C1", 1, tenant="T2"),
                                       refund_rec("rx", "RX", "C1", 1, tenant="T2"))]
    res = reconcile("T1", "O-1", mine + leaked)
    assert (res.status, res.captured_cents, res.refunded_cents) == ("PAID", 12900, 0)
    assert res.anomalies == []


def test_capture_id_reused_by_another_order_in_the_same_tenant_is_detected():
    recs = [order_rec(), capture_rec("p1", "C1"),
            order_rec("order-2", order="O-2"), capture_rec("p9", "C1", order="O-2")]
    assert run(recs, order="O-1")["status"] == "UNKNOWN"
    assert run(recs, order="O-2")["status"] == "UNKNOWN"
