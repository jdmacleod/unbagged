"""The Products cap keeps the most-bought, not the start of the alphabet (#141).

Found by /devex-review on 2026-10-22: on a response past the cap, the index cut
by name dropped the end of the alphabet, and the stopped list with it. The API
takes `limit`, so the synthetic Kroger response is capped here well below its
size rather than generating a response past the real one.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from unbagged import api, db
from unbagged.index_cap import cap

FIXTURE = (
    Path(__file__).parent.parent / "src/unbagged/adapters/kroger/fixtures/synthetic_report.txt"
)


def product(name: str, purchases: int, stopped: bool = False) -> dict:
    return {"description": name, "key": name, "purchases": purchases, "stopped": stopped}


class TestCap:
    def test_the_least_bought_are_left_out_not_the_last_letters(self):
        products = [product("APPLES", 1), product("BREAD", 5), product("ZUCCHINI", 9)]
        capped = cap(products, 2)
        assert [p["description"] for p in capped["products"]] == ["BREAD", "ZUCCHINI"]
        assert capped["left_out"] == 1
        assert capped["left_out_max_purchases"] == 1

    def test_ties_at_the_cut_are_settled_by_name_so_the_page_is_stable(self):
        products = [product("C", 1), product("A", 1), product("B", 1)]
        assert [p["key"] for p in cap(products, 2)["products"]] == ["A", "B"]

    def test_under_the_cap_nothing_is_said(self):
        capped = cap([product("A", 1)], 5)
        assert capped["truncated"] is False
        assert capped["left_out"] == 0
        assert capped["left_out_max_purchases"] == 0

    def test_the_stopped_list_includes_what_the_cap_left_out(self):
        products = [product("A", 9), product("B", 2, stopped=True)]
        capped = cap(products, 1)
        assert [p["key"] for p in capped["products"]] == ["A"]
        assert [p["key"] for p in capped["stopped"]] == ["B"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("UNBAGGED_INCOMING", str(tmp_path / "incoming"))
    with TestClient(api.app) as test_client, FIXTURE.open("rb") as fh:
        created = test_client.post(
            "/api/requests",
            files={"files": ("synthetic_report.txt", fh, "text/plain")},
            data={"declared_retailer": "kroger"},
        )
        assert created.status_code == 201, created.text
        test_client.rid = created.json()["request_id"]
        yield test_client


def index(client, limit):
    return client.get(f"/api/requests/{client.rid}/product-index?limit={limit}").json()


class TestThroughTheApi:
    LIMIT = 50

    def test_nothing_kept_was_bought_less_than_anything_left_out(self, client):
        whole, capped = index(client, 5000), index(client, self.LIMIT)
        kept = {p["key"] for p in capped["products"]}
        dropped = [p for p in whole["products"] if p["key"] not in kept]
        assert len(kept) == self.LIMIT
        assert capped["left_out"] == len(dropped) == whole["product_count"] - self.LIMIT
        assert min(p["purchases"] for p in capped["products"]) >= max(
            p["purchases"] for p in dropped
        )
        assert capped["left_out_max_purchases"] == max(p["purchases"] for p in dropped)

    def test_the_capped_page_is_still_alphabetical(self, client):
        names = [(p["description"], p["key"]) for p in index(client, self.LIMIT)["products"]]
        assert names == sorted(names)

    def test_the_stopped_list_is_the_same_capped_or_not(self, client):
        whole, capped = index(client, 5000), index(client, self.LIMIT)
        assert capped["stopped"] == whole["stopped"]
        assert len(capped["stopped"]) == capped["stopped_count"]
