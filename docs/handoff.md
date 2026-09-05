# Implementation handoff: CA Right-to-Know response visualizer

**Status:** design complete, no code written yet
**Target implementer:** Claude Code
**License:** MIT
**Audience:** individuals who have filed CCPA/CPRA access requests and received responses

---

## 0. Read this first

This project handles **real personal data belonging to real people**, arriving as
undocumented dumps from grocery retailers. The single most likely way this project fails
is by leaking someone's home address into a public git repository.

**Milestone M0 (PII safeguards) must be completed and verified before any real report
touches the working tree.** Do not reorder the milestones.

The reference Kroger report that motivated this project contains a verified home address,
phone number, email, loyalty card number, and 24 months of itemized purchases. **It must
never be added to the repository, not even temporarily, not even in a branch that is later
deleted.** Git history is forever without `git filter-repo`.

---

## 1. What the project does

A person files a right-to-know request with a grocery retailer. Weeks later they get back
a PDF containing raw internal JSON, or a zip of CSVs, or a letter. It is technically
compliant and practically unreadable.

This tool ingests those responses, normalizes them into a common schema, and renders three
things:

1. **What they have** — purchase timeline, identity graph, drill-down to line items
2. **What they infer** — modeled and appended attributes, separated by likely origin
3. **What they didn't tell you** — a compliance view against the CCPA disclosure
   categories, per retailer

Point 3 is where the project puts its weight. The tooling surveyed when this brief
was written covered request *generation* (Datenanfragen, YourDigitalRights) and
company-side request *fulfillment* (Ethyca Fides, inthhq/dsar); the nearest thing
found for reading a response back was an unreleased Streamlit prototype from the
University of Bristol's Jean Golding Institute, scoped to Tesco Clubcard data. That
was a survey at one point in time, not a standing claim about the field.

### Non-goals

- Not a request generator. Link out to Datenanfragen rather than duplicating it.
- Not legal advice. The compliance view reports observations, never conclusions about
  whether a retailer broke the law.
- Not a hosted service. Local-first, single-user, no accounts, no telemetry.
- Not a general takeout viewer. Scope is legally compelled access responses.

---

## 2. Repository naming

Pick one before the first commit; renaming after publication costs you inbound links.

| Candidate | Read | Risk |
|---|---|---|
| `unbagged` | Unpacking what's in your bag. Short, memorable, grocery-adjacent without being locked to groceries. | Slightly cute for a tool researchers might cite. |
| `basket-case` | Best pun available. Instantly signals grocery + data. | Idiom means "mentally unwell"; may read as unserious or, worse, careless. |
| `shelf-life` | Evokes both retail and data retention periods, which is thematically apt. | Ambiguous out of context; several unrelated projects use it. |
| `right-to-know` | Maximally findable. Says exactly what it is. | Boring, and collides with US environmental right-to-know laws in search. |
| `rtk-viewer` | Clear, unglamorous, scales past groceries. | Acronym is opaque to newcomers. |
| `loyalty-lens` | Alliterative, describes the function. | Sounds like a martech vendor product. |

**Recommendation: `unbagged`**, with the tagline "Read what the grocery store knows about
you." It's memorable, it doesn't lock the project to a single sector if it later expands
to pharmacies or telecoms, and it avoids the tonal problem of `basket-case`. If the
project's primary audience turns out to be privacy researchers rather than shoppers,
`rtk-viewer` is the safer institutional choice.

Check availability on GitHub, PyPI, and npm before committing, even if you don't plan to
publish packages immediately.

---

## 3. Architecture

```
  Retailer responses (PDF / zip / CSV / letter)
                  |
          [ Ingest + hash + store ]
                  |
          [ Adapter registry ]  <- sniff() picks the adapter
                  |
      Kroger | Safeway | H Mart | generic-fallback
                  |
          [ Canonical SQLite store ]
                  |
      identity | transactions | inferences | disclosures
                  |
          [ FastAPI read API ]
                  |
      Timeline | Profile | Compliance | Compare
```

### Stack

- **Backend:** Python 3.12, FastAPI, SQLite (via SQLAlchemy Core or plain `sqlite3` —
  do not pull in a heavyweight ORM for four tables)
- **Parsing:** `pdfplumber` for text extraction, `pypdf` for structure. Note that the
  Kroger report's JSON is pretty-printed and interrupted by bare page-number lines; the
  adapter must strip those before `json.loads`.
