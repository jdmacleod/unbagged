"""H Mart response adapter.

Everything here is derived from one real response. `NOTES.md` records what was
observed and why each judgment call was made; this file implements it. Where the
two disagree, the notes are the specification.

The interesting thing about this retailer is what it *did not* send. A
right-to-know request was answered with a loyalty points statement: 67 basket
totals over 79 months, a date, a store, an amount, and nothing else. No line
items, no prose, and nothing addressing any of the eight disclosure categories.
So the adapter's real job is to record a shape the model had never met — a
response that discloses what a visit cost without disclosing what was in it —
and to say so plainly rather than rendering it as a smaller Kroger.
"""

from __future__ import annotations

import logging
from datetime import datetime

from unbagged.adapters.base import (
    AdapterError,
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    Identity,
    IdType,
    ParseResult,
    Provenance,
    RequestMeta,
    Severity,
    SniffResult,
    SourceBundle,
    Transaction,
    WarningCollector,
    absent_disclosures,
)
from unbagged.extraction import Table, extract, extract_all

RETAILER_ID = "hmart"
DISPLAY_NAME = "H Mart"
SCHEMA_VERSION = 1

log = logging.getLogger(__name__)

#: Rows read while deciding whether this adapter owns a bundle: the banner, the
#: header, and one line of data.
SNIFF_ROWS = 3

#: Matched case-insensitively and whitespace-normalised, because a header is a
#: label a person typed and neither is worth failing over. Everything else about
#: the header is not negotiable — see `_columns`.
EXPECTED = ("smartcard", "date of purchase", "branch", "amount", "point")

#: `Timestamp.toString()` from a Java process. The trailing `.0` is fractional
#: seconds; the seconds themselves were `00` on every row observed, so the real
#: resolution is minutes.
STAMP = "%Y-%m-%d %H:%M:%S.%f"


def _normalise(value: str | None) -> str:
    return " ".join((value or "").split()).lower()


def _columns(table: Table) -> dict[str, int] | None:
    """Map each expected header to the 1-based column it sits in.

    **By name, never by position.** A column that moves or is renamed then makes
    this return None and the adapter decline, rather than reading the next
    column along. The alternative is silent: `Point` is `Amount` rounded to the
    nearest whole number, so a swap of those two shifts every basket total by
    less than a dollar, which nothing on screen would ever show.

    The header is looked for in the first few rows because the export puts a
    single-cell banner above it.
    """
    for row in table.rows[:SNIFF_ROWS]:
        seen = {_normalise(cell): index for index, cell in enumerate(row.cells, 1)}
        if all(header in seen for header in EXPECTED):
            return {header: seen[header] for header in EXPECTED}
    return None


