"""In-memory event store backing the three tool interfaces.

Records are kept as raw dicts: validation happens in one place (`models.parse_event`)
on the workflow side, so the tools stay dumb data access.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Iterable, Tuple

from .models import NOTE, ORDER_CREATED

_ID_FIELDS = ("event_id", "capture_id", "refund_id")


def category(record: dict) -> str:
    t = record.get("type")
    if t == ORDER_CREATED:
        return "order"
    if t == NOTE:
        return "notes"
    return "payments"  # includes unsupported types, so they are surfaced rather than hidden


class EventStore:
    def __init__(self, records: Iterable = ()):
        self._by_order: dict = defaultdict(list)
        self._by_key: dict = defaultdict(list)
        self.load_errors: list = []
        for i, rec in enumerate(records, 1):
            self.add(rec, i)

    def add(self, rec, line_no=None) -> None:
        where = f"line {line_no}" if line_no is not None else "record"
        if not isinstance(rec, dict):
            self.load_errors.append(f"{where}: not a JSON object")
            return
        tenant, order = rec.get("tenant_id"), rec.get("order_id")
        if not (isinstance(tenant, str) and tenant and isinstance(order, str) and order):
            self.load_errors.append(f"{where}: missing tenant_id/order_id, cannot be attributed to any order")
            return
        self._by_order[(tenant, order)].append(rec)
        for f in _ID_FIELDS:
            v = rec.get(f)
            if isinstance(v, str) and v:
                self._by_key[(tenant, f, v)].append(rec)

    def _collect(self, tenant_id: str, order_id: str) -> list:
        """Records of this order plus same-tenant records of OTHER orders that reuse one of
        its event/capture/refund ids, so identity reuse across orders can be detected.
        Nothing from another tenant is ever returned (rule 09)."""
        own = list(self._by_order.get((tenant_id, order_id), []))
        seen = {id(r) for r in own}
        linked = []
        for r in own:
            for f in _ID_FIELDS:
                v = r.get(f)
                if isinstance(v, str) and v:
                    for other in self._by_key.get((tenant_id, f, v), []):
                        if id(other) not in seen:
                            seen.add(id(other))
                            linked.append(other)
        return own + linked

    def records(self, tenant_id: str, order_id: str, wanted: str) -> list:
        return [copy.deepcopy(r) for r in self._collect(tenant_id, order_id) if category(r) == wanted]


def load_jsonl(path: str) -> Tuple[EventStore, list]:
    """Read a JSON Lines file. Bad lines are collected, never raised."""
    store = EventStore()
    errors: list = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {n}: invalid JSON ({exc.msg})")
                continue
            store.add(rec, n)
    return store, errors + store.load_errors