- **Frontend:** React + Vite + TypeScript, Recharts for charts, Tailwind for layout
- **Packaging:** Docker + Docker Compose, single container in the default path

### Why not Streamlit

Streamlit would cut implementation time roughly in half and is the obvious choice for a
research prototype. It is rejected here because the compliance view and identity graph
need real interaction (hover-to-provenance, cell drill-down, cross-retailer diffing) that
Streamlit makes awkward, and because a REST API means the parsers stay usable headlessly
for people who want to script against their own data. If velocity becomes the binding
constraint, Streamlit is an acceptable fallback — but keep the adapter layer separable so
the UI can be swapped without rewriting the parsers.

### Why one container

The default `docker compose up` should start exactly one service. Individuals are the
audience; a Postgres sidecar and a separate frontend container are friction with no payoff
at this scale. The API serves the built static frontend from the same process. SQLite
lives on a bind-mounted volume so the user can back it up by copying a file.

A `docker-compose.dev.yml` overlay adds a Vite dev server with HMR for contributors.

---

## 4. The adapter contract

This is the core abstraction. Every retailer gets one adapter; the rest of the system
never knows which retailer it's looking at.

```python
# src/unbagged/adapters/base.py

from typing import Protocol, Sequence
from dataclasses import dataclass

@dataclass(frozen=True)
class SourceBundle:
    """Everything the user handed us for one request, already hashed and stored."""
    documents: Sequence[SourceDocument]   # paths are inside the container's data volume
    declared_retailer: str | None          # user hint from the upload form, may be wrong

@dataclass(frozen=True)
class ParseResult:
    identities: list[Identity]
    transactions: list[Transaction]        # each carries its own items
    inferences: list[Inference]
    disclosures: list[Disclosure]          # including explicit ABSENT findings
    warnings: list[ParseWarning]           # unparsed regions, ambiguous fields

class RetailerAdapter(Protocol):
    retailer_id: str        # "kroger"
    display_name: str       # "Kroger"
    schema_version: int     # bump when the retailer changes their format

    def sniff(self, bundle: SourceBundle) -> float:
        """Confidence 0.0-1.0 that this adapter handles this bundle.
        Must be cheap and must not raise. Registry picks the highest scorer."""

    def parse(self, bundle: SourceBundle) -> ParseResult:
        """Full parse. May raise AdapterError with a user-readable message."""
```

### Rules for adapters

1. **Every emitted record carries provenance.** `source_document_id`, `page`, and a
   `locator` string (a JSON path, a line range, whatever the format allows). The UI must
   be able to answer "where did this come from" for any cell on screen.
2. **Adapters never mutate values.** Store `description_raw` exactly as it appeared.
   Normalization and enrichment (category assignment, UPC lookup) happen in a separate
   pass so the original is always recoverable.
3. **Absence is a finding.** If a report contains no section on categories of sources, the
   adapter emits `Disclosure(category=SOURCES, status=ABSENT)`. Silence in the data model
   is indistinguishable from "not yet parsed"; explicit ABSENT is not.
4. **Adapters degrade, they don't crash.** A malformed basket should produce a
   `ParseWarning` and a skipped record, not a stack trace that loses the other 53 baskets.
5. **Each adapter ships with a synthetic fixture and a `NOTES.md`** documenting observed
   quirks of that retailer's format. The notes are as valuable as the code — they're the
   institutional memory of what Kroger's export actually looks like.

### Directory layout per adapter

```
src/unbagged/adapters/kroger/
    __init__.py
    adapter.py
    NOTES.md              # observed format quirks, undocumented fields
    fixtures/
        synthetic_report.txt      # generated, committed, contains no real data
        generate.py               # Faker-based generator that produced it
tests/adapters/test_kroger.py
```

### Kroger adapter: known format facts

Derived from one real report (report ID redacted here; do not record real IDs in the repo).

- Delivered as a PDF whose text layer is four pretty-printed JSON blobs separated by
  prose headers. Extract text, then split on the headers.
- Bare page-number lines (`\n  12\n`) are interleaved into the JSON. Strip with
  `re.sub(r'\n\s*\d{1,3}\r?\n', '\n', text)` before parsing.
- Section headers observed, in order:
  - `Section 1: Specific Pieces of Personal Information Collected`
  - `Data we hold related to our Loyalty program:`
  - `Data we hold to communicate and advertise to you in a personalized way:`
  - `Email Information`
  - `Data related to in-store services:` / `Information about your purchases:`
