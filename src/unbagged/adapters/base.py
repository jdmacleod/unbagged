"""What an adapter author imports.

Everything an adapter needs is re-exported here, so `docs/writing-an-adapter.md`
can say "import from `unbagged.adapters.base`" and mean it. The canonical
definitions live in `unbagged.models`; this module adds only the pieces that are
specific to writing an adapter.

The five rules from HANDOFF.md §4, restated because they are the whole contract:

1. **Every emitted record carries provenance** — document, page, locator.
2. **Adapters never mutate values.** `description_raw` is stored exactly as it
   appeared; normalisation happens in a later pass.
3. **Absence is a finding.** Emit `Disclosure(..., status=ABSENT)`, never nothing.
4. **Adapters degrade, they don't crash.** One malformed basket costs one
   `ParseWarning` and one skipped record, not the other fifty-three baskets.
5. **Ship a synthetic fixture and a NOTES.md.** The notes are as valuable as the
   code — they are the institutional memory of what the format actually looks like.
"""

from __future__ import annotations

from unbagged.models import (
    AdapterError,
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
    RetailerAdapter,
    Scale,
    Scope,
    Severity,
    SourceBundle,
    SourceDocument,
    Transaction,
    TxnItem,
)

__all__ = [
    "AdapterError",
    "Channel",
    "Disclosure",
    "DisclosureCategory",
    "DisclosureStatus",
    "FollowUpAction",
    "FollowUpKind",
    "Identity",
    "IdType",
    "Inference",
    "InferenceOrigin",
    "ParseResult",
    "ParseWarning",
    "Provenance",
    "RequestMeta",
    "RetailerAdapter",
    "Scale",
    "Scope",
    "Severity",
    "SourceBundle",
    "SourceDocument",
    "Transaction",
    "TxnItem",
    "absent_disclosures",
    "WarningCollector",
]


class WarningCollector:
    """Somewhere for an adapter to put the things it could not parse.

    Rule 4 says adapters degrade rather than crash, which in practice means every
    loop body needs a try/except that records and continues. This makes that cheap
    enough that nobody is tempted to skip it.
    """

    def __init__(self) -> None:
        self._warnings: list[ParseWarning] = []

    def add(
        self,
        message: str,
        *,
        locator: str | None = None,
        severity: Severity = Severity.WARNING,
    ) -> None:
        self._warnings.append(
            ParseWarning(message=message, severity=severity, locator=locator)
        )

    def info(self, message: str, *, locator: str | None = None) -> None:
        self.add(message, locator=locator, severity=Severity.INFO)

    def error(self, message: str, *, locator: str | None = None) -> None:
        self.add(message, locator=locator, severity=Severity.ERROR)

    def as_tuple(self) -> tuple[ParseWarning, ...]:
        return tuple(self._warnings)

    def __len__(self) -> int:
        return len(self._warnings)

    def __bool__(self) -> bool:
        return bool(self._warnings)


def absent_disclosures(
    found: dict[DisclosureCategory, Disclosure],
    *,
    note: str,
    provenance: Provenance | None = None,
) -> tuple[Disclosure, ...]:
    """Return `found`, completed with an explicit ABSENT for every missing category.

    Rule 3 in one function. An adapter builds whatever it actually found and hands
    it here; what comes back always covers all eight categories, because a missing
    row means "not yet parsed" and an ABSENT row means "the retailer did not say".
    Those are different facts and the UI shows them differently.
    """
    complete = []
    for category in DisclosureCategory:
        if category in found:
            complete.append(found[category])
        else:
            complete.append(
                Disclosure(
                    category=category,
                    status=DisclosureStatus.ABSENT,
                    notes=note,
                    provenance=provenance or Provenance(),
                )
            )
    return tuple(complete)
