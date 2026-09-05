"""Read queries shaped by the four views, not by the tables.

`repository.py` is about persistence: it round-trips a ParseResult. This module
answers the questions the UI actually asks, which do not map one-to-one onto rows
— "what did they infer and where did it come from", "which categories did this
retailer skip", "what has this product cost me over two years".

Every function returns plain dicts and lists, ready to serialise.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from statistics import median
from typing import Any

from unbagged.models import DisclosureCategory, DisclosureStatus, InferenceOrigin


def _escape_like(value: str) -> str:
    """Neutralise LIKE's wildcards so a search matches what was typed.

    Backslash first, or it would escape the escapes added after it.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params)]


def disclosed_specific_pieces(conn: sqlite3.Connection, request_id: int) -> bool:
    """Did this retailer actually disclose the specific pieces of data?

    The distinction this guards is the product's whole thesis. A count of zero
    means one of two very different things: the retailer told us about this
    category and the answer is genuinely none, or the retailer said nothing at
    all. Rendering both as `0` states the first when the truth is the second,
    which is the opposite of what a compliance tool is for. "Identifiers held
    for you: 0" reads as a fact about the retailer; what the response actually
    contained was silence.

    A response with SPECIFIC_PIECES anything other than `provided` disclosed no
    data, so every data-derived number for it is unknown rather than zero, and
    the API returns null so the UI can render it as "not disclosed".
    """
    row = conn.execute(
        "SELECT status FROM disclosure WHERE request_id = ? AND category = ?",
        (request_id, DisclosureCategory.SPECIFIC_PIECES.value),
    ).fetchone()
    return bool(row) and row["status"] == DisclosureStatus.PROVIDED.value


def _provenance(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_document_id": row.pop("source_document_id", None),
        "page": row.pop("page", None),
        "locator": row.pop("locator", None),
    }


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------


