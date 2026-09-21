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
HMART_FIXTURES = (
    Path(__file__).parent.parent / "src" / "unbagged" / "adapters" / "hmart" / "fixtures"
)
REPORT = KROGER_FIXTURES / "synthetic_report.txt"


def _produced() -> dict[Path, set[str]]:
    """What every generator says it produces, keyed by fixtures directory.

    Asked of the generators rather than written out here. The capture filenames
    carry the dates the H Mart generator draws, so a literal list would need
    rewriting on every reseed — and a check against a hand-maintained list of
    what the directory holds is no check at all.
    """
    produced: dict[Path, set[str]] = {}
    for path in make_fixtures.find_generators():
        module = make_fixtures.load(path)
        produced.setdefault(path.parent, set()).update(module.generate())
    return produced


# The strip documented in the adapter notes and in docs/handoff.md section 4.
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


def PURCHASES(blobs: list[dict]) -> dict:
    """The purchase blob, found by shape rather than by position."""
    return next(b for b in blobs if "customer" in b)["customer"][0]


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

    def test_stripping_page_numbers_recovers_every_blob(self, blobs):
        # Five now: the loyalty section carries both the identity graph and the
        # propensity scores, which is what a real report does.
        assert len(blobs) == 5


def json_fails_without_stripping(report: str) -> bool:
    for raw in JSON_BLOB.findall(report):
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            return True
    return False


class TestIdentityBlob:
    def test_the_nested_account_shape_is_reproduced(self, blobs):
        blob = next(b for b in blobs if "accounts" in b)
        account = blob["accounts"][0]["accounts"][0]
        assert set(account) >= {"dataSource", "loyaltyCards", "personalInfo"}
        # Card numbers are the *keys* of loyaltyCards, not values.
        card_number, card = next(iter(account["loyaltyCards"].items()))
        assert card_number.isdigit()
        assert set(card) >= {"altIds", "cardNumberWithCD", "status", "type"}
        assert set(account["personalInfo"]["name"]) == {"firstName", "lastName"}

    def test_identity_groups_state_their_own_scope(self, blobs):
        # The report says which groups describe a household, so the adapter does
        # not have to guess from field names.
        blob = next(b for b in blobs if "groups" in b)
        types = {g["type"] for g in blob["groups"]}
        assert types == {"CG_PERSON", "KROGER_HOUSEHOLD"}
        household = next(g for g in blob["groups"] if g["type"] == "KROGER_HOUSEHOLD")
        assert set(household["aliasIds"]) == {"ehhn", "householdId"}
        assert "address" in household["metadata"]

    def test_contact_details_use_reserved_for_fiction_values(self, blobs):
        email = next(b for b in blobs if "emailData" in b)
        addresses = [r["Value"] for r in email["emailData"] if r["Name"] == "EmailAddress"]
        assert addresses and all(a.endswith("@example.com") for a in addresses)

    def test_email_arrives_as_name_value_pairs(self, blobs):
        # The Name half is a field label, not a value — a shape that has already
        # caught out one harvester.
        email = next(b for b in blobs if "emailData" in b)
        assert {r["Name"] for r in email["emailData"]} >= {
            "EmailAddress",
            "SubscriberID",
            "SubscriberKey",
            "Status",
        }


class TestInferenceBlob:
    def test_the_five_propensity_axes_sit_under_the_loyalty_header(self, blobs):
        # Under loyalty, not advertising: that is where a real report puts them.
        blob = next(b for b in blobs if "Convenience" in b)
        assert set(blob) == {
            "loyaltyIdNumber",
            "Convenience",
            "Loyalty",
            "Price",
            "Quality",
            "Variety Seeking",
        }
        axes = {k: v for k, v in blob.items() if k != "loyaltyIdNumber"}
        # Prose, not numbers — the real report scores these in words.
        assert all(isinstance(v, str) and not v.isdigit() for v in axes.values())

    def test_appended_attributes_are_split_by_who_they_describe(self, blobs):
        blob = next(b for b in blobs if "Individual" in b and "Household" in b)
        individual = blob["Individual"][0]
        household = blob["Household"][0]
        assert "Education Level of Individual" in individual
        assert "Year of Birth for Individual" in individual
        # These describe people who never enrolled in anything.
        assert "Income Predictor Score (in $000)" in household
        assert "Number of Children in Household" in household

    def test_likelihood_labels_carry_their_own_scale(self, blobs):
        blob = next(b for b in blobs if "Individual" in b)
        likelihoods = [k for k in blob["Individual"][0] if k.startswith("Likelihood")]
        assert likelihoods
        for label in likelihoods:
            assert "7=Most Likely" in label
            assert blob["Individual"][0][label] in [str(n) for n in range(1, 8)]