class HMartAdapter:
    retailer_id = RETAILER_ID
    display_name = DISPLAY_NAME
    schema_version = SCHEMA_VERSION

    def sniff(self, bundle: SourceBundle) -> float | SniffResult:
        """Claim a spreadsheet whose header row is the one that was observed.

        Bounded: `max_pages` bounds rows for a tabular document, so this reads
        three of them rather than the whole file. If the read spends its byte
        budget before reaching a row at all, that is reported as a reason rather
        than as a bare zero — a budget spent looking is not the same answer as
        "this is not my format", and the person holding the file is the one who
        cannot tell those apart.
        """
        for document in bundle.documents:
            try:
                extracted = extract(document, max_pages=SNIFF_ROWS)
            except Exception:
                # A sniff must not raise, and a document this adapter cannot
                # read is simply not its business. Logged rather than swallowed:
                # a format change shows up here first, and a silent continue
                # would make that invisible.
                log.debug("hmart sniff could not read %s", document.original_filename)
                continue
            # Checked before the empty-tables guard, not after: a read that
            # spent its budget has no tables by definition, so the guard would
            # swallow exactly the case the reason exists for.
            if extracted.spent_budget:
                return SniffResult(
                    0.0,
                    "This spreadsheet's preamble was larger than the "
                    "quarter-megabyte this looks at, so no header row was "
                    "reached. It has not been ruled out; it has not been read.",
                )
            if not extracted.tables:
                continue
            for table in extracted.tables:
                if _columns(table):
                    return 0.9
        return 0.0

    def parse(self, bundle: SourceBundle) -> ParseResult:
        """Read every sheet that carries the columns, in every document.

        Not just the first. `sniff()` claims a bundle if ANY document has a
        matching sheet, so reading only `documents[0]` made an accepted response
        fail whenever something else was uploaded alongside it — and, worse,
        silently dropped the rest of a response split across files or sheets.
        Half a history disappearing without a warning is the failure this whole
        adapter is careful about; it does not get an exception for its own
        entry point.
        """
        warnings = WarningCollector()
        extracted = extract_all(bundle.documents)
        documents = [document for document in extracted if document.tables]

        # Every file the person uploaded is accounted for, out loud.
        #
        # `extract_all` drops a document it cannot read and logs it server-side,
        # and the filter above drops one that read but holds no sheets. Both are
        # reasonable on their own and both are invisible: the report then looks
        # complete while part of the upload is missing from it. Which file, and
        # why, is the reader's business — they are the only person who can say
        # whether the missing one mattered.
        readable = {document.filename for document in extracted}
        for source in bundle.documents:
            if source.original_filename not in readable:
                warnings.add(
                    f"{source.original_filename} could not be read at all, so "
                    "nothing from it is in this report.",
                    locator=source.original_filename,
                )
        for document in extracted:
            if not document.tables:
                warnings.add(
                    f"{document.filename} was read but holds no spreadsheet, so "
                    "nothing from it is in this report.",
                    locator=document.filename,
                )

        if not documents:
            raise AdapterError(
                "None of the uploaded files could be read as a spreadsheet. The "
                "export this reads is the XML kind: in Excel choose Save As and "
                "pick XML Spreadsheet 2003, or export CSV."
            )

        # (table, its column map, the document it came from), for every sheet
        # that carries the header — across every file in the bundle.
        matched: list[tuple[Table, dict[str, int], int | None]] = []
        skipped = 0
        for document in documents:
            for table in document.tables:
                columns = _columns(table)
                if columns is None:
                    skipped += 1
                    continue
                matched.append((table, columns, document.document_id))

        if not matched:
            raise AdapterError(
                "This spreadsheet does not carry the columns an H Mart export "
                "has. If the format has changed, the response is still worth "
                "keeping — see docs/writing-an-adapter.md."
            )

        if skipped:
            # Named rather than passed over: a sheet nobody read is a gap in the
            # archive, and the reader is the one who can say whether it mattered.
            warnings.info(
                f"{skipped} sheet(s) in this upload did not carry the H Mart "
                "columns and were not read."
            )

        transactions: list[Transaction] = []
        cards: dict[str, Provenance] = {}
        for table, columns, document_id in matched:
            _check_declared(table, warnings)
            header_row = next(
                row.number
                for row in table.rows[:SNIFF_ROWS]
                if all(h in {_normalise(c) for c in row.cells} for h in EXPECTED)
            )
            for row in table.rows:
                if row.number <= header_row:
                    continue
                txn = _transaction(table, row, columns, document_id, warnings)
                if txn is not None:
                    transactions.append(txn)
                card = row.value(columns["smartcard"])
                if card and card not in cards:
                    cards[card] = _provenance(
                        table, row.number, columns["smartcard"], document_id
                    )

        # Sorted, because a bundle of several files arrives in upload order and
        # a timeline built from it would otherwise jump between them.
        transactions.sort(key=lambda t: t.occurred_at)

        if not transactions:
            warnings.add(
                "The spreadsheet carried a header row and no readable purchases. "
                "An export with nothing in it is a finding about the response, "
                "not an error in reading it.",
                severity=Severity.WARNING,
            )

        table, _, document_id = matched[0]
        header_row = next(
            row.number
            for row in table.rows[:SNIFF_ROWS]
            if all(h in {_normalise(c) for c in row.cells} for h in EXPECTED)
        )

        first = min((t.occurred_at for t in transactions), default=None)
        provenance = _provenance(table, header_row, 1, document_id)

        return ParseResult(
            request=RequestMeta(
                retailer_id=RETAILER_ID,
                display_name=DISPLAY_NAME,
                period_start=first[:10] if first else None,
                period_end=(
                    max(t.occurred_at for t in transactions)[:10]
                    if transactions
                    else None
                ),
                adapter_schema_version=SCHEMA_VERSION,
            ),
            identities=tuple(
                Identity(
                    id_type=IdType.LOYALTY_CARD,
                    value=card,
                    # Not INDIVIDUAL. The response never says who a card belongs
                    # to, and the retailer's published terms say one card per
                    # household — which points the other way. Recording a scope
                    # the response did not state would be inventing one.
                    scope=None,
                    first_seen=first,
                    provenance=where,
                )
                for card, where in cards.items()
            ),
            transactions=tuple(transactions),
            disclosures=_disclosures(transactions, provenance),
            follow_ups=_follow_ups(),
            warnings=warnings.as_tuple(),
        )


