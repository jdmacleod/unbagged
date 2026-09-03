import pytest

from tests.minipdf import build_pdf
from unbagged.extraction import (
    ExtractedDocument,
    ExtractionError,
    extract,
    extract_all,
    looks_like_pdf,
)
from unbagged.models import SourceDocument


def document(path, **kwargs) -> SourceDocument:
    return SourceDocument(
        original_filename=path.name, sha256="0" * 64, path=str(path), **kwargs
    )


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(build_pdf([
        "Section 1: Specific Pieces of Personal Information Collected",
        "Data we hold related to our Loyalty program:",
        "Information about your purchases:",
    ]))
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
        extracted = extract_all(
            (document(pdf_path), document(missing), document(good))
        )
        assert [e.filename for e in extracted] == ["report.pdf", "letter.txt"]


class TestPageArithmetic:
    def test_page_starts_account_for_the_joining_newline(self):
        doc = ExtractedDocument(pages=("abc", "de", "f"), filename="x", media_type="text/plain")
        assert doc.text == "abc\nde\nf"
        assert doc.page_starts() == (0, 4, 7)
        assert [doc.page_of(i) for i in range(len(doc.text))] == [1, 1, 1, 1, 2, 2, 2, 3]
