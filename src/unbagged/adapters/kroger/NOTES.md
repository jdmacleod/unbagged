# Kroger response format

What one real CCPA response actually looked like, and what the synthetic fixture
reproduces. **No values from the real report appear here or anywhere in this
repository** — only structure. Where a shape is described, it was observed once;
treat every claim below as "seen in one report", not as a documented contract.

> **Revised after reading a real response.** An earlier version of this file
> described the format second-hand, and three of the four blobs turned out to be
> shaped differently. Everything below has now been checked against an actual
> report. Where the two disagreed, the differences are called out, because the
> earlier shapes are still read as fallbacks and a future report may use them.

## Delivery

A single PDF. The text layer is prose interrupted by four pretty-printed JSON
blobs. There is no attachment, no CSV, and no machine-readable envelope: the JSON
is embedded in the document text and has to be recovered from it.

## The page-number quirk

Bare page-number lines are interleaved into the text, including in the middle of
the JSON blobs, so the JSON does not parse as extracted:

```json
      "emailAddress": "someone@example.com",
  3
      "subscriptionStatus": "Unsubscribed"
```

Strip them before parsing:

```python
re.sub(r"\n\s*\d{1,3}\r?\n", "\n", text)
```

### The JSON is not valid JSON

Two separate reasons, both seen in one report, both handled by
`unbagged.jsonscan.repair`:

- **A newline inside a string.** PDF text extraction wraps long lines, and the
  advertising section has label keys long enough to wrap. A literal newline
  inside a JSON string is not legal, and the section simply failed to parse.
- **A trailing comma** before a closing brace. The identity blob has one.

Between them these cost two of the six blobs until the repair pass existed. The
repair is deliberately narrow: joining a wrapped line and dropping a trailing
comma are both reversals of a known corruption. Anything cleverer starts guessing
at what the retailer meant, and a parser that invents structure is worse than one
that reports it could not read a section.

**Hazard:** the documented page-strip pattern also eats a JSON line consisting
only of a number, which
is what a pretty-printed array of bare numbers looks like. No such array was
observed, and the generator deliberately produces none, but any future export
containing one would silently lose an element.

`reader.strip_page_markers` therefore does not use the naive form. Matching the
shape is only the first step: a candidate counts as a page marker only if it
belongs to the document's longest run of numbers increasing by exactly one. A
value sitting in an array forms a run with nothing, so it survives.

The trade-off is that a report printing fewer than two page numbers keeps them in
its text. That is the right way round — two stray lines are recoverable, a
silently deleted value is not.

## Section headers, in the order observed

1. `Section 1: Specific Pieces of Personal Information Collected`
2. `Data we hold related to our Loyalty program:`
3. `Data we hold to communicate and advertise to you in a personalized way:`
4. `Email Information`
5. `Data related to in-store services:` / `Information about your purchases:`

**There is no Section 2, 3 or 4.** The report is numbered as though there were.
Categories of sources, business purposes, third-party recipients, and sale/share
status are all simply absent — which is why the adapter emits an explicit
`ABSENT` disclosure for each rather than leaving a gap. See `docs/legal-basis.md`.

## The four blobs

There are **five**, not four, and two of them share the loyalty header. Position
is therefore not a way to identify a blob; `reader.blob_with_keys` finds them by
shape.

| Follows header | Shape |
|---|---|
| Loyalty program | `{accounts, groups}` — the identity graph |
| Loyalty program | `{loyaltyIdNumber, Convenience, Loyalty, Price, Quality, Variety Seeking}` |
| Personalized advertising | `{Individual: [{...}], Household: [{...}]}` |
| Email Information | `{emailData: [{Name, Value}], smsData, pushData}` |
| Purchases | `{customer: [{subtaskid, loyaltyno, basket[]}]}` |

### Identifiers

```
accounts[].accounts[].loyaltyCards           keyed BY the card number
                     .loyaltyCards[*].altIds, .cardNumberWithCD, .status, .type
                     .personalInfo.name.{firstName, lastName}
accounts[].attributes[]                      {name, value} pairs
groups[]  .type          CG_PERSON | KROGER_HOUSEHOLD
          .aliasIds      cgPersonId | ehhn + householdId
          .metadata      {key: {type, value}} — epsn, address, cgPersonName, …
```