def _provenance(
    table: Table, row: int, column: int, document_id: int | None
) -> Provenance:
    """Where a value came from.

    `page` is None and stays None: a spreadsheet has no printed pages, and
    inventing a 1 would cite a page that does not exist. The locator carries the
    weight instead, as an A1 cell reference — which is what a person sees when
    they open the file, and what `docs/writing-an-adapter.md` has always
    promised adapters could use.
    """
    return Provenance(
        source_document_id=document_id,
        page=None,
        locator=table.locator(row, column),
    )


def _check_declared(table: Table, warnings: WarningCollector) -> None:
    """The sheet states its own size; disagreeing with it is worth saying.

    Free, because the attributes are already read. It is the only check
    available on whether the reader lost rows, and a reader that loses rows
    silently is the failure this whole file is careful about.

    Compared against the highest row number rather than the number of row
    elements. `ExpandedRowCount` counts the sheet as expanded, including rows
    that were skipped rather than written — so counting elements reports a
    shortfall on any sheet with a gap in it, which is a legal shape and not a
    fault. Same for columns against the widest row.
    """
    if table.declared_rows is not None and table.rows:
        highest = max(row.number for row in table.rows)
        if table.declared_rows != highest:
            warnings.info(
                f"The sheet declares {table.declared_rows} rows and the last one "
                f"read is numbered {highest}.",
                locator=table.name,
            )
    if table.declared_columns is not None and table.rows:
        widest = max(len(row) for row in table.rows)
        if widest > table.declared_columns:
            warnings.info(
                f"The sheet declares {table.declared_columns} columns and a row "
                f"reaches {widest}.",
                locator=table.name,
            )


def _transaction(
    table: Table,
    row,
    columns: dict[str, int],
    document_id: int | None,
    warnings: WarningCollector,
) -> Transaction | None:
    """One visit, or None when the row cannot be one.

    A row is short when a column the header named holds nothing — expressed in
    the header's columns rather than as a count, so it survives a column being
    added and does not mistake a legitimately sparse row for a broken one.
    """
    stamp = row.value(columns["date of purchase"])
    when = _timestamp(stamp)
    if when is None:
        # An undated visit cannot go on a timeline, and putting it somewhere
        # arbitrary would be worse than saying it was skipped.
        warnings.add(
            f"Skipped a row with no readable date ({stamp!r}).",
            locator=table.locator(row.number, columns["date of purchase"]),
        )
        return None

    amount = _amount(row.value(columns["amount"]))
    if amount is None:
        warnings.add(
            "A visit had no readable amount; it is kept, with no total.",
            locator=table.locator(row.number, columns["amount"]),
        )

    branch = row.value(columns["branch"])
    if branch is None:
        warnings.info(
            "A visit named no branch.",
            locator=table.locator(row.number, columns["branch"]),
        )

    return Transaction(
        occurred_at=when,
        items=(),
        # The response discloses no line items at all. Fabricating a single line
        # per basket would invent structure the retailer never sent.
        store_code=branch,
        # No channel field exists. Defaulting to in_store would be a claim the
        # data does not support — the same call the Kroger adapter makes.
        channel=None,
        total_pre_discount=amount,
        provenance=_provenance(
            table, row.number, columns["amount"], document_id
        ),
    )


