# AI usage

> **DRAFT: make this accurate to how *you* used AI before submitting.** The assessment permits AI
> tools but expects you to be able to explain and change every line. Read the code, run it, and edit
> this file so it reflects your own review.

## Tools used

- **Claude** (Anthropic): produced the initial implementation, dataset generator, tests and
  documentation drafts in this repository. I (the submitter) am responsible for the result.

## One useful suggestion

Checking the tests with **mutation**: break one rule at a time in a scratch copy (count `ingested_at`
in event identity, drop the refund limit, remove the tenant filter, demote chargebacks) and confirm
the suite fails. It proved the tests were not vacuous and exposed a real gap (nothing tested the
engine's own tenant filter).

## One suggestion corrected or rejected

- **Corrected:** the first version put *every* trusted event ID into `evidence_event_ids`, including
  ignored duplicate re-sends of the same capture. Evidence is now only the records that established
  a fact, matching the brief ("a duplicate was ignored").
- **Rejected:** letting a model word the explanation. See `DECISIONS.md` item 5.

## Verification I did myself

*(Fill this in: what you ran, what you changed, what you checked against the brief.)*
