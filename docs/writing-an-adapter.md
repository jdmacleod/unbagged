# Writing an adapter

An adapter turns one retailer's response into the canonical model. Everything
downstream — the schema, the API, all five views — is retailer-agnostic, so
adding a retailer means adding a package and one import. You should not need to
touch core code. If you find yourself editing `views.py` or `api.py` to make a
retailer work, that is a bug in the abstraction; please say so in an issue.

## Before you start

**Do not attach your report to anything.** Not an issue, not a PR, not a private
fork. It contains your home address, your phone number, your loyalty card number
and years of your purchases, and git history is permanent. If you need to show
someone the shape of a file:

```bash
unbagged sanitize path/to/report.json -o skeleton.json
```

Read `CONTRIBUTING.md` before you put anything in `data/`.

## The five rules

Everything else in this document is detail. These are the contract.

1. **Every emitted record carries provenance** — `source_document_id`, `page`,
   `locator`. The UI must be able to answer "where did this come from" for any
   value on screen. That is the entire premise of the tool: a number you cannot
   check against the document is a number you have to take on faith, and taking
   a retailer's word for it is what got the user here.
2. **Never mutate a value.** `description_raw` is stored exactly as it appeared,
   spelling and all. Normalisation and enrichment happen in a later pass so the
   original stays recoverable.
3. **Absence is a finding.** If the response has no section on categories of
   sources, emit `Disclosure(category=SOURCES, status=ABSENT)`. A missing row
   means "not yet parsed"; an `ABSENT` row means "the retailer did not say".
   Those are different facts and the UI shows them differently. `absent_disclosures()`
   fills the gaps for you — call it and you cannot get this wrong.
4. **Degrade, do not crash.** One malformed basket costs one `ParseWarning` and
   one skipped record, not the other fifty-three baskets. `WarningCollector` makes
   this a one-liner in a loop body, which is the point: anything more expensive
   gets skipped under deadline.
5. **Ship a synthetic fixture and a `NOTES.md`.** The notes are as valuable as
   the code. They are the institutional memory of what the format actually looks
   like, and they are what lets the next person tell a retailer's format change
   from a parser bug.

## Layout

```
src/unbagged/adapters/<retailer>/
    __init__.py          registers the adapter
    adapter.py           sniff() and parse()
    reader.py            optional: format quirks, if there are enough to isolate
    NOTES.md             what you observed, and every judgment call you made
    fixtures/
        generate.py      Faker-backed, deterministic, seeded from structure
        synthetic_*.txt  generated, committed, contains nothing real
tests/test_<retailer>_adapter.py
```

## The protocol

```python
from unbagged.adapters.base import ParseResult, SourceBundle

class MyAdapter:
    retailer_id = "example"       # stable; it is a database value
    display_name = "Example Mart"
    schema_version = 1            # bump when the retailer changes their format

    def sniff(self, bundle: SourceBundle) -> float:
        """Confidence 0.0-1.0. Must be cheap and must not raise."""

    def parse(self, bundle: SourceBundle) -> ParseResult:
        """Full parse. May raise AdapterError with a user-readable message."""
```

Import everything you need from `unbagged.adapters.base`. Register in
`__init__.py`:

```python
from unbagged.adapters.example.adapter import adapter
from unbagged.adapters.registry import register

register(adapter)
```

…and add the package to the import list in `adapters/__init__.py`. That is the
one line of core code you touch.

### `sniff()`

Read as little as possible. `extract(document, max_pages=3)` exists for exactly
this: choosing an adapter should not cost a full extraction of a 48-page PDF.

Score on what the documents *contain*, not on what the user typed. The upload
form's `declared_retailer` is a hint worth a small bonus and nothing more —
people mislabel, and a report that says Kroger on every page is a Kroger report
whatever the form said.

`sniff()` must not raise. The registry catches anyway and scores a raising
adapter zero, because one broken adapter must not make every report unparseable
— but do not rely on that.

If you are writing a fallback rather than a retailer, set `fallback = True`. The
registry only consults fallbacks when no real adapter scores above zero.

### `parse()`

Raise `AdapterError` only when nothing useful can be produced at all, and write
the message for the person who uploaded the file — it is shown to them verbatim.
Everything short of that is a warning plus a skipped record.

## Provenance in practice

A `locator` is format-specific and opaque to everything except you and the human
reading it: a JSON path, a line range, a CSV cell reference. Whatever lets
someone find the value again.

`page` is the printed page number where the format has one. That is what someone
holding a printout is looking for, and it is not always the PDF's page index.

Finding offsets cheaply: records appear in a document in the same order they
appear in the parsed structure, so a forward-only cursor finds each one in a
single pass rather than rescanning the whole document per record. See
`kroger/adapter.py`'s `Cursor`.

