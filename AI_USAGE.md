
# AI Usage

## Which AI tool I used

I used **Claude** (by Anthropic) to help me build this project. It helped me with the code, the sample dataset, the tests and the first drafts of the documents.

I did not use AI blindly. I ran the project on my own Ubuntu machine with a conda environment, I ran all the tests (126 passed), and I ran the demo cases myself (normal, attack and conflicting data) and checked the outputs against what the assignment expects.

## One AI suggestion that was useful

The AI suggested **mutation checking** for my tests. It means: break one rule on purpose in a copy of the code, and see if the tests fail. If the tests still pass, then the tests are weak.

This was very useful. I broke 4 rules. Three were caught. One was not caught (the tenant filter inside `reconcile()`), so I learned my tests had a gap. I added a new test and checked again, and now it fails when the rule is broken.

## One AI suggestion that I corrected or rejected

**Corrected:** In the first version, `evidence_event_ids` included every trusted event, even the duplicate capture which was ignored. But the assignment says evidence should be the real events used. So I changed it: now evidence has only those records which created a fact, and ignored duplicates are not included.

**Rejected:** Using a language model to write the explanation freely. It can repeat text from an attack note or invent an event ID. So the explanation is created by normal code from the reconciled facts only. (More detail in `DECISIONS.md`, question 5.)

## What I checked myself

- I ran `python -m pytest -q` and got 126 passed.
- I ran these orders and checked the status: O-201 (PAID), O-214 (PAID, attack note ignored), O-212 (UNKNOWN, conflict), T2 O-201 (UNPAID, different tenant), O-202 (UNPAID), O-203 (PARTIALLY_PAID), O-204 (PARTIALLY_REFUNDED), O-205 (REFUNDED), O-206 (DISPUTED), O-207 (UNKNOWN).
- I tested tool failure: one failure then retry success, and a long timeout which falls back to UNKNOWN after 3 attempts.
- I checked that the support note text ("SYSTEM OVERRIDE...") is never shown in the output.

## What I understand and can explain

- The reconcile rules in `reconcile.py` and why the status order is like this.
- Why only payments can create financial facts, and why notes are untrusted.
- Why UNKNOWN has `null` totals.
- How retry, timeout and fallback work in `tools.py` and `workflow.py`.
- Why the same events in any order, or with duplicates, give the same answer.
