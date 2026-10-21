"""Albertsons Companies response adapter.

Everything here is derived from one real response. `NOTES.md` records what was
observed and why each judgment call was made; this file implements it. Where the
two disagree, the notes are the specification.

The response arrives as two files that say different things, and the adapter's
job is to read them as one:

* **a report PDF** — the request details, a loyalty profile, consent
  preferences and points events, as key/value tables;
* **a workbook** — every line item on the card, with a quantity and a weight,
  and a second sheet of attributes the retailer labels inferred.

Two shapes a response can take are worth reading for, and the adapter raises a
follow-up for each when it sees them: a loyalty profile registered to a
different first name than the request was made in, and a purchase history that
ends long before the request while the report dates later activity.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from unbagged.adapters.albertsons import report as rp
from unbagged.adapters.base import (
    AdapterError,
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
    Provenance,
    RequestMeta,
    Scale,
    Scope,
    SniffResult,
    SourceBundle,
    Transaction,
    TxnItem,
    WarningCollector,
    absent_disclosures,
)
from unbagged.extraction import ExtractedDocument, Row, Table, extract, extract_all

log = logging.getLogger(__name__)

RETAILER_ID = "albertsons"
DISPLAY_NAME = "Albertsons"

#: 1: the first format observed. The package was a stub at 0 until a real
#: response arrived, and was renamed from `safeway` when it did: the response is
#: issued by Albertsons Companies for every banner it runs, Safeway among them.
SCHEMA_VERSION = 1

#: Rows read while deciding whether this adapter owns a workbook: a title, a
#: blank row and the header, with two to spare. Pages, for the PDF.
SNIFF_ROWS = 5

#: The transaction sheet's header, in the order it was observed. Columns are
#: found by name, never by position.
TRANSACTION_HEADERS = (
    "TXN_DTE",
    "TXN_ID",
    "STORE_ID",
    "CARD_NBR",
    "CATEGORY_NM",
    "UPC_ID",
    "UPC_DSC",
    "QTY",
    "LBS",
    "NET",
)

#: The columns a line cannot be read without. The rest degrade to None.
REQUIRED_TRANSACTION = ("TXN_DTE", "TXN_ID", "UPC_DSC", "NET")

DEMOGRAPHIC_HEADERS = ("Related Card", "Inferred Description", "Value")

#: The report's data sections, as their headings read once undoubled.
VISITS = "Loyalty Store Visits Details"
PROFILE = "Loyalty Profile Details"
PREFERENCES = "Preference Details"
EARN_BURN = "Loyalty Earn Burn Details"
KNOWN_SECTIONS = (VISITS, PROFILE, PREFERENCES, EARN_BURN)

#: Values the profile prints for a field it holds nothing in. `I/ss` is what
#: the observed report printed for the formatted, maiden and nick names; what it
#: stands for is not stated, and it is read as absence rather than as a name.
PLACEHOLDERS = frozenset({"", "I/ss"})

#: Report fields that are identifiers, and what kind. `None` scope where the
#: response does not say whose: a card here is a household's card by the
#: response's own heading for the points table, and nothing says which member
#: of the household it belongs to.
IDENTIFIER_FIELDS: dict[str, tuple[IdType, Scope | None]] = {
    "Card_nbr": (IdType.LOYALTY_CARD, None),
    "Loyalty_program_card_nbr": (IdType.LOYALTY_CARD, None),
    "Customer_id": (IdType.INTERNAL_PERSON, Scope.INDIVIDUAL),
    "Alternate_id_txt": (IdType.ALTERNATE_ID, None),
    "Household_id": (IdType.HOUSEHOLD, Scope.HOUSEHOLD),
    "Phone": (IdType.PHONE, None),
    # Not printed in the observed report, but the row reader accepts it, and a
    # disclosed address dropped on the floor would sit beside a note saying
    # none was given.
    "Email": (IdType.EMAIL, None),
}

#: Identifiers inside a multi-line cell — the preference records.
RECORD_IDENTIFIERS: dict[str, tuple[IdType, Scope | None]] = {
    "os_cust_id": (IdType.ALTERNATE_ID, None),
}

#: Profile values the retailer computed from the visits it holds. First-party
#: by construction: "the store you used most" is a count over the baskets.
FIRST_PARTY_FIELDS = ("Customer_most_spend_store_nbr", "Customer_most_used_store_nbr")

#: Labels that say outright they describe a household or where it lives. Only
#: the label is read: the sheet does not say whom each attribute describes, and
#: a subject the label does not state is left unstated.
HOUSEHOLD_LABEL = re.compile(
    r"household|residence|dwelling|home (?:value|ownership)|median|census|county|"
    r"tract|block|metropolitan|adults|persons",
    re.IGNORECASE,
)

#: Attributes the baskets in this response could account for. A grocery basket
#: shows fresh produce, sunscreen and razors; it does not show a net worth.
DERIVABLE_LABEL = re.compile(r"heavy buyer of|health and beauty", re.IGNORECASE)

#: Attributes about a channel this response discloses nothing of. The retailer
#: runs online ordering, and no line here says whether it was online, so the
#: baskets cannot settle these either way.
UNSETTLED_LABEL = re.compile(r"internet|online", re.IGNORECASE)

#: Numeric attributes that are a count or a number of years.
COUNT_LABEL = re.compile(r"^age$|number of|num of|length of residence", re.IGNORECASE)

#: Sentences in the instructions worth quoting, and what they bear on.
CATEGORIES_SENTENCE = re.compile(r"categor(?:y|ies) of (?:personal )?(?:data|information)", re.I)
#: What the response says it left out: an exemption, another person's data, or
#: a plain "we do not include". Every matching sentence is quoted, in order.
WITHHELD_SENTENCE = re.compile(
    r"\bexempt|another (?:party|person)|do not include|not included", re.IGNORECASE
)

SUBMITTED = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")
SERIAL = re.compile(r"^\d+(?:\.\d+)?$")

#: Descriptions of an open-department ring: a register key, not a product, whose
#: code is shared by whatever was rung under it.
DEPARTMENT_RING = re.compile(r"\bDEP(?:ARTMEN)?T\.? KEY\b|^N/A$", re.IGNORECASE)

#: Codes this short are department and adjustment keys, not product codes.
#: Produce price-lookup codes are four or five digits and stay codes.
MIN_PRODUCT_CODE = 4

#: How many times one per-line warning is repeated before the rest are counted.
REPEATED_WARNINGS = 5


def _normalise(value: str | None) -> str:
    return " ".join((value or "").split()).lower()


def _columns(table: Table, expected: tuple[str, ...], required: tuple[str, ...]):
    """`({header: column}, header row number)` or None.

    By name, never by position, looked for in the first few rows because the
    transaction sheet puts a title and a blank row above its header.
    """
    for row in table.rows[:SNIFF_ROWS]:
        seen = {_normalise(cell): index for index, cell in enumerate(row.cells, 1) if cell}
        if all(_normalise(header) in seen for header in required):
            columns = {h: seen[_normalise(h)] for h in expected if _normalise(h) in seen}
            return columns, row.number
    return None


def _is_report(pages: tuple[str, ...]) -> bool:
    titles = {section.title for section in rp.read(pages).sections}
    return len(titles & set(KNOWN_SECTIONS)) >= 2


class AlbertsonsAdapter:
    retailer_id = RETAILER_ID
    display_name = DISPLAY_NAME
    schema_version = SCHEMA_VERSION

    def sniff(self, bundle: SourceBundle) -> float | SniffResult:
        """Claim either half of the response: the workbook's header or the
        report's section headings.

        Either is enough on its own, because either may be uploaded on its own
        and each is a positive identification. A budget spent before a header
        was reached is reported as a reason, not as a bare zero — but only if
        nothing else in the bundle settles it.
        """
        reason = None
        for document in bundle.documents:
            try:
                extracted = extract(document, max_pages=SNIFF_ROWS)
            except Exception:
                # sniff() must not raise. Logged, because a format change
                # shows up here first.
                log.debug("albertsons sniff could not read %s", document.original_filename)
                continue
            if extracted.spent_budget:
                reason = (
                    "This workbook's preamble was larger than the quarter-megabyte "
                    "this looks at, so no header row was reached. It has not been "
                    "ruled out; it has not been read."
                )
                continue
            for table in extracted.tables:
                if _columns(table, TRANSACTION_HEADERS, REQUIRED_TRANSACTION) or _columns(
                    table, DEMOGRAPHIC_HEADERS, DEMOGRAPHIC_HEADERS
                ):
                    return 0.9
            if extracted.pages and _is_report(extracted.pages):
                return 0.9
        return SniffResult(0.0, reason) if reason else 0.0

    def parse(self, bundle: SourceBundle) -> ParseResult:
        warnings = WarningCollector()
        extracted = extract_all(bundle.documents)

        # Every file the person uploaded is accounted for, out loud — the same
        # rule the H Mart adapter keeps, for the same reason: a report that
        # silently lacks half the upload looks complete.
        readable = {document.filename for document in extracted}
        for source in bundle.documents:
            if source.original_filename not in readable:
                warnings.add(
                    f"{source.original_filename} could not be read at all, so "
                    "nothing from it is in this report.",
                    locator=source.original_filename,
                )

        reports: list[ExtractedDocument] = []
        transaction_sheets: list[tuple[ExtractedDocument, Table, dict[str, int], int]] = []
        demographic_sheets: list[tuple[ExtractedDocument, Table, dict[str, int], int]] = []
        for document in extracted:
            used = False
            for table in document.tables:
                if found := _columns(table, TRANSACTION_HEADERS, REQUIRED_TRANSACTION):
                    transaction_sheets.append((document, table, *found))
                    used = True
                elif found := _columns(table, DEMOGRAPHIC_HEADERS, DEMOGRAPHIC_HEADERS):
                    demographic_sheets.append((document, table, *found))
                    used = True
                else:
                    warnings.info(
                        f"The sheet {table.name!r} in {document.filename} is not one "
                        "this adapter knows, and was not read.",
                        locator=f"{document.filename}: {table.name}",
                    )
            if not document.tables and _is_report(document.pages):
                reports.append(document)
                used = True
            if not used and not document.tables:
                warnings.info(
                    f"{document.filename} is not part of an Albertsons response as "
                    "far as this adapter can tell, and was not read.",
                    locator=document.filename,
                )

        if not reports and not transaction_sheets and not demographic_sheets:
            raise AdapterError(
                "None of the uploaded files look like an Albertsons response: no "
                "report with its loyalty tables, and no workbook with its "
                "transaction or demographic sheet. If the format has changed, the "
                "response is still worth keeping — see docs/writing-an-adapter.md."
            )

        source = reports[0] if reports else None
        report = rp.read(source.pages) if source else None
        if len(reports) > 1:
            warnings.info(
                f"{len(reports)} report PDFs were uploaded; only {source.filename} was read.",
                locator=source.filename,
            )
        if report is None:
            warnings.info(
                "No report PDF was in this upload, so the request details, the "
                "loyalty profile and its identifiers are not in this record, and "
                "the categories that depend on the report's prose are not graded."
            )
        else:
            self._check_report(report, source, warnings)
        if not transaction_sheets:
            warnings.add(
                "No purchase history was in this upload. Albertsons sends it as a "
                "separate workbook beside the report; if you received one, upload "
                "the two together."
            )

        def cite(field: rp.Field, where: str) -> Provenance:
            return Provenance(
                source_document_id=source.document_id if source else None,
                page=field.page,
                locator=f"{where} › {field.key}",
            )

        transactions, cards = _transactions(transaction_sheets, warnings)
        demographics = _demographics(demographic_sheets, warnings)
        identities = _identities(report, cite, cards, demographic_sheets)
        inferences = (_first_party(report, cite) if report else []) + demographics
        request = _request_meta(report, transactions)
        disclosures = _disclosures(
            report, source, transactions, inferences, transaction_sheets, identities
        )

        return ParseResult(
            request=request,
            identities=tuple(identities),
            transactions=tuple(transactions),
            inferences=tuple(inferences),
            disclosures=disclosures,
            follow_ups=_follow_ups(report, request, transactions, demographics, disclosures),
            warnings=warnings.as_tuple(),
        )

    @staticmethod
    def _check_report(
        report: rp.Report, source: ExtractedDocument, warnings: WarningCollector
    ) -> None:
        """Say what the report reader passed over, so a format change is loud."""
        for section in report.sections:
            if section.title not in KNOWN_SECTIONS:
                warnings.info(
                    f"The report has a section this adapter does not read: {section.title}.",
                    locator=f"{source.filename}, page {section.page}",
                )
        if report.unread:
            page = report.unread[0][0]
            warnings.info(
                f"{len(report.unread)} line(s) in the report's tables were not "
                "recognised as a field, and were not read.",
                locator=f"{source.filename}, page {page}",
            )
        if not report.request:
            warnings.info(
                "The report has no request-details block, so the reference "
                "number and the date submitted are not recorded."
            )


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------


def _present(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return None if text in PLACEHOLDERS else text


def _number(value: str | None) -> float | None:
    """A cell as a number, or None. Never guessed.

    The sheet stores amounts as binary doubles, so `1.1500000000000001` and
    `-4.1E-3` both occur and both mean what `float()` says they mean.
    Non-finite values are refused: `float("nan")` parses, and a NaN compares
    false against everything downstream.
    """
    if value is None:
        return None
    try:
        number = float(value.strip().replace(",", "").lstrip("$"))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _occurred_on(value: str | None, date1904: bool) -> str | None:
    """A date cell as `YYYY-MM-DD`, or with a clock when the cell has one.

    The observed sheet stores a day count — an Excel date serial — formatted
    to display as a date. A text date is also accepted, ISO or US order,
    because a re-saved workbook may carry either.

    **Date only, no clock.** The response gives a day and nothing else, and
    `T00:00:00` would claim a midnight it never said. The views take the first
    ten characters for a date and print no time when there is none.

    **Never a date that is not one.** A value shaped like a date is built into
    a real one before it is returned, so `2020-13-45` and a serial past the
    year 9999 are refused here rather than stored and crashing whatever reads
    them later. A fraction of a day is rounded to the second on the full
    timestamp, so a hair short of midnight is the next day, not 00:00:00 of
    this one.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        if SERIAL.match(text):
            serial = float(text)
            if serial < 1:
                return None
            epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
            moment = epoch + timedelta(seconds=round(serial * 86400))
            if moment.time() == datetime.min.time():
                return moment.date().isoformat()
            return moment.isoformat(timespec="seconds")
        if match := ISO_DATE.match(text):
            year, month, day = (int(group) for group in match.groups())
        elif match := US_DATE.match(text):
            month, day, year = (int(group) for group in match.groups())
        else:
            return None
        return date(year, month, day).isoformat()
    except (ValueError, OverflowError):
        return None


