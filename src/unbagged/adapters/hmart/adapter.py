"""H Mart adapter — not yet written.

Scores 0.0 and raises on parse. See NOTES.md.
"""

from __future__ import annotations

from unbagged.adapters.base import (
    AdapterError,
    ParseResult,
    SniffResult,
    SourceBundle,
)

RETAILER_ID = "hmart"
DISPLAY_NAME = "H Mart"
SCHEMA_VERSION = 0


class HMartAdapter:
    retailer_id = RETAILER_ID
    display_name = DISPLAY_NAME
    schema_version = SCHEMA_VERSION

    def sniff(self, bundle: SourceBundle) -> SniffResult:
        return SniffResult(
            0.0,
            "The H Mart adapter has not been written yet, so this response was "
            "read by the generic fallback instead.",
        )

    def parse(self, bundle: SourceBundle) -> ParseResult:
        raise AdapterError(
            "The H Mart adapter has not been written yet. Your response was read "
            "by the generic fallback instead, which records what the response did "
            "and did not address. See docs/writing-an-adapter.md."
        )


adapter = HMartAdapter()
