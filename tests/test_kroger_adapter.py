"""The Kroger adapter against its synthetic fixture."""

from pathlib import Path

import pytest

import unbagged.adapters  # noqa: F401  (registers the adapter)
from unbagged.adapters.kroger.adapter import KrogerAdapter
from unbagged.adapters.registry import registry
from unbagged.models import (
    DisclosureCategory,
    DisclosureStatus,
    FollowUpKind,
    IdType,
    InferenceOrigin,
    Scale,
    Scope,
    SourceBundle,
    SourceDocument,
)

FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)


def bundle_for(path: Path, **kwargs) -> SourceBundle:
    document = SourceDocument(
        original_filename=path.name, sha256="0" * 64, path=str(path), id=1
    )
    return SourceBundle(documents=(document,), **kwargs)


@pytest.fixture(scope="module")
def result():
    return KrogerAdapter().parse(bundle_for(FIXTURE))


class TestSelection:
    def test_the_registry_picks_kroger_for_a_kroger_report(self):
        match = registry.select(bundle_for(FIXTURE))
        assert match.adapter.retailer_id == "kroger"
        assert match.is_confident

    def test_content_beats_a_wrong_user_hint(self):
        # People mislabel the upload form; the report itself is the evidence.
        match = registry.select(bundle_for(FIXTURE, declared_retailer="safeway"))
        assert match.adapter.retailer_id == "kroger"

    def test_an_unrelated_document_scores_low(self, tmp_path):
        letter = tmp_path / "letter.txt"
        letter.write_text("Dear customer, we hold no data about you.\n")
        assert KrogerAdapter().sniff(bundle_for(letter)) < 0.25

    def test_sniff_never_raises(self, tmp_path):
        missing = SourceDocument("gone.pdf", "0" * 64, path=str(tmp_path / "gone.pdf"))
        assert KrogerAdapter().sniff(SourceBundle(documents=(missing,))) == 0.0

    def test_an_empty_bundle_scores_zero(self):
        assert KrogerAdapter().sniff(SourceBundle()) == 0.0


class TestRequestMeta:
    def test_the_report_reference_is_read(self, result):
        assert result.request.report_reference.startswith("SYNTH-")

    def test_the_coverage_window_is_read(self, result):
        assert result.request.period_start < result.request.period_end
        assert result.request.adapter_schema_version == KrogerAdapter.schema_version


class TestIdentities:
    def test_every_documented_identifier_type_is_emitted(self, result):
        assert {i.id_type for i in result.identities} >= {
            IdType.LOYALTY_CARD, IdType.ALTERNATE_ID, IdType.HOUSEHOLD,
            IdType.INTERNAL_PERSON, IdType.EMAIL, IdType.PHONE, IdType.ADDRESS,
        }

    def test_household_identifiers_are_scoped_to_the_household(self, result):
        # These cover people who never enrolled in anything.
        household = [i for i in result.identities if i.scope is Scope.HOUSEHOLD]
        assert {i.id_type for i in household} == {IdType.HOUSEHOLD, IdType.ADDRESS}

    def test_the_address_is_household_scoped(self, result):
        address = next(i for i in result.identities if i.id_type is IdType.ADDRESS)
        assert address.scope is Scope.HOUSEHOLD
        assert "," in address.value

    def test_identifiers_are_not_duplicated(self, result):
        pairs = [(i.id_type, i.value) for i in result.identities]
        assert len(pairs) == len(set(pairs))

    def test_each_identifier_is_traceable(self, result):
        for identity in result.identities:
            assert identity.provenance.locator.startswith("$.customer[0]")
            assert identity.provenance.page >= 1
            assert identity.provenance.source_document_id == 1


class TestTransactions:
    def test_every_basket_becomes_a_transaction(self, result):
        raw = FIXTURE.read_text(encoding="utf-8")
        assert len(result.transactions) == raw.count('"orderno"')

    def test_baskets_carry_their_line_items(self, result):
        assert result.item_count() > 1000
        assert all(t.items for t in result.transactions)

    def test_timestamps_are_sortable_and_in_opening_hours(self, result):
        times = [t.occurred_at for t in result.transactions]
        assert times == sorted(times)
        hours = {int(t[11:13]) for t in times}
        assert min(hours) >= 5 and max(hours) <= 23

    def test_timestamps_carry_no_fabricated_timezone(self, result):
        # The report gives a store-local wall clock with no zone. Asserting UTC
        # would push every evening shop in California into the next day.
        assert not any(t.occurred_at.endswith("Z") for t in result.transactions)

    def test_channel_is_left_unset_rather_than_guessed(self, result):
        # The report has no channel field; in_store would be an invented claim.
        assert all(t.channel is None for t in result.transactions)

    def test_placeholder_rows_are_kept_not_filtered(self, result):
        descriptions = [i.description_raw for t in result.transactions for i in t.items]
        assert "UNKNOWN" in descriptions

    def test_returns_keep_their_negative_amounts(self, result):
        amounts = [i.retail_amt for t in result.transactions for i in t.items]
        assert any(a < 0 for a in amounts)

    def test_descriptions_survive_verbatim(self, result):
        raw = FIXTURE.read_text(encoding="utf-8")
        sample = result.transactions[0].items[0].description_raw
        assert f'"purchasedescription": "{sample}"' in raw

    def test_split_tenders_are_not_silently_halved(self, result):
        # A basket paid with two tenders keeps both; dropping one would rewrite
        # the receipt.
        assert all(t.tender_type for t in result.transactions)

    def test_each_basket_is_traceable_to_a_page(self, result):
        pages = [t.provenance.page for t in result.transactions]
        assert pages == sorted(pages)
        assert result.transactions[0].provenance.locator == "$.customer[0].basket[0]"


