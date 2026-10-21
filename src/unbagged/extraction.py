"""Getting text — and now tables — out of whatever the retailer sent.

Adapters work on what this module hands them, not on file formats. This is the
boundary: it turns a stored document into pages of text, or into sheets of
placed cells, and nothing more. It knows about PDFs, plain text, SpreadsheetML
2003 and the `.xlsx` kind of workbook; it knows nothing about any retailer.

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
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Container, Iterator
from dataclasses import dataclass
from pathlib import Path
from xml.parsers import expat

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

#: The `.xlsx` kind: Office Open XML, a zip of XML parts. Unrelated to `SS`
#: above despite the shared word — Excel's 2003 XML format and its 2007 zip
#: format have nothing else in common, and a reader of one reads none of the
#: other. Albertsons answers with this kind.
XLSX = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: What one workbook's XML may inflate to while it is read.
#:
#: The same reasoning as `ingest.MAX_ARCHIVE_BYTES`, because a workbook IS an
#: archive: the upload cap bounds compressed bytes, which is the wrong end of a
#: decompression bomb. 64 MB is what the XML kind of spreadsheet may be on
#: upload, so the two readers hold at most the same, and a real transaction
#: sheet of many years' line items is a few megabytes.
MAX_WORKBOOK_BYTES = 64 * 1024 * 1024

#: Excel's last column is XFD, the 16,384th. A cell naming one past it is not a
#: spreadsheet, and both readers pad a row out to the column a cell names — so
#: an uncapped reference let one 25-byte cell cost an 18,000-slot row.
MAX_COLUMN = 16_384

#: How many cell slots one workbook may place, padding included.
#:
#: The byte cap bounds the XML; it does not bound what the XML asks for. A row
#: of one cell at column XFD is a few bytes and 16,384 slots, so memory follows
#: the slots, and the slots are what is budgeted. Every row is charged at least
#: one, so a sheet of empty rows is budgeted too: ten million `<row/>` elements
#: are 60 MB of XML, under the byte cap, and measured 2.9 GB resident before
#: they were. A real response's sheets place tens of thousands of cells.
MAX_WORKBOOK_CELLS = 2_000_000

#: How many elements one row may hold, at any depth beneath it.
#:
#: Counting a row's cells is not enough, because a cell is a tree: a `Data`
#: holding a million empty children is one cell, and measured 1.9 GB before
#: everything under a row was counted. The bound is generous — a full-width row
#: of rich-text cells with a run and its properties each — and a real
#: response's rows hold a few dozen.
MAX_ROW_ELEMENTS = 16 * MAX_COLUMN

#: How deeply elements may nest. A real sheet is eight deep at most — a run's
#: properties inside a run inside an inline string inside a cell — and a cell
#: whose text is a thousand nested tags is not a spreadsheet.
MAX_DEPTH = 64

#: The parts read whole rather than streamed — relationships and the workbook
#: part — are a few KB in any real file, so they get a cap of their own rather
#: than the whole workbook's allowance each.
MAX_STRUCTURE_BYTES = 1024 * 1024

#: The namespace of a workbook saved as "Strict Open XML Spreadsheet". Same
#: shape, different names; this reader knows the transitional ones only.
XLSX_STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"

#: What a damaged or unsupported zip raises from inside `zipfile`. Narrower than
#: this, a corrupt deflate stream escaped as `zlib.error` and failed the whole
#: upload rather than costing the one file, against `extract_all`'s contract.
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    RuntimeError,
    OSError,
    zlib.error,
    EOFError,
    NotImplementedError,
)

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

#: How much of a file's head is read to decide what kind of file it is.
PROLOG_BYTES = 4096

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
    #: The workbook counts date serials from 1904 rather than 1900. Only an
    #: `.xlsx` can say so, and it matters to anything reading a date column: a
    #: serial is a day count, and the same count lands four years apart under
    #: the two epochs with nothing in the cell to tell them apart.
    date1904: bool = False

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


DECLARATION_MESSAGE = (
    "This file carries a document type declaration, which a retailer export "
    "does not. It has not been read."
)


class _DeclarationGuard:
    """Refuse a document type declaration, as the parser sees it.

    Internal entity expansion is what a declaration buys an attacker, and a
    retailer export carries none, so any declaration is refused outright.

    This used to be a regular expression over the first 4 KB, decoded as UTF-8.
    Both halves of that were wrong and both were demonstrated: a part encoded
    as UTF-16 decoded to noise the expression never matched, and a comment
    longer than the window pushed the declaration past it — yet the parser
    behind it read both and expanded the entity. So the question is put to an
    expat parser, which decodes the bytes the way the reading parser will and
    reports a declaration wherever in the prolog it sits.

    It stops at the root element, because a declaration cannot follow one, so
    it costs only the prolog however large the document is. Fed the same chunks
    as the reading parser and ahead of it, so nothing reaches that parser until
    the guard has seen it.

    **Reparse deferral is switched off.** Expat 2.6+ holds back a large token
    that spans chunks until more input arrives, and the first version of this
    guard was never fed a final chunk: a prolog comment past ~131 KB left the
    declaration behind it unread by the guard and read by the parser at
    `close()`, which expanded an entity into a cell. With deferral off the guard
    parses every byte it is given, and `close()` feeds it the end anyway.
    """

    def __init__(self) -> None:
        self._parser = expat.ParserCreate()
        if hasattr(self._parser, "SetReparseDeferralEnabled"):
            self._parser.SetReparseDeferralEnabled(False)
        self._parser.StartDoctypeDeclHandler = self._refuse
        self._parser.EntityDeclHandler = self._refuse
        self._parser.StartElementHandler = self._root
        self._done = False

    @staticmethod
    def _refuse(*_args: object) -> None:
        raise ExtractionError(DECLARATION_MESSAGE)

    def _root(self, *_args: object) -> None:
        self._done = True

    def feed(self, data: bytes | str, final: bool = False) -> None:
        if self._done:
            return
        try:
            self._parser.Parse(data, final)
        except (LookupError, ValueError) as exc:
            # A declared encoding expat cannot decode: an unknown name, a
            # multi-byte codec, or one that is not a text encoding at all. The
            # reading parser raises the same, and neither is a ParseError, so
            # unhandled it failed the whole upload rather than this one file.
            raise ExtractionError(_encoding_message()) from exc
        except expat.ExpatError:
            # Malformed before the root. The reading parser fails on the same
            # bytes and says so in its own words; nothing past this point can
            # reach it, so there is nothing left to guard.
            self._done = True

    def close(self) -> None:
        """The end of the input: anything still held is parsed now."""
        self.feed(b"", final=True)


class _Tree:
    """The open elements of one streamed document, kept to what a row needs.

    Pull parsing does not by itself bound memory: the parser still builds the
    tree, and every element it has ended stays attached to its parent. So:

    * **Every element at `row_depth` or above is detached once it ends** and
      the caller has had it — rows, and anything a document puts beside them.
      Detaching only rows left everything else in place, and each row's removal
      then scanned past it all: 1.2 MB of rows among other elements took 109 s.
      Detached as they end, an element is always its parent's only child.
    * **Everything beneath `row_depth` is counted**, against `MAX_ROW_ELEMENTS`
      per element at that depth, and nesting against `MAX_DEPTH`. That subtree
      is held until its row ends, because the row is read whole.
    * **A row's direct children are counted against `MAX_COLUMN`** as they
      start, with the message that names columns, because that is the limit a
      person can recognise.

    So memory follows one row, not the sheet. The consequence is the one
    `read_tables` states: the caller must take what it needs from an element
    while it holds it.
    """

    def __init__(self, row_depth: int) -> None:
        self.open: list[ET.Element] = []
        self.row_depth = row_depth
        self.children = 0
        self.elements = 0

    def start(self, element: ET.Element) -> None:
        depth = len(self.open) + 1
        if depth > MAX_DEPTH:
            raise ExtractionError(_too_deep_message())
        if depth == self.row_depth:
            self.children = self.elements = 0
        elif depth > self.row_depth:
            self.elements += 1
            if self.elements > MAX_ROW_ELEMENTS:
                raise ExtractionError(_too_many_elements_message())
            if depth == self.row_depth + 1:
                self.children += 1
                if self.children > MAX_COLUMN:
                    raise ExtractionError(_past_last_column_message())
        self.open.append(element)

    def end(self) -> None:
        self.open.pop()

    def release(self, element: ET.Element) -> None:
        """Detach an element that has ended, once the caller is done with it."""
        if self.open and len(self.open) < self.row_depth:
            self.open[-1].remove(element)


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
    guard = _DeclarationGuard()
    parser = ET.XMLPullParser(events=("start", "end"))
    tables: list[Table] = []
    cells_placed = 0
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
    #: Rows sit at depth 4: Workbook, Worksheet, Table, Row. Cleared but
    #: attached, two million `<Row/>` measured 468 MB; see `_Tree`.
    tree = _Tree(row_depth=4)

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

    def consume() -> bool:
        """Handle every event the parser has ready. True when a bound is hit."""
        nonlocal name, row_number, spans, in_sheet, declared_rows, declared_columns
        nonlocal cells_placed
        for event, element in parser.read_events():
            tag = element.tag.rpartition("}")[2]
            if event == "start":
                tree.start(element)
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
                continue
            tree.end()
            if tag == "Row":
                row_number = _as_int(element.get(f"{{{SS}}}Index")) or (row_number + 1)
                # Dropped as they expire, which also makes `spans` the set of
                # columns blocked in THIS row. Keyed on the last row a span
                # covers rather than a countdown, because `ss:Index` on a Row
                # skips rows and a countdown would hold a span open across the
                # gap.
                spans = {column: last for column, last in spans.items() if last >= row_number}
                cells, opened = _cells_of(element, spans.keys())
                cells_placed += max(1, len(cells))
                if cells_placed > MAX_WORKBOOK_CELLS:
                    raise ExtractionError(_too_many_cells_message())
                for column, down in opened.items():
                    spans[column] = row_number + down
                rows.append(Row(cells=cells, number=row_number))
                element.clear()
                tree.release(element)
                if max_rows is not None and len(rows) >= max_rows:
                    return True
                continue
            if tag == "Worksheet":
                element.clear()
            tree.release(element)
        return False

    stopping = False
    try:
        for start in range(0, len(source), CHUNK_BYTES):
            chunk = source[start : start + CHUNK_BYTES]
            guard.feed(chunk)
            parser.feed(chunk)
            consumed += len(chunk)
            if consume():
                stopping = True
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
            #
            # And its events are read: a parser can hold back a large token
            # until the end, and what it releases at close() — a whole sheet,
            # once — was being dropped without a word.
            guard.close()
            parser.close()
            stopping = consume()
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
        if column > MAX_COLUMN:
            raise ExtractionError(_past_last_column_message())
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


# --------------------------------------------------------------------------
# The `.xlsx` kind
# --------------------------------------------------------------------------


class _Shared(int):
    """A cell that holds an index into the shared-string table, not a value.

    A subclass rather than a bare int so the resolving pass cannot mistake a
    number for a reference: every `t="s"` cell is one of these, every numeric
    cell is a `str`, and the two never meet.
    """


def looks_like_xlsx(path: Path) -> bool:
    """A zip that is a spreadsheet package, decided by what it declares.

    The package names its own main part, and a workbook's main part is a
    workbook. A `.docx` is the same kind of zip with a different main part, so
    this is what keeps a letter from being read as an empty spreadsheet.
    """
    if not looks_like_zip(path):
        return False
    try:
        with zipfile.ZipFile(path) as package:
            return _workbook_part(package) is not None
    except (*_ZIP_ERRORS, KeyError, ExtractionError):
        return False


def _workbook_part(package: zipfile.ZipFile) -> tuple[str, ET.Element] | None:
    """The workbook part's path and its parsed root, or None if there is none.

    Read from the package's own relationships: almost always `xl/workbook.xml`,
    and the fallback says so, but the package is entitled to put it elsewhere.
    The root comes back with the path so the part is parsed once per read, not
    once to recognise it and again to use it.
    """
    names = set(package.namelist())
    candidates = []
    if "_rels/.rels" in names:
        root = _parse_part(package, "_rels/.rels")
        for rel in root.iter(f"{{{PACKAGE_REL}}}Relationship"):
            if rel.get("Type", "").endswith("/officeDocument"):
                candidates.append(rel.get("Target", "").lstrip("/"))
    candidates.append("xl/workbook.xml")
    for name in candidates:
        if name in names:
            workbook = _parse_part(package, name)
            if workbook.tag in (f"{{{XLSX}}}workbook", f"{{{XLSX_STRICT}}}workbook"):
                return name, workbook
    return None


def _read_part(package: zipfile.ZipFile, name: str) -> bytes:
    """One structural part, whole, refused past `MAX_STRUCTURE_BYTES`.

    For relationships and the workbook part, which are a few KB and are needed
    whole. The sheets and the string table are streamed; see `_stream_part`.
    """
    try:
        with package.open(name) as handle:
            data = handle.read(MAX_STRUCTURE_BYTES + 1)
    except _ZIP_ERRORS as exc:
        raise ExtractionError(_package_message(exc)) from exc
    if len(data) > MAX_STRUCTURE_BYTES:
        raise ExtractionError(
            f"This workbook's {name} is too large for the part it is, which is a "
            "few kilobytes in any real workbook. It has not been read."
        )
    return data


def _parse_part(package: zipfile.ZipFile, name: str) -> ET.Element:
    data = _read_part(package, name)
    guard = _DeclarationGuard()
    guard.feed(data, final=True)
    try:
        # The guard has refused any declaration, which is the whole of the
        # attack S314 is about here: see `_DeclarationGuard`.
        return ET.fromstring(data)  # noqa: S314
    except ET.ParseError as exc:
        raise ExtractionError(f"This workbook is damaged: {name} is not readable ({exc}).") from exc


def _stream_part(
    package: zipfile.ZipFile, name: str, spent: list[int], *, row_depth: int
) -> Iterator[ET.Element]:
    """Each element of one part as it ends, inflation charged to `spent`.

    `spent` is a one-element list so every part read in one workbook shares a
    single allowance — the cap has to hold across the workbook, not per part,
    or a bomb split over twenty sheets is twenty times the cap.

    Elements at `row_depth` and above are detached once the caller has had
    them, and everything beneath is budgeted; see `_Tree`. Clearing a row is
    not enough on its own: the emptied element stays attached to its parent,
    and a few million of them under `sheetData` are gigabytes from a capped
    byte count.

    A generator so the caller can stop early: a bounded read that has its rows
    closes this, and nothing past them is inflated at all.
    """
    guard = _DeclarationGuard()
    parser = ET.XMLPullParser(events=("start", "end"))
    tree = _Tree(row_depth)
    try:
        with package.open(name) as handle:
            while chunk := handle.read(CHUNK_BYTES):
                spent[0] += len(chunk)
                if spent[0] > MAX_WORKBOOK_BYTES:
                    raise ExtractionError(_too_large_message())
                guard.feed(chunk)
                parser.feed(chunk)
                yield from _ended(parser, tree)
        guard.close()
        parser.close()
        yield from _ended(parser, tree)
    except _ZIP_ERRORS as exc:
        raise ExtractionError(_package_message(exc)) from exc
    except ET.ParseError as exc:
        raise ExtractionError(f"This workbook is damaged: {name} is not readable ({exc}).") from exc


def _ended(parser: ET.XMLPullParser, tree: _Tree) -> Iterator[ET.Element]:
    """End events, each detached once the caller has had it."""
    for event, element in parser.read_events():
        if event == "start":
            tree.start(element)
            continue
        tree.end()
        yield element
        tree.release(element)


def _package_message(exc: Exception) -> str:
    """RuntimeError is what `zipfile` raises for an encrypted member."""
    if isinstance(exc, RuntimeError):
        return (
            "This workbook is password protected. Open it in Excel, remove the "
            "password, save it, and upload it again."
        )
    return (
        f"This workbook could not be opened ({exc}). If it was downloaded, the "
        "download may be incomplete — download it again and re-upload."
    )


def _past_last_column_message() -> str:
    return (
        "This spreadsheet names a column past the last one a spreadsheet can "
        "have, which no real export does. It has not been read."
    )


def _too_many_elements_message() -> str:
    return (
        f"One row of this spreadsheet holds more than {MAX_ROW_ELEMENTS:,} elements, "
        "far more than any real row. It has not been read."
    )


def _too_deep_message() -> str:
    return (
        f"This spreadsheet's markup is nested more than {MAX_DEPTH} deep, which no "
        "real spreadsheet is. It has not been read."
    )


def _encoding_message() -> str:
    return (
        "This file declares a text encoding that cannot be read here. Open it in "
        "the program that made it, save it again, and re-upload that."
    )


def _too_many_cells_message() -> str:
    return (
        f"This spreadsheet places more than {MAX_WORKBOOK_CELLS:,} cells, far more "
        "than a right-to-know response holds. It has not been read."
    )


def _too_large_message() -> str:
    return (
        f"This workbook inflates to more than {MAX_WORKBOOK_BYTES // (1024 * 1024)} MB "
        "of spreadsheet once it is opened, which is far more than a right-to-know "
        "response holds. It has not been read."
    )


_A1 = re.compile(r"^([A-Z]{1,3})(\d+)$")


def _column_index(letters: str) -> int:
    """A -> 1, Z -> 26, AA -> 27. The inverse of `column_letter`."""
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index


def _dimension(ref: str | None) -> tuple[int | None, int | None]:
    """`A1:J9131` -> (9131 rows, 10 columns). What the sheet says of its size."""
    if not ref:
        return None, None
    corner = _A1.match(ref.rpartition(":")[2].replace("$", ""))
    if not corner:
        return None, None
    # `_as_int`, not `int`: a reference of thousands of digits raised past
    # Python's integer-string limit, and a ValueError cost the whole upload.
    return _as_int(corner.group(2)), _column_index(corner.group(1))


def _text_of(element: ET.Element) -> str:
    """The text of a string item: its `t`, or its rich-text runs' `t`s.

    Deliberately not `itertext()`. A string item may carry `rPh` phonetic runs —
    furigana for the cell's text — and their `t` children are not the value;
    gathering the whole subtree would append them to it.
    """
    parts = [element.findtext(f"{{{XLSX}}}t") or ""]
    parts += [run.findtext(f"{{{XLSX}}}t") or "" for run in element.iterfind(f"{{{XLSX}}}r")]
    return "".join(parts)


def _cell_value(cell: ET.Element) -> str | _Shared | None:
    """What one `c` element holds, as the sheet wrote it.

    Numbers come back as the text of `v`, unparsed: `1.1500000000000001` stays
    that, and so does a date serial. What a number MEANS — a date, a price, an
    identifier — is the adapter's to decide, because only the adapter knows
    which column it is in. A formatted display value is never produced here.
    """
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(f"{{{XLSX}}}is")
        return None if inline is None else _text_of(inline)
    value = cell.findtext(f"{{{XLSX}}}v")
    if value is None:
        return None
    if kind == "s":
        try:
            index = int(value)
        except ValueError:
            return None
        if index < 0:
            # Python would read -1 as "the last string", which is a real value
            # from somewhere else in the sheet arriving with no warning.
            raise ExtractionError(
                "This workbook is damaged: a cell refers to a string by a negative position."
            )
        return _Shared(index)
    if kind == "b":
        return "TRUE" if value.strip() == "1" else "FALSE"
    return value


def read_workbook(
    path: Path,
    *,
    max_rows: int | None = None,
    budget_bytes: int | None = None,
) -> TableRead:
    """Read an `.xlsx` into placed cells, with the same bounds `read_tables` has.

    The two readers return the same thing so an adapter never needs to know
    which kind of spreadsheet it was handed. Where they differ is underneath:

    * **Cells are placed by their own reference.** Every `c` carries `r="D14"`,
      which names its column outright, so a skipped cell cannot shift the ones
      after it the way an omitted SpreadsheetML cell can. A cell without `r` is
      legal and takes the next column, which is the same rule `ss:Index`
      leaves behind.
    * **A merged region needs nothing.** Its covered cells are written as empty
      `c` elements, not left out, so they hold their columns themselves.
    * **Strings live in one table for the whole workbook.** A cell holds an
      index into it, and the table is read after the sheets — only as far as
      the highest index any read row needs, which for a bounded read is a few
      entries at the front.

    `max_rows` stops after that many rows of the sheet being read, and ends the
    read there, exactly as `read_tables` does. `budget_bytes` bounds what a
    bounded read may inflate; the hard `MAX_WORKBOOK_BYTES` bounds every read.
    """
    try:
        package = zipfile.ZipFile(path)
    except _ZIP_ERRORS as exc:
        raise ExtractionError(_package_message(exc)) from exc

    with package:
        found = _workbook_part(package)
        if found is None:
            raise ExtractionError("This file is a zip, but not a workbook.")
        workbook_name, workbook = found
        if workbook.tag == f"{{{XLSX_STRICT}}}workbook":
            raise ExtractionError(
                "This workbook was saved as Strict Open XML Spreadsheet, which this "
                "does not read. Open it in Excel and save it as Excel Workbook "
                "(.xlsx), then upload that."
            )
        properties = workbook.find(f"{{{XLSX}}}workbookPr")
        date1904 = properties is not None and properties.get("date1904") in ("1", "true")

        targets, strings_name = _workbook_relationships(package, workbook_name)
        spent = [0]
        raw_tables: list[tuple[str, list[tuple[int, list]], int | None, int | None, bool]] = []
        stopping = spent_budget = False
        cells_placed = 0

        for sheet in workbook.iter(f"{{{XLSX}}}sheet"):
            target = targets.get(sheet.get(f"{{{XLSX_REL}}}id") or "")
            if target is None:
                # A chartsheet, or a relationship the package does not carry.
                # Neither has rows to read.
                continue
            rows: list[tuple[int, list]] = []
            declared_rows = declared_columns = None
            row_number = 0
            # Rows sit at depth 3: worksheet, sheetData, row.
            events = _stream_part(package, target, spent, row_depth=3)
            try:
                for element in events:
                    if budget_bytes is not None and spent[0] >= budget_bytes:
                        # Checked on every event, not only on rows: a preamble
                        # that never reaches a row is the case the budget is for.
                        spent_budget = stopping = True
                        break
                    tag = element.tag.rpartition("}")[2]
                    if tag == "dimension":
                        declared_rows, declared_columns = _dimension(element.get("ref"))
                    elif tag == "row":
                        row_number = _as_int(element.get("r")) or (row_number + 1)
                        cells = _xlsx_cells(element)
                        cells_placed += max(1, len(cells))
                        if cells_placed > MAX_WORKBOOK_CELLS:
                            raise ExtractionError(_too_many_cells_message())
                        element.clear()
                        if not cells:
                            # A row with no cells says nothing an adapter can
                            # read, and no number is lost: every row kept
                            # carries its own.
                            continue
                        rows.append((row_number, cells))
                        if max_rows is not None and len(rows) >= max_rows:
                            stopping = True
                            break
                    elif tag == "sheetData":
                        element.clear()
            finally:
                events.close()
            raw_tables.append(
                (sheet.get("name") or "", rows, declared_rows, declared_columns, stopping)
            )
            if stopping:
                break

        needed = max(
            (
                cell
                for _name, rows, *_rest in raw_tables
                for _number, cells in rows
                for cell in cells
                if isinstance(cell, _Shared)
            ),
            default=-1,
        )
        strings: list[str] = []
        if needed >= 0:
            found_strings = _shared_strings(package, strings_name, needed, spent, budget_bytes)
            if found_strings is None:
                # A bounded read that spent its budget in the string table: the
                # same answer as one that spent it before reaching a row.
                return TableRead(tables=(), spent_budget=True)
            strings = found_strings

    tables = []
    for name, rows, declared_rows, declared_columns, truncated in raw_tables:
        placed = []
        for number, cells in rows:
            resolved = []
            for cell in cells:
                if isinstance(cell, _Shared):
                    if not 0 <= cell < len(strings):
                        raise ExtractionError(
                            "This workbook is damaged: a cell refers to a string "
                            "the workbook does not contain."
                        )
                    resolved.append(strings[cell])
                else:
                    resolved.append(cell)
            placed.append(Row(cells=tuple(resolved), number=number))
        tables.append(
            Table(
                name=name,
                rows=tuple(placed),
                declared_rows=declared_rows,
                declared_columns=declared_columns,
                truncated=truncated,
                date1904=date1904,
            )
        )
    return TableRead(tables=tuple(tables), spent_budget=spent_budget)


def _workbook_relationships(
    package: zipfile.ZipFile, workbook_name: str
) -> tuple[dict[str, str], str | None]:
    """Each worksheet's part, by relationship id, and the shared-string part.

    Targets are relative to the workbook part's own folder, so they are joined
    against it rather than assumed to live in `xl/`.
    """
    folder, _, leaf = workbook_name.rpartition("/")
    rels_name = f"{folder}/_rels/{leaf}.rels" if folder else f"_rels/{leaf}.rels"
    names = set(package.namelist())
    sheets: dict[str, str] = {}
    strings: str | None = None
    if rels_name not in names:
        return sheets, strings
    for rel in _parse_part(package, rels_name).iter(f"{{{PACKAGE_REL}}}Relationship"):
        target = rel.get("Target", "")
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(folder, target))
        if resolved not in names:
            continue
        kind = rel.get("Type", "")
        if kind.endswith("/worksheet"):
            sheets[rel.get("Id", "")] = resolved
        elif kind.endswith("/sharedStrings"):
            strings = resolved
    return sheets, strings


def _xlsx_cells(row: ET.Element) -> list:
    """Place a row's cells at the columns their references name."""
    placed: list = []
    column = 0
    for cell in row.iterfind(f"{{{XLSX}}}c"):
        ref = _A1.match(cell.get("r") or "")
        column = _column_index(ref.group(1)) if ref else column + 1
        if column > MAX_COLUMN:
            raise ExtractionError(_past_last_column_message())
        while len(placed) < column - 1:
            placed.append(None)
        value = _cell_value(cell)
        if len(placed) == column - 1:
            placed.append(value)
        else:
            # A reference to a column already filled — out of order, or twice.
            # The later one wins, which is what Excel shows.
            placed[column - 1] = value
    return placed