class TestPurchaseBlob:
    def test_baskets_carry_the_documented_shape(self, blobs):
        basket = PURCHASES(blobs)["basket"][0]
        for key in (
            "date",
            "time",
            "division",
            "store",
            "orderno",
            "total_amount_prior_to_discounts",
            "tenders",
            "items",
        ):
            assert key in basket, key
        item = basket["items"][0]
        for key in ("purchasedescription", "productupc", "retailamt", "customerloyamt"):
            assert key in item, key

    def test_amounts_and_dates_arrive_as_strings(self, blobs):
        # A real report sends "12.34", not 12.34, and welds a zeroed time onto
        # the date while keeping the real clock in a separate field.
        basket = PURCHASES(blobs)["basket"][0]
        assert isinstance(basket["total_amount_prior_to_discounts"], str)
        assert isinstance(basket["items"][0]["retailamt"], str)
        assert re.match(r"^\d{2}/\d{2}/\d{4} 00:00:00$", basket["date"])
        assert re.match(r"^\d{2}:\d{2}:\d{2}$", basket["time"])

    def test_placeholder_rows_are_reproduced(self, blobs):
        items = [i for b in PURCHASES(blobs)["basket"] for i in b["items"]]
        placeholders = [i for i in items if i["purchasedescription"] == "UNKNOWN"]
        assert placeholders, "the adapter has to recognise these; the fixture must have them"
        assert all(float(i["retailamt"]) == 0.0 for i in placeholders)
        # pii-scan: allow placeholder UPC, not an identifier
        assert {i["productupc"] for i in placeholders} == {"00010000080000"}

    def test_returns_appear_as_negative_amounts(self, blobs):
        items = [i for b in PURCHASES(blobs)["basket"] for i in b["items"]]
        assert any(float(i["retailamt"]) < 0 for i in items)

    def test_the_window_spans_roughly_two_years(self, blobs):
        baskets = PURCHASES(blobs)["basket"]
        assert len(baskets) > 80
        years = {b["date"][6:10] for b in baskets}
        assert len(years) > 1

    def test_there_are_enough_distinct_products_for_a_price_series(self, blobs):
        items = [i for b in PURCHASES(blobs)["basket"] for i in b["items"]]
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


class TestStrayFixtureFiles:
    """The other half of the regeneration guarantee.

    `--check` used to compare only the filenames a generator names, which proves
    those files are synthetic and says nothing about anything sitting beside
    them. A real report added to a fixtures directory was re-included by
    .gitignore, exempted from the address rules *because* this check was assumed
    to cover it, skipped by the scanner if its suffix was binary, and reported
    clean by every safeguard in the project. These tests are that gap.
    """

    def test_a_file_no_generator_produces_is_reported(self):
        # Every generated directory has to be credited, not just the first one:
        # passing a subset is what a second adapter looks like on the day it
        # lands, and the check correctly calls its fixture unexplained.
        # Credited from the generators themselves rather than from a literal
        # list. The captures are named for the dates the generator draws, so a
        # literal here would have to be rewritten on every reseed — and the one
        # thing this check must never become is a list somebody updates by
        # pasting in whatever the directory currently holds.
        produced = _produced()
        stray = make_fixtures.unexplained_files(produced)
        assert stray == []

        # Credit one directory and not the other: the uncredited fixture is now
        # unexplained, which is exactly what a dropped-in report would be.
        stray = make_fixtures.unexplained_files({KROGER_FIXTURES: {"synthetic_report.txt"}})
        assert "synthetic_history.xls" in " ".join(stray)

        # Credit the statement but not the captures. A committed PNG that no
        # generator produces is the binary form of the accident this guards:
        # the scanner reads no text out of it, so this check is the only thing
        # standing between a real screen capture of a receipt and a commit.
        partial = {**produced, HMART_FIXTURES: {"synthetic_history.xls"}}
        stray = make_fixtures.unexplained_files(partial)
        assert stray, "a committed capture no generator produces must be reported"
        assert all(name.endswith(".png") for name in stray), stray

        # Same directory, but the generator is not credited with the fixture: the
        # committed file is now unexplained, which is what a dropped-in report is.
        stray = make_fixtures.unexplained_files({KROGER_FIXTURES: set()})
        assert "synthetic_report.txt" in " ".join(stray)

    def test_the_generator_itself_is_not_a_stray(self):
        stray = make_fixtures.unexplained_files({KROGER_FIXTURES: {"synthetic_report.txt"}})
        assert not any("generate.py" in s for s in stray)

    def test_every_committed_fixture_is_accounted_for(self):
        """The live assertion, over the real repository."""
        assert make_fixtures.run(check=True) == 0


