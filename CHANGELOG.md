# Changelog

Notable changes, newest first. Format follows [Keep a Changelog][kac]; versions
follow [semantic versioning][semver], with the compatibility contract read
against **your database** rather than against a code API, because nothing
imports this package:

- **MAJOR** — an irreversible migration, or reports must be re-ingested to
  display correctly. Back up `./data` before upgrading.
- **MINOR** — new capability. Existing data reads the same.
- **PATCH** — fixes and internals. Nothing already loaded looks different.

A release that changes what **already-ingested** data displays says so at the
top of its entry, and is never a PATCH. That case is invisible to a version
number on its own: no migration runs, nothing needs re-reading, and yet a figure
you wrote down last month is now different.

**Nothing from a real response appears here.** This file is committed and
permanent. It describes what changed in the software, never what any report
contained — no dates from a shopping history, no product names, no counts drawn
from a specific response. See `CONTRIBUTING.md`.

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/

## [Unreleased]

### Changed

- **The SPA route serves from an allowlist built at startup, not a path built
  per request.** Same behaviour, different construction: every file in the
  bundle is mapped once from its request path to the `Path` that serves it, so a
  request selects an entry rather than assembling one. Nothing to validate,
  because nothing is built. The containment check that used to run per request
  now runs over the map as it is built — still load-bearing, and asserted:
  remove it and the symlink case in `TestStaticRouteTraversal` fails.

  This also clears three `py/path-injection` alerts that CodeQL raised against
  the old form. They were false positives — the old guard blocked all thirteen
  traversal vectors the tests cover — but CodeQL does not model
  `Path.is_relative_to` as a sanitizer, and rewriting the check as
  `try/except relative_to` did not satisfy it either. Cutting the taint at its
  source did.

- **The timeline's month is a running head that runs.** It used to print once at
  the moment the month changed and then scroll away, so from about row 40 of a
  ~5,300px roll nothing on screen answered "when am I". The months are now a
  sticky rail in the margin, one row each with a bar for what the month cost, and
  each one jumps. A one-line head names the month you are in. The 148px inline
  chart comes back as reclaimed space above `lg`; below `lg`, where there is no
  margin, it stays and its bars became clickable. The page citation moves from
  the margin onto the row, because a sticky rail and a per-row footnote cannot
  share one column — recorded in `DESIGN.md`'s decisions log.
- **The compliance follow-up draft is as tall as it needs, up to a cap.** It was
  a fixed `rows={18}`: 364px of read-only preview on a section running about
  700px, for a document that gets read in a mail client. The field now takes the
  draft's own line count capped at eight — 176px for the fixture's 25-line
  letter, four rows for a four-line one — with a control that shows the whole
  thing. Shortened rather than hidden: the field still scrolls to every word
  without touching the control, and the control appears only when the collapsed
  box is actually holding something back.

### Added

- **The product index saves as an image.** SVG of text, not a rasterised
  screenshot: the page is a field of type, so the file is selectable,
  searchable, scales to a wall print, and came out at 36 KB for 399 products.
  It needs no library — the obvious route is html2canvas, which is around fifty
  times the 2.3 KB this cost and re-implements text layout slightly wrong, in a
  build that vendors everything and asserts it loads nothing from another
  origin. The saved file references nothing it would have to fetch, asserted
  against the real download in a browser. It exports what is on screen, filters
  included.

## [0.11.0] - 2026-09-05

### Changed

- **`unbagged sanitize` masks keys that are identifiers.** Same input, different
  skeleton: a key matching a long digit run, a UUID or an email is now
  `<key:len=N>`. Field names are unaffected. Reason in the Security section below.
- **Documentation trimmed for a public repository.** The README carries badges and
  screenshots and drops an unverifiable claim about what other tooling does; the
  implementation brief moved to `docs/handoff.md` with every `§N` citation in
  `src/` and `tests/` rewritten and checked to resolve. Two cross-document
  contradictions fixed: `CLAUDE.md` said red had one call site where `DESIGN.md`,
  which it names as the authority, records two, and `docs/handoff.md` §6 still
  embedded a `.gitignore` and a "`.dockerignore` mirrors `.gitignore`" instruction
  that this release proves wrong.

