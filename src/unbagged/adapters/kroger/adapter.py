"""Kroger CCPA response adapter.

Everything here is derived from one real report. `NOTES.md` records what was
observed and why each judgment call was made; this file implements it. Where the
two disagree, the notes are the specification.

The classification of inferences is the part worth reading. Kroger's report mixes
two populations in one blob: propensity scores it computed from the baskets in
that same report, and demographic attributes it cannot have computed from
groceries at all. Separating them, and saying so, is the most interesting thing
this tool does.
"""

from __future__ import annotations

import re
from typing import Any

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
    SourceBundle,
    Transaction,
    TxnItem,
    WarningCollector,
    absent_disclosures,
)
from unbagged.adapters.kroger import reader
from unbagged.extraction import ExtractedDocument, extract, extract_all

RETAILER_ID = "kroger"
DISPLAY_NAME = "Kroger"
SCHEMA_VERSION = 1

# Pages of a PDF read during sniff(). Deciding which adapter owns a bundle must
# not cost a full extraction of a 48-page report.
SNIFF_PAGES = 3

REPORT_REFERENCE = re.compile(r"Report reference:\s*(\S+)")
REPORT_PERIOD = re.compile(
    r"Report period:\s*(\d{4}-\d{2}-\d{2})\s*(?:through|to|-)\s*(\d{4}-\d{2}-\d{2})"
)
SUPPLEMENTAL_HINT = re.compile(r"prior to\s+(\d{4})", re.IGNORECASE)

PLACEHOLDER_DESCRIPTION = "UNKNOWN"

# Identifier fields observed in the loyalty blob, and what each one is.
# Eight keys for one shopper, and the report explains none of them.
IDENTIFIER_FIELDS: tuple[tuple[str, IdType, Scope], ...] = (
    ("loyaltyno", IdType.LOYALTY_CARD, Scope.INDIVIDUAL),
    ("cardNumberWithCD", IdType.LOYALTY_CARD, Scope.INDIVIDUAL),
    ("alternateId", IdType.ALTERNATE_ID, Scope.INDIVIDUAL),
    ("ehhn", IdType.HOUSEHOLD, Scope.HOUSEHOLD),
    ("householdId", IdType.HOUSEHOLD, Scope.HOUSEHOLD),
    ("cgPersonId", IdType.INTERNAL_PERSON, Scope.INDIVIDUAL),
    ("epsn", IdType.INTERNAL_PERSON, Scope.INDIVIDUAL),
    ("SubscriberID", IdType.INTERNAL_PERSON, Scope.INDIVIDUAL),
    ("emailAddress", IdType.EMAIL, Scope.INDIVIDUAL),
    ("phoneNumber", IdType.PHONE, Scope.INDIVIDUAL),
)

ADDRESS_FIELDS = ("addressLine1", "addressLine2", "city", "state", "zipCode")

# Which demographic attributes describe a household rather than a person. These
# are the ones that describe people who never enrolled in anything, and the
# profile view calls them out.
HOUSEHOLD_ATTRIBUTES = frozenset({
    "householdComposition", "numberOfAdults", "numberOfChildren",
    "homeOwnerStatus", "lengthOfResidence", "incomePredictorScore",
})

# Whether an attribute could have been derived from the transactions in this same
# report. Anything absent from this map is False: the default assumption for an
# appended attribute is that the baskets do not explain it.
#
# Recorded as the adapter's judgment, shown to the user as a caveat, never as a
# fact about what Kroger actually did.
DERIVABLE_FROM_TXNS: dict[str, bool | None] = {
    # Pet food appears as line items in these very baskets.
    "petOwner": True,
    # Kroger holds its own online order records, but this report discloses no
    # channel field, so from the data provided this is genuinely unknown.
    "onlineShopperLikelihood": None,
}

ORDINAL_PREFIX = re.compile(r"^\s*([1-7])\s*[-–]")


