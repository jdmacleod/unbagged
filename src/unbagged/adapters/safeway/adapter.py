"""Safeway (Albertsons) adapter — not yet written.

Scores 0.0 and raises on parse, so a Safeway response falls through to the
generic fallback rather than being mangled by a guess. See NOTES.md for what to
expect when a real response arrives.
"""

from __future__ import annotations

from unbagged.adapters.base import (
    AdapterError,
    ParseResult,
    SniffResult,
    SourceBundle,
)

RETAILER_ID = "safeway"
DISPLAY_NAME = "Safeway"
SCHEMA_VERSION = 0


class SafewayAdapter:
    retailer_id = RETAILER_ID
    display_name = DISPLAY_NAME
    # 0 means "no format has been observed yet". Bump to 1 with the first parse.
    schema_version = SCHEMA_VERSION

    def sniff(self, bundle: SourceBundle) -> SniffResult:
        # Deliberately never claims a bundle. Recognising Safeway's format
        # without being able to parse it would take the response away from the
        # generic fallback, which can at least record what is missing.
        #
        # The zero carries its reason rather than travelling alone: "nobody has
        # written this yet" and "this is not a Safeway response" are different
        # facts, and a bare score reports them identically.
        return SniffResult(
            0.0,
            "The Safeway adapter has not been written yet, so this response was "
            "read by the generic fallback instead.",
        )

    def parse(self, bundle: SourceBundle) -> ParseResult:
        raise AdapterError(
            "The Safeway adapter has not been written yet — no real response has "
            "been available to build it against. Your response was read by the "
            "generic fallback instead. If you have one, see "
            "docs/writing-an-adapter.md, and send a sanitised skeleton "
            "(`unbagged sanitize`) rather than the report itself."
        )


adapter = SafewayAdapter()
