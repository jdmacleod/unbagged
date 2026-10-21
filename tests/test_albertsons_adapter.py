"""The Albertsons adapter, against the synthetic report and workbook.

The report is committed as the text pdfplumber extracts from the PDF; the
workbook is built here by the same generator, because an `.xlsx` is a zip and
this repository does not commit one. Counts are taken from the generator's own
model rather than written down, so a lost line shows up as a mismatch.
"""

import io
import re
import zipfile
from pathlib import Path

import pytest
from tools import make_fixtures

import unbagged.adapters  # noqa: F401  (registers everything)
from unbagged.adapters.albertsons import report as rp
from unbagged.adapters.albertsons.adapter import AlbertsonsAdapter
from unbagged.adapters.registry import registry
from unbagged.models import (
    AdapterError,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpKind,
    IdType,
    InferenceOrigin,
    Scale,
    Scope,
    Severity,
    SourceBundle,
    SourceDocument,
)

FIXTURES = (
    Path(__file__).parent.parent / "src" / "unbagged" / "adapters" / "albertsons" / "fixtures"
)
GENERATOR = make_fixtures.load(FIXTURES / "generate.py")
MODEL = GENERATOR.build()
LINES = sum(len(b["lines"]) for b in MODEL["baskets"])
BASKETS = len({b["txn_id"] for b in MODEL["baskets"]})