class Cursor:
    """Forward-only search over a blob's raw text, for locating provenance.

    Records appear in the JSON in the same order they appear in the parsed
    structure, so a forward cursor finds each one in a single pass instead of
    rescanning 300 KB per basket.
    """

    def __init__(self, raw: str, base_offset: int) -> None:
        self._raw = raw
        self._base = base_offset
        self._at = 0

    def find(self, needle: str) -> int:
        """Absolute offset of the next occurrence, or the current position."""
        found = self._raw.find(needle, self._at)
        if found == -1:
            return self._base + self._at
        self._at = found + len(needle)
        return self._base + found


def _number(value: Any) -> float | None:
    """Coerce a reported amount. Retailers send numbers, strings and '$1,234.56'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _occurred_at(date: Any, time: Any) -> str | None:
    """Combine the report's date and time fields into one ISO-8601 timestamp.

    Deliberately not stamped with Z. The report gives a store-local wall clock
    with no timezone, and asserting UTC would push every evening shop in
    California into the next day. Sorting is unaffected; see NOTES.md.
    """
    day = _text(date)
    if not day:
        return None
    clock = _text(time) or "00:00:00"
    if len(clock) == 5:
        clock = f"{clock}:00"
    return f"{day}T{clock}"


class KrogerAdapter:
    retailer_id = RETAILER_ID
    display_name = DISPLAY_NAME
    schema_version = SCHEMA_VERSION

    # -- selection ---------------------------------------------------------

    def sniff(self, bundle: SourceBundle) -> float:
        """Cheap, and never raises. See registry.score()."""
        score = 0.0
        if (bundle.declared_retailer or "").strip().lower() == RETAILER_ID:
            score += 0.2

        for document in bundle.documents:
            if RETAILER_ID in (document.original_filename or "").lower():
                score = max(score, score + 0.1)
            try:
                head = extract(document, max_pages=SNIFF_PAGES).text
            except Exception:
                # sniff() must not raise: an unreadable file is simply not a
                # Kroger report as far as selection is concerned.
                continue
            if reader.SECTION_HEADERS[0] in head:
                score += 0.5
            matched = sum(1 for h in reader.SECTION_HEADERS[1:] if h in head)
            if matched:
                score += min(0.3, 0.1 * matched)
        return min(score, 1.0)

    # -- parsing -----------------------------------------------------------

    def parse(self, bundle: SourceBundle) -> ParseResult:
        warnings = WarningCollector()
        documents = extract_all(bundle.documents)
        for document in bundle.documents:
            if not any(e.filename == document.original_filename for e in documents):
                warnings.error(
                    f"{document.original_filename} could not be read and was skipped."
                )
        if not documents:
            raise AdapterError(
                "None of the uploaded files could be read as text. If this is a "
                "scanned PDF, it has no text layer and cannot be parsed yet."
            )

        report = self._pick_report(documents, warnings)
        clean, pages = reader.strip_page_markers(report.text)
        sections = reader.find_sections(clean)
        blobs = reader.find_blobs(clean, sections)

        for start, end in reader.unparseable_spans(clean):
            warnings.error(
                "A JSON block in the report did not parse and was skipped.",
                locator=f"offset {start}-{end}, page {pages.page_of(start)}",
            )
        truncated = reader.unterminated_span(clean)
        if truncated is not None:
            # Saying "the file ends partway through, from about page 34" is far
            # more use than silently returning three sections of four.
            warnings.error(
                "The report appears to be truncated: a data section begins and "
                "never ends. Everything before this point was read; anything "
                "after it is missing from the file, not from the retailer.",
                locator=f"offset {truncated}, page {pages.page_of(truncated)}",
            )
        if not sections:
            warnings.error(
                "None of the known Kroger section headers were found. The report "
                "format may have changed; see the adapter's NOTES.md."
            )

        def provenance(offset: int, locator: str) -> Provenance:
            return Provenance(
                source_document_id=report.document_id,
                page=pages.page_of(offset),
                locator=locator,
            )

        identities = self._identities(blobs, provenance, warnings)
        transactions = self._transactions(blobs, provenance, warnings)
        inferences = self._inferences(blobs, provenance, warnings)
        disclosures = self._disclosures(sections, blobs, provenance)
        follow_ups = self._follow_ups(clean, disclosures)

        return ParseResult(
            request=self._request_meta(clean, transactions),
            identities=identities,
            transactions=transactions,
            inferences=inferences,
            disclosures=disclosures,
            follow_ups=follow_ups,
            warnings=warnings.as_tuple(),
        )

    # -- pieces ------------------------------------------------------------

    def _pick_report(
        self, documents: list[ExtractedDocument], warnings: WarningCollector
    ) -> ExtractedDocument:
        """The document that actually contains the report.

        A bundle can hold a cover letter alongside the report. Picking the one
        with the most recognised headers beats picking the first.
        """
        best, best_score = documents[0], -1
        for document in documents:
            score = sum(1 for h in reader.SECTION_HEADERS if h in document.text)
            if score > best_score:
                best, best_score = document, score
        for document in documents:
            if document is not best:
                warnings.info(
                    f"{document.filename} contained no Kroger report sections and "
                    "was not parsed."
                )
        return best

    def _request_meta(
        self, clean: str, transactions: tuple[Transaction, ...]
    ) -> RequestMeta:
        reference = REPORT_REFERENCE.search(clean)
        period = REPORT_PERIOD.search(clean)
        if period:
            start, end = period.group(1), period.group(2)
        elif transactions:
            # Falling back to the data itself is honest about what was actually
            # covered, which may be narrower than what the prose claims.
            dates = sorted(t.occurred_at for t in transactions if t.occurred_at)
            start, end = (dates[0][:10], dates[-1][:10]) if dates else (None, None)
        else:
            start = end = None
        return RequestMeta(
            retailer_id=RETAILER_ID,
            display_name=DISPLAY_NAME,
            report_reference=reference.group(1) if reference else None,
            period_start=start,
            period_end=end,
            adapter_schema_version=SCHEMA_VERSION,
        )

    def _identities(self, blobs, provenance, warnings) -> tuple[Identity, ...]:
        blob = reader.blob_for_header(blobs, reader.LOYALTY_HEADER)
        identities: list[Identity] = []
        seen: set[tuple[str, str]] = set()

        def add(id_type: IdType, value: str | None, scope: Scope, offset: int, path: str):
            text = _text(value)
            if not text or (str(id_type), text) in seen:
                return
            seen.add((str(id_type), text))
            identities.append(
                Identity(id_type=id_type, value=text, scope=scope,
                         provenance=provenance(offset, path))
            )

        if blob is None:
            warnings.error("No loyalty section found, so no identifiers were read.")
        else:
            customer = self._first_customer(blob.data, warnings, reader.LOYALTY_HEADER)
            if customer:
                cursor = Cursor(blob.raw, blob.start)
                for field, id_type, scope in IDENTIFIER_FIELDS:
                    if field not in customer:
                        continue
                    offset = cursor.find(f'"{field}"')
                    add(id_type, customer[field], scope,
                        offset, f"$.customer[0].{field}")

                address = ", ".join(
                    part for part in (_text(customer.get(f)) for f in ADDRESS_FIELDS) if part
                )
                if address:
                    offset = cursor.find('"addressLine1"')
                    # Household-scoped on purpose: an address describes everyone
                    # living there, not only the person who enrolled.
                    add(IdType.ADDRESS, address, Scope.HOUSEHOLD,
                        offset, "$.customer[0].addressLine1")

        email_blob = reader.blob_for_header(blobs, reader.EMAIL_HEADER)
        if email_blob is not None:
            customer = self._first_customer(email_blob.data, warnings, reader.EMAIL_HEADER)
            cursor = Cursor(email_blob.raw, email_blob.start)
            for index, record in enumerate((customer or {}).get("emailActivity", []) or []):
                if not isinstance(record, dict):
                    continue
                value = _text(record.get("emailAddress"))
                if value:
                    offset = cursor.find(f'"{value}"')
                    add(IdType.EMAIL, value, Scope.INDIVIDUAL, offset,
                        f"$.customer[0].emailActivity[{index}].emailAddress")

        return tuple(identities)

    def _transactions(self, blobs, provenance, warnings) -> tuple[Transaction, ...]:
        blob = reader.blob_for_header(blobs, *reader.PURCHASE_HEADERS)
        if blob is None:
            warnings.error("No purchase section found, so no transactions were read.")
            return ()

        customer = self._first_customer(blob.data, warnings, "purchases")
        baskets = (customer or {}).get("basket") or []
        if not isinstance(baskets, list):
            warnings.error("The purchase section's basket field was not a list.",
                           locator="$.customer[0].basket")
            return ()

        cursor = Cursor(blob.raw, blob.start)
        transactions: list[Transaction] = []
        for index, basket in enumerate(baskets):
            path = f"$.customer[0].basket[{index}]"
            offset = cursor.find('"date"')
            try:
                transaction = self._basket(basket, index, offset, path, provenance, warnings)
            except Exception as exc:
                # Rule 4: one malformed basket costs one warning and one record,
                # not the other fifty-three baskets.
                warnings.error(f"Basket {index} could not be read ({exc}).", locator=path)
                continue
            if transaction is not None:
                transactions.append(transaction)
        return tuple(transactions)

    def _basket(self, basket, index, offset, path, provenance, warnings) -> Transaction | None:
        if not isinstance(basket, dict):
            warnings.error(f"Basket {index} was not an object and was skipped.",
                           locator=path)
            return None
        occurred_at = _occurred_at(basket.get("date"), basket.get("time"))
        if not occurred_at:
            warnings.error(f"Basket {index} had no usable date and was skipped.",
                           locator=path)
            return None

        tenders = basket.get("tenders") or []
        tender_types = [
            _text(t.get("tendertype")) for t in tenders if isinstance(t, dict)
        ]
        # A split payment is two tender types on one basket. Joining them keeps
        # the fact; dropping the second would quietly rewrite the receipt.
        tender = " + ".join(t for t in tender_types if t) or None

        items = basket.get("items") or []
        if not isinstance(items, list):
            warnings.error(f"Basket {index} had a malformed item list.",
                           locator=f"{path}.items")
            items = []

        return Transaction(
            occurred_at=occurred_at,
            external_order_id=_text(basket.get("orderno")),
            store_code=_text(basket.get("store")),
            division_code=_text(basket.get("division")),
            # Channel is left unset on purpose: the report has no field for it,
            # and guessing in_store would be a claim the data does not support.
            channel=None,
            tender_type=tender,
            total_pre_discount=_number(basket.get("total_amount_prior_to_discounts")),
            provenance=provenance(offset, path),
            items=self._items(items, index, path, warnings),
        )

    def _items(self, items, basket_index, path, warnings) -> tuple[TxnItem, ...]:
        parsed: list[TxnItem] = []
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                warnings.add(
                    f"Item {position} of basket {basket_index} was not an object.",
                    locator=f"{path}.items[{position}]",
                )
                continue
            description = _text(item.get("purchasedescription"))
            parsed.append(
                TxnItem(
                    # Stored exactly as it appeared, placeholder rows included.
                    # Dropping them would be a mutation, and their count is itself
                    # a fact about the quality of the disclosure.
                    description_raw=description if description is not None else "",
                    upc=_text(item.get("productupc")),
                    quantity=_number(item.get("quantity")),
                    retail_amt=_number(item.get("retailamt")),
                    loyalty_amt=_number(item.get("customerloyamt")),
                )
            )
        return tuple(parsed)

    def _inferences(self, blobs, provenance, warnings) -> tuple[Inference, ...]:
        blob = reader.blob_for_header(blobs, reader.ADVERTISING_HEADER)
        if blob is None:
            warnings.info(
                "No personalised-advertising section found, so no inferred "
                "attributes were read."
            )
            return ()

        customer = self._first_customer(blob.data, warnings, reader.ADVERTISING_HEADER)
        if not customer:
            return ()

        cursor = Cursor(blob.raw, blob.start)
        inferences: list[Inference] = []

        for label, value in (customer.get("propensities") or {}).items():
            raw = _text(value)
            if raw is None:
                continue
            offset = cursor.find(f'"{label}"')
            inferences.append(
                Inference(
                    label=str(label),
                    value_raw=raw,
                    # Computable from the baskets in this very report.
                    origin=InferenceOrigin.FIRST_PARTY_MODEL,
                    scale=Scale.CATEGORICAL,
                    subject=Scope.INDIVIDUAL,
                    derivable_from_txns=True,
                    provenance=provenance(offset, f"$.customer[0].propensities.{label}"),
                )
            )

        for group in ("demographics", "likelihoods"):
            for label, value in (customer.get(group) or {}).items():
                raw = _text(value)
                if raw is None:
                    continue
                offset = cursor.find(f'"{label}"')
                ordinal = ORDINAL_PREFIX.match(raw)
                numeric = _number(value)
                inferences.append(
                    Inference(
                        label=str(label),
                        value_raw=raw,
                        # Nothing in a grocery basket says how long someone has
                        # lived at an address or whether they will take a cruise.
                        # These were bought, and the report does not say from whom.
                        origin=InferenceOrigin.APPENDED_THIRD_PARTY,
                        value_num=float(ordinal.group(1)) if ordinal else numeric,
                        scale=Scale.ORDINAL_1_7 if ordinal else self._scale_of(value),
                        subject=(
                            Scope.HOUSEHOLD if label in HOUSEHOLD_ATTRIBUTES
                            else Scope.INDIVIDUAL
                        ),
                        derivable_from_txns=DERIVABLE_FROM_TXNS.get(str(label), False),
                        provenance=provenance(offset, f"$.customer[0].{group}.{label}"),
                    )
                )
        return tuple(inferences)

    @staticmethod
    def _scale_of(value: Any) -> Scale:
        if isinstance(value, bool):
            return Scale.CATEGORICAL
        if isinstance(value, (int, float)):
            return Scale.COUNT
        return Scale.CATEGORICAL

    def _disclosures(self, sections, blobs, provenance) -> tuple[Disclosure, ...]:
        """What the report answered, and — mostly — what it did not.

        Kroger's response addresses the specific pieces of personal information
        and nothing else. There is no Section 2, 3 or 4; categories of sources,
        business purposes, third-party recipients and sale/share status are all
        simply missing. Each becomes an explicit ABSENT finding, because a gap in
        the data model would be indistinguishable from a gap in the parser.
        """
        found: dict[DisclosureCategory, Disclosure] = {}
        header = reader.SECTION_HEADERS[0]
        section = next((s for s in sections if s.header == header), None)
        if section is not None:
            found[DisclosureCategory.SPECIFIC_PIECES] = Disclosure(
                category=DisclosureCategory.SPECIFIC_PIECES,
                status=DisclosureStatus.PROVIDED,
                evidence=header,
                notes=(
                    f"{len(blobs)} structured data sections were returned under this "
                    "heading."
                ),
                provenance=provenance(section.start, "$"),
            )
        return absent_disclosures(
            found,
            note=(
                "The report contains no section addressing this category. It is "
                "numbered as though Sections 2 to 4 exist, but they are not present."
            ),
            provenance=provenance(0, "$"),
        )

    def _follow_ups(self, clean: str, disclosures) -> tuple[FollowUpAction, ...]:
        actions: list[FollowUpAction] = []
        supplemental = SUPPLEMENTAL_HINT.search(clean)
        if supplemental:
            actions.append(
                FollowUpAction(
                    kind=FollowUpKind.SUPPLEMENTAL_PERIOD,
                    description=(
                        "The report covers a limited window and directs the requester "
                        f"to contact the privacy office for data prior to "
                        f"{supplemental.group(1)}. That supplemental request has to be "
                        "made separately."
                    ),
                )
            )
        for disclosure in disclosures:
            if disclosure.status is DisclosureStatus.ABSENT:
                actions.append(
                    FollowUpAction(
                        kind=FollowUpKind.MISSING_CATEGORY,
                        description=(
                            f"Ask Kroger to disclose {disclosure.category.value}, which "
                            "the response did not address."
                        ),
                    )
                )
        return tuple(actions)

    @staticmethod
    def _first_customer(data: Any, warnings: WarningCollector, where: str) -> dict | None:
        if isinstance(data, dict):
            customers = data.get("customer")
            if isinstance(customers, list) and customers:
                if isinstance(customers[0], dict):
                    return customers[0]
            elif isinstance(customers, dict):
                return customers
        warnings.error(f"The {where} section had no customer object.",
                       locator="$.customer[0]")
        return None


adapter = KrogerAdapter()