### Added

- **`make screenshots`** regenerates `docs/screenshots/` from a throwaway container
  seeded only with the synthetic fixture. It takes no URL on purpose: the PII
  scanner cannot read a PNG, so the guarantee that a published screenshot contains
  nobody's data has to come from the capture path rather than from a check
  afterwards. A developer's own instance is bind-mounted to `./data` and is exactly
  what must not be photographed.

### Security

- **The runtime dependencies are pinned and hashed.** The Dockerfile ran
  `pip install .`, resolving 29 packages fresh at every build with no pins and no
  hashes, into the container that reads people's reports; a compromised release
  anywhere in that graph executed at build time and then ran against the data.
  `frontend/package-lock.json` already gave the UI this guarantee. The Python
  half is now `docker/requirements.txt`, installed with `--require-hashes` —
  which is all-or-nothing, so the build cannot silently fall back to a
  floor-based resolve. `make lock` regenerates it inside `python:3.12-slim` on
  linux/amd64, because hashes are per-wheel and a lock compiled in a macOS venv
  pins wheels the image cannot install. The contributor path is unchanged:
  `pip install -e ".[dev]"` still resolves floors on whatever platform you are
  on, which is why the lock lives in `docker/` and not at the root. Proved
  load-bearing by corrupting a hash and watching the build refuse it.
  `tools/check_lock.py` runs in CI and fails when the lock stops covering what
  `pyproject.toml` declares; Dependabot has a `/docker` entry so the pins do not
  decay into unpatched dependencies.

- **`unbagged sanitize` no longer publishes identifiers that appear as keys.**
  The skeleton kept object keys on the reasoning that keys are the retailer's
  schema. True for field names, and false for a map keyed by the user's data —
  and the counterexample was already in this repository, asserted by a test: a
  Kroger identity blob keys `loyaltyCards` by the card number. The skeleton
  published those numbers while faithfully masking everything they pointed at,
  and `CONTRIBUTING.md` tells people to attach the output to a public issue. A
  key matching a long digit run, a UUID or an email address is now masked to
  `<key:len=N>`; the length is kept so the shape of the map stays readable, and
  every field name survives.

- **A real report could be committed into a `fixtures/` directory and pass every
  safeguard in the project.** The one hole in `.gitignore` is its re-inclusion of
  fixture directories, and `tools/scan_pii.py` stands its address-shaped rules
  down inside a generated one — both safe only because `make fixtures-check`
  proves the directory's contents come out of a seeded generator. That check
  compared only the filenames the generator produced, so anything committed
  *alongside* them was covered by nothing. Verified end to end before the fix: a
  file carrying a name, street address, city/state/ZIP and a 13-digit loyalty
  number was reported clean by the working-tree scan, the history scan,
  `fixtures-check` and the CI stray-file job. `--check` now compares both
  directions and fails on any committed file no generator produces, and the
  scanner independently rejects any file in a fixtures directory it cannot read
  — a PDF or a spreadsheet there was previously skipped on its suffix and never
  looked at. Both halves are covered by tests.

- **`.dockerignore` said it mirrored `.gitignore` and did not.** The two formats
  look alike and match differently: a `.gitignore` pattern with no slash matches
  at every depth, while a `.dockerignore` pattern is matched against the whole
  path, so `*.pdf` excluded `./report.pdf` and nothing below it. `make reset`
  renames `./data` to `data.bak-<timestamp>` and leaves it in the checkout, and
  that directory was denied by neither name nor wildcard — so the next
  `docker compose build` sent every report and the database to the daemon, which
  caches its build context. Confirmed by building a probe context and listing what
  arrived. Every wildcard is now `**/`-prefixed, `data.bak-*/` and `.gstack/` are
  denied by name, and `tests/test_packaging.py` fails if a depth-limited pattern
  reappears or if the two files stop denying the same report formats.

