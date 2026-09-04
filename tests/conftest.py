"""Shared test fixtures.

The guard in `_isolated_db` is not ceremony. `db.db_path()` falls back to
`data/db/unbagged.sqlite` whenever `UNBAGGED_DB` is unset, so a typo in the
environment variable name does not fail — it silently points the whole test at
the user's real database and ingests into it. That happened. The assert turns a
silent write to real data into a loud stop.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)


@pytest.fixture(scope="module")
def fixture_conn_module(tmp_path_factory) -> Iterator[tuple]:
    """The synthetic report, ingested once per module. Yields (conn, request_id).

    Module-scoped because ingesting parses a 12,000-line document, and the
    read-only view tests that want it do not mutate anything.
    """
    from unbagged import db

    root = tmp_path_factory.mktemp("fixture-db")
    previous = {k: os.environ.get(k) for k in (db.DB_PATH_ENV, "UNBAGGED_INCOMING")}
    os.environ[db.DB_PATH_ENV] = str(root / "test.sqlite")
    os.environ["UNBAGGED_INCOMING"] = str(root / "incoming")
    assert Path(db.db_path()) != Path(db.DEFAULT_DB_PATH), (
        "refusing to run against the real database"
    )

    try:
        from fastapi.testclient import TestClient

        from unbagged import api

        with TestClient(api.app) as client:
            with FIXTURE.open("rb") as handle:
                response = client.post(
                    "/api/requests",
                    files={"files": (FIXTURE.name, handle, "text/plain")},
                    data={"declared_retailer": "kroger"},
                )
            assert response.status_code == 201, response.text
            request_id = response.json()["request_id"]

            conn = db.connect()
            try:
                yield conn, request_id
            finally:
                conn.close()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