- There is **no Section 2, 3, or 4**. Categories of sources, business purposes, third-party
  recipients, and sale/share status are all absent. The adapter must emit ABSENT
  disclosures for each.
- The report states a 24-month coverage window and directs the user to email the privacy
  office separately for data back to 2022. Emit this as a `FollowUpAction` record.
- Purchase JSON shape: `customer[0].basket[]`, each with `date`, `time`, `division`,
  `store`, `orderno`, `total_amount_prior_to_discounts`, `tenders[]`, `items[]`.
  Items have `purchasedescription`, `productupc`, `retailamt`, `customerloyamt`.
<!-- pii-scan: allow placeholder UPC below, not a loyalty or card number -->
- `purchasedescription` of `"UNKNOWN"` with UPC `00010000080000` and zero amounts appears
  frequently — treat as a placeholder, not a product.
- Negative `retailamt` values occur (returns/voids). Do not filter them; they're real.
- Identity graph fields observed: `loyaltyno`, `cardNumberWithCD`, `alternateId`, `ehhn`,
  `householdId`, `cgPersonId`, `epsn`, `SubscriberID`. Each becomes an `Identity` row.
- Inference blob one: five propensity axes (Convenience, Loyalty, Price, Quality, Variety
  Seeking) with prose values. Classify `origin=FIRST_PARTY_MODEL`.
- Inference blob two: demographics and likelihoods (age, education, gender, pet ownership,
  income predictor score, cruise/travel likelihood on a 1-7 scale, household composition).
  Classify `origin=APPENDED_THIRD_PARTY` — these are not derivable from grocery baskets
  and their source is undisclosed. This classification is the most interesting output the
  tool produces; get it right.

### Safeway and H Mart

Not yet received. Create stub adapters that `sniff()` to 0.0 and raise
`NotImplementedError` on parse, plus a `NOTES.md` recording expectations:

- **Safeway (Albertsons):** historically returns a zip of CSVs plus a separate categories
  letter. Expect the statutory disclosure sections to arrive as prose, not data — the
  disclosure parser will need light NLP or manual entry support.
- **H Mart:** small enough that a PDF letter or a non-response is likely. Build the
  generic-fallback adapter to handle "a letter with no structured data" gracefully, since
  that is itself a compliance finding.

---

## 5. Canonical schema

SQLite. All tables carry `request_id`. Timestamps ISO-8601 UTC.

```sql
CREATE TABLE request (
    id INTEGER PRIMARY KEY,
    retailer_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    report_reference TEXT,              -- retailer's own report ID
    submitted_at TEXT,
    received_at TEXT,
    statute TEXT DEFAULT 'CCPA',
    period_start TEXT,
    period_end TEXT,
    adapter_schema_version INTEGER
);

CREATE TABLE source_document (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    original_filename TEXT,
    sha256 TEXT NOT NULL,
    media_type TEXT,
    page_count INTEGER
);

CREATE TABLE identity (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    id_type TEXT NOT NULL,     -- loyalty_card|alternate_id|household|internal_person|email|phone|address
    value TEXT NOT NULL,
    scope TEXT,                -- individual|household
    first_seen TEXT,
    source_document_id INTEGER,
    locator TEXT
);

CREATE TABLE txn (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    external_order_id TEXT,
    occurred_at TEXT NOT NULL,
    store_code TEXT,
    division_code TEXT,
    channel TEXT,              -- in_store|online|fuel|pharmacy
    tender_type TEXT,
    total_pre_discount REAL,
    source_document_id INTEGER,
    locator TEXT
);

CREATE TABLE txn_item (
    id INTEGER PRIMARY KEY,
    txn_id INTEGER REFERENCES txn(id),
    description_raw TEXT NOT NULL,
    upc TEXT,
    quantity REAL,
    retail_amt REAL,
    loyalty_amt REAL,
    category TEXT,             -- nullable, filled by enrichment pass
    category_confidence REAL
);

CREATE TABLE inference (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    label TEXT NOT NULL,
    value_raw TEXT NOT NULL,
    value_num REAL,
    scale TEXT,                -- categorical|ordinal_1_7|currency|count|prose
    subject TEXT,              -- individual|household
    origin TEXT NOT NULL,      -- first_party_model|appended_third_party|unknown
    derivable_from_txns INTEGER,   -- 0/1/NULL, adapter's judgment, shown as a caveat
    source_document_id INTEGER,
    locator TEXT
);

CREATE TABLE disclosure (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    category TEXT NOT NULL,    -- see enum below
    status TEXT NOT NULL,      -- provided|partial|absent
    evidence TEXT,             -- quoted or summarized, may be NULL when absent
    notes TEXT,
    source_document_id INTEGER,
    locator TEXT
);

CREATE TABLE follow_up (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    kind TEXT,                 -- supplemental_period|missing_category|clarification
    description TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE parse_warning (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id),
    severity TEXT,
    message TEXT,
    locator TEXT
);
```

