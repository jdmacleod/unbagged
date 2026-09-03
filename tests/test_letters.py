"""The follow-up letter draft.

The letter is the one output a user sends to somebody else, so its wording is
tested the way its content is: it must report what a response did not contain
and never assert that anyone broke the law.
"""

import pytest

from unbagged.letters import CITATIONS, READABLE, draft_follow_up
from unbagged.models import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    RequestMeta,
)

META = RequestMeta(
    retailer_id="kroger", display_name="Kroger", report_reference="SYNTH-1"
)

ACCUSATIONS = ("violat", "unlawful", "illegal", "breach", "failed to comply", "must ")


def all_absent() -> list[Disclosure]:
    return [Disclosure(c, DisclosureStatus.ABSENT) for c in DisclosureCategory]


class TestCoverage:
    def test_every_category_has_a_citation_and_a_phrasing(self):
        for category in DisclosureCategory:
            assert category in CITATIONS
            assert category in READABLE

    def test_absent_categories_are_named_with_their_citation(self):
        draft = draft_follow_up(META, all_absent())
        for category in DisclosureCategory:
            assert READABLE[category] in draft["letter"]
            assert CITATIONS[category] in draft["letter"]

    def test_partial_categories_are_flagged_as_partial(self):
        disclosures = [
            Disclosure(DisclosureCategory.SOURCES, DisclosureStatus.PARTIAL)
        ]
        draft = draft_follow_up(META, disclosures)
        assert "only in part" in draft["letter"]
        assert draft["partial_categories"] == ["SOURCES"]

    def test_provided_categories_are_not_asked_for_again(self):
        disclosures = [
            Disclosure(DisclosureCategory.SPECIFIC_PIECES, DisclosureStatus.PROVIDED)
        ]
        draft = draft_follow_up(META, disclosures)
        assert draft["absent_categories"] == []
        assert READABLE[DisclosureCategory.SPECIFIC_PIECES] not in draft["letter"]


class TestTone:
    @pytest.mark.parametrize("word", ACCUSATIONS)
    def test_the_draft_never_accuses(self, word):
        # The tool reports observations. Whether an omission is a violation
        # depends on facts it cannot see; see docs/legal-basis.md.
        assert word not in draft_follow_up(META, all_absent())["letter"].lower()

    def test_the_draft_carries_its_own_disclaimer(self):
        assert "not legal advice" in draft_follow_up(META, all_absent())["note"]

    def test_the_user_sends_it_themselves(self):
        # Placeholders, so nobody sends an unread draft with a name in it.
        letter = draft_follow_up(META, all_absent())["letter"]
        assert "[your name]" in letter


class TestDetails:
    def test_the_retailers_own_reference_is_quoted_when_known(self):
        assert "SYNTH-1" in draft_follow_up(META, all_absent())["letter"]

    def test_a_missing_reference_does_not_leave_an_empty_bracket(self):
        meta = RequestMeta(retailer_id="hmart", display_name="H Mart")
        letter = draft_follow_up(meta, all_absent())["letter"]
        assert "()" not in letter
        assert "your reference" not in letter

    def test_the_supplemental_period_is_asked_for_when_one_was_offered(self):
        action = FollowUpAction(FollowUpKind.SUPPLEMENTAL_PERIOD, "earlier data offered")
        letter = draft_follow_up(META, all_absent(), [action])["letter"]
        assert "earlier period" in letter

    def test_no_supplemental_paragraph_when_none_was_offered(self):
        assert "earlier period" not in draft_follow_up(META, all_absent())["letter"]

    def test_a_complete_response_produces_a_letter_that_says_so(self):
        disclosures = [
            Disclosure(c, DisclosureStatus.PROVIDED) for c in DisclosureCategory
        ]
        letter = draft_follow_up(META, disclosures)["letter"]
        assert "no further questions" in letter
