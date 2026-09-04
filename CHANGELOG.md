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
- `HANDOFF.md` §8 and §9 are marked historical, with a table of what shipped
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