- **Report formats that arrive as spreadsheets or archives were denied nowhere.**
  `.gitignore` covered `.pdf`, `.zip` and `.csv`; `.xlsx`, `.ods`, `.docx`,
  `.eml`, `.mbox`, `.7z`, `.rar` and the tarballs were trackable, and every one
  of them is a format `scan_pii.py` cannot read. Both ignore files and the CI
  stray-file job now carry the same sixteen suffixes.

- **`tests/fixtures/**` was re-included by `.gitignore` and verified by nothing.**
  `make_fixtures.py` only ever covered `src/**/fixtures/`, so the second
  re-inclusion was an exemption with no check behind it. Removed, and CI now
  fails on any re-inclusion that regeneration does not cover.

- **CI actions are pinned to commit SHAs.** A tag is a movable pointer the
  action's owner controls; repointing `v4` would have run new code here with no
  diff in the workflow. `.github/dependabot.yml` keeps the pins from decaying
  into unpatched dependencies, and covers `pip` and `npm` too.

- **Security linting was configured but never running.** `ruff`'s `S`
  (flake8-bandit) rules were not selected, which meant the `# noqa: S608` already
  sitting in `ingest.py` silenced a rule that had never run — a suppression that
  read as a reviewed decision and was inert. `S` is now selected. All eleven hits
  in `src/` and `tools/` were traced and are safe; each carries an inline
  suppression naming the reason, and the three SQL sites say why the interpolated
  value cannot come from a caller.

- **`SECURITY.md`** states what to report, how to report it privately, and what is
  in scope for a single-user local app.

### Added

- **A response can be removed.** The endpoint and its client wrapper had existed
  from the start with no call site, so a report could be loaded and never taken
  back; the only remedy was `make reset`, which moves the whole data directory
  aside. The control sits at the foot of the page and confirms by naming the
  retailer and its coverage window. It also makes the duplicate-upload error
  actionable, which told people to "remove the existing one first".
- The synthetic report is named in the README as something you can drop in
  before your own response arrives, which takes weeks.

### Changed

- Red now has two documented call sites instead of one: a delete confirmation
  and a failed request. The file said "exactly one" and named a control that did
  not exist, while the only real use was the error box.
- The A-Z rail on Products is reachable by keyboard before the products it
  skips, not after all 399 of them.
- Basket rows wrap to two lines on a narrow screen instead of squeezing the
  store column to nothing.
- `docs/handoff.md` §8 and §9 are marked historical, with a table of what shipped
  against what was asked for. §§0-7 stay authoritative; the adapter rules and
  the schema are cited from source.
- The upload prompt no longer offers to read a zip, which was never supported.

### Fixed

- Compare and Timeline scrolled the page sideways on a phone. Compare's cause
  was an absolutely positioned screen-reader label escaping a `static` scroll
  container and counting toward the document's own width.
- A skip link that scrolled but did not move keyboard focus, so it was
  decoration.
- `unbagged sanitize` reported ".pdf is not supported" for a path that did not
  exist, because the suffix was checked before the file.
- The version in the footer is asserted against the `VERSION` file. It silently
  reported 0.9.0 through the entire 0.10.0 bump.
- Documentation that described a UI that had been replaced: the compliance
  matrix, and a price history counting "days a product was bought". The second
  was also wrong about the data — the format puts a repeat purchase on one line,
  measured at 0 of 762 product-days.

## [0.10.0] - 2026-09-04

### Added

- **Products, a sixth view.** Every product in a response, set as a typographic
  index: alphabetical, sized by how often it was bought, with an A-Z thumb rail
  and a size legend in the margin. Not a ranking — Prices already answers "what
  do I buy most" precisely — but a portrait of the vocabulary a retailer files
  your shopping under. Entries link through to the visits that contained them.
- A closing list of products bought more than once and then not again before the
  coverage window closed, worded as an observation about dates.

