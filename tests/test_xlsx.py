"""The `.xlsx` reader: placed cells, shared strings, bounds, and refusals.

Everything here is built in the test from parts, because a workbook is a zip
and a zip is the one kind of fixture this repository will not commit.
"""

import io
import zipfile
from pathlib import Path

import pytest
from tools import make_fixtures

from unbagged import extraction
from unbagged.extraction import (
    XLSX_MEDIA_TYPE,
    ExtractionError,
    classify,
    extract,
    looks_like_xlsx,
    probe,
    read_workbook,
)
from unbagged.models import SourceDocument

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

GENERATOR = make_fixtures.load(Path("src/unbagged/adapters/albertsons/fixtures/generate.py"))


def sheet(rows: str, extra: str = "") -> str:
    return f'<worksheet xmlns="{MAIN}">{extra}<sheetData>{rows}</sheetData></worksheet>'


def workbook(
    sheets: dict[str, str],
    strings: list[str] | None = None,
    *,
    properties: str = "",
    content_types: str = "spreadsheetml.sheet.main+xml",
) -> bytes:
    """A minimal package: the parts a reader needs and nothing else."""
    entries = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheets, 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    parts = {
        "[Content_Types].xml": f'<Types><Override ContentType="{content_types}"/></Types>',
        "_rels/.rels": (
            f'<Relationships xmlns="{PKG}"><Relationship Id="r" '
            f'Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}">{properties}<sheets>{entries}</sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PKG}">{rels}'
        + (
            f'<Relationship Id="rS" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>'
            if strings is not None
            else ""
        )
        + "</Relationships>",
    }
    for i, body in enumerate(sheets.values(), 1):
        parts[f"xl/worksheets/sheet{i}.xml"] = body
    if strings is not None:
        parts["xl/sharedStrings.xml"] = (
            f'<sst xmlns="{MAIN}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, body in parts.items():
            package.writestr(name, body)
    return buffer.getvalue()


def write(tmp_path, data: bytes, name: str = "book.xlsx") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def doc(path: Path) -> SourceDocument:
    return SourceDocument(path.name, "0" * 64, path=str(path))


@pytest.fixture(scope="module")
def read(tmp_path_factory):
    path = tmp_path_factory.mktemp("xlsx") / "details.xlsx"
    path.write_bytes(GENERATOR.build_workbook())
    return read_workbook(path)


class TestTheGeneratedResponse:
    """The fixture workbook, which carries every shape the real one did."""

    def test_sheets_arrive_in_workbook_order_with_their_names(self, read):
        assert [t.name for t in read.tables] == ["Albertsons Transactions", "Demographic Data"]

    def test_the_header_is_row_three_under_a_merged_title(self, read):
        transactions = read.tables[0]
        assert transactions.rows[0].cells[0] == "SALES/TRANSACTION HISTORY"
        # The merged region's covered cells are written empty, not left out.
        assert transactions.rows[0].cells[1:] == (None,) * 9
        assert transactions.rows[2].number == 3
        assert transactions.rows[2].cells == GENERATOR.HEADERS

    def test_every_line_is_read_and_the_dimension_agrees(self, read):
        transactions = read.tables[0]
        model = GENERATOR.build()
        lines = sum(len(b["lines"]) for b in model["baskets"])
        assert len(transactions.rows) == lines + 3
        assert transactions.declared_rows == lines + 3
        assert transactions.declared_columns == 10

    def test_numbers_come_back_as_the_sheet_wrote_them(self, read):
        """Unparsed: a date is its serial, and an artefact stays an artefact."""
        data = read.tables[0].rows[3:]
        assert all(row.cells[0].isdigit() for row in data)
        nets = [row.cells[9] for row in data]
        assert any("E-" in net for net in nets), "the fixture must carry E-notation"
        assert any(len(net.partition(".")[2]) > 4 for net in nets), "and float artefacts"


class TestPlacement:
    def test_a_skipped_cell_does_not_shift_the_ones_after_it(self, tmp_path):
        rows = '<row r="1"><c r="A1"><v>1</v></c><c r="D1"><v>4</v></c></row>'
        (table,) = read_workbook(write(tmp_path, workbook({"S": sheet(rows)}))).tables
        assert table.rows[0].cells == ("1", None, None, "4")
        assert table.locator(1, 4) == "S!D1"

    def test_a_cell_without_a_reference_takes_the_next_column(self, tmp_path):
        rows = '<row r="1"><c r="B1"><v>2</v></c><c><v>3</v></c></row>'
        (table,) = read_workbook(write(tmp_path, workbook({"S": sheet(rows)}))).tables
        assert table.rows[0].cells == (None, "2", "3")

    def test_row_numbers_come_from_the_row_not_a_count(self, tmp_path):
        rows = '<row r="2"><c r="A2"><v>1</v></c></row><row r="7"><c r="A7"><v>2</v></c></row>'
        (table,) = read_workbook(write(tmp_path, workbook({"S": sheet(rows)}))).tables
        assert [r.number for r in table.rows] == [2, 7]


class TestValues:
    def test_shared_inline_boolean_and_error_cells(self, tmp_path):
        rows = (
            '<row r="1"><c r="A1" t="s"><v>1</v></c>'
            '<c r="B1" t="inlineStr"><is><t>inline</t></is></c>'
            '<c r="C1" t="b"><v>1</v></c><c r="D1" t="e"><v>#N/A</v></c></row>'
        )
        book = workbook({"S": sheet(rows)}, strings=["zero", "one"])
        (table,) = read_workbook(write(tmp_path, book)).tables
        assert table.rows[0].cells == ("one", "inline", "TRUE", "#N/A")

    def test_rich_text_is_joined_and_phonetic_runs_are_not_part_of_it(self, tmp_path):
        rows = '<row r="1"><c r="A1" t="inlineStr"><is><r><t>Ab</t></r><r><t>cd</t></r>'
        rows += "<rPh><t>ignored</t></rPh></is></c></row>"
        (table,) = read_workbook(write(tmp_path, workbook({"S": sheet(rows)}))).tables
        assert table.rows[0].cells == ("Abcd",)

    def test_the_1904_epoch_is_carried_to_the_reader(self, tmp_path):
        book = workbook(
            {"S": sheet('<row r="1"><c r="A1"><v>1</v></c></row>')},
            properties='<workbookPr date1904="1"/>',
        )
        (table,) = read_workbook(write(tmp_path, book)).tables
        assert table.date1904 is True

    def test_a_string_index_past_the_table_is_a_damaged_workbook(self, tmp_path):
        rows = '<row r="1"><c r="A1" t="s"><v>5</v></c></row>'
        book = workbook({"S": sheet(rows)}, strings=["only"])
        with pytest.raises(ExtractionError, match="damaged"):
            read_workbook(write(tmp_path, book))

    def test_a_shared_string_with_no_table_is_a_damaged_workbook(self, tmp_path):
        rows = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        with pytest.raises(ExtractionError, match="damaged"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)})))