def timeline(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    store: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Visits over the coverage window, with the header stats above them.

    `query` searches line-item descriptions and UPCs, and returns the baskets
    that contain a match — the useful shape for "when did I buy this".
    """
    where = ["t.request_id = ?"]
    params: list[Any] = [request_id]
    if store:
        where.append("t.store_code = ?")
        params.append(store)
    if date_from:
        where.append("t.occurred_at >= ?")
        params.append(date_from)
    if date_to:
        # Inclusive of the whole day when a bare date is given.
        where.append("t.occurred_at <= ?")
        params.append(f"{date_to}T23:59:59" if len(date_to) == 10 else date_to)
    if query:
        # Escaped, because LIKE treats % and _ as wildcards and the search box
        # feeds it raw user input. Typing "%" used to return every basket and
        # "_" matched any single character, so a product code containing an
        # underscore silently over-matched with nothing to explain why.
        # (Injection was never possible; the value has always been bound.)
        pattern = f"%{_escape_like(query)}%"
        where.append(
            "EXISTS (SELECT 1 FROM txn_item i WHERE i.txn_id = t.id"
            " AND (i.description_raw LIKE ? ESCAPE '\\'"
            " OR i.upc LIKE ? ESCAPE '\\'))"
        )
        params.extend([pattern, pattern])

    # Every element of `where` is a literal fragment written above; every value
    # is bound in `params`. Nothing the caller sends reaches the SQL text.
    clause = " AND ".join(where)
    baskets = _rows(
        conn,
        f"""
        SELECT t.id, t.occurred_at, t.external_order_id, t.store_code, t.division_code,
               t.channel, t.tender_type, t.total_pre_discount,
               t.source_document_id, t.page, t.locator,
               COUNT(i.id) AS item_count,
               -- Shelf and paid are summed over the SAME lines. Written as two
               -- independent SUMs, a line with a loyalty amount and no retail
               -- amount landed in `paid_total` and not in `shelf_total`, so the
               -- basket reported a saving it never had and the two columns
               -- described different sets of rows. Whichever amount is present
               -- stands in for the missing one, so the pair is always consistent
               -- and no line is silently dropped from the totals.
               COALESCE(SUM(
                   CASE WHEN i.retail_amt IS NOT NULL OR i.loyalty_amt IS NOT NULL
                        THEN COALESCE(i.retail_amt, i.loyalty_amt) END
               ), 0) AS shelf_total,
               -- A line with no loyalty price disclosed cost its shelf price:
               -- no loyalty price is no loyalty saving. COALESCE rather than
               -- SUM(loyalty_amt), which silently drops those lines from the
               -- total and understates what the basket cost.
               COALESCE(SUM(
                   CASE WHEN i.retail_amt IS NOT NULL OR i.loyalty_amt IS NOT NULL
                        THEN COALESCE(i.loyalty_amt, i.retail_amt) END
               ), 0) AS paid_total
        FROM txn t LEFT JOIN txn_item i ON i.txn_id = t.id
        WHERE {clause}
        GROUP BY t.id
        ORDER BY t.occurred_at, t.id
        """,  # noqa: S608 - `clause` is literal fragments; every value is bound
        tuple(params),
    )
    for basket in baskets:
        basket["provenance"] = _provenance(basket)
        _settle(basket)

    return {"stats": stats(conn, request_id), "filtered_count": len(baskets),
            "baskets": baskets}


def _settle(basket: dict[str, Any]) -> dict[str, Any]:
    """Turn a basket's two disclosed amounts into the three a receipt has.

    Both amounts are **prices**. `retail_amt` is the shelf price and
    `loyalty_amt` is what the line actually cost under the loyalty programme;
    the saving is the difference. `loyalty_amt` is not a discount to subtract —
    see `models.TxnItem`, where reading it the wrong way is spelled out, because
    the wrong reading produces a plausible-looking number rather than an error.

    So `paid_total` here is summed, not derived: it is what the retailer
    disclosed the basket cost. `saved_total` is the derived one.
    """
    shelf, paid = basket["shelf_total"], basket["paid_total"]
    basket["shelf_total"] = round(shelf, 2)
    basket["paid_total"] = round(paid, 2)
    basket["saved_total"] = round(shelf - paid, 2)

    # The retailer states its own pre-discount basket total. Comparing it to the
    # summed shelf lines is the only check available on whether this parse read
    # the basket whole. A gap is reported, never silently absorbed — but it is
    # not necessarily a parse fault: a stated total may also carry tax or fees
    # that the itemised lines never break out. Either way the reader should see
    # it. None when the retailer stated no total to check against.
    stated = basket.get("total_pre_discount")
    basket["stated_pre_discount_delta"] = (
        None if stated is None else round(basket["shelf_total"] - stated, 2)
    )
    return basket


def stats(conn: sqlite3.Connection, request_id: int) -> dict[str, Any]:
    """Header numbers for the timeline.

    `distinct_products` counts UPCs on lines that cost something, and
    `zero_value_lines` is reported separately rather than folded in. Kroger's
    export is full of placeholder rows naming no product at zero cost; counting
    them as products would inflate the number, and hiding them would conceal a
    fact about the quality of the disclosure.

    There is no `total_spend` here on purpose. The name has one obvious meaning
    to a reader — what left my account — and the only number this view used to
    put behind it was the summed shelf amount, which is larger by every loyalty
    saving the shopper earned. `total_shelf` and `total_paid` each say which one
    they are, and `total_saved` is the difference between them.
    """
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS basket_count,
               MIN(t.occurred_at) AS first_visit,
               MAX(t.occurred_at) AS last_visit,
               COALESCE(SUM(CASE WHEN i.retail_amt IS NOT NULL THEN i.retail_amt END), 0)
                   AS total_shelf,
               -- See the same COALESCE in timeline(): loyalty_amt is the price
               -- paid, and a line without one cost its shelf price.
               COALESCE(SUM(COALESCE(i.loyalty_amt, i.retail_amt)), 0) AS total_paid,
               COUNT(i.id) AS line_count,
               COUNT(DISTINCT CASE WHEN i.retail_amt <> 0 THEN i.upc END)
                   AS distinct_products,
               SUM(CASE WHEN i.retail_amt = 0 THEN 1 ELSE 0 END) AS zero_value_lines,
               SUM(CASE WHEN i.retail_amt < 0 THEN 1 ELSE 0 END) AS negative_lines
        FROM txn t LEFT JOIN txn_item i ON i.txn_id = t.id
        WHERE t.request_id = ?
        """,
        (request_id,),
    ).fetchone()
    result = dict(row)
    result["total_shelf"] = round(result["total_shelf"], 2)
    result["total_paid"] = round(result["total_paid"], 2)
    result["total_saved"] = round(result["total_shelf"] - result["total_paid"], 2)
    stores = _rows(
        conn,
        "SELECT store_code, COUNT(*) AS visits FROM txn WHERE request_id = ?"
        " AND store_code IS NOT NULL GROUP BY store_code ORDER BY visits DESC",
        (request_id,),
    )
    result["stores"] = stores
    result["zero_value_lines"] = result["zero_value_lines"] or 0
    result["negative_lines"] = result["negative_lines"] or 0

    # Null, not zero, when the retailer disclosed no specific pieces. See
    # disclosed_specific_pieces().
    result["disclosed"] = disclosed_specific_pieces(conn, request_id)
    if not result["disclosed"]:
        for key in (
            "basket_count", "total_shelf", "total_paid", "total_saved",
            "line_count", "distinct_products", "zero_value_lines", "negative_lines",
            "first_visit", "last_visit",
        ):
            result[key] = None
    return result


