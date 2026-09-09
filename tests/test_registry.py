import logging

import pytest

from unbagged.adapters.base import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    ParseResult,
    Provenance,
    RequestMeta,
    Severity,
    SniffResult,
    SourceBundle,
    WarningCollector,
    absent_disclosures,
)
from unbagged.adapters.registry import MIN_CONFIDENCE, AdapterRegistry


class FakeAdapter:
    """An adapter that returns a bare float, which most of them do."""

    def __init__(self, retailer_id: str, confidence: float = 0.0, raises: bool = False):
        self.retailer_id = retailer_id
        self.display_name = retailer_id.title()
        self.schema_version = 1
        self._confidence = confidence
        self._raises = raises

    def sniff(self, bundle: SourceBundle) -> float:
        if self._raises:
            raise RuntimeError("third-party adapter is broken")
        return self._confidence

    def parse(self, bundle: SourceBundle) -> ParseResult:
        return ParseResult(request=RequestMeta(self.retailer_id, self.display_name))


class ExplainingAdapter(FakeAdapter):
    """An adapter that says why, which is what SniffResult exists for."""

    def __init__(self, retailer_id: str, confidence: float, reason: str | None):
        super().__init__(retailer_id, confidence)
        self._reason = reason

    def sniff(self, bundle: SourceBundle) -> SniffResult:
        return SniffResult(self._confidence, self._reason)


class GarbageAdapter(FakeAdapter):
    """An adapter that returns something that is not a score at all."""

    def __init__(self, retailer_id: str, outcome):
        super().__init__(retailer_id)
        self._outcome = outcome

    def sniff(self, bundle: SourceBundle):
        return self._outcome


BUNDLE = SourceBundle()


