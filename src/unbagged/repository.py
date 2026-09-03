"""Persisting and reading back a ParseResult.

The write side is one function: `save_parse_result` takes what an adapter produced
and writes it as a single request, in one transaction. The read side is shaped by
the four views rather than by the tables, because the UI's questions ("what did
they infer, and where did it come from") do not map one-to-one onto rows.

Everything here is plain SQL. The point of the canonical schema is that this module
is the only place that knows about columns.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import replace

from unbagged.db import transaction
from unbagged.models import (
    Channel,
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    Identity,
    IdType,
    Inference,
    InferenceOrigin,
    ParseResult,
    ParseWarning,
    Provenance,
    RequestMeta,
    Scale,
    Scope,
    Severity,
    SourceDocument,
    Transaction,
    TxnItem,
)


def _enum(cls, value):
    """Read a stored TEXT value back as its enum, tolerating unknown values.

    A retailer inventing a new tender type should not make an existing database
    unreadable, so an unrecognised value comes back as the raw string rather than
    raising. The UI shows it; the adapter's NOTES.md explains it later.
    """
    if value is None:
        return None
    try:
        return cls(value)
    except ValueError:
        return value


def _provenance(row: sqlite3.Row) -> Provenance:
    keys = row.keys()
    return Provenance(
        source_document_id=row["source_document_id"] if "source_document_id" in keys else None,
        page=row["page"] if "page" in keys else None,
        locator=row["locator"] if "locator" in keys else None,
    )


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------


def insert_request(conn: sqlite3.Connection, meta: RequestMeta) -> int:
    cur = conn.execute(
        "INSERT INTO request (retailer_id, display_name, report_reference, submitted_at,"
        " received_at, statute, period_start, period_end, adapter_schema_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            meta.retailer_id, meta.display_name, meta.report_reference, meta.submitted_at,
            meta.received_at, meta.statute, meta.period_start, meta.period_end,
            meta.adapter_schema_version,
        ),
    )
    return int(cur.lastrowid)


def insert_documents(
    conn: sqlite3.Connection, request_id: int, documents: Iterable[SourceDocument]
) -> tuple[SourceDocument, ...]:
    """Store each document and return copies carrying their assigned ids.

    Adapters need those ids to fill in provenance, so ingestion must happen before
    parsing rather than alongside it.
    """
    stored = []
    for doc in documents:
        cur = conn.execute(
            "INSERT INTO source_document (request_id, original_filename, sha256,"
            " media_type, page_count) VALUES (?, ?, ?, ?, ?)",
            (request_id, doc.original_filename, doc.sha256, doc.media_type, doc.page_count),
        )
        stored.append(replace(doc, id=int(cur.lastrowid)))
    return tuple(stored)


def save_parse_result(
    conn: sqlite3.Connection,
    result: ParseResult,
    *,
    documents: Sequence[SourceDocument] = (),
) -> int:
    """Write a whole parse as one request, atomically.

    A parse that fails halfway must leave nothing behind: a partially written
    request is indistinguishable from a report that was genuinely that short.
    """
    with transaction(conn):
        request_id = insert_request(conn, result.request)
        if documents:
            insert_documents(conn, request_id, documents)

        conn.executemany(
            "INSERT INTO identity (request_id, id_type, value, scope, first_seen,"
            " source_document_id, page, locator) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    request_id, str(i.id_type), i.value,
                    str(i.scope) if i.scope else None, i.first_seen,
                    i.provenance.source_document_id, i.provenance.page,
                    i.provenance.locator,
                )
                for i in result.identities
            ],
        )

        for txn in result.transactions:
            cur = conn.execute(
                "INSERT INTO txn (request_id, external_order_id, occurred_at, store_code,"
                " division_code, channel, tender_type, total_pre_discount,"
                " source_document_id, page, locator)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id, txn.external_order_id, txn.occurred_at, txn.store_code,
                    txn.division_code, str(txn.channel) if txn.channel else None,
                    txn.tender_type, txn.total_pre_discount,
                    txn.provenance.source_document_id, txn.provenance.page,
                    txn.provenance.locator,
                ),
            )
            txn_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO txn_item (txn_id, description_raw, upc, quantity, retail_amt,"
                " loyalty_amt, category, category_confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        txn_id, it.description_raw, it.upc, it.quantity, it.retail_amt,
                        it.loyalty_amt, it.category, it.category_confidence,
                    )
                    for it in txn.items
                ],
            )

        conn.executemany(
            "INSERT INTO inference (request_id, label, value_raw, value_num, scale, subject,"
            " origin, derivable_from_txns, source_document_id, page, locator)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    request_id, f.label, f.value_raw, f.value_num,
                    str(f.scale) if f.scale else None,
                    str(f.subject) if f.subject else None,
                    str(f.origin),
                    None if f.derivable_from_txns is None else int(f.derivable_from_txns),
                    f.provenance.source_document_id, f.provenance.page,
                    f.provenance.locator,
                )
                for f in result.inferences
            ],
        )

        conn.executemany(
            "INSERT INTO disclosure (request_id, category, status, evidence, notes,"
            " source_document_id, page, locator) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    request_id, str(d.category), str(d.status), d.evidence, d.notes,
                    d.provenance.source_document_id, d.provenance.page,
                    d.provenance.locator,
                )
                for d in result.disclosures
            ],
        )

        conn.executemany(
            "INSERT INTO follow_up (request_id, kind, description, resolved)"
            " VALUES (?, ?, ?, ?)",
            [
                (request_id, str(f.kind), f.description, int(f.resolved))
                for f in result.follow_ups
            ],
        )

        conn.executemany(
            "INSERT INTO parse_warning (request_id, severity, message, locator)"
            " VALUES (?, ?, ?, ?)",
            [
                (request_id, str(w.severity), w.message, w.locator)
                for w in result.warnings
            ],
        )

    return request_id


def delete_request(conn: sqlite3.Connection, request_id: int) -> None:
    """Remove a request and everything hanging off it, via ON DELETE CASCADE."""
    with transaction(conn):
        conn.execute("DELETE FROM request WHERE id = ?", (request_id,))


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


def get_request(conn: sqlite3.Connection, request_id: int) -> RequestMeta | None:
    row = conn.execute("SELECT * FROM request WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        return None
    return RequestMeta(
        retailer_id=row["retailer_id"],
        display_name=row["display_name"],
        report_reference=row["report_reference"],
        submitted_at=row["submitted_at"],
        received_at=row["received_at"],
        statute=row["statute"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        adapter_schema_version=row["adapter_schema_version"],
    )


def list_requests(conn: sqlite3.Connection) -> list[tuple[int, RequestMeta]]:
    """Every request, oldest first. The compare view is empty-stated until this
    returns more than one row."""
    rows = conn.execute("SELECT id FROM request ORDER BY id")
    return [(r["id"], get_request(conn, r["id"])) for r in rows]


def get_documents(conn: sqlite3.Connection, request_id: int) -> list[SourceDocument]:
    rows = conn.execute(
        "SELECT * FROM source_document WHERE request_id = ? ORDER BY id", (request_id,)
    )
    return [
        SourceDocument(
            original_filename=r["original_filename"],
            sha256=r["sha256"],
            media_type=r["media_type"],
            page_count=r["page_count"],
            id=r["id"],
        )
        for r in rows
    ]


def get_identities(conn: sqlite3.Connection, request_id: int) -> list[Identity]:
    rows = conn.execute(
        "SELECT * FROM identity WHERE request_id = ? ORDER BY id", (request_id,)
    )
    return [
        Identity(
            id_type=_enum(IdType, r["id_type"]),
            value=r["value"],
            scope=_enum(Scope, r["scope"]),
            first_seen=r["first_seen"],
            provenance=_provenance(r),
        )
        for r in rows
    ]


def get_transactions(
    conn: sqlite3.Connection, request_id: int, *, with_items: bool = True
) -> list[Transaction]:
    rows = list(
        conn.execute(
            "SELECT * FROM txn WHERE request_id = ? ORDER BY occurred_at, id", (request_id,)
        )
    )
    items_by_txn: dict[int, list[TxnItem]] = {}
    if with_items and rows:
        item_rows = conn.execute(
            "SELECT i.* FROM txn_item i JOIN txn t ON t.id = i.txn_id"
            " WHERE t.request_id = ? ORDER BY i.id",
            (request_id,),
        )
        for ir in item_rows:
            items_by_txn.setdefault(ir["txn_id"], []).append(
                TxnItem(
                    description_raw=ir["description_raw"],
                    upc=ir["upc"],
                    quantity=ir["quantity"],
                    retail_amt=ir["retail_amt"],
                    loyalty_amt=ir["loyalty_amt"],
                    category=ir["category"],
                    category_confidence=ir["category_confidence"],
                )
            )
    return [
        Transaction(
            occurred_at=r["occurred_at"],
            items=tuple(items_by_txn.get(r["id"], ())),
            external_order_id=r["external_order_id"],
            store_code=r["store_code"],
            division_code=r["division_code"],
            channel=_enum(Channel, r["channel"]),
            tender_type=r["tender_type"],
            total_pre_discount=r["total_pre_discount"],
            provenance=_provenance(r),
        )
        for r in rows
    ]


def get_inferences(conn: sqlite3.Connection, request_id: int) -> list[Inference]:
    rows = conn.execute(
        "SELECT * FROM inference WHERE request_id = ? ORDER BY id", (request_id,)
    )
    return [
        Inference(
            label=r["label"],
            value_raw=r["value_raw"],
            origin=_enum(InferenceOrigin, r["origin"]),
            value_num=r["value_num"],
            scale=_enum(Scale, r["scale"]),
            subject=_enum(Scope, r["subject"]),
            derivable_from_txns=(
                None if r["derivable_from_txns"] is None else bool(r["derivable_from_txns"])
            ),
            provenance=_provenance(r),
        )
        for r in rows
    ]


def get_disclosures(conn: sqlite3.Connection, request_id: int) -> list[Disclosure]:
    rows = conn.execute(
        "SELECT * FROM disclosure WHERE request_id = ? ORDER BY id", (request_id,)
    )
    return [
        Disclosure(
            category=_enum(DisclosureCategory, r["category"]),
            status=_enum(DisclosureStatus, r["status"]),
            evidence=r["evidence"],
            notes=r["notes"],
            provenance=_provenance(r),
        )
        for r in rows
    ]


def get_follow_ups(conn: sqlite3.Connection, request_id: int) -> list[FollowUpAction]:
    rows = conn.execute(
        "SELECT * FROM follow_up WHERE request_id = ? ORDER BY id", (request_id,)
    )
    return [
        FollowUpAction(
            kind=_enum(FollowUpKind, r["kind"]),
            description=r["description"],
            resolved=bool(r["resolved"]),
        )
        for r in rows
    ]


def get_warnings(conn: sqlite3.Connection, request_id: int) -> list[ParseWarning]:
    rows = conn.execute(
        "SELECT * FROM parse_warning WHERE request_id = ? ORDER BY id", (request_id,)
    )
    return [
        ParseWarning(
            message=r["message"],
            severity=_enum(Severity, r["severity"]),
            locator=r["locator"],
        )
        for r in rows
    ]


def load_parse_result(conn: sqlite3.Connection, request_id: int) -> ParseResult | None:
    """Reassemble everything stored for one request.

    This is the round-trip counterpart to save_parse_result, and the acceptance
    criterion for M1: what an adapter produced is what comes back.
    """
    meta = get_request(conn, request_id)
    if meta is None:
        return None
    return ParseResult(
        request=meta,
        identities=tuple(get_identities(conn, request_id)),
        transactions=tuple(get_transactions(conn, request_id)),
        inferences=tuple(get_inferences(conn, request_id)),
        disclosures=tuple(get_disclosures(conn, request_id)),
        follow_ups=tuple(get_follow_ups(conn, request_id)),
        warnings=tuple(get_warnings(conn, request_id)),
    )
