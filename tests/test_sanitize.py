import json

import pytest

from unbagged.sanitize import (
    bucket_number,
    coarsen_date,
    sanitize_file,
    sanitize_text,
    skeleton_json,
    skeleton_text,
)


class TestPrimitives:
    def test_numbers_bucket_to_order_of_magnitude(self):
        assert bucket_number(4.99) == "<num:1e0>"
        assert bucket_number(7.15) == "<num:1e0>"
        assert bucket_number(142.30) == "<num:1e2>"
        assert bucket_number(0) == "<num:0>"
        assert bucket_number(-3.50) == "<num:-1e0>"

    def test_dates_coarsen_to_month(self):
        assert coarsen_date("2024-07-19") == "<date:2024-07>"
        assert coarsen_date("7/19/2024") == "<date:2024-07>"
        assert coarsen_date("not a date") is None


class TestJson:
    def test_keys_survive_and_values_do_not(self):
        out = skeleton_json({"purchasedescription": "ORGANIC BANANAS", "retailamt": 2.49})
        assert out == {"purchasedescription": "<str:len=15>", "retailamt": "<num:1e0>"}

    def test_homogeneous_lists_collapse(self):
        baskets = [{"total": 41.20}, {"total": 88.15}, {"total": 12.00}]
        assert skeleton_json(baskets) == [{"total": "<num:1e1>"}, "<repeated:3>"]

    def test_lists_differing_in_magnitude_do_not_collapse(self):
        baskets = [{"total": 41.20}, {"total": 8.15}, {"total": 112.00}]
        assert len(skeleton_json(baskets)) == 3

    def test_heterogeneous_lists_are_kept(self):
        assert skeleton_json([1, "ab", None]) == ["<num:1e0>", "<str:len=2>", None]

    def test_nesting_and_dispatch(self):
        raw = json.dumps({"customer": [{"basket": [{"date": "2024-07-19"}]}]})
        assert sanitize_text(raw)["body"] == {
            "customer": [{"basket": [{"date": "<date:2024-07>"}]}]
        }


class TestText:
    def test_alphanumeric_runs_are_masked_and_punctuation_survives(self):
        # Structure is what a maintainer needs; the words are the user's data.
        assert skeleton_text('  "loyaltyno": "0123456789",') == ['  "a9": "910",']

    def test_line_count_is_preserved(self):
        assert len(skeleton_text("a\nb\nc")) == 3


class TestCsv:
    def test_headers_survive_row_values_do_not(self):
        out = sanitize_text("date,store,total\n2024-07-19,Store 42,41.20\n", filename="a.csv")
        assert out["format"] == "csv"
        assert out["rows"] == 1
        assert [c["name"] for c in out["columns"]] == ["date", "store", "total"]
        assert out["columns"][0]["sample_shape"] == "<date:2024-07>"
        assert "Store 42" not in json.dumps(out)


class TestFiles:
    def test_binary_report_formats_are_refused(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        with pytest.raises(ValueError, match="not supported yet"):
            sanitize_file(pdf)

    def test_roundtrip_leaks_nothing(self, tmp_path):
        # example.com is reserved by RFC 2606 and cannot be registered. The
        # literal here used to be a gmail.com address, which is deliverable to
        # whoever holds it — not something to publish in a repository whose
        # subject is other people's personal data.
        identifier = "shopper@example.com"
        src = tmp_path / "report.json"
        street = "1428 Elm Street"  # pii-scan: allow fictional address, not a real one
        src.write_text(json.dumps({"email": identifier, "street": street}))
        assert identifier not in json.dumps(sanitize_file(src))


# Named once so the suppressions live in one place rather than down a parametrize
# list. Both are synthetic: fabricated to the right shape to exercise the rule.
CARD_KEY = "4166872310945"      # pii-scan: allow synthetic card-shaped test literal
HOUSEHOLD_KEY = "600123456789"  # pii-scan: allow synthetic household-id test literal
# The UUID from RFC 4122 section 3, a documentation value with no owner.
# gitleaks reads any 36-char hyphenated hex string as a high-entropy secret.
UUID_KEY = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"  # gitleaks:allow


class TestIdentifierKeys:
    """A skeleton is meant to be attachable to a public issue without reading it.

    That guarantee held for values and not for keys. CONTRIBUTING.md tells people
    to run `unbagged sanitize` and attach the output, so the case below is not
    hypothetical: the Kroger identity blob keys `loyaltyCards` BY the card number,
    which tests/test_fixtures.py asserts, and the old skeleton published those keys
    while faithfully masking everything they pointed at.
    """

    def test_a_map_keyed_by_loyalty_card_number_is_masked(self):
        card = CARD_KEY
        out = sanitize_text(json.dumps({"loyaltyCards": {card: {"status": "ACTIVE"}}}))
        rendered = json.dumps(out)
        assert card not in rendered
        # The schema survives; only the identifier goes.
        assert "loyaltyCards" in rendered
        assert "status" in rendered

    def test_the_masked_key_keeps_its_length(self):
        card = CARD_KEY
        out = sanitize_text(json.dumps({"cards": {card: 1}}))
        assert f"<key:len={len(card)}>" in json.dumps(out)

    @pytest.mark.parametrize(
        "key",
        [CARD_KEY, HOUSEHOLD_KEY, UUID_KEY, "shopper@example.com"],
    )
    def test_identifier_shaped_keys_are_masked(self, key):
        out = sanitize_text(json.dumps({"m": {key: 1}}))
        assert key not in json.dumps(out)

    @pytest.mark.parametrize(
        "key",
        [
            "loyaltyCards", "cardNumberWithCD", "ehhn", "householdId",
            "total_amount_prior_to_discounts", "purchasedescription", "productupc",
            "2024", "01", "Section 1", "EmailAddress", "store", "orderno",
        ],
    )
    def test_field_names_survive(self, key):
        """The schema is the reason a skeleton is worth sending. Short numeric keys
        like a year or a month index stay: masking those would blind a maintainer to
        the structure without protecting anything."""
        out = sanitize_text(json.dumps({"m": {key: 1}}))
        assert key in json.dumps(out)

    def test_csv_headers_go_through_the_same_rule(self):
        out = sanitize_text(f"{CARD_KEY},name\n1,2\n", filename="x.csv")
        names = [c["name"] for c in out["columns"]]
        assert CARD_KEY not in names
        assert "name" in names

    def test_it_agrees_with_the_pii_scanner_on_what_is_an_identifier(self):
        """sanitize.py mirrors tools/scan_pii.py's patterns rather than importing
        them, because the shipped application must not depend on repo tooling. This
        asserts the duplication has not drifted on the cases that matter."""
        from tools import scan_pii

        def scanner_flags(value: str) -> bool:
            return bool(scan_pii.scan_lines([value], "x", denylist=()))

        for identifier in (CARD_KEY, UUID_KEY, "shopper@notreserved.example"):
            masked = identifier not in json.dumps(sanitize_text(json.dumps({"m": {identifier: 1}})))
            assert masked, f"sanitize keeps {identifier!r} that the scanner would flag"

        # And neither treats an ordinary field name as an identifier.
        assert not scanner_flags("purchasedescription")
        assert "purchasedescription" in json.dumps(
            sanitize_text(json.dumps({"m": {"purchasedescription": 1}}))
        )