@pytest.fixture(scope="module")
def report_text() -> str:
    return (FIXTURES / "synthetic_report.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    return GENERATOR.build_workbook()


def bundle(tmp_path, *files: tuple[str, str | bytes]) -> SourceBundle:
    documents = []
    for number, (name, content) in enumerate(files, start=1):
        path = tmp_path / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        documents.append(SourceDocument(name, f"{number:064d}", path=str(path), id=number))
    return SourceBundle(documents=tuple(documents))


@pytest.fixture()
def both(tmp_path, report_text, workbook_bytes) -> SourceBundle:
    return bundle(tmp_path, ("report.txt", report_text), ("details.xlsx", workbook_bytes))


@pytest.fixture()
def parsed(both):
    return AlbertsonsAdapter().parse(both)


def rewritten_workbook(workbook_bytes: bytes, part: str, change) -> bytes:
    """The workbook with one part passed through `change`."""
    source = zipfile.ZipFile(io.BytesIO(workbook_bytes))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == part:
                data = change(data.decode("utf-8")).encode("utf-8")
            target.writestr(info.filename, data)
    return buffer.getvalue()


class TestSniff:
    def test_claims_the_report_alone(self, tmp_path, report_text):
        assert AlbertsonsAdapter().sniff(bundle(tmp_path, ("r.txt", report_text))) == 0.9

    def test_claims_the_workbook_alone(self, tmp_path, workbook_bytes):
        assert AlbertsonsAdapter().sniff(bundle(tmp_path, ("d.xlsx", workbook_bytes))) == 0.9

    def test_declines_a_letter(self, tmp_path):
        assert AlbertsonsAdapter().sniff(bundle(tmp_path, ("a.txt", "Dear customer,\n"))) == 0.0

    def test_declines_a_workbook_whose_amount_column_was_renamed(self, tmp_path, workbook_bytes):
        # Loudly, rather than reading whatever column sits where NET was.
        renamed = rewritten_workbook(
            workbook_bytes, "xl/sharedStrings.xml", lambda x: x.replace(">NET<", ">AMOUNT<")
        )
        assert AlbertsonsAdapter().sniff(bundle(tmp_path, ("d.xlsx", renamed))) == 0.0

    def test_the_registry_picks_it_over_every_other_adapter(self, both):
        match = registry.select(both)
        assert match.adapter.retailer_id == "albertsons"


class TestTransactions:
    def test_every_line_becomes_an_item_and_every_txn_id_a_basket(self, parsed):
        assert parsed.item_count() == LINES
        assert len(parsed.transactions) == BASKETS

    def test_a_basket_is_dated_and_never_given_a_clock_it_did_not_have(self, parsed):
        for txn in parsed.transactions:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", txn.occurred_at)
        assert parsed.transactions[0].occurred_at == MODEL["baskets"][0]["day"].isoformat()

    def test_net_is_the_price_paid_and_no_shelf_price_is_invented(self, parsed):
        items = [item for txn in parsed.transactions for item in txn.items]
        assert all(item.retail_amt is None for item in items)
        assert all(item.loyalty_amt is not None for item in items)

    def test_quantities_are_disclosed_and_read(self, parsed):
        items = [item for txn in parsed.transactions for item in txn.items]
        assert all(item.quantity is not None for item in items)
        assert any(item.quantity == 0 for item in items), "markdown lines carry QTY 0"

    def test_amounts_in_e_notation_and_fractions_of_a_cent_survive(self, parsed):
        amounts = [i.loyalty_amt for t in parsed.transactions for i in t.items]
        assert any(0 < abs(a) < 0.1 for a in amounts)
        assert any(round(a, 2) != a for a in amounts)

    def test_no_basket_total_is_invented(self, parsed):
        assert all(txn.total_pre_discount is None for txn in parsed.transactions)

    def test_each_basket_cites_its_first_txn_id_cell(self, parsed):
        for txn in parsed.transactions:
            assert txn.provenance.page is None
            assert re.fullmatch(r"Albertsons Transactions!B\d+", txn.provenance.locator)
            assert txn.external_order_id

    def test_the_window_is_what_the_data_covers(self, parsed):
        assert parsed.request.period_start == MODEL["baskets"][0]["day"].isoformat()
        assert parsed.request.period_end == MODEL["baskets"][-1]["day"].isoformat()


class TestIdentities:
    def test_the_card_is_one_identity_across_both_files(self, parsed):
        cards = [i for i in parsed.identities if i.id_type is IdType.LOYALTY_CARD]
        assert [c.value for c in cards] == [MODEL["card"]]
        # Not INDIVIDUAL: the response never says whose card it is.
        assert cards[0].scope is None

    def test_household_and_address_are_household_scoped(self, parsed):
        scopes = {i.id_type: i.scope for i in parsed.identities}
        assert scopes[IdType.HOUSEHOLD] is Scope.HOUSEHOLD
        assert scopes[IdType.ADDRESS] is Scope.HOUSEHOLD

    def test_placeholders_are_read_as_absence_not_as_a_name(self, parsed):
        (name,) = [
            i
            for i in parsed.identities
            if i.id_type is IdType.NAME and i.provenance.locator.startswith("Loyalty Profile")
        ]
        assert "I/ss" not in name.value
        assert name.value == f"{GENERATOR.HOLDER} {GENERATOR.MIDDLE} {GENERATOR.FAMILY}".upper()

    def test_report_fields_cite_their_page_and_table(self, parsed):
        (household,) = [i for i in parsed.identities if i.id_type is IdType.HOUSEHOLD]
        assert household.provenance.page == 2
        assert household.provenance.locator == "Loyalty Profile Details › Household_id"


class TestInferences:
    def test_every_demographic_row_is_an_appended_attribute(self, parsed):
        appended = [
            i for i in parsed.inferences if i.origin is InferenceOrigin.APPENDED_THIRD_PARTY
        ]
        assert len(appended) == len(GENERATOR.DEMOGRAPHICS)

    def test_the_store_counts_are_first_party_and_derivable(self, parsed):
        first = [i for i in parsed.inferences if i.origin is InferenceOrigin.FIRST_PARTY_MODEL]
        assert {i.label for i in first} == {
            "Customer_most_spend_store_nbr",
            "Customer_most_used_store_nbr",
        }
        assert all(i.derivable_from_txns for i in first)

    def test_a_code_made_of_digits_is_not_a_quantity(self, parsed):
        by_label = {i.label: i for i in parsed.inferences}
        assert by_label["Census Tract"].value_num is None
        assert by_label["County Code"].scale is Scale.CATEGORICAL
        assert by_label["Median Home Value (in thousands)"].scale is Scale.CURRENCY
        assert by_label["Age"].scale is Scale.COUNT

    def test_derivability_is_three_valued(self, parsed):
        by_label = {i.label: i for i in parsed.inferences}
        assert by_label["Heavy Buyer of Fresh Products"].derivable_from_txns is True
        assert by_label["Net Worth"].derivable_from_txns is False
        # No channel field is disclosed, so the baskets cannot settle it.
        assert by_label["Consumer Internet Buyer"].derivable_from_txns is None

    def test_a_subject_is_named_only_where_the_label_names_it(self, parsed):
        by_label = {i.label: i for i in parsed.inferences}
        assert by_label["Number of Persons in Household"].subject is Scope.HOUSEHOLD
        assert by_label["Age"].subject is None


class TestDisclosures:
    def test_all_eight_are_recorded(self, parsed):
        assert parsed.missing_categories() == ()

    def test_specific_pieces_is_partial_and_counts_what_there_was(self, parsed):
        pieces = next(
            d for d in parsed.disclosures if d.category is DisclosureCategory.SPECIFIC_PIECES
        )
        assert pieces.status is DisclosureStatus.PARTIAL
        assert f"{BASKETS} purchases with {LINES} line items" in pieces.evidence

    def test_categories_collected_quotes_the_sentence_it_rests_on(self, parsed, report_text):
        categories = next(
            d for d in parsed.disclosures if d.category is DisclosureCategory.CATEGORIES_COLLECTED
        )
        assert categories.status is DisclosureStatus.PARTIAL
        assert "categories of data collected" in categories.evidence
        assert categories.evidence.split()[0] in report_text

    def test_the_rest_are_absent(self, parsed):
        absent = {d.category for d in parsed.disclosures if d.status is DisclosureStatus.ABSENT}
        assert absent == set(DisclosureCategory) - {
            DisclosureCategory.SPECIFIC_PIECES,
            DisclosureCategory.CATEGORIES_COLLECTED,
        }


class TestFindings:
    def test_a_profile_in_another_name_is_raised(self, parsed):
        assert any("different first name" in f.description for f in parsed.follow_ups)

    def test_and_is_not_raised_when_the_names_agree(self, tmp_path, report_text, workbook_bytes):
        same = report_text.replace(f" {GENERATOR.REQUESTER} ", f" {GENERATOR.HOLDER} ")
        result = AlbertsonsAdapter().parse(
            bundle(tmp_path, ("r.txt", same), ("d.xlsx", workbook_bytes))
        )
        assert not any("different first name" in f.description for f in result.follow_ups)

    def test_the_gap_between_the_history_and_the_request_is_stated(self, parsed):
        end = MODEL["baskets"][-1]["day"].isoformat()
        (gap,) = [f for f in parsed.follow_ups if f"ends on {end}" in f.description]
        assert gap.kind is FollowUpKind.CLARIFICATION
        assert MODEL["points_day"].isoformat() in gap.description

    def test_what_was_withheld_is_quoted(self, parsed):
        (withheld,) = [f for f in parsed.follow_ups if "withheld" in f.description]
        assert "exempt" in withheld.description
        assert "another person" in withheld.description

    def test_the_request_details_are_read(self, parsed):
        assert parsed.request.report_reference == MODEL["reference"]
        assert parsed.request.submitted_at == "2026-03-02T09:15:00"
        assert parsed.request.retailer_id == "albertsons"


class TestHalfAResponse:
    def test_the_report_alone_says_the_history_is_missing(self, tmp_path, report_text):
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("r.txt", report_text)))
        assert result.transactions == ()
        assert any("No purchase history" in w.message for w in result.warnings)
        assert result.missing_categories() == ()

    def test_the_workbook_alone_says_the_report_is_missing(self, tmp_path, workbook_bytes):
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", workbook_bytes)))
        assert result.item_count() == LINES
        assert result.request.report_reference is None
        assert any("No report PDF" in w.message for w in result.warnings)

    def test_nothing_recognisable_is_an_error_written_for_a_person(self, tmp_path):
        with pytest.raises(AdapterError, match="look like an Albertsons response"):
            AlbertsonsAdapter().parse(bundle(tmp_path, ("a.txt", "Dear customer,\n")))

    def test_an_unreadable_file_beside_the_response_is_named(self, tmp_path, report_text):
        result = AlbertsonsAdapter().parse(
            bundle(tmp_path, ("r.txt", report_text), ("scan.pdf", b"%PDF-1.4 nothing"))
        )
        assert any("scan.pdf could not be read" in w.message for w in result.warnings)


