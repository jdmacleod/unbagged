"""Last-resort adapter for a response with no structured data in it.

Some retailers answer a right-to-know request with a letter. That is not a
parsing failure to be swallowed — **it is the finding**. A response containing no
data at all has not disclosed the specific pieces of personal information held,
and every other category with it.

So this adapter accepts anything readable, records what it could and could not
find, and never pretends to more than it has.

What it deliberately does *not* do: mark a category `PROVIDED`. Keyword matching
can show that a response mentioned a topic. It cannot show the response answered
it, and the difference is the entire point of the compliance view. The strongest
claim available from a keyword is `PARTIAL`, with the sentence quoted so the
reader can judge it themselves.
"""

from __future__ import annotations

import re

from unbagged.adapters.base import (
    AdapterError,
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    ParseResult,
    Provenance,
    RequestMeta,
    Severity,
    SourceBundle,
    WarningCollector,
    absent_disclosures,
)
from unbagged.extraction import extract, extract_all

RETAILER_ID = "generic"
DISPLAY_NAME = "Unidentified retailer"
SCHEMA_VERSION = 1

# Below every real adapter, and the registry prefers a non-fallback regardless.
FALLBACK_CONFIDENCE = 0.1
SNIFF_PAGES = 3

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

# Phrases that indicate a response has *touched* a disclosure category. Matching
# one is evidence the topic came up, never that it was answered.
CATEGORY_HINTS: dict[DisclosureCategory, tuple[str, ...]] = {
    DisclosureCategory.CATEGORIES_COLLECTED: (
        "categories of personal information", "categories of information we collect",
        "types of personal information",
    ),
    DisclosureCategory.SOURCES: (
        "categories of sources", "sources from which", "where we collect",
        "obtained from",
    ),
    DisclosureCategory.BUSINESS_PURPOSE: (
        "business purpose", "commercial purpose", "purpose for collecting",
        "why we collect",
    ),
    DisclosureCategory.THIRD_PARTIES_SHARED_WITH: (
        "third parties", "service providers", "categories of third parties",
        "we disclose to",
    ),
    DisclosureCategory.SPECIFIC_PIECES: (
        "specific pieces", "personal information we hold", "information we hold about you",
    ),
    DisclosureCategory.SOLD_OR_SHARED: (
        "sold or shared", "sale of personal information", "we do not sell",
        "cross-context behavioral advertising",
    ),
    DisclosureCategory.DISCLOSED_FOR_BUSINESS_PURPOSE: (
        "disclosed for a business purpose", "disclosed for business purposes",
    ),
    DisclosureCategory.RETENTION_PERIOD: (
        "retention period", "how long we retain", "we retain", "retention schedule",
    ),
}

NO_DATA_HINTS = (
    "we do not hold", "no personal information", "we have no record",
    "unable to locate", "no records were found",
)


def _sentence_containing(text: str, phrase: str) -> str | None:
    index = text.lower().find(phrase)
    if index < 0:
        return None
    for match in SENTENCE.finditer(text):
        if match.start() <= index < match.end():
            return " ".join(match.group(0).split())[:400]
    return None


class GenericAdapter:
    retailer_id = RETAILER_ID
    display_name = DISPLAY_NAME
    schema_version = SCHEMA_VERSION
    # Consulted only when no real adapter recognises the bundle.
    fallback = True

    def sniff(self, bundle: SourceBundle) -> float:
        for document in bundle.documents:
            try:
                if extract(document, max_pages=SNIFF_PAGES).text.strip():
                    return FALLBACK_CONFIDENCE
            except Exception:
                continue
        return 0.0

    def parse(self, bundle: SourceBundle) -> ParseResult:
        warnings = WarningCollector()
        documents = extract_all(bundle.documents)
        if not documents:
            raise AdapterError(
                "None of the uploaded files could be read as text. A scanned PDF "
                "with no text layer looks like this, and OCR is out of scope."
            )

        document = max(documents, key=lambda d: len(d.text))
        text = document.text
        provenance = Provenance(source_document_id=document.document_id, page=1, locator="$")

        warnings.add(
            "No adapter recognised this response, so it was read as unstructured "
            "text. Purchases, identifiers and inferred attributes were not "
            "extracted — not because the retailer withheld them, but because "
            "nothing here knows this format. See docs/writing-an-adapter.md.",
            severity=Severity.WARNING,
        )

        found: dict[DisclosureCategory, Disclosure] = {}
        for category, phrases in CATEGORY_HINTS.items():
            for phrase in phrases:
                sentence = _sentence_containing(text, phrase)
                if sentence:
                    found[category] = Disclosure(
                        category=category,
                        # PARTIAL, never PROVIDED. A keyword shows the topic was
                        # mentioned; only a person can say it was answered.
                        status=DisclosureStatus.PARTIAL,
                        evidence=sentence,
                        notes=(
                            "Matched on wording alone. Read the quoted sentence and "
                            "decide for yourself whether it answers the question."
                        ),
                        provenance=provenance,
                    )
                    break

        follow_ups = [
            FollowUpAction(
                kind=FollowUpKind.CLARIFICATION,
                description=(
                    "This response was read as plain text because no adapter knows "
                    "its format. Check it by hand before relying on the matrix "
                    "below, and consider contributing an adapter for this retailer."
                ),
            )
        ]

        lowered = text.lower()
        if any(hint in lowered for hint in NO_DATA_HINTS):
            follow_ups.append(
                FollowUpAction(
                    kind=FollowUpKind.CLARIFICATION,
                    description=(
                        "The response appears to state that little or no personal "
                        "information is held. If you have a loyalty account with this "
                        "retailer, that is worth querying."
                    ),
                )
            )

        for category in DisclosureCategory:
            if category not in found:
                follow_ups.append(
                    FollowUpAction(
                        kind=FollowUpKind.MISSING_CATEGORY,
                        description=(
                            f"No wording in this response addresses "
                            f"{category.label}."
                        ),
                    )
                )

        declared = (bundle.declared_retailer or "").strip()
        return ParseResult(
            request=RequestMeta(
                retailer_id=declared.lower().replace(" ", "-") or RETAILER_ID,
                display_name=declared or DISPLAY_NAME,
                adapter_schema_version=SCHEMA_VERSION,
            ),
            disclosures=absent_disclosures(
                found,
                note=(
                    "No wording in this response addresses this category. Read as "
                    "plain text, so a phrasing this adapter does not know could "
                    "have been missed."
                ),
                provenance=provenance,
            ),
            follow_ups=tuple(follow_ups),
            warnings=warnings.as_tuple(),
        )


adapter = GenericAdapter()
