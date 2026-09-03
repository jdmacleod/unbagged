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

## Quickstart

Three commands, no Python install, no Node install, no database setup.

```bash
git clone https://github.com/jasonmacleod/unbagged
cd unbagged
docker compose up
```

Then open <http://localhost:8420> and drag the retailer's response onto the upload
area.

**The first run builds the app**, which took 75 seconds on a clean machine with
nothing cached: it pulls two base images, compiles the UI, and installs the
Python dependencies. There is no prebuilt image to download, deliberately — you
run what you can read. Later starts are immediate.

Reading a long report takes 10 to 30 seconds. A 116-page PDF is about 14 seconds
of text extraction, and the page tells you it is working.

Your database and your uploads live in `./data`, on your disk — back the whole
thing up by copying that directory.

### Running it

| | |
|---|---|
| `docker compose up` or `make up` | Start it, on <http://localhost:8420> |
| `make down` | Stop it and remove the container |
| `make logs` | Follow the logs |
| `make reset CONFIRM=yes` | Move `./data` aside to `data.bak-<timestamp>` and start empty. Nothing is deleted; remove the backup yourself when you are sure. |

To use a different port, copy `.env.example` to `.env` and set `UNBAGGED_PORT`.
The app is only ever published on `127.0.0.1`; that part is not configurable, and
it is what keeps your report off your local network.

## What you get

**Timeline** — every visit over the coverage window, with the header numbers
(spend, baskets, distinct products, window) above it. Click a basket to expand the
line items, with the shelf price and the loyalty discount side by side, which is
the one thing a receipt never shows you.

**Profile** — the identifiers the retailer holds for you, and the attributes it
has inferred, split by where they came from. Scores it modelled from your own
baskets sit in one column; attributes it obtained somewhere it does not name sit
in the other. Anything describing your *household* rather than you is called out,
because those describe people who never signed up for anything.

**Compliance** — retailers as rows, the eight CCPA/CPRA disclosure categories as
columns, and a "draft a follow-up" action that writes a supplemental request
naming what went unanswered. You read it and send it yourself.

**Compare** and **Prices** — two retailers side by side once a second response
arrives, and a personal inflation series per product, which two years of itemised
baskets contains for free.

## Status

**All eight milestones complete.** Kroger is the only full adapter — it is the
only format anyone has had a real response for. A retailer with no adapter still
works: the fallback reads the response as text and records what it did and did
not address, because a letter with no data in it is itself the finding.

To add a retailer, see `docs/writing-an-adapter.md`. It should not require
touching any code outside the retailer's own package.

| | Milestone | State |
|---|---|---|
| M0 | Safeguards and scaffolding | done |
| M1 | Canonical schema | done |
| M2 | Synthetic fixture generator | done |
| M3 | Kroger adapter | done |
| M4 | Read API | done |
| M5 | UI — timeline, profile, compliance, compare, prices | done |
| M6 | Docker packaging | done |
| M7 | Adapter authoring guide, fallback, stubs | done |

## Working on it

```bash
make setup           # venv, dev deps, git hooks
make setup-frontend  # npm install
make dev             # compose + Vite, on http://localhost:5173
make test            # fast suite
make test-container  # slow: builds and runs a real container
make check-pii       # run this before every commit
```

`make dev` serves **one** URL, <http://localhost:5173>, with hot reload for both
the UI and the backend. The API is proxied at `/api` on that same port. The
backend's own port is not published in dev, because it would serve the UI bundle
frozen into the image at build time with no way to tell the difference in a
browser.

`make test-container` is the tier that runs a real container: effective uid,
self-healing data permissions, bounded restart, and that the production image is
the runtime stage. Tests that need real uid semantics skip loudly on Docker
Desktop, where bind-mount ownership is remapped and they would pass without
checking anything.

`make help` lists the rest.

## Your data stays on your machine

- Local-first and single-user. No accounts, no hosted service, no telemetry, no crash
  reporting, no update checks.
- The app binds to `127.0.0.1` by default, and the published Docker port is
  `127.0.0.1`-scoped too. Without that prefix Docker publishes on every interface
  and goes straight through the host firewall. LAN access is an explicit opt-in,
  and `unbagged serve` warns on stderr if you ask for it.
- Fonts and JS are vendored. Zero CDN requests — the app works fully offline, and a CDN
  request would leak usage timing to a third party.
- Your reports live in `data/`, which is gitignored wholesale, excluded from Docker build
  context, and guarded by a pre-commit hook, a scanner, and a CI gate. If you plan to
  contribute, read `CONTRIBUTING.md` before you put anything there.

## Supported responses

| Retailer | State |
|---|---|
| Kroger | Full adapter — purchases, identity graph, inferred attributes, disclosures |
| Safeway (Albertsons) | Stub. Expectations recorded in its `NOTES.md`; no real response seen |
| H Mart | Stub. A letter is handled by the fallback already |
| Anything else | Fallback: read as text, disclosures recorded, no data extracted |

## What this is not

- **Not a request generator.** Use [Datenanfragen](https://www.datarequests.org/) to file.
- **Not legal advice.** The compliance matrix reports observations. It never concludes
  that a retailer broke the law.
- **Not a hosted service**, and not a general data-takeout viewer. Scope is legally
  compelled access responses.

## License

MIT. See `LICENSE`.