class TestDegradation:
    def test_one_undated_line_costs_that_line_only(self, tmp_path, report_text, workbook_bytes):
        serial = str((MODEL["baskets"][0]["day"] - GENERATOR.date(1899, 12, 30)).days)
        broken = rewritten_workbook(
            workbook_bytes,
            "xl/worksheets/sheet1.xml",
            lambda x: x.replace(f'<c r="A4" s="4"><v>{serial}</v>', '<c r="A4" s="4"><v>x</v>', 1),
        )
        result = AlbertsonsAdapter().parse(
            bundle(tmp_path, ("r.txt", report_text), ("d.xlsx", broken))
        )
        assert result.item_count() == LINES - 1
        (warning,) = [w for w in result.warnings if "no readable date" in w.message]
        assert warning.locator == "Albertsons Transactions!A4"

    def test_a_junk_amount_keeps_the_line_and_says_so(self, tmp_path, workbook_bytes):
        broken = rewritten_workbook(
            workbook_bytes,
            "xl/worksheets/sheet1.xml",
            lambda x: re.sub(r'(<c r="J4" s="8"><v>)[^<]*', r"\1NaN", x, count=1),
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", broken)))
        assert result.item_count() == LINES
        assert result.transactions[0].items[0].loyalty_amt is None
        assert any(w.locator == "Albertsons Transactions!J4" for w in result.warnings)

    def test_an_unknown_report_section_is_named(self, tmp_path, report_text):
        extra = report_text.replace(
            "2 / 2", f"{GENERATOR._double('Digital Account Details')}\nApp_user_id 12\n2 / 2"
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("r.txt", extra)))
        assert any("Digital Account Details" in w.message for w in result.warnings)
        assert all(w.severity is not Severity.ERROR for w in result.warnings)