def _shared_strings(
    package: zipfile.ZipFile,
    name: str | None,
    needed: int,
    spent: list[int],
    budget_bytes: int | None,
) -> list[str] | None:
    """The shared-string table, read only as far as index `needed`.

    None when a bounded read spends its budget first. The table is in order of
    first use in any workbook Excel wrote, so the strings a sniff needs are at
    the front — but a tool that orders it otherwise could put one at the end of
    a large table, and a sniff is not entitled to read all of it.
    """
    if name is None:
        raise ExtractionError(
            "This workbook is damaged: its cells refer to a string table it does not contain."
        )
    strings: list[str] = []
    # String items sit at depth 2: sst, si.
    events = _stream_part(package, name, spent, row_depth=2)
    try:
        for element in events:
            if budget_bytes is not None and spent[0] >= budget_bytes:
                return None
            if element.tag == f"{{{XLSX}}}si":
                strings.append(_text_of(element))
                element.clear()
                if len(strings) > needed:
                    break
    finally:
        events.close()
    return strings


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


def extract_workbook(path: Path, max_rows: int | None = None) -> tuple[tuple[Table, ...], bool]:
    """Read an `.xlsx` into sheets, bounded the same way `extract_spreadsheet` is."""
    read = read_workbook(
        path,
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

    `xlsx` is decided by the package's own declaration and before any suffix
    check, for the reason every content check here is: a workbook that reached
    this tool through a mail client may be called anything. A zip that does not
    declare a workbook — a damaged download, a `.docx` — falls through to the
    suffix and is answered from there.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf" or looks_like_pdf(path):
        return "pdf"
    if looks_like_spreadsheetml(_head(path)):
        return "spreadsheetml"
    if looks_like_image(path):
        return "image"
    if looks_like_xlsx(path):
        return "xlsx"
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
        if kind == "xlsx":
            return DocumentFacts(XLSX_MEDIA_TYPE, None)
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
    elif kind in ("spreadsheetml", "xlsx"):
        # Routed by content, never by suffix: `.xls` covers two unrelated
        # formats and only one of them is the XML kind. The two readers return
        # the same sheets, so nothing past this point knows which ran.
        if kind == "xlsx":
            tables, spent = extract_workbook(path, max_pages)
            media_type = XLSX_MEDIA_TYPE
        else:
            tables, spent = extract_spreadsheet(path, max_pages)
            media_type = "application/vnd.ms-excel"
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
            media_type=media_type,
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
        # Reached only when the content checks above said no: a binary BIFF
        # `.xls`, or a `.xlsx` whose package will not open or declares no
        # workbook. `.xlsx` itself is read — see `read_workbook`.
        if suffix == ".xlsx" and _head_bytes(path).startswith(OLE_MAGIC):
            # Excel saves a password-protected .xlsx inside an OLE container,
            # not a zip. "Download it again" is advice that cannot work for it.
            raise ExtractionError(
                f"{document.original_filename} is password protected. Open it in "
                "Excel, remove the password, save it as Excel Workbook (.xlsx), "
                "and upload that."
            )
        raise ExtractionError(
            f"{document.original_filename} is an Excel workbook this cannot open. "
            "An .xlsx workbook is read directly, so if this is one it may be "
            "damaged — download it again. An older .xls is not: in Excel choose "
            "Save As and pick Excel Workbook (.xlsx) or XML Spreadsheet 2003."
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
            "Supported inputs are PDF, text, images, .xlsx, the XML kind of "
            "spreadsheet export, and a zip of those."
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


#: The container Excel uses for a password-protected workbook and for an old
#: binary `.xls` alike.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _head_bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(len(OLE_MAGIC))
    except OSError:
        return b""


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