### Changed

- Size in the index is quantised onto five absolute tiers rather than scaled
  continuously. A continuous ramp put 28.3% of comparable pairs backwards,
  because a long name set small paints more ink than a short name set large.
  Absolute tiers take that to 0% and remove a divide-by-zero with it.
- The reading measure is now actually delivered. The margin was 15rem against a
  declared 92ch measure that did not fit the shell, so the spine silently
  rendered 640px and basket rows 540px. At 11rem they measure 704px and 604px.
- The tab hints are visible sub-labels instead of `title` tooltips, which were
  unreachable on touch.
- The appended-attributes panel in Profile now reaches the page edge at every
  breakpoint. Its negative margin did not match the shell's padding above `sm`,
  so it rendered as an inset card rather than as a change of stock.
- Prices caps the unpriceable list the same way it caps the priced table.

### Fixed

- The synthetic fixture had no long tail. A uniform draw over a small catalogue
  produced a median product bought five times and 4.9% bought exactly once,
  against 68% in a real response, so nothing that has to survive a long tail
  could be tested. The catalogue is now far larger than any one history and the
  draw is weighted.
- The fixture emitted the same product twice in one basket. A trip puts a
  product on exactly one line; the previous generator drew with replacement, so
  a purchase count and a visit count disagreed for the same product.

## [0.9.0] - 2026-09-03

First versioned release. All eight milestones complete. Pre-1.0 deliberately:
the adapter contract promises a retailer can be added without touching core
code, and that has been exercised against exactly one real format. A second real
response is what earns 1.0.

**Figures change.** Baskets already loaded will show different totals after this
upgrade. Nothing needs re-ingesting for the totals themselves; the correction is
in how stored amounts are read, and the stored amounts were always kept verbatim.

### Fixed

- **Kroger (adapter schema 1 → 2): the loyalty amount is the price a line cost,
  not a discount to subtract from the shelf price.** Read as a discount, an item
  bought at its ordinary price came out free, so "You paid" showed nothing on
  most lines and every total collapsed to a fraction of what was actually spent.
  The synthetic fixture had encoded the same misreading, so the test suite
  agreed with the bug; the fixture generator is corrected too.
- The Prices tab counted rows in the export rather than the days a product was
  bought, and labelled the result as though it were a quantity. This retailer
  discloses no quantity at all, so a trip carrying three of something arrived as
  three rows indistinguishable from three separate trips. Days bought and raw
  line count are now reported separately, and same-day rows collapse to one
  point so a price series no longer doubles back on itself.
- A basket's collapsed row showed its pre-discount total while the line items
  beneath it showed what was paid, so the two never agreed and nothing on screen
  explained the gap.

### Added

- Every basket is checked against the total the retailer states for it, and
  marked in the timeline when the two disagree. Spot-checked against a real
  response by hand: where they disagree, the difference is in the response as
  supplied, so the marker is informational and there is nothing to correct.
- The Prices chart plots what was paid alongside the shelf price, and says
  plainly that no quantity was disclosed.
- `tools/check_footing.py`, which compares a stored basket against its source
  document and prints only counts, booleans and differences — never document
  content — so a parse can be verified without quoting anyone's report.
- The running version appears in the footer. With no telemetry and no update
  check, there was previously no way for a person to say what they were running.

### Changed

- "Total spend" is now **Total paid**, showing what left the account, with the
  shelf total and the loyalty saving beside it so the arithmetic is checkable.
  Comparing two retailers on pre-discount totals ranked them by whose shelf
  prices were higher rather than by which one cost more.
- `VERSION` at the repo root is the single source of truth for the version; the
  package and the Docker image both read it at build time.

### Notes for anyone with a Kroger report already loaded

Nothing is required. Totals correct themselves on upgrade because the raw
amounts were never mutated. The database still records that the report was read
by adapter schema 1; re-ingesting is the way to stamp it with the current
reading, and is otherwise unnecessary.
