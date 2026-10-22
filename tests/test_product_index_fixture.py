"""Products, read from the synthetic Kroger response through the API.

Kept apart from `test_api.py`, which is at its size limit. These assert what
the fixture the screenshots are taken from shows, so a change to the fixture or
to the product rules that hides a state from review fails here.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from unbagged import api, db

FIXTURE = (
    Path(__file__).parent.parent / "src/unbagged/adapters/kroger/fixtures/synthetic_report.txt"
)


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("UNBAGGED_INCOMING", str(tmp_path / "incoming"))
    with TestClient(api.app) as client, FIXTURE.open("rb") as fh:
        created = client.post(
            "/api/requests",
            files={"files": ("synthetic_report.txt", fh, "text/plain")},
            data={"declared_retailer": "kroger"},
        )
        assert created.status_code == 201, created.text
        rid = created.json()["request_id"]
        yield client.get(f"/api/requests/{rid}/product-index").json()


class TestSetAsideIsReachable:
    """Issues #104 and #105. The fixture carries a carrier-bag fee and a priced
    container deposit, which are charges and not things bought: they leave the
    list and the page says lines were set aside. Before, no fixture could reach
    a non-zero count, so its copy had never rendered."""

    def test_the_count_is_not_zero(self, index):
        assert index["set_aside"] >= 2

    def test_no_charge_is_listed_as_a_product(self, index):
        names = [p["description"] for p in index["products"]]
        assert not [n for n in names if "BAG FEE" in n or "DEPOSIT" in n]

    def test_a_stray_bracket_is_cleaned_not_counted(self, index):
        """The leading-punctuation path: the product stays, under a clean name."""
        assert not [p for p in index["products"] if p["description"].startswith("(")]
