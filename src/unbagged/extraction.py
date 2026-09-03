"""Getting text out of whatever the retailer sent.

Adapters work on text, not on file formats. This module is the boundary: it turns
a stored document into pages of text and nothing more. It knows about PDFs and
plain text; it knows nothing about any retailer.

Page fidelity matters more than it looks. Provenance has to answer "which page of
this 48-page PDF", because that is the question of someone holding a printout, so
pages are kept separate rather than concatenated and forgotten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from unbagged.models import SourceDocument

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"
TEXT_SUFFIXES = {".txt", ".text", ".json", ".csv", ".md"}


class ExtractionError(Exception):
    """Raised when a document cannot be read at all. Distinct from a parse
    failure: nothing downstream can do anything useful with the bytes."""


@dataclass(frozen=True)
class ExtractedDocument:
    """Pages of text, plus enough identity to attach provenance to them."""

    pages: tuple[str, ...]
    filename: str
    media_type: str
    document_id: int | None = None

    @property
    def text(self) -> str:
        """Every page joined. Adapters that need page numbers use `page_starts`
        or, for formats that print their own page numbers, recover them from the
        text — that is what a reader of the printout actually sees."""
        return "\n".join(self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_starts(self) -> tuple[int, ...]:
        """Character offset in `text` at which each page begins."""
        starts, offset = [], 0
        for page in self.pages:
            starts.append(offset)
            offset += len(page) + 1   # the joining newline
        return tuple(starts)

    def page_of(self, offset: int) -> int:
        """1-based page containing a character offset in `text`."""
        page = 1
        for index, start in enumerate(self.page_starts(), start=1):
            if offset >= start:
                page = index
            else:
                break
        return page


def looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(PDF_MAGIC)) == PDF_MAGIC
    except OSError:
        return False


def extract_pdf(path: Path) -> tuple[str, ...]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ExtractionError(
            "Reading PDFs needs pdfplumber. Install the project with `pip install -e .`."
        ) from exc

    try:
        with pdfplumber.open(str(path)) as pdf:
            return tuple((page.extract_text() or "") for page in pdf.pages)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            f"Could not read {path.name} as a PDF. If it opens in a viewer, it may be "
            "a scan with no text layer, which this tool cannot read yet."
        ) from exc


def extract_text_file(path: Path) -> tuple[str, ...]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExtractionError(f"Could not read {path.name}: {exc}") from exc
    # A text file has no pages of its own. Reports that print their own page
    # numbers still carry them in the text, which is where adapters look.
    return (content,)


def extract(document: SourceDocument) -> ExtractedDocument:
    """Read one stored document into pages of text."""
    if not document.path:
        raise ExtractionError(f"{document.original_filename} has no stored path")
    path = Path(document.path)
    if not path.is_file():
        raise ExtractionError(f"{document.original_filename} is not on disk at {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf" or looks_like_pdf(path):
        pages = extract_pdf(path)
        media_type = "application/pdf"
    elif suffix in TEXT_SUFFIXES or document.media_type == "text/plain":
        pages = extract_text_file(path)
        media_type = document.media_type or "text/plain"
    else:
        raise ExtractionError(
            f"{document.original_filename} is a {suffix or 'typeless'} file. "
            "Supported inputs are PDF and text; unzip an archive first."
        )

    if not any(page.strip() for page in pages):
        raise ExtractionError(
            f"{document.original_filename} produced no text. A scanned PDF with no "
            "text layer looks like this; OCR is out of scope."
        )
    return ExtractedDocument(
        pages=pages,
        filename=document.original_filename,
        media_type=media_type,
        document_id=document.id,
    )


def extract_all(documents: tuple[SourceDocument, ...]) -> list[ExtractedDocument]:
    """Extract every document that can be read, skipping those that cannot.

    One unreadable attachment in a bundle of four must not lose the other three;
    the caller records the failures as parse warnings.
    """
    extracted = []
    for document in documents:
        try:
            extracted.append(extract(document))
        except ExtractionError:
            log.warning("could not extract %s", document.original_filename)
    return extracted
