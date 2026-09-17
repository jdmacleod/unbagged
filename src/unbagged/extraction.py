"""Getting text — and now tables — out of whatever the retailer sent.

Adapters work on what this module hands them, not on file formats. This is the
boundary: it turns a stored document into pages of text, or into sheets of
placed cells, and nothing more. It knows about PDFs, plain text and
SpreadsheetML; it knows nothing about any retailer.

Page fidelity matters more than it looks. Provenance has to answer "which page of
this 48-page PDF", because that is the question of someone holding a printout, so
pages are kept separate rather than concatenated and forgotten.

A spreadsheet has no pages, so it answers the same question with a cell
reference instead. `docs/writing-an-adapter.md` has always promised adapters "a
CSV cell reference" as a locator; `Table.locator()` is where that promise is
finally kept.

**One representation, not two.** A tabular document carries `tables` and an
empty `pages`; `text` renders the sheets on demand. The alternative — storing a
tab-separated copy alongside the rows so existing callers need no change — was
built and then withdrawn: it bought four call sites at the price of two copies
that can drift, a test to prove they have not, and a peak of 111.9 MB against
16.8 MB on a 66.8 MB document. `text` is already a property, so nothing calling
it had to change anyway.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Container
from dataclasses import dataclass
from pathlib import Path

from unbagged.models import SourceDocument

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"

#: How a zip can begin. `PK\x03\x04` is a local file header and is what an
#: archive with anything in it starts with; `PK\x05\x06` is an end-of-central-
#: directory record, which is the whole of an EMPTY archive; `PK\x07\x08` marks
#: one spanned across volumes. All three are recognised so that an archive is
#: answered as an archive rather than falling through to "this is a .zip file
#: and is not supported", which is the one sentence that would be untrue.
#:
#: Recognition is by these records only, so a self-extracting archive — a zip
#: with an executable stub in front of it — is NOT one of these. That is
#: deliberate: it is a program, and unpacking a program somebody mailed you is
#: not a thing this should offer to do.
#:
#: Being zip-shaped is the point and also the difficulty: it is the container
#: under `.xlsx`, `.docx` and `.odt` too. Routing on it alone would claim all of
#: them, so `classify` tests it after the formats that live inside a zip have
#: had their say.
ZIP_MAGICS: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

#: The one that carries a member. `ingest` expands only these, because the other
#: two hold nothing to expand.
ZIP_MAGIC = ZIP_MAGICS[0]

TEXT_SUFFIXES = {".txt", ".text", ".json", ".csv", ".md"}

#: Image formats, by their magic bytes, mapped to the media type to store.
#:
#: Routed by content like everything else here, and for a sharper reason than
#: usual: a screen capture reaches this tool through whatever the operating
#: system's screenshot key produced and whatever the mail client renamed it to,
#: so the suffix is the least reliable thing about it. JPEG's magic is the first
#: three bytes only — the fourth varies by marker — and WebP's sits after a
#: four-byte length, so both are matched at an offset rather than as a prefix.
IMAGE_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (8, b"WEBP", "image/webp"),
)

#: Enough to cover every offset in IMAGE_MAGIC.
IMAGE_MAGIC_BYTES = 16

#: The SpreadsheetML 2003 namespace. A workbook saved by Excel as "XML
#: Spreadsheet 2003", and what at least one retailer's export servlet emits
#: under an `.xls` extension.
SS = "urn:schemas-microsoft-com:office:spreadsheet"

#: Fed to the pull parser one piece at a time. Nothing turns on the size; it is
#: large enough that the loop is not the cost and small enough to stop early.
CHUNK_BYTES = 64 * 1024

#: How far a bounded read will look before giving up.
#:
#: Deciding which adapter owns an upload must not cost a full parse of every
#: candidate, so that read is bounded — but the header row is the only thing
#: distinguishing one spreadsheet export from another, and SpreadsheetML puts
#: DocumentProperties and Styles in front of the worksheet with no stated size
#: limit. A prefix might not reach the header at all.
#:
#: 256 KB is roughly seven times an entire observed export, so it reaches the
#: header on anything shaped like one while still refusing a document whose
#: preamble is pathological. Measured: a bounded read of a 66.8 MB document
#: costs 2.4 ms against 3.7 s for a full parse.
SNIFF_BUDGET_BYTES = 256 * 1024

#: A DOCTYPE must precede the root element, so the prolog is the whole search.
#: Deliberately not a scan of the entire document: that would undo the bounded
#: read it sits next to.
PROLOG_BYTES = 4096

_DOCTYPE = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_XML_PROLOG = re.compile(r"^\s*<\?xml\b", re.IGNORECASE)


class ExtractionError(Exception):
    """Raised when a document cannot be read at all. Distinct from a parse
    failure: nothing downstream can do anything useful with the bytes."""


class NotTextBearing(ExtractionError):
    """A file this layer is not the reader for, rather than one that is broken.

    An image is the case: the transcription tier reads those, so the exception
    is the routing working. A subclass rather than a separate type, so every
    existing `except ExtractionError` still catches it — and carried distinctly
    so `extract_all` can tell a routing decision from a failure without opening
    the file a second time to re-derive what `classify` already decided.
    """


@dataclass(frozen=True)
class ExtractedDocument:
    """Pages of text, plus enough identity to attach provenance to them."""

    pages: tuple[str, ...]
    filename: str
    media_type: str
    document_id: int | None = None
    #: Sheets, for a tabular document. Defaulted, so nothing that constructs an
    #: ExtractedDocument for a PDF or a text file had to change.
    tables: tuple[Table, ...] = ()
    #: The read stopped on its byte budget rather than at the end of the file.
    spent_budget: bool = False

    @property
    def text(self) -> str:
        """Every page joined, or the sheets rendered, for a tabular document.

        Adapters that need page numbers use `page_starts` or, for formats that
        print their own page numbers, recover them from the text — that is what
        a reader of the printout actually sees.

        A tabular document has no pages and renders its sheets here on demand.
        That keeps one representation rather than storing a second copy beside
        the rows, and every existing caller — the generic fallback's keyword
        matching, the unknown-format message — keeps working untouched because
        this was already a property."""
        if self.pages:
            return "\n".join(self.pages)
        return "\n\n".join(table.as_text() for table in self.tables)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_starts(self) -> tuple[int, ...]:
        """Character offset in `text` at which each page begins."""
        starts, offset = [], 0
        for page in self.pages:
            starts.append(offset)
            offset += len(page) + 1  # the joining newline
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


def column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA. The column half of an A1 reference."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


@dataclass(frozen=True)
class Row:
    """One row of a sheet, with every cell at the column it declares.

    `cells` is positional and may contain `None` for a column the row skipped.
    That is not a formatting detail: SpreadsheetML omits empty cells entirely
    and marks the next present one with `ss:Index` naming its real column, so a
    reader that appends cells in encounter order shifts every value after a gap
    one column to the left. Against a dense header row that produces no error
    and no warning, just the wrong field — measured on a constructed row, an
    amount landing in the store column and a basket total dropping to its
    rounded points value.

    `number` is the row's own 1-based number, which is also `ss:Index`-aware:
    a row element may declare its position and skip the empty rows above it.
    """

    cells: tuple[str | None, ...]
    number: int

    def __len__(self) -> int:
        return len(self.cells)

    def value(self, column: int) -> str | None:
        """The value at a 1-based column, or None if the row does not reach it."""
        if 1 <= column <= len(self.cells):
            return self.cells[column - 1]
        return None


@dataclass(frozen=True)
class Table:
    """One worksheet: its name, its rows, and what it said about its own size."""

    name: str
    rows: tuple[Row, ...] = ()
    #: What `ss:Table` declared. Worth keeping because it is a free cross-check
    #: on a reader that could otherwise lose rows silently.
    declared_rows: int | None = None
    declared_columns: int | None = None
    #: True when the read stopped on a bound rather than at the end of the sheet.
    truncated: bool = False

    def locator(self, row: int, column: int) -> str:
        """An A1 cell reference, qualified by sheet: `Workbook!D14`.

        A1 rather than R1C1 because a locator is whatever lets someone find the
        value again, and A1 is what they see when they open the file.
        """
        return f"{self.name}!{column_letter(column)}{row}"

    def as_text(self) -> str:
        """The sheet as tab-separated lines.

        Rendered on demand for callers that want text — keyword matching, the
        unknown-format message — and never stored, so there is no second copy to
        drift from the rows. Tabs and newlines inside a cell collapse to a
        space: a cell carrying either would otherwise split its row into the
        wrong number of columns. The projection is lossy by design and is not a
        data source; `rows` is.
        """
        return "\n".join("\t".join(_flatten(cell) for cell in row.cells) for row in self.rows)


def _flatten(cell: str | None) -> str:
    return "" if cell is None else re.sub(r"[\t\r\n]+", " ", cell)


@dataclass(frozen=True)
class TableRead:
    """What a bounded read got, and whether it ran out before it was done."""

    tables: tuple[Table, ...] = ()
    #: The read stopped because it spent its byte budget, not because it
    #: finished. Distinct from finding nothing: "I did not look far enough" and
    #: "this is not that format" are different answers and a caller that
    #: reports them identically is lying to whoever uploaded the file.
    spent_budget: bool = False


def looks_like_spreadsheetml(text: str) -> bool:
    """Cheap enough for a routing decision: an XML prolog and the namespace."""
    head = text[:PROLOG_BYTES]
    return bool(_XML_PROLOG.match(head)) and SS in head


def _refuse_doctype(text: str) -> None:
    """Refuse a document type declaration before the parser ever sees one.

    Measured on this project's Python 3.12: external entities are already
    refused by the parser, but internal entity expansion is not — a few lines of
    declaration expand to gigabytes and take the container with them. A retailer
    export carries no DTD, so this cannot refuse a real file.

    Only the prolog is scanned, because a DOCTYPE must precede the root element
    and scanning the whole document would undo the bounded read beside it.
    """
    if _DOCTYPE.search(text[:PROLOG_BYTES]):
        raise ExtractionError(
            "This file carries a document type declaration, which a retailer "
            "export does not. It has not been read."
        )


def read_tables(
    source: str,
    *,
    max_rows: int | None = None,
    budget_bytes: int | None = None,
) -> TableRead:
    """Read SpreadsheetML into placed cells, optionally stopping early.

    Bounded two ways, because one is not enough: `max_rows` stops once the
    caller has seen what it needs, and `budget_bytes` stops a document whose
    preamble never gets to a row at all.

    Every row is cleared as it is consumed. Without that the pull parser retains
    the whole tree and peaks at 594 MB on a 66.8 MB document against 661 MB for
    parsing it outright — a 10% saving from a technique that is supposed to be
    bounded. Cleared, the same read peaks at 16.8 MB. Note the consequence for
    callers: a cleared tree cannot be walked again, so anything that needs a
    cell must take it during the pass.

    Rows are read on `end` events. A `start` event guarantees only that `>` has
    been seen, so cell text is not there yet; the worksheet's name, which is an
    attribute, is taken on `start` because by the time its `end` arrives every
    row it should have labelled has already gone past.
    """
    _refuse_doctype(source)

    parser = ET.XMLPullParser(events=("start", "end"))
    tables: list[Table] = []
    name = ""
    rows: list[Row] = []
    row_number = 0
    declared_rows: int | None = None
    declared_columns: int | None = None
    in_sheet = False
    spent_budget = False
    consumed = 0
    #: Columns held by an open `ss:MergeDown`, as {column: last row covered}.
    #: Numbers rather than elements: every row is cleared as it is consumed, so
    #: nothing that outlives a row may be a reference into the tree.
    spans: dict[int, int] = {}

    def flush() -> None:
        nonlocal rows, in_sheet, declared_rows, declared_columns
        if in_sheet:
            tables.append(
                Table(
                    name=name,
                    rows=tuple(rows),
                    declared_rows=declared_rows,
                    declared_columns=declared_columns,
                    truncated=stopping,
                )
            )
        rows, in_sheet = [], False
        declared_rows = declared_columns = None

    stopping = False
    try:
        for start in range(0, len(source), CHUNK_BYTES):
            chunk = source[start : start + CHUNK_BYTES]
            parser.feed(chunk)
            consumed += len(chunk)
            for event, element in parser.read_events():
                tag = element.tag.rpartition("}")[2]
                if event == "start":
                    if tag == "Worksheet":
                        flush()
                        in_sheet = True
                        name = element.get(f"{{{SS}}}Name") or ""
                        row_number = 0
                        # A span cannot reach out of the sheet it was written in.
                        spans = {}
                    elif tag == "Table":
                        declared_rows = _as_int(element.get(f"{{{SS}}}ExpandedRowCount"))
                        declared_columns = _as_int(element.get(f"{{{SS}}}ExpandedColumnCount"))
                elif event == "end":
                    if tag == "Row":
                        row_number = _as_int(element.get(f"{{{SS}}}Index")) or (row_number + 1)
                        # Dropped as they expire, which also makes `spans` the
                        # set of columns blocked in THIS row. Keyed on the last
                        # row a span covers rather than a countdown, because
                        # `ss:Index` on a Row skips rows and a countdown would
                        # hold a span open across the gap.
                        spans = {
                            column: last for column, last in spans.items() if last >= row_number
                        }
                        cells, opened = _cells_of(element, spans.keys())
                        for column, down in opened.items():
                            spans[column] = row_number + down
                        rows.append(Row(cells=cells, number=row_number))
                        element.clear()
                        if max_rows is not None and len(rows) >= max_rows:
                            stopping = True
                            break
                    elif tag == "Worksheet":
                        element.clear()
            if stopping:
                break
            if (
                budget_bytes is not None
                and consumed >= budget_bytes
                and (max_rows is None or len(rows) < max_rows)
                and start + CHUNK_BYTES < len(source)
            ):
                spent_budget = True
                stopping = True
                break
        if not stopping:
            # A truncated document is well-formed right up to where it stops, so
            # feed() never complains about it. close() is what asks whether the
            # tree actually finished, and it is the only thing that catches a
            # download that ended half way through. Skipped when the read
            # stopped on a bound, where an unfinished tree is the intent.
            parser.close()
    except ET.ParseError as exc:
        raise ExtractionError(_malformed_message(source, exc)) from exc

    flush()
    return TableRead(tables=tuple(tables), spent_budget=spent_budget)


def _malformed_message(source: str, exc: ET.ParseError) -> str:
    """Two different problems wear the same exception, and the fixes differ."""
    if not source.rstrip().endswith(">"):
        return "This file is incomplete; it ends part way through. Download it again and re-upload."
    return f"This file is not readable as a spreadsheet ({exc})."


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _cells_of(
    row: ET.Element, blocked: Container[int] = frozenset()
) -> tuple[tuple[str | None, ...], dict[int, int]]:
    """Place a row's cells at the columns they declare, filling the gaps.

    Three things move a cell away from its position in encounter order, and
    ignoring any of them shifts every value after it one or more columns left.

    `ss:Index` names a cell's real column, because SpreadsheetML omits an empty
    cell entirely rather than writing a blank one. It is written against real
    columns, so it overrides all of the arithmetic below rather than adding to
    it.

    `ss:MergeAcross` names how many further columns this cell occupies. The
    columns it swallows are not written either, so the next cell along is the
    one after the span, not the one after this element — a cell with
    `ss:MergeAcross="1"` sits in columns 1 and 2, and the element following it
    is column 3. The observed H Mart export merges its banner cell across four
    columns, so this is a shape the format really does arrive in.

    `blocked` is the same omission arriving from a PREVIOUS row. A cell carrying
    `ss:MergeDown` occupies its column in the rows beneath it, and those
    positions are left out of their rows exactly as a merged-across column is
    left out of its own — so a row under a span has one fewer element than it
    has columns, and every cell after the span lands to the left of where it
    belongs. The caller carries the open spans, because a row cannot see them.

    Against a dense header row none of this raises anything: the values simply
    land one field to the left. Measured on a constructed row, a merged `Branch`
    moved the points value into the amount column, so a $12.34 basket recorded
    as $12.00 with no error anywhere.

    Returns the placed cells and any spans this row OPENS, as
    `{column: rows below}`. A merged region carries its value once, in its
    top-left cell; every other position in it is empty, which is what the
    swallowed columns already read as.

    Cells carry their text in an `ss:Data` child rather than directly, and that
    child may itself hold markup — the banner cell's `ss:Data` wraps its text in
    `html:B` and `html:U` — so the text is gathered from the whole subtree.
    """
    placed: list[str | None] = []
    opened: dict[int, int] = {}
    column = 0
    for cell in row:
        if cell.tag.rpartition("}")[2] != "Cell":
            continue
        index = _as_int(cell.get(f"{{{SS}}}Index"))
        if index:
            column = index
        else:
            column += 1
            # A column held by a span from above is not written in this row, so
            # the implicit "next column" steps over it. Without this the cell
            # lands in the span's column and everything after it follows.
            while column in blocked:
                column += 1
        while len(placed) < column - 1:
            placed.append(None)
        data = cell.find(f"{{{SS}}}Data")
        placed.append(None if data is None else "".join(data.itertext()))
        # Clamped at zero: a negative span would walk the next cell backwards
        # over one already placed, which is worse than ignoring the attribute.
        across = max(0, _as_int(cell.get(f"{{{SS}}}MergeAcross")) or 0)
        down = max(0, _as_int(cell.get(f"{{{SS}}}MergeDown")) or 0)
        if down:
            # A region merged both ways blocks every column it spans, not just
            # the one its value sits in.
            for held in range(column, column + across + 1):
                opened[held] = down
        column += across
    return tuple(placed), opened


def looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(PDF_MAGIC)) == PDF_MAGIC
    except OSError:
        return False


def looks_like_zip(path: Path) -> bool:
    """Magic bytes only. Says nothing about whether the archive is readable.

    An empty archive and a truncated one both fail later, with their own
    messages — this answers "is this shaped like a zip", which is the routing
    question.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    return head in ZIP_MAGICS