def _product_code(upc: str | None, description: str | None) -> str | None:
    """The line's product code, or None where the code names no product.

    A department-key ring and a markdown or adjustment line carry a short
    register code shared by every item rung under it. Kept, it would make
    unrelated purchases one "product" with one price series.
    """
    code = _present(upc)
    if code is None or len(code) < MIN_PRODUCT_CODE:
        return None
    if description and DEPARTMENT_RING.search(description.strip()):
        return None
    return code


def _data_rows(table: Table, header_row: int):
    """The rows after the header, by position in the sheet.

    Not by the number a row declares: that is the sheet's to write, and a data
    row numbered at or below the header was skipped without a word.
    """
    for position, row in enumerate(table.rows):
        if row.number == header_row:
            return table.rows[position + 1 :]
    return ()


class _LineWarnings:
    """Per-line warnings, with a repeat counted rather than repeated.

    One quirk on every line of a large sheet — a column the export left empty
    — was a warning per line, thousands of them, each stored and rendered. The
    first few are kept with their cells, so a reader can look; the rest become
    one line saying how many.
    """

    def __init__(self, warnings: WarningCollector) -> None:
        self._warnings = warnings
        self._seen: dict[tuple[str, str], int] = {}

    def add(self, message: str, *, locator: str, severity: str = "warning") -> None:
        key = (severity, message)
        self._seen[key] = self._seen.get(key, 0) + 1
        if self._seen[key] <= REPEATED_WARNINGS:
            if severity == "info":
                self._warnings.info(message, locator=locator)
            else:
                self._warnings.add(message, locator=locator)

    def close(self) -> None:
        for (severity, message), count in self._seen.items():
            extra = count - REPEATED_WARNINGS
            if extra > 0:
                summary = f"{extra} more lines like the above: {message}"
                if severity == "info":
                    self._warnings.info(summary)
                else:
                    self._warnings.add(summary)


