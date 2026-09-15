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
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

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
from unbagged.adapters.hmart import receipt as rc
from unbagged.extraction import Table, classify, extract, extract_all
from unbagged.models import TxnItem
from unbagged.transcription import OcrUnavailable, transcribe

RETAILER_ID = "hmart"
DISPLAY_NAME = "H Mart"

#: 2: the response grew a second half.
#:
#: The first was a points statement and nothing else, so every visit had a
#: total and no contents. A later reply added screen captures of the receipt
#: viewer, one or two per visit, which itemise 44 of the 67. Transactions
#: parsed under version 1 carry no line items and cannot acquire any; the bump
#: records that a basket read by this adapter now means something different.
SCHEMA_VERSION = 2

#: Confidence for a bundle of captures with no spreadsheet beside them.
#:
#: Well under the 0.9 a matching header row earns, and deliberately so: that is
#: a positive identification and this is a filename convention. It is enough to
#: beat the generic fallback, which is the decision that actually matters —
#: an image yields no text, so the fallback scores nothing on one and a bundle
#: of captures would otherwise be refused outright rather than read.
CAPTURE_CONFIDENCE = 0.4

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


def _captures(bundle: SourceBundle) -> list:
    """The documents in this bundle that are captures of a receipt.

    Identified by being an image AND by carrying a date in the filename the
    store's viewer puts there. Both, not either: "it is a PNG" says nothing
    about which retailer sent it, and a name alone says nothing about what is
    in the file.

    Cheap enough for `sniff` on purpose — it reads the first sixteen bytes of
    each file and no more. Transcribing one costs the better part of a second,
    and `sniff` is called on every adapter for every upload.
    """
    from pathlib import Path

    found = []
    for document in bundle.documents:
        if not document.path or rc.capture_date(document.original_filename) is None:
            continue
        try:
            if classify(document, Path(document.path)) == "image":
                found.append(document)
        except OSError:
            continue
    return found


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
        return CAPTURE_CONFIDENCE if _captures(bundle) else 0.0

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
        # A capture is excluded from this accounting, not exempted from it: it
        # is read by `_itemise` further down, which raises its own warning when
        # it cannot be. Without the exclusion every capture in the bundle was
        # reported as unreadable — 46 warnings saying a PNG is not a
        # spreadsheet, and the one warning that mattered, about the receipt
        # that genuinely could not be read, sitting at the bottom of them.
        readable = {document.filename for document in extracted}
        readable |= {document.original_filename for document in _captures(bundle)}
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
                    cards[card] = _provenance(table, row.number, columns["smartcard"], document_id)

        # Sorted, because a bundle of several files arrives in upload order and
        # a timeline built from it would otherwise jump between them.
        transactions.sort(key=lambda t: t.occurred_at)
        transactions = _itemise(bundle, transactions, warnings)

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
                    max(t.occurred_at for t in transactions)[:10] if transactions else None
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
            disclosures=_disclosures(transactions, provenance, _captures(bundle)),
            follow_ups=_follow_ups(),
            warnings=warnings.as_tuple(),
        )


def _itemise(
    bundle: SourceBundle,
    transactions: list[Transaction],
    warnings: WarningCollector,
) -> list[Transaction]:
    """Fill in what was in each basket, from the captures that show it.

    The response arrived in two parts, and they answer different questions: the
    spreadsheet says what every visit cost, the captures say what 44 of them
    contained. Joined here rather than left as two responses, because they are
    one retailer's answer to one request and a reader comparing them by hand is
    work this is for.

    **Nothing is attached to a basket unless the receipt it came from adds up.**
    A capture is read by a machine looking at 10px type, and the one check that
    does not come from the same machine is the total the receipt prints on
    itself. A receipt that does not reconcile is named in a warning and its
    visit keeps the total-only row it already had.

    That rule is doing more than it looks. The timeline already marks a basket
    whose lines miss its stated total, and tells the reader the difference is
    in the response as supplied rather than in how it was read. Letting an
    unreconciled receipt through would make that sentence false, and turn this
    tool's own misreading into a finding against the retailer.
    """
    captures = _captures(bundle)
    if not captures:
        return transactions

    engine = None
    by_name = {document.original_filename: document for document in captures}
    itemised: dict[int, Transaction] = {}
    unmatched: list[rc.Receipt] = []

    for names in rc.group_by_visit(list(by_name)):
        try:
            receipts = _read_visit([by_name[name] for name in names])
        except OcrUnavailable as exc:
            # One message for the whole upload, not one per capture: the engine
            # is either there or it is not, and 46 copies of that is not more
            # informative than one.
            engine = engine or str(exc)
            continue
        for receipt in receipts:
            short = rc.foots(receipt)
            if short is not None:
                warnings.add(_unreconciled(receipt, short), locator=receipt.captures[0])
                continue
            index = _match(receipt, transactions, itemised)
            if index is None:
                unmatched.append(receipt)
                continue
            disagreement = _disagrees(transactions[index], receipt)
            if disagreement is not None:
                warnings.add(disagreement, locator=receipt.captures[0])
                continue
            itemised[index] = _with_items(transactions[index], receipt, by_name)

    if engine:
        warnings.error(
            f"{len(captures)} receipt captures were part of this response and "
            f"none could be read. {engine} Every visit still carries the total "
            "the points statement gave for it."
        )
    for receipt in unmatched:
        warnings.add(_unmatched(receipt), locator=receipt.captures[0])

    return [itemised.get(index, txn) for index, txn in enumerate(transactions)]


