import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from unbagged.models import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    Identity,
    IdType,
    Inference,
    InferenceOrigin,
    ParseResult,
    Provenance,
    RequestMeta,
    Scale,
    Scope,
    Transaction,
    TxnItem,
)

LEGAL_BASIS = Path(__file__).parent.parent / "docs" / "legal-basis.md"

META = RequestMeta(retailer_id="kroger", display_name="Kroger")


class TestEnums:
    def test_disclosure_categories_match_the_documented_set(self):
        # The compliance view's rows are generated from this enum, so the keys
        # are an interface. Changing one is a schema change, not a rename.
        assert [c.value for c in DisclosureCategory] == [
            "CATEGORIES_COLLECTED",
            "SOURCES",
            "BUSINESS_PURPOSE",
            "THIRD_PARTIES_SHARED_WITH",
            "SPECIFIC_PIECES",
            "SOLD_OR_SHARED",
            "DISCLOSED_FOR_BUSINESS_PURPOSE",
            "RETENTION_PERIOD",
        ]

    def test_every_category_is_cited_in_the_legal_basis_doc(self):
        text = LEGAL_BASIS.read_text(encoding="utf-8")
        for category in DisclosureCategory:
            assert f"`{category.value}`" in text, category

    def test_the_legal_basis_doc_disclaims_legal_advice(self):
        text = LEGAL_BASIS.read_text(encoding="utf-8").lower()
        assert "legal advice" in text
        assert "reports observations, not conclusions" in text

    def test_str_enums_serialise_as_their_values(self):
        # They are written straight into TEXT columns and JSON responses.
        assert f"{DisclosureStatus.ABSENT}" == "absent"
        assert f"{InferenceOrigin.APPENDED_THIRD_PARTY}" == "appended_third_party"


class TestProvenance:
    def test_every_emitted_record_carries_provenance(self):
        for record in (
            Identity(IdType.LOYALTY_CARD, "0412998877665"),  # pii-scan: allow synthetic
            Transaction(occurred_at="2024-07-19T00:00:00Z"),
            Inference("Price sensitivity", "High", InferenceOrigin.FIRST_PARTY_MODEL),
            Disclosure(DisclosureCategory.SOURCES, DisclosureStatus.ABSENT),
        ):
            assert hasattr(record, "provenance")
            assert isinstance(record.provenance, Provenance)

    def test_provenance_defaults_are_not_shared_between_records(self):
        a = Identity(IdType.EMAIL, "a@example.com")
        b = Identity(IdType.EMAIL, "b@example.com")
        assert a.provenance is not b.provenance


class TestParseResult:
    def test_missing_categories_reports_what_the_adapter_forgot(self):
        result = ParseResult(
            request=META,
            disclosures=(
                Disclosure(DisclosureCategory.SPECIFIC_PIECES, DisclosureStatus.PROVIDED),
            ),
        )
        missing = result.missing_categories()
        assert DisclosureCategory.SPECIFIC_PIECES not in missing
        assert DisclosureCategory.SOURCES in missing
        assert len(missing) == len(DisclosureCategory) - 1

    def test_a_complete_adapter_leaves_nothing_missing(self):
        # An explicit ABSENT is a finding; a missing row is a bug. The distinction
        # is what this method exists to police.
        result = ParseResult(
            request=META,
            disclosures=tuple(
                Disclosure(c, DisclosureStatus.ABSENT) for c in DisclosureCategory
            ),
        )
        assert result.missing_categories() == ()

    def test_item_count_spans_transactions(self):
        result = ParseResult(
            request=META,
            transactions=(
                Transaction("2024-07-19T00:00:00Z", items=(TxnItem("MILK"), TxnItem("EGGS"))),
                Transaction("2024-08-02T00:00:00Z", items=(TxnItem("BREAD"),)),
            ),
        )
        assert result.item_count() == 3

    def test_an_empty_result_is_valid(self):
        # A retailer that sends a letter with no structured data is itself a
        # finding, so the model must represent that without special-casing.
        result = ParseResult(request=META)
        assert result.transactions == ()
        assert len(result.missing_categories()) == len(DisclosureCategory)


class TestImmutability:
    def test_records_are_frozen(self):
        item = TxnItem("ORGANIC BANANAS", retail_amt=2.49)
        # Adapters never mutate values (docs/handoff.md section 4, rule 2); the type
        # system should enforce that rather than a code review.
        with pytest.raises(FrozenInstanceError):
            item.retail_amt = 0.0

    def test_raw_descriptions_survive_verbatim(self):
        # Normalisation happens in a later pass so the original is recoverable.
        raw = "  UNKNOWN  "
        assert TxnItem(raw).description_raw == raw


class TestInference:
    def test_ordinal_scale_carries_both_forms(self):
        # The 1-7 likelihood scales need the raw label for display and the number
        # for sorting; storing only one of them loses information the report gave.
        inf = Inference(
            label="Cruise likelihood",
            value_raw="5 - Above average",
            value_num=5.0,
            scale=Scale.ORDINAL_1_7,
            subject=Scope.INDIVIDUAL,
            origin=InferenceOrigin.APPENDED_THIRD_PARTY,
            derivable_from_txns=False,
        )
        assert inf.value_num == 5.0
        assert re.match(r"^\d", inf.value_raw)
        assert inf.derivable_from_txns is False


class TestCategoryLabels:
    def test_every_category_has_a_readable_label(self):
        # A new category cannot be added without naming it, because the UI and the
        # follow-up text both read this rather than the enum key.
        for category in DisclosureCategory:
            assert category.label
            assert category.label != category.value
            assert "_" not in category.label

    def test_labels_do_not_leak_into_the_stored_value(self):
        assert str(DisclosureCategory.SOURCES) == "SOURCES"
