"""Record model and strict validation for TraceGuard input events.

Everything that enters the reconciliation engine goes through `parse_event`.
Raw dicts that fail validation raise `RecordError` and never become `Event`s.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

ORDER_CREATED = "ORDER_CREATED"
PAYMENT_FAILED = "PAYMENT_FAILED"
PAYMENT_CAPTURED = "PAYMENT_CAPTURED"
REFUND_ISSUED = "REFUND_ISSUED"
CHARGEBACK_OPENED = "CHARGEBACK_OPENED"
NOTE = "NOTE"

EVENT_TYPES = (
    ORDER_CREATED,
    PAYMENT_FAILED,
    PAYMENT_CAPTURED,
    REFUND_ISSUED,
    CHARGEBACK_OPENED,
    NOTE,
)

SRC_CHECKOUT = "checkout"
SRC_PAYMENTS = "payments"

# The only source allowed to establish each kind of fact (rules 01 and 02).
TRUSTED_SOURCE = {
    ORDER_CREATED: SRC_CHECKOUT,
    PAYMENT_FAILED: SRC_PAYMENTS,
    PAYMENT_CAPTURED: SRC_PAYMENTS,
    REFUND_ISSUED: SRC_PAYMENTS,
    CHARGEBACK_OPENED: SRC_PAYMENTS,
}

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_SAFE_TYPE = re.compile(r"^[A-Z_]{1,40}$")


class RecordError(Exception):
    """A raw record failed validation. `code` is a stable machine-readable tag."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Event:
    tenant_id: str
    order_id: str
    event_id: str
    source: str
    type: str
    occurred_at: datetime
    ingested_at: Optional[datetime] = None
    attempt_id: Optional[str] = None
    capture_id: Optional[str] = None
    refund_id: Optional[str] = None
    amount_cents: Optional[int] = None
    currency: Optional[str] = None
    text: Optional[str] = None

    def identity(self) -> tuple:
        """Substantive fields only. `ingested_at` is deliberately excluded (rule 04).

        `text` only counts for notes: on payment records it is commentary, not state.
        """
        return (
            self.tenant_id,
            self.order_id,
            self.event_id,
            self.source,
            self.type,
            self.occurred_at,
            self.attempt_id,
            self.capture_id,
            self.refund_id,
            self.amount_cents,
            self.currency,
            self.text if self.type == NOTE else None,
        )

    def sort_key(self) -> tuple:
        return (self.occurred_at, self.event_id, self.type, self.source)


def is_trusted(event: Event) -> bool:
    """True when the event's source is allowed to establish its kind of fact."""
    return event.type != NOTE and event.source == TRUSTED_SOURCE.get(event.type)


def describe_raw(raw: Mapping) -> dict:
    """Best-effort, never-raising summary of an unvalidated record."""
    def s(key: str) -> Optional[str]:
        v = raw.get(key) if isinstance(raw, Mapping) else None
        return v if isinstance(v, str) and v else None

    return {k: s(k) for k in ("tenant_id", "order_id", "event_id", "source", "type")}


# --- field validators ------------------------------------------------------


def _req_str(raw: Mapping, field: str) -> str:
    v = raw.get(field)
    if not isinstance(v, str) or not v.strip():
        raise RecordError("MISSING_FIELD", f"{field} is missing or not a non-empty string")
    return v


def _opt_str(raw: Mapping, field: str) -> Optional[str]:
    v = raw.get(field)
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        raise RecordError("MALFORMED_RECORD", f"{field} must be a non-empty string when present")
    return v


def _ts(value: Any, field: str, required: bool) -> Optional[datetime]:
    if value is None:
        if required:
            raise RecordError("INVALID_TIMESTAMP", f"{field} is missing")
        return None
    if not isinstance(value, str):
        raise RecordError("INVALID_TIMESTAMP", f"{field} must be an ISO 8601 string")
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise RecordError("INVALID_TIMESTAMP", f"{field} is not valid ISO 8601") from None
    if dt.tzinfo is None:  # assumption: timestamps without an offset are UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _amount(raw: Mapping, required: bool) -> Optional[int]:
    v = raw.get("amount_cents")
    if v is None:
        if required:
            raise RecordError("INVALID_AMOUNT", "amount_cents is missing")
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise RecordError("INVALID_AMOUNT", "amount_cents must be an integer number of cents")
    if v <= 0:
        raise RecordError("INVALID_AMOUNT", "amount_cents must be positive")
    return v


def _currency(raw: Mapping, required: bool) -> Optional[str]:
    v = raw.get("currency")
    if v is None:
        if required:
            raise RecordError("INVALID_CURRENCY", "currency is missing")
        return None
    if not isinstance(v, str) or not _CURRENCY.match(v):
        raise RecordError("INVALID_CURRENCY", "currency must be a 3-letter uppercase ISO code")
    return v


def parse_event(raw: Any) -> Event:
    """Validate one raw record. Raises RecordError; never any other exception."""
    try:
        return _parse(raw)
    except RecordError:
        raise
    except Exception as exc:  # defensive: untrusted input must never crash the engine
        raise RecordError("MALFORMED_RECORD", f"unparseable record ({type(exc).__name__})") from None


def _parse(raw: Any) -> Event:
    if not isinstance(raw, Mapping):
        raise RecordError("MALFORMED_RECORD", "record is not a JSON object")

    etype = raw.get("type")
    if not isinstance(etype, str) or etype not in EVENT_TYPES:
        shown = f" '{etype}'" if isinstance(etype, str) and _SAFE_TYPE.match(etype) else ""
        raise RecordError("UNSUPPORTED_TYPE", f"unsupported or missing event type{shown}")

    tenant_id = _req_str(raw, "tenant_id")
    order_id = _req_str(raw, "order_id")
    event_id = _req_str(raw, "event_id")
    source = _req_str(raw, "source")
    occurred_at = _ts(raw.get("occurred_at"), "occurred_at", required=True)
    ingested_at = _ts(raw.get("ingested_at"), "ingested_at", required=False)

    if etype == NOTE:
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise RecordError("MALFORMED_RECORD", "text must be a string when present")
        return Event(tenant_id, order_id, event_id, source, etype, occurred_at, ingested_at, text=text)

    attempt_id = _opt_str(raw, "attempt_id")
    capture_id = _opt_str(raw, "capture_id")
    refund_id = _opt_str(raw, "refund_id")
    text = raw.get("text") if isinstance(raw.get("text"), str) else None
    amount = currency = None

    if etype == ORDER_CREATED:
        amount, currency = _amount(raw, True), _currency(raw, True)
    elif etype == PAYMENT_FAILED:
        if attempt_id is None:
            raise RecordError("MISSING_FIELD", "attempt_id is required on a failed payment")
    elif etype == PAYMENT_CAPTURED:
        if capture_id is None:
            raise RecordError("MISSING_FIELD", "capture_id is required on a capture")
        amount, currency = _amount(raw, True), _currency(raw, True)
    elif etype == REFUND_ISSUED:
        if capture_id is None or refund_id is None:
            raise RecordError("MISSING_FIELD", "capture_id and refund_id are required on a refund")
        amount, currency = _amount(raw, True), _currency(raw, True)
    elif etype == CHARGEBACK_OPENED:
        if capture_id is None:
            raise RecordError("MISSING_FIELD", "capture_id is required on a chargeback")
        amount = _amount(raw, False)
        currency = _currency(raw, required=amount is not None)

    return Event(
        tenant_id, order_id, event_id, source, etype, occurred_at, ingested_at,
        attempt_id, capture_id, refund_id, amount, currency, text,
    )