class TestBounds:
    def test_a_bounded_read_stops_at_its_rows_and_says_it_was_bounded(self, tmp_path):
        rows = "".join(f'<row r="{n}"><c r="A{n}"><v>{n}</v></c></row>' for n in range(1, 50))
        book = workbook({"S": sheet(rows), "T": sheet(rows)})
        read = read_workbook(write(tmp_path, book), max_rows=3)
        (table,) = read.tables
        assert len(table.rows) == 3
        assert table.truncated
        assert not read.spent_budget

    def test_a_preamble_past_the_budget_is_reported_not_rejected(self, tmp_path):
        """Long enough that the stream has inflated past the budget before
        reaching a row — not merely longer than it in total."""
        column = '<col min="1" max="1"/>'
        padding = f"<cols>{column * 60_000}</cols>"
        rows = '<row r="1"><c r="A1"><v>1</v></c></row>'
        book = workbook({"S": sheet(rows, extra=padding)})
        read = read_workbook(write(tmp_path, book), max_rows=3, budget_bytes=256 * 1024)
        assert read.spent_budget
        assert read.tables[0].rows == ()

    def test_inflation_past_the_cap_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extraction, "MAX_WORKBOOK_BYTES", 4096)
        rows = "".join(f'<row r="{n}"><c r="A{n}"><v>{n}</v></c></row>' for n in range(1, 2000))
        with pytest.raises(ExtractionError, match="inflates to more than"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)})))

    def test_a_document_type_declaration_is_refused(self, tmp_path):
        body = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "a">]>' + sheet("")
        with pytest.raises(ExtractionError, match="document type declaration"):
            read_workbook(write(tmp_path, workbook({"S": body})))