# --------------------------------------------------------------------------
# The workbook
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    """One row of the transaction sheet, read by header name."""

    table: Table
    row: Row
    columns: dict[str, int]

    def at(self, header: str) -> str | None:
        """The cell under `header`, or None when the sheet has no such column."""
        column = self.columns.get(header)
        return self.row.value(column) if column else None

    def where(self, header: str) -> str:
        column = self.columns.get(header, 1)
        return self.table.locator(self.row.number, column)


def _transactions(matched, warnings: WarningCollector):
    """Baskets, by TXN_ID, and every card the sheet names.

    One row is one line item; a basket is the rows sharing a TXN_ID. A row that
    cannot be placed — no date, no TXN_ID — is skipped with a warning naming its
    cell, and costs that one line, not the basket around it.
    """
    baskets: dict[tuple, dict] = {}
    cards: dict[str, Provenance] = {}
    lines = _LineWarnings(warnings)
    for document, table, columns, header_row in matched:
        _check_declared(table, warnings)
        for row in _data_rows(table, header_row):
            if not any(row.cells):
                continue

            line = _Line(table, row, columns)
            at, where = line.at, line.where
            card = _present(at("CARD_NBR"))
            if card and card not in cards:
                cards[card] = Provenance(document.document_id, None, where("CARD_NBR"))

            when = _occurred_on(at("TXN_DTE"), table.date1904)
            if when is None:
                lines.add("Skipped a line with no readable date.", locator=where("TXN_DTE"))
                continue
            txn_id = _present(at("TXN_ID"))
            if txn_id is None:
                lines.add(
                    "Skipped a line with no transaction number, so no basket to put it in.",
                    locator=where("TXN_ID"),
                )
                continue

            key = (document.document_id, table.name, txn_id)
            basket = baskets.get(key)
            if basket is None:
                basket = baskets[key] = {
                    "when": when,
                    "store": _present(at("STORE_ID")),
                    "txn_id": txn_id,
                    "items": [],
                    "provenance": Provenance(document.document_id, None, where("TXN_ID")),
                }
            elif basket["when"] != when:
                lines.add(
                    "A transaction number appears on more than one date; its lines "
                    "are kept together under the first.",
                    locator=where("TXN_DTE"),
                    severity="info",
                )

            description = at("UPC_DSC")
            if description is None:
                lines.add("A line had no description.", locator=where("UPC_DSC"), severity="info")
            amount = _number(at("NET"))
            if amount is None:
                lines.add(
                    "A line had no readable amount; it is kept, with none.",
                    locator=where("NET"),
                )
            basket["items"].append(
                TxnItem(
                    description_raw=description or "",
                    upc=_product_code(at("UPC_ID"), description),
                    quantity=_number(at("QTY")),
                    # No shelf price is disclosed. NET is what the line cost
                    # after every markdown, including basket-level ones spread
                    # across lines, which is why it carries fractions of a
                    # cent. It is a price paid — see TxnItem.loyalty_amt.
                    retail_amt=None,
                    loyalty_amt=amount,
                )
            )

    lines.close()
    transactions = [
        Transaction(
            occurred_at=basket["when"],
            items=tuple(basket["items"]),
            external_order_id=basket["txn_id"],
            store_code=basket["store"],
            # No channel field. Defaulting to in_store would be a claim the
            # data does not support — the call both other adapters make.
            channel=None,
            # No basket total either, so there is nothing to foot against.
            # Summing the lines here would make the footing check compare the
            # lines with themselves.
            total_pre_discount=None,
            provenance=basket["provenance"],
        )
        for basket in baskets.values()
    ]
    transactions.sort(key=lambda t: t.occurred_at)
    return transactions, cards


