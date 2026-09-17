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

**`ss:MergeAcross` shifts columns the same way, and the real export has one.**
A merged cell occupies the columns it spans, and the columns it swallows are
not written. The next `ss:Cell` element is therefore the one after the span,
not the one after this element: a cell with `ss:MergeAcross="1"` sits in
columns 1 and 2, and the element following it is column 3. The banner row
merges across four, so this attribute is in every response this retailer
sends. Honoured alongside `ss:Index`, which still wins when both appear
because `ss:Index` is written against real columns.

In the observed file the merge costs nothing — nothing follows the banner cell,
so there is no value to displace. It is fixed because the damage when something
does follow is the damage this whole reader exists to prevent: measured on a
constructed row, a merged `Branch` put the points value in the amount column
and a $12.34 basket recorded as $12.00, silently. `tests/test_merged_cells.py`
holds that case.

**`ss:MergeDown` is not honoured, and that gap is open.** It swallows a column
in the rows *beneath* the cell, so those rows omit it and everything after it
in them shifts left — the same corruption across rows instead of along one.
Handling it means carrying spans between rows, which `read_tables` has no place
for today. The observed export contains none. Filed rather than guessed at.

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
| A merged cell | The columns it spans are skipped, so the next cell lands after them |
| A row with no readable date | Skipped with a warning — an undated visit cannot go on a timeline |
| A row with no readable amount | Kept, with no total, and a warning |
| A sheet with a header and no data rows | An empty result and a warning, never an error. An export with nothing in it is a finding about the response |
| Nothing readable in the bundle | `AdapterError` written for the person who uploaded it |

## The synthetic fixture

`fixtures/synthetic_history.xls`, produced by `fixtures/generate.py` and
regenerated by `make fixtures`. It reproduces the envelope, the element order,
the banner row **as the export actually writes it** — merged across the full
width, its text inside `html:B` / `html:U` / `html:Font`, and an `ss:NamedCell`
sibling after the `ss:Data` — the `ss:StyleID` on every cell, the `ss:Column`
widths ahead of the rows, the worksheet's `ss:Names` and `x:WorksheetOptions`
siblings, the string-typed cells, the Java timestamp, the stripped trailing
zero, the `Point == round(Amount)` relationship, several branches unevenly
weighted, months with no purchase at all, sparse rows and a row-number gap.

The banner was a plain single cell until 2026-09-09, which is why nothing here
noticed that `ss:MergeAcross` was being ignored. It still cannot catch that on
its own — nothing follows the banner cell, so there is nothing to displace —
and `tests/test_merged_cells.py` carries the displacement case instead. The
style *bodies* — `ss:Font`, `ss:Border`, `ss:Interior`, `ss:NumberFormat` — are
left out on purpose: nothing reads them and they cannot move a value.

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

## The second response: screen captures of the receipt viewer

A later reply added 46 PNG screen captures, one or two per store visit, covering
44 of the 67 visits the points statement lists. All of 2020 and 22 other visits
are still totals only.

### What a capture looks like

A white page, **519 to 542 pixels wide** — the width varies between captures,
which is the first thing to know, because a crop measured off one of them reads
nothing at all off another. Drawn in three inks: `#002D8C` for descriptions,
`#B55D00` for amounts, black for the header and the footer. Hairline rules
separate the header, the body, the tender line and the timestamp.

Three columns, and the line pitch is about 20px:

| column | x | holds |
|---|---|---|
| flag | 6-98 | `WT`, `CL`, `***`, and full-width `2.50 lb @ 1.50 / lb` lines |
| description | ~104 | product names, `SC - …` discount lines, `TAX`, `BALANCE`, the tender |
| amount | right-aligned, ~12px from the edge | `$ 12.48`, `$ -3.75` |

In order: `Customer ID: <smartcard>`, the purchase lines, `TAX`, `*** BALANCE`,
the tender line, a stamp `2019-03-04 11:07:00  2  118  0042`, then a card block.

### How often the printed timestamp is actually readable

