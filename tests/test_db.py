import sqlite3

import pytest

from unbagged import db

EXPECTED_TABLES = {
    "request",
    "source_document",
    "identity",
    "txn",
    "txn_item",
    "inference",
    "disclosure",
    "follow_up",
    "parse_warning",
}


@pytest.fixture
def conn(tmp_path):
    with db.open_db(tmp_path / "test.sqlite") as c:
        yield c


def table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r["name"] for r in rows}


class TestMigrations:
    def test_schema_creates_cleanly(self, conn):
        assert table_names(conn) >= EXPECTED_TABLES

    def test_every_migration_is_recorded(self, conn):
        rows = list(conn.execute("SELECT version, name FROM schema_migration ORDER BY version"))
        # Asserting the shape rather than a fixed list, so adding a migration
        # does not mean editing this test.
        assert [r["version"] for r in rows] == [v for v, _ in db.available_migrations()]
        assert [r["version"] for r in rows] == list(range(1, len(rows) + 1))
        assert rows[0]["name"] == "001_initial.sql"

    def test_migrating_twice_is_a_no_op(self, tmp_path):
        path = tmp_path / "twice.sqlite"
        with db.open_db(path) as c:
            assert db.migrate(c) == []
        with db.open_db(path) as c:
            assert db.migrate(c) == []
            assert table_names(c) >= EXPECTED_TABLES

    def test_migration_filenames_are_validated(self, tmp_path, monkeypatch):
        # A stray .sql file must fail loudly rather than be applied in whatever
        # order the filesystem happens to return.
        assert all(v > 0 for v, _ in db.available_migrations())
        mdir = tmp_path / "migrations"
        mdir.mkdir()
        (mdir / "add-stuff.sql").write_text("SELECT 1;")
        monkeypatch.setattr(db, "MIGRATIONS_DIR", mdir)
        with pytest.raises(ValueError, match="NNN_lower_snake_case"):
            db.available_migrations()

    def test_a_failing_migration_leaves_no_partial_schema(self, tmp_path, monkeypatch):
        mdir = tmp_path / "migrations"
        mdir.mkdir()
        (mdir / "001_broken.sql").write_text(
            "CREATE TABLE good (id INTEGER PRIMARY KEY);\nCREATE TABLE bad (;\n"
        )
        monkeypatch.setattr(db, "MIGRATIONS_DIR", mdir)
        conn = db.connect(tmp_path / "broken.sqlite")
        try:
            with pytest.raises(sqlite3.OperationalError):
                db.migrate(conn)
            # Neither the half-built schema nor the migration record survives, so
            # the next run retries from scratch instead of skipping the missing half.
            assert "good" not in table_names(conn)
            assert db.applied_versions(conn) == set()
        finally:
            conn.close()


class TestPragmas:
    def test_foreign_keys_are_enforced(self, conn):
        # Off by default in SQLite, which would make every ON DELETE CASCADE a lie.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO txn (request_id, occurred_at) VALUES (9999, '2024-01-01')")

    def test_deleting_a_request_cascades(self, conn):
        conn.execute("INSERT INTO request (id, retailer_id, display_name) VALUES (1, 'k', 'K')")
        conn.execute("INSERT INTO txn (id, request_id, occurred_at) VALUES (1, 1, '2024-01-01')")
        conn.execute("INSERT INTO txn_item (txn_id, description_raw) VALUES (1, 'MILK')")
        conn.execute("DELETE FROM request WHERE id = 1")
        assert conn.execute("SELECT count(*) c FROM txn").fetchone()["c"] == 0
        assert conn.execute("SELECT count(*) c FROM txn_item").fetchone()["c"] == 0


class TestConstraints:
    def test_a_document_cannot_be_ingested_twice(self, conn):
        conn.execute("INSERT INTO request (id, retailer_id, display_name) VALUES (1, 'k', 'K')")
        conn.execute(
            "INSERT INTO source_document (request_id, original_filename, sha256) "
            "VALUES (1, 'report.pdf', 'abc123')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO source_document (request_id, original_filename, sha256) "
                "VALUES (1, 'report-copy.pdf', 'abc123')"
            )


class TestConfiguration:
    def test_db_path_follows_the_environment(self, monkeypatch):
        monkeypatch.setenv(db.DB_PATH_ENV, "/data/db/unbagged.sqlite")
        assert str(db.db_path()) == "/data/db/unbagged.sqlite"

    def test_db_path_defaults_under_the_gitignored_data_dir(self, monkeypatch):
        monkeypatch.delenv(db.DB_PATH_ENV, raising=False)
        assert db.db_path().parts[0] == "data"


# Every table that cascades off `request`. Named rather than discovered, because
# the point of the census below is to catch a rebuild that empties one of them,
# and a discovery query would happily find nothing and report success.
#
# `follow_up` was missing from this tuple when it was first written, which is the
# defect issue #32 names: a guard asserting something about a set the author typed
# out, rather than about that set's relationship to the schema. The census would
# have passed a rebuild that dropped every follow-up. Caught in review on #75.
# `test_the_census_covers_every_cascading_table` below now asserts the
# relationship, so the next table to gain a cascade cannot quietly go uncounted.
CASCADING_TABLES = (
    "source_document",
    "identity",
    "txn",
    "inference",
    "disclosure",
    "follow_up",
    "parse_warning",
)


def cascading_from_request(conn) -> set[str]:
    """Tables the schema says cascade when a request is deleted."""
    found = set()
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        table = row["name"]
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):  # noqa: S608
            if fk["table"] == "request" and fk["on_delete"] == "CASCADE":
                found.add(table)
    return found