class TestRouting:
    def test_a_workbook_is_classified_by_content_whatever_it_is_called(self, tmp_path):
        path = write(tmp_path, GENERATOR.build_workbook(), "Additional Details (1).bin")
        assert looks_like_xlsx(path)
        assert classify(doc(path), path) == "xlsx"

    def test_a_word_document_is_not_a_workbook(self, tmp_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            package.writestr("[Content_Types].xml", "<Types/>")
            package.writestr(
                "_rels/.rels",
                f'<Relationships xmlns="{PKG}"><Relationship Id="r" '
                f'Type="{REL}/officeDocument" Target="word/document.xml"/></Relationships>',
            )
            package.writestr("word/document.xml", "<document/>")
        assert not looks_like_xlsx(write(tmp_path, buffer.getvalue(), "letter.docx"))

    def test_extract_and_probe_agree_on_what_it_is(self, tmp_path):
        path = write(tmp_path, GENERATOR.build_workbook())
        extracted = extract(doc(path))
        assert extracted.media_type == XLSX_MEDIA_TYPE
        assert extracted.pages == ()
        assert len(extracted.tables) == 2
        assert probe(doc(path)).media_type == XLSX_MEDIA_TYPE

    def test_a_bounded_extract_reads_the_first_rows_only(self, tmp_path):
        path = write(tmp_path, GENERATOR.build_workbook())
        (table,) = extract(doc(path), max_pages=5).tables
        assert len(table.rows) == 5

    def test_a_damaged_xlsx_says_to_download_it_again(self, tmp_path):
        path = write(tmp_path, b"PK\x03\x04and the rest of a workbook")
        with pytest.raises(ExtractionError, match="download it again"):
            extract(doc(path))


class TestHostileWorkbooks:
    """Shapes a workbook can take to cost far more than its size, or to slip a
    declaration past a guard. Each was demonstrated against this reader before
    it was fixed; each test failed first."""

    def test_a_cell_beyond_excels_last_column_is_refused(self, tmp_path):
        # One short cell named past XFD padded its row to ~18,000 slots: a
        # 10 KB file measured 600 MB and 92 s before this was refused.
        rows = '<row r="1"><c r="ZZZ1"><v>1</v></c></row>'
        with pytest.raises(ExtractionError, match="column"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)})))

    def test_the_cells_a_workbook_places_are_budgeted(self, tmp_path, monkeypatch):
        # Within the column limit, padding still costs memory per slot, so the
        # slots are what is budgeted, not the bytes that asked for them.
        monkeypatch.setattr(extraction, "MAX_WORKBOOK_CELLS", 1000)
        rows = "".join(f'<row r="{n}"><c r="XFD{n}"><v>1</v></c></row>' for n in range(1, 4))
        with pytest.raises(ExtractionError, match="cells"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)})))

    def test_empty_rows_are_budgeted_too(self, tmp_path, monkeypatch):
        """A row with no cells placed nothing and was charged nothing, yet cost
        a record each: ten million `<row/>` measured 2.9 GB resident."""
        monkeypatch.setattr(extraction, "MAX_WORKBOOK_CELLS", 1000)
        with pytest.raises(ExtractionError, match="cells"):
            read_workbook(write(tmp_path, workbook({"S": sheet("<row/>" * 1001)})))

    def test_a_row_with_no_cells_is_not_kept(self, tmp_path):
        rows = '<row r="1"/><row r="2"><c r="A2"><v>1</v></c></row>'
        (table,) = read_workbook(write(tmp_path, workbook({"S": sheet(rows)}))).tables
        assert [r.number for r in table.rows] == [2]

    def test_a_declaration_in_a_utf16_part_is_refused(self, tmp_path):
        body = (
            '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "a">]>' + sheet("")
        ).encode("utf-16")
        buffer = io.BytesIO(workbook({"S": sheet("")}))
        rebuilt = io.BytesIO()
        with zipfile.ZipFile(buffer) as source, zipfile.ZipFile(rebuilt, "w") as target:
            for info in source.infolist():
                data = body if info.filename == "xl/worksheets/sheet1.xml" else source.read(info)
                target.writestr(info.filename, data)
        with pytest.raises(ExtractionError, match="document type declaration"):
            read_workbook(write(tmp_path, rebuilt.getvalue()))

    def test_a_declaration_after_a_long_comment_is_refused(self, tmp_path):
        body = '<?xml version="1.0"?><!--' + "p" * 6000 + '--><!DOCTYPE x [<!ENTITY a "a">]>'
        body += sheet("")
        with pytest.raises(ExtractionError, match="document type declaration"):
            read_workbook(write(tmp_path, workbook({"S": body})))

    def test_the_xml_spreadsheet_reader_refuses_it_too(self):
        source = (
            '<?xml version="1.0"?><!--' + "p" * 6000 + '--><!DOCTYPE x [<!ENTITY a "a">]>'
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"/>'
        )
        with pytest.raises(ExtractionError, match="document type declaration"):
            extraction.read_tables(source)

    def test_a_negative_string_index_is_a_damaged_workbook(self, tmp_path):
        rows = '<row r="1"><c r="A1" t="s"><v>-1</v></c></row>'
        with pytest.raises(ExtractionError, match="damaged"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)}, strings=["only"])))

    def test_a_corrupt_compressed_part_is_an_extraction_error(self, tmp_path):
        """zlib's own error used to escape, failing the whole upload rather
        than costing the one file, against `extract_all`'s contract."""
        rows = "".join(f'<row r="{n}"><c r="A{n}"><v>{n}</v></c></row>' for n in range(1, 400))
        data = bytearray(workbook({"S": sheet(rows)}))
        start = data.find(b"xl/worksheets/sheet1.xml") + 60
        data[start : start + 40] = b"\xff" * 40
        with pytest.raises(ExtractionError):
            read_workbook(write(tmp_path, bytes(data)))

    def test_a_structural_part_is_small_or_refused(self, tmp_path):
        """The relationships and the workbook are a few KB in any real file,
        and are parsed whole, so they get a small cap of their own."""
        padding = "<!--" + "x" * (2 * 1024 * 1024) + "-->"
        book = workbook({"S": sheet("")}, properties=padding)
        with pytest.raises(ExtractionError, match="too large"):
            read_workbook(write(tmp_path, book))