Not often. Measured through the shipped reader on the real corpus, **15 of 43
visits could not be placed by their printed timestamp at all** and fell back to
the date in the capture's filename together with the basket's own total. An
early prototype suggested five; it read the stamp from a different crop, and the
figure did not survive contact with the code that ships.

Each fallback now raises an INFO warning naming the capture. "Matched on the
printed timestamp" and "matched on a filename and an amount" are different
claims, and a reader auditing this archive cannot otherwise tell which visits
rest on the weaker one. Both facts come from the response and neither is a
guess — but a third of the itemised visits resting on the second is worth
knowing.

### The three reconciliations

These are the whole reason reading these by machine is defensible.

1. **The stamp matches `Date of Purchase` to the second.** Every capture's date
   appears in the points statement.
2. **`sum(lines) + TAX == BALANCE`**, printed on the receipt itself.
3. **`sum(lines) ==` the statement's `Amount`.** The statement reports a visit's
   **pre-tax subtotal**, not what was paid. Three visits looked like
   disagreements until that was worked out, by 0.20, 0.70 and 1.26 — each
   exactly that receipt's own tax line. `Point` is the subtotal rounded.

### What is read by arithmetic or geometry rather than by reading words

The type is 10px and the engine is not reliable on it. Everything structural is
therefore decided some other way, and the decisions are worth keeping:

- **Where a receipt ends.** A finished capture leaves 44 to 207 pixels below its
  last amount; the two that are the top half of a taller receipt leave 3 and 4,
  pressed against the edge the screen cut. Looking for the word `BALANCE`
  instead fails on a seventh of the corpus — it comes back as `BALANGE`, `ANE
  wu`, `Serr`, `x` — and looking for the timestamp fails on a third.
- **Which line is the total.** The tender repeats it, so the last two equal
  amounts are the balance and its echo, and tax is the line above them.
- **How much of a seam between two captures is a repeat.** One repeated line is
  either the overlap or the same product scanned twice. Both are ordinary, and
  guessing either invents a line or loses one that was paid for, so every
  plausible overlap is tried and the one that reconciles is taken.
- **A capture with two products at the same price is not a receipt.** One head
  capture ends on two lines at 2.99, which reads positionally as a balance and
  its tender echo. It was taken as complete, its other half was never joined,
  and the visit split in two.

### Hazards

- **A dozen captures carry a freehand scrawl** drawn across them in pure
  `#0000FF`. The page itself never uses that colour, so it is masked back to
  white before the engine sees it. Unmasked, one capture read 14 lines and came
  up short of its stated total; masked, 16 lines reading exactly to it.


- **One capture is clipped at the source**, and the earlier note here was wrong
  about what that means. It is the narrowest page in the corpus and its amount
  column runs into the right edge, so the last digit of every amount is cut —
  but cut THROUGH, not cut off. Enough of each glyph survives that a person
  reads the page without difficulty.

  That is worse than losing the digit outright, and it fails in two directions
  at once. Where the surviving sliver resolves to another digit the engine
  returns a plausible wrong number and says nothing: five amounts on that page
  came back altered, one of them by four cents. Where it resolves to no digit
  the amount stops matching at all and the whole row is dropped, which on that
  page took the BALANCE line with it — so the total that would have caught the
  other five was gone too.

  A vision model reads the page correctly, every clipped digit included. What
  stopped it being used was not the model: the gate needs a figure the reader
  had no hand in, and the clip had taken the only one on the page.

  The points statement looks like the missing figure, and it was tried. It does
  not work, and the way it fails is worth recording because it will be proposed
  again.

  With `balance` set to `anchor + tax`, `foots()` reduces to `subtotal ==
  anchor` — the same comparison `_disagrees` makes a moment later. One
  constraint, one scalar, wearing two hats. Four rounds of adversarial review
  each found a way through, and each fix opened the next:

  | Round | What was stored |
  |---|---|
  | 1 | one invented line worth exactly the statement's total |
  | 2 | anything at all, on a page the engine read nothing off |
  | 3 | one invented line, on a page whose only legible figure the model's own tax matched |
  | 4 | one invented line of 4,999 behind ten genuine matches of a tenth each |

  Round 4 is the one that settles it. Every candidate second fact here comes
  from the engine's own partial reading, which is weak by construction — a clip
  is the whole reason this route was wanted — and is influenced by whoever
  supplied the capture, who also supplied the statement the figure comes from.
  A floor counting matched LINES says nothing about matched VALUE, and bounding
  the answer cannot manufacture a measurement that is not there.

  So a page with no printed total stores nothing. The visit keeps the figure the
  statement gave it, which no reading of the picture can move, and the warning
  names the capture and the reason. Recovering the basket needs a capture that
  is not clipped, which means asking the retailer again.

  `_clipped` is the physical signal, and it measures the amount column only —
  the stamp line ends in a run of digits that can sit against the edge of a
  page whose amounts are well clear of it. Measured on amount ink: the gap is
  0px on that capture and 4px to 24px on the other 45.