### Disclosure category enum

Mirrors the CCPA/CPRA disclosure obligations. Keep these exact keys; the compliance view
columns are generated from them.

```
CATEGORIES_COLLECTED       # Civ. Code 1798.110(a)(1)
SOURCES                    # 1798.110(a)(2)
BUSINESS_PURPOSE           # 1798.110(a)(3)
THIRD_PARTIES_SHARED_WITH  # 1798.110(a)(4)
SPECIFIC_PIECES            # 1798.110(a)(5)
SOLD_OR_SHARED             # 1798.115(a)(2)-(3)
DISCLOSED_FOR_BUSINESS_PURPOSE
RETENTION_PERIOD           # 1798.100(a)(3)
```

Ship a `docs/legal-basis.md` mapping each key to its statutory citation, with a prominent
note that the tool reports observations and is not legal advice.

---

## 6. PII safeguards

Nine layers. Implement all of them in M0, before writing any parser.

> **The embedded config below was the brief's starting point and has since been
> superseded.** Read `.gitignore`, `.dockerignore` and `CONTRIBUTING.md` for what
> is actually in force; two of the three rules sketched here turned out to be
> wrong in ways that mattered, and the corrections are recorded in `CHANGELOG.md`.

### 6.1 Deny-by-default `.gitignore`

Do not enumerate risky patterns and hope you caught them all. Ignore the data directories
wholesale and whitelist nothing inside them. Broad denials first, then narrow
re-inclusions only for fixture directories that CI verifies are synthetic.

Two amendments the brief did not anticipate. The re-inclusion list has to be
exactly the set a regeneration check covers — `tests/fixtures/**` was re-included
here and covered by nothing, an exemption with no check behind it. And three
report formats is not the set: every format the scanner cannot read has to be
denied, which is sixteen of them.

### 6.2 `.dockerignore` mirrors `.gitignore`

Real data must never be baked into an image layer. The data directory is a bind mount at
runtime, never a `COPY`.

**"Mirrors" is the trap.** The two files look alike and match differently: a
`.gitignore` pattern with no slash matches at every depth, while a `.dockerignore`
pattern is matched against the whole relative path, so a bare `*.pdf` excludes
`./report.pdf` and nothing below it. Copying the lines across verbatim protects the
root and leaks every subdirectory. Every wildcard needs a `**/` prefix, and the
build context matters as much as the image layer — the daemon caches what it is
sent.

### 6.3 Directory separation, enforced by convention and by CI

```
data/incoming/     gitignored, bind-mounted, where users drop reports
data/db/           gitignored, SQLite lives here
src/**/fixtures/   committed, synthetic only, CI-verified
```

`tests/fixtures/` appeared here as a fourth line marked CI-verified. It was not:
the regeneration check only ever covered `src/**/fixtures/`. The re-inclusion has
been removed rather than the check extended, because one exempted directory is
easier to guarantee than two.

### 6.4 Synthetic fixture generator

`tools/make_fixtures.py`, Faker-backed, deterministic under a fixed seed. It emits a
structurally faithful Kroger report — same section headers, same JSON shape, same page-
number interleaving, same `UNKNOWN` placeholder rows, same negative-amount returns — with
entirely fabricated values.

This is not optional polish. Without it, every contributor is tempted to test against
their own real report, and one of them will eventually commit it.

Seed the generator from the *structure* of the reference report, never from its values.
Fabricated addresses must use reserved-for-fiction values (e.g. `555` phone prefixes,
`example.com` email domains, a street address that does not resolve).

### 6.5 PII scanner

`tools/scan_pii.py`, runnable as `make check-pii`. Scans the entire working tree plus,
in CI, the full git history. Flags:

- Email addresses not on `example.com` / `example.org`
- Phone numbers with a non-`555` exchange
- Street-address-shaped strings (number + street-type token)
- 9-digit and 5+4 ZIP patterns adjacent to a state abbreviation
- Payment-card-shaped digit runs (Luhn check to cut false positives)
- SSN-shaped patterns
- Loyalty-card-length digit runs (12-14 digits) outside fixture files
- Any UUID appearing outside a fixture or a test
- A `denylist.txt` of literal strings that must never appear, populated by the project
  owner locally and **itself gitignored** — the denylist would otherwise be a PII file

