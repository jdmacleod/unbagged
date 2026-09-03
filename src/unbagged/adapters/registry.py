"""Choosing an adapter for a bundle.

Adapters declare confidence via `sniff()`; the registry picks the highest scorer.
The retailer the user declared on the upload form is a hint, not an instruction —
people mislabel, and a Safeway response forwarded by a Kroger subsidiary is still
a Safeway response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from unbagged.models import RetailerAdapter, SourceBundle

log = logging.getLogger(__name__)

# Below this, a match is a guess rather than a finding, and the caller should say
# so rather than silently parsing with the wrong adapter.
MIN_CONFIDENCE = 0.25


@dataclass(frozen=True)
class Match:
    adapter: RetailerAdapter
    confidence: float

    @property
    def is_confident(self) -> bool:
        return self.confidence >= MIN_CONFIDENCE


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, RetailerAdapter] = {}

    def register(self, adapter: RetailerAdapter) -> RetailerAdapter:
        if adapter.retailer_id in self._adapters:
            raise ValueError(f"adapter {adapter.retailer_id!r} is already registered")
        self._adapters[adapter.retailer_id] = adapter
        return adapter

    def get(self, retailer_id: str) -> RetailerAdapter | None:
        return self._adapters.get(retailer_id)

    def all(self) -> tuple[RetailerAdapter, ...]:
        return tuple(self._adapters[k] for k in sorted(self._adapters))

    def score(self, bundle: SourceBundle) -> list[Match]:
        """Every adapter's confidence, best first.

        `sniff()` must not raise, but an adapter that does raise must not take the
        upload down with it: a broken third-party adapter would otherwise make
        every report unparseable. It scores zero and is logged.
        """
        matches = []
        for adapter in self.all():
            try:
                confidence = float(adapter.sniff(bundle))
            except Exception:
                log.exception("adapter %s raised in sniff()", adapter.retailer_id)
                confidence = 0.0
            matches.append(Match(adapter, max(0.0, min(1.0, confidence))))
        return sorted(matches, key=lambda m: (-m.confidence, m.adapter.retailer_id))

    def select(self, bundle: SourceBundle) -> Match | None:
        """The best match, or None when nothing recognises the bundle."""
        matches = self.score(bundle)
        if not matches or matches[0].confidence <= 0.0:
            return None
        return matches[0]


registry = AdapterRegistry()


def register(adapter: RetailerAdapter) -> RetailerAdapter:
    return registry.register(adapter)
