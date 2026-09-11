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

## Unreleased

### Fixed

- **Starting the app did not build it, so it served the version before last.**
  `make up` ran `docker compose up` with no `--build`, and Compose reuses an
  image already tagged `unbagged:local` rather than noticing the checkout has
  moved. Pulling a release and starting it therefore ran the previous release:
  its code, its migrations, its bugs. The footer was the only thing that showed
  it, and the footer was right — the container really was the version it named,
  which is why every version test passed. `make up`, `make dev`, and the
  quickstart in the README now rebuild first; a rebuild with nothing to do
  measured at about two seconds. The dev overlay also mounts `VERSION`, so
  bumping it against a running stack no longer reloads onto the copy baked into
  the image. A guard asserts the property over the whole repository rather than
  over a list of files: every line that starts the app has to rebuild it,
  wherever it is written down.

## [0.14.0] - 2026-09-11

**This release changes what already-ingested data displays, in three places.** A
migration runs and nothing needs re-reading: every response keeps the id it had,
so a bookmark or a saved link still opens what it always did. What reads
differently on a report you already loaded — Compare's three inventory figures,
identifiers held, inferred attributes and how many of those came from elsewhere,
render an em dash rather than **0** wherever the category was not answered in
full, because a zero there read as a fact about the retailer rather than as an
absence of disclosure; Compare's row for the headline figure reads **Total
spent** rather than "Total paid" when the responses being compared disclose
totals without line items, which is what the Timeline header already said over
the same number; and a store whose colour could not be told apart from one
already on screen is given a different hue, so two branches of one retailer no
longer render identically in the legend and on every row below it. The first two
are figures and words you may have written down. Nothing else about a stored
report reads differently.

### Changed

- **The test suite now fails as whatever it is actually doing.** A hung test ran
  until something else gave up: no time cap existed, so the browser tier could
  spend five minutes on one test and then report a timeout rather than a
  diagnosis. Both tiers have a cap sized against their measured worst case, and a
  stuck test now names the line it is stuck on. Tests that need a browser check
  that one was actually downloaded rather than only that the library imports, so
  a machine without one skips in milliseconds instead of building an image and
  failing at launch. The browser tests no longer restate adapter constants they
  do not own, so retuning a confidence score cannot break a two-minute test with
  a DOM assertion. Repo tooling is invoked as `python -m tools.X` everywhere,
  which removes a hand-rolled `sys.path` workaround and the class of failure that
  hides from pytest. On Linux the container tier hands its scratch directories
  back rather than leaving root-owned trees that later runs cannot clean up.

- **The formatter the project said it used had never been run.** `CLAUDE.md` has
  claimed `ruff` for lint and format since the start, but `make lint` only ever
  ran the check half, so nothing applied or enforced the other one and 64 of 97
  Python files differed from what it produces. The formatter has now been applied
  in a single mechanical commit, listed in `.git-blame-ignore-revs` so history
  stays navigable, and `make lint`, the pre-commit hook and CI all run
  `ruff format --check` from here on. `make format` applies it. Two things had to
  be decided rather than swept: the formatter splits calls across lines, which
  moved two `# pii-scan: allow` suppressions away from the values they protect —
  caught by the scanner, and fixed by moving them next to the literal — and it
  joins strings onto lines it will not then split, which pushed six letter
  phrases past the 100-column limit its own linter enforces. Those are
  parenthesised now, so both tools are satisfied and the column limit stands.
  `docs/handoff.md` is excluded from formatting: it is the live contract, and a
  contract is changed deliberately rather than by a sweep.

- **Four places where the documentation described something the repository does
  not do.** The architecture diagram in the handoff named four views when there
  are six, and stops naming them at all now — it is about where the read path
  goes, and enumerating views had gone stale twice. `make help` still called the
  browser tests "the layout regression test", from when there was one of them.
  Three committed test docstrings cited a QA report path under a gitignored
  directory, so the pointer resolved for nobody who cloned; they cite the commit
  that landed each fix instead, and `CONTRIBUTING.md` now says why. The document
  the Compliance view names on screen — where the eight categories come from —
  was reachable from nine files but not from the README, and now is. Also caught
  while linking it: the README said Kroger was the only full adapter, two lines
  below a table listing H Mart as one too.

