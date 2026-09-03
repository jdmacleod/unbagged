"""Drafting a supplemental request from what a response left out.

The tool never sends anything. It produces plain text the user reads, edits and
sends themselves, and the text reports observations rather than accusations: it
says which categories the response did not address, not that anyone broke the
law. See docs/legal-basis.md.
"""

from __future__ import annotations

from collections.abc import Sequence

from unbagged.models import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    RequestMeta,
)

CITATIONS: dict[DisclosureCategory, str] = {
    DisclosureCategory.CATEGORIES_COLLECTED: "1798.110(a)(1)",
    DisclosureCategory.SOURCES: "1798.110(a)(2)",
    DisclosureCategory.BUSINESS_PURPOSE: "1798.110(a)(3)",
    DisclosureCategory.THIRD_PARTIES_SHARED_WITH: "1798.110(a)(4)",
    DisclosureCategory.SPECIFIC_PIECES: "1798.110(a)(5)",
    DisclosureCategory.SOLD_OR_SHARED: "1798.115(a)(2)-(3)",
    DisclosureCategory.DISCLOSED_FOR_BUSINESS_PURPOSE: "1798.115(a)(3)",
    DisclosureCategory.RETENTION_PERIOD: "1798.100(a)(3)",
}

READABLE: dict[DisclosureCategory, str] = {
    DisclosureCategory.CATEGORIES_COLLECTED:
        "the categories of personal information you collected about me",
    DisclosureCategory.SOURCES:
        "the categories of sources you collected it from",
    DisclosureCategory.BUSINESS_PURPOSE:
        "the business or commercial purpose for collecting it",
    DisclosureCategory.THIRD_PARTIES_SHARED_WITH:
        "the categories of third parties you disclose it to",
    DisclosureCategory.SPECIFIC_PIECES:
        "the specific pieces of personal information you hold about me",
    DisclosureCategory.SOLD_OR_SHARED:
        "whether you sold or shared it, and with which categories of third parties",
    DisclosureCategory.DISCLOSED_FOR_BUSINESS_PURPOSE:
        "the categories you disclosed for a business purpose",
    DisclosureCategory.RETENTION_PERIOD:
        "how long you retain each category, or the criteria you use to decide",
}

DISCLAIMER = (
    "Read this before sending. It reports what the response did not contain; it "
    "does not assert that anyone broke the law, and it is not legal advice."
)


def _cite(category: DisclosureCategory) -> str:
    citation = CITATIONS.get(category)
    return f"Civ. Code § {citation}" if citation else "the CCPA"


def draft_follow_up(
    meta: RequestMeta,
    disclosures: Sequence[Disclosure],
    follow_ups: Sequence[FollowUpAction] = (),
) -> dict[str, object]:
    """Return the letter text plus what it was built from."""
    absent = [d for d in disclosures if d.status is DisclosureStatus.ABSENT]
    partial = [d for d in disclosures if d.status is DisclosureStatus.PARTIAL]

    reference = (
        f" (your reference {meta.report_reference})" if meta.report_reference else ""
    )
    lines = [
        f"To: {meta.display_name} Privacy Office",
        f"Subject: Supplemental request under the CCPA{reference}",
        "",
        "Hello,",
        "",
        "Thank you for your response to my request for access to the personal "
        "information you hold about me.",
        "",
    ]

    if absent or partial:
        lines += [
            "Having reviewed it, I did not find information addressing the "
            "following. I am requesting it under the sections noted:",
            "",
        ]
        for disclosure in absent:
            lines.append(
                f"  - {READABLE[disclosure.category]} ({_cite(disclosure.category)})"
            )
        for disclosure in partial:
            lines.append(
                f"  - {READABLE[disclosure.category]} ({_cite(disclosure.category)}), "
                "which your response addressed only in part"
            )
    else:
        lines.append(
            "Your response addressed each of the categories I checked for, and I "
            "have no further questions about them."
        )

    if any(f.kind is FollowUpKind.SUPPLEMENTAL_PERIOD for f in follow_ups):
        lines += [
            "",
            "I am also requesting the earlier period your response referred to. It "
            "stated that data outside the window you provided is available on "
            "request, and I would like that data as well.",
        ]

    lines += [
        "",
        "Please confirm receipt and let me know if you need anything further to "
        "verify my identity.",
        "",
        "Thank you,",
        "",
        "[your name]",
        "[the email address or loyalty account used for the original request]",
    ]

    return {
        "letter": "\n".join(lines),
        "absent_categories": [str(d.category) for d in absent],
        "partial_categories": [str(d.category) for d in partial],
        "note": DISCLAIMER,
    }