class TestReportReader:
    def test_a_value_that_looks_doubled_is_left_alone(self):
        report = rp.read(
            (
                f"{GENERATOR._double('Detailed Report')}\n"
                f"{GENERATOR._double('Loyalty Store Visits Details')}\n"
                "Card_first_store_used_id 1133\n",
            )
        )
        (section,) = report.sections
        assert section.get("Card_first_store_used_id").value == "1133"

    def test_a_key_with_no_value_is_one_empty_field(self):
        report = rp.read(
            (
                f"{GENERATOR._double('Detailed Report')}\n"
                f"{GENERATOR._double('Loyalty Profile Details')}\n"
                "Maiden_nm\nPhone 3105550142\n",
            )
        )
        assert report.sections[0].get("Maiden_nm").value == ""
        assert report.sections[0].get("Phone").value == "3105550142"

    def test_a_wrapped_value_is_joined_to_the_row_above(self):
        report = rp.read(
            (
                "Detailed Report\nLoyalty Profile Details\n"
                "Address 12 LARKSPUR HOLLOW\nWAY UNIT 4\nState_cd CA\n",
            )
        )
        assert report.sections[0].get("Address").value == "12 LARKSPUR HOLLOW WAY UNIT 4"

    def test_the_same_report_reads_the_same_without_the_doubled_bold(self, report_text):
        plain = "\n".join(rp.heading(line) or line for line in report_text.splitlines())
        doubled, undoubled = rp.read((report_text,)), rp.read((plain,))
        assert [s.title for s in doubled.sections] == [s.title for s in undoubled.sections]
        assert doubled.request.keys() == undoubled.request.keys()

    def test_pages_are_counted_from_footers_in_text_and_from_pages_in_a_pdf(self, report_text):
        one, two = report_text.split("1 / 2\n")
        as_pdf = rp.read((one, two.replace("2 / 2\n", "")))
        as_text = rp.read((report_text,))
        assert as_pdf.section("Preference Details").page == 2
        assert as_text.section("Preference Details").page == 2


def json_text(value) -> str:
    import json

    return json.dumps(value)


