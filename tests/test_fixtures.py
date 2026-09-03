"""The synthetic fixture is the only test data this project has.

These tests assert the two things that make it trustworthy: it reproduces the
documented quirks of the real format, and it is byte-identical to what its
generator produces from a fixed seed.
"""

import json
import re
from pathlib import Path

import pytest
from tools import make_fixtures, scan_pii

KROGER_FIXTURES = (
    Path(__file__).parent.parent / "src" / "unbagged" / "adapters" / "kroger" / "fixtures"
)
REPORT = KROGER_FIXTURES / "synthetic_report.txt"

# The strip documented in the adapter notes and in HANDOFF.md section 4.
PAGE_NUMBER_LINE = re.compile(r"\n\s*\d{1,3}\r?\n")
JSON_BLOB = re.compile(r"^\{$.*?^\}$", re.M | re.S)

HEADERS = (
    "Section 1: Specific Pieces of Personal Information Collected",
    "Data we hold related to our Loyalty program:",
    "Data we hold to communicate and advertise to you in a personalized way:",
    "Email Information",
    "Data related to in-store services:",
    "Information about your purchases:",
)


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def blobs(report) -> list[dict]:
    return [json.loads(b) for b in JSON_BLOB.findall(PAGE_NUMBER_LINE.sub("\n", report))]


class TestRegeneration:
    def test_the_committed_fixture_is_generator_output(self):
        """The load-bearing check. scan_pii.py stands address-shaped rules down
        inside generated fixture directories because this test exists: a real
        report dropped in here does not reproduce from the seed."""
        assert make_fixtures.run(check=True) == 0

    def test_generation_is_deterministic(self):
        module = make_fixtures.load(KROGER_FIXTURES / "generate.py")
        assert module.generate(1234) == module.generate(1234)
        assert module.generate(1234) != module.generate(5678)

    def test_the_generator_is_discovered(self):
        assert KROGER_FIXTURES / "generate.py" in make_fixtures.find_generators()


class TestStructuralFidelity:
    def test_headers_appear_in_the_documented_order(self, report):
        positions = [report.index(h) for h in HEADERS]
        assert positions == sorted(positions)

    def test_there_is_no_section_two_three_or_four(self, report):
        # The absence is the finding: the report is numbered as though these
        # existed, and the adapter emits ABSENT disclosures for them.
        for n in (2, 3, 4):
            assert f"Section {n}:" not in report

    def test_the_coverage_window_and_supplemental_path_are_stated(self, report):
        assert "twenty-four (24) month period" in report
        assert "2022" in report and "supplemental request" in report

    def test_page_numbers_interleave_the_json(self, report):
        # Bare page-number lines land inside the blobs, which is why the report
        # does not parse as extracted.
        assert not JSON_BLOB.findall(report) or json_fails_without_stripping(report)

    def test_stripping_page_numbers_recovers_four_parseable_blobs(self, blobs):
        assert len(blobs) == 4


def json_fails_without_stripping(report: str) -> bool:
    for raw in JSON_BLOB.findall(report):
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            return True
    return False


class TestIdentityBlob:
    def test_every_documented_identifier_is_present(self, blobs):
        customer = blobs[0]["customer"][0]
        for key in ("loyaltyno", "cardNumberWithCD", "alternateId", "ehhn",
                    "householdId", "cgPersonId", "epsn", "SubscriberID"):
            assert key in customer, key

    def test_contact_details_use_reserved_for_fiction_values(self, blobs):
        customer = blobs[0]["customer"][0]
        assert customer["emailAddress"].endswith("@example.com")
        # 555 is the reserved exchange; the middle group is what matters.
        assert re.search(r"\)\s*555-", customer["phoneNumber"])


class TestInferenceBlob:
    def test_the_five_propensity_axes_are_present_with_prose_values(self, blobs):
        propensities = blobs[1]["customer"][0]["propensities"]
        assert set(propensities) == {
            "Convenience", "Loyalty", "Price", "Quality", "Variety Seeking"
        }
        # Prose, not numbers — the real report scores these in words.
        assert all(isinstance(v, str) and not v.isdigit() for v in propensities.values())

    def test_appended_attributes_are_present_and_not_derivable_from_baskets(self, blobs):
        customer = blobs[1]["customer"][0]
        assert "educationLevel" in customer["demographics"]
        assert "householdComposition" in customer["demographics"]
        assert "incomePredictorScore" in customer["likelihoods"]
        assert "cruiseLikelihood" in customer["likelihoods"]

    def test_likelihood_scales_are_ordinal_one_to_seven(self, blobs):
        for value in blobs[1]["customer"][0]["likelihoods"].values():
            assert re.match(r"^[1-7] - ", value), value


class TestPurchaseBlob:
    def test_baskets_carry_the_documented_shape(self, blobs):
        basket = blobs[3]["customer"][0]["basket"][0]
        for key in ("date", "time", "division", "store", "orderno",
                    "total_amount_prior_to_discounts", "tenders", "items"):
            assert key in basket, key
        item = basket["items"][0]
        for key in ("purchasedescription", "productupc", "retailamt", "customerloyamt"):
            assert key in item, key

    def test_placeholder_rows_are_reproduced(self, blobs):
        items = [i for b in blobs[3]["customer"][0]["basket"] for i in b["items"]]
        placeholders = [i for i in items if i["purchasedescription"] == "UNKNOWN"]
        assert placeholders, "the adapter has to recognise these; the fixture must have them"
        assert all(i["retailamt"] == 0.0 for i in placeholders)
        # pii-scan: allow placeholder UPC, not an identifier
        assert {i["productupc"] for i in placeholders} == {"00010000080000"}

    def test_returns_appear_as_negative_amounts(self, blobs):
        items = [i for b in blobs[3]["customer"][0]["basket"] for i in b["items"]]
        assert any(i["retailamt"] < 0 for i in items)

    def test_the_window_spans_roughly_two_years(self, blobs):
        baskets = blobs[3]["customer"][0]["basket"]
        assert len(baskets) > 80
        assert baskets[0]["date"] < baskets[-1]["date"]
        assert baskets[-1]["date"][:4] != baskets[0]["date"][:4]

    def test_there_are_enough_distinct_products_for_a_price_series(self, blobs):
        items = [i for b in blobs[3]["customer"][0]["basket"] for i in b["items"]]
        assert len({i["productupc"] for i in items}) > 150


class TestSafety:
    def test_the_fixture_passes_the_pii_scanner(self):
        rel = str(REPORT.relative_to(Path(__file__).parent.parent))
        assert scan_pii.scan_paths([rel], denylist=()) == []

    def test_the_fixture_directory_counts_as_generated(self):
        rel = str(REPORT.relative_to(Path(__file__).parent.parent))
        assert scan_pii.is_generated_fixture(rel)

    def test_a_hand_written_fixture_does_not_get_the_relaxation(self):
        # tests/fixtures/ has no generator, so nothing stands down there.
        assert not scan_pii.is_generated_fixture("tests/fixtures/whatever.txt")