class TestOtherWorkbookShapes:
    def test_a_password_protected_xlsx_says_so(self, tmp_path):
        """Excel wraps an encrypted .xlsx in an OLE container, not a zip, and
        it was told it 'may be damaged — download it again'."""
        path = write(tmp_path, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504, "details.xlsx")
        with pytest.raises(ExtractionError, match="password"):
            extract(doc(path))

    def test_a_strict_open_xml_workbook_says_how_to_re_save_it(self, tmp_path):
        source = zipfile.ZipFile(io.BytesIO(workbook({"S": sheet("")})))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as target:
            for info in source.infolist():
                body = source.read(info).replace(
                    MAIN.encode(), b"http://purl.oclc.org/ooxml/spreadsheetml/main"
                )
                target.writestr(info.filename, body)
        strict = buffer.getvalue()
        with pytest.raises(ExtractionError, match="Strict Open XML"):
            extract(doc(write(tmp_path, strict)))

    def test_sanitize_reduces_a_workbook_to_its_shape(self, tmp_path):
        from unbagged.sanitize import sanitize_file

        skeleton = sanitize_file(write(tmp_path, GENERATOR.build_workbook()))
        assert skeleton["format"] == "xlsx"
        transactions = skeleton["sheets"][0]
        assert transactions["rows"] > 3
        assert "TXN_DTE" in str(transactions["header"])


