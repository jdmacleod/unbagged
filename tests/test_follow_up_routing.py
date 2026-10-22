"""Which follow-ups the draft letter carries, and the page saying so.

Found by /devex-review on 2026-10-22. Compliance listed every follow-up that is
not a restated missing category under one heading, "also worth asking": a
question the draft puts to the retailer beside a note that was only ever meant
for the reader. Nothing on the page said which the letter would ask.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tools import make_fixtures

from unbagged import api, db
from unbagged.letters import draft_follow_up
from unbagged.models import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    RequestMeta,
)

ADAPTERS = Path(__file__).parent.parent / "src" / "unbagged" / "adapters"
KROGER_REPORT = ADAPTERS / "kroger" / "fixtures" / "synthetic_report.txt"
ALBERTSONS = ADAPTERS / "albertsons" / "fixtures"

META = RequestMeta(retailer_id="kroger", display_name="Kroger", report_reference="SYNTH-1")
ALL_ABSENT = [Disclosure(c, DisclosureStatus.ABSENT) for c in DisclosureCategory]


@pytest.mark.parametrize("kind", list(FollowUpKind))
@pytest.mark.parametrize("question", [None, "Please tell me why."])
def test_in_letter_agrees_with_the_letter(kind, question):
    """Computed against the letter rather than restated: `in_letter` is true
    exactly when adding the follow-up changes the draft."""
    action = FollowUpAction(kind=kind, description="A note for the reader.", question=question)
    plain = draft_follow_up(META, ALL_ABSENT)["letter"]
    with_it = draft_follow_up(META, ALL_ABSENT, [action])["letter"]
    assert action.in_letter is (with_it != plain)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("UNBAGGED_INCOMING", str(tmp_path / "incoming"))
    with TestClient(api.app) as test_client:
        yield test_client


def upload(client, retailer, *files):
    response = client.post(
        "/api/requests", files=[("files", f) for f in files], data={"declared_retailer": retailer}
    )
    assert response.status_code == 201, response.text
    return client.get("/api/compliance").json()["rows"][0]["follow_ups"]


def test_kroger_offers_its_earlier_period_in_the_letter(client):
    follow_ups = upload(client, "kroger", ("report.txt", KROGER_REPORT.read_bytes(), "text/plain"))
    by_kind = {f["kind"]: f["in_letter"] for f in follow_ups}
    assert by_kind == {"supplemental_period": True, "missing_category": False}


def test_albertsons_questions_are_marked_as_asked(client):
    workbook = make_fixtures.load(ALBERTSONS / "generate.py").build_workbook()
    follow_ups = upload(
        client,
        "albertsons",
        ("report.txt", (ALBERTSONS / "synthetic_report.txt").read_bytes(), "text/plain"),
        ("details.xlsx", workbook, "application/octet-stream"),
    )
    clarifications = [f for f in follow_ups if f["kind"] == "clarification"]
    assert len(clarifications) == 3
    assert all(f["in_letter"] for f in clarifications)
