"""Rule 4: adapters degrade, they don't crash.

A malformed basket costs one warning and one skipped record, not the other
fifty-three baskets. These tests feed the adapter damaged input and assert it
keeps what it can and says what it lost.
"""

import json
import re
from pathlib import Path

import pytest

from unbagged.adapters.kroger.adapter import KrogerAdapter
from unbagged.models import AdapterError, Severity, SourceBundle, SourceDocument

FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)

HEADERS = (
    "Section 1: Specific Pieces of Personal Information Collected\n\n"
    "Data we hold related to our Loyalty program:\n\n"
)


def parse_text(tmp_path, text: str):
    path = tmp_path / "report.txt"
    path.write_text(text, encoding="utf-8")
    document = SourceDocument("report.txt", "0" * 64, path=str(path), id=1)
    return KrogerAdapter().parse(SourceBundle(documents=(document,)))


def messages(result) -> str:
    return " | ".join(w.message for w in result.warnings)


def report_with_baskets(baskets: list) -> str:
    return (
        HEADERS
        # pii-scan: allow synthetic loyalty number
        + json.dumps({"customer": [{"loyaltyno": "6000000000001"}]}, indent=2)
        + "\n\nInformation about your purchases:\n\n"
        + json.dumps({"customer": [{"basket": baskets}]}, indent=2)
        + "\n"
    )


GOOD_BASKET = {
    "date": "2025-03-04", "time": "18:22:10", "division": "016", "store": "00318",
    "orderno": "000001", "total_amount_prior_to_discounts": 41.2,
    "tenders": [{"tendertype": "CREDIT", "amount": 41.2}],
    "items": [{"purchasedescription": "BANANAS", "productupc": "00000004011",
               "retailamt": 2.49, "customerloyamt": 0.3}],
}


class TestTruncation:
    def test_a_truncated_report_keeps_what_survived(self, tmp_path):
        """The M3 acceptance criterion."""
        raw = FIXTURE.read_text(encoding="utf-8")
        cut = raw[: raw.index("Information about your purchases:") + 4000]
        result = parse_text(tmp_path, cut)
        # The identity section is before the cut and must survive intact.
        assert result.identities
        assert result.transactions == ()
        assert any(w.severity is Severity.ERROR for w in result.warnings)
        assert "truncat" in messages(result).lower()

    def test_truncation_is_located_for_the_user(self, tmp_path):
        raw = FIXTURE.read_text(encoding="utf-8")
        result = parse_text(tmp_path, raw[: raw.index('"basket"') + 2000])
        truncation = next(w for w in result.warnings if "truncat" in w.message.lower())
        assert truncation.locator and "page" in truncation.locator

    def test_a_truncated_report_still_reports_disclosures(self, tmp_path):
        # What the retailer failed to disclose does not become unknowable just
        # because the file was cut short.
        raw = FIXTURE.read_text(encoding="utf-8")
        result = parse_text(tmp_path, raw[:6000])
        assert result.missing_categories() == ()


class TestMalformedBaskets:
    def test_one_bad_basket_does_not_lose_the_others(self, tmp_path):
        baskets = [GOOD_BASKET, "not a basket at all", GOOD_BASKET]
        result = parse_text(tmp_path, report_with_baskets(baskets))
        assert len(result.transactions) == 2
        basket_warnings = [w for w in result.warnings if "Basket" in w.message]
        assert len(basket_warnings) == 1

    def test_a_basket_with_no_date_is_skipped_with_a_warning(self, tmp_path):
        broken = {k: v for k, v in GOOD_BASKET.items() if k != "date"}
        result = parse_text(tmp_path, report_with_baskets([GOOD_BASKET, broken]))
        assert len(result.transactions) == 1
        assert "date" in messages(result)

    def test_a_malformed_item_list_costs_the_items_not_the_basket(self, tmp_path):
        broken = {**GOOD_BASKET, "items": "three things"}
        result = parse_text(tmp_path, report_with_baskets([broken]))
        assert len(result.transactions) == 1
        assert result.transactions[0].items == ()
        assert "item list" in messages(result)

    def test_a_non_object_item_is_skipped(self, tmp_path):
        broken = {**GOOD_BASKET, "items": [GOOD_BASKET["items"][0], 42]}
        result = parse_text(tmp_path, report_with_baskets([broken]))
        assert len(result.transactions[0].items) == 1
        assert result.warnings

    def test_a_basket_field_that_is_not_a_list(self, tmp_path):
        text = (
            HEADERS + '{"customer": [{"loyaltyno": "1"}]}\n\n'
            "Information about your purchases:\n\n"
            '{"customer": [{"basket": {"date": "2025-01-01"}}]}\n'
        )
        result = parse_text(tmp_path, text)
        assert result.transactions == ()
        assert any(w.severity is Severity.ERROR for w in result.warnings)

    def test_missing_optional_fields_are_not_fatal(self, tmp_path):
        sparse = {"date": "2025-03-04", "items": []}
        result = parse_text(tmp_path, report_with_baskets([sparse]))
        assert len(result.transactions) == 1
        transaction = result.transactions[0]
        assert transaction.store_code is None
        assert transaction.tender_type is None
        assert transaction.total_pre_discount is None


