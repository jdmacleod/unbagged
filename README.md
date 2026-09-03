# unbagged

**Read what the grocery store knows about you.**

You file a right-to-know request with a grocery retailer. Weeks later a PDF arrives
containing raw internal JSON, or a zip of CSVs, or a letter. It is technically compliant
and practically unreadable.

`unbagged` ingests those responses, normalizes them into a common schema, and shows you
three things:

1. **What they have** — a purchase timeline, an identity graph, drill-down to line items.
2. **What they infer** — modeled and appended attributes, separated by likely origin. The
   demographic and household-composition attributes are usually not derivable from your
   baskets, which means they were bought from somewhere the report does not name.
3. **What they didn't tell you** — a compliance matrix against the CCPA/CPRA disclosure
   categories, per retailer. Absence is recorded as a finding, not as a blank.

Point 3 is the part nothing else does. Existing open-source tooling covers request
*generation* and company-side request *fulfillment*. Nothing reads the response back.

## Status

**Early. M0 of 8.** The PII safeguards and scaffolding are in place; no parsers yet.
See `HANDOFF.md` for the full design and milestone plan.

| | Milestone | State |
|---|---|---|
| M0 | Safeguards and scaffolding | done |
| M1 | Canonical schema | next |
| M2 | Synthetic fixture generator | |
| M3 | Kroger adapter | |
| M4 | Read API | |
| M5 | UI — timeline, profile, compliance | |
| M6 | Docker packaging | |
| M7 | Adapter authoring guide | |

## Quickstart

Not yet — packaging lands at M6. The bar it has to clear:

```bash
git clone https://github.com/jasonmacleod/unbagged
cd unbagged
docker compose up
# open http://localhost:8420 and drag the PDF onto the upload area
```

For now, development setup:

```bash
make setup        # venv, dev deps, git hooks
make test
make check-pii
```

## Your data stays on your machine

- Local-first and single-user. No accounts, no hosted service, no telemetry, no crash
  reporting, no update checks.
- The app binds to `127.0.0.1` by default. LAN access is an explicit opt-in.
- Fonts and JS are vendored. Zero CDN requests — the app works fully offline, and a CDN
  request would leak usage timing to a third party.
- Your reports live in `data/`, which is gitignored wholesale, excluded from Docker build
  context, and guarded by a pre-commit hook, a scanner, and a CI gate. If you plan to
  contribute, read `CONTRIBUTING.md` before you put anything there.

## What this is not

- **Not a request generator.** Use [Datenanfragen](https://www.datarequests.org/) to file.
- **Not legal advice.** The compliance matrix reports observations. It never concludes
  that a retailer broke the law.
- **Not a hosted service**, and not a general data-takeout viewer. Scope is legally
  compelled access responses.

## License

MIT. See `LICENSE`.