def transaction_detail(conn: sqlite3.Connection, txn_id: int) -> dict[str, Any] | None:
    """One basket with its line items, for the drill-down.

    All three amounts travel together so the discount delta is visible per line,
    which is the thing a receipt never shows you, and the basket totals are
    summed from these same lines so the drill-down foots to the row that opened
    it. Those two numbers came from different queries before and disagreed by
    the loyalty discount, with nothing on screen to explain the gap.
    """
    row = conn.execute("SELECT * FROM txn WHERE id = ?", (txn_id,)).fetchone()
    if row is None:
        return None
    basket = dict(row)
    basket["provenance"] = _provenance(basket)
    items = _rows(
        conn,
        "SELECT id, description_raw, upc, quantity, retail_amt, loyalty_amt,"
        " category, category_confidence FROM txn_item WHERE txn_id = ? ORDER BY id",
        (txn_id,),
    )
    for item in items:
        retail, loyalty = item.get("retail_amt"), item.get("loyalty_amt")
        # loyalty_amt is the price paid, not a discount. A line with no loyalty
        # price cost its shelf price. `net_amt` used to be retail − loyalty,
        # which on a full-price line — two thirds of a real response — rendered
        # $0.00 under a column headed "You paid".
        item["paid_amt"] = retail if loyalty is None else loyalty
        item["saved_amt"] = (
            None
            if retail is None or item["paid_amt"] is None
            else round(retail - item["paid_amt"], 2)
        )
    basket["items"] = items
    basket["item_count"] = len(items)
    basket["shelf_total"] = sum(i["retail_amt"] or 0 for i in items)
    basket["paid_total"] = sum(i["paid_amt"] or 0 for i in items)
    _settle(basket)
    return basket


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


