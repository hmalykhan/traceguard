"""Checks the engine against hand-written expectations for the 196-event example dataset."""
import json
import os

import pytest

from traceguard import StoreTools, investigate, load_jsonl
from tests.helpers import NO_SLEEP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(ROOT, "data", "events.jsonl")
EXPECTED = json.load(open(os.path.join(ROOT, "data", "expected.json"), encoding="utf-8"))


@pytest.fixture(scope="module")
def tools_and_ids():
    store, errors = load_jsonl(EVENTS)
    ids = set()
    for line in open(EVENTS, encoding="utf-8"):
        try:
            ids.add(json.loads(line).get("event_id"))
        except json.JSONDecodeError:
            pass
    return StoreTools(store), ids, errors


def test_dataset_is_large_and_loader_reports_bad_lines(tools_and_ids):
    _, ids, errors = tools_and_ids
    assert len(ids) >= 100
    assert len(errors) == 2  # one invalid JSON line, one record with no tenant/order


@pytest.mark.parametrize("case", EXPECTED, ids=lambda c: f"{c['tenant']}-{c['order']}")
def test_every_dataset_order_matches_expectation(case, tools_and_ids):
    tools, ids, _ = tools_and_ids
    out = investigate(tools, case["tenant"], case["order"], "Was this order paid?", policy=NO_SLEEP)
    assert out["status"] == case["status"], case["why"]
    assert out["captured_cents"] == case["captured_cents"]
    assert out["refunded_cents"] == case["refunded_cents"]
    assert out["net_paid_cents"] == case["net_paid_cents"]
    assert set(out["evidence_event_ids"]) <= ids  # evidence must exist in the input
    if out["status"] == "UNKNOWN":
        assert out["confidence"] == "LOW"
        assert out["captured_cents"] is None