class TestThroughTheApp:
    """Both files uploaded together, end to end: ingest keeps the workbook as
    one document rather than unpacking it, the registry picks this adapter, and
    everything it read is stored."""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from unbagged import api, db

        monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "test.sqlite"))
        monkeypatch.setenv("UNBAGGED_INCOMING", str(tmp_path / "incoming"))
        with TestClient(api.app) as test_client:
            yield test_client

    @pytest.fixture()
    def uploaded(self, client, report_text, workbook_bytes) -> int:
        xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response = client.post(
            "/api/requests",
            files=[
                ("files", ("report.txt", report_text.encode("utf-8"), "text/plain")),
                ("files", ("details.xlsx", workbook_bytes, xlsx)),
            ],
        )
        assert response.status_code == 201, response.text
        return response.json()["request_id"]

    def test_products_lists_what_was_bought_with_no_shelf_price(self, client, uploaded):
        """Regression: Products counted only lines with a shelf price, and this
        retailer discloses only what was paid, so the page was empty."""
        index = client.get(f"/api/requests/{uploaded}/product-index").json()
        # Every code that was ever sold at a positive amount: the predicate.
        bought = {
            upc for b in MODEL["baskets"] for _d, upc, _c, _q, _w, net in b["lines"] if net > 0
        }
        assert index["product_count"] == len(bought)
        timeline = client.get(f"/api/requests/{uploaded}/timeline").json()
        # The headline and the page under it agree on what a product is.
        assert timeline["stats"]["distinct_products"] == index["total_products"]

    def test_markdowns_and_refunds_are_still_not_purchases(self, client, uploaded):
        index = client.get(f"/api/requests/{uploaded}/product-index").json()
        stats = client.get(f"/api/requests/{uploaded}/timeline").json()["stats"]
        assert stats["negative_lines"] > 0
        assert stats["zero_value_lines"] > 0
        listed = json_text(index)
        assert "MKDN REALLOC" not in listed and "INSTANT SAVINGS" not in listed

    def test_prices_draws_what_was_paid_and_invents_no_shelf_price(self, client, uploaded):
        """Regression: Prices was empty for the same reason Products was."""
        history = client.get(f"/api/requests/{uploaded}/price-history?min_observations=3").json()
        assert history["product_count"] > 0
        assert history["priceable_count"] > 0
        assert history["quantity_disclosed"] is True
        for product in history["products"]:
            assert product["shelf_disclosed"] is False
            for point in product["points"]:
                assert point["retail_amt"] is None
                assert point["saved_amt"] is None
                assert point["paid_amt"] > 0

    def test_a_line_of_several_is_priced_per_unit(self, client, uploaded):
        """The response says how many, so a two-can line plots at one can's
        price instead of being set aside as a suspected multiple."""
        history = client.get(f"/api/requests/{uploaded}/price-history?min_observations=4").json()
        points = [point for product in history["products"] for point in product["points"]]
        several = [p for p in points if p["quantity"] and p["quantity"] > 1]
        assert several, "the fixture must carry lines of more than one"
        for point in several:
            # To a hundredth of a cent, which is where `_per_unit` rounds.
            assert point["unit_paid"] == pytest.approx(
                point["paid_amt"] / point["quantity"], abs=1e-4
            )
        assert all(not p["multiple_of"] for p in several)

    def test_only_the_weighed_products_go_unpriced(self, client, uploaded):
        """Weight is not a disclosed quantity — a weighed line says one — so a
        per-pound product is still found by its shape and still not drawn."""
        history = client.get(f"/api/requests/{uploaded}/price-history?min_observations=4").json()
        weighed = {upc for _d, upc, *_ in GENERATOR.WEIGHED}
        present = {p["upc"] for p in history["products"]} & weighed
        unpriced = {p["upc"] for p in history["products"] if not p["priceable"]}
        # Both directions, and not vacuously: every weighed product present is
        # left undrawn, and nothing else is.
        assert present, "the fixture must carry weighed products bought often enough"
        assert unpriced == present

    def test_no_saving_is_stated_where_no_shelf_price_was_disclosed(self, client, uploaded):
        """Regression: Compare showed a loyalty saving of minus everything paid.

        The request totals summed shelf prices over the lines that had one (none
        here) and paid amounts over every line, so their difference was the
        whole paid total, negated. Basket rows claimed a full-price saving of
        zero, and the basket detail a negative saving the size of the basket.
        With no shelf price disclosed there is no saving to state at any level.
        """
        paid = round(sum(net for b in MODEL["baskets"] for *_, net in b["lines"]), 2)
        compare = client.get("/api/compare").json()["requests"]
        (row,) = [r for r in compare if r["retailer_id"] == "albertsons"]
        assert row["total_paid"] == pytest.approx(paid)
        assert row["total_shelf"] is None
        assert row["total_saved"] is None

        timeline = client.get(f"/api/requests/{uploaded}/timeline").json()
        assert timeline["stats"]["total_saved"] is None
        assert "shelf_lines" not in timeline["stats"]
        for basket in timeline["baskets"]:
            assert basket["paid_total"] is not None
            assert basket["shelf_total"] is None
            assert basket["saved_total"] is None

        detail = client.get(f"/api/transactions/{timeline['baskets'][0]['id']}").json()
        assert detail["paid_total"] == pytest.approx(timeline["baskets"][0]["paid_total"])
        assert detail["shelf_total"] is None
        assert detail["saved_total"] is None

    def test_a_workbook_alone_is_not_shown_as_a_perfect_record(self, client, workbook_bytes):
        """Second-pass finding: ungraded categories were counted as answered,
        so a workbook uploaded alone read 'addressed every category'."""
        xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        created = client.post(
            "/api/requests", files=[("files", ("details.xlsx", workbook_bytes, xlsx))]
        )
        assert created.status_code == 201, created.text
        (row,) = client.get("/api/compliance").json()["rows"]
        assert row["absent_count"] == 0
        assert row["unassessed_count"] == len(DisclosureCategory) - 1
        (compared,) = client.get("/api/compare").json()["requests"]
        # Not zero: nothing was assessed that could make it zero.
        assert compared["absent_disclosures"] is None

    def test_the_pair_is_read_as_one_response(self, client, report_text, workbook_bytes):
        xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response = client.post(
            "/api/requests",
            files=[
                ("files", ("report.txt", report_text.encode("utf-8"), "text/plain")),
                ("files", ("Additional Details.xlsx", workbook_bytes, xlsx)),
            ],
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["retailer_id"] == "albertsons"
        assert body["summary"]["transactions"] == BASKETS
        assert body["summary"]["items"] == LINES
        documents = client.get(f"/api/requests/{body['request_id']}").json()["documents"]
        assert sorted(d["original_filename"] for d in documents) == [
            "Additional Details.xlsx",
            "report.txt",
        ]


class TestDates:
    """Every branch of `_occurred_on`, with known answers. The fixture writes
    whole 1900-epoch serials only, which left the rest unexercised."""

    @pytest.mark.parametrize(
        ("value", "date1904", "expected"),
        [
            ("45000", False, "2023-03-15"),
            ("45000", True, "2027-03-16"),
            ("45000.5", False, "2023-03-15T12:00:00"),
            # A hair short of midnight rounds to the next day, not to 00:00:00
            # of the same one.
            ("45000.9999999999", False, "2023-03-16"),
            ("2023-03-15", False, "2023-03-15"),
            ("3/5/2023", False, "2023-03-05"),
            # Shaped like dates and not dates: refused, never stored.
            ("2020-13-45", False, None),
            ("13/45/2020", False, None),
            ("99999999", False, None),
            ("0", False, None),
            ("x", False, None),
            (None, False, None),
        ],
    )
    def test_known_answers(self, value, date1904, expected):
        from unbagged.adapters.albertsons.adapter import _occurred_on

        assert _occurred_on(value, date1904) == expected

    def test_an_impossible_date_costs_its_line_not_the_upload(self, tmp_path, report_text):
        serial = str((MODEL["baskets"][0]["day"] - GENERATOR.date(1899, 12, 30)).days)
        broken = rewritten_workbook(
            GENERATOR.build_workbook(),
            "xl/worksheets/sheet1.xml",
            lambda x: x.replace(
                f'<c r="A4" s="4"><v>{serial}</v>', '<c r="A4" s="4"><v>99999999</v>', 1
            ),
        )
        result = AlbertsonsAdapter().parse(
            bundle(tmp_path, ("r.txt", report_text), ("d.xlsx", broken))
        )
        assert result.item_count() == LINES - 1


class TestTheReportsOwnClaims:
    def test_a_placeholder_name_is_not_a_different_name(self, tmp_path, report_text):
        """A profile with no first name printed `I/ss`, and was reported as one
        registered to somebody else: an untrue claim, in a letter."""
        blank = report_text.replace(
            f"First_nm {GENERATOR.HOLDER.upper()}", f"First_nm {GENERATOR.PLACEHOLDER}"
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("r.txt", blank)))
        assert not any("different first name" in f.description for f in result.follow_ups)

    def test_a_disclosed_email_is_kept_and_not_denied(self, tmp_path, report_text):
        with_email = report_text.replace("Phone ", "Email someone@example.com\nPhone ", 1)
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("r.txt", with_email)))
        assert any(i.id_type is IdType.EMAIL for i in result.identities)
        pieces = next(
            d for d in result.disclosures if d.category is DisclosureCategory.SPECIFIC_PIECES
        )
        assert "email" not in (pieces.notes or "").lower()