Two things worth noticing.

**Loyalty card numbers are dictionary keys, not values.** A reader that walks
values finds the card's metadata and never the number itself.

**The report types its identity groups**, so it states for itself whether a
record describes a person or a household. That is better evidence than inferring
scope from a field name, which is what the adapter used to do. `KROGER_HOUSEHOLD`
carries the postal address, and an address describes everyone living there — not
only the person who enrolled.

`cardNumberWithCD` is the loyalty number plus a check digit, which makes it a
14-digit run indistinguishable in shape from a payment card. The generator picks
a check digit that deliberately *fails* Luhn, so `tools/scan_pii.py` keeps its
payment-card rule armed inside this fixture directory rather than standing it
down. Real cards stay catchable; the field's shape is unaffected.

Eight identifiers for one shopper, and the report explains none of them. That is
itself worth surfacing: each becomes an `Identity` row so the user can see how
many separate keys exist for them. `householdId` and `ehhn` are household-scoped,
which means they cover people who never enrolled in anything.

### Inferences

Two populations, in two different blobs under two different headers.

**Propensity axes** — Convenience, Loyalty, Price, Quality, Variety Seeking — sit
under the *loyalty* header beside a `loyaltyIdNumber`, not under the advertising
header. They carry prose values, not numbers. Classified `FIRST_PARTY_MODEL`:
they are computable from the baskets in this very report.

**Appended attributes** sit under the advertising header, already grouped by whom
they describe:

| `Individual` | `Household` |
|---|---|
| Age of Individual | Income Predictor Score (in $000) |
| Year of Birth for Individual | Number of Adults / Children / Individuals in Household |
| Education Level of Individual | Presence of Children Ages 0-2 |
| Gender of Individual | Known Presence/Absence of Children Age 0-17 |
| Cat Owner, Dog Owner | |
| Likelihood of: a New/Used Auto, Going on a Cruise, Traveling Domestically, Traveling Internationally | |

The 1–7 scale is stated **in the label** — `(7=Most Likely; 1=Least Likely)` —
rather than as a prefix on the value, so the scale is read from the key.

All classified `APPENDED_THIRD_PARTY`.

The reasoning for that classification, which matters more than the code:
**nothing in a grocery basket tells you someone's education level, how long they
have lived at an address, or whether they will take a cruise.** These attributes
cannot be derived from purchase data; they were bought from somewhere, and the
report does not say from whom. That gap is the single most interesting output
this tool produces, and it is also exactly what `SOURCES` being `ABSENT` means in
practice.

### Purchases (blob 4)

`customer[0].basket[]`, each with `date`, `time`, `division`, `store`, `orderno`,
`total_amount_prior_to_discounts`, `tenders[]`, and `items[]`. Items carry
`purchasedescription`, `productupc`, `retailamt`, `customerloyamt`.

Two things that look like bugs and are not:

- **`UNKNOWN` placeholder rows.** A `purchasedescription` of `"UNKNOWN"` with zero
  amounts and the constant UPC `00010000080000`, <!-- pii-scan: allow placeholder UPC, not an identifier -->
  appearing in most baskets and sometimes more than once. Not a product. Counting
  them inflates every basket's item count, so they are stored (nothing is
  dropped) but flagged.
- **Negative `retailamt`.** Returns and voids. Real, and never filtered — a
  filtered return makes the spend total wrong in the user's favour, which is the
  wrong direction to be wrong in.

## Judgment calls the adapter makes

These are decisions, not readings. Each one could have gone the other way, and a
future maintainer should be able to see why it did not.

**Timestamps carry no timezone.** The report gives `date` and `time` as a
store-local wall clock with no zone. The adapter stores `YYYY-MM-DDTHH:MM:SS`
with no `Z`, because stamping UTC on a 19:30 California purchase would move it to
the following day in every view. Sorting is unaffected. The canonical schema says
timestamps are UTC; this is the documented exception, and it is honest about what
the retailer actually disclosed.

