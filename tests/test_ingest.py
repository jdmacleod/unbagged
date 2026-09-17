import io
import zipfile
from pathlib import Path

import pytest

from unbagged import db, ingest, repository
from unbagged.extraction import extract
from unbagged.ingest import IngestError, safe_filename, store_upload, store_upload_many
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


class TestWhichDocumentARecordCites:
    """A bundle of more than one file, where the citation has to pick.

    `_save()` used to rewrite every row's `source_document_id` to the FIRST
    stored document, unconditionally. Every bundle the project had ever ingested
    held one file, where that is the right answer by coincidence, so the bug was
    invisible: the adapter's own per-document provenance was computed, written,
    and then overwritten a moment later.

    The shape that exposes it is a response arriving as a spreadsheet plus a
    folder of receipt captures — every line item would cite the spreadsheet,
    which is a citation pointing at a document that does not contain the value.

    Asserted through a real adapter rather than a stub. The generic fallback
    parses the LONGEST document in the bundle (`generic/adapter.py`), so making
    the second file the longer one asks the question directly: the answer is the
    first document's id if the bug is present and the second's if it is not.
    """

    def test_a_record_cites_the_document_it_was_read_from(self, conn, tmp_path):
        short = store_upload("short.txt", b"Dear customer,\n", directory=tmp_path)
        long = store_upload(
            "long.txt",
            b"Dear customer,\nWe hold the following categories.\n" * 40,
            directory=tmp_path,
        )
        result = ingest.ingest(conn, [short, long])

        documents = repository.get_documents(conn, result.request_id)
        assert len(documents) == 2
        cited = {
            row["source_document_id"]
            for row in conn.execute(
                "SELECT source_document_id FROM disclosure WHERE request_id = ?",
                (result.request_id,),
            )
        }
        # Not `!= documents[0].id`: that passes on a NULL too, and a citation
        # that points nowhere is the other way this fails.
        assert cited == {documents[1].id}

    def test_the_order_files_arrive_in_is_the_order_they_are_cited_by(self, conn, tmp_path):
        """The mapping is positional, so a reversal has to change the answer.

        Same two files, swapped. If index and id were being matched up by
        anything other than position — sorted by name, say, or by size — this
        pair would agree with the one above instead of disagreeing.
        """
        short = store_upload("short.txt", b"Dear customer,\n", directory=tmp_path)
        long = store_upload(
            "long.txt",
            b"Dear customer,\nWe hold the following categories.\n" * 40,
            directory=tmp_path,
        )
        result = ingest.ingest(conn, [long, short])

        documents = repository.get_documents(conn, result.request_id)
        cited = {
            row["source_document_id"]
            for row in conn.execute(
                "SELECT source_document_id FROM disclosure WHERE request_id = ?",
                (result.request_id,),
            )
        }
        assert cited == {documents[0].id}


def archive(members: dict[str, bytes], *, compress: bool = False) -> bytes:
    """A zip in memory. Stored by default so a member's bytes are its own.

    `compress=True` is for the one test that needs the compressed size to be
    much smaller than what it expands to, which is the whole shape of a
    decompression bomb.
    """
    buffer = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buffer, "w", compression=method) as handle:
        for name, content in members.items():
            handle.writestr(name, content)
    return buffer.getvalue()


class TestAResponseThatArrivesAsAZip:
    """One upload, many documents.

    A response can arrive as a folder of screen captures, a page or two per
    visit. The alternative to reading the archive is telling the reader to
    unpack it and select every file by hand — and missing one leaves the
    response short a visit with nothing on screen saying which.
    """

    def test_the_members_become_separate_files(self, tmp_path):
        stored = store_upload_many(
            "response.zip",
            archive({"a.png": b"first", "b.png": b"second"}),
            directory=tmp_path,
        )
        assert [f.original_filename for f in stored] == ["a.png", "b.png"]
        assert len({f.sha256 for f in stored}) == 2

    def test_a_file_that_is_not_an_archive_is_stored_as_one_file(self, tmp_path):
        (stored,) = store_upload_many("report.txt", b"hello", directory=tmp_path)
        assert stored.original_filename == "report.txt"

    def test_a_member_keeps_its_own_name_and_not_its_folder(self, tmp_path):
        """The capture reader takes a visit's date out of the filename the
        store's own export produced. A folder prefix is not part of that."""
        (stored,) = store_upload_many(
            "response.zip", archive({"captures/190304.png": b"x"}), directory=tmp_path
        )
        assert stored.original_filename == "190304.png"

    def test_archiver_noise_is_skipped_silently(self, tmp_path):
        """A macOS zip shadows every file with a `__MACOSX` resource fork.

        Reported rather than skipped, they would bury an upload's real warnings
        under a pile of notes about metadata nobody sent on purpose.
        """
        stored = store_upload_many(
            "response.zip",
            archive(
                {
                    "190304.png": b"real",
                    "__MACOSX/._190304.png": b"fork",
                    ".DS_Store": b"junk",
                }
            ),
            directory=tmp_path,
        )
        assert [f.original_filename for f in stored] == ["190304.png"]

    def test_an_empty_member_is_skipped_rather_than_refused(self, tmp_path):
        stored = store_upload_many(
            "response.zip", archive({"real.txt": b"x", "blank.txt": b""}), directory=tmp_path
        )
        assert [f.original_filename for f in stored] == ["real.txt"]


