from pathlib import Path

import pytest

from unbagged import db, ingest, repository
from unbagged.extraction import extract
from unbagged.ingest import IngestError, safe_filename, store_upload
from unbagged.models import SourceDocument

HMART_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "unbagged"
    / "adapters"
    / "hmart"
    / "fixtures"
    / "synthetic_history.xls"
)
#: Enough bytes to make a text file exist. Not a response, and deliberately not
#: from `tools/make_fixtures.py`: the tests below assert what a file IS — its
#: media type and page count — not what it says, and routing that through a
#: generated 400KB retailer report would make the assertion harder to read and
#: no more true. `LETTER` in test_generic_adapter.py is the one to reuse when a
#: test needs something that parses as a response.
TEXT_BYTES = b"A response, as text.\n"


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


@pytest.fixture
def conn(tmp_path):
    with db.open_db(tmp_path / "ingest.sqlite") as c:
        yield c


class TestWhatIsStoredAboutTheFileItself:
    """Issue #46: `media_type` and `page_count` were NULL for every document.

    Both columns existed in the schema, `repository.py` wrote and read them,
    `ExtractedDocument` carried them, and `test_extraction.py` asserted them at
    the extraction layer. They simply never travelled the last step: `ingest()`
    hardcoded `media_type=None` and never set `page_count` at all.

    Nothing rendered them, so nothing looked wrong — and the test factory
    supplied `application/pdf` and 48, so every test touching a stored document
    got values the real path could not produce. That is the fixture-fiction
    shape `test_layout.py`'s own docstring counts as having bitten this project
    four times, and the reason this asserts against the ingest path rather than
    against a factory.
    """

    def test_a_text_response_records_what_it_is(self, conn, tmp_path):
        stored = store_upload("response.txt", TEXT_BYTES, directory=tmp_path)
        result = ingest.ingest(conn, [stored])
        documents = repository.get_documents(conn, result.request_id)
        assert documents, "no document was stored"
        assert documents[0].media_type == "text/plain"
        assert documents[0].page_count == 1

    def test_a_spreadsheet_records_no_page_count_rather_than_a_wrong_one(self, conn, tmp_path):
        """A spreadsheet is sheets and rows. Zero pages would be a claim; null is not."""
        stored = store_upload("history.xls", HMART_FIXTURE.read_bytes(), directory=tmp_path)
        result = ingest.ingest(conn, [stored])
        documents = repository.get_documents(conn, result.request_id)
        assert documents[0].media_type == "application/vnd.ms-excel"
        assert documents[0].page_count is None

    def test_what_is_stored_is_what_extraction_would_say(self, conn, tmp_path):
        """The relationship, not two lists of expected values.

        The failure this guards is the two drifting: a document stored with one
        media type and read with another. Both go through `classify()`, and this
        is what proves it end to end rather than at the seam.
        """
        stored = store_upload("history.xls", HMART_FIXTURE.read_bytes(), directory=tmp_path)
        result = ingest.ingest(conn, [stored])
        document = repository.get_documents(conn, result.request_id)[0]

        as_read = extract(
            SourceDocument(
                original_filename=stored.original_filename,
                sha256=stored.sha256,
                path=str(stored.path),
            )
        )
        assert document.media_type == as_read.media_type
        assert document.page_count == (as_read.page_count or None)
