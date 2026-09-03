# Kroger response format

What one real CCPA response actually looked like, and what the synthetic fixture
reproduces. **No values from the real report appear here or anywhere in this
repository** — only structure. Where a shape is described, it was observed once;
treat every claim below as "seen in one report", not as a documented contract.

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

**Hazard:** that pattern also eats a JSON line consisting only of a number, which
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

| # | Follows header | Shape |
|---|---|---|
| 1 | Loyalty program | `customer[0]` with the identifier set below |
| 2 | Personalized advertising | `customer[0].propensities` / `.demographics` / `.likelihoods` |
| 3 | Email Information | `customer[0].emailActivity[]` |
| 4 | Purchases | `customer[0].basket[]` |

### Identifiers (blob 1)

`loyaltyno`, `cardNumberWithCD`, `alternateId`, `ehhn`, `householdId`,
`cgPersonId`, `epsn`, `SubscriberID`.

`cardNumberWithCD` is the loyalty number plus a check digit, which makes it a
14-digit run indistinguishable in shape from a payment card. The generator picks
a check digit that deliberately *fails* Luhn, so `tools/scan_pii.py` keeps its
payment-card rule armed inside this fixture directory rather than standing it
down. Real cards stay catchable; the field's shape is unaffected.

Eight identifiers for one shopper, and the report explains none of them. That is
itself worth surfacing: each becomes an `Identity` row so the user can see how
many separate keys exist for them. `householdId` and `ehhn` are household-scoped,
which means they cover people who never enrolled in anything.

### Inferences (blob 2)

Two populations that the report mixes together and the adapter must separate.

**Propensity axes** — Convenience, Loyalty, Price, Quality, Variety Seeking —
carry prose values ("Above Average"), not numbers. Classified
`FIRST_PARTY_MODEL`: they are computable from the baskets in this very report.

**Demographics and likelihoods** — age range, education level, gender, household
composition, adult and child counts, pet ownership, home ownership, length of
residence, income predictor score, and 1–7 likelihood scales for cruise, travel,
charitable giving and online shopping. Classified `APPENDED_THIRD_PARTY`.

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

**`petOwner` is appended but derivable.** It is classified
`APPENDED_THIRD_PARTY` like the rest of the demographics block, but flagged
`derivable_from_txns=True`: pet food appears as line items in these very baskets.
The two fields answer different questions — where it most likely came from, and
whether the retailer *could* have worked it out from what it already had.

**`onlineShopperLikelihood` is derivable-unknown.** Kroger holds its own online
order records, but this report discloses no channel field, so from the data
provided the question cannot be answered. `NULL`, not `False`. The tri-state
exists for exactly this.

**The address is household-scoped.** An address describes everyone living there,
not only the person who enrolled.

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
seeded from structure, never from values. It currently yields roughly 126
baskets, 1,300 line items and 228 distinct UPCs across 24 months, against
roughly 380 distinct UPCs across 23 months in the reference. Prices carry a
small annual drift so the price-history view shows a series rather than noise.

`make fixtures-check` regenerates it and fails on any difference. That check is
load-bearing: `tools/scan_pii.py` stands a few address-shaped rules down inside
generated fixture directories, and byte-identical regeneration is what replaces
them. A real report dropped in here does not reproduce from the seed.