def _check_declared(table: Table, warnings: WarningCollector) -> None:
    """The sheet's `dimension` against what was read: a free check on lost rows."""
    if table.declared_rows is not None and table.rows:
        highest = max(row.number for row in table.rows)
        if table.declared_rows != highest:
            warnings.info(
                f"The sheet declares {table.declared_rows} rows and the last one "
                f"read is numbered {highest}.",
                locator=table.name,
            )


def _demographics(matched, warnings: WarningCollector) -> list[Inference]:
    """The `Demographic Data` sheet: one inferred attribute per row.

    All `APPENDED_THIRD_PARTY`. The sheet heads its label column "Inferred
    Description", and what it holds — net worth, a wealth decile, credit-card
    types, a census block, mail-order buying — cannot be computed from a
    grocery basket. It was obtained elsewhere, and the response does not say
    from where.
    """
    out: list[Inference] = []
    for document, table, columns, header_row in matched:
        for row in _data_rows(table, header_row):
            if not any(row.cells):
                continue
            label = _present(row.value(columns["Inferred Description"]))
            value = _present(row.value(columns["Value"]))
            locator = table.locator(row.number, columns["Value"])
            if label is None:
                warnings.info("An inferred attribute had no description.", locator=locator)
                continue
            if value is None:
                warnings.info(f"The attribute {label!r} had no value.", locator=locator)
                continue
            scale, number = _scale(label, value)
            out.append(
                Inference(
                    label=label,
                    value_raw=value,
                    origin=InferenceOrigin.APPENDED_THIRD_PARTY,
                    value_num=number,
                    scale=scale,
                    subject=Scope.HOUSEHOLD if HOUSEHOLD_LABEL.search(label) else None,
                    derivable_from_txns=_derivable(label),
                    provenance=Provenance(document.document_id, None, locator),
                )
            )
    return out