## Classifying inferences

This is the most interesting output the tool produces, and the easiest to get
wrong in a way nobody notices.

`origin` is a claim about **where an attribute most likely came from**:

- `FIRST_PARTY_MODEL` — the retailer computed it from data in this response.
  A propensity score derived from the baskets in the same document.
- `APPENDED_THIRD_PARTY` — it cannot have come from the purchase data, so it was
  obtained elsewhere, and the response does not say where.
- `UNKNOWN` — you genuinely cannot tell. Use it rather than guessing.

`derivable_from_txns` is a **different question**: could the transactions in this
response explain this value? Three states, and the third is not decoration:

| Value | Meaning |
|---|---|
| `True` | The baskets could account for it. |
| `False` | Nothing in the baskets explains it. |
| `None` | Not enough was disclosed to tell either way. |

Kroger's `petOwner` is `APPENDED_THIRD_PARTY` with `derivable_from_txns=True`:
pet food is right there in the baskets, but nothing says that is where the value
came from. `onlineShopperLikelihood` is `None`, because the retailer holds its
own order records and this response discloses no channel field. Collapsing that
into `False` would state something the data does not support.

Write your reasoning in `NOTES.md`. The classification is a judgment, it will be
argued with, and the argument should be with your reasoning rather than your
code.

## Deciding disclosure status

| Status | When |
|---|---|
| `PROVIDED` | The response addresses the category with content specific to the user or to the categories the statute names. |
| `PARTIAL` | Addressed, but incompletely. Quote what there was as `evidence`. |
| `ABSENT` | Not addressed at all. Say in `notes` what you looked for. |

Never upgrade a status on inference. "Our affiliates" without naming them is
`PARTIAL` with the phrase quoted, not `PROVIDED`.

**If you are matching on keywords, the ceiling is `PARTIAL`.** A keyword shows a
topic was mentioned; it cannot show the question was answered, and a green cell
next to "we take your privacy seriously" is worse than a red one. The generic
fallback follows this rule and is worth reading as an example.

`docs/legal-basis.md` maps each category to its citation, and records two things
that should *not* be scored as failures: the 12-month lookback with its
supplemental-request path, and a refusal on verification grounds.

## Fixtures

```bash
make fixtures        # regenerate
make fixtures-check  # fail if a committed fixture is not generator output
```

Your `fixtures/generate.py` exposes `generate(seed) -> {filename: content}` and
must be deterministic: the same seed produces byte-identical output.

**Seed it from the structure of a real response, never from its values.** Same
section headers, same nesting, same quirks — placeholder rows, negative amounts,
whatever the format does that a parser has to survive — with entirely fabricated
contents. Use reserved-for-fiction values: `555` phone exchanges, `example.com`
domains, street names that do not exist.

The determinism is not tidiness. `make fixtures-check` runs in CI, and
byte-identical regeneration is what proves nobody dropped a real report into a
fixtures directory. `tools/scan_pii.py` relies on it: a few address-shaped rules
stand down inside fixture directories that ship a generator, precisely because
this check covers them. A hand-written fixture directory has no generator and
keeps every rule.

One trap worth knowing: if your format has a field that renders as a long digit
run, make the generator produce a value that **fails** a Luhn check. A 14-digit
run that passes Luhn is indistinguishable from a payment card, and the honest fix
is to keep the scanner's card rule armed rather than to relax it.

## Testing

Test against your fixture, and test the degradation separately — truncated input,
one malformed record among good ones, amounts arriving as `"$1,234.56"` or `""`
or junk, a missing section, a corrupt blob beside a good one. See
`tests/test_kroger_degradation.py`; that file found a real bug in the reader.

Assert the counts the fixture actually contains rather than magic numbers where
you can. `test_every_basket_becomes_a_transaction` compares the parsed count
against a `grep` of the raw text, which catches the failure that matters most:
silently losing a record while cleaning up the input.

## Checklist

- [ ] `sniff()` is cheap, does not raise, and scores on content
- [ ] Every emitted record has a document, a page and a locator
- [ ] Raw values are stored verbatim
- [ ] `absent_disclosures()` is called, so all eight categories are covered
- [ ] Every loop body records a warning instead of raising
- [ ] `AdapterError` messages read like something you would show a person
- [ ] `fixtures/generate.py` is deterministic and seeded from structure only
- [ ] `make fixtures-check`, `make check-pii`, `make test` and `make lint` pass
- [ ] `NOTES.md` records the format, the quirks, and every judgment call
- [ ] The retailer package is imported in `adapters/__init__.py`