class TestTheFabricatedNumbersStayUncatchable:
    """The scanner's payment-card rule must stay armed inside fixtures/.

    `tools/scan_pii.py` stands a few address-shaped rules down inside generated
    fixture directories, because byte-identical regeneration replaces them. It
    does NOT stand down the payment-card rule, so every fabricated 13-19 digit
    run in a fixture has to fail a Luhn check or it trips the scanner on every
    regeneration. Both generators assert that about their own constants.

    The guards were untested. That matters here more than it usually would: the
    kroger fee UPC was first written as a value that DID pass, and the only
    thing that said so was a red scan.
    """

    #: A 14-digit UPC-shaped run that DOES pass Luhn. Not a card number and not
    #: drawn from anything: it is the exact value the kroger fee UPC was first
    #: written as, which is what made the scanner red and started this guard.
    #: The 13-digit Visa test number does not pass, which is worth knowing before
    #: reaching for one here.
    LUHN_PASSING = "00099000010001"  # pii-scan: allow fabricated UPC, not a card number

    def _generator(self, retailer: str):
        return make_fixtures.load(Path(f"src/unbagged/adapters/{retailer}/fixtures/generate.py"))

    def test_the_kroger_guard_fires_on_a_luhn_passing_fee_upc(self, monkeypatch):
        module = self._generator("kroger")
        monkeypatch.setattr(module, "BAG_FEE_UPC", self.LUHN_PASSING)
        with pytest.raises(ValueError, match="Luhn"):
            module.generate()

    def test_the_kroger_guard_fires_on_a_luhn_passing_placeholder_upc(self, monkeypatch):
        module = self._generator("kroger")
        monkeypatch.setattr(module, "PLACEHOLDER_UPC", self.LUHN_PASSING)
        with pytest.raises(ValueError, match="Luhn"):
            module.generate()

    def test_the_hmart_guard_fires_on_a_luhn_passing_smartcard(self, monkeypatch):
        module = self._generator("hmart")
        monkeypatch.setattr(module, "SMARTCARD", self.LUHN_PASSING)
        with pytest.raises(ValueError, match="Luhn"):
            module.generate()

    def test_the_committed_constants_all_fail_luhn(self):
        """The guards pass today, stated directly rather than only as a side effect."""
        from tools.receiptimage import luhn_ok

        kroger = self._generator("kroger")
        hmart = self._generator("hmart")
        for value in (kroger.BAG_FEE_UPC, kroger.PLACEHOLDER_UPC, hmart.SMARTCARD):
            assert not luhn_ok(value), f"{value} passes a Luhn check"


class TestTheKrogerFixtureCarriesAZeroLoyaltyLine:
    """A line at zero against a positive shelf amount.

    The shape that tells a price field from a discount field. Read the wrong way
    round it is a 100% discount, which is silent: "you paid" renders $0.00 and
    the basket total collapses. The generator draws it at `ZERO_LOYALTY_SHARE`
    and nothing asserted it actually reaches the committed fixture.
    """

    def test_the_fixture_contains_lines_paid_at_zero(self):
        from unbagged.adapters.kroger import reader

        text = REPORT.read_text(encoding="utf-8")
        clean, _pages = reader.strip_page_markers(text)
        blob = reader.blob_with_keys(reader.find_blobs(clean), "customer")
        assert blob is not None, "no purchase blob in the fixture"

        zeroed = [
            item
            for basket in blob.data["customer"][0]["basket"]
            for item in basket["items"]
            if float(item["retailamt"]) > 0 and float(item["customerloyamt"]) == 0
        ]
        assert zeroed, "no line paid at zero against a positive shelf amount"
        # Measured at 35 of 788 priced lines in the real response. Wide bounds:
        # this catches the shape vanishing, not an exact draw.
        assert 10 <= len(zeroed) <= 120, f"{len(zeroed)} zero-loyalty lines"