### Fixed

- **Every stored document recorded nothing about what it was.** The media type
  and page count of an uploaded file were written as empty for every response
  ever loaded, even though the database had columns for them, the reader worked
  them out, and the tests asserted them one layer up. They simply never
  travelled the last step. Nothing displayed them, so nothing looked wrong — and
  the test fixtures supplied a 48-page PDF, so anything built on them would have
  worked in the tests and been blank in the app. Both are recorded now, worked
  out without re-reading the file, and a spreadsheet records no page count
  rather than a wrong one, because sheets are not pages.
- **One drawing fault blanked the whole page.** React removes everything when a
  view fails to draw, so a value in a shape nothing anticipated left a white
  screen with no explanation — no header, no tabs, no version number to quote,
  and no way to tell whether the response had been lost. The views are drawn from
  files this tool has often never seen before, so that is a real risk rather than
  a theoretical one. A failure now stays inside the view it happened in: the
  page says what went wrong, says the response is still stored, and leaves the
  tabs and the footer where they were so another view is one click away.

- **A response could be handed the id of one you had just deleted.** The id is
  what the upload report panel and the `?r=` in the address bar both use to name
  a response, and SQLite hands the highest one out again after a delete. Remove
  the newest response, upload another, and the new one could take the old one's
  id: the report panel then showed one response's counts and warnings over
  another's, and pressing Back onto an older history entry rendered a different
  response than the entry meant. Ids are never reused now. Existing ones are
  preserved by the migration, so a bookmarked link still opens what it always
  did. Each surface had been patched separately before this — the panel learned
  to compare the retailer as well as the id, which closed one case and left the
  other — and the schema now removes the cause rather than the symptoms.

- **Two stores could be given the same colour, and the legend exists to tell
  them apart.** Store colours are hashed into six hues, so three stores share
  one about 44% of the time — and the synthetic H Mart history hits it, with two
  branches rendering identically in the legend and on every row below it. The
  hash still assigns every colour; only a store whose colour cannot be told
  apart from one already on screen moves, so a store that was not part of the
  problem keeps the colour it has always had. "Cannot be told apart" is wider
  than "the same slot", and that is the part that matters: several of the six
  hues are effectively identical to a reader with red-green colour blindness, so
  handing out a different slot can leave two dots looking the same to roughly
  one person in twelve while appearing fixed. Two- and three-store responses are
  now always separable; above that the assignment reaches the best the palette
  can do, and past six there is nothing left to give. The label sits beside
  every colour at every count, which is why this degrades rather than breaks.

- **The same figure was called two different things depending on which tab you
  were on.** For a response that discloses basket totals and no line items, the
  Timeline header read "Total spent" and the Compare row read "Total paid" over
  the same number. Both were deliberate and both had written reasons, and
  neither had been reconciled with the other. Compare's row label now follows
  the columns beside it, so the two views never disagree; the column head keeps
  saying which column holds which quantity. The rule that picks the word lives
  in one place now, shared by three surfaces that were each deciding it
  separately — the header, the month bar axis, and this row. The axis keeps
  asking its own question about the months on screen, because that is genuinely
  what its bars are drawn from; only the vocabulary is shared.

