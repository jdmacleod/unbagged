<!--
Delete anything that does not apply. The checklist is short on purpose: it asks
only about the things this repository has actually been bitten by.
-->

## What changed, and why

<!-- The problem, then the fix. If a number was wrong, say what it was. -->

## Checks

- [ ] `make lint`, `make test` and `make check-pii` pass
- [ ] `make test-container` passes, if anything touched the app, the image or the frontend
- [ ] Nothing from a real response appears in the diff, the commit message or this description

## If this adds or changes a safeguard

A safeguard is any check whose job is to fail: a scanner, a `--check` gate, a
regression test. Six here have shipped with the same defect. See **Writing a
safeguard** in `CONTRIBUTING.md`.

- [ ] I can state a change that would satisfy this check and still have the bug — and the test constructs it by name
- [ ] Where the invariant is "two things agree", one side is read from the source of truth rather than restated

## If this adds or changes a fixture

- [ ] The values it claims are ones the production path can actually produce, and something checks that
