"""Merged cells, and the columns they swallow.

Regression: ISSUE-001 — a cell carrying ss:MergeAcross occupies the columns it
spans, and those columns are not written. Reading the next element as the next
column shifts every value after it one or more fields left, against a dense
header row, with no error and no warning.
Found by /qa on 2026-09-09; fixed in 822c97b.

The same class of bug as `ss:Index`, and the one `tests/test_extraction.py`
already guards. This is the other half of it.
"""

import pytest

from unbagged import extraction
from unbagged.adapters.hmart.adapter import HMartAdapter
from unbagged.models import SourceBundle, SourceDocument

SS = "urn:schemas-microsoft-com:office:spreadsheet"

HEADERS = ("Smartcard", "Date of Purchase", "Branch", "Amount", "Point")


def _cell(text: str, *, merge: int | None = None, index: int | None = None,
          extra: str = "") -> str:
    at = ""
    if index is not None:
        at += f' ss:Index="{index}"'
    if merge is not None:
        at += f' ss:MergeAcross="{merge}"'
    return (
        f"<ss:Cell{at}><ss:Data ss:Type=\"String\">{text}</ss:Data>{extra}</ss:Cell>"
    )


def _sheet(rows: str, name: str = "Workbook", attrs: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{SS}">'
        f'<ss:Worksheet ss:Name="{name}"><ss:Table {attrs}>{rows}'
        f"</ss:Table></ss:Worksheet></ss:Workbook>"
    )


def _row(cells: str, index: int | None = None) -> str:
    at = f' ss:Index="{index}"' if index else ""
    return f"<ss:Row{at}>{cells}</ss:Row>"


def bundle(tmp_path, text: str, name: str = "history.xls") -> SourceBundle:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return SourceBundle(documents=(SourceDocument(name, "0" * 64, path=str(path)),))


class TestMergeAcross:
    def test_a_merged_cell_occupies_the_columns_it_spans(self):
        # MergeAcross="1" means this cell is columns 1 AND 2, so the element
        # after it is column 3 — not column 2.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", merge=1) + _cell("c")))
        ).tables[0]
        assert table.rows[0].cells == ("a", None, "c")

    def test_a_span_of_four_leaves_the_next_cell_at_column_six(self):
        # The width the observed H Mart banner row merges across.
        table = extraction.read_tables(
            _sheet(_row(_cell("banner", merge=4) + _cell("f")))
        ).tables[0]
        assert table.rows[0].cells == ("banner", None, None, None, None, "f")

    def test_an_explicit_index_after_a_span_still_wins(self):
        # ss:Index is written against real columns, so it overrides the span's
        # arithmetic rather than being added to it.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", merge=2) + _cell("e", index=5)))
        ).tables[0]
        assert table.rows[0].cells == ("a", None, None, None, "e")

    @pytest.mark.parametrize("span", ["-3", "not a number", ""])
    def test_an_unusable_span_is_ignored_rather_than_walking_backwards(self, span):
        # A negative span would place the next cell over one already written,
        # which loses a value outright — worse than ignoring the attribute.
        table = extraction.read_tables(
            _sheet(_row(f'<ss:Cell ss:MergeAcross="{span}">'
                        '<ss:Data ss:Type="String">a</ss:Data></ss:Cell>'
                        + _cell("b")))
        ).tables[0]
        assert table.rows[0].cells == ("a", "b")

    def test_a_merged_cell_does_not_widen_the_row_it_ends(self):
        # Nothing follows the span, so nothing is placed in it. Padding here
        # would make a one-cell banner row report five columns and put
        # `_check_declared` at odds with a sheet that is not wrong.
        table = extraction.read_tables(
            _sheet(_row(_cell("banner", merge=4)))
        ).tables[0]
        assert table.rows[0].cells == ("banner",)


class TestAMergedCellDoesNotCostABasketItsCents:
    """The failure this is really about, at the adapter's own boundary."""

    def _document(self, merge: bool) -> str:
        # Branch spans Branch and Amount, so Amount is inside the merge and the
        # points value sits at column 5 where the header says Point.
        branch = _cell("SOME BRANCH", merge=1) if merge else _cell("SOME BRANCH")
        data = _cell("40100200300") + _cell("2020-01-18 09:59:00.0") + branch
        if not merge:
            data += _cell("12.34")
        data += _cell("12")
        return _sheet(
            _row("".join(_cell(h) for h in HEADERS)) + _row(data),
            attrs='ss:ExpandedColumnCount="5" ss:ExpandedRowCount="2"',
        )

    def test_the_points_value_is_not_read_as_the_amount(self, tmp_path):
        parsed = HMartAdapter().parse(
            bundle(tmp_path, self._document(merge=True))
        )
        (txn,) = parsed.transactions
        # Shifted, this held 12.0 — the amount rounded — and looked like a
        # perfectly ordinary basket total.
        assert txn.total_pre_discount is None
        assert any("no readable amount" in w.message for w in parsed.warnings)

    def test_the_unmerged_row_is_read_the_way_it_always_was(self, tmp_path):
        parsed = HMartAdapter().parse(
            bundle(tmp_path, self._document(merge=False))
        )
        (txn,) = parsed.transactions
        assert txn.total_pre_discount == pytest.approx(12.34)
        assert txn.store_code == "SOME BRANCH"


class TestTheBannerCellAsTheExportActuallyWritesIt:
    """The observed banner: a span of four, markup inside ss:Data, and a
    non-Data child after it. All three sat between the reader and the header."""

    def test_the_header_is_still_found_under_a_merged_marked_up_banner(self, tmp_path):
        banner = (
            '<ss:Cell ss:StyleID="title" ss:MergeAcross="4">'
            '<ss:Data xmlns:html="http://www.w3.org/TR/REC-html40"'
            ' ss:Type="String"><html:B><html:U>Purchase</html:U></html:B>'
            " History</ss:Data>"
            '<ss:NamedCell ss:Name="Print_Titles" /></ss:Cell>'
        )
        document = _sheet(
            _row(banner)
            + _row("".join(_cell(h) for h in HEADERS))
            + _row(
                _cell("40100200300") + _cell("2020-01-18 09:59:00.0")
                + _cell("SOME BRANCH") + _cell("12.34") + _cell("12")
            ),
            attrs='ss:ExpandedColumnCount="5" ss:ExpandedRowCount="3"',
        )
        assert HMartAdapter().sniff(bundle(tmp_path, document)) == 0.9
        parsed = HMartAdapter().parse(bundle(tmp_path, document))
        assert len(parsed.transactions) == 1
        assert parsed.transactions[0].total_pre_discount == pytest.approx(12.34)

    def test_markup_inside_a_cell_is_read_as_its_text(self):
        # The text is split across three elements; a reader taking only
        # `Data.text` would see the first fragment and stop.
        table = extraction.read_tables(
            _sheet(_row(
                '<ss:Cell><ss:Data xmlns:html="http://www.w3.org/TR/REC-html40"'
                ' ss:Type="String">a<html:B>b</html:B>c</ss:Data></ss:Cell>'
            ))
        ).tables[0]
        assert table.rows[0].cells == ("abc",)

    def test_a_child_after_ss_data_does_not_displace_the_value(self):
        table = extraction.read_tables(
            _sheet(_row(_cell("v", extra='<ss:NamedCell ss:Name="Print_Titles" />')))
        ).tables[0]
        assert table.rows[0].cells == ("v",)
