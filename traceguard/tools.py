"""The three tool interfaces, plus timeout, bounded retry and fault injection."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from .store import EventStore


class ToolError(Exception):
    """Transient tool failure. The only kind of failure worth retrying."""


class ToolTimeout(ToolError):
    """A tool call did not return in time."""


class Tools(Protocol):
    def get_order(self, tenant_id: str, order_id: str) -> list: ...
    def get_payment_events(self, tenant_id: str, order_id: str) -> list: ...
    def get_support_notes(self, tenant_id: str, order_id: str) -> list: ...


TOOL_NAMES = ("get_order", "get_payment_events", "get_support_notes")


class StoreTools:
    """Tools backed by the in-memory store."""

    def __init__(self, store: EventStore):
        self._store = store

    def get_order(self, tenant_id: str, order_id: str) -> list:
        return self._store.records(tenant_id, order_id, "order")

    def get_payment_events(self, tenant_id: str, order_id: str) -> list:
        return self._store.records(tenant_id, order_id, "payments")

    def get_support_notes(self, tenant_id: str, order_id: str) -> list:
        return self._store.records(tenant_id, order_id, "notes")


class FaultInjectingTools:
    """Wraps real tools and misbehaves on a script, to demonstrate failure handling.

    `faults[tool]` is a list consumed one entry per call. Each entry is None (behave
    normally), an Exception instance (raise it), ("sleep", seconds) (hang, then behave
    normally) or ("return", value) (return that value instead of real data).
    Once the list is exhausted the tool behaves normally.
    """

    def __init__(self, inner: Tools, faults: Dict[str, list]):
        self._inner = inner
        self._faults = {k: list(v) for k, v in faults.items()}
        self.calls: Dict[str, int] = {n: 0 for n in TOOL_NAMES}

    def _call(self, name: str, tenant_id: str, order_id: str):
        self.calls[name] += 1
        queue = self._faults.get(name) or []
        action = queue.pop(0) if queue else None
        if isinstance(action, Exception):
            raise action
        if isinstance(action, tuple) and action[0] == "sleep":
            time.sleep(action[1])
        if isinstance(action, tuple) and action[0] == "return":
            return action[1]
        return getattr(self._inner, name)(tenant_id, order_id)

    def get_order(self, tenant_id, order_id):
        return self._call("get_order", tenant_id, order_id)

    def get_payment_events(self, tenant_id, order_id):
        return self._call("get_payment_events", tenant_id, order_id)

    def get_support_notes(self, tenant_id, order_id):
        return self._call("get_support_notes", tenant_id, order_id)


def parse_fault_spec(spec: str) -> tuple:
    """'TOOL[:COUNT[:KIND]]' -> (tool, [exceptions]). KIND is 'error' (default) or 'timeout'."""
    parts = spec.split(":")
    tool = parts[0]
    if tool not in TOOL_NAMES:
        raise ValueError(f"unknown tool '{tool}'; expected one of {', '.join(TOOL_NAMES)}")
    try:
        count = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    except ValueError:
        raise ValueError("fault COUNT must be an integer") from None
    kind = parts[2] if len(parts) > 2 else "error"
    if kind not in ("error", "timeout"):
        raise ValueError("fault KIND must be 'error' or 'timeout'")
    exc = ToolTimeout if kind == "timeout" else ToolError
    return tool, [exc(f"injected {kind} in {tool}") for _ in range(max(count, 0))]


@dataclass
class RetryPolicy:
    max_attempts: int = 3        # hard upper bound on calls per tool; never unlimited
    timeout_s: float = 2.0       # per-attempt wall-clock limit
    backoff_s: float = 0.05      # doubles each retry: 0.05, 0.10, ...
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)


@dataclass
class ToolOutcome:
    ok: bool
    data: Any = None
    attempts: int = 0
    error: Optional[str] = None


def _call_with_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    """Run fn in a daemon thread and give up waiting after timeout_s.

    Python cannot kill a thread, so a hung call is abandoned, not cancelled; the daemon
    flag keeps it from blocking interpreter exit.
    """
    box: dict = {}

    def runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise ToolTimeout(f"no response within {timeout_s}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def call_with_retry(fn: Callable[[], Any], policy: RetryPolicy) -> ToolOutcome:
    """Call fn with a timeout, retrying ToolError/ToolTimeout up to policy.max_attempts times.

    Any other exception is a bug or a permanent failure: it is not retried.
    Never raises; failure is reported in the outcome so the caller can fall back.
    """
    attempts_allowed = max(1, policy.max_attempts)
    last_error = "unknown error"
    for attempt in range(1, attempts_allowed + 1):
        try:
            return ToolOutcome(True, _call_with_timeout(fn, policy.timeout_s), attempt)
        except ToolError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts_allowed:
                policy.sleep(policy.backoff_s * (2 ** (attempt - 1)))
        except Exception as exc:  # noqa: BLE001 - permanent failure, do not retry
            return ToolOutcome(False, None, attempt, f"{type(exc).__name__}: {exc}")
    return ToolOutcome(False, None, attempts_allowed, last_error)