**Channel is left `NULL`.** There is no channel field in the response. Defaulting
to `in_store` would be a claim the data does not support — and it would hide the
fact that Kroger holds online order data it did not disclose here.

**Split tenders are joined.** A basket with two `tenders[]` entries becomes
`"CREDIT + GIFT CARD"` rather than just the first. Dropping the second would
quietly rewrite the receipt.

**Cat Owner and Dog Owner are appended but derivable.** Classified
`APPENDED_THIRD_PARTY` like the rest of the block, but flagged
`derivable_from_txns=True`: pet food appears as line items in these very baskets.
The two fields answer different questions — where a value most likely came from,
and whether the retailer *could* have worked it out from what it already had.

Their values are small integers rather than flags, and the report does not say
what the scale is. Stored as `COUNT` with the raw value intact, because guessing
would be worse than admitting the label is undocumented.

**An online-shopping likelihood, if one appears, is derivable-unknown.** Kroger
holds its own online order records, but the response discloses no channel field,
so from the data provided the question cannot be answered — `NULL`, not `False`.
No such attribute appeared in the report that was read, and the fixture does not
invent one; `_derivable` is unit-tested directly instead. The tri-state exists
for exactly this case.

**The address is household-scoped**, and the report agrees: it lives on the
`KROGER_HOUSEHOLD` group.

**Dates are not dates.** The `date` field arrives as `"08/17/2024 00:00:00"` — a
US-format date with a zeroed time welded on — while the real clock sits in a
separate `time` field. Concatenating the two produces something that is not a
timestamp and does not sort, which put the timeline in arbitrary order until it
was fixed. Both halves are parsed.

**Amounts arrive as strings.** `"12.34"`, not `12.34`. Coerced, and left `NULL`
when they cannot be.

## Coverage window

24 months, stated in the prose. The report directs the requester to email the
privacy office separately for data back to 2022, which the adapter records as a
`supplemental_period` follow-up rather than as a compliance failure — see the
lookback note in `docs/legal-basis.md`.

## Failure modes and what the adapter does about them

| Input | Behaviour |
|---|---|
| File truncated mid-blob | Everything before the cut is kept. One ERROR warning naming the page, and saying the data is missing from the *file*, not from the retailer. |
| One basket malformed | That basket is skipped with a warning; the others parse. |
| Basket has no date | Skipped — an undated transaction cannot appear on a timeline. |
| `items` is not a list | The basket survives with no line items, and says so. |
| Amount is `"$1,234.56"` or `""` or junk | Coerced where possible, `NULL` otherwise. Never guessed. |
| One JSON blob is corrupt | The other three still parse. |
| Prose letter, no data at all | Parses to an empty result with all eight disclosures recorded. A retailer that sends no data is itself a finding, not an exception. |
| Nothing readable in the bundle | `AdapterError` with a message written for the person who uploaded it, mentioning the scanned-PDF case. |

The distinction that matters in the truncation message: a short file is the
uploader's problem to re-download, while a short *response* is the retailer's
problem to answer for. Conflating them would put a compliance finding in front of
someone whose download just failed.

## The synthetic fixture

`fixtures/synthetic_report.txt`, produced by `fixtures/generate.py` and
regenerated by `make fixtures`. It reproduces every quirk above: the header
sequence, the four blobs, mid-JSON page numbers, the placeholder rows, the
negative amounts, and the missing sections.

Scale differs from the reference report and that is deliberate — the fixture is
seeded from structure, never from values. It currently yields roughly 122
baskets, 1,300 line items and 226 distinct UPCs across 24 months, against 54
baskets and 790 line items in the report that was read. Prices carry a
small annual drift so the price-history view shows a series rather than noise.

`make fixtures-check` regenerates it and fails on any difference. That check is
load-bearing: `tools/scan_pii.py` stands a few address-shaped rules down inside
generated fixture directories, and byte-identical regeneration is what replaces
them. A real report dropped in here does not reproduce from the seed.