- **Two captures sharing a date may be two visits.** One pair in the corpus is
  two separate trips on one day, at different lanes, for different amounts.
  Their filenames are indistinguishable from a continuation pair; only reading
  them tells the two apart.
- **The stamp's first field is a lane, not a store.** The same values appear
  against both of the branches in the corpus, so it cannot be one. The branch
  comes from the statement, which names it; a capture never does.
- **The gate has exactly two inputs, and both must come off the page.** It is
  `sum(lines) + tax - balance`, so every figure in it that a reader also
  authored is a free variable that reader can solve for. Pinning the balance
  alone left `tax`: an answer can quote the printed total back correctly, since
  it reads the same pixels, and let the tax absorb whatever the basket was
  inflated by. Measured on a synthetic page printing 20.00 — lines of 1000.00
  and 250.00 with a tax of -1230.00 reconciled and were stored. Where the TAX
  line itself is illegible, a bound of `0 <= tax <= balance` is what stands in:
  it holds the subtotal inside `[0, balance]`, so a basket can never claim more
  than the receipt says was paid.


### Which vision model, and the evidence for it

Measured 2026-09-16 against Ollama 0.33.3 on one host, by
`python -m tools.bakeoff_vision`. Twelve vision-capable models, six synthetic
pages each, 144 calls, two tiers: **A** through the lane as it ships — the real
prompt, the real schema, `from_reply` then `foots` — and **B** the same call with
the schema left off and nothing else changed.

Tier B goes through `ollama.ask` with `schema=None` rather than a transport of
its own. The first run of this table did use its own, and it cost a real
measurement: `qwen3-vl:30b` scored one page as no-answer that the lane's retry
recovers. A tier whose entire output is the gap between it and Tier A cannot
afford a second difference in it.

Nothing here was measured on a real capture and nothing here can be. The pages
come from `tools/bakeoff_cases.py`, which draws them from rows written in source,
so the ground truth is the code and a score can be published.

**`gate` is out of five**, not six: the cut-off page is the top half of a taller
receipt and prints no total, so there is nothing for the gate to check. **Four
is the ceiling** — the clipped page is refused by design, see the hazard above —
and three models reach it.

| Model | gate | mean s | strict | furniture | note |
|---|---|---|---|---|---|
| `minicpm-v4.5:8b` | **4/5** | **8.2** | 1.00 | 5 | fastest of the three that clear the hazards |
| `qwen3-vl:8b` | 4/5 | 11.4 | 1.00 | 15 | same reading, half again the time, three times the furniture |
| `qwen3-vl:30b` | 4/5 | 18.9 | 1.00 | 10 | no better than its 8b sibling and twice the cost |
| `gemma3:4b` | 3/5 | 7.0 | 1.00 | 4 | best recall of any model (0.94); loses the scrawl |
| `mistral-small3.2` | 3/5 | 27.1 | 1.00 | 3 | reads well, loses the scrawl, slow |
| `gemma3:12b` | 3/4 | 40.3 | 0.83 | 6 | one page hit the 180s timeout outright |
| `qwen3.8:27b` | 2/5 | 28.7 | 0.50 | 0 | loses the scrawl and half its amounts to the schema |
| `gemma4:e4b` | 1/5 | 6.2 | 1.00 | 0 | fast and reads a third of the lines |
| `glm-ocr` | 1/5 | 3.2 | 0.68 | 15 | fastest thing here; returns amounts in Chinese numerals |
| `gemma4:12b` | 0/5 | 10.0 | 1.00 | 0 | quotes the total back and drops lines — what `foots` is for |
| `qwen3.6:27b` | 0/5 | 37.3 | 0.12 | 0 | reads 0.90 of the lines and gates on none of them |
| `qwen3.5:9b` | 0/5 | 4.4 | 0.17 | 0 | reads 0.17 under the schema, 0.77 without it |

