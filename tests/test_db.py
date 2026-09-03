import sqlite3

import pytest

from unbagged import db

EXPECTED_TABLES = {
    "request", "source_document", "identity", "txn", "txn_item",
    "inference", "disclosure", "follow_up", "parse_warning",
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

    def test_migration_is_recorded(self, conn):
        rows = list(conn.execute("SELECT version, name FROM schema_migration"))
        assert [r["version"] for r in rows] == [1]
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
            conn.execute(
                "INSERT INTO txn (request_id, occurred_at) VALUES (9999, '2024-01-01')"
            )

    def test_deleting_a_request_cascades(self, conn):
        conn.execute(
            "INSERT INTO request (id, retailer_id, display_name) VALUES (1, 'k', 'K')"
        )
        conn.execute(
            "INSERT INTO txn (id, request_id, occurred_at) VALUES (1, 1, '2024-01-01')"
        )
        conn.execute(
            "INSERT INTO txn_item (txn_id, description_raw) VALUES (1, 'MILK')"
        )
        conn.execute("DELETE FROM request WHERE id = 1")
        assert conn.execute("SELECT count(*) c FROM txn").fetchone()["c"] == 0
        assert conn.execute("SELECT count(*) c FROM txn_item").fetchone()["c"] == 0


class TestConstraints:
    def test_a_document_cannot_be_ingested_twice(self, conn):
        conn.execute(
            "INSERT INTO request (id, retailer_id, display_name) VALUES (1, 'k', 'K')"
        )
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