class TestWhatAnArchiveIsNotAllowedToDo:
    """Every guard bounds something the archive itself declares.

    An archive is a description of files written by whoever sent it, and none of
    it is true until it has been read. `extraction.py` refuses a DTD before
    parsing XML for the same class of reason.
    """

    def test_a_member_pointing_outside_the_archive_is_refused(self, tmp_path):
        with pytest.raises(IngestError, match="points outside"):
            store_upload_many("response.zip", archive({"../escape.txt": b"x"}), directory=tmp_path)

    def test_an_absolute_member_is_refused(self, tmp_path):
        with pytest.raises(IngestError, match="points outside"):
            store_upload_many("response.zip", archive({"/etc/passwd": b"x"}), directory=tmp_path)

    def test_nothing_is_written_when_one_member_is_refused(self, tmp_path):
        """Refused loudly and wholesale. A response should not contain one, and
        keeping the rest would hide that it did."""
        with pytest.raises(IngestError):
            store_upload_many(
                "response.zip",
                archive({"good.txt": b"x", "../escape.txt": b"y"}),
                directory=tmp_path,
            )
        assert list(tmp_path.iterdir()) == []

    def test_an_archive_inside_an_archive_is_refused_rather_than_recursed(self, tmp_path):
        with pytest.raises(IngestError, match="another archive"):
            store_upload_many(
                "outer.zip",
                archive({"inner.zip": archive({"a.txt": b"x"})}),
                directory=tmp_path,
            )

    def test_too_many_members_are_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest, "MAX_ARCHIVE_MEMBERS", 2)
        with pytest.raises(IngestError, match="more than 2 entries"):
            store_upload_many(
                "response.zip",
                archive({f"{n}.txt": b"x" for n in range(3)}),
                directory=tmp_path,
            )

    def test_entries_that_get_filtered_out_still_count(self, tmp_path, monkeypatch):
        """Counted before anything is filtered, not after.

        Counting only the members that survived meant an archive could carry
        hundreds of thousands of directory and metadata entries, never reach the
        cap, and be iterated in full anyway.
        """
        monkeypatch.setattr(ingest, "MAX_ARCHIVE_MEMBERS", 3)
        noise = {f"__MACOSX/._{n}": b"fork" for n in range(5)}
        with pytest.raises(IngestError, match="more than 3 entries"):
            store_upload_many(
                "response.zip", archive({"real.txt": b"x", **noise}), directory=tmp_path
            )

    def test_one_member_cannot_be_read_whole_before_it_is_measured(self, tmp_path):
        """The cap has to bound the allocation, not describe it afterwards.

        Reading the member and then measuring it defeats the limit it enforces:
        a single entry inside an archive well under the upload cap expands to
        gigabytes in memory before any comparison happens. Each member is read
        to one byte past what is left of the budget instead.
        """
        payload = archive({"big.txt": b"0" * 5_000_000}, compress=True)
        assert len(payload) < 50_000, "the compressed size must pass the upload cap"
        with pytest.raises(IngestError, match="expands to more than"):
            store_upload_many("bomb.zip", payload, directory=tmp_path, budget=4096)
        assert list(tmp_path.iterdir()) == []

    def test_what_it_expands_to_is_capped_not_what_was_sent(self, tmp_path):
        """The upload limit bounds the COMPRESSED bytes, which is the wrong end
        of a decompression bomb: a few hundred kilobytes of zeroes expands to
        gigabytes. This is the other end."""
        payload = archive({"big.txt": b"0" * 200_000}, compress=True)
        assert len(payload) < 1024, "the point is that the compressed size passes"
        with pytest.raises(IngestError, match="expands to more than"):
            store_upload_many("bomb.zip", payload, directory=tmp_path, budget=1024)

    def test_nothing_is_written_when_the_budget_is_blown(self, tmp_path):
        """Tested before the member is written, so a bomb is refused rather than
        stored and then complained about."""
        payload = archive({"big.txt": b"0" * 200_000}, compress=True)
        with pytest.raises(IngestError):
            store_upload_many("bomb.zip", payload, directory=tmp_path, budget=1024)
        assert list(tmp_path.iterdir()) == []

    def test_the_budget_is_what_a_caller_has_left_not_what_one_file_may_use(self, tmp_path):
        """A per-archive cap honoured ten times over is ten times the cap.

        `api.py` carries the remaining allowance across the upload loop for this
        reason; here it is the parameter that makes that possible.
        """
        payload = archive({"a.txt": b"0" * 4000}, compress=True)
        first = store_upload_many("one.zip", payload, directory=tmp_path, budget=8000)
        assert first
        spent = sum(f.size for f in first)
        with pytest.raises(IngestError, match="expands to more than"):
            store_upload_many(
                "two.zip",
                archive({"b.txt": b"1" * 4000}, compress=True),
                directory=tmp_path,
                budget=8000 - spent - 3999,
            )

    def test_an_archive_with_nothing_readable_says_so(self, tmp_path):
        with pytest.raises(IngestError, match="nothing readable"):
            store_upload_many(
                "response.zip", archive({"__MACOSX/._x": b"fork"}), directory=tmp_path
            )

    def test_a_zip_that_cannot_be_opened_says_so(self, tmp_path):
        with pytest.raises(IngestError, match="could not be opened"):
            store_upload_many("broken.zip", b"PK\x03\x04truncated", directory=tmp_path)

    def test_an_empty_archive_is_answered_as_an_archive(self, tmp_path):
        """It opens with `PK\\x05\\x06` and no local file header at all.

        Recognising only the header that carries a member sent this down the
        unsupported-format path, where the reader was told a `.zip` file is not
        supported — the one sentence about it that is untrue.
        """
        with pytest.raises(IngestError, match="nothing readable"):
            store_upload_many("empty.zip", archive({}), directory=tmp_path)

    def test_a_self_extracting_archive_is_not_unpacked(self, tmp_path):
        """A zip behind an executable stub is a program, and unpacking a program
        somebody mailed you is not a thing this offers to do. It is stored as an
        ordinary file and refused downstream by format."""
        stub = b"MZ\x90\x00" + b"\x00" * 60 + archive({"a.txt": b"x"})
        (stored,) = store_upload_many("setup.exe", stub, directory=tmp_path)
        assert stored.original_filename == "setup.exe"