- **Compare stated that a retailer holds no inferred attributes, for a response
  that never mentioned inferences.** The three inventory figures — identifiers
  held, inferred attributes, and how many of those came from elsewhere — were
  gated on a predicate that asks whether the response disclosed *purchases*.
  That is a different question. A response can disclose every visit's date,
  store and total while saying nothing about what it infers, and the adapter for
  that shape grades the disclosure "partial" on purpose; the purchase predicate
  answered yes and the cell rendered `0`, which reads as a fact about the
  company. It reads as an em dash now, and a zero appears only where the
  category was answered in full. A count that is not zero always renders,
  whatever the grade, so a disclosed identifier is not hidden alongside it. The
  sentence explaining the mark has moved above the table and now appears
  whenever a dash is on the page rather than only when a whole column disclosed
  nothing, which was the one case it was hidden for. Each dash also carries a
  short label for screen readers, which announce a lone em dash as "em dash" or
  skip it. Narrows the problem rather than closing it: the statute does not
  enumerate inferences separately, so a retailer that answered in full and has
  no inference section still shows a zero.

- **The upload moved you back to where you had been ten seconds earlier.** A
  long report takes tens of seconds to read, and there is nothing to do while it
  does. Wander off to another view and the finished upload put you back on the
  one you were on when you dropped the file — for a different response than the
  one you had been reading. The view is now read as the upload lands rather than
  as it starts.
- **A beat of the wrong response between an upload and its report.** The list of
  responses is re-read after every upload, and until it arrived the page fell
  back to the oldest response you had loaded: the wrong retailer on screen, the
  wrong name in the selector, and a round of queries for a response nobody asked
  for — competing for the same capacity as the upload still finishing. The new
  response is committed before any of that starts, so the page now shows it
  straight away and the report no longer disappears and pops back.
- **Back could not be used after an upload.** Adding or removing a response is
  something you did, not somewhere you went, and both were recorded in browser
  history as though they were places. Two consequences: after the first upload
  of a session Back appeared to do nothing at all, and after removing a response
  Back landed on a link naming the response that had just been deleted — a URL
  worth nothing if it were bookmarked or sent to someone. Neither is recorded in
  history now, and the address bar still names the response on screen, so a
  bookmark and a page reload still work.
- **Nothing told you the upload had finished.** The uploader sits at the foot of
  a long document, so you are at the bottom of the page when you drop a file,
  and the response that replaces the page can be a very different length. Where
  you ended up was whatever the browser happened to do with the scroll position.
  The report is now brought into view and given focus, and the outcome is
  announced — which retailer matched, whether the match was confident, and the
  counts. For anyone using a screen reader the entire page changed in silence
  before this, with no way to tell the upload had done anything at all. A
  refused upload now says so out loud too, rather than only drawing a box.
- **A failed request for the list of responses deleted the report and the app.**
  Any interruption — restarting the backend, a dropped connection — emptied the
  page: the report you had just waited half a minute for was gone, and the app
  returned to its first-run screen, offering to load a first response for an
  archive that was not empty. Being unable to read the list is not the same fact
  as there being nothing in it, and the two now read differently. What was
  loaded stays on screen, with a line saying the list could not be re-read and a
  way to try again; a failed first read says so plainly instead of claiming the
  archive is empty.
- **A second window never found out about a removal.** Removing a response in
  one tab left every other tab still offering it, still pointed at it, and every
  request for it failing — with no way back except reloading the page by hand.
  The same happened to a single tab after `make reset`. A tab now re-reads the
  list when you return to it, and moves off a response that is no longer there
  rather than leaving the address bar naming it.
- **A double drop sent two uploads.** Two clicks in quick succession, or a drop
  landing on top of a click, both got through: the second was refused by the
  server, so nothing was duplicated, but the refusal read like a bug and could
  leave the report describing a different upload than the one on screen. A drop
  refused because one is already running now says so, rather than being
  swallowed in silence.
- **Removing a response could remove a different one.** The confirmation names
  the retailer you are about to delete, and the response behind an id can change
  underneath it — the database reuses the number of a deleted row, and a tab
  that refreshes in the background can pick up the new occupant. Nothing forced
  the confirmation to notice, so the text could change from one retailer to
  another between reading it and clicking it. The removal is now tied to the
  retailer as well as the number, and it is withdrawn entirely while the list of
  responses cannot be read, since it cannot be aimed reliably then.
