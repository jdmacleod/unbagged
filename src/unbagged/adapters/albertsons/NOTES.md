# Albertsons response format

What one real response actually looked like, and what the synthetic fixture
reproduces. **No values from the real response appear here or anywhere in this
repository** — only structure and counts. Every claim below was observed
**once**. Treat each as "seen in one response", never as a documented contract.

> **This file previously described a response nobody had seen** — a zip of CSVs
> and a prose letter answering the categories. Neither held. The package was a
> stub named `safeway`; it is `albertsons` now, because the response is issued
> by Albertsons Companies for every banner it runs.

## Delivery

Two files, sent together, neither mentioning the other:

| File | What it is |
|---|---|
| `<date>_<ref>_<Last>_<First>_Report_pdf.pdf` | Two pages, rendered from HTML by wkhtmltopdf. A text layer, no scan. |
| `<First> <Last> Additional Details.xlsx` | A real OOXML workbook, two sheets. |

The adapter reads either alone and says what is missing when only one arrives.
`sniff()` claims a bundle on either half.

## The report PDF

In order: **Request details**, **Instructions** (two paragraphs and a list of
contact routes), **Detailed Report**, then four key/value tables:

| Heading | Scoped to | Rows |
|---|---|---|
| `Loyalty Store Visits Details [cc:<card>]` | a card | first and last store and visit date, the card number, a customer UUID |
| `Loyalty Profile Details [cc: <card>]` | a card | 22: address, alternate id, first/last/most-used/most-spent stores and dates, name parts, head-of-household flag, household id, mail flag, phone, ZIP+4, state |
| `Preference Details` | — | two cells, each a consent code, an id and an effective window |
| `Loyalty Earn Burn Details [hh:<household>]` | a household | one points event: earn, burn, program code, origin, a transaction id |

The bracketed scope is the response's own statement of what a table is about,
and the only one. Note that the points table is a **household's**, while the
visits and profile are a **card's**.

### The text layer, and the three things that bite

`report.py` isolates these.

**Bold arrives doubled, character by character.** The renderer draws a bold run
twice at an offset and the text layer keeps both: `Loyalty Profile Details`
extracts as `LLooyyaallttyy PPrrooffiillee DDeettaaiillss`, and the card number
inside the heading's brackets is doubled too. Every heading and every
request-details label is bold; **no value is**. Undoubling is therefore applied
only to lines where *every* token is doubled, and to request-block tokens that
match a known label. Applied to values it would corrupt them: `1133` is a
doubled `13`. The reader also accepts the same headings undoubled, so it does
not depend on the renderer.

**The request block prints two label/value pairs per line** — `Request ref ID
<ref> Request type Request for personal information`. Labels are found by
matching the six known label phrases; every other token belongs verbatim to the
value of the label before it.

**A multi-line cell is split around its row label.** Each preference cell holds
four `name:value` lines, and its label (`Customer Preference 1`) is vertically
centred, so it extracts between the second and third. Lines are grouped into a
record by their own keys — a repeated key starts the next record — never by
position.

Also: a `Key value` row per field, split at the first space; the profile table
breaks across the `1 / 2` page footer with no repeated heading; and three name
fields print `I/ss`, read as absence. What `I/ss` stands for is not stated.

### Identifiers

Seven distinct keys for one profile: the card (printed twice, plus in both
headings), a customer UUID, an alternate id, the household id, a preference
customer id, a phone number and an address. **Two of them are the household id
with a fixed prefix** — the alternate id and the preference customer id — which
the response does not say. The earn/burn transaction id is not stored as an
identity; it identifies an event, not a person.

Scope, and the reasoning:

- card, alternate id, preference id, phone: **`None`**. Nothing says whose. The
  card's own points are reported against the household.
- customer UUID, name: `INDIVIDUAL` — the profile is one named person's.
- household id, address: `HOUSEHOLD`. An address describes everyone who lives
  there.