class TestAmountCoercion:
    @pytest.mark.parametrize(
        "raw,expected",
        [(2.49, 2.49), ("2.49", 2.49), ("$1,234.56", 1234.56), ("", None),
         (None, None), ("not a number", None), (-6.99, -6.99)],
    )
    def test_amounts_are_coerced_or_dropped_never_guessed(self, tmp_path, raw, expected):
        item = {"purchasedescription": "THING", "productupc": "1", "retailamt": raw}
        result = parse_text(tmp_path, report_with_baskets([{**GOOD_BASKET, "items": [item]}]))
        assert result.transactions[0].items[0].retail_amt == expected


class TestMissingSections:
    def test_a_letter_with_no_data_is_parsed_as_a_finding(self, tmp_path):
        # A retailer that sends prose and no data is itself a compliance finding,
        # so this must produce a result rather than an exception.
        result = parse_text(
            tmp_path,
            "Section 1: Specific Pieces of Personal Information Collected\n\n"
            "We hold no personal information about you.\n",
        )
        assert result.transactions == ()
        assert result.identities == ()
        assert result.missing_categories() == ()
        assert any(w.severity is Severity.ERROR for w in result.warnings)

    def test_an_unrecognised_format_says_so_rather_than_pretending(self, tmp_path):
        result = parse_text(tmp_path, "Dear customer,\n\nThank you for writing.\n")
        assert "section headers were found" in messages(result)

    def test_a_customer_object_that_is_missing(self, tmp_path):
        text = HEADERS + '{"notCustomer": []}\n'
        result = parse_text(tmp_path, text)
        assert result.identities == ()
        assert "customer" in messages(result)


class TestUnreadableInput:
    def test_nothing_readable_raises_a_message_meant_for_a_person(self, tmp_path):
        missing = SourceDocument("gone.pdf", "0" * 64, path=str(tmp_path / "gone.pdf"))
        with pytest.raises(AdapterError, match="scanned PDF"):
            KrogerAdapter().parse(SourceBundle(documents=(missing,)))

    def test_an_unreadable_attachment_does_not_lose_a_readable_one(self, tmp_path):
        good = tmp_path / "report.txt"
        good.write_text(report_with_baskets([GOOD_BASKET]), encoding="utf-8")
        documents = (
            SourceDocument("gone.pdf", "0" * 64, path=str(tmp_path / "gone.pdf")),
            SourceDocument("report.txt", "1" * 64, path=str(good), id=2),
        )
        result = KrogerAdapter().parse(SourceBundle(documents=documents))
        assert len(result.transactions) == 1
        assert "gone.pdf" in messages(result)


class TestCorruptJson:
    def test_one_corrupt_blob_does_not_cost_the_others(self, tmp_path):
        text = (
            # pii-scan: allow synthetic loyalty number
            HEADERS + '{"customer": [{"loyaltyno": nonsense}]}\n\n'
            "Information about your purchases:\n\n"
            + json.dumps({"customer": [{"basket": [GOOD_BASKET]}]}, indent=2) + "\n"
        )
        result = parse_text(tmp_path, text)
        assert len(result.transactions) == 1
        assert result.identities == ()
        assert re.search(r"did not parse", messages(result))