class TestSecondPassFindings:
    """From the second review pass, each demonstrated against the first fix."""

    def test_a_declaration_behind_a_deferred_comment_is_refused(self, tmp_path):
        # Past ~131 KB a prolog comment is deferred across chunks by expat, and
        # the guard never parsed the tail; an entity was expanded into a cell.
        body = '<?xml version="1.0"?><!--' + "p" * 200_000 + "-->"
        body += '<!DOCTYPE x [<!ENTITY e "X">]>' + sheet(
            '<row r="1"><c r="A1" t="inlineStr"><is><t>&e;</t></is></c></row>'
        )
        with pytest.raises(ExtractionError, match="document type declaration"):
            read_workbook(write(tmp_path, workbook({"S": body})))

    def test_the_xml_spreadsheet_reader_refuses_it_too(self):
        source = (
            '<?xml version="1.0"?><!--' + "p" * 200_000 + '--><!DOCTYPE x [<!ENTITY e "X">]>'
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"/>'
        )
        with pytest.raises(ExtractionError, match="document type declaration"):
            extraction.read_tables(source)

    def test_the_xml_spreadsheet_reader_keeps_rows_its_parser_deferred(self):
        # Events the parser only produced at close() were never read: a sheet
        # behind a long comment came back as no sheets at all.
        source = (
            '<?xml version="1.0"?><!--' + "p" * 200_000 + "-->"
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">'
            '<Worksheet Name="S"><Table><Row><Cell><Data>1</Data></Cell></Row></Table>'
            "</Worksheet></Workbook>"
        )
        (table,) = extraction.read_tables(source).tables
        assert [row.cells for row in table.rows] == [("1",)]

    def test_one_row_cannot_hold_more_cells_than_a_sheet_has_columns(self, tmp_path):
        # Repeating one reference charged one slot however many cells the row
        # held: 5M of them measured 2.1 GB inside a single row.
        rows = '<row r="1">' + '<c r="A1"/>' * 20_000 + "</row>"
        with pytest.raises(ExtractionError, match="column"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)})))

    def test_nor_can_a_row_of_the_xml_kind(self):
        source = (
            '<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">'
            '<Worksheet Name="S"><Table><Row>' + "<Cell/>" * 20_000 + "</Row></Table>"
            "</Worksheet></Workbook>"
        )
        with pytest.raises(ExtractionError, match="column"):
            extraction.read_tables(source)

    def test_an_absurd_dimension_is_ignored_not_fatal(self, tmp_path):
        rows = '<row r="1"><c r="A1"><v>1</v></c></row>'
        extra = f'<dimension ref="A1:J{"9" * 5000}"/>'
        (table,) = read_workbook(write(tmp_path, workbook({"S": sheet(rows, extra=extra)}))).tables
        assert table.declared_rows is None


XML_SPREADSHEET = "urn:schemas-microsoft-com:office:spreadsheet"


def xml_spreadsheet(table: str) -> str:
    return (
        f'<?xml version="1.0"?><Workbook xmlns="{XML_SPREADSHEET}">'
        f'<Worksheet Name="S"><Table>{table}</Table></Worksheet></Workbook>'
    )