## The workbook

### `Albertsons Transactions`

Row 1 is a title merged across A1:J1, row 2 is blank, **row 3 is the header**:

`TXN_DTE, TXN_ID, STORE_ID, CARD_NBR, CATEGORY_NM, UPC_ID, UPC_DSC, QTY, LBS, NET`

Then one row per line item. Measured, as counts only:

- one row per line item for one card, sorted by date; baskets are
  heavy-tailed, a handful of lines typically and dozens at the extreme
- the history can end well before the request was submitted (see "What to
  look for" below)
- no TXN_ID spans two dates or two stores; **no UPC appears twice in one
  basket**, the same as Kroger
- `TXN_DTE` is an **Excel date serial** in a numeric cell, styled to display as
  a date; no time of day anywhere
- `TXN_ID`, `STORE_ID`, `CARD_NBR`, `UPC_ID` are **numeric cells**, so UPCs have
  lost their leading zeros and widths vary (10 and 11 digits mostly; 1 and 6
  digits for department-key rings)
- `UPC_DSC` is the only text column — every description is a shared string
- `CATEGORY_NM` is a **bare number** (dozens of distinct codes) with no legend anywhere in
  the response. Not stored: the model has no slot for a retailer's own category
  code, and `TxnItem.category` is the enrichment pass's, not the retailer's

**`QTY` and `LBS` are disclosed** — the gap Kroger's export leaves. Weighed
lines carry `QTY` 1 and a weight. `QTY` is stored as `TxnItem.quantity`, and
Prices divides by it; `LBS` has no field and is not stored.

**`NET` is the amount paid, after every markdown** — stored as `loyalty_amt`,
with `retail_amt` left `None`. Three things prove it is net, and they are worth
knowing because each looks like a bug:

- **fractions of a cent** on under 3% of lines (`1.8341`): a basket-level markdown is
  spread across the basket's lines, and the reallocation lines name themselves
  (`GROC DEPT KEY MKDN REALLOC <dept>`)
- **negative lines**, about 1%, almost all `QTY` 0 — markdowns, instant
  savings and unidentified-item corrections — and a smaller share at zero,
  mostly coupons
- **binary-float artefacts** (`1.1500000000000001`) and **E-notation**
  (`-4.1E-3`) in numeric cells, because the values were computed upstream as
  doubles. `float()` reads both; nothing is rounded, because a fraction of a
  cent here is data, not noise

What is not there: a shelf price, a basket total, a time, a tender, a channel.
So there is no footing check (nothing to foot against — summing the lines into
`total_pre_discount` would compare them with themselves).

Prices reads `QTY`: a line's price is its amount divided by its quantity, so
a line of two plots at one item's price. Weighed lines say `QTY` 1, so the
per-pound products are still found by the shape of their amounts and left
undrawn — which is right, and is what `LBS` would fix if it had a field.

Products and Prices originally counted a purchase by its shelf price alone, so
both were empty for this retailer. They now share `views.PURCHASE_AMOUNT`: the
shelf price where disclosed, the paid amount where not. Prices draws only the
paid line here and says why. `NET` is never relabelled as a shelf price to fill
the chart; that would be the inverted-amount mistake `TxnItem`'s docstring
warns about, in a new direction.

### `Demographic Data`

`Related Card, Inferred Description, Value` — dozens of rows, all one card,
sorted by label. The retailer's own heading is "Inferred". Every row is
`APPENDED_THIRD_PARTY`: age and year of birth, gender, family position, marital
status, education, occupation, household income band, net worth, a wealth
decile, credit-line count and card types, investments, home value and length of
residence, dwelling type, census tract, block group and county, metropolitan
area, and dozens of interest, buyer and mail-order flags. Nothing in a grocery
basket gives a net worth or a census block. **The source is not named.** It is
the largest appended block of the three responses seen, and the most concrete
form `SOURCES = ABSENT` has taken.

Judgment calls, each of which could have gone the other way:

- **Subject is named only when the label names it.** `HOUSEHOLD` for labels
  about a household, a residence, a dwelling, a home's value, or the census
  geography it sits in; `None` otherwise. `Age` gets `None`: the sheet does not
  say whose age it is, and the profile it hangs off need not be the
  requester's (below).
- **`derivable_from_txns`**: `True` for "heavy buyer of …" and health-and-beauty
  purchase flags, which the baskets could explain; `None` for internet-buyer
  flags, because no channel is disclosed; `False` for everything else.
- **`value_num` only for a number that is a quantity.** Age, counts and lengths
  of residence are `COUNT`; "(in thousands)" values are `CURRENCY`. A census
  tract, a county code and a year are digits, not quantities, and stay
  `CATEGORICAL` with no number.

The profile's most-used and most-spent-at stores are emitted as
`FIRST_PARTY_MODEL`, `derivable_from_txns=True`: they are counts over the visits.

## What to look for in a response

The adapter raises a follow-up for each of these shapes when a response shows
it. Each is a property of the format worth reading for, not a claim about any
one response.

**A profile in another name.** The request block prints the name the request
was made in; the profile table prints the registered holder's. On a household
card they can differ, and then the profile, its card's purchases and the
inferred attributes describe the holder or the household rather than the
requester — while the instructions say data belonging to anyone else is left
out. The adapter compares the two first names, ignoring a placeholder, and
raises a `CLARIFICATION` when they differ. It cannot say which is right, and
does not try.

**A history that stops long before the request, with later activity
elsewhere.** The visits table is a card's and the points table a household's,
so the report can date household activity after the card's last purchase. The
adapter states the gap and asks for purchases under another card, phone number
or online account.

**Nothing digital.** The format has no place for email, app, clipped offers,
online orders or advertising data — where Kroger disclosed an advertising blob.
The instructions name what was withheld; the adapter quotes that paragraph in a
follow-up.

## Disclosure grading

- **`SPECIFIC_PIECES` = PARTIAL.** Rich — line items with quantities — but no
  shelf price, tender, channel or digital data is present, and the window may
  stop short of the request. The evidence counts what there was; the notes say
  only what was observed missing in the files actually read, and the coverage
  distance where there is one.
- **`CATEGORIES_COLLECTED` = PARTIAL.** The guide says its headings stand for the
  categories of data collected; the adapter quotes that sentence as evidence.
  They name tables, not the statutory categories, and none covers the inferred
  attributes. A keyword match never earns more than PARTIAL, and this is one.
- **The other six = ABSENT.** The prose is a guide to the file's layout and a
  list of exclusions; it addresses no source, purpose, recipient, sale, share
  or retention.
- **Without the report, only `SPECIFIC_PIECES` is graded.** Every other category
  is decided by the report's prose, so a workbook uploaded alone leaves them
  ungraded — no row, which Compliance shows as "not assessed" — rather than
  ABSENT, which would blame the retailer for a file nobody uploaded.

## Failure modes and what the adapter does about them

| Input | Behaviour |
|---|---|
| Report alone | Profile, identifiers, disclosures; a warning that the history is missing. |
| Workbook alone | Baskets and inferences; the prose-dependent categories left ungraded, and a warning saying so. |
| A line with an unreadable or impossible date, or no TXN_ID | That line skipped, warning names the cell. |
| An amount that is junk or `NaN` | Line kept with no amount, warning names the cell. |
| One quirk on every line | The first few warnings name their cells; the rest are one warning with a count. |
| A department-key or adjustment ring | Kept as a line with no product code, so unrelated rings are not one "product". |
| A report section it does not know | Info naming it. Lines in a table it cannot read as fields are counted. |
| A sheet it does not know | Info naming it. |
| A workbook whose `NET` column is renamed | Not claimed. Loud, rather than reading the wrong column. |
| A broken or encrypted `.xlsx` | `ExtractionError` from the reader, with what to do. |
| Nothing recognisable | `AdapterError`, written for the person who uploaded it. |

## The `.xlsx` reader

`extraction.read_workbook`, new with this adapter, stdlib only (`zipfile` +
`ElementTree`); no new dependency. It returns the same `Table`s the
SpreadsheetML reader does. Cells are placed by their own `r=` reference; a
merged region needs nothing because its covered cells are written empty; the
shared-string table is read after the sheets, only as far as the highest index
needed; `date1904` is carried on the `Table` because a serial means nothing
without its epoch. Numbers come back as the text of `v`, never formatted.

What it refuses, each because it was demonstrated against an earlier version:

- **inflation past 64 MB** across the workbook, and a structural part past 1 MB;
- **a column past XFD**, and **more than two million cell slots** (every row
  charged at least one): a 10 KB file of far-column cells measured 600 MB, and
  ten million empty rows 2.9 GB, before these;
- **any document type declaration**, decided by an expat guard on the prolog
  rather than a text scan — a UTF-16 part and a long leading comment both got
  past the scan. The SpreadsheetML reader uses the same guard;
- **a negative string index**, which Python would read as "the last string";
- **a corrupt deflate stream**, as an `ExtractionError` that costs the one file
  rather than the upload.

Every element at row depth or above is detached from the tree once it has been
read, and everything beneath a row is counted against a per-row element budget
and a nesting limit, so memory follows one row rather than the sheet. A part
declaring an encoding expat cannot decode is refused as one file, not the
upload.

## The synthetic fixture

`fixtures/synthetic_report.txt` is the report **as pdfplumber extracts it**, and
is committed. The workbook is built on demand by `build_workbook(seed)` and is
**not committed**: an `.xlsx` is a zip the scanner cannot read, and
`tests/test_scan_pii.py` names `fixtures/response.xlsx` here as a finding. Tests
build it where they use it.

Both come from one model, so the report's card, household, dates and stores
match the workbook's. It reproduces every structural shape above — doubled
bold, two-pair request lines, the page break mid-profile, split preference
cells, `I/ss` — and, to exercise the follow-ups, a requester whose first name
differs from the holder's and points and preferences dated after the last
purchase; and in the workbook the merged title, header on row 3, serial
dates, numeric identifiers, float artefacts, E-notation, fractional cents,
`QTY` 0 markdowns, zero-value coupons, weighed lines and one-digit
department-key UPCs.

Scale is a fixture's, not a response's: under two hundred baskets over four and
a half years, 26 attributes. **A fabricated date must not coincide with a real
visit**: the denylist built from a maintainer's own response lists every visit
date, and `make check-pii` fails on a fixture date that matches one.

Every 13–19 digit run in the report fails Luhn, and the generator checks its own
output against the scanner's card rule; the alternate and preference ids are
built from the household id with a prefix, as the format builds them, so the
household id is chosen for all three to fail together.

### Proportions

The first fixture was structurally faithful and distributionally implausible —
few weighed lines, short baskets, a spread markdown in one basket in eight —
which is how Prices came to look unusable on the fixture while a real response
priced most of its products. The composer is now tuned to round, invented
targets for a plausible grocery history, recorded as `TARGETS` in the generator
and held there by `tests/test_fixture_shape.py::TestAlbertsonsShape`: a handful
of lines a trip with a long tail, most lines bought singly, weighed produce in
about half the baskets, and markdowns, coupons and spread markdowns rare. They
describe no household.

The store shares are tested as an average over ten seeds rather than on the
committed one: baskets are heavy-tailed, so a few long ones move one seed's
line share by several points (a standard deviation near 0.04 across 40 seeds).
Weighed lines are rounded to the cent, as the register does, so a fraction of a
cent comes only from a spread markdown.

`make fixtures-check` regenerates the report and fails on any difference.