def _read_visit(documents: list) -> list[rc.Receipt]:
    """The receipts one group of captures holds — usually one, sometimes two.

    Two captures of one date are the halves of a tall receipt about as often as
    they are two trips to the shop, and the filenames do not distinguish them.
    A capture that reached the bottom of its page is a receipt on its own; a
    capture that ran off the edge is half of one.
    """
    from pathlib import Path

    parts = [
        rc.read_capture(transcribe(Path(document.path).read_bytes()), document.original_filename)
        for document in documents
    ]
    if len(parts) > 1 and all(part.complete for part in parts):
        return parts
    return [rc.stitch(parts)]


def _match(
    receipt: rc.Receipt,
    transactions: list[Transaction],
    taken: dict[int, Transaction],
) -> int | None:
    """Which visit this receipt is of, or None.

    The timestamp the receipt prints matches the points statement to the
    second, so that is the join. It is also the least legible line on the page
    — a digit of it comes back wrong on about a ninth of the real captures — so
    where it cannot be read, the date in the capture's filename and the
    receipt's own subtotal stand in for it together.

    Not the subtotal alone. Two visits to the same shop for the same basket is
    an ordinary thing, and matching on a number that repeats would attach a
    receipt to the wrong day rather than to none.
    """
    for index, txn in enumerate(transactions):
        if index in taken:
            continue
        if receipt.stamp and txn.occurred_at == receipt.stamp.occurred_at:
            return index
    date = rc.capture_date(receipt.captures[0])
    if date is None:
        return None
    for index, txn in enumerate(transactions):
        if index in taken or not txn.occurred_at.startswith(date):
            continue
        if txn.total_pre_discount is not None and _close(
            Decimal(str(txn.total_pre_discount)), receipt.subtotal
        ):
            return index
    return None


def _disagrees(txn: Transaction, receipt: rc.Receipt) -> str | None:
    """Do the two halves of the response agree about what this visit cost?

    The receipt reconciles against itself by the time this is asked, and the
    statement is a separate document written by a separate system. So this is
    the one check that compares the two against each other, and it is the
    strongest of the three: a misreading would have to survive the receipt's
    own total AND land on the figure the statement independently reports.

    It is not redundant with the join. A receipt matched on its timestamp is
    matched on the timestamp alone — that is what makes the timestamp a good
    key — so nothing has yet compared the money. Without this, a capture whose
    lines were misread into a self-consistent basket attached to the right
    visit and left the timeline showing a basket "under by" the difference,
    with a note telling the reader that difference was in the response as
    supplied. It would have been in how it was read.

    The statement reports a visit's PRE-tax total, which is what the receipt's
    lines sum to. Comparing against the receipt's balance instead fails on
    every visit that paid any tax.
    """
    if txn.total_pre_discount is None:
        return None
    stated = Decimal(str(txn.total_pre_discount))
    if _close(stated, receipt.subtotal):
        return None
    where = " and ".join(receipt.captures)
    return (
        f"{where} reads as a basket of {receipt.subtotal}, and the points "
        f"statement gives {stated} for the visit it belongs to. The two halves "
        "of this response disagree about what this visit cost, so its contents "
        "have not been stored; the statement's total stands."
    )


def _close(stated: Decimal, read: Decimal) -> bool:
    """Equal to the cent.

    Not a tolerance. Both figures are money that was read exactly, and a join
    that accepts "nearly" is a join that will one day attach a basket to the
    wrong visit and leave nothing on screen to show it.
    """
    return stated == read