- **A failed refresh could put a deleted response back on screen.** Once the app
  had successfully seen that a response was gone, a later failed refresh
  resurrected the report describing it — counts, retailer and all, read out to
  screen readers a second time. Being unable to look is not the same as looking
  and finding it there, and only the second one is now allowed to bring anything
  back.
- **A failed refresh could throw away the response you asked for.** Opening a
  saved link while the backend was restarting dropped the response from the
  address bar, so recovering landed you somewhere else with no record of what
  you had asked for. And a failed refresh immediately after an upload took the
  new response with it — the report stayed on screen while the page moved to a
  different one.
- **Back onto a removed response left the address bar naming it.** Pressing Back
  onto a link to a response that had since been deleted showed a different one
  while the address bar went on naming the deleted one for the rest of the
  session, so anything copied from there was wrong.
- **A failed refresh could hide an upload that was still running.** Tabbing away
  and back during a long parse could replace the running progress indicator with
  an error about something else entirely.
- **The first upload never showed what it had just read.** Every upload produces
  a short report — which retailer matched, how confident that match is, the
  counts, and any warning the adapter raised — and on the very first one it was
  created and destroyed in the same instant. The screen a first-time reader is
  looking at is replaced by the report the moment a response loads, and the
  panel went with it.

  The warning that mattered most was the one about a file that did not make it
  in. Dropping two retailers' responses together is one upload, and one upload
  belongs to one retailer: the reader that recognises its own format wins, and
  the other file is named in a warning saying nothing from it is in this report.
  That line was destroyed before it could be read, so a whole second response
  could go missing with nothing on screen to say so. The "check this is the
  retailer you meant" caveat on a weak match went the same way.

  Now held by the page rather than by the panel, so it survives the moment the
  screen changes under it. A browser-tier test drives a real first upload of two
  responses at once and fails against the previous build.
- **Adding a response did not switch to it.** From the second upload onward the
  new response joined the list and the page kept showing the previous one, so
  the line saying what had just been read sat above a timeline belonging to
  something else. The first upload of a session hid this, because the response
  it created was the only one there.

  The cost was not only confusion. **Remove acts on the response you are
  looking at**, so the obvious next click after adding removed the wrong one.
  Adding a response now selects it, and any product filter from the response
  you were on is dropped rather than carried across.
- **Filters followed you onto a response that had never heard of them.** The
  timeline's store, date and search boxes belong to the view rather than to the
  address bar, and switching response did not reset them. A store from the
  response you left matches nothing in the one you arrive at, so the control
  read "every store" while the store was still being asked for — and the new
  response came up empty, under a heading counting every visit in it, with
  nothing on screen to clear. Switching now starts the timeline fresh.
- **The report outlived the thing it described.** Keeping it on the page is what
  makes the fix above work, and it also meant the report stopped dying when it
  should. Removing a response left its report standing; so did a failure to
  re-read the stored responses, which returns you to the first-run screen. It is
  now tied to the response it names, so it goes when that goes and stays while
  that stays — deleting some *other* response no longer throws away an accurate
  report, warnings included.
- **A refused upload showed the refusal above the previous success.** Dropping
  the same response twice is the ordinary way to reach it: a long parse gives no
  sign of progress, so people drop the file again, and the server refuses it by
  name. The old report stood directly beneath that refusal, reading as though
  the second drop had partly worked. A report is now cleared when an upload
  starts, so it also stops sitting under the spinner for the whole of a parse.

## [0.13.0] - 2026-09-09

**This release changes what already-ingested data displays.** No migration runs
and nothing needs re-reading, so the change is invisible to the version number
on its own — which is why it is stated here. A basket whose disclosed saving was
**zero** used to render an em dash and now renders blank: the dash meant absence
and a zero saving is a disclosed fact, not a missing one. Measured against the
Kroger fixture: four baskets of 127. Nothing else about a stored report reads
differently.