def _timestamp(value: str | None) -> str | None:
    """`2020-01-18 09:59:00.0` to `2020-01-18T09:59:00`.

    Stored as a store-local wall clock with no zone, which is what the response
    gives. Stamping UTC on it would move an evening shop to the next day in
    every view. The canonical schema says timestamps are UTC; this is the
    documented exception and the Kroger adapter makes the same one.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), STAMP).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _amount(value: str | None) -> float | None:
    """Amounts arrive as strings, and not always with two decimal places."""
    if value is None:
        return None
    try:
        return float(value.strip().replace(",", "").lstrip("$"))
    except ValueError:
        return None


def _disclosures(
    transactions: tuple[Transaction, ...] | list[Transaction],
    provenance: Provenance,
) -> tuple[Disclosure, ...]:
    """What the response addressed, and what it did not.

    `SPECIFIC_PIECES` is PARTIAL, and the choice is load-bearing. The response
    did disclose specific pieces of personal information — a card number, and
    every visit's date, store and total — so ABSENT would be false. It disclosed
    nothing about what was in any basket, so PROVIDED would be false too, and
    PROVIDED is the cell a compliance reader weighs most heavily. PARTIAL is the
    only one of the three that is true.

    Everything else is ABSENT. There is no prose in the response at all.
    """
    found = {
        DisclosureCategory.SPECIFIC_PIECES: Disclosure(
            category=DisclosureCategory.SPECIFIC_PIECES,
            status=DisclosureStatus.PARTIAL,
            evidence=(
                f"{len(transactions)} visits, each with a date, a store and a "
                "total. No line items."
            ),
            notes=(
                "The response says what each visit cost and never what was in "
                "it. Both halves are specific pieces of personal information; "
                "only one of them was disclosed."
            ),
            provenance=provenance,
        )
    }
    return absent_disclosures(
        found,
        note=(
            "The response contains no prose at all — no sections, no headings, "
            "nothing addressing this category."
        ),
        provenance=provenance,
    )


def _follow_ups() -> tuple[FollowUpAction, ...]:
    """What is worth doing about it.

    The artifact-class note is the finding, and it is a stronger one than it
    first looked: the file has the shape of a self-service points statement,
    and it was sent in answer to a statutory access request. Recording that is
    not a hedge against the compliance matrix — it is the reason the matrix
    reads the way it does.
    """
    return (
        FollowUpAction(
            kind=FollowUpKind.CLARIFICATION,
            description=(
                "This response has the shape of a loyalty points statement: a "
                "date, a store and a total for each visit, with no line items "
                "and no prose. It was sent in answer to a right-to-know "
                "request, so the categories it does not address below are "
                "unaddressed by the response itself."
            ),
        ),
        FollowUpAction(
            kind=FollowUpKind.MISSING_CATEGORY,
            description=(
                "The retailer publishes a retention rule — an account is "
                "withdrawn after three years of inactivity and information is "
                "kept for a year afterwards — and the response does not state "
                "it. A policy that exists and was not given when asked is "
                "worth asking for directly."
            ),
        ),
    )


adapter = HMartAdapter()
