# H Mart response format

What one real response actually looked like, and what the synthetic fixture
reproduces. **No values from the real response appear here or anywhere in this
repository** — only structure. Every claim below was observed **once**. Treat
each as "seen in one response", never as a documented contract.

> **This file previously said no response had been seen, and told the next
> person not to write a speculative adapter.** That was the right advice and it
> held: everything here is measured from an actual response, and the shapes that
> earlier guess predicted — a PDF letter, or no reply at all — were both wrong.

## Delivery

A single file with an `.xls` extension that is **not an Excel workbook**. It is
SpreadsheetML 2003: XML in the `urn:schemas-microsoft-com:office:spreadsheet`
namespace, of the kind a Java web application emits with a
`application/vnd.ms-excel` content type. One worksheet, one table, five columns.

The whole document is **a single line**, 36 KB, with no newlines anywhere.

Element order is `DocumentProperties`, `ExcelWorkbook`, `Styles`, then
`Worksheet`. That matters more than it looks: the header row is the only thing
distinguishing this export from any other spreadsheet, and everything before the
worksheet stands between a reader and it, with no stated size limit.

## What it contains

| Column | Shape |
|---|---|
| `Smartcard` | 11 digits, identical on every row, and also in the filename |
| `Date of Purchase` | `YYYY-MM-DD HH:MM:SS.0` — Java `Timestamp.toString()` |
| `Branch` | a store **name**, not a code |
| `Amount` | a decimal, usually but not always two places |
| `Point` | an integer |

**Every cell is `ss:Type="String"`.** Dates, amounts and points included; nothing
in the document is typed as a number or a date.

Measured across the response, as counts and ratios only:

- 67 rows, sorted ascending by timestamp, no repeated (timestamp, branch) pair
- a 79-month span, of which 49 months carry a purchase; the busiest carries 5
- seconds are `00` on 67 of 67 rows, so the real resolution is minutes
- no negative amounts, no zero amounts, no blank cells
- **`Point == round(Amount)` on 67 of 67 rows.** Not floor (24/67), not ceil
  (43/67)
- two distinct branches, one dominant

## What it does not contain

No line items. No UPC, description, quantity, tender type, order number, channel
or loyalty price. **No prose of any kind** — no headings, no sections, nothing
addressing any of the eight disclosure categories.

## The finding

A right-to-know request was filed and this file is the reply. The artifact has
the shape of a self-service loyalty points statement, and it was sent in answer
to a statutory access request. **That is the finding, and it is a stronger one
than a missing section would be:** the response says what each visit cost and
never what was in it.

An earlier draft of the plan argued the opposite — that the file might be a
self-service download and so should not be graded against the statute. Confirmed
with the requester on 2026-09-09 that a request was filed, which withdrew that
reading entirely.

Two things the retailer publishes and the response omits:

- **The points formula.** "$1 of purchase (excluding tax) equals 1 point",
  which matches `Point == round(Amount)` and settles what `Amount` is: a pre-tax
  purchase amount. The programme accrues points toward a gift certificate rather
  than discounting at the till, so **no loyalty discount is ever applied to this
  figure** and the pre-versus-post-discount question does not arise.
- **A retention rule.** Withdrawal after three years of inactivity, information
  kept a year afterwards. Nothing in the response says so, which sharpens
  `RETENTION_PERIOD = ABSENT`: the policy exists and was not given when asked.

Also published, and also absent from the response: **one card per household**.
That is why `Identity.scope` is `None` rather than `INDIVIDUAL` — see below.

## Judgment calls the adapter makes

These are decisions, not readings. Each could have gone the other way.

**`SPECIFIC_PIECES` is `PARTIAL`.** The response did disclose specific pieces of
personal information — a card number, and every visit's date, store and total —
so `ABSENT` would be false. It disclosed nothing about the contents of any
basket, so `PROVIDED` would be false too, and `PROVIDED` is the cell a
compliance reader weighs most heavily. `PARTIAL` is the only one of the three
that is true. Everything else is `ABSENT`; there is no prose to read.

**The card's scope is `None`, not `INDIVIDUAL`.** The response never says who a
card belongs to, and the published terms say one card per household, which
points the other way. Recording `INDIVIDUAL` would state something the response
did not. The frontend's margin note used to claim "all individual" whenever
nothing was household-scoped; that was corrected in the same work.

**`page` is `None` on every record.** A spreadsheet has no printed pages and
inventing a `1` would cite one that does not exist. The locator carries the
weight instead, as an A1 cell reference — `Workbook!D14` — which is what a
person sees when they open the file.

**`Branch` goes in `store_code` and is stored verbatim.** It is a store name in
a field named for a code, which is the closest available home; the mismatch is
documented rather than papered over. It is set in the interface sans rather than
the mono numeral face, because a place name is not a numeral.

**`Amount` goes in `total_pre_discount`.** The field name says "pre discount" for
a response that discloses no discount. With no discount mechanism in the
programme at all, the stated total is simultaneously the pre-discount figure and
the amount paid, so the two readings do not diverge here.

