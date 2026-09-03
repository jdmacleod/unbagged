# H Mart response format

**No real response has been seen.** Expectation only.

## What to expect

H Mart is small enough that **a PDF letter, or no response at all, is the likely
outcome**. Both are covered without writing anything here:

- A letter with no structured data is handled by the generic fallback, which
  records the absence rather than erroring. That absence is the finding — see
  `../generic/NOTES.md`.
- A non-response is not something software can parse. It is still worth
  recording, and the schema supports it: a `request` row with `submitted_at` set,
  `received_at` null, and every disclosure `ABSENT`. There is no UI for entering
  that by hand yet, and that gap is worth closing before writing this adapter.

**Do not write a speculative adapter for this retailer.** The generic fallback
already produces the right answer for a letter, and a stub that guesses at a
format nobody has seen is worse than no stub: it can win a `sniff()` contest it
should have lost.

## When a response does arrive

If it turns out to contain structured data, follow
`docs/writing-an-adapter.md`, and replace this file with what was observed.
