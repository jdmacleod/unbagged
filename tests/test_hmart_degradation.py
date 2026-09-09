"""What the H Mart path does with input that is wrong in a specific way.

Every case here is a thing that will actually arrive: a workbook saved in the
other Excel format, a download that stopped, a sheet with a header and nothing
under it. The rule the whole file tests is that each one produces a different
message, because each one has a different fix and telling someone the wrong one
sends them to solve a problem they do not have.
"""

from pathlib import Path

import pytest

from unbagged.adapters.hmart.adapter import HMartAdapter
from unbagged.extraction import ExtractionError, extract, read_tables
from unbagged.models import AdapterError, SourceBundle, SourceDocument

SS = "urn:schemas-microsoft-com:office:spreadsheet"
FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "hmart" / "fixtures" / "synthetic_history.xls"
)


def sheet(rows: str, name: str = "Workbook", attrs: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{SS}">'
        f'<ss:Worksheet ss:Name="{name}"><ss:Table {attrs}>{rows}'
        f"</ss:Table></ss:Worksheet></ss:Workbook>"
    )


def row(*values: str | None) -> str:
    cells = "".join(
        "" if v is None else f'<ss:Cell><ss:Data ss:Type="String">{v}</ss:Data></ss:Cell>'
        for v in values
    )
    return f"<ss:Row>{cells}</ss:Row>"


HEADER = row("Smartcard", "Date of Purchase", "Branch", "Amount", "Point")


def doc(tmp_path, text: str, name: str = "history.xls") -> SourceDocument:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return SourceDocument(name, "0" * 64, path=str(path))


class TestTheFileIsWrongInSomeWay:
    def test_a_real_excel_workbook_is_told_how_to_re_save_it(self, tmp_path):
        # A binary BIFF workbook wears the same extension and is not this
        # format. Advertising `.xls` in the picker makes this reachable, so the
        # message has to be worth reaching.
        path = tmp_path / "book.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
        with pytest.raises(ExtractionError, match="XML Spreadsheet 2003"):
            extract(SourceDocument("book.xls", "0" * 64, path=str(path)))

    def test_a_truncated_download_says_to_download_it_again(self, tmp_path):
        # Distinct from "this reader cannot handle it": a short file is the
        # uploader's to re-fetch, and telling them otherwise wastes their time.
        whole = FIXTURE.read_text(encoding="utf-8")
        with pytest.raises(ExtractionError, match="incomplete"):
            extract(doc(tmp_path, whole[: len(whole) // 2]))

    def test_a_document_type_declaration_is_refused(self, tmp_path):
        # Internal entity expansion is live on this Python; a retailer export
        # carries no DTD, so this cannot refuse a real file.
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;">]>'
            f'<ss:Workbook xmlns:ss="{SS}"><r>&lol2;</r></ss:Workbook>'
        )
        with pytest.raises(ExtractionError, match="document type declaration"):
            extract(doc(tmp_path, bomb))

    def test_the_declaration_is_looked_for_in_the_prolog_only(self):
        # Scanning the whole document would undo the bounded read beside it, and
        # a DOCTYPE must precede the root element anyway, so anything past the
        # prolog cannot be one. Padded so the text really is beyond the window
        # rather than merely late in a short file.
        padding = "".join(
            row("c", "2024-01-01 00:00:00.0", "b", "1.00", "1") for _ in range(60)
        )
        late = sheet(HEADER + padding).replace(
            "</ss:Table>", "<!-- the text <!DOCTYPE x> appears here --></ss:Table>"
        )
        assert late.index("DOCTYPE") > 4096
        assert read_tables(late).tables


class TestTheSheetIsWrongInSomeWay:
    def test_a_header_with_no_rows_under_it_is_a_finding_not_an_error(self, tmp_path):
        # An export with nothing in it says something about the response. It
        # must not arrive as an exception.
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(doc(tmp_path, sheet(HEADER)),))
        )
        assert parsed.transactions == ()
        assert any("no readable purchases" in w.message for w in parsed.warnings)
        assert parsed.missing_categories() == ()

    def test_a_renamed_column_is_refused_rather_than_read_positionally(self, tmp_path):
        renamed = sheet(
            row("Smartcard", "Date of Purchase", "Store", "Amount", "Point")
        )
        with pytest.raises(AdapterError, match="does not carry the columns"):
            HMartAdapter().parse(SourceBundle(documents=(doc(tmp_path, renamed),)))

    def test_a_row_with_no_date_is_skipped_and_said_so(self, tmp_path):
        body = sheet(HEADER + row("c", "not a date", "b", "1.00", "1"))
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(doc(tmp_path, body),))
        )
        assert parsed.transactions == ()
        assert any("no readable date" in w.message for w in parsed.warnings)

    def test_a_row_with_no_amount_is_kept_without_one(self, tmp_path):
        # The visit happened. Dropping it because one cell is unreadable would
        # lose a fact the response did give.
        body = sheet(HEADER + row("c", "2024-01-01 09:00:00.0", "b", "junk", "1"))
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(doc(tmp_path, body),))
        )
        assert len(parsed.transactions) == 1
        assert parsed.transactions[0].total_pre_discount is None
        assert any("no readable amount" in w.message for w in parsed.warnings)

    def test_one_bad_row_does_not_cost_the_others(self, tmp_path):
        body = sheet(
            HEADER
            + row("c", "2024-01-01 09:00:00.0", "b", "1.00", "1")
            + row("c", "not a date", "b", "2.00", "2")
            + row("c", "2024-01-03 09:00:00.0", "b", "3.00", "3")
        )
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(doc(tmp_path, body),))
        )
        assert len(parsed.transactions) == 2

    def test_a_sheet_declaring_more_rows_than_it_has_is_noted(self, tmp_path):
        body = sheet(HEADER + row("c", "2024-01-01 09:00:00.0", "b", "1.00", "1"),
                     attrs='ss:ExpandedRowCount="99"')
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(doc(tmp_path, body),))
        )
        # The sheet's own declaration is the only free check on whether the
        # reader lost rows, and losing rows silently is the failure that matters.
        assert any("declares 99 rows" in w.message for w in parsed.warnings)


class TestTheAmountColumn:
    @pytest.mark.parametrize(
        "text,expected",
        [("13.84", 13.84), ("38.7", 38.7), ("1,234.56", 1234.56), ("$5.00", 5.0)],
    )
    def test_amounts_arrive_as_strings_and_are_coerced(self, tmp_path, text, expected):
        body = sheet(HEADER + row("c", "2024-01-01 09:00:00.0", "b", text, "1"))
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(doc(tmp_path, body),))
        )
        assert parsed.transactions[0].total_pre_discount == expected