class TestWithoutTheReport:
    """D3: a workbook alone grades only what a workbook can show."""

    def test_categories_that_need_the_report_are_not_graded(self, tmp_path, workbook_bytes):
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", workbook_bytes)))
        assert {d.category for d in result.disclosures} == {DisclosureCategory.SPECIFIC_PIECES}
        assert not any(f.kind is FollowUpKind.MISSING_CATEGORY for f in result.follow_ups)
        assert any("not graded" in w.message for w in result.warnings)

    def test_the_note_claims_nothing_about_files_it_did_not_see(self, tmp_path, report_text):
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("r.txt", report_text)))
        pieces = next(
            d for d in result.disclosures if d.category is DisclosureCategory.SPECIFIC_PIECES
        )
        # No workbook was read, so nothing may be said about its columns.
        assert "shelf price" not in (pieces.notes or "")


class TestLinesThatAreNotProducts:
    def test_department_key_rings_carry_no_product_code(self, parsed):
        """An open-department ring reuses one short code for unrelated items,
        so storing it would merge them into one 'product'."""
        rings = [
            item
            for txn in parsed.transactions
            for item in txn.items
            if "DEPT KEY" in item.description_raw or "DEPARTMENT KEY" in item.description_raw
        ]
        assert rings, "the fixture must carry department-key rings"
        assert all(item.upc is None for item in rings)
        short = [i for t in parsed.transactions for i in t.items if i.upc and len(i.upc) <= 3]
        assert short == []