def profile(conn: sqlite3.Connection, request_id: int) -> dict[str, Any]:
    """Identities and inferences, grouped by where they most likely came from.

    The split is the point. A propensity score computed from your own baskets and
    a household income estimate bought from somewhere the report does not name
    are different kinds of claim, and showing them in one undifferentiated list
    would lose the only interesting thing about them.
    """
    identities = _rows(
        conn,
        "SELECT id, id_type, value, scope, first_seen, source_document_id, page, locator"
        " FROM identity WHERE request_id = ? ORDER BY id",
        (request_id,),
    )
    for identity in identities:
        identity["provenance"] = _provenance(identity)

    inferences = _rows(
        conn,
        "SELECT id, label, value_raw, value_num, scale, subject, origin,"
        " derivable_from_txns, source_document_id, page, locator"
        " FROM inference WHERE request_id = ? ORDER BY id",
        (request_id,),
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inference in inferences:
        inference["provenance"] = _provenance(inference)
        inference["derivable_from_txns"] = (
            None if inference["derivable_from_txns"] is None
            else bool(inference["derivable_from_txns"])
        )
        grouped[inference["origin"]].append(inference)

    household = [i for i in inferences if i["subject"] == "household"]
    return {
        "identities": identities,
        "identity_count": len(identities),
        "inferences_by_origin": {
            origin.value: grouped.get(origin.value, []) for origin in InferenceOrigin
        },
        # Surfaced separately because these describe people who never enrolled in
        # anything — the profile view calls them out rather than burying them.
        "household_scoped": household,
        "household_scoped_count": len(household),
    }


# --------------------------------------------------------------------------
# Compliance
# --------------------------------------------------------------------------


def compliance(conn: sqlite3.Connection) -> dict[str, Any]:
    """Retailers as rows, the eight disclosure categories as columns."""
    requests = _rows(
        conn,
        "SELECT id, retailer_id, display_name, report_reference, period_start,"
        " period_end, statute FROM request ORDER BY id",
    )
    rows = []
    for request in requests:
        cells = {
            r["category"]: r
            for r in _rows(
                conn,
                "SELECT category, status, evidence, notes, source_document_id, page,"
                " locator FROM disclosure WHERE request_id = ?",
                (request["id"],),
            )
        }
        for cell in cells.values():
            cell["provenance"] = _provenance(cell)
        row = dict(request)
        row["cells"] = {
            category.value: cells.get(
                category.value,
                # A category with no row at all means the adapter never looked,
                # which is a different fact from the retailer not answering.
                {"category": category.value, "status": None,
                 "notes": "This adapter did not assess this category."},
            )
            for category in DisclosureCategory
        }
        row["absent_count"] = sum(
            1 for c in row["cells"].values() if c.get("status") == DisclosureStatus.ABSENT
        )
        row["follow_ups"] = _rows(
            conn,
            "SELECT id, kind, description, resolved FROM follow_up WHERE request_id = ?"
            " ORDER BY id",
            (request["id"],),
        )
        rows.append(row)
    return {"categories": [c.value for c in DisclosureCategory], "rows": rows}


# --------------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------------


def compare(conn: sqlite3.Connection) -> dict[str, Any]:
    """Side-by-side summary per retailer. Empty-stated until there are two."""
    requests = _rows(
        conn,
        "SELECT id, retailer_id, display_name, period_start, period_end FROM request"
        " ORDER BY id",
    )
    for request in requests:
        request_id = request["id"]
        summary = stats(conn, request_id)
        disclosed = summary["disclosed"]
        request["disclosed"] = disclosed
        request["visits"] = summary["basket_count"]
        # Paid, not shelf. Comparing two retailers on pre-discount totals ranks
        # them by who lists higher prices, not by who cost you more.
        request["total_paid"] = summary["total_paid"]
        request["total_shelf"] = summary["total_shelf"]
        request["total_saved"] = summary["total_saved"]
        request["distinct_products"] = summary["distinct_products"]
        request["first_visit"] = summary["first_visit"]
        request["last_visit"] = summary["last_visit"]

        def count(sql: str, params: tuple, *, known: bool = disclosed) -> int | None:
            # None, not 0, when nothing was disclosed: "0 identifiers" is a claim
            # about the retailer, and this response made no such claim.
            # `known` is bound as a default so the closure does not capture the
            # loop variable.
            return conn.execute(sql, params).fetchone()["c"] if known else None

        request["identifier_count"] = count(
            "SELECT COUNT(*) c FROM identity WHERE request_id = ?", (request_id,)
        )
        request["inference_count"] = count(
            "SELECT COUNT(*) c FROM inference WHERE request_id = ?", (request_id,)
        )
        request["appended_inference_count"] = count(
            "SELECT COUNT(*) c FROM inference WHERE request_id = ? AND origin = ?",
            (request_id, InferenceOrigin.APPENDED_THIRD_PARTY.value),
        )
        # Not gated: what a retailer failed to address is a real finding about
        # that retailer, and it is exactly what this row is worth reading for.
        request["absent_disclosures"] = conn.execute(
            "SELECT COUNT(*) c FROM disclosure WHERE request_id = ? AND status = ?",
            (request_id, DisclosureStatus.ABSENT.value),
        ).fetchone()["c"]
    return {"requests": requests, "comparable": len(requests) > 1}


# --------------------------------------------------------------------------
# Price history
# --------------------------------------------------------------------------


def price_history(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    min_observations: int = 3,
    limit: int = 200,
) -> dict[str, Any]:
    """What each product cost you over the coverage window.

    A personal inflation series, which two years of itemised baskets contains
    for free — but only for the products whose amounts are actually a price.

    **A line carries an amount, never a quantity and never a weight.** Two cans
    of tomatoes bought together arrive as one line at twice the price of one
    can, and chicken thighs sold by weight arrive at a different amount every
    trip for reasons that have nothing to do with the price changing. Charting
    those as a price series reports a two-can trip as a 100% rise. So each
    product is classified by the shape of its own amounts, and the ones the
    response cannot price are named rather than drawn.

    Classification is stated as suspicion, never as fact. The response does not
    say, and neither should the app.
    """
    rows = _rows(
        conn,
        """
        SELECT i.upc, i.description_raw, i.retail_amt, i.loyalty_amt,
               substr(t.occurred_at, 1, 10) AS on_date
        FROM txn_item i JOIN txn t ON t.id = i.txn_id
        WHERE t.request_id = ? AND i.upc IS NOT NULL AND i.retail_amt > 0
        ORDER BY t.occurred_at, i.id
        """,
        (request_id,),
    )

    series: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = series.setdefault(
            row["upc"],
            {"upc": row["upc"], "descriptions": defaultdict(int), "points": []},
        )
        entry["descriptions"][row["description_raw"]] += 1
        retail = row["retail_amt"]
        # loyalty_amt is the price paid. No loyalty price means the shelf price.
        paid = retail if row["loyalty_amt"] is None else row["loyalty_amt"]
        entry["points"].append(
            {
                "date": row["on_date"],
                "retail_amt": retail,
                "paid_amt": paid,
                "saved_amt": round(retail - paid, 2),
            }
        )

    products = []
    for entry in series.values():
        points = entry["points"]
        # One line is one purchase. An earlier version grouped by day and
        # reported a separate line count, on the belief that buying three of
        # something arrived as three lines; across a real response that happened
        # on 0 of 762 product-days. The idea came from the synthetic fixture,
        # whose generator picks products with replacement.
        if len(points) < min_observations:
            continue
        points.sort(key=lambda p: p["date"])

        shape = _price_shape([p["retail_amt"] for p in points])
        for point, multiple in zip(points, shape["multiples"], strict=True):
            point["multiple_of"] = multiple

        # Only amounts that look like a single unit can carry a price change,
        # and the change is measured on what you PAID.
        #
        # It used to be measured on the shelf amount while the chart drew paid as
        # the heavy line and shelf as the light one, so the "Change" column and
        # the line the eye follows were answering different questions. The tab is
        # called "what things cost you"; paid is the figure that means that, and
        # First and Latest move with it so all three agree.
        singles = [p for p, m in zip(points, shape["multiples"], strict=True) if not m]
        change = None
        if shape["kind"] == "unit" and len(singles) >= 2:
            first, last = singles[0]["paid_amt"], singles[-1]["paid_amt"]
            change = round((last - first) / first * 100, 1) if first else None

        products.append(
            {
                "upc": entry["upc"],
                # The description can vary between visits; the commonest one is
                # the honest label, and the raw values stay reachable per point.
                "description": max(entry["descriptions"].items(), key=lambda kv: kv[1])[0],
                "purchases": len(points),
                # "unit"     — amounts look like one item at a stable-ish price
                # "multiple" — some amounts are near-exact integer multiples of
                #              the commonest one, so those trips probably bought
                #              more than one
                # "weight"   — amounts never repeat and range widely, which is
                #              what a per-pound item does
                "shape": shape["kind"],
                "base_price": shape["base"],
                "multiple_count": sum(1 for m in shape["multiples"] if m),
                "priceable": shape["kind"] == "unit",
                "first_seen": points[0]["date"],
                "last_seen": points[-1]["date"],
                "first_price": singles[0]["paid_amt"] if singles else points[0]["paid_amt"],
                "last_price": singles[-1]["paid_amt"] if singles else points[-1]["paid_amt"],
                "min_price": min(p["retail_amt"] for p in points),
                "max_price": max(p["retail_amt"] for p in points),
                "change_pct": change,
                "points": points,
            }
        )

    # Priceable products first: those are the ones the view can actually plot.
    products.sort(key=lambda p: (not p["priceable"], -p["purchases"], p["description"]))
    return {
        "min_observations": min_observations,
        "product_count": len(products),
        "priceable_count": sum(1 for p in products if p["priceable"]),
        # Kroger's export carries no quantity field on a line. Stated once here
        # so the view can say so rather than implying a quantity it never got.
        "quantity_disclosed": _quantity_disclosed(conn, request_id),
        "products": products[:limit],
    }


# Two amounts within this of each other are treated as the same price. Prices
# drift over two years, so exact repetition is too strict to find the base.
SAME_PRICE = 0.03
# How close to a whole number a ratio must sit before it reads as a multiple.
MULTIPLE_TOLERANCE = 0.02
# How far either side of a candidate multiple counts as "on the way there".
DRIFT_MARGIN = 0.15


def _near_multiple(ratio: float) -> int | None:
    """The whole number this ratio sits on, if it sits close enough to one.

    Tolerance is proportional to the multiple. A 3x line carries three times the
    accumulated jitter of a 1x line, so a fixed absolute window is simultaneously
    too tight at the top of the range and too loose at the bottom.

    This exists because two call sites disagreed. Choosing a better base tested
    `abs(ratio - round(ratio)) <= MULTIPLE_TOLERANCE * round(ratio)` while
    `multiple_of` tested `abs(ratio - nearest) > MULTIPLE_TOLERANCE`, so a
    candidate could be accepted as the unit by the first rule and then explain
    none of the amounts under the second: the base moved and nothing was marked.
    """
    nearest = round(ratio)
    if nearest < 2:
        return None
    return nearest if abs(ratio - nearest) <= MULTIPLE_TOLERANCE * nearest else None


def _price_shape(amounts: list[float]) -> dict[str, Any]:
    """Guess whether a product's amounts are a unit price, multiples, or weight.

    The base is the amount the product sits at most often, not the cheapest: a
    product always bought in twos would otherwise have its pair price treated as
    the unit and every single purchase read as a half-price sale.

    Every output here is a suspicion. The response states no quantity and no
    weight, so nothing in this function can be verified from it, and the view is
    expected to say "consistent with" rather than "is".
    """
    # A non-positive amount is not a price. Zeros are the export's placeholder
    # rows and negatives are returns; both are filtered before this function is
    # reached through `price_history`, but it is a pure function with its own
    # tests and callers, and clustering divided by `cluster[0]` — so a single
    # 0.00 raised ZeroDivisionError. The returned `multiples` list still lines up
    # with the input, because the caller zips the two with strict=True.
    priced = [a for a in amounts if a > 0]
    if not priced:
        return {"kind": "unit", "base": None, "multiples": [None] * len(amounts)}

    # Cluster amounts that are within SAME_PRICE of each other; the biggest
    # cluster is the product's ordinary price.
    clusters: list[list[float]] = []
    for amount in sorted(priced):
        for cluster in clusters:
            if abs(amount - cluster[0]) / cluster[0] <= SAME_PRICE:
                cluster.append(amount)
                break
        else:
            clusters.append([amount])

    # Ties break toward the middle of the range, not the bottom. When nothing
    # repeats every cluster has one member, and `-c[0]` picked the CHEAPEST
    # amount as the base — the exact reading the docstring above rules out, and
    # the one that makes every other amount look like a large multiple of it.
    # With no repetition there is no "most often", so "most typical" is the
    # honest fallback.
    typical = median(priced)
    biggest = max(clusters, key=lambda c: (len(c), -abs(c[0] - typical)))
    base = round(sum(biggest) / len(biggest), 2)

    # The commonest amount is not always one item. A product usually bought in
    # twos makes the pair the base, and the single purchase then reads as a
    # half-price sale rather than as the unit. If some amount divides the base a
    # whole number of times, that amount is the better unit.
    for candidate in sorted(set(amounts)):
        if candidate <= 0 or candidate >= base:
            continue
        if _near_multiple(base / candidate):
            base = round(candidate, 2)
            break

    def multiple_of(amount: float) -> int | None:
        """Is this amount more than one item, or just a price that drifted up?

        Both look like `2 x base`. The difference is the ground in between: a
        price that doubles over two years is observed at the values on the way,
        while a second item appears from nowhere at exactly twice. So a multiple
        is only claimed when the range between the two is empty.

        Without that check a product whose price genuinely doubled came out as a
        quantity buy, which is the same failure as the one this whole function
        exists to prevent, pointed the other way.
        """
        if base <= 0 or amount <= 0:
            return None
        nearest = _near_multiple(amount / base)
        if nearest is None:
            return None
        between = [
            a for a in priced
            if base * (1 + DRIFT_MARGIN) < a < base * (nearest - DRIFT_MARGIN)
        ]
        # One amount on the way is enough. This asked for two, which cannot
        # exist at the smallest series the API allows: `min_observations` floors
        # at 2, and two observations leave no room for two intermediates, so the
        # escape hatch could never fire exactly where a doubling is least
        # distinguishable from a two-buy.
        return None if len(between) >= 1 else nearest

    multiples = [multiple_of(a) for a in amounts]
    spread = max(priced) / min(priced)

    if any(multiples):
        kind = "multiple"
    elif (
        len(biggest) / len(priced) <= REPEAT_SHARE
        and len(priced) >= 4
        and spread > WEIGHT_SPREAD
        and not _trending(priced)
    ):
        # Amounts rarely repeat, the range is wide, and it goes up and down at
        # random: what a per-pound item does. A price that simply climbed also
        # rarely repeats and also spreads wide, so the direction test is what
        # keeps a genuine two-year rise out of this bucket.
        kind = "weight"
    else:
        kind = "unit"
    return {"kind": kind, "base": base, "multiples": multiples}


# What a weight-priced series looks like, measured rather than guessed. Across
# the fixture generator's own pricing model, with the label taken from the draw:
#
#                       largest cluster    spread       trending
#   sold by weight           18%            2.83           4%
#   sold by the unit         33%            1.39          11%
#
# The test used to be "the largest cluster holds exactly one amount", which is
# true of only 12% of weight series and capped recall at 0.12 — 88% of
# weight-priced products were charted as if their amounts were prices, which is
# the single failure the Prices view exists to prevent. Swept over the two
# thresholds, 0.25 and 1.8 maximise F1 at 0.839 (precision 0.874, recall 0.807).
REPEAT_SHARE = 0.25
WEIGHT_SPREAD = 1.8

# How much of a series has to move the same way before it reads as a trend
# rather than as noise.
TREND_SHARE = 0.75


def _trending(amounts: list[float]) -> bool:
    """Does this series mostly move one way? Amounts arrive in date order."""
    steps = [b - a for a, b in zip(amounts, amounts[1:], strict=False) if b != a]
    if len(steps) < 3:
        return False
    up = sum(1 for s in steps if s > 0)
    return max(up, len(steps) - up) / len(steps) >= TREND_SHARE


def _quantity_disclosed(conn: sqlite3.Connection, request_id: int) -> bool:
    """Did any line in this response carry a quantity?

    `txn_item.quantity` exists because a retailer might disclose one. Kroger
    does not: every line is a description, a UPC and two amounts, so a product
    bought twice on one trip is two lines and nothing distinguishes that from
    two trips. Absence of the field is a gap in the disclosure, and this view
    names it rather than presenting a line count as a quantity.
    """
    row = conn.execute(
        "SELECT COUNT(i.quantity) AS c FROM txn_item i JOIN txn t ON t.id = i.txn_id"
        " WHERE t.request_id = ?",
        (request_id,),
    ).fetchone()
    return row["c"] > 0


# --------------------------------------------------------------------------
# Product index
# --------------------------------------------------------------------------

# Size is quantised onto this absolute ladder rather than scaled continuously
# between the smallest and largest count. Three things follow from that, and
# all three were bugs in the continuous version:
#
#   1. The encoding becomes *ordinal*, which is the only claim a portrait
#      should make. On a continuous ramp, 28.3% of comparable pairs painted
#      more ink for the smaller number, because a long name at a small size
#      out-inks a short name at a large one. Quantised, that figure is 0%.
#   2. The tiers are absolute, so there is no min/max to normalise against and
#      therefore no division to guard. A filter matching one product, or
#      matching only single-purchase products, is no longer a special case.
#   3. Five steps map onto the type ladder DESIGN.md already defines, so the
#      view invents no sizes of its own.
#
# (tier, minimum purchases). Highest first; a product takes the first tier it
# reaches. The frontend maps tier -> px; the API never sends a pixel size.
PURCHASE_TIERS: tuple[tuple[int, int], ...] = ((5, 12), (4, 7), (3, 4), (2, 2), (1, 1))

# How long a product has to have been absent, before the end of the coverage
# window, before the view will say out loud that you stopped buying it.
STALE_AFTER_DAYS = 180

# A cap that discloses, rather than a silent truncation. `price_history` already
# caps at 200; one rule for the whole app, not two.
INDEX_LIMIT = 2000


def _tier(purchases: int) -> int:
    return next(tier for tier, floor in PURCHASE_TIERS if purchases >= floor)


def product_index(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    query: str | None = None,
    min_purchases: int = 1,
    limit: int = INDEX_LIMIT,
) -> dict[str, Any]:
    """Every product you bought, by name, sized by how often.

    Not a ranking. The order is alphabetical, which is the one order that is not
    a ranking, and it is what keeps this a portrait of a vocabulary rather than
    a worse version of the Prices table — which already sorts by purchases and
    answers "what do I buy most" precisely.

    **What counts as a purchase.** The same predicate `price_history` uses: a
    line with a UPC and a positive amount. That excludes the export's zero-value
    placeholder rows, which name no product, and the negative lines, which are
    returns and would otherwise let a refund create an index entry for something
    you gave back. Three different predicates for "a purchase" existed in this
    module and the choice moves the headline figure, so the index and Prices are
    deliberately pinned to the same one.
    """
    rows = _rows(
        conn,
        """
        SELECT i.upc, i.description_raw, COUNT(*) AS purchases,
               MIN(substr(t.occurred_at, 1, 10)) AS first_seen,
               MAX(substr(t.occurred_at, 1, 10)) AS last_seen
        FROM txn_item i JOIN txn t ON t.id = i.txn_id
        WHERE t.request_id = ? AND i.upc IS NOT NULL AND i.retail_amt > 0
        GROUP BY i.upc, i.description_raw
        """,
        (request_id,),
    )

    # Fold the per-description groups into one entry per product. The retailer
    # spells the same UPC differently between visits; the commonest spelling is
    # the honest label, which is the rule `price_history` already follows.
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = merged.setdefault(
            row["upc"],
            {
                "upc": row["upc"],
                "names": defaultdict(int),
                "purchases": 0,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            },
        )
        entry["names"][row["description_raw"]] += row["purchases"]
        entry["purchases"] += row["purchases"]
        entry["first_seen"] = min(entry["first_seen"], row["first_seen"])
        entry["last_seen"] = max(entry["last_seen"], row["last_seen"])

    coverage_end = max((e["last_seen"] for e in merged.values()), default=None)
    stale_before = _minus_days(coverage_end, STALE_AFTER_DAYS) if coverage_end else None

    products = []
    for entry in merged.values():
        name = max(entry["names"].items(), key=lambda kv: kv[1])[0]
        products.append(
            {
                "upc": entry["upc"],
                "description": name,
                "purchases": entry["purchases"],
                "tier": _tier(entry["purchases"]),
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                # Bought more than once and then not again for half a year
                # before the window closed. The second-purchase condition is
                # what keeps this an observation about a habit rather than a
                # label slapped on every product anyone ever tried once — and
                # two thirds of them were tried once.
                "stopped": bool(
                    stale_before
                    and entry["purchases"] >= 2
                    and entry["last_seen"] < stale_before
                ),
            }
        )

    total_products = len(products)
    bought_once_total = sum(1 for p in products if p["purchases"] == 1)

    if query:
        # Matched in Python rather than in SQL, because the counts have already
        # been aggregated and re-running the group with a LIKE would change
        # which rows form the totals. Substring matching on a list this size is
        # free, and it sidesteps LIKE's wildcards entirely rather than escaping
        # them: there is no pattern here for "%" or "_" to be special in, so
        # typing either matches products containing that character, which is
        # what someone typing it into a search box meant.
        needle = query.strip().lower()
        products = [
            p
            for p in products
            if needle in p["description"].lower() or needle in p["upc"].lower()
        ]
    if min_purchases > 1:
        products = [p for p in products if p["purchases"] >= min_purchases]

    # Alphabetical, with the UPC breaking ties so the order is total and the
    # same filter always renders the same page.
    products.sort(key=lambda p: (p["description"], p["upc"]))

    return {
        "disclosed": disclosed_specific_pieces(conn, request_id),
        # Unfiltered, because the headline figure is a fact about the response
        # and not about whatever is currently typed in the filter box.
        "total_products": total_products,
        "bought_once_total": bought_once_total,
        "product_count": len(products),
        "bought_once": sum(1 for p in products if p["purchases"] == 1),
        "min_purchases": min_purchases,
        "coverage_end": coverage_end,
        "stale_before": stale_before,
        "stopped_count": sum(1 for p in products if p["stopped"]),
        # Populations per tier, so the legend can say what a size means without
        # the frontend recomputing it.
        "tiers": [
            {
                "tier": tier,
                "min_purchases": floor,
                "count": sum(1 for p in products if p["tier"] == tier),
            }
            for tier, floor in PURCHASE_TIERS
        ],
        "truncated": len(products) > limit,
        "limit": limit,
        "products": products[:limit],
    }


def _minus_days(iso_date: str, days: int) -> str:
    """`YYYY-MM-DD` minus n days, without dragging in a timezone.

    Dates in this response are store-local wall clock with no offset, so they
    are compared as strings everywhere else in this module and are handled the
    same way here.
    """
    from datetime import date, timedelta

    year, month, day = (int(part) for part in iso_date.split("-"))
    return (date(year, month, day) - timedelta(days=days)).isoformat()
