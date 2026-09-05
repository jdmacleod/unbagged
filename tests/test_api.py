"""The API, end to end, against the synthetic fixture."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from unbagged import api, db
from unbagged.models import DisclosureCategory

FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("UNBAGGED_INCOMING", str(tmp_path / "incoming"))
    with TestClient(api.app) as test_client:
        yield test_client


@pytest.fixture
def uploaded(client):
    with FIXTURE.open("rb") as fh:
        response = client.post(
            "/api/requests",
            files={"files": ("synthetic_report.txt", fh, "text/plain")},
            data={"declared_retailer": "kroger"},
        )
    assert response.status_code == 201, response.text
    return response.json()


class TestMeta:
    def test_health(self, client):
        assert client.get("/api/health").json()["status"] == "ok"

    def test_adapters_are_listed_for_the_upload_form(self, client):
        ids = [a["retailer_id"] for a in client.get("/api/adapters").json()["adapters"]]
        assert "kroger" in ids

    def test_the_openapi_schema_generates(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/requests/{request_id}/timeline" in schema["paths"]


class TestProductIndex:
    """The index endpoint, and the properties the view is designed around."""

    def test_it_lists_products_alphabetically(self, client, uploaded):
        body = client.get(
            f"/api/requests/{uploaded['request_id']}/product-index"
        ).json()
        names = [p["description"] for p in body["products"]]
        assert names == sorted(names)

    def test_the_headline_counts_the_response_not_the_filter(self, client, uploaded):
        """`total_products` and `bought_once_total` are facts about the
        response. Recomputing them from the filtered set would make the
        headline figure change as you type, which would state something untrue
        about the retailer."""
        rid = uploaded["request_id"]
        everything = client.get(f"/api/requests/{rid}/product-index").json()
        filtered = client.get(
            f"/api/requests/{rid}/product-index", params={"min_purchases": 4}
        ).json()
        assert filtered["total_products"] == everything["total_products"]
        assert filtered["bought_once_total"] == everything["bought_once_total"]
        assert filtered["product_count"] < everything["product_count"]

    def test_tiers_are_absolute_so_a_filter_cannot_resize_a_product(self, client, uploaded):
        """The whole reason for quantising. On a scale normalised between the
        smallest and largest count, filtering changes the range and every
        surviving product silently changes size."""
        rid = uploaded["request_id"]
        everything = client.get(f"/api/requests/{rid}/product-index").json()
        filtered = client.get(
            f"/api/requests/{rid}/product-index", params={"min_purchases": 3}
        ).json()
        before = {p["upc"]: p["tier"] for p in everything["products"]}
        for product in filtered["products"]:
            assert product["tier"] == before[product["upc"]]

    def test_a_filter_matching_one_product_does_not_divide_by_zero(self, client, uploaded):
        """min == max is not a special case when the tiers are absolute. With
        two thirds of products bought exactly once this is a likely filter
        result, not an exotic one."""
        rid = uploaded["request_id"]
        everything = client.get(f"/api/requests/{rid}/product-index").json()
        one = everything["products"][0]
        body = client.get(
            f"/api/requests/{rid}/product-index", params={"q": one["description"]}
        ).json()
        assert body["product_count"] >= 1
        assert all(p["tier"] in (1, 2, 3, 4, 5) for p in body["products"])

    def test_the_search_matches_what_was_typed(self, client, uploaded):
        """No LIKE, so no wildcards to leak. A percent sign is a character
        someone typed, and this catalogue really does contain "2% MILK", so the
        right answer is those products — not all of them, and not none."""
        rid = uploaded["request_id"]
        everything = client.get(f"/api/requests/{rid}/product-index").json()
        wild = client.get(
            f"/api/requests/{rid}/product-index", params={"q": "%"}
        ).json()
        assert 0 < wild["product_count"] < everything["product_count"]
        assert all("%" in p["description"] for p in wild["products"])

    def test_search_is_case_insensitive(self, client, uploaded):
        rid = uploaded["request_id"]
        lower = client.get(f"/api/requests/{rid}/product-index", params={"q": "milk"})
        upper = client.get(f"/api/requests/{rid}/product-index", params={"q": "MILK"})
        assert lower.json()["product_count"] == upper.json()["product_count"]

    def test_placeholder_and_refund_lines_never_become_products(self, client, uploaded):
        """The export carries zero-value placeholder rows naming no product, and
        negative rows that are returns. Neither is something you bought, and the
        same predicate `price_history` uses excludes both."""
        rid = uploaded["request_id"]
        body = client.get(f"/api/requests/{rid}/product-index").json()
        assert all(p["purchases"] > 0 for p in body["products"])
        assert not any(p["description"] == "UNKNOWN" for p in body["products"])

    def test_stopped_products_were_bought_more_than_once(self, client, uploaded):
        """Otherwise 'you stopped buying this' is a label on every product
        anyone ever tried once, and two thirds of them were."""
        rid = uploaded["request_id"]
        body = client.get(f"/api/requests/{rid}/product-index").json()
        for product in body["products"]:
            if product["stopped"]:
                assert product["purchases"] >= 2
                assert product["last_seen"] < body["stale_before"]

    def test_the_cap_discloses_itself(self, client, uploaded):
        rid = uploaded["request_id"]
        body = client.get(
            f"/api/requests/{rid}/product-index", params={"limit": 5}
        ).json()
        assert body["truncated"] is True
        assert len(body["products"]) == 5
        assert body["product_count"] > 5

    def test_a_missing_request_is_a_404(self, client):
        assert client.get("/api/requests/9999/product-index").status_code == 404


class TestClickThroughContract:
    """Pins the contract the Products index depends on: the query string it
    sends must return the visits containing that product, and *only* that
    product. Without this, a change to Timeline's matching silently empties
    every click, or silently over-fills it, and nothing turns red."""

    def test_an_index_entry_filters_the_timeline_to_itself(self, client, uploaded):
        rid = uploaded["request_id"]
        index = client.get(f"/api/requests/{rid}/product-index").json()
        # A product bought several times, so the assertion is about matching
        # rather than about a single lucky row.
        product = max(index["products"], key=lambda p: p["purchases"])
        timeline = client.get(
            f"/api/requests/{rid}/timeline", params={"q": product["upc"]}
        ).json()
        assert timeline["baskets"], product["upc"]
        # Every visit that shows a positive line, plus at most the visits where
        # the product came back as a refund. Never more.
        assert product["purchases"] <= timeline["filtered_count"] <= product["purchases"] + 3

    def test_linking_by_name_would_pull_in_other_products(self, client, uploaded):
        """Why the link carries a UPC and not the name.

        The timeline search is a substring match and this catalogue is full of
        names that contain each other — BANANAS EA is inside ORGANIC BANANAS EA
        and SIMPLE TRUTH ORG BANANAS EA. Linking by name opened a timeline
        claiming 26 visits "that included" a product bought 20 times, six of
        which were a different product. This test fails if someone switches the
        link back to the description.
        """
        rid = uploaded["request_id"]
        index = client.get(f"/api/requests/{rid}/product-index").json()
        by_name = {p["description"]: p for p in index["products"]}
        contained = next(
            (
                name
                for name in by_name
                if any(name != other and name in other for other in by_name)
            ),
            None,
        )
        assert contained is not None, "fixture no longer has overlapping names"

        by_upc = client.get(
            f"/api/requests/{rid}/timeline", params={"q": by_name[contained]["upc"]}
        ).json()["filtered_count"]
        by_description = client.get(
            f"/api/requests/{rid}/timeline", params={"q": contained}
        ).json()["filtered_count"]
        assert by_upc < by_description


class TestUpload:
    def test_a_report_is_ingested_and_summarised(self, uploaded):
        assert uploaded["retailer_id"] == "kroger"
        assert uploaded["confident"]
        summary = uploaded["summary"]
        assert summary["transactions"] > 100
        assert summary["items"] > 1000
        assert summary["disclosures"] == len(DisclosureCategory)
        assert uploaded["warnings"] == []

    def test_the_stored_file_lands_in_the_incoming_directory(self, client, uploaded, tmp_path):
        # Gitignored, outside the Docker build context, covered by a pre-commit hook.
        stored = list((tmp_path / "incoming").iterdir())
        assert len(stored) == 1
        assert stored[0].name.endswith("synthetic_report.txt")

    def test_an_empty_upload_is_rejected_with_a_readable_message(self, client):
        response = client.post(
            "/api/requests", files={"files": ("empty.txt", b"", "text/plain")}
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_a_letter_with_no_data_is_accepted_as_a_finding(self, client):
        """A retailer that answers with prose has still not disclosed anything.

        Refusing to ingest it would hide the finding behind an error message, so
        it goes to the fallback adapter and shows up in the matrix as absent.
        """
        response = client.post(
            "/api/requests",
            files={"files": ("letter.txt", b"Dear customer, hello.\n", "text/plain")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["retailer_id"] == "generic"
        # Presented as a guess, not a finding: the UI labels it uncertain.
        assert body["confident"] is False
        assert body["summary"]["transactions"] == 0
        assert body["summary"]["disclosures"] == len(DisclosureCategory)
        assert any("no adapter recognised" in w["message"].lower()
                   for w in body["warnings"])

    def test_a_scanned_pdf_is_told_it_has_no_text_layer(self, client):
        """The app already knew this and used to say something else.

        A scanned PDF and a .zip both used to return "may need a new adapter —
        see docs/writing-an-adapter.md", which a shopper cannot act on, while
        extraction.py had already worked out the real reason.
        """
        response = client.post(
            "/api/requests",
            files={"files": ("scan.pdf", b"%PDF-1.4\nnot really\n", "application/pdf")},
        )
        assert response.status_code == 400
        detail = response.json()["detail"].lower()
        assert "no text" in detail or "ocr" in detail
        assert "writing-an-adapter" not in detail

    def test_an_archive_is_told_to_unzip_first(self, client):
        # A zip is what Safeway sends, per docs/handoff.md section 4.
        response = client.post(
            "/api/requests",
            files={"files": ("bundle.zip", b"PK\x03\x04nope", "application/zip")},
        )
        assert response.status_code == 400
        assert "unzip" in response.json()["detail"].lower()

    def test_the_same_file_twice_in_one_upload_is_refused(self, client):
        payload = FIXTURE.read_bytes()
        response = client.post(
            "/api/requests",
            files=[
                ("files", ("a.txt", payload, "text/plain")),
                ("files", ("b.txt", payload, "text/plain")),
            ],
        )
        assert response.status_code == 400
        assert "double every basket" in response.json()["detail"]

    def test_a_wrong_retailer_hint_does_not_win(self, client):
        with FIXTURE.open("rb") as fh:
            response = client.post(
                "/api/requests",
                files={"files": ("report.txt", fh, "text/plain")},
                data={"declared_retailer": "safeway"},
            )
        assert response.json()["retailer_id"] == "kroger"


class TestRequests:
    def test_listing_and_fetching(self, client, uploaded):
        listed = client.get("/api/requests").json()["requests"]
        assert len(listed) == 1
        detail = client.get(f"/api/requests/{uploaded['request_id']}").json()
        assert detail["retailer_id"] == "kroger"
        assert len(detail["documents"]) == 1
        assert detail["documents"][0]["sha256"]

    def test_an_unknown_request_is_a_404(self, client):
        assert client.get("/api/requests/999").status_code == 404

    def test_deleting_takes_everything_with_it(self, client, uploaded):
        request_id = uploaded["request_id"]
        assert client.delete(f"/api/requests/{request_id}").status_code == 204
        assert client.get(f"/api/requests/{request_id}").status_code == 404
        assert client.get(f"/api/requests/{request_id}/timeline").status_code == 404


class TestTimeline:
    def test_stats_and_baskets(self, client, uploaded):
        data = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        stats = data["stats"]
        assert stats["basket_count"] == len(data["baskets"])
        assert stats["total_shelf"] > 0
        assert stats["first_visit"] < stats["last_visit"]
        assert stats["stores"]

    def test_placeholder_lines_are_counted_separately_not_as_products(self, client, uploaded):
        stats = client.get(
            f"/api/requests/{uploaded['request_id']}/timeline"
        ).json()["stats"]
        # Counting them as products would inflate the number; hiding them would
        # conceal a fact about the quality of the disclosure.
        assert stats["zero_value_lines"] > 0
        assert stats["distinct_products"] > 100
        assert stats["distinct_products"] < stats["line_count"]

    def test_returns_are_visible_rather_than_filtered(self, client, uploaded):
        stats = client.get(
            f"/api/requests/{uploaded['request_id']}/timeline"
        ).json()["stats"]
        assert stats["negative_lines"] > 0

    def test_filtering_by_store(self, client, uploaded):
        request_id = uploaded["request_id"]
        all_baskets = client.get(f"/api/requests/{request_id}/timeline").json()
        store = all_baskets["stats"]["stores"][0]["store_code"]
        filtered = client.get(
            f"/api/requests/{request_id}/timeline", params={"store": store}
        ).json()
        assert 0 < filtered["filtered_count"] < all_baskets["filtered_count"]
        assert all(b["store_code"] == store for b in filtered["baskets"])

    def test_filtering_by_date_range(self, client, uploaded):
        request_id = uploaded["request_id"]
        data = client.get(
            f"/api/requests/{request_id}/timeline",
            params={"date_from": "2025-01-01", "date_to": "2025-06-30"},
        ).json()
        assert data["baskets"]
        assert all("2025-01-01" <= b["occurred_at"][:10] <= "2025-06-30"
                   for b in data["baskets"])

    def test_searching_returns_the_baskets_containing_a_match(self, client, uploaded):
        request_id = uploaded["request_id"]
        data = client.get(
            f"/api/requests/{request_id}/timeline", params={"q": "BANANA"}
        ).json()
        assert data["filtered_count"] > 0
        detail = client.get(f"/api/transactions/{data['baskets'][0]['id']}").json()
        assert any("BANANA" in i["description_raw"] for i in detail["items"])


class TestTransactionDetail:
    def test_line_items_show_the_discount_delta(self, client, uploaded):
        """`loyalty_amt` is the price paid, so the saving is the difference.

        This test previously asserted the opposite — that what you paid is
        retail minus loyalty — which is the bug it now guards against. On a
        full-price line, where the two amounts are equal, that arithmetic
        renders $0.00 under a column headed "You paid".
        """
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        detail = client.get(f"/api/transactions/{timeline['baskets'][0]['id']}").json()
        assert detail["items"]
        for item in detail["items"]:
            if item["retail_amt"] is None:
                continue
            expected = (
                item["retail_amt"] if item["loyalty_amt"] is None else item["loyalty_amt"]
            )
            assert item["paid_amt"] == expected
            assert item["saved_amt"] == round(item["retail_amt"] - expected, 2)

    def test_a_full_price_line_is_not_free(self, client, uploaded):
        """The regression in one line.

        Most lines in a real response carry a loyalty price equal to the shelf
        price: ordinary items, no promotion. Reading that field as a discount
        makes every one of them 100% off.
        """
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        full_price = 0
        # 25 baskets, not 15. The count below is a sample-size floor rather than
        # a property, and baskets got smaller when the generator stopped putting
        # the same product on two lines of one trip.
        for basket in timeline["baskets"][:25]:
            detail = client.get(f"/api/transactions/{basket['id']}").json()
            for item in detail["items"]:
                r, loyal = item["retail_amt"], item["loyalty_amt"]
                if r is None or loyal is None or r <= 0 or abs(loyal - r) >= 0.005:
                    continue
                full_price += 1
                assert item["paid_amt"] == r, "a full-price line costs its shelf price"
                assert item["saved_amt"] == 0
        assert full_price > 50, "the fixture must carry full-price lines"

    def test_every_basket_is_traceable(self, client, uploaded):
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        for basket in timeline["baskets"][:5]:
            assert basket["provenance"]["locator"]
            assert basket["provenance"]["page"] >= 1
            assert basket["provenance"]["source_document_id"]

    def test_the_drill_down_foots_to_the_row_that_opened_it(self, client, uploaded):
        """The basket row and its own line items have to agree.

        They did not. The row showed summed `retail_amt` — the shelf amount
        before discounts — while the "You paid" column beneath it subtracted the
        loyalty discount per line, so the two disagreed by the whole discount
        with nothing on screen explaining the gap.
        """
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        checked = 0
        for basket in timeline["baskets"][:10]:
            detail = client.get(f"/api/transactions/{basket['id']}").json()
            paid_from_lines = round(
                sum(i["paid_amt"] or 0 for i in detail["items"]), 2
            )
            assert detail["paid_total"] == paid_from_lines
            assert basket["paid_total"] == paid_from_lines
            assert basket["shelf_total"] == detail["shelf_total"]
            assert basket["saved_total"] == round(
                basket["shelf_total"] - basket["paid_total"], 2
            )
            checked += 1
        assert checked == 10

    def test_paid_is_at_most_shelf_and_usually_close_to_it(self, client, uploaded):
        """Sanity bounds that catch a discount/price mix-up from either side.

        Reading the loyalty price as a discount put paid at a small fraction of
        shelf — about 14% on a real response. A tool whose spend figure is an
        eighth of the truth looks plausible on every screen.
        """
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        priced = [b for b in timeline["baskets"] if b["shelf_total"] > 0]
        assert priced
        for basket in priced:
            assert basket["paid_total"] <= basket["shelf_total"] + 0.005
            assert basket["saved_total"] >= -0.005
        saved = sum(b["saved_total"] for b in priced)
        shelf = sum(b["shelf_total"] for b in priced)
        assert 0 < saved / shelf < 0.35, "a loyalty saving, not most of the basket"

    def test_summed_lines_match_the_total_the_retailer_stated(self, client, uploaded):
        """The retailer's own arithmetic, used as a check on ours.

        Every basket states `total_amount_prior_to_discounts`. It was parsed and
        never compared to anything. A non-zero delta means the parse dropped a
        line, which would make every total in the app quietly short.
        """
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        deltas = [b["stated_pre_discount_delta"] for b in timeline["baskets"]]
        assert all(d is not None for d in deltas), "the fixture states a total per basket"
        assert all(abs(d) < 0.01 for d in deltas)

    def test_an_unknown_transaction_is_a_404(self, client, uploaded):
        assert client.get("/api/transactions/999999").status_code == 404


class TestProfile:
    def test_inferences_are_split_by_origin(self, client, uploaded):
        data = client.get(f"/api/requests/{uploaded['request_id']}/profile").json()
        by_origin = data["inferences_by_origin"]
        assert len(by_origin["first_party_model"]) == 5
        assert len(by_origin["appended_third_party"]) > 10

    def test_household_scoped_attributes_are_called_out(self, client, uploaded):
        # These describe people who never enrolled in anything.
        data = client.get(f"/api/requests/{uploaded['request_id']}/profile").json()
        labels = {i["label"] for i in data["household_scoped"]}
        assert {"Income Predictor Score (in $000)",
                "Number of Children in Household"} <= labels

    def test_the_identity_graph_is_returned_with_provenance(self, client, uploaded):
        data = client.get(f"/api/requests/{uploaded['request_id']}/profile").json()
        assert data["identity_count"] > 5
        assert all(i["provenance"]["locator"] for i in data["identities"])

    def test_derivability_survives_the_round_trip(self, client, uploaded):
        # True / False / unknown are three different claims, and SQLite's
        # 1 / 0 / NULL must not collapse the third into the second.
        data = client.get(f"/api/requests/{uploaded['request_id']}/profile").json()
        appended = {
            i["label"]: i["derivable_from_txns"]
            for i in data["inferences_by_origin"]["appended_third_party"]
        }
        assert appended["Cat Owner"] is True
        assert appended["Education Level of Individual"] is False


class TestCompliance:
    def test_the_matrix_has_a_cell_for_every_category(self, client, uploaded):
        data = client.get("/api/compliance").json()
        assert data["categories"] == [c.value for c in DisclosureCategory]
        row = data["rows"][0]
        assert set(row["cells"]) == set(data["categories"])
        assert row["absent_count"] == 7
        assert row["cells"]["SPECIFIC_PIECES"]["status"] == "provided"

    def test_absent_cells_explain_themselves(self, client, uploaded):
        row = client.get("/api/compliance").json()["rows"][0]
        assert row["cells"]["SOURCES"]["notes"]

    def test_follow_ups_travel_with_the_row(self, client, uploaded):
        row = client.get("/api/compliance").json()["rows"][0]
        assert any(f["kind"] == "supplemental_period" for f in row["follow_ups"])

    def test_an_empty_database_returns_an_empty_matrix(self, client):
        assert client.get("/api/compliance").json()["rows"] == []


class TestFollowUpLetter:
    def test_the_letter_names_the_missing_categories(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/follow-up-letter"
        ).json()
        assert len(data["absent_categories"]) == 7
        assert "1798.110(a)(2)" in data["letter"]
        assert "[your name]" in data["letter"]

    def test_the_letter_reports_rather_than_accuses(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/follow-up-letter"
        ).json()
        assert "not legal advice" in data["note"]
        for word in ("violat", "unlawful", "illegal", "breach"):
            assert word not in data["letter"].lower()

    def test_the_supplemental_period_is_requested_too(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/follow-up-letter"
        ).json()
        assert "earlier period" in data["letter"]


class TestCompare:
    def test_a_single_retailer_is_not_comparable_yet(self, client, uploaded):
        data = client.get("/api/compare").json()
        assert data["comparable"] is False
        assert len(data["requests"]) == 1

    def test_two_retailers_compare(self, client, uploaded):
        """Two retailers means two different responses.

        This used to create its second retailer by re-uploading the identical
        fixture, which only worked because of the bug fixed in ISSUE-002: the
        same report could be ingested twice as two indistinguishable requests.
        That is not what two retailers looks like, and the assertion that both
        columns held identical counts was really asserting the duplicate.

        Updated by /qa on 2026-09-03 rather than deleted: the intent, that
        Compare works with more than one response, is preserved and now tested
        against a genuinely different second response.
        """
        client.post(
            "/api/requests",
            files={"files": ("letter.txt", b"Dear customer, we do not sell data.\n",
                             "text/plain")},
            data={"declared_retailer": "Corner Market"},
        )
        data = client.get("/api/compare").json()
        assert data["comparable"] is True
        assert len(data["requests"]) == 2

        by_name = {r["display_name"]: r for r in data["requests"]}
        kroger = by_name["Kroger"]
        assert kroger["identifier_count"] > 0
        assert kroger["appended_inference_count"] > 0
        assert kroger["absent_disclosures"] == 7

        # The second retailer disclosed nothing, so its metrics are null rather
        # than zero, and its absent count is still a real finding.
        letter = by_name["Corner Market"]
        assert letter["disclosed"] is False
        assert letter["identifier_count"] is None
        assert letter["absent_disclosures"] > 0


class TestPriceHistory:
    def test_products_seen_repeatedly_get_a_series(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history"
        ).json()
        assert data["product_count"] > 20
        product = data["products"][0]
        assert product["purchases"] >= data["min_observations"]
        assert len(product["points"]) == product["purchases"]
        assert product["first_seen"] <= product["last_seen"]

    def test_one_line_is_one_purchase(self, client, uploaded):
        """Deliberately replaces a test that asserted the opposite.

        The removed test pinned that several lines sharing a date collapse into
        one point, on the belief that buying three of something arrived as three
        lines. Measured across a real response that happened on 0 of 762
        product-days: the format puts the whole trip on one line and multiplies
        the amount instead. The old belief came from the synthetic fixture,
        whose generator picks products with replacement, so the fixture produced
        a shape the real format never emits.
        """
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history",
            params={"min_observations": 2},
        ).json()
        assert data["quantity_disclosed"] is False

        for product in data["products"]:
            dates = [point["date"] for point in product["points"]]
            assert dates == sorted(dates)
            assert product["purchases"] == len(product["points"])

    def test_products_the_response_cannot_price_are_separated_out(self, client, uploaded):
        """A line carries an amount, never a quantity and never a weight.

        So an amount at twice another can be a price rise or a second item, and
        an amount that moves every trip can be a per-pound product. Neither is a
        unit price, and neither may drive a price-change figure.
        """
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history",
            params={"min_observations": 2},
        ).json()
        assert data["priceable_count"] <= data["product_count"]

        for product in data["products"]:
            assert product["shape"] in {"unit", "multiple", "weight"}
            assert product["priceable"] == (product["shape"] == "unit")
            # Only a product whose amounts look like single units gets a change.
            if not product["priceable"]:
                assert product["change_pct"] is None
            # Points flagged as more than one item never set the endpoints.
            flagged = [p for p in product["points"] if p["multiple_of"]]
            assert all(p["multiple_of"] >= 2 for p in flagged)

    def test_priceable_products_are_listed_first(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history",
            params={"min_observations": 2},
        ).json()
        flags = [p["priceable"] for p in data["products"]]
        assert flags == sorted(flags, reverse=True), "priceable first, then the rest"

    def test_points_carry_what_was_paid_not_only_the_shelf_price(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history"
        ).json()
        discounted = 0
        for product in data["products"]:
            for point in product["points"]:
                assert point["paid_amt"] <= point["retail_amt"] + 0.005
                assert point["saved_amt"] == round(
                    point["retail_amt"] - point["paid_amt"], 2
                )
                discounted += point["saved_amt"] > 0
        assert discounted, "the fixture must carry discounts or this proves nothing"

    def test_refunds_are_excluded_from_prices(self, client, uploaded):
        # A negative amount is a refund, not a price — but it stays in the
        # transaction record.
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history"
        ).json()
        for product in data["products"]:
            assert all(p["retail_amt"] > 0 for p in product["points"])

    def test_the_threshold_is_adjustable(self, client, uploaded):
        request_id = uploaded["request_id"]
        loose = client.get(
            f"/api/requests/{request_id}/price-history",
            params={"min_observations": 2},
        ).json()
        tight = client.get(
            f"/api/requests/{request_id}/price-history",
            params={"min_observations": 10},
        ).json()
        assert loose["product_count"] > tight["product_count"]


class TestDisclosedVersusZero:
    """A count of zero and a silence are different claims.

    "Identifiers held for you: 0" is a statement about the retailer. A response
    that disclosed nothing never made that statement, and rendering it as 0 says
    the opposite of what the whole product is for.
    """

    @pytest.fixture
    def letter_only(self, client):
        response = client.post(
            "/api/requests",
            files={"files": ("letter.txt", b"Dear customer, thank you for writing.\n",
                             "text/plain")},
            data={"declared_retailer": "Corner Market"},
        )
        assert response.status_code == 201
        return response.json()["request_id"]

    def test_a_letter_reports_not_disclosed_rather_than_zero(self, client, letter_only):
        stats = client.get(f"/api/requests/{letter_only}/timeline").json()["stats"]
        assert stats["disclosed"] is False
        for key in ("basket_count", "total_shelf", "total_paid", "distinct_products",
                    "first_visit", "last_visit", "line_count"):
            assert stats[key] is None, f"{key} should be null, not zero"

    def test_compare_nulls_the_metrics_it_cannot_support(self, client, uploaded, letter_only):
        rows = {r["display_name"]: r for r in client.get("/api/compare").json()["requests"]}
        letter = rows["Corner Market"]
        assert letter["disclosed"] is False
        for key in ("visits", "total_paid", "total_shelf", "distinct_products",
                    "identifier_count", "inference_count",
                    "appended_inference_count"):
            assert letter[key] is None, f"{key} should be null, not zero"

    def test_what_the_retailer_failed_to_address_is_still_counted(
        self, client, uploaded, letter_only
    ):
        # This one is never nulled: it is a real finding about the retailer, and
        # it is the entire reason a response like this is worth reading.
        rows = {r["display_name"]: r for r in client.get("/api/compare").json()["requests"]}
        assert rows["Corner Market"]["absent_disclosures"] > 0

    def test_a_retailer_that_did_disclose_keeps_its_real_numbers(self, client, uploaded):
        rows = {r["display_name"]: r for r in client.get("/api/compare").json()["requests"]}
        kroger = rows["Kroger"]
        assert kroger["disclosed"] is True
        assert kroger["visits"] > 100
        assert kroger["identifier_count"] > 0

    def test_a_genuine_zero_survives_when_data_was_disclosed(self, client, uploaded):
        """The gate must not swallow real zeros.

        Filtering the timeline to a window with no visits is a true zero, and it
        has to stay 0 rather than becoming "not disclosed".
        """
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/timeline",
            params={"date_from": "1990-01-01", "date_to": "1990-12-31"},
        ).json()
        assert data["stats"]["disclosed"] is True
        assert data["filtered_count"] == 0


class TestDuplicateAcrossRequests:
    """Regression: ISSUE-002 — the same report ingested twice as two requests
    Found by /qa on 2026-09-03
    Report: .gstack/qa-reports/qa-report-localhost8420-2026-09-03.md

    The in-upload duplicate check only covered one request. Dropping the same
    file again — what people do when a 13-second parse shows no progress —
    produced a second request identical to the first: same retailer, same
    reference, indistinguishable in the selector, and a duplicate column in
    Compare. The message told the reader to remove the existing one, which was
    not possible from the UI until /devex-review added the control on
    2026-09-04; `test_reloading_after_deleting_the_original_works` below is the
    other half of that instruction.
    """

    def test_the_same_report_cannot_be_loaded_twice(self, client, uploaded):
        with FIXTURE.open("rb") as fh:
            second = client.post(
                "/api/requests", files={"files": ("again.txt", fh, "text/plain")}
            )
        assert second.status_code == 400
        detail = second.json()["detail"]
        assert "already loaded" in detail
        assert "Kroger" in detail, "the message must name the existing entry"
        assert len(client.get("/api/requests").json()["requests"]) == 1

    def test_a_different_file_is_still_accepted(self, client, uploaded):
        response = client.post(
            "/api/requests",
            files={"files": ("letter.txt", b"Dear customer, hello.\n", "text/plain")},
            data={"declared_retailer": "Corner Market"},
        )
        assert response.status_code == 201
        assert len(client.get("/api/requests").json()["requests"]) == 2

    def test_reloading_after_deleting_the_original_works(self, client, uploaded):
        # The message tells the user to remove the existing one first, so that
        # path has to actually work.
        client.delete(f"/api/requests/{uploaded['request_id']}")
        with FIXTURE.open("rb") as fh:
            again = client.post(
                "/api/requests", files={"files": ("again.txt", fh, "text/plain")}
            )
        assert again.status_code == 201


class TestSearchWildcards:
    """Regression: ISSUE-001 — LIKE wildcards leaked from the search box
    Found by /qa on 2026-09-03
    Report: .gstack/qa-reports/qa-report-localhost8420-2026-09-03.md

    The search fed raw input into a LIKE pattern. "%" returned every basket and
    "_" matched any single character, so a product code containing an underscore
    silently over-matched with nothing on screen to explain why. The value was
    always bound, so injection was never possible — this is a correctness bug,
    not a security one.
    """

    def test_a_percent_matches_a_literal_percent_not_everything(self, client, uploaded):
        rid = uploaded["request_id"]
        everything = client.get(f"/api/requests/{rid}/timeline").json()["filtered_count"]
        percent = client.get(
            f"/api/requests/{rid}/timeline", params={"q": "%"}
        ).json()
        assert percent["filtered_count"] < everything, "% still behaves as a wildcard"
        # The fixture carries "SIMPLE TRUTH 2% MILK", so a literal match is expected.
        assert percent["filtered_count"] > 0
        detail = client.get(f"/api/transactions/{percent['baskets'][0]['id']}").json()
        assert any("%" in i["description_raw"] for i in detail["items"])

    def test_an_underscore_matches_a_literal_underscore(self, client, uploaded):
        rid = uploaded["request_id"]
        everything = client.get(f"/api/requests/{rid}/timeline").json()["filtered_count"]
        under = client.get(
            f"/api/requests/{rid}/timeline", params={"q": "_"}
        ).json()["filtered_count"]
        assert under < everything, "_ still behaves as a single-character wildcard"

    def test_ordinary_searches_are_unaffected(self, client, uploaded):
        rid = uploaded["request_id"]
        hits = client.get(
            f"/api/requests/{rid}/timeline", params={"q": "BANANA"}
        ).json()
        assert hits["filtered_count"] > 0
        detail = client.get(f"/api/transactions/{hits['baskets'][0]['id']}").json()
        assert any("BANANA" in i["description_raw"] for i in detail["items"])

    def test_a_backslash_does_not_break_the_query(self, client, uploaded):
        # The escape character itself has to survive being searched for.
        response = client.get(
            f"/api/requests/{uploaded['request_id']}/timeline", params={"q": "back\\slash"}
        )
        assert response.status_code == 200
        assert response.json()["filtered_count"] == 0


class TestStaticRouteTraversal:
    """The SPA catch-all serves any file under the static root and nothing above it.

    `spa()` resolves the candidate and checks `is_relative_to` the static root.
    Nothing asserted that until CodeQL flagged the route as `py/path-injection`
    (high) on three lines. The finding is a false positive — the taint tracker
    does not model `Path.is_relative_to` as a sanitizer — but it was true that a
    refactor could have dropped the guard and no test would have noticed, which
    is the same shape as the unverified safeguards this project has been bitten
    by before. So the guard is now asserted rather than argued about.

    `resolve()` is what makes the symlink case work: a link inside the static
    root pointing outside it resolves to its target, which is then not relative
    to the root. That is the case a string-prefix check would miss.
    """

    @pytest.fixture
    def served(self, tmp_path, monkeypatch):
        import os

        static = tmp_path / "static"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("INDEX")
        (static / "assets" / "app.js").write_text("APP_JS")
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP_SECRET")
        os.symlink(secret, static / "escape.txt")

        monkeypatch.setenv(api.STATIC_DIR_ENV, str(static))
        from fastapi import FastAPI

        app = FastAPI()
        assert api.mount_frontend(app), "the frontend did not mount"
        return TestClient(app)

    @pytest.mark.parametrize(
        "path",
        [
            "../secret.txt",
            "../../secret.txt",
            "../../../../../../etc/passwd",
            "/etc/passwd",
            "//etc/passwd",
            "/../secret.txt",
            "..%2fsecret.txt",
            "..%252fsecret.txt",
            ".%2e/secret.txt",
            "....//secret.txt",
            "assets/../../secret.txt",
            "escape.txt",  # a symlink inside the root, pointing out of it
        ],
    )
    def test_nothing_above_the_static_root_is_served(self, served, path):
        body = served.get(f"/{path}").text
        assert "TOP_SECRET" not in body
        assert "root:x:" not in body
        # A miss is not an error: an unknown path is a client-side route.
        assert body == "INDEX"

    def test_real_assets_are_still_served(self, served):
        assert served.get("/assets/app.js").text == "APP_JS"

    def test_an_unknown_path_returns_the_shell(self, served):
        assert served.get("/timeline").text == "INDEX"
