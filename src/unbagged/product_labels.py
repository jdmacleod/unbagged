"""How a retailer's printed product name becomes a label, and when it is a charge.

Split out of `views.py`, which uses both: the product index and price history
show every name through `clean_label`, and set a line aside when
`is_non_product` says it is a charge rather than a thing bought. Where these
sit among the other reasons a line leaves the index is written down beside
`views.PRODUCT_KEY` (#100).
"""

from __future__ import annotations

import re

#: A price the retailer printed into the name field, e.g. `$0.05 CRV DEPOSIT`.
#:
#: **The currency symbol is required, and that is the whole point.** A rule that
#: stripped any leading number was measured against the corpus and matched seven
#: names: the five that really carry a price, and two that open with a bottle
#: size followed by a unit. Eating the size off those leaves a name that is
#: wrong and still reads like a plausible product, which is worse than an ugly
#: one — it is the `2% MILK` hazard, one step along. Requiring the symbol
#: matched five of five and nothing else.
_PRICE_PREFIX = re.compile(r"^\s*\$\s*\d+(?:[.,]\d+)?\s+")


def _strip_leading_punctuation(text: str) -> str:
    """Drop what a name opens with that no reader needs.

    A stray bracket, a quotation mark, an OCR artefact. Digits are NOT
    punctuation here and must survive: `2% MILK` and `7UP` are products.

    **Letters means letters in any script, which an `A-Za-z` class does not.**
    This was written as `^[^A-Za-z0-9]+` and would have stripped the whole of a
    name written in Hangul or Han, leaving the empty string — which the caller
    reads as "this row names no product" and drops from Products AND Prices. On
    a Korean grocer's response that is not an edge case, it is the shelf. Caught
    by review on #102. `str.isalnum` is Unicode-aware, so 한 and é and 7 all
    count as the start of a name and only punctuation is taken.
    """
    for index, char in enumerate(text):
        if char.isalnum():
            return text[index:]
    return ""


#: Words a receipt uses for a line that is a charge but not a thing you bought:
#: a container deposit, a redemption value, a tax printed as its own row, a
#: carrier-bag fee (#105). `BAG` is safe here only because of the rule below: a
#: bag OF something has a word that is not in the set.
#:
#: **Every token has to be in here before a line is refused**, never one of
#: them. Measured against the corpus: matching on containment drops eight rows
#: where five are meant, and the three it takes are real drinks whose names
#: carry the word beside a size and a unit. `adapters/hmart/receipt.py`
#: `_is_furniture` records the same lesson from the other direction, where
#: matching the word `TENDER` alone deleted a real `GIFT CARD` purchase and the
#: basket then came up short for a reason nobody could see.
#:
#: Kept as a module constant rather than asked of the adapter. `RetailerAdapter`
#: is a structural Protocol that no adapter inherits, so it cannot hand one a
#: default, and `product_index` holds no retailer to ask. CLAUDE.md's rule is
#: that core must not branch on retailer IDENTITY; a shared constant branches on
#: nothing. It moves behind the protocol the first time a jurisdiction disagrees
#: with it. Where it sits among the other exclusions is under `views.PRODUCT_KEY`.
NON_PRODUCT_WORDS = frozenset({"CRV", "DEPOSIT", "DEP", "BOTTLE", "TAX", "FEE", "CARRYOUT", "BAG"})


def clean_label(raw: str) -> str:
    """The name with the retailer's furniture taken off the front.

    Display only. The raw spelling is what every lookup still matches on, which
    is why `product_index` returns it alongside as `match_name`: the timeline
    searches `description_raw` with a LIKE, so a cleaned string sent as a query
    finds a product that was bought and reports that it never was.

    Returns `""` where nothing is left, which means the row names no product.
    """
    return _strip_leading_punctuation(_PRICE_PREFIX.sub("", raw)).strip()


def is_non_product(label: str) -> bool:
    """Is this line a charge rather than something bought?

    True only when EVERY word is charge vocabulary (deposit, tax, bag fee). `CRV DEPOSIT` is
    the receipt charging you for a container; a drink whose name happens to end
    in `CRV` is a drink.
    """
    words = label.upper().split()
    return bool(words) and all(word in NON_PRODUCT_WORDS for word in words)
