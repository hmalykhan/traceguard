"""Replaceable model interface.

The model is only ever asked to classify the question into one of a fixed set of
intents. The workflow validates the answer against that set, so a wrong, hostile or
failing model can change what the explanation emphasises, never the status or any
amount. Those come only from `reconcile`.

To plug in a real LLM, implement `interpret(question) -> str` returning one of
VALID_INTENTS. No network code lives in this project.
"""
from __future__ import annotations

from typing import Callable, Protocol

INTENT_PAID = "PAID"          # "Was this order paid?"
INTENT_FAILURE = "FAILURE"    # "Why does checkout show a failure?"
INTENT_EVIDENCE = "EVIDENCE"  # "What evidence supports the current status?"
INTENT_GENERAL = "GENERAL"
VALID_INTENTS = (INTENT_PAID, INTENT_FAILURE, INTENT_EVIDENCE, INTENT_GENERAL)


class ModelProvider(Protocol):
    def interpret(self, question: str) -> str: ...


class RuleBasedModel:
    """Default, offline, deterministic. No credentials, no network."""

    def interpret(self, question: str) -> str:
        q = (question or "").lower()
        if any(w in q for w in ("evidence", "proof", "support", "prove", "cite")):
            return INTENT_EVIDENCE
        if any(w in q for w in ("fail", "declin", "error", "why")):
            return INTENT_FAILURE
        if any(w in q for w in ("paid", "pay", "refund", "charge", "status")):
            return INTENT_PAID
        return INTENT_GENERAL


class MockModel:
    """Scriptable model for tests and demos: returns a fixed answer or raises."""

    def __init__(self, answer="GENERAL", raises: Exception = None, fn: Callable = None):
        self._answer, self._raises, self._fn = answer, raises, fn

    def interpret(self, question: str) -> str:
        if self._raises is not None:
            raise self._raises
        if self._fn is not None:
            return self._fn(question)
        return self._answer