def _scale(label: str, value: str) -> tuple[Scale, float | None]:
    """Only a number that IS a number gets a `value_num`.

    A census tract, a county code and a year are digits and are not
    quantities; reading them as counts would put them on a scale they do not
    belong on.
    """
    number = _number(value) if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else None
    if number is None:
        return Scale.CATEGORICAL, None
    if "(in thousands)" in label.lower():
        return Scale.CURRENCY, number
    if COUNT_LABEL.search(label):
        return Scale.COUNT, number
    return Scale.CATEGORICAL, None


def _derivable(label: str) -> bool | None:
    if UNSETTLED_LABEL.search(label):
        return None
    return bool(DERIVABLE_LABEL.search(label))


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def _identities(report, cite, cards, demographic_sheets) -> list[Identity]:
    found: list[Identity] = []
    seen: set[tuple[str, str]] = set()

    def add(id_type, value, scope, provenance, first_seen=None):
        text = _present(value)
        if not text or (str(id_type), text) in seen:
            return
        seen.add((str(id_type), text))
        found.append(Identity(id_type, text, scope, first_seen, provenance))

    if report is not None:
        visits = report.section(VISITS)
        first_visit = visits.get("Card_first_visit_dt") if visits else None
        for section in report.sections:
            if section.title not in KNOWN_SECTIONS:
                continue
            for field in section.fields:
                if field.key in IDENTIFIER_FIELDS:
                    id_type, scope = IDENTIFIER_FIELDS[field.key]
                    first = (
                        first_visit.value
                        if first_visit and id_type is IdType.LOYALTY_CARD
                        else None
                    )
                    add(id_type, field.value, scope, cite(field, section.title), first)
            for number, record in enumerate(section.records, start=1):
                for key, field in record.items():
                    if key in RECORD_IDENTIFIERS:
                        id_type, scope = RECORD_IDENTIFIERS[key]
                        add(id_type, field.value, scope, cite(field, f"{section.title} › {number}"))

        # The name the request was made in, which the report prints. Recorded
        # because the response holds it, and so that a builder of the PII
        # denylist that reads identities finds it: before this, the requester's
        # own name reached no list at all.
        requester = " ".join(
            part
            for key in ("First name", "Last name")
            if (f := report.request.get(key)) and (part := _present(f.value))
        )
        if requester and (field := report.request.get("First name")):
            add(IdType.NAME, requester, Scope.INDIVIDUAL, cite(field, "Request details"))

        profile = report.section(PROFILE)
        if profile is not None:
            name = " ".join(
                part
                for key in ("First_nm", "Middle_nm", "Last_nm")
                if (f := profile.get(key)) and (part := _present(f.value))
            )
            if name and (first := profile.get("First_nm") or profile.get("Last_nm")):
                add(IdType.NAME, name, Scope.INDIVIDUAL, cite(first, PROFILE))
            if street := profile.get("Address"):
                # Composed from the three fields the profile splits it into. An
                # address describes everyone who lives there.
                rest = " ".join(
                    part
                    for key in ("State_cd", "Postal_zone_cd")
                    if (f := profile.get(key)) and (part := _present(f.value))
                )
                line = ", ".join(part for part in (_present(street.value), rest) if part)
                add(IdType.ADDRESS, line, Scope.HOUSEHOLD, cite(street, PROFILE))

    for card, provenance in cards.items():
        add(IdType.LOYALTY_CARD, card, None, provenance)
    for document, table, columns, header_row in demographic_sheets:
        for row in _data_rows(table, header_row):
            if card := row.value(columns["Related Card"]):
                provenance = Provenance(
                    document.document_id, None, table.locator(row.number, columns["Related Card"])
                )
                add(IdType.LOYALTY_CARD, card, None, provenance)
    return found


