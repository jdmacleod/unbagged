"""Products, read from the synthetic Kroger and Albertsons responses through the API.

Kept apart from `test_api.py`, which is at its size limit. These assert what
the fixture the screenshots are taken from shows, so a change to the fixture or
to the product rules that hides a state from review fails here.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tools import make_fixtures

from unbagged import api, db

FIXTURE = (
    Path(__file__).parent.parent / "src/unbagged/adapters/kroger/fixtures/synthetic_report.txt"
)
ALBERTSONS = Path(__file__).parent.parent / "src/unbagged/adapters/albertsons/fixtures"


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


class TestOneNameTwoCodes:
    """Issue #98, from the fixture the screenshots are taken from."""

    def test_both_entries_are_marked_and_nothing_else_is(self, index):
        marked = [p for p in index["products"] if p["shares_name"]]
        assert {p["description"] for p in marked} == {"BANANAS EA"}
        assert len({p["upc"] for p in marked}) == 2


@pytest.fixture
def albertsons_index(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("UNBAGGED_INCOMING", str(tmp_path / "incoming"))
    workbook = make_fixtures.load(ALBERTSONS / "generate.py").build_workbook()
    report = (ALBERTSONS / "synthetic_report.txt").read_bytes()
    with TestClient(api.app) as client:
        created = client.post(
            "/api/requests",
            files=[
                ("files", ("report.txt", report, "text/plain")),
                ("files", ("details.xlsx", workbook, "application/octet-stream")),
            ],
            data={"declared_retailer": "albertsons"},
        )
        assert created.status_code == 201, created.text
        rid = created.json()["request_id"]
        yield client.get(f"/api/requests/{rid}/product-index").json()


class TestRegisterDeposits:
    """Issue #140. Albertsons rings a container deposit in register shorthand,
    and every one was listed as a product, one of them in the top size tier."""

    def test_every_deposit_the_generator_rings_is_set_aside(self, albertsons_index):
        generator = make_fixtures.load(ALBERTSONS / "generate.py")
        rung = {d[0] for d in generator.DEPOSITS} & {
            line[0] for b in generator.build()["baskets"] for line in b["lines"]
        }
        assert rung, "the fixture must ring at least one deposit"
        assert albertsons_index["set_aside"] == len(rung)

    def test_no_deposit_is_listed(self, albertsons_index):
        names = [p["description"] for p in albertsons_index["products"]]
        assert not [n for n in names if n.startswith(("CRV ", "DEPOSIT "))]