class TestInferences:
    def test_propensities_are_classified_as_first_party(self, result):
        first_party = [
            f for f in result.inferences if f.origin is InferenceOrigin.FIRST_PARTY_MODEL
        ]
        assert {f.label for f in first_party} == {
            "Convenience", "Loyalty", "Price", "Quality", "Variety Seeking"
        }
        assert all(f.derivable_from_txns for f in first_party)
        assert all(f.scale is Scale.CATEGORICAL for f in first_party)

    def test_demographics_are_classified_as_appended(self, result):
        """The most interesting output this tool produces.

        Nothing in a grocery basket says how long someone has lived at an
        address, or whether they will take a cruise. These were bought, and the
        report does not say from whom.
        """
        appended = {
            f.label for f in result.inferences
            if f.origin is InferenceOrigin.APPENDED_THIRD_PARTY
        }
        assert {"educationLevel", "householdComposition", "incomePredictorScore",
                "cruiseLikelihood", "lengthOfResidence"} <= appended

    def test_household_attributes_describe_the_household(self, result):
        household = {
            f.label for f in result.inferences if f.subject is Scope.HOUSEHOLD
        }
        assert {"householdComposition", "numberOfAdults", "numberOfChildren",
                "incomePredictorScore"} <= household

    def test_ordinal_scales_keep_both_the_label_and_the_number(self, result):
        cruise = next(f for f in result.inferences if f.label == "cruiseLikelihood")
        assert cruise.scale is Scale.ORDINAL_1_7
        assert 1 <= cruise.value_num <= 7
        assert cruise.value_raw.startswith(str(int(cruise.value_num)))

    def test_derivability_is_tri_state(self, result):
        by_label = {f.label: f.derivable_from_txns for f in result.inferences}
        # Pet food is in these baskets.
        assert by_label["petOwner"] is True
        # Nothing here explains this one.
        assert by_label["educationLevel"] is False
        # Kroger holds its own order data, but this report discloses no channel,
        # so from the data provided it is genuinely unknown.
        assert by_label["onlineShopperLikelihood"] is None

    def test_every_inference_is_traceable(self, result):
        for inference in result.inferences:
            assert inference.provenance.locator.startswith("$.customer[0].")
            assert inference.provenance.page >= 1


class TestDisclosures:
    def test_all_eight_categories_are_accounted_for(self, result):
        assert result.missing_categories() == ()
        assert len(result.disclosures) == len(DisclosureCategory)

    def test_specific_pieces_is_the_only_one_provided(self, result):
        provided = [
            d.category for d in result.disclosures
            if d.status is DisclosureStatus.PROVIDED
        ]
        assert provided == [DisclosureCategory.SPECIFIC_PIECES]

    def test_the_missing_sections_are_recorded_as_findings(self, result):
        # There is no Section 2, 3 or 4. Silence in the data model would be
        # indistinguishable from "not yet parsed".
        absent = {
            d.category for d in result.disclosures
            if d.status is DisclosureStatus.ABSENT
        }
        assert DisclosureCategory.SOURCES in absent
        assert DisclosureCategory.THIRD_PARTIES_SHARED_WITH in absent
        assert DisclosureCategory.RETENTION_PERIOD in absent

    def test_absences_explain_themselves(self, result):
        for disclosure in result.disclosures:
            if disclosure.status is DisclosureStatus.ABSENT:
                assert disclosure.notes


class TestFollowUps:
    def test_the_supplemental_window_is_recorded_not_scored_as_a_failure(self, result):
        supplemental = [
            f for f in result.follow_ups if f.kind is FollowUpKind.SUPPLEMENTAL_PERIOD
        ]
        assert len(supplemental) == 1
        assert "privacy office" in supplemental[0].description

    def test_every_absent_category_gets_a_follow_up(self, result):
        missing = [
            f for f in result.follow_ups if f.kind is FollowUpKind.MISSING_CATEGORY
        ]
        absent = [d for d in result.disclosures if d.status is DisclosureStatus.ABSENT]
        assert len(missing) == len(absent)


class TestCleanParse:
    def test_a_well_formed_report_produces_no_warnings(self, result):
        assert result.warnings == ()