def _with_items(txn: Transaction, receipt: rc.Receipt, by_name: dict) -> Transaction:
    """The same visit, with what was in it.

    `total_pre_discount` is left as the points statement gave it — the pre-tax
    subtotal, which is what the lines sum to, so the timeline's own footing
    check reports no difference. The receipt's balance includes tax, and the
    schema has nowhere to put tax; recorded in NOTES.md rather than rounded
    into a line that was never on the receipt.
    """
    document = by_name.get(receipt.captures[0])
    return replace(
        txn,
        items=tuple(
            TxnItem(
                description_raw=line.description,
                quantity=float(line.quantity) if line.quantity is not None else None,
                retail_amt=float(line.amount),
                # Left None throughout. A weight discount and a cancellation are
                # their own negative lines on this receipt, the way a return is
                # in a Kroger export — folding either into a loyalty price is
                # the failure `models.py` spends thirty lines warning about.
                loyalty_amt=None,
            )
            for line in receipt.lines
        ),
        tender_type=receipt.tender or txn.tender_type,
        division_code=receipt.stamp.lane if receipt.stamp else txn.division_code,
        external_order_id=receipt.stamp.number if receipt.stamp else txn.external_order_id,
        provenance=Provenance(
            source_document_id=document.id if document else None,
            page=1,
            locator=" + ".join(receipt.captures),
        ),
    )


def _unreconciled(receipt: rc.Receipt, short: Decimal) -> str:
    where = " and ".join(receipt.captures)
    return (
        f"{where} could not be read into a basket that adds up: its lines come "
        f"to {short:+} against the total printed on the receipt. Nothing from "
        "it has been stored, because a basket read wrongly is worse than one "
        "not read at all. The visit still carries the total the points "
        "statement gave for it."
    )


def _unmatched(receipt: rc.Receipt) -> str:
    where = " and ".join(receipt.captures)
    return (
        f"{where} is a receipt that adds up, and no visit in the points "
        "statement matches its date and total. Its contents have not been "
        "stored: adding a visit the statement does not list would count a trip "
        "to the shop twice if the two are the same one."
    )


def _provenance(table: Table, row: int, column: int, document_id: int | None) -> Provenance:
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
                f"The sheet declares {table.declared_columns} columns and a row reaches {widest}.",
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
        provenance=_provenance(table, row.number, columns["amount"], document_id),
    )


def _timestamp(value: str | None) -> str | None:
    """`2019-03-04 11:07:00.0` to `2019-03-04T11:07:00`.

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
    captures: list | None = None,
) -> tuple[Disclosure, ...]:
    """What the response addressed, and what it did not.

    `SPECIFIC_PIECES` is PARTIAL, and the choice is load-bearing. The response
    did disclose specific pieces of personal information — a card number, every
    visit's date, store and total, and now the contents of some of those
    baskets — so ABSENT would be false. It has still not disclosed the contents
    of all of them, so PROVIDED would be false too, and PROVIDED is the cell a
    compliance reader weighs most heavily. PARTIAL is the only one of the three
    that is true.

    It stays PARTIAL after the captures arrived, which is worth being explicit
    about: a second reply that itemises two thirds of the visits is more than
    was held before and is not the whole of what was asked for. `legal-basis.md`
    is direct about which way to resolve that — a category is never upgraded on
    inference, and the grade describes the response rather than the effort. What
    changes is the evidence, which now says how many of the visits were covered,
    because a reader can weigh two thirds and cannot weigh "partial".

    Everything else is ABSENT. There is no prose in the response at all.
    """
    total = len(transactions)
    with_items = sum(1 for txn in transactions if txn.items)
    if with_items:
        evidence = (
            f"{total} visits, each with a date, a store and a total. "
            f"{with_items} of them itemised, from {len(captures or ())} screen "
            "captures of the receipts."
        )
        notes = (
            "The response came in two parts: a points statement covering every "
            "visit, and captures of the receipt viewer covering some of them. "
            f"{total - with_items} visits still say what they cost and not what "
            "was in them."
        )
    else:
        evidence = f"{total} visits, each with a date, a store and a total. No line items."
        notes = (
            "The response says what each visit cost and never what was in "
            "it. Both halves are specific pieces of personal information; "
            "only one of them was disclosed."
        )
    found = {
        DisclosureCategory.SPECIFIC_PIECES: Disclosure(
            category=DisclosureCategory.SPECIFIC_PIECES,
            status=DisclosureStatus.PARTIAL,
            evidence=evidence,
            notes=notes,
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