class TestThirdPassFindings:
    """From the third review pass, each demonstrated against the second fix."""

    def test_rows_among_other_elements_are_freed_in_linear_time(self):
        # Only rows were detached, so every other element stayed in the table
        # and each row's removal scanned past all of them: 1.2 MB took 109 s.
        import time

        source = xml_spreadsheet("<Row/><Column/>" * 40_000)
        began = time.perf_counter()
        extraction.read_tables(source)
        assert time.perf_counter() - began < 3

    def test_what_one_row_holds_is_budgeted_at_every_depth(self, monkeypatch):
        # Only a row's direct children were counted; deep inside one cell the
        # elements were free, and measured 1.9 GB.
        monkeypatch.setattr(extraction, "MAX_ROW_ELEMENTS", 100)
        cell = "<Cell><Data>" + "<B/>" * 200 + "</Data></Cell>"
        with pytest.raises(ExtractionError, match="elements"):
            extraction.read_tables(xml_spreadsheet(f"<Row>{cell}</Row>"))

    def test_so_is_what_an_element_beside_the_rows_holds(self, monkeypatch):
        monkeypatch.setattr(extraction, "MAX_ROW_ELEMENTS", 100)
        with pytest.raises(ExtractionError, match="elements"):
            extraction.read_tables(xml_spreadsheet("<Column>" + "<X/>" * 200 + "</Column>"))

    def test_and_in_a_workbook_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extraction, "MAX_ROW_ELEMENTS", 100)
        rows = '<row r="1"><c r="A1" t="inlineStr"><is>' + "<r/>" * 200 + "</is></c></row>"
        with pytest.raises(ExtractionError, match="elements"):
            read_workbook(write(tmp_path, workbook({"S": sheet(rows)})))

    def test_and_in_a_shared_string(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extraction, "MAX_ROW_ELEMENTS", 100)
        rows = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        book = workbook({"S": sheet(rows)}, strings=["<r/>" * 200])
        with pytest.raises(ExtractionError, match="elements"):
            read_workbook(write(tmp_path, book))

    def test_nesting_is_bounded(self):
        cell = "<Cell><Data>" + "<B>" * 500 + "</B>" * 500 + "</Data></Cell>"
        with pytest.raises(ExtractionError, match="nested"):
            extraction.read_tables(xml_spreadsheet(f"<Row>{cell}</Row>"))

    def test_a_real_rich_text_cell_still_reads(self):
        cell = '<Cell><Data ss:Type="String" xmlns:html="http://www.w3.org/TR/REC-html40">'
        cell += "<html:B><html:U>Banner</html:U></html:B></Data></Cell>"
        source = xml_spreadsheet(f"<Row>{cell}</Row>").replace(
            "<Workbook ", f'<Workbook xmlns:ss="{XML_SPREADSHEET}" '
        )
        (table,) = extraction.read_tables(source).tables
        assert table.rows[0].cells == ("Banner",)

    @pytest.mark.parametrize("encoding", ["bogus", "utf-7", "idna", "rot13"])
    def test_an_unreadable_encoding_costs_the_one_file(self, tmp_path, encoding):
        # LookupError and ValueError escaped every reader, failing the whole
        # upload rather than the one file, against `extract_all`'s contract.
        body = f'<?xml version="1.0" encoding="{encoding}"?>' + sheet("")
        with pytest.raises(ExtractionError, match="encoding"):
            read_workbook(write(tmp_path, workbook({"S": body})))

    def test_an_unreadable_encoding_in_a_structural_part(self, tmp_path):
        buffer = io.BytesIO(workbook({"S": sheet("")}))
        rebuilt = io.BytesIO()
        with zipfile.ZipFile(buffer) as source, zipfile.ZipFile(rebuilt, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "_rels/.rels":
                    data = b'<?xml version="1.0" encoding="bogus"?>' + data
                target.writestr(info.filename, data)
        path = write(tmp_path, rebuilt.getvalue())
        assert looks_like_xlsx(path) is False
        with pytest.raises(ExtractionError, match="encoding"):
            read_workbook(path)
