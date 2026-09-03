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

    def test_a_file_with_no_readable_text_is_refused(self, client):
        # Nothing to find a finding in. Distinct from a letter that says nothing.
        response = client.post(
            "/api/requests",
            files={"files": ("scan.pdf", b"%PDF-1.4\nnot really\n", "application/pdf")},
        )
        assert response.status_code == 400
        assert "adapter" in response.json()["detail"].lower()

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
        assert stats["total_spend"] > 0
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
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        detail = client.get(f"/api/transactions/{timeline['baskets'][0]['id']}").json()
        assert detail["items"]
        for item in detail["items"]:
            if item["retail_amt"] is not None:
                assert item["net_amt"] == round(
                    item["retail_amt"] - (item["loyalty_amt"] or 0), 2
                )

    def test_every_basket_is_traceable(self, client, uploaded):
        timeline = client.get(f"/api/requests/{uploaded['request_id']}/timeline").json()
        for basket in timeline["baskets"][:5]:
            assert basket["provenance"]["locator"]
            assert basket["provenance"]["page"] >= 1
            assert basket["provenance"]["source_document_id"]

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
        assert {"householdComposition", "incomePredictorScore"} <= labels

    def test_the_identity_graph_is_returned_with_provenance(self, client, uploaded):
        data = client.get(f"/api/requests/{uploaded['request_id']}/profile").json()
        assert data["identity_count"] > 5
        assert all(i["provenance"]["locator"] for i in data["identities"])

    def test_derivability_survives_as_three_states(self, client, uploaded):
        data = client.get(f"/api/requests/{uploaded['request_id']}/profile").json()
        appended = {
            i["label"]: i["derivable_from_txns"]
            for i in data["inferences_by_origin"]["appended_third_party"]
        }
        assert appended["petOwner"] is True
        assert appended["educationLevel"] is False
        assert appended["onlineShopperLikelihood"] is None


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
        with FIXTURE.open("rb") as fh:
            client.post("/api/requests", files={"files": ("second.txt", fh, "text/plain")})
        data = client.get("/api/compare").json()
        assert data["comparable"] is True
        assert len(data["requests"]) == 2
        for request in data["requests"]:
            assert request["identifier_count"] > 0
            assert request["appended_inference_count"] > 0
            assert request["absent_disclosures"] == 7


class TestPriceHistory:
    def test_products_seen_repeatedly_get_a_series(self, client, uploaded):
        data = client.get(
            f"/api/requests/{uploaded['request_id']}/price-history"
        ).json()
        assert data["product_count"] > 20
        product = data["products"][0]
        assert product["observations"] >= data["min_observations"]
        assert len(product["points"]) == product["observations"]
        assert product["first_seen"] <= product["last_seen"]

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