def _first_party(report: rp.Report, cite) -> list[Inference]:
    """The profile's most-used and most-spent-at stores: counts over the visits."""
    profile = report.section(PROFILE)
    if profile is None:
        return []
    out = []
    for key in FIRST_PARTY_FIELDS:
        field = profile.get(key)
        if field is None or _present(field.value) is None:
            continue
        out.append(
            Inference(
                label=key,
                value_raw=field.value,
                origin=InferenceOrigin.FIRST_PARTY_MODEL,
                scale=Scale.CATEGORICAL,
                subject=None,
                derivable_from_txns=True,
                provenance=cite(field, PROFILE),
            )
        )
    return out


def _request_meta(report: rp.Report | None, transactions: list[Transaction]) -> RequestMeta:
    reference = submitted = None
    if report is not None:
        if field := report.request.get("Request ref ID"):
            reference = _present(field.value)
        if field := report.request.get("Date submitted"):
            submitted = _submitted(field.value)
    dates = [t.occurred_at[:10] for t in transactions]
    return RequestMeta(
        retailer_id=RETAILER_ID,
        display_name=DISPLAY_NAME,
        report_reference=reference,
        submitted_at=submitted,
        # The window is what the data covers. The report states none of its own.
        period_start=min(dates) if dates else None,
        period_end=max(dates) if dates else None,
        adapter_schema_version=SCHEMA_VERSION,
    )