class TestWhatAMemberIsCalledOnceItIsOut:
    """A name collision here loses a visit, which is what this feature prevents.

    `adapter._captures` maps captures by `original_filename` and keeps the last
    of any duplicate. Flattening two folders onto one set of names therefore
    discards a capture and the basket on it — and the adapter's warning tells
    the reader to rename the files, which nobody can act on when it was the
    unpacking that collided them.
    """

    def test_a_unique_basename_loses_its_folder(self, tmp_path):
        stored = store_upload_many(
            "response.zip",
            archive({"captures/sc_030419.png": b"a", "captures/sc_030519.png": b"b"}),
            directory=tmp_path,
        )
        assert [f.original_filename for f in stored] == ["sc_030419.png", "sc_030519.png"]

    def test_a_repeated_basename_keeps_every_path(self, tmp_path):
        stored = store_upload_many(
            "response.zip",
            archive({"visit-a/sc_030419.png": b"a", "visit-b/sc_030419.png": b"b"}),
            directory=tmp_path,
        )
        assert [f.original_filename for f in stored] == [
            "visit-a/sc_030419.png",
            "visit-b/sc_030419.png",
        ]

    def test_the_capture_reader_reads_a_path_exactly_as_it_reads_a_name(self):
        """Which is what makes keeping the path free of consequences.

        `receipt._stem` strips a leading path before matching, so the date and
        the visit key come out the same either way.
        """
        from unbagged.adapters.hmart import receipt as rc

        assert rc.capture_date("visit-a/sc_030419.png") == rc.capture_date("sc_030419.png")
        assert rc.capture_date("visit-a/sc_030419.png") == "2019-03-04"
        # Two captures of one visit, which is what they are when their stems
        # agree — and both survive to be read rather than one being dropped.
        (group,) = rc.group_by_visit(["visit-a/sc_030419.png", "visit-b/sc_030419.png"])
        assert len(group) == 2