Exit non-zero on any hit. Provide `# pii-scan: allow <reason>` inline suppressions so
false positives don't train people to bypass the whole check.

### 6.6 Pre-commit hooks

`.pre-commit-config.yaml` with:

- `gitleaks` (credentials)
- the project's own `scan-pii` hook
- `check-added-large-files` (a 5 MB PDF sneaking in is a red flag)
- a `no-data-dir` hook that hard-fails if any staged path starts with `data/`

Document `pre-commit install` in the first section of CONTRIBUTING.md, and have `make
setup` run it automatically.

### 6.7 CI gate

GitHub Actions workflow that runs `make check-pii` against the checkout **and** against
`git log -p` for the PR's commits. A contributor who commits their real report and then
amends it away still gets caught before merge.

### 6.8 Sanitizer CLI for bug reports

`unbagged sanitize <file> -o skeleton.json` produces a structure-preserving skeleton:
keys retained, string leaves replaced with `<str:len=N>`, numbers bucketed to order of
magnitude, dates coarsened to month. Someone whose Safeway report won't parse can attach
the skeleton to an issue without sending their groceries to the internet.

CONTRIBUTING.md must state plainly: **never attach a real report to an issue or PR.**

### 6.9 Runtime privacy posture

- Bind to `127.0.0.1` by default, not `0.0.0.0`. Document the override for users who
  actually want LAN access.
- No telemetry, no crash reporting, no update checks.
- Vendor all fonts and JS. Zero CDN requests — the app must work fully offline, and a CDN
  request leaks usage timing to a third party.
- No outbound network calls from the app container in normal operation. If UPC enrichment
  against an external product database is added later, it must be opt-in, off by default,
  and clearly labeled as sending UPCs off-device.
- Screenshots in README and docs come from synthetic fixtures only. Add this to the PR
  checklist.

### 6.10 `CLAUDE.md` in the repo root

Claude Code will read this on every session. Include:

