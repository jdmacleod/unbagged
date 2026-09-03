"""Canonical domain model.

Adapters produce these; the repository writes them; the API reads them back.
Nothing downstream of an adapter knows which retailer it is looking at.

Two rules from HANDOFF.md §4 are encoded here rather than left to convention:

* **Every emitted record carries provenance.** `Provenance` is a required field on
  everything an adapter emits, so "where did this come from" is answerable for any
  cell on screen.
* **Absence is a finding.** `DisclosureStatus.ABSENT` is a value, not a missing row.
  Silence in the data model is indistinguishable from "not yet parsed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class DisclosureCategory(StrEnum):
    """The CCPA/CPRA disclosure obligations, one column of the compliance matrix
    each. Keys are stable: `docs/legal-basis.md` maps them to their citations, and
    the matrix columns are generated from this enum."""

    CATEGORIES_COLLECTED = "CATEGORIES_COLLECTED"
    SOURCES = "SOURCES"
    BUSINESS_PURPOSE = "BUSINESS_PURPOSE"
    THIRD_PARTIES_SHARED_WITH = "THIRD_PARTIES_SHARED_WITH"
    SPECIFIC_PIECES = "SPECIFIC_PIECES"
    SOLD_OR_SHARED = "SOLD_OR_SHARED"
    DISCLOSED_FOR_BUSINESS_PURPOSE = "DISCLOSED_FOR_BUSINESS_PURPOSE"
    RETENTION_PERIOD = "RETENTION_PERIOD"

    @property
    def label(self) -> str:
        """Readable third-person name, for text a person will read."""
        return CATEGORY_LABELS[self.value]


# Readable names for the disclosure categories, in the third person. The enum
# keys are an interface and stay stable; these are what a person reads. Kept
# beside the enum so a new category cannot be added without naming it, and
# separate from letters.py, whose phrasing is second-person letter voice.
CATEGORY_LABELS: dict[str, str] = {
    "CATEGORIES_COLLECTED": "the categories of personal information collected",
    "SOURCES": "the categories of sources it was collected from",
    "BUSINESS_PURPOSE": "the business or commercial purpose for collecting it",
    "THIRD_PARTIES_SHARED_WITH": "the categories of third parties it is disclosed to",
    "SPECIFIC_PIECES": "the specific pieces of personal information held",
    "SOLD_OR_SHARED": "whether it was sold or shared, and with whom",
    "DISCLOSED_FOR_BUSINESS_PURPOSE": "the categories disclosed for a business purpose",
    "RETENTION_PERIOD": "how long each category is retained",
}


class DisclosureStatus(StrEnum):
    PROVIDED = "provided"
    PARTIAL = "partial"
    ABSENT = "absent"


class InferenceOrigin(StrEnum):
    """Where an attribute most likely came from.

    The distinction between a propensity score a retailer computed from your own
    baskets and a household-income estimate it bought from a data broker is the
    most interesting thing this tool says. Adapters must justify the call in
    their NOTES.md.
    """

    FIRST_PARTY_MODEL = "first_party_model"
    APPENDED_THIRD_PARTY = "appended_third_party"
    UNKNOWN = "unknown"


class IdType(StrEnum):
    NAME = "name"
    LOYALTY_CARD = "loyalty_card"
    ALTERNATE_ID = "alternate_id"
    HOUSEHOLD = "household"
    INTERNAL_PERSON = "internal_person"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"


class Scope(StrEnum):
    """Whether a record describes one person or everyone at an address.

    Household-scoped records describe people who never enrolled in anything,
    which the profile view calls out specifically.
    """

    INDIVIDUAL = "individual"
    HOUSEHOLD = "household"


class Channel(StrEnum):
    IN_STORE = "in_store"
    ONLINE = "online"
    FUEL = "fuel"
    PHARMACY = "pharmacy"


class Scale(StrEnum):
    CATEGORICAL = "categorical"
    ORDINAL_1_7 = "ordinal_1_7"
    CURRENCY = "currency"
    COUNT = "count"
    PROSE = "prose"


class FollowUpKind(StrEnum):
    SUPPLEMENTAL_PERIOD = "supplemental_period"
    MISSING_CATEGORY = "missing_category"
    CLARIFICATION = "clarification"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Provenance:
    """Where a record came from, precisely enough to show the user.

    `locator` is format-specific — a JSON path for Kroger, a line range for a
    letter, a cell reference for a CSV. It is opaque to everything except the
    adapter that produced it and the human reading it.
    """

    source_document_id: int | None = None
    page: int | None = None
    locator: str | None = None


@dataclass(frozen=True)
class SourceDocument:
    """One file the user handed us, already hashed and stored."""

    original_filename: str
    sha256: str
    media_type: str | None = None
    page_count: int | None = None
    path: str | None = None      # inside the data volume; never persisted to the DB
    id: int | None = None


@dataclass(frozen=True)
class SourceBundle:
    """Everything the user handed us for one request."""

    documents: tuple[SourceDocument, ...] = ()
    declared_retailer: str | None = None   # user hint from the upload form, may be wrong

    def text_documents(self) -> tuple[SourceDocument, ...]:
        return tuple(d for d in self.documents if d.path)


@dataclass(frozen=True)
class Identity:
    id_type: IdType
    value: str
    scope: Scope | None = None
    first_seen: str | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(frozen=True)
class TxnItem:
    """One line on one receipt.

    `description_raw` is stored exactly as it appeared. Adapters never mutate
    values; category assignment happens in a separate enrichment pass so the
    original is always recoverable.
    """

    description_raw: str
    upc: str | None = None
    quantity: float | None = None
    retail_amt: float | None = None
    loyalty_amt: float | None = None
    category: str | None = None
    category_confidence: float | None = None


@dataclass(frozen=True)
class Transaction:
    occurred_at: str
    items: tuple[TxnItem, ...] = ()
    external_order_id: str | None = None
    store_code: str | None = None
    division_code: str | None = None
    channel: Channel | None = None
    tender_type: str | None = None
    total_pre_discount: float | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(frozen=True)
class Inference:
    label: str
    value_raw: str
    origin: InferenceOrigin
    value_num: float | None = None
    scale: Scale | None = None
    subject: Scope | None = None
    derivable_from_txns: bool | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(frozen=True)
class Disclosure:
    category: DisclosureCategory
    status: DisclosureStatus
    evidence: str | None = None
    notes: str | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(frozen=True)
class FollowUpAction:
    kind: FollowUpKind
    description: str
    resolved: bool = False


@dataclass(frozen=True)
class ParseWarning:
    message: str
    severity: Severity = Severity.WARNING
    locator: str | None = None


@dataclass(frozen=True)
class RequestMeta:
    """What the report says about itself."""

    retailer_id: str
    display_name: str
    report_reference: str | None = None
    submitted_at: str | None = None
    received_at: str | None = None
    statute: str = "CCPA"
    period_start: str | None = None
    period_end: str | None = None
    adapter_schema_version: int | None = None


@dataclass(frozen=True)
class ParseResult:
    """Everything one adapter got out of one bundle."""

    request: RequestMeta
    identities: tuple[Identity, ...] = ()
    transactions: tuple[Transaction, ...] = ()
    inferences: tuple[Inference, ...] = ()
    disclosures: tuple[Disclosure, ...] = ()
    follow_ups: tuple[FollowUpAction, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()

    def missing_categories(self) -> tuple[DisclosureCategory, ...]:
        """Categories with no disclosure record at all.

        An adapter is supposed to emit an explicit ABSENT for every category it
        did not find, so a non-empty result here means the adapter is incomplete
        rather than the retailer.
        """
        seen = {d.category for d in self.disclosures}
        return tuple(c for c in DisclosureCategory if c not in seen)

    def item_count(self) -> int:
        return sum(len(t.items) for t in self.transactions)


class AdapterError(Exception):
    """Raised by parse() with a message that can be shown to the user verbatim."""


class RetailerAdapter(Protocol):
    retailer_id: str        # "kroger"
    display_name: str       # "Kroger"
    schema_version: int     # bump when the retailer changes their format

    def sniff(self, bundle: SourceBundle) -> float:
        """Confidence 0.0-1.0 that this adapter handles this bundle.
        Must be cheap and must not raise. The registry picks the highest scorer."""

    def parse(self, bundle: SourceBundle) -> ParseResult:
        """Full parse. May raise AdapterError with a user-readable message."""