`strict` is the fraction of returned amounts `receipt._decimal` accepts, scored
apart from whether the model read the digits. That separation is the whole
reason the tier's first failure went unseen for as long as it did.

#### What the two tiers actually told us

**The schema is a reader, not just a validator.** The bottom two rows read the
page perfectly well and score nothing through the lane. `qwen3.6:27b` returns
0.90 of the lines and wraps every amount in `{"value": …}`, so `_decimal`
accepts 12% of them; with the schema removed it gates on three pages at 0.92
recall and 1.00 strict. `qwen3.5:9b` goes from 0.17 to 0.77 the same way.
Screening on Tier A alone would have thrown both away as weak readers, and they
are not weak readers.

It runs the other way too, and harder than expected — worth knowing before
anyone proposes dropping the schema. `glm-ocr` answers in 3.2s with it and
rambles past the timeout without it, twice. `qwen3-vl:30b` goes from 18.9s to
93.0s and `qwen3-vl:8b` from 11.4s to 68.2s. Constrained decoding is not only a
correctness device here; it is most of the speed.

**`foots` is load-bearing, not a formality.** `gemma4:12b` returns the printed
total correctly on every page and still gates on none: it quotes the total back
and loses a line on the way, which nothing in the reply itself can show. The
arithmetic is what catches it.

**The clipped page behaves exactly as the hazard above says it must.** Every
model that answers returns the right total for it and none passes the gate,
because the clip alters the lines rather than the total. No model reads its way
out of that, which is the measurement behind "recovering the basket needs a
capture that is not clipped".

**The scrawl is the discriminator among competent models.** `gemma3:4b` has the
best recall in the table and loses the scrawl page; so does `mistral-small3.2`.
A dozen real captures carry one, so it is not an exotic case.

#### What this did not settle

The shipped default, `qwen2.5vl:7b`, **is not in this table because the host
does not have it**. It was never measured against anything, which is what the
bake-off was for; what is recorded here is the field it is being replaced by,
not a comparison with it.

Everything above is one host, one day, one quantisation of each tag. A re-pulled
tag can be a different build — `gemma3:12b` hitting a 180s timeout on one page
is the kind of result that may not reproduce. Re-run the bake-off rather than
trusting this table after a model is re-pulled.


### Deliberately not read

- **The flag column.** `WT`, `CL` and `***` are two glyphs of 10px type and come
  back as `wr`, `week`, `nee`, `ANE`, `AK`. A line's meaning is carried by its
  description and the sign of its amount, both of which read exactly, so reading
  the flag would buy a way to be wrong for no capability.
- **The card block.** The last four digits, the expiry, the approval code and
  the host code are all on the page. None is transcribed, none is stored.
- **Tax.** It reconciles the receipt, and the schema has nowhere to put it.
  `total_pre_discount` stays the statement's pre-tax subtotal, which is what the
  lines sum to, so the timeline's own footing check reports no difference. A
  line for it would put a row in the basket the receipt never had.

### What still is not disclosed

`SPECIFIC_PIECES` stays PARTIAL. Two thirds of the visits are itemised, none of
2020 is, no UPC appears anywhere, and no other category is addressed at all.
`docs/legal-basis.md` is direct about which way that resolves: a category is
never upgraded on inference, and the grade describes the response rather than
the effort behind it.