```markdown
# Working in this repository

This project processes real personal data. Follow these rules without exception.

- NEVER read, open, cat, grep, or otherwise access files under `data/`. If you need
  sample input, use `src/unbagged/adapters/*/fixtures/` or `tests/fixtures/`.
- NEVER add files under `data/` to git, even temporarily.
- NEVER paste report content into commit messages, PR descriptions, code comments,
  test names, or docstrings.
- All test data must come from `tools/make_fixtures.py`. If you need a new shape,
  extend the generator rather than hand-writing a fixture from a real report.
- Run `make check-pii` before every commit. Do not bypass it with `--no-verify`.
- If you believe real data has entered the working tree, STOP and tell the user.
  Do not attempt to clean git history yourself.
```

---

## 7. Docker packaging

### Default path for an individual user

```bash
git clone https://github.com/<owner>/unbagged
cd unbagged
docker compose up
# open http://localhost:8420
# drag the PDF onto the upload area
```

Three commands, no Python install, no Node install, no database setup. This is the bar.

### `docker-compose.yml`

```yaml
services:
  unbagged:
    build: .
    image: unbagged:local
    ports:
      - "127.0.0.1:8420:8000"
    volumes:
      - ./data:/data
    environment:
      UNBAGGED_DB: /data/db/unbagged.sqlite
      UNBAGGED_INCOMING: /data/incoming
    restart: unless-stopped
```

Note the `127.0.0.1:` prefix on the port binding — without it Docker publishes to all
interfaces and can punch through the host firewall.

### `Dockerfile`

Multi-stage. Stage 1 builds the frontend with Node and emits static assets. Stage 2 is a
`python:3.12-slim` base that installs the backend, copies the built assets, runs as a
non-root user, and serves everything from uvicorn. Final image should land under ~400 MB.

### `docker-compose.dev.yml`

Overlay adding a `web` service running Vite with HMR, proxying `/api` to the backend, plus
source bind mounts. Invoked as
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up`.

### Makefile

`make up`, `make dev`, `make test`, `make check-pii`, `make fixtures`, `make setup`.
Wrap the Docker commands so contributors never need to remember overlay syntax.

---

## 8. UI views

> **Superseded. `DESIGN.md` is the authority for the UI.**
>
> This section is the original brief. It was written before there was a design
> system, and three of its five view specs were deliberately replaced during
> implementation. It is kept because the *reasoning* it records is still useful,
> and because sections 0-7 above remain the live contract — the adapter rules in
> §4 and the schema in §5 are cited from `adapters/base.py`, `models.py`,
> `db.py` and four test modules. Only this section and §9 are historical.
>
> What shipped, and where it departs:
>
> | Brief said | Shipped | Why |
> |---|---|---|
> | Timeline as a scatter or bar of visits | One continuous ruled roll, with a month bar chart above it | Your March baskets are not a separate concern from your April ones; a mark per basket answered a question nobody asked |
> | Profile as inference cards in two columns | Three movements down one spine, with the appended-attribute block on visibly different stock | Two equal columns held 5 cards against 16, which made the most important finding in the product look like a rendering bug. The imbalance *is* the finding |
> | Compliance as retailers-rows by categories-columns, cells green/amber/red | Eight categories read down the page per retailer, answers quoted, a blank rule where an answer should be | A one-row matrix is a spreadsheet with nothing to compare, and it hid the evidence behind a click. Colour was carrying severity, which `DESIGN.md` forbids |
> | Compare, once a second response lands | As specified, with a blank second column standing in until one arrives | — |
> | Price history (stretch) | Shipped, plus a Products index the brief did not anticipate | A line carries an amount and nothing else, so products whose amounts are not a unit price are named rather than charted |
>
> The decisions log at the end of `DESIGN.md` records each departure with its
> reasoning at the time.

## 9. Milestones

> **All milestones are complete as of 0.9.0 (2026-09-03).** This section is kept
> as the record of the intended order, not as a plan. Work since then is recorded
> in `CHANGELOG.md`, and planned work is filed as GitHub issues.

Each milestone is independently mergeable with passing CI.

**M0 — Safeguards and scaffolding.** Repo init, MIT LICENSE, `.gitignore`,
`.dockerignore`, `CLAUDE.md`, pre-commit config, `tools/scan_pii.py`, CI workflow,
Makefile, CONTRIBUTING.md.
*Acceptance:* deliberately place a file containing a fake-but-realistic address under
`data/` and in the tree; confirm the hook and CI both reject it.

**M1 — Canonical schema.** Migrations, models, a repository layer. No parsers yet.
*Acceptance:* schema creates cleanly, round-trips a hand-built `ParseResult`.

**M2 — Fixture generator.** `tools/make_fixtures.py` emitting a structurally faithful
synthetic Kroger report.
*Acceptance:* generated fixture passes `scan_pii` and matches the documented section
structure.

**M3 — Kroger adapter.** Full parse against the synthetic fixture. All four blobs,
identity graph, both inference classes, ABSENT disclosures, follow-up action for the
pre-2022 supplemental window.
*Acceptance:* parses the fixture into the expected record counts; degrades with warnings
rather than crashing on a truncated input.

**M4 — API.** FastAPI read endpoints for each view, plus upload/ingest.
*Acceptance:* OpenAPI schema generated; endpoints covered by tests against fixture data.

**M5 — UI.** Timeline, Profile, Compliance. Compare and price history if time allows.
*Acceptance:* every displayed value traceable to a provenance link.
*Shipped:* all five, plus a sixth Products view added in 0.10.0. See §8 above.

**M6 — Docker packaging.** Single-container compose, dev overlay, README quickstart.
*Acceptance:* a clean machine with only Docker installed reaches a working UI in three
commands.

**M7 — Adapter authoring guide.** `docs/writing-an-adapter.md`, Safeway and H Mart stubs,
generic-fallback adapter for unstructured letters.
*Acceptance:* a contributor can add a new retailer without touching core code.

---

## 10. Open questions for the project owner

1. **Scope beyond groceries.** The schema is retailer-agnostic already. Do you want the
   README to invite pharmacy, telecom, and airline adapters, or stay narrow to build a
   credible v1 first? This decision drives the name choice in section 2.
2. **Real-report validation.** M3 tests against synthetic fixtures only. You'll need to
   validate the Kroger adapter against your actual report locally, outside the repo.
   Suggest a documented workflow: `data/incoming/`, never staged, verified by the M0
   tooling.
3. **Contact with Bristol.** Fryer and Day have run public workshops on this exact
   interaction design and listed public release as future work. Worth an email before M5
   locks the UI down.
4. **Follow-up letter templates.** Should the compliance view's letter generator borrow
   Datenanfragen's template approach, or stay minimal? Reusing their letter-generator
   package is possible but adds a TypeScript dependency to a Python backend.
