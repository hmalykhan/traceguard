# Engineering decisions

> **DRAFT: rewrite each answer in your own words before submitting.** The assessment asks for your
> own words, and you will be asked to defend these in the review. Edit anything that does not match
> your own thinking, and replace item 4 with whatever you actually hit and verified yourself.

## 1. What is the most dangerous assumption in this problem?

That a record being *present* means it is *true and relevant*. Support notes, checkout failure
signals and payment rows from the wrong source all look like evidence. If any of them can move
money totals or status, anyone who can write a note controls the answer. So trust is decided by
(source, type) pairs, never by content. A second dangerous assumption is that absence of evidence
means "unpaid": missing events usually mean incomplete history, so no capture and no verified failure
gives UNKNOWN, not UNPAID.

## 2. Which part must not depend on an LLM, and why?

Everything that produces status, totals, anomalies, evidence IDs and confidence (`reconcile.py`).
A model is non-deterministic, can be manipulated by text in the data (the injection note is exactly
that attack), and cannot be audited the way a rule can. The model here only picks an intent from a
fixed list, and its output is validated against that list, so a hostile or broken model changes
wording at worst. A test proves the decision is identical under a model that answers "REFUNDED",
returns garbage, or raises.

## 3. When is UNKNOWN the right answer?

When the records cannot support a single reliable answer: conflicting order definitions, an event
or transaction id reused with different details, refunds that reference no capture or exceed it,
over-capture, currency mismatch, a failed-and-captured attempt, a malformed or unsupported record
from the source that is allowed to move money, a failed essential tool call, or simply no valid
capture and no verified failure. Totals are `null` there, because a wrong number is worse than a
missing one. UNKNOWN is not a failure of the system; guessing would be.

## 4. What was the hardest bug you found, and how did you verify the fix?

*(Replace with your own experience from the review.)* The most instructive finding in this build
was a test gap, not a code bug. A mutation check (deliberately breaking four rules in a scratch
copy) showed that removing the engine's own tenant filter went unnoticed, because the store and
workflow already isolate tenants. I added a test that feeds foreign-tenant events directly into
`reconcile()` and re-ran the broken copy to confirm the suite now fails. Also during review I found
that evidence IDs would have included ignored duplicate events; evidence is now built only from
records that established a fact.

## 5. Which approach or AI-generated suggestion did you reject, and why?

*(Adjust to your own session.)* Letting a model write or "polish" the explanation unchecked: it could
repeat injected text or cite events that do not exist, so the explanation is built from reconciled
facts only and never copies note text. Also rejected: treating every integrity conflict as blocking.
A conflict between two *notes* cannot affect money, and making it blocking would let a note writer
force UNKNOWN, so it is a warning. Finally, rejected silently dropping malformed payments records:
a record that might have been a real capture has to make the answer UNKNOWN.

## 6. What would break first at one million events per day?

About 12 events per second on average is not much; the design breaks first on structure, not speed.
The CLI loads the whole file into memory per run, the store is a Python dict, and the cross-order id
links are built by scanning in-process. Next would be tool calls that spawn a thread per attempt, and
reprocessing an order from scratch on every question. I would move to a database partitioned by
tenant with unique indexes on (tenant, event_id), (tenant, capture_id) and (tenant, refund_id),
ingest idempotently (which also pushes conflict detection to write time), and cache or incrementally
maintain per-order state.

## 7. If you had one more day, what would you improve first?

Property tests that generate *invalid* histories with a known expected anomaly, so each integrity
rule is tested by construction and not only by hand-written cases. After that: a persistent store,
structured logging of tool calls, and a decision about a "chargeback closed" event, which the brief
does not define but a real system needs.
