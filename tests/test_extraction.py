from pathlib import Path

import pytest

from tests.minipdf import build_pdf
from unbagged import extraction
from unbagged.extraction import (
    ExtractedDocument,
    ExtractionError,
    extract,
    extract_all,
    looks_like_pdf,
    probe,
)
from unbagged.models import SourceDocument


def document(path, **kwargs) -> SourceDocument:
    return SourceDocument(original_filename=path.name, sha256="0" * 64, path=str(path), **kwargs)


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(
        build_pdf(
            [
                "Section 1: Specific Pieces of Personal Information Collected",
                "Data we hold related to our Loyalty program:",
                "Information about your purchases:",
            ]
        )
    )
    return path


class TestPdf:
    def test_pages_stay_separate(self, pdf_path):
        extracted = extract(document(pdf_path))
        assert extracted.page_count == 3
        assert "Loyalty program" in extracted.pages[1]
        assert extracted.media_type == "application/pdf"

    def test_offsets_map_back_to_page_numbers(self, pdf_path):
        # Provenance has to answer "which page of the printout", so the mapping
        # from an offset in the joined text back to a page has to survive.
        extracted = extract(document(pdf_path))
        offset = extracted.text.index("Loyalty program")
        assert extracted.page_of(offset) == 2
        assert extracted.page_of(0) == 1
        assert extracted.page_of(len(extracted.text) - 1) == 3

    def test_a_pdf_is_detected_by_content_not_extension(self, tmp_path, pdf_path):
        mislabelled = tmp_path / "report.txt"
        mislabelled.write_bytes(pdf_path.read_bytes())
        assert looks_like_pdf(mislabelled)
        assert extract(document(mislabelled)).page_count == 3

    def test_a_pdf_with_no_text_layer_says_so(self, tmp_path):
        path = tmp_path / "scan.pdf"
        path.write_bytes(build_pdf(["", ""]))
        with pytest.raises(ExtractionError, match="no text"):
            extract(document(path))

    def test_a_corrupt_pdf_gives_a_readable_message(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4\nthis is not a pdf\n")
        with pytest.raises(ExtractionError, match="Could not read"):
            extract(document(path))


class TestText:
    def test_a_text_file_is_one_page(self, tmp_path):
        path = tmp_path / "report.txt"
        path.write_text("Information about your purchases:\n{}\n")
        extracted = extract(document(path))
        assert extracted.page_count == 1
        assert extracted.page_of(10) == 1

    def test_encoding_problems_do_not_raise(self, tmp_path):
        path = tmp_path / "report.txt"
        path.write_bytes(b"Loyalty program: caf\xe9 purchases\n")
        assert "Loyalty program" in extract(document(path)).text


class TestFailures:
    def test_a_missing_file_says_which_one(self, tmp_path):
        with pytest.raises(ExtractionError, match="not on disk"):
            extract(document(tmp_path / "gone.txt"))

    def test_a_document_with_no_path_is_rejected(self):
        doc = SourceDocument(original_filename="report.pdf", sha256="0" * 64)
        with pytest.raises(ExtractionError, match="no stored path"):
            extract(doc)

    def test_an_unsupported_format_names_the_way_out(self, tmp_path):
        path = tmp_path / "bundle.zip"
        path.write_bytes(b"PK\x03\x04nope")
        with pytest.raises(ExtractionError, match="unzip"):
            extract(document(path))

    def test_an_empty_file_is_an_extraction_failure(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("   \n\n")
        with pytest.raises(ExtractionError):
            extract(document(path))


class TestExtractAll:
    def test_one_unreadable_document_does_not_lose_the_others(self, tmp_path, pdf_path):
        good = tmp_path / "letter.txt"
        good.write_text("Dear customer,\n")
        missing = tmp_path / "nowhere.txt"
        extracted = extract_all((document(pdf_path), document(missing), document(good)))
        assert [e.filename for e in extracted] == ["report.pdf", "letter.txt"]


class TestPageArithmetic:
    def test_page_starts_account_for_the_joining_newline(self):
        doc = ExtractedDocument(pages=("abc", "de", "f"), filename="x", media_type="text/plain")
        assert doc.text == "abc\nde\nf"
        assert doc.page_starts() == (0, 4, 7)
        assert [doc.page_of(i) for i in range(len(doc.text))] == [1, 1, 1, 1, 2, 2, 2, 3]


SS = "urn:schemas-microsoft-com:office:spreadsheet"


def _sheet(rows: str, name: str = "Workbook", attrs: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{SS}">'
        f'<ss:Worksheet ss:Name="{name}"><ss:Table {attrs}>{rows}'
        f"</ss:Table></ss:Worksheet></ss:Workbook>"
    )


def _row(*values, index: int | None = None) -> str:
    at = f' ss:Index="{index}"' if index else ""
    cells = ""
    for value in values:
        if isinstance(value, tuple):
            column, text = value
            cells += (
                f'<ss:Cell ss:Index="{column}"><ss:Data ss:Type="String">{text}</ss:Data></ss:Cell>'
            )
        else:
            cells += f'<ss:Cell><ss:Data ss:Type="String">{value}</ss:Data></ss:Cell>'
    return f"<ss:Row{at}>{cells}</ss:Row>"


class TestSpreadsheet:
    def test_cells_land_at_the_columns_they_declare(self):
        # The whole reason this reader exists rather than a positional one.
        table = extraction.read_tables(_sheet(_row("a", "b", (4, "d"), "e"))).tables[0]
        assert table.rows[0].cells == ("a", "b", None, "d", "e")

    def test_a_row_can_declare_its_own_number(self):
        # ss:Index on a Row skips empty rows above it, and the row number is
        # half of every locator this adapter emits.
        table = extraction.read_tables(_sheet(_row("a") + _row("z", index=5))).tables[0]
        assert [row.number for row in table.rows] == [1, 5]

    def test_a_locator_is_an_a1_reference_qualified_by_sheet(self):
        table = extraction.read_tables(_sheet(_row("a"), name="Sheet1")).tables[0]
        assert table.locator(14, 4) == "Sheet1!D14"
        assert table.locator(1, 27) == "Sheet1!AA1"

    def test_the_declared_size_is_kept_for_cross_checking(self):
        table = extraction.read_tables(
            _sheet(_row("a"), attrs='ss:ExpandedRowCount="69" ss:ExpandedColumnCount="5"')
        ).tables[0]
        assert (table.declared_rows, table.declared_columns) == (69, 5)

    def test_a_read_can_stop_after_a_number_of_rows(self):
        many = _sheet("".join(_row(f"v{i}") for i in range(500)))
        read = extraction.read_tables(many, max_rows=2)
        assert len(read.tables[0].rows) == 2
        assert read.tables[0].truncated

    def test_a_spent_budget_is_reported_rather_than_looking_like_an_empty_sheet(self):
        padded = _sheet(_row("a")).replace(
            "<ss:Worksheet", "<!-- " + "x" * 300_000 + " --><ss:Worksheet", 1
        )
        read = extraction.read_tables(padded, max_rows=2, budget_bytes=256 * 1024)
        assert read.spent_budget

    def test_a_document_type_declaration_is_refused(self):
        with pytest.raises(extraction.ExtractionError, match="document type"):
            extraction.read_tables('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><r/>')

    def test_a_truncated_document_is_caught_by_closing_the_parser(self):
        # Feeding a half document raises nothing: it is well-formed as far as it
        # goes. Only closing asks whether the tree finished.
        whole = _sheet(_row("a", "b"))
        with pytest.raises(extraction.ExtractionError, match="incomplete"):
            extraction.read_tables(whole[: len(whole) - 30])

    def test_the_source_may_have_no_newlines_at_all(self):
        # The shape a real export arrives in, and the one dimension the
        # committed fixture is deliberately unfaithful in.
        flat = _sheet(_row("a", "b")).replace("\n", "")
        assert extraction.read_tables(flat).tables[0].rows[0].cells == ("a", "b")


class TestTextProjection:
    def test_a_tabular_document_renders_its_sheets_as_text(self):
        # One representation. `text` is a property, so the generic fallback and
        # the unknown-format message keep working without knowing about tables.
        table = extraction.read_tables(_sheet(_row("a", "b") + _row("c", "d"))).tables[0]
        assert table.as_text() == "a\tb\nc\td"

    def test_a_tab_inside_a_cell_cannot_split_the_row(self):
        # Otherwise the projection stops matching the rows it came from.
        table = extraction.read_tables(_sheet(_row("a\tb", "c"))).tables[0]
        assert table.as_text() == "a b\tc"

    def test_the_projection_has_one_field_per_cell(self):
        rows = extraction.read_tables(_sheet(_row("a", "b", (4, "d")))).tables[0]
        assert rows.as_text().split("\t") == ["a", "b", "", "d"]


class TestProbeAgreesWithExtract:
    """The cheap read and the real one must describe the same file.

    `probe()` exists so `ingest()` can store what a document is without paying
    for its text. The moment the two disagree, a document is stored with one
    media type and read with another, and provenance starts pointing at a file
    that does not match its own record.

    Asserted as a relationship rather than as two lists of expected values,
    because expected values written from the same understanding as the code are
    exactly what issue #32 is about. Both callers route through `classify()`, and
    this is what proves it.
    """

    def _facts(self, path: Path):
        doc = SourceDocument(original_filename=path.name, sha256="a" * 64, path=str(path))
        return probe(doc), extract(doc)

    def test_they_agree_on_a_pdf(self, tmp_path):
        path = tmp_path / "report.pdf"
        path.write_bytes(build_pdf([f"Page {i}." for i in range(7)]))
        facts, extracted = self._facts(path)
        assert facts.media_type == extracted.media_type == "application/pdf"
        assert facts.page_count == extracted.page_count == 7

    def test_they_agree_on_a_text_file(self, tmp_path):
        path = tmp_path / "letter.txt"
        path.write_text("Dear customer,\n\nNothing here.\n", encoding="utf-8")
        facts, extracted = self._facts(path)
        assert facts.media_type == extracted.media_type
        assert facts.page_count == extracted.page_count

    def test_they_agree_on_every_committed_fixture(self):
        """The formats this project actually ships, rather than ones invented here."""
        adapters = Path(__file__).resolve().parent.parent / "src" / "unbagged" / "adapters"
        fixtures = [
            p
            for p in sorted(adapters.glob("*/fixtures/*"))
            if p.is_file() and p.suffix not in {".py", ".md"}
        ]
        assert fixtures, "no committed fixtures found; this test would prove nothing"
        for path in fixtures:
            facts, extracted = self._facts(path)
            assert facts is not None, f"probe could not read {path.name}"
            assert facts.media_type == extracted.media_type, path.name
            # A spreadsheet has no pages to count, and both say so their own way.
            expected = extracted.page_count or None
            assert facts.page_count == expected, path.name

    def test_a_file_it_cannot_place_is_metadata_missing_not_an_error(self, tmp_path):
        """Never raises: this is metadata, and the payload has its own error path."""
        path = tmp_path / "mystery.bin"
        path.write_bytes(b"\x00\x01\x02")
        doc = SourceDocument(original_filename=path.name, sha256="a" * 64, path=str(path))
        assert probe(doc) is None