def _populate(conn) -> dict[str, int]:
    """One row in every table that hangs off a request, and a census of them."""
    conn.execute("INSERT INTO request (retailer_id, display_name) VALUES ('kroger', 'Kroger')")
    conn.execute("INSERT INTO request (retailer_id, display_name) VALUES ('hmart', 'H Mart')")
    conn.execute("INSERT INTO source_document (request_id, sha256) VALUES (1, ?)", ("a" * 64,))
    conn.execute(
        "INSERT INTO identity (request_id, id_type, value) VALUES (1, 'email', 'x@example.com')"
    )
    conn.execute("INSERT INTO txn (request_id, occurred_at) VALUES (1, '2024-01-01T00:00:00')")
    conn.execute("INSERT INTO txn_item (txn_id, description_raw) VALUES (1, 'BANANAS')")
    conn.execute(
        "INSERT INTO inference (request_id, label, value_raw, origin)"
        " VALUES (1, 'score', '0.5', 'unknown')"
    )
    conn.execute(
        "INSERT INTO disclosure (request_id, category, status) VALUES (1, 'SOURCES', 'absent')"
    )
    conn.execute(
        "INSERT INTO follow_up (request_id, kind, description)"
        " VALUES (1, 'supplemental_period', 'ask again for the earlier window')"
    )
    conn.execute(
        "INSERT INTO parse_warning (request_id, severity, message) VALUES (1, 'warning', 'msg')"
    )
    return _census(conn)


def _census(conn) -> dict[str, int]:
    tables = ("request", "txn_item", *CASCADING_TABLES)
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608


class TestRequestIdsAreNeverReused:
    """Issues #54 and #64, and the migration that closes both.

    `id INTEGER PRIMARY KEY` is a rowid alias, and SQLite hands the highest one
    out again after a delete. Two surfaces were affected and each had been
    guarded separately — the upload panel by comparing id AND retailer, the URL
    not at all. Guarding surfaces one at a time is what produced two issues from
    one cause, so the id is monotonic now and no consumer has to check.
    """

    def test_the_census_covers_every_cascading_table(self, conn):
        """The list and the schema must agree, or the census proves nothing.

        Written because the list did NOT agree: `follow_up` was missing, so a
        rebuild that dropped every follow-up would have passed the preservation
        test below. Asserting the relationship rather than the membership is the
        difference issue #32 is about.
        """
        assert set(CASCADING_TABLES) == cascading_from_request(conn)

    def test_a_deleted_id_is_not_handed_out_again(self, conn):
        conn.execute("INSERT INTO request (retailer_id, display_name) VALUES ('kroger', 'Kroger')")
        conn.execute("INSERT INTO request (retailer_id, display_name) VALUES ('hmart', 'H Mart')")
        conn.execute("DELETE FROM request WHERE id = 2")
        reused = conn.execute(
            "INSERT INTO request (retailer_id, display_name) VALUES ('kroger', 'Kroger')"
        ).lastrowid
        assert reused == 3, "the id of the deleted response must not come back"

    def test_the_schema_says_so_rather_than_relying_on_observed_behaviour(self, conn):
        # A rowid alias only reuses the HIGHEST id, so a test that deletes a
        # middle row passes with or without the fix. Assert the declaration too.
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'request'").fetchone()[0]
        assert "AUTOINCREMENT" in sql

    def test_the_rebuild_keeps_every_row_in_an_existing_database(self, tmp_path, monkeypatch):
        """The upgrade path, on a database that already holds a response.

        This is the test that matters, and it has to stop at 002 and then move:
        re-running `migrate()` on an already-migrated database is a no-op and
        would pass without exercising anything.

        `DROP TABLE request` with foreign keys ON is not inert — it performs an
        implicit DELETE FROM, which fires all seven ON DELETE CASCADEs. Measured
        on a scratch copy before the runner was changed: three transactions in,
        zero out, and the migration reporting success.
        """
        every = db.available_migrations()
        assert [v for v, _ in every][-1] == 3, "this test pins itself to 003"

        # Patched BEFORE the database is opened: `open_db` migrates on the way in,
        # so a rewind applied afterwards would find the fix already there.
        monkeypatch.setattr(
            db, "available_migrations", lambda: [(v, p) for v, p in every if v <= 2]
        )
        with db.open_db(tmp_path / "upgrade.sqlite") as conn:
            assert (
                "AUTOINCREMENT"
                not in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'request'"
                ).fetchone()[0]
            ), "the rewind must land BEFORE the fix, or this proves nothing"

            before = _populate(conn)
            assert all(n > 0 for n in before.values()), "the census must have something to lose"

            monkeypatch.setattr(db, "available_migrations", lambda: every)
            assert db.migrate(conn) == [3]

            assert _census(conn) == before, "the rebuild dropped rows"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert (
                "AUTOINCREMENT"
                in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'request'").fetchone()[
                    0
                ]
            )
            # And the ids that already existed still name the same responses, so
            # a bookmarked URL keeps working across the upgrade.
            surviving = [
                tuple(r) for r in conn.execute("SELECT id, retailer_id FROM request ORDER BY id")
            ]
            assert surviving == [(1, "kroger"), (2, "hmart")]

    def test_cascade_still_deletes_children_after_the_rebuild(self, conn):
        """The rebuild must not cost the behaviour foreign keys were on for."""
        _populate(conn)
        conn.execute("DELETE FROM request WHERE id = 1")
        for table in CASCADING_TABLES:
            remaining = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE request_id = 1"  # noqa: S608
            ).fetchone()[0]
            assert remaining == 0, f"{table} kept rows for a deleted request"

    def test_the_database_carries_no_dangling_references(self, conn):
        _populate(conn)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
