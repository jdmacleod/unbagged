"""Which products a capped Products index keeps, and in what order (#141).

The index is drawn alphabetically, and it used to be capped the same way: sort
by name, take the first `limit`. On a response with more products than the cap
that dropped the end of the alphabet — every product after some letter, the
rail's later letters, and every one of them from the list of products bought
and not since — while keeping thousands of things bought once. A cut by name is
the one cut that says nothing about what was bought.

So the cap ranks by how often each product was bought and keeps the top of
that, then draws what it kept alphabetically. What goes is the least-bought
tail, which on a large response is a slice of the products bought once, the
least informative entries the page has. The page says how many it left out and
how often the most-bought of them was bought, so the cut is checkable.

Kept out of `views.py`, which is past its size baseline.
"""

from __future__ import annotations

from typing import Any


def _alphabetical(product: dict[str, Any]) -> tuple[str, str]:
    # The product key breaks ties so the order is total and the same filter
    # always renders the same page. The key rather than the UPC: a response
    # that disclosed no codes has none to sort by, and None does not compare
    # against a string.
    return (product["description"], product["key"])


def cap(products: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    """The capped index and what the page needs to say about the cap.

    `products` is everything the filters let through. The stopped list is taken
    from all of it, not from what the cap kept: it is short, and drawing it from
    the capped page is how it lost every stopped product past the cut.
    """
    by_purchases = sorted(products, key=lambda p: (-p["purchases"], *_alphabetical(p)))
    kept, left_out = by_purchases[:limit], by_purchases[limit:]
    return {
        "truncated": bool(left_out),
        "limit": limit,
        "left_out": len(left_out),
        # The most any left-out product was bought: 1 means only things bought
        # once were cut, which is the sentence the page most often gets to say.
        "left_out_max_purchases": max((p["purchases"] for p in left_out), default=0),
        "products": sorted(kept, key=_alphabetical),
        "stopped": sorted((p for p in products if p["stopped"]), key=_alphabetical),
    }