def _submitted(value: str) -> str | None:
    """`03/02/2026 09:15 AM` → `2026-03-02T09:15:00`. A wall clock, no zone."""
    for pattern in SUBMITTED:
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _latest_dated(report: rp.Report | None) -> tuple[str, str] | None:
    """The latest date the report attaches to anything, and to what.

    Read from the points event and the preferences, which is where dates after
    the purchase history appear. A preference's end date is not read: the one
    observed is 9999-12-31, which is "open", not a date anything happened.
    """
    if report is None:
        return None
    candidates: list[tuple[str, str]] = []
    if (earn := report.section(EARN_BURN)) and (field := earn.get("Last_update_ts")):
        candidates.append((field.value[:10], "a loyalty points event"))
    if preferences := report.section(PREFERENCES):
        for record in preferences.records:
            if field := record.get("first_eff_ts"):
                candidates.append((field.value[:10], "a contact preference"))
    candidates = [c for c in candidates if ISO_DATE.match(c[0])]
    return max(candidates) if candidates else None


# --------------------------------------------------------------------------
# What the response addressed
# --------------------------------------------------------------------------


def _disclosures(report, source, transactions, inferences, transaction_sheets, identities):
    """What the response addressed, graded only from what was read.

    Every category but SPECIFIC_PIECES is decided by the report's prose. With no
    report in the upload those are left ungraded — no row, which the Compliance
    view shows as "not assessed" — rather than ABSENT, which would say the
    retailer failed to address something in a file nobody uploaded, and would
    turn into a demand in the follow-up letter.
    """
    provenance = Provenance(
        source_document_id=source.document_id if source else None,
        page=1 if source else None,
        locator="Instructions" if source else None,
    )
    found: dict[DisclosureCategory, Disclosure] = {}

    sentence = rp.quote(report, CATEGORIES_SENTENCE) if report else None
    if sentence:
        tables = [s.title for s in report.sections if s.title in KNOWN_SECTIONS]
        appended = sum(1 for i in inferences if i.origin is InferenceOrigin.APPENDED_THIRD_PARTY)
        found[DisclosureCategory.CATEGORIES_COLLECTED] = Disclosure(
            category=DisclosureCategory.CATEGORIES_COLLECTED,
            status=DisclosureStatus.PARTIAL,
            evidence=sentence,
            notes=(
                "The response says its headings are the categories collected. They "
                f"name its tables — {', '.join(tables) or 'none'} — rather than the "
                "categories the statute names"
                + (
                    f", and none of them covers the {appended} inferred attributes in the workbook."
                    if appended
                    else "."
                )
            ),
            provenance=provenance,
        )

    items = sum(len(t.items) for t in transactions)
    parts = []
    if transactions:
        parts.append(
            f"{len(transactions)} purchases with {items} line items, "
            f"{transactions[0].occurred_at[:10]} to {transactions[-1].occurred_at[:10]}"
        )
    if report is not None and report.section(PROFILE):
        parts.append("a loyalty profile")
    appended = sum(1 for i in inferences if i.origin is InferenceOrigin.APPENDED_THIRD_PARTY)
    if appended:
        parts.append(f"{appended} inferred attributes")
    # Each sentence is said only about a file that was read, and only what was
    # observed to be missing in it.
    gaps = []
    if transaction_sheets:
        missing = ["no shelf price beside the amount paid", "no basket total"]
        if not any("T" in t.occurred_at for t in transactions):
            missing.append("no time of day")
        missing.append("no payment method")
        gaps.append(f"The purchase history has {', '.join(missing)}.")
    if report is not None and transaction_sheets:
        digital = ["an app", "a website", "an online order"]
        if not any(i.id_type is IdType.EMAIL for i in identities):
            digital.insert(2, "an email address")
        gaps.append(f"Nothing from {', '.join(digital[:-1])} or {digital[-1]}.")
    # Cited to the workbook where there is one: that is where the pieces are.
    if transaction_sheets:
        document, table, columns, header_row = transaction_sheets[0]
        pieces = Provenance(document.document_id, None, table.locator(header_row, 1))
    else:
        pieces = provenance
    found[DisclosureCategory.SPECIFIC_PIECES] = Disclosure(
        category=DisclosureCategory.SPECIFIC_PIECES,
        status=DisclosureStatus.PARTIAL,
        evidence="; ".join(parts) + "." if parts else None,
        notes=" ".join([*gaps, _coverage_gap(report, transactions)]).strip() or None,
        provenance=pieces,
    )
    if report is None:
        return tuple(found.values())
    return absent_disclosures(
        found,
        note=(
            "The response's prose is a guide to its own layout and a list of what it "
            "left out. Nothing in it addresses this category."
        ),
        provenance=provenance,
    )


