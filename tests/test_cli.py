"""End-to-end tests through the real command line."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(ROOT, "data", "events.jsonl")
FIELDS = {"status", "captured_cents", "refunded_cents", "net_paid_cents", "anomalies",
          "evidence_event_ids", "explanation", "confidence"}


def cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), *args],
                          capture_output=True, text=True, cwd=ROOT, timeout=30)


def test_cli_prints_the_exact_output_contract():
    p = cli("--events", EVENTS, "--tenant", "T1", "--order", "O-201", "--question", "Was this order paid?")
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert set(out) == FIELDS
    assert out["status"] == "PAID" and out["evidence_event_ids"] == ["order-201", "pay-201-1", "pay-201-2"]
    assert "warning:" in p.stderr  # unreadable input lines are reported, not hidden


def test_cli_cross_tenant_same_order_id_gives_different_answers():
    t1 = json.loads(cli("--events", EVENTS, "--tenant", "T1", "--order", "O-201").stdout)
    t2 = json.loads(cli("--events", EVENTS, "--tenant", "T2", "--order", "O-201").stdout)
    assert (t1["status"], t2["status"]) == ("PAID", "UNPAID")


def test_cli_injection_case_stays_paid_and_never_echoes_the_note():
    p = cli("--events", EVENTS, "--tenant", "T1", "--order", "O-214")
    out = json.loads(p.stdout)
    assert out["status"] == "PAID" and out["refunded_cents"] == 0
    assert "OVERRIDE" not in p.stdout and any(a["code"] == "NOTE_SUSPICIOUS" for a in out["anomalies"])


def test_cli_failed_tool_call_then_retry_recovers():
    p = cli("--events", EVENTS, "--tenant", "T1", "--order", "O-201",
            "--inject-failure", "get_payment_events:1", "--trace")
    assert json.loads(p.stdout)["status"] == "PAID"
    assert "get_payment_events ok=True attempts=2" in p.stderr


def test_cli_persistent_tool_failure_falls_back_to_unknown():
    p = cli("--events", EVENTS, "--tenant", "T1", "--order", "O-201",
            "--inject-failure", "get_payment_events:9:timeout", "--trace")
    out = json.loads(p.stdout)
    assert out["status"] == "UNKNOWN" and out["captured_cents"] is None
    assert "get_payment_events ok=False attempts=3" in p.stderr


def test_cli_unknown_order_and_missing_file_are_controlled():
    out = json.loads(cli("--events", EVENTS, "--tenant", "T1", "--order", "NOPE").stdout)
    assert out["status"] == "UNKNOWN" and out["evidence_event_ids"] == []
    p = cli("--events", "does-not-exist.jsonl", "--tenant", "T1", "--order", "O-1")
    assert p.returncode == 2 and "error" in json.loads(p.stdout)


def test_the_three_question_types_get_different_wording_but_the_same_decision():
    qs = ["Was order O-201 actually paid?", "Why does checkout show a failure?",
          "What evidence supports the current status?"]
    outs = [json.loads(cli("--events", EVENTS, "--tenant", "T1", "--order", "O-201", "--question", q).stdout)
            for q in qs]
    assert len({json.dumps({k: o[k] for k in o if k != "explanation"}, sort_keys=True) for o in outs}) == 1
    assert len({o["explanation"] for o in outs}) == 3
    assert "Evidence:" in outs[2]["explanation"]