class TestRowsAreReadInOrder:
    def test_a_row_numbered_at_or_below_the_header_is_still_read(self, tmp_path, workbook_bytes):
        """Rows were skipped by the number they declare, which is the sheet's to
        write: a data row numbered 1 vanished without a warning."""
        renumbered = rewritten_workbook(
            workbook_bytes,
            "xl/worksheets/sheet1.xml",
            lambda x: x.replace('<row r="4">', '<row r="1">', 1),
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", renumbered)))
        assert result.item_count() == LINES


class TestWarningsDoNotFlood:
    def test_one_quirk_on_every_line_is_one_warning_with_a_count(self, tmp_path, workbook_bytes):
        junk = rewritten_workbook(
            workbook_bytes,
            "xl/worksheets/sheet1.xml",
            lambda x: re.sub(r'(<c r="J\d+" s="8"><v>)[^<]*', r"\1NaN", x),
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", junk)))
        amounts = [w for w in result.warnings if "readable amount" in w.message]
        assert len(amounts) <= 6
        assert any(str(LINES - 5) in w.message for w in amounts)


class TestMoreDegradation:
    def test_a_line_without_a_transaction_number_costs_that_line(self, tmp_path, workbook_bytes):
        broken = rewritten_workbook(
            workbook_bytes,
            "xl/worksheets/sheet1.xml",
            lambda x: re.sub(r'(<c r="B4" s="5">)<v>[^<]*</v>', r"\1", x, count=1),
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", broken)))
        assert result.item_count() == LINES - 1
        assert any(w.locator == "Albertsons Transactions!B4" for w in result.warnings)

    def test_one_transaction_on_two_dates_keeps_its_lines_together(self, tmp_path, workbook_bytes):
        first = MODEL["baskets"][0]
        assert len(first["lines"]) >= 2, "the first fixture basket must have two lines"
        serial = (first["day"] - GENERATOR.date(1899, 12, 30)).days
        moved = rewritten_workbook(
            workbook_bytes,
            "xl/worksheets/sheet1.xml",
            lambda x: x.replace(
                f'<c r="A5" s="4"><v>{serial}</v>', f'<c r="A5" s="4"><v>{serial + 1}</v>', 1
            ),
        )
        result = AlbertsonsAdapter().parse(bundle(tmp_path, ("d.xlsx", moved)))
        assert result.item_count() == LINES
        assert len(result.transactions) == BASKETS
        assert any("more than one date" in w.message for w in result.warnings)


class TestTheRequestersName:
    def test_it_is_recorded_as_a_name_the_response_holds(self, parsed):
        names = {i.value for i in parsed.identities if i.id_type is IdType.NAME}
        assert f"{GENERATOR.REQUESTER} {GENERATOR.FAMILY}" in names