def _coverage_gap(report, transactions) -> str:
    """The distance from the last purchase to the request, and what is later."""
    if not transactions:
        return ""
    end = transactions[-1].occurred_at[:10]
    sentences = []
    submitted = None
    if report is not None and (field := report.request.get("Date submitted")):
        submitted = _submitted(field.value)
    if submitted:
        years = (date.fromisoformat(submitted[:10]) - date.fromisoformat(end)).days / 365.25
        if years >= 1:
            # Floored, and said as "more than": rounding a span up to the next
            # whole year would overstate the one number the sentence is for.
            span = "a year" if years < 2 else f"{int(years)} years"
            sentences.append(
                f"The purchase history ends on {end}, more than {span} before the "
                "request was submitted, so nothing in it falls in the twelve months "
                "before the request."
            )
    latest = _latest_dated(report)
    if latest and latest[0] > end:
        sentences.append(
            f"The report itself dates {latest[1]} to {latest[0]}, after the last purchase it lists."
        )
    return " ".join(sentences)


def _follow_ups(report, request, transactions, demographics, disclosures):
    actions: list[FollowUpAction] = []

    if report is not None and (profile := report.section(PROFILE)):
        asked = report.request.get("First name")
        holder = profile.get("First_nm")
        # Both compared only when both are names. A profile with no first name
        # prints a placeholder, and reading that as a different name would put
        # an untrue claim about whose record this is into a letter.
        asked_name = _present(asked.value) if asked else None
        holder_name = _present(holder.value) if holder else None
        if asked_name and holder_name and _normalise(asked_name) != _normalise(holder_name):
            actions.append(
                FollowUpAction(
                    kind=FollowUpKind.CLARIFICATION,
                    description=(
                        "The request was made in one name, and the loyalty profile this "
                        "response returned is registered to a different first name. "
                        "Everything in it — the card, its purchases"
                        + (f", the {len(demographics)} inferred attributes" if demographics else "")
                        + " — describes that profile's holder or household, and the "
                        "response does not say why it was returned for this request or "
                        "whether the requester has a record of their own."
                    ),
                )
            )

    if transactions:
        gap = _coverage_gap(report, transactions)
        if gap:
            actions.append(
                FollowUpAction(
                    kind=FollowUpKind.CLARIFICATION,
                    description=(
                        f"{gap} Ask whether purchases after that date are held under "
                        "another card, a phone number or an online account linked to "
                        "this household."
                    ),
                )
            )

    if report is not None:
        withheld = rp.quote_all(report, WITHHELD_SENTENCE)
        if withheld:
            quoted = "“" + " ".join(withheld) + "”"
            actions.append(
                FollowUpAction(
                    kind=FollowUpKind.CLARIFICATION,
                    description=(
                        f"The response says it withheld some information: {quoted} "
                        "What can be provided with another person's information "
                        "removed is worth asking for."
                    ),
                )
            )

    for disclosure in disclosures:
        if disclosure.status is DisclosureStatus.ABSENT:
            actions.append(
                FollowUpAction(
                    kind=FollowUpKind.MISSING_CATEGORY,
                    description=(
                        f"Ask {DISPLAY_NAME} to disclose {disclosure.category.label}, "
                        "which the response did not address."
                    ),
                )
            )
    return tuple(actions)


adapter = AlbertsonsAdapter()
