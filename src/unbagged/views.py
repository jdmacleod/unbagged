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
from typing import Any

from unbagged.models import DisclosureCategory, DisclosureStatus, InferenceOrigin


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
        where.append(
            "EXISTS (SELECT 1 FROM txn_item i WHERE i.txn_id = t.id"
            " AND (i.description_raw LIKE ? OR i.upc LIKE ?))"
        )
        params.extend([f"%{query}%", f"%{query}%"])

    clause = " AND ".join(where)
    baskets = _rows(
        conn,
        f"""
        SELECT t.id, t.occurred_at, t.external_order_id, t.store_code, t.division_code,
               t.channel, t.tender_type, t.total_pre_discount,
               t.source_document_id, t.page, t.locator,
               COUNT(i.id) AS item_count,
               COALESCE(SUM(i.retail_amt), 0) AS items_total,
               COALESCE(SUM(i.loyalty_amt), 0) AS loyalty_total
        FROM txn t LEFT JOIN txn_item i ON i.txn_id = t.id
        WHERE {clause}
        GROUP BY t.id
        ORDER BY t.occurred_at, t.id
        """,
        tuple(params),
    )
    for basket in baskets:
        basket["provenance"] = _provenance(basket)

    return {"stats": stats(conn, request_id), "filtered_count": len(baskets),
            "baskets": baskets}


def stats(conn: sqlite3.Connection, request_id: int) -> dict[str, Any]:
    """Header numbers for the timeline.

    `distinct_products` counts UPCs on lines that cost something, and
    `zero_value_lines` is reported separately rather than folded in. Kroger's
    export is full of placeholder rows naming no product at zero cost; counting
    them as products would inflate the number, and hiding them would conceal a
    fact about the quality of the disclosure.
    """
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS basket_count,
               MIN(t.occurred_at) AS first_visit,
               MAX(t.occurred_at) AS last_visit,
               COALESCE(SUM(CASE WHEN i.retail_amt IS NOT NULL THEN i.retail_amt END), 0)
                   AS total_spend,
               COALESCE(SUM(i.loyalty_amt), 0) AS total_loyalty_discount,
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
            "basket_count", "total_spend", "total_loyalty_discount", "line_count",
            "distinct_products", "zero_value_lines", "negative_lines",
            "first_visit", "last_visit",
        ):
            result[key] = None
    return result


def transaction_detail(conn: sqlite3.Connection, txn_id: int) -> dict[str, Any] | None:
    """One basket with its line items, for the drill-down.

    Both amounts travel together so the discount delta is visible per line, which
    is the thing a receipt never shows you.
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
        item["net_amt"] = None if retail is None else round(retail - (loyalty or 0), 2)
    basket["items"] = items
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
        request["total_spend"] = summary["total_spend"]
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

    A personal inflation series, which the data contains for free. Returns and
    voids are excluded here — a negative amount is a refund, not a price — but
    they remain in the transaction record.
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
        entry["points"].append(
            {"date": row["on_date"], "retail_amt": row["retail_amt"],
             "loyalty_amt": row["loyalty_amt"]}
        )

    products = []
    for entry in series.values():
        points = entry["points"]
        if len(points) < min_observations:
            continue
        first, last = points[0]["retail_amt"], points[-1]["retail_amt"]
        products.append(
            {
                "upc": entry["upc"],
                # The description can vary between visits; the commonest one is
                # the honest label, and the raw values stay reachable per point.
                "description": max(entry["descriptions"].items(), key=lambda kv: kv[1])[0],
                "observations": len(points),
                "first_seen": points[0]["date"],
                "last_seen": points[-1]["date"],
                "first_price": first,
                "last_price": last,
                "min_price": min(p["retail_amt"] for p in points),
                "max_price": max(p["retail_amt"] for p in points),
                "change_pct": round((last - first) / first * 100, 1) if first else None,
                "points": points,
            }
        )

    products.sort(key=lambda p: (-p["observations"], p["description"]))
    return {
        "min_observations": min_observations,
        "product_count": len(products),
        "products": products[:limit],
    }
