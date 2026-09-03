import pytest

from unbagged import db, ingest
from unbagged.ingest import IngestError, safe_filename, store_upload


class TestSafeFilename:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("report.pdf", "report.pdf"),
            ("../../etc/passwd", "passwd"),
            ("/absolute/path/report.pdf", "report.pdf"),
            # Windows separators survive Path() on POSIX, so they are scrubbed too.
            ("..\\..\\win.ini", "win.ini"),
            ("my report (final).PDF", "my_report_final_.PDF"),
            ("", "upload"),
            ("...", "upload"),
        ],
    )
    def test_hostile_names_cannot_escape_the_incoming_directory(self, given, expected):
        assert safe_filename(given) == expected

    def test_long_names_are_truncated_but_keep_their_suffix(self):
        name = safe_filename("a" * 300 + ".pdf")
        assert len(name) <= 80
        assert name.endswith(".pdf")


class TestStoreUpload:
    def test_content_is_hashed_and_written(self, tmp_path):
        stored = store_upload("report.txt", b"hello", directory=tmp_path)
        assert stored.path.read_bytes() == b"hello"
        assert len(stored.sha256) == 64
        assert stored.original_filename == "report.txt"

    def test_the_same_bytes_land_in_the_same_place(self, tmp_path):
        first = store_upload("a.txt", b"same", directory=tmp_path)
        second = store_upload("a.txt", b"same", directory=tmp_path)
        assert first.path == second.path
        assert len(list(tmp_path.iterdir())) == 1

    def test_two_files_with_one_name_do_not_overwrite_each_other(self, tmp_path):
        first = store_upload("report.pdf", b"one", directory=tmp_path)
        second = store_upload("report.pdf", b"two", directory=tmp_path)
        assert first.path != second.path
        assert first.path.read_bytes() == b"one"

    def test_an_empty_file_is_refused(self, tmp_path):
        with pytest.raises(IngestError, match="empty"):
            store_upload("empty.pdf", b"", directory=tmp_path)


class TestIngest:
    def test_no_files_is_refused(self, tmp_path):
        with (
            db.open_db(tmp_path / "t.sqlite") as conn,
            pytest.raises(IngestError, match="No files"),
        ):
            ingest.ingest(conn, [])

    def test_the_incoming_directory_follows_the_environment(self, monkeypatch):
        monkeypatch.setenv(ingest.INCOMING_ENV, "/data/incoming")
        assert str(ingest.incoming_dir()) == "/data/incoming"

    def test_the_default_incoming_directory_is_gitignored(self, monkeypatch):
        monkeypatch.delenv(ingest.INCOMING_ENV, raising=False)
        assert ingest.incoming_dir().parts[0] == "data"