### Added

- **An H Mart adapter**, and with it the tabular half of the extraction
  boundary. `extraction.py` spoke PDF and text, so a spreadsheet never reached
  an adapter at all; it now reads SpreadsheetML into placed cells, routed by
  content because `.xls` covers two unrelated formats. `Table.locator()` finally
  delivers the A1 cell reference `docs/writing-an-adapter.md` has always
  promised adapters as a locator.
- **A response can now disclose what a visit cost without disclosing what was in
  it.** The model had only met itemised baskets. Line-derived figures go null
  rather than zero for such a response — zero would say the retailer disclosed
  baskets costing nothing — and the headline figure carries the stated totals,
  which are the only real money in it.
- `sniff()` may return a `SniffResult` carrying a reason as well as a score. A
  bounded read that spends its budget scores the same 0.0 as a file in somebody
  else's format, and those are different answers.
- `unbagged sanitize` understands spreadsheets, so a maintainer can be sent the
  shape of one without the values.

### Fixed

- **A merged cell moved every value after it one column left, and said nothing
  about it.** SpreadsheetML writes a cell spanning several columns once, with
  `ss:MergeAcross`, and does not write the columns it swallows — so the next
  cell element belongs *after* the span, not next to it. The reader placed it
  next to it. Against a dense header row that raises nothing and reads nothing
  as wrong; the values simply land in the neighbouring fields.

  The observed H Mart export merges its banner across four columns, so this
  attribute arrives in every response that retailer sends. It costs nothing
  there, because nothing follows the banner cell. Measured on a row where
  `Branch` is merged across one, which is the shape that does cost something:
  the points value landed in the amount column, and since a point is the amount
  rounded, a basket recorded as its own total rounded to the nearest dollar. The
  same failure `ss:Index` causes, from the attribute beside it.

  No released version could have mis-read one: reading spreadsheets at all
  arrives in this release. `ss:MergeDown` is the same fault across rows rather
  than along one and is **not** fixed — it needs state the reader does not carry
  between rows, and no observed export uses it.
- **The fixture generator could not produce the shape that hid it.** It wrote
  the banner as a plain single cell; the real one is merged, wraps its text in
  `html:B`/`html:U`/`html:Font`, and carries a second child after its `ss:Data`.
  Now reproduced, along with the style ids on every cell, the column widths and
  the worksheet's siblings. It still cannot catch that particular shift on its
  own, because nothing follows a banner — recorded in the generator rather than
  left to be assumed.