**`Point` has no destination.** The model has no field for it, and it is not an
attribute about a person, so an `Inference` would be the wrong shape. It is a
disclosed column deliberately not stored. Recorded as
[#36](https://github.com/jdmacleod/unbagged/issues/36) if it is ever wanted.

**No `TxnItem` is emitted.** Fabricating one line per basket to carry the total
would invent structure the retailer never sent.

**`channel` is `NULL`.** There is no channel field. Defaulting to `in_store`
would be a claim the data does not support. Same call the Kroger adapter makes.

**Timestamps carry no timezone.** Stored as `YYYY-MM-DDTHH:MM:SS` with no `Z`,
a store-local wall clock, because stamping UTC on an evening shop would move it
to the next day in every view. The canonical schema says timestamps are UTC;
this is the documented exception, as it is for Kroger.

## Hazards, and what the reader does about them

**`ss:Index` shifts columns, and matching headers by name does not save you.**
SpreadsheetML omits empty cells and marks the next present one with `ss:Index`
naming its real column. A reader that appends cells in encounter order moves
every value after the gap one column left. Measured on a constructed sparse row:
the amount landed in the store field and the basket total dropped to its rounded
points value — a $12.34 basket recording as $12.00, with no error anywhere.

The header row stays dense and matches perfectly, so header-name mapping is no
defence: the damage is in the data rows underneath. `ss:Index` also appears on
`ss:Row`, where it corrupts the row number instead — which is half of the
locator. Both are honoured, and `ExpandedRowCount` / `ExpandedColumnCount` are
used as a free cross-check.

The real response contains no sparse rows. **The fixture generates them anyway**:
a fixture that cannot produce a shape cannot test the code that handles it.

**A short row is defined in the header's columns, not as a count.** After
placement, a row is short when a column the header named holds nothing. A count
of five would be an `n=1` constant and would mistake a legal sparse row for a
broken one.

**The bounded read needs a byte budget, not just a row limit.** Deciding which
adapter owns an upload must not cost a full parse, but a prefix is not enough:
the preamble stands in front of the header with no stated size limit. The budget
is 256 KB, roughly seven times an entire observed export. When it is spent
before a header row is reached, `sniff()` returns a **reason** rather than a bare
zero — "I did not look far enough" and "this is not that format" are different
answers and both score 0.0.

**Rows are cleared as they are read.** Without it the pull parser retains the
whole tree: 594 MB on a 66.8 MB document, against 661 MB for parsing it
outright. Cleared, 16.8 MB.

**A `start` event does not carry cell text.** Rows are read on `end`. The
worksheet's name is an attribute, so it is taken on `start` — by the time its
`end` arrives, every row it should have labelled has gone past.

**A truncated file needs `close()`.** Feeding a document that stops half way
raises nothing: it is well-formed up to where it ends. Only closing the parser
asks whether the tree finished. That is what tells a failed download apart from
a file this reader cannot handle, and the two need different advice.

## Failure modes and what the adapter does about them

| Input | Behaviour |
|---|---|
| A real binary Excel workbook | Refused by content, with a message naming Save As → XML Spreadsheet 2003 |
| Truncated mid-document | Refused, telling the uploader to download it again |
| A document type declaration | Refused before any XML is parsed |
| Preamble larger than the budget | `sniff()` returns 0.0 **with a reason** |
| A header column renamed or missing | The adapter declines and the response falls to the generic fallback |
| A sparse row | Cells placed at their declared columns; the missing one is `None` and noted |
| A row with no readable date | Skipped with a warning — an undated visit cannot go on a timeline |
| A row with no readable amount | Kept, with no total, and a warning |
| A sheet with a header and no data rows | An empty result and a warning, never an error. An export with nothing in it is a finding about the response |
| Nothing readable in the bundle | `AdapterError` written for the person who uploaded it |

## The synthetic fixture

`fixtures/synthetic_history.xls`, produced by `fixtures/generate.py` and
regenerated by `make fixtures`. It reproduces the envelope, the element order,
the banner row, the string-typed cells, the Java timestamp, the stripped
trailing zero, the `Point == round(Amount)` relationship, several branches
unevenly weighted, months with no purchase at all, sparse rows and a row-number
gap.

**Scale differs from the reference response deliberately** — 108 rows across a
104-month window, against 67 across 79 months — so the fixture cannot be
mistaken for a reproduction of a real response.

### Two deliberate departures, and why

**The fixture is newline-separated; the real export is one line.** Not because
`scan_pii.py` cannot cope — it masks matches and prints `path:line`, so it
reports fine. The real costs of a single-line fixture are that every finding
reports line 1, and that an inline `pii-scan: allow` suppression becomes
impossible, since the marker must sit on the offending line or the one above it.
Because the fixture is unfaithful in exactly the dimension a chunk-fed read
depends on, `tests/test_hmart_adapter.py` feeds the reader the newline-free form
as well.

**The generated card fails a Luhn check by construction.** At the observed width
of 11 digits it matches neither scanner rule — `PAYMENT_CARD` wants 13-19 digits
and `LOYALTY_NUMBER` 12-14 — so no Luhn check is reached today. The constraint
exists because the scale is meant to differ from the reference file, which makes
a width change plausible, and `PAYMENT_CARD` stays armed inside a generated
fixtures directory while `LOYALTY_NUMBER` stands down.

### One caveat worth carrying

Python's `round()` is half-even; a Java portal is near-certainly half-up. At 67
rows a half-cent case is unlikely to have occurred, so a single response cannot
distinguish them. The generator and the fixture both use Python's `round()`, so
a test asserting that relationship would be self-consistent and would prove
nothing about the format. Nothing asserts it. It matters more if the deferred
`FIRST_PARTY_MODEL` inference is ever built.