def looks_like_image(path: Path) -> str | None:
    """The image media type of this file, or None if it is not one.

    Returns the type rather than a bool so `classify()` and `probe()` cannot
    disagree about WHICH image it is, the same way they cannot disagree about
    whether a `.xls` is the XML kind.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(IMAGE_MAGIC_BYTES)
    except OSError:
        return None
    for offset, magic, media_type in IMAGE_MAGIC:
        if head[offset : offset + len(magic)] == magic:
            return media_type
    return None


def extract_pdf(path: Path, max_pages: int | None = None) -> tuple[str, ...]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ExtractionError(
            "Reading PDFs needs pdfplumber. Install the project with `pip install -e .`."
        ) from exc

    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
            return tuple((page.extract_text() or "") for page in pages)
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


def extract_spreadsheet(path: Path, max_rows: int | None = None) -> tuple[tuple[Table, ...], bool]:
    """Read a SpreadsheetML file into sheets."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExtractionError(f"Could not read {path.name}: {exc}") from exc
    read = read_tables(
        source,
        max_rows=max_rows,
        budget_bytes=SNIFF_BUDGET_BYTES if max_rows is not None else None,
    )
    return read.tables, read.spent_budget


@dataclass(frozen=True)
class DocumentFacts:
    """What a document is, without reading what it says.

    `page_count` is None where the format has no pages to count — a spreadsheet
    is sheets and rows — which is the same distinction `ExtractedDocument`
    already draws by leaving `pages` empty.
    """

    media_type: str
    page_count: int | None = None


