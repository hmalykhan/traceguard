#!/usr/bin/env python3
"""TraceGuard CLI.

    python run.py --events data/events.jsonl --tenant T1 --order O-201 \\
        --question "Was this order paid?"
"""
from __future__ import annotations

import argparse
import json
import sys

from traceguard import FaultInjectingTools, RetryPolicy, StoreTools, investigate_with_trace, load_jsonl
from traceguard.tools import parse_fault_spec


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TraceGuard: investigate an order's payment state.")
    p.add_argument("--events", required=True, help="path to a JSON Lines events file")
    p.add_argument("--tenant", required=True)
    p.add_argument("--order", required=True)
    p.add_argument("--question", default="What is the payment status of this order?")
    p.add_argument("--inject-failure", action="append", default=[], metavar="TOOL[:COUNT[:KIND]]",
                   help="demo: make a tool fail COUNT times (KIND error|timeout) to show retry/fallback")
    p.add_argument("--max-attempts", type=int, default=3, help="max calls per tool (default 3)")
    p.add_argument("--timeout", type=float, default=2.0, help="per-attempt tool timeout, seconds")
    p.add_argument("--trace", action="store_true", help="print the tool-call trace to stderr")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        store, load_errors = load_jsonl(args.events)
    except OSError as exc:
        print(json.dumps({"error": f"cannot read events file: {exc.strerror or exc}"}), file=sys.stdout)
        return 2

    for msg in load_errors[:5]:
        print(f"warning: {msg}", file=sys.stderr)
    if len(load_errors) > 5:
        print(f"warning: ... and {len(load_errors) - 5} more unreadable input line(s)", file=sys.stderr)

    tools = StoreTools(store)
    if args.inject_failure:
        try:
            faults = dict(parse_fault_spec(s) for s in args.inject_failure)
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}))
            return 2
        tools = FaultInjectingTools(tools, faults)

    policy = RetryPolicy(max_attempts=args.max_attempts, timeout_s=args.timeout)
    output, trace = investigate_with_trace(tools, args.tenant, args.order, args.question, policy=policy)

    if args.trace:
        for t in trace:
            print(f"trace: {t['tool']} ok={t['ok']} attempts={t['attempts']}"
                  + (f" error={t['error']}" if t["error"] else ""), file=sys.stderr)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
