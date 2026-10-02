# 5. Numeric provenance is enforced in code, not prompts

- **Status:** Accepted
- **Date:** 2026-08-10
- **Implemented in:** `c473b6d`, `b49d8db`; `app/factsheet.py`

## Context

A fabricated figure shipped in this codebase once. A fee-impact expression in
`fund_rag.py` evaluated to $0 for any expense ratio of 1% or more and to a constant
below it, presenting an invented number as analysis. Separately, asking a model not
to do arithmetic is not the same as preventing it, and an invented fee or return in
a finance product is the failure that matters most.

## Decision

The model is handed a **FACTS** block of exact figures, and **every number in its
output must appear in FACTS or in a supplied excerpt**, checked by code
(`factsheet.find_unsupported_numbers`). Unsupported bullets are dropped before the
user sees them. The same check guards free-text answers on `/factsheet/ask`, where
one unsupported number invalidates the whole answer (`unverifiable`).

Numbers are compared **numerically**, not as strings, so `55.0` in FACTS supports a
model's `$55`. Integers 0 to 10 and years are exempt, as counting and dating.

## Consequences

- A hallucinated number cannot reach a user from these two paths, regardless of
  prompt wording or provider.
- Some correct answers are withheld when the model rephrases a figure in a way the
  check cannot match. That trade is deliberate: refusing is cheap, a wrong number
  is not.
- It does **not** cover other paths. `chat.py` relies on the prompt for "every
  number must come from the data"; extending the check there is open work.
- Related, same principle: Portfolio X-Ray emits no currency figure unless the user
  supplied amounts ([`design/portfolio-xray.md`](../design/portfolio-xray.md)).

## Alternatives considered

- **Prompt-only instruction:** tried first in spirit; insufficient.
- **A second model to fact-check the first:** more cost and latency, and itself
  unverifiable. A deterministic check is cheaper and testable.