def classify(document: SourceDocument, path: Path) -> str:
    """Which reader owns this file. One definition, two callers.

    `extract()` and `probe()` must never disagree about what a file IS, or a
    document gets stored with one media type and read with another. Routing is
    by content rather than by suffix wherever content can answer: `.xls` covers
    two unrelated formats and only one of them is the XML kind this reads.

    `archive` is tested LAST among the content checks, and the order is
    load-bearing. `.xlsx`, `.docx` and `.odt` are zips, so a magic-bytes test
    placed first would claim every one of them as an archive to be unpacked.
    Everything this can actually read is identified before the question is
    asked. An archive never reaches `extract()` in the ordinary case — `ingest`
    expands one into its members before a bundle is built — so this exists for
    the case that slips past: a member that is itself an archive.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf" or looks_like_pdf(path):
        return "pdf"
    if looks_like_spreadsheetml(_head(path)):
        return "spreadsheetml"
    if looks_like_image(path):
        return "image"
    if suffix in TEXT_SUFFIXES or document.media_type == "text/plain":
        return "text"
    if suffix in {".xls", ".xlsx"}:
        return "binary_workbook"
    if looks_like_zip(path):
        return "archive"
    return "unsupported"


def probe(document: SourceDocument) -> DocumentFacts | None:
    """A document's media type and page count, without extracting its text.

    Cheap on purpose. `ingest()` needs these to store provenance that can answer
    "which page of this 48-page PDF", and it does not need the text — the
    adapter has already read that. Measured on a 48-page PDF: 18ms here against
    106ms for a full extraction, and the gap widens with text density.

    Never raises. This is metadata; a file whose type cannot be worked out is a
    file stored without it, not a failed upload. The payload has its own error
    path and it has already run by the time this is called.
    """
    if not document.path:
        return None
    path = Path(document.path)
    if not path.is_file():
        return None
    try:
        kind = classify(document, path)
        if kind == "pdf":
            import pdfplumber

            with pdfplumber.open(str(path)) as pdf:
                return DocumentFacts("application/pdf", len(pdf.pages))
        if kind == "spreadsheetml":
            return DocumentFacts("application/vnd.ms-excel", None)
        if kind == "image":
            # One capture is one page. Not None: a page count of None means the
            # format has no pages to count, which is true of a spreadsheet and
            # false of an image — a citation saying "page 2 of 3" across a set
            # of captures is exactly what a reader needs.
            return DocumentFacts(looks_like_image(path) or "image/png", 1)
        if kind == "text":
            return DocumentFacts(document.media_type or "text/plain", len(extract_text_file(path)))
    except Exception:
        # Same reasoning as the docstring: metadata that cannot be worked out is
        # metadata that goes unstored, and nothing downstream reads it yet.
        return None
    return None


def extract(document: SourceDocument, max_pages: int | None = None) -> ExtractedDocument:
    """Read one stored document into pages of text, or into sheets.

    `max_pages` exists for sniff(): deciding which adapter owns a bundle should
    not cost a full extraction of a 48-page PDF. **For a tabular document it
    bounds rows rather than pages**, reinterpreted rather than supplemented on
    purpose: a second parameter would have left `kroger/adapter.py`'s
    `max_pages=SNIFF_PAGES` and the generic fallback's equivalent doing an
    unbounded read of every spreadsheet in the bundle, so the bounded read would
    have protected exactly one adapter.
    """
    if not document.path:
        raise ExtractionError(f"{document.original_filename} has no stored path")
    path = Path(document.path)
    if not path.is_file():
        # The NAME, not the path. `document.path` points into `data/incoming/`
        # and carries a content-hash prefix, and this message is now logged
        # verbatim — a path the project treats as off-limits does not belong in
        # a log line to buy a reader nothing they did not already know.
        raise ExtractionError(f"{document.original_filename} is not on disk")

    suffix = path.suffix.lower()
    kind = classify(document, path)
    if kind == "pdf":
        pages = extract_pdf(path, max_pages)
        media_type = "application/pdf"
    elif kind == "spreadsheetml":
        # Routed by content, never by suffix: `.xls` covers two unrelated
        # formats and only one of them is this one.
        tables, spent = extract_spreadsheet(path, max_pages)
        if not spent and not any(table.rows for table in tables):
            # Guarded on `spent`, because a bounded read that gave up before
            # reaching a row also has no rows — and reporting that as an empty
            # spreadsheet would destroy the distinction the budget exists to
            # preserve. A caller that asked for a bounded read gets the fact
            # that it was bounded; only an unbounded read finding nothing means
            # the file is empty.
            raise ExtractionError(
                f"{document.original_filename} is a spreadsheet with no rows in it."
            )
        return ExtractedDocument(
            pages=(),
            filename=document.original_filename,
            media_type="application/vnd.ms-excel",
            document_id=document.id,
            tables=tables,
            spent_budget=spent,
        )
    elif kind == "text":
        pages = extract_text_file(path)
        media_type = document.media_type or "text/plain"
    elif kind == "image":
        # Reached only when no adapter claimed the image, because the adapter
        # that reads captures reads them as pixels through `transcription` and
        # never comes here. So the useful thing to say is which responses
        # arrive as images at all — not, as this used to, that "an image
        # reaches an adapter as a transcript", which described a design that
        # was replaced before it shipped and names nothing a reader can act on.
        raise NotTextBearing(
            f"{document.original_filename} is an image, and no retailer this "
            "knows about answers with one of this shape. Screen captures of a "
            "receipt are read when their filenames are the ones the store's "
            "own export produced; a photograph or a screenshot taken by hand "
            "is not something this can identify."
        )
    elif kind == "binary_workbook":
        # Reached only when the content check above said no, so this is a real
        # binary workbook rather than the XML kind.
        raise ExtractionError(
            f"{document.original_filename} is an Excel workbook. The export this "
            "reads is the XML kind: in Excel choose Save As and pick XML "
            "Spreadsheet 2003, or export CSV."
        )
    elif kind == "archive":
        # Reached only for an archive INSIDE an archive: `ingest` expands the
        # outer one before a bundle exists, and refuses a nested one by name
        # rather than recursing. A reader who gets here has an archive whose
        # members are archives, and the fix is theirs to make.
        raise ExtractionError(
            f"{document.original_filename} is an archive inside an archive. "
            "Unpack the inner one and upload what is in it."
        )
    else:
        raise ExtractionError(
            f"{document.original_filename} is a {suffix or 'typeless'} file. "
            "Supported inputs are PDF, text, images, the XML kind of spreadsheet "
            "export, and a zip of those."
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


def _head(path: Path) -> str:
    """The first few KB, for a routing decision that must not read the file."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(PROLOG_BYTES)
    except OSError:
        return ""


def extract_all(documents: tuple[SourceDocument, ...]) -> list[ExtractedDocument]:
    """Extract every document that can be read, skipping those that cannot.

    One unreadable attachment in a bundle of four must not lose the other three;
    the caller records the failures as parse warnings.
    """
    extracted = []
    for document in documents:
        try:
            extracted.append(extract(document))
        except NotTextBearing as exc:
            # Not a failure: the transcription tier reads these. It was being
            # logged as a warning anyway, once per file, so a healthy upload of
            # a statement and its receipt captures wrote one scary line per
            # capture — both untrue and the loudest thing in the log.
            log.debug("%s routes to the transcription tier: %s", document.original_filename, exc)
        except ExtractionError as exc:
            # A real failure, logged with the REASON rather than the filename
            # alone. `extract` raises with a sentence written for a person:
            # which kind of file this is, what would have had to read it, and
            # what to do next. "could not extract <file>" threw all of that
            # away and left nothing anyone could act on.
            log.warning("could not read %s: %s", document.original_filename, exc)
    return extracted
