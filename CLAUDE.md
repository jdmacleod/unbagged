# Working in this repository

This project processes real personal data — home addresses, phone numbers, loyalty card
numbers, and itemized purchase histories belonging to real people. Follow these rules
without exception.

- NEVER read, open, cat, grep, or otherwise access files under `data/`. If you need
  sample input, use `src/unbagged/adapters/*/fixtures/` or `tests/fixtures/`.
- NEVER add files under `data/` to git, even temporarily. Not in a branch. Not with
  `git add -f`. Git history is forever without `git filter-repo`.
- NEVER paste report content into commit messages, PR descriptions, code comments,
  test names, or docstrings.
- All test data must come from `tools/make_fixtures.py`. If you need a new shape,
  extend the generator rather than hand-writing a fixture from a real report.
- Run `make check-pii` before every commit. Do not bypass it with `--no-verify`.
- If you believe real data has entered the working tree, STOP and tell the user.
  Do not attempt to clean git history yourself.

## Project shape

Implementation follows the milestones in `HANDOFF.md` §9, in order. M0 (safeguards and
scaffolding) is complete. Do not reorder milestones — the safeguards exist so that later
milestones can be developed against real reports locally without risk to the repo.

- `src/unbagged/adapters/` — one adapter per retailer, behind the `RetailerAdapter`
  protocol. Core code never branches on retailer identity.
- `tools/` — repo tooling: PII scanner, fixture generator, hook helpers.
- `data/` — gitignored, bind-mounted at runtime, off-limits (see above).

## Conventions

- Python 3.12, `src/` layout, `ruff` for lint and format.
- Adapters degrade with `ParseWarning`s rather than raising; absence of a disclosure is
  recorded explicitly as `status=absent`, never as a missing row.
- Every emitted record carries provenance: `source_document_id`, `page`, `locator`.
- No outbound network calls from the application at runtime. No telemetry, no CDN
  requests, no update checks.

## Escape hatch for the PII scanner

If `make check-pii` flags something genuinely benign, add an inline suppression on the
offending line (or the line directly above it) with a reason:

    upc = "00010000080000"  # pii-scan: allow known placeholder UPC, not a card number

Never suppress a whole file, and never disable a rule to make CI green.
