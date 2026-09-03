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

# The report types its identity groups, which means it states whether a record
# describes a person or a household rather than leaving the adapter to guess
# from field names.
GROUP_SCOPES: dict[str, Scope] = {
    "CG_PERSON": Scope.INDIVIDUAL,
    "KROGER_HOUSEHOLD": Scope.HOUSEHOLD,
}

# aliasIds and metadata keys, and what kind of identifier each one is.
ALIAS_TYPES: dict[str, IdType] = {
    "cgPersonId": IdType.INTERNAL_PERSON,
    "epsn": IdType.INTERNAL_PERSON,
    "ehhn": IdType.HOUSEHOLD,
    "householdId": IdType.HOUSEHOLD,
    "loyaltyId": IdType.LOYALTY_CARD,
}

# Inference labels that the baskets in this same report could account for.
# Everything else defaults to False; see NOTES.md for the reasoning.
DERIVABLE_LABELS = ("cat owner", "dog owner", "pet owner")

# "(7=Most Likely; 1=Least Likely)" and friends.
ORDINAL_LABEL = re.compile(r"\(\s*[17]\s*=\s*(?:most|least)", re.IGNORECASE)

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

# The five named axes. Used to find the blob by shape, because the report puts
# more than one structure under the same header.
PROPENSITY_AXES = ("Convenience", "Loyalty", "Price", "Quality", "Variety Seeking")


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