- **The product index counted products a response never itemised.** A response
  that priced every visit and itemised none satisfies "did they disclose the
  specific pieces", so it reached a headline reading `0 of 0 products you bought
  exactly once` directly above an empty state explaining that nothing was
  itemised. Two zeros presented as facts about the shopper, contradicted three
  lines down. It now takes the branch Price History already had, for the same
  reason.
- **"Two years of shopping" was a hardcoded claim about the reader's own data.**
  Written against a response that covered two years. The window is on the
  Timeline, measured; it is no longer restated where nothing checks it.
- **The em dash meant two things.** It renders for a null, which is absence, and
  it also rendered for a *zero saving*, which is a disclosed fact. Settled in
  `DESIGN.md`: absence only, and a disclosed zero renders blank.
- **A response in a format without pages rendered no citations at all.** `Cite`
  returned nothing unless a page number was set, which retired the footnote
  apparatus this design is built on for a whole class of response. It falls back
  to the locator.
- The Profile margin claimed "all individual" for identifiers whose scope the
  response never stated.
- Price History said "nothing bought often enough" — a claim about the shopper,
  offering a threshold control that could not help — when the retailer had
  disclosed no line items at all.
- The month rail was built for two years of months and became unreachable past
  about forty: taller than the viewport, so `sticky` had nothing to stick to. It
  is grouped by year, and months with no visit are now visible as gaps rather
  than absent rows.

## [0.12.0] - 2026-09-05

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

### Fixed


- **The favicon and logo never appeared, because the assets were never wired to
  anything.** They were committed to `resources/` in a commit named "add the
  logo and icon assets, unmoved" and the second half never happened:
  `index.html` declared no icon, `frontend/public/` did not exist, and nothing
  served `resources/`. Every brand URL fell through to the SPA shell, so the
  browser asked for `/favicon.ico` on each page load and got HTML back. Nothing
  in the suite noticed, because nothing asserted the app has a favicon at all.

  The tab now carries the mark (`.ico` plus an SVG for browsers that take one,
  and an apple-touch-icon), and the first-run screen carries it once. That
  needed `DESIGN.md` to change: it banned illustration outright, and the first
  version of this claimed a first-run exception the file did not contain. The
  ban is now scoped to surfaces carrying report data, with the tab and the
  empty state named as exceptions in a dated decisions row.

  `make brand` produces the served copies from the sources, stripping a C2PA
  content-credential manifest that is most of each file: 93% and 90% of the two
  SVGs and 64% of the touch icon. The `.ico` carries none and ships as authored,
  which is now asserted rather than assumed — it is a container of complete PNG
  streams, and the sources it could have been assembled from each carry a `caBX`
  chunk. Not a bandwidth argument: this is a local-first app served over
  loopback, where transfer cost is not a cost. The reason is that the manifest
  names `c2pa.org` and carries a provenance record, in a build whose README
  promises it reaches no other host.

  `make brand-check` runs in CI and compares both directions, so a hand-copied
  source or a stray file fails. It also asserts a property of the bytes rather
  than only equality with what is committed: both sides of an equality check run
  through the same stripper, so equality alone would stay green forever if the
  stripper silently stopped matching. One `<metadata id="...">` from an editor
  that writes attributes was enough. The served SVGs are checked for more than
  metadata: they are navigable same-origin documents, so a `<script>`,
  a `<foreignObject>`, an inline event handler or an off-origin `href` in the
  artwork would run in the app's origin. The realistic way one arrives is an
  optimiser round-trip on the logo, not an attacker; the artwork is clean, and
  nothing had been enforcing that it stays clean.

  Four ways it could have shipped a manifest anyway, each now closed and each
  with a test that fails without the fix:

  - A **symlink** under `frontend/public/` was invisible. `Path.rglob` yields a
    symlinked directory but does not descend it, so `public/vendor ->
    ../../resources` reported "4 served asset(s) match their sources" while
    Vite, which stats through symlinks, copied every raw source into the build.
    The walk now follows links and reports the link itself.
  - **Any unrecognised suffix** was shipped verbatim: the `.ico` pass-through
    was the `else` branch for everything, so a `.webp` or `.jpg` added to the
    served set would have shipped its EXIF and XMP with the check green. It is
    an allowlist now, and anything else stops the build.
  - **`make brand` could not produce a green tree.** It reported a stray to
    stderr and exited 0, and the repair text named `make brand`, which writes
    the served files and removes nothing. It exits non-zero now and says so.
  - **PNG comparison was byte-for-byte**, which made the check a function of
    whichever zlib the local Pillow wheel bundles: anyone on a platform with no
    wheel hit permanent drift on a file they never touched. It compares decoded
    pixels and chunk types now, which also let the exact `pillow==12.3.0` pin go
    back to a floor. That pin had frozen the dev and CI environment out of
    security updates for a library that parses images inside user-supplied PDFs.

  The strip itself was rebuilding each PNG from `tobytes()` to make it lossless
  by construction. It was lossy instead: `Image.frombytes` attaches a default
  palette to what `tobytes()` returned as palette indices, so a paletted icon
  came back black, and tRNS transparency went the same way. Today's touch icon
  is RGB and was safe by accident. Copying and emptying `info` keeps the palette
  and drops the manifest.

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