class TestRegistry:
    def test_highest_scorer_wins(self):
        reg = AdapterRegistry()
        reg.register(FakeAdapter("kroger", 0.9))
        reg.register(FakeAdapter("safeway", 0.2))
        assert reg.select(BUNDLE).adapter.retailer_id == "kroger"

    def test_nothing_recognises_the_bundle(self):
        reg = AdapterRegistry()
        reg.register(FakeAdapter("safeway", 0.0))
        assert reg.select(BUNDLE) is None

    def test_an_empty_registry_selects_nothing(self):
        assert AdapterRegistry().select(BUNDLE) is None

    def test_a_broken_sniff_scores_zero_instead_of_breaking_the_upload(self, caplog):
        # One third-party adapter raising must not make every report unparseable.
        reg = AdapterRegistry()
        reg.register(FakeAdapter("broken", raises=True))
        reg.register(FakeAdapter("kroger", 0.8))
        with caplog.at_level(logging.ERROR):
            match = reg.select(BUNDLE)
        assert match.adapter.retailer_id == "kroger"
        assert "broken" in caplog.text

    def test_confidence_is_clamped(self):
        reg = AdapterRegistry()
        reg.register(FakeAdapter("liar", 4.0))
        assert reg.select(BUNDLE).confidence == 1.0

    def test_a_weak_match_is_reported_as_a_guess(self):
        reg = AdapterRegistry()
        reg.register(FakeAdapter("maybe", MIN_CONFIDENCE / 2))
        match = reg.select(BUNDLE)
        assert match is not None
        assert not match.is_confident

    def test_ties_break_deterministically(self):
        reg = AdapterRegistry()
        reg.register(FakeAdapter("zebra", 0.5))
        reg.register(FakeAdapter("aardvark", 0.5))
        assert reg.select(BUNDLE).adapter.retailer_id == "aardvark"

    def test_a_bare_float_still_works(self):
        # Most adapters have nothing to explain and keep returning a number.
        reg = AdapterRegistry()
        reg.register(FakeAdapter("plain", 0.7))
        match = reg.select(BUNDLE)
        assert match.confidence == 0.7
        assert match.reason is None

    def test_a_reason_survives_to_the_match(self):
        # The whole point: a score that needs explaining can carry the
        # explanation, instead of arriving as a number nobody can interpret.
        reg = AdapterRegistry()
        reg.register(ExplainingAdapter("bounded", 0.0, "gave up after 256 KB"))
        scored = reg.score(BUNDLE)
        assert scored[0].confidence == 0.0
        assert scored[0].reason == "gave up after 256 KB"

    def test_a_reason_is_optional_on_a_result(self):
        reg = AdapterRegistry()
        reg.register(ExplainingAdapter("quiet", 0.4, None))
        assert reg.select(BUNDLE).reason is None

    def test_an_explained_score_is_clamped_like_any_other(self):
        reg = AdapterRegistry()
        reg.register(ExplainingAdapter("liar", 9.0, "very sure"))
        assert reg.select(BUNDLE).confidence == 1.0

    @pytest.mark.parametrize("outcome", ["very sure", None, object(), float("nan")])
    def test_a_score_that_is_not_a_score_is_treated_as_a_broken_adapter(
        self, outcome, caplog
    ):
        # Reading the answer happens inside the same guard as the call. A word,
        # a None, or a NaN would otherwise reach the sort: NaN compares false
        # against everything, so it survives the clamp and then orders the
        # contest at random, which is a coin toss nothing on screen explains.
        reg = AdapterRegistry()
        reg.register(GarbageAdapter("broken", outcome))
        reg.register(FakeAdapter("kroger", 0.8))
        with caplog.at_level(logging.ERROR):
            match = reg.select(BUNDLE)
        assert match.adapter.retailer_id == "kroger"
        assert "broken" in caplog.text

    def test_double_registration_is_rejected(self):
        reg = AdapterRegistry()
        reg.register(FakeAdapter("kroger"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(FakeAdapter("kroger"))

    def test_lookup_by_id(self):
        reg = AdapterRegistry()
        adapter = reg.register(FakeAdapter("kroger"))
        assert reg.get("kroger") is adapter
        assert reg.get("hmart") is None


class TestAbsentDisclosures:
    def test_missing_categories_become_explicit_findings(self):
        found = {
            DisclosureCategory.SPECIFIC_PIECES: Disclosure(
                DisclosureCategory.SPECIFIC_PIECES, DisclosureStatus.PROVIDED
            )
        }
        complete = absent_disclosures(found, note="No such section in the report.")
        assert len(complete) == len(DisclosureCategory)
        by_category = {d.category: d for d in complete}
        assert by_category[DisclosureCategory.SOURCES].status is DisclosureStatus.ABSENT
        assert by_category[DisclosureCategory.SOURCES].notes

    def test_what_was_found_is_left_alone(self):
        provided = Disclosure(
            DisclosureCategory.SPECIFIC_PIECES, DisclosureStatus.PROVIDED,
            evidence="Section 1"
        )
        complete = absent_disclosures({provided.category: provided}, note="n/a")
        assert provided in complete

    def test_the_result_always_covers_every_category(self):
        result = ParseResult(
            request=RequestMeta("kroger", "Kroger"),
            disclosures=absent_disclosures({}, note="nothing found"),
        )
        assert result.missing_categories() == ()

    def test_provenance_is_carried_onto_absences(self):
        # "We looked at this document and it did not say" is a stronger claim than
        # "we have no record", so the absence points at where it was looked for.
        prov = Provenance(source_document_id=1, page=1, locator="$")
        complete = absent_disclosures({}, note="n/a", provenance=prov)
        assert all(d.provenance == prov for d in complete)


class TestWarningCollector:
    def test_collects_in_order_with_severities(self):
        warnings = WarningCollector()
        warnings.info("skipped an empty section", locator="$.a")
        warnings.add("basket 37 had a malformed item list", locator="$.b")
        warnings.error("could not read the purchase blob at all")
        assert len(warnings) == 3
        assert [w.severity for w in warnings.as_tuple()] == [
            Severity.INFO, Severity.WARNING, Severity.ERROR
        ]
        assert warnings.as_tuple()[1].locator == "$.b"

    def test_an_empty_collector_is_falsy(self):
        assert not WarningCollector()