US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _occurred_at(date: Any, time: Any) -> str | None:
    """Combine the report's date and time fields into one ISO-8601 timestamp.

    The `date` field is not always a date. Real reports send
    "08/17/2024 00:00:00" — a US-format date with a zeroed time welded on —
    while `time` separately holds the real clock. Concatenating the two
    produced "08/17/2024 00:00:00T15:52:00", which is not a timestamp, does not
    sort, and put the timeline in arbitrary order. Both parts are parsed now.

    Deliberately not stamped with Z. The report gives a store-local wall clock
    with no timezone, and asserting UTC would push every evening shop in
    California into the next day. Sorting is unaffected; see NOTES.md.
    """
    raw_date = _text(date)
    if not raw_date:
        return None

    if match := ISO_DATE.match(raw_date):
        year, month, day = match.group(1), match.group(2), match.group(3)
    elif match := US_DATE.match(raw_date):
        year = match.group(3)
        month = f"{int(match.group(1)):02d}"
        day = f"{int(match.group(2)):02d}"
    else:
        return None

    # Prefer the dedicated time field; fall back to any clock inside `date`,
    # which is where a zeroed placeholder usually lives.
    clock = None
    for candidate in (_text(time), raw_date[len(match.group(0)):]):
        if candidate and (found := CLOCK.search(candidate)):
            hour, minute, second = found.group(1), found.group(2), found.group(3) or "00"
            if clock is None or (hour, minute, second) != ("00", "00", "00"):
                clock = f"{int(hour):02d}:{minute}:{second}"
            if clock != "00:00:00":
                break
    return f"{year}-{month}-{day}T{clock or '00:00:00'}"


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
        """Every key the retailer holds for one shopper.

        Two shapes have been observed under the loyalty header. The current one
        nests accounts and typed identity groups; an earlier flat `customer[0]`
        shape is still read, because a retailer is free to send either and the
        cost of supporting both is a fallback branch.
        """
        identities: list[Identity] = []
        seen: set[tuple[str, str]] = set()

        def add(id_type: IdType, value: Any, scope: Scope, offset: int, path: str):
            text = _text(value)
            if not text or (str(id_type), text) in seen:
                return
            seen.add((str(id_type), text))
            identities.append(
                Identity(id_type=id_type, value=text, scope=scope,
                         provenance=provenance(offset, path))
            )

        accounts_blob = reader.blob_with_keys(blobs, "accounts", "groups")
        if accounts_blob is not None:
            self._identities_from_accounts(accounts_blob, add, provenance)
        else:
            flat = reader.blob_for_header(blobs, reader.LOYALTY_HEADER)
            if flat is None:
                warnings.error("No loyalty section found, so no identifiers were read.")
            else:
                self._identities_from_flat_customer(flat, add, warnings)

        purchases = reader.blob_for_header(blobs, *reader.PURCHASE_HEADERS)
        if purchases is not None:
            customer = self._first_customer(purchases.data, WarningCollector(), "purchases")
            if customer:
                cursor = Cursor(purchases.raw, purchases.start)
                add(IdType.LOYALTY_CARD, customer.get("loyaltyno"), Scope.INDIVIDUAL,
                    cursor.find('"loyaltyno"'), "$.customer[0].loyaltyno")

        self._identities_from_email(blobs, add)
        if not identities:
            warnings.error(
                "No identifiers were found. The loyalty section's shape may have "
                "changed; see the adapter's NOTES.md."
            )
        return tuple(identities)

    def _identities_from_accounts(self, blob, add, provenance) -> None:
        """The nested `accounts` / `groups` shape."""
        cursor = Cursor(blob.raw, blob.start)
        for i, outer in enumerate(blob.data.get("accounts") or []):
            if not isinstance(outer, dict):
                continue
            for j, account in enumerate(outer.get("accounts") or []):
                if not isinstance(account, dict):
                    continue
                base = f"$.accounts[{i}].accounts[{j}]"
                # Card numbers are the *keys* of loyaltyCards, not values.
                for number, card in (account.get("loyaltyCards") or {}).items():
                    offset = cursor.find(f'"{number}"')
                    add(IdType.LOYALTY_CARD, number, Scope.INDIVIDUAL,
                        offset, f"{base}.loyaltyCards.{number}")
                    if isinstance(card, dict):
                        add(IdType.LOYALTY_CARD, card.get("cardNumberWithCD"),
                            Scope.INDIVIDUAL, offset,
                            f"{base}.loyaltyCards.{number}.cardNumberWithCD")
                        for alt in card.get("altIds") or []:
                            add(IdType.ALTERNATE_ID, alt, Scope.INDIVIDUAL, offset,
                                f"{base}.loyaltyCards.{number}.altIds")
                name = (account.get("personalInfo") or {}).get("name") or {}
                full = " ".join(
                    part for part in (_text(name.get("firstName")),
                                      _text(name.get("lastName"))) if part
                )
                if full:
                    add(IdType.NAME, full, Scope.INDIVIDUAL,
                        cursor.find('"personalInfo"'), f"{base}.personalInfo.name")

        for k, group in enumerate(blob.data.get("groups") or []):
            if not isinstance(group, dict):
                continue
            # The report says which of these describe a household. Believe it.
            scope = GROUP_SCOPES.get(str(group.get("type")), Scope.INDIVIDUAL)
            base = f"$.groups[{k}]"
            offset = cursor.find(f'"{group.get("type")}"')
            for key, value in (group.get("aliasIds") or {}).items():
                add(ALIAS_TYPES.get(key, IdType.INTERNAL_PERSON), value, scope,
                    offset, f"{base}.aliasIds.{key}")
            for key, entry in (group.get("metadata") or {}).items():
                value = entry.get("value") if isinstance(entry, dict) else entry
                if key in ALIAS_TYPES:
                    add(ALIAS_TYPES[key], value, scope, offset,
                        f"{base}.metadata.{key}.value")
                elif key.lower() == "address" and value is not None:
                    text = value if isinstance(value, str) else ", ".join(
                        str(v) for v in (value.values() if isinstance(value, dict)
                                         else value) if v
                    )
                    # Household-scoped on purpose: an address describes everyone
                    # living there, not only the person who enrolled.
                    add(IdType.ADDRESS, text, Scope.HOUSEHOLD, offset,
                        f"{base}.metadata.address.value")

    def _identities_from_flat_customer(self, blob, add, warnings) -> None:
        """The flat `customer[0]` shape, kept as a fallback."""
        customer = self._first_customer(blob.data, warnings, reader.LOYALTY_HEADER)
        if not customer:
            return
        cursor = Cursor(blob.raw, blob.start)
        for field, id_type, scope in IDENTIFIER_FIELDS:
            if field in customer:
                add(id_type, customer[field], scope,
                    cursor.find(f'"{field}"'), f"$.customer[0].{field}")
        address = ", ".join(
            part for part in (_text(customer.get(f)) for f in ADDRESS_FIELDS) if part
        )
        if address:
            add(IdType.ADDRESS, address, Scope.HOUSEHOLD,
                cursor.find('"addressLine1"'), "$.customer[0].addressLine1")

    def _identities_from_email(self, blobs, add) -> None:
        """Email identifiers, from either the name/value or the activity shape."""
        blob = reader.blob_for_header(blobs, reader.EMAIL_HEADER)
        if blob is None:
            return
        cursor = Cursor(blob.raw, blob.start)
        data = blob.data if isinstance(blob.data, dict) else {}

        for index, row in enumerate(data.get("emailData") or []):
            if not isinstance(row, dict):
                continue
            name, value = _text(row.get("Name")), row.get("Value")
            offset = cursor.find(f'"{name}"')
            if name == "EmailAddress":
                add(IdType.EMAIL, value, Scope.INDIVIDUAL, offset,
                    f"$.emailData[{index}].Value")
            elif name in ("SubscriberID", "SubscriberKey"):
                add(IdType.INTERNAL_PERSON, value, Scope.INDIVIDUAL, offset,
                    f"$.emailData[{index}].Value")

        customer = data.get("customer")
        if isinstance(customer, list) and customer and isinstance(customer[0], dict):
            for index, record in enumerate(customer[0].get("emailActivity") or []):
                if isinstance(record, dict):
                    value = _text(record.get("emailAddress"))
                    if value:
                        add(IdType.EMAIL, value, Scope.INDIVIDUAL,
                            cursor.find(f'"{value}"'),
                            f"$.customer[0].emailActivity[{index}].emailAddress")

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
        """The two populations the report mixes, separated.

        Propensity axes are computable from the baskets in this same report.
        Age, education level, income band and cruise likelihood are not: nothing
        in a grocery basket says any of them. They were obtained elsewhere, and
        the response does not say where — which is also what SOURCES being
        ABSENT means in practice.
        """
        inferences: list[Inference] = []
        inferences += self._propensities(blobs, provenance)
        inferences += self._appended_attributes(blobs, provenance)
        if not inferences:
            warnings.info(
                "No inferred attributes were found. If the report has a "
                "personalised-advertising section, its shape has changed."
            )
        return tuple(inferences)

    def _propensities(self, blobs, provenance) -> list[Inference]:
        """Five named axes with prose values, keyed beside a loyalty id."""
        blob = reader.blob_with_keys(blobs, *PROPENSITY_AXES)
        if blob is None:
            blob = reader.blob_for_header(blobs, reader.ADVERTISING_HEADER)
            if blob is None or not isinstance(blob.data, dict):
                return []
            customer = blob.data.get("customer")
            source = (
                customer[0].get("propensities", {})
                if isinstance(customer, list) and customer else {}
            )
            path = "$.customer[0].propensities"
        else:
            source = {k: v for k, v in blob.data.items() if k in PROPENSITY_AXES}
            path = "$"

        cursor = Cursor(blob.raw, blob.start)
        out = []
        for label, value in source.items():
            raw = _text(value)
            if raw is None:
                continue
            out.append(
                Inference(
                    label=str(label),
                    value_raw=raw,
                    origin=InferenceOrigin.FIRST_PARTY_MODEL,
                    scale=Scale.CATEGORICAL,
                    subject=Scope.INDIVIDUAL,
                    derivable_from_txns=True,
                    provenance=provenance(cursor.find(f'"{label}"'), f"{path}.{label}"),
                )
            )
        return out

    def _appended_attributes(self, blobs, provenance) -> list[Inference]:
        """Attributes the report groups by whether they describe you or your household.

        The retailer states the subject itself — an `Individual` and a
        `Household` bucket — which is better evidence than any guess from a
        field name. The household bucket is the one worth staring at: it
        describes people who never enrolled in anything.
        """
        blob = reader.blob_with_keys(blobs, "Individual") or reader.blob_with_keys(
            blobs, "Household"
        )
        if blob is not None:
            groups = [
                (subject, row)
                for subject, rows in blob.data.items()
                for row in (rows if isinstance(rows, list) else [rows])
                if isinstance(row, dict)
            ]
            path_for = lambda subject, label: f"$.{subject}[0].{label!r}"  # noqa: E731
        else:
            legacy = reader.blob_for_header(blobs, reader.ADVERTISING_HEADER)
            if legacy is None or not isinstance(legacy.data, dict):
                return []
            customer = legacy.data.get("customer")
            first = customer[0] if isinstance(customer, list) and customer else {}
            groups = [
                (group, first.get(group) or {})
                for group in ("demographics", "likelihoods")
            ]
            blob = legacy
            path_for = lambda subject, label: f"$.customer[0].{subject}.{label}"  # noqa: E731

        cursor = Cursor(blob.raw, blob.start)
        out = []
        for subject, row in groups:
            scope = (
                Scope.HOUSEHOLD
                if str(subject).lower() == "household"
                else Scope.INDIVIDUAL
            )
            for label, value in row.items():
                raw = _text(value)
                if raw is None:
                    continue
                if scope is Scope.INDIVIDUAL and str(subject).lower() not in (
                    "individual", "demographics", "likelihoods"
                ):
                    continue
                scale, number = self._scale_and_number(str(label), value)
                out.append(
                    Inference(
                        label=str(label),
                        value_raw=raw,
                        origin=InferenceOrigin.APPENDED_THIRD_PARTY,
                        value_num=number,
                        scale=scale,
                        subject=(
                            Scope.HOUSEHOLD
                            if scope is Scope.HOUSEHOLD
                            or str(label) in HOUSEHOLD_ATTRIBUTES
                            else Scope.INDIVIDUAL
                        ),
                        derivable_from_txns=self._derivable(str(label)),
                        provenance=provenance(
                            cursor.find(f'"{label}"'), path_for(subject, label)
                        ),
                    )
                )
        return out

    @staticmethod
    def _derivable(label: str) -> bool | None:
        """Could the baskets in this report account for this value?

        A separate question from where it came from, and the answer is
        three-valued. Pet ownership is right there in the line items. Nothing
        here explains a year of birth. And where the report withholds what would
        settle it — an online-shopping score with no channel field disclosed —
        the honest answer is that we cannot tell.
        """
        lowered = label.lower()
        if any(hint in lowered for hint in DERIVABLE_LABELS):
            return True
        if "online" in lowered and ("shop" in lowered or "purchas" in lowered):
            return None
        return DERIVABLE_FROM_TXNS.get(label, False)

    @staticmethod
    def _scale_and_number(label: str, value: Any) -> tuple[Scale, float | None]:
        raw = _text(value) or ""
        if ORDINAL_LABEL.search(label) or ORDINAL_PREFIX.match(raw):
            match = ORDINAL_PREFIX.match(raw)
            return Scale.ORDINAL_1_7, float(match.group(1)) if match else _number(value)
        number = _number(value)
        if number is not None and not isinstance(value, bool):
            currency = "$" in label or "income" in label.lower()
            return (Scale.CURRENCY if currency else Scale.COUNT), number
        return Scale.CATEGORICAL, None

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
                            f"Ask {DISPLAY_NAME} to disclose "
                            f"{disclosure.category.label}, which the response did "
                            "not address."
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
