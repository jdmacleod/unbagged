"""Merged cells, and the columns they swallow.

Regression: ISSUE-001 — a cell carrying ss:MergeAcross occupies the columns it
spans, and those columns are not written. Reading the next element as the next
column shifts every value after it one or more fields left, against a dense
header row, with no error and no warning.
Found by /qa on 2026-09-09; fixed in 822c97b.

`ss:MergeDown` is the same corruption across rows and was left open as #41,
because handling it means carrying spans BETWEEN rows and `read_tables` kept no
cross-row state at all. The observed export has none — one `MergeAcross="4"` on
the banner and nothing else — so every case below rests on the SpreadsheetML
rule rather than on the sample, which is the footing #44 asks new fixes to
stand on.

The same class of bug as `ss:Index`, and the one `tests/test_extraction.py`
already guards. This is the rest of it.
"""

import pytest

from unbagged import extraction
from unbagged.adapters.hmart.adapter import HMartAdapter
from unbagged.models import SourceBundle, SourceDocument

SS = "urn:schemas-microsoft-com:office:spreadsheet"

HEADERS = ("Smartcard", "Date of Purchase", "Branch", "Amount", "Point")


def _cell(
    text: str,
    *,
    merge: int | None = None,
    down: int | None = None,
    index: int | None = None,
    extra: str = "",
) -> str:
    at = ""
    if index is not None:
        at += f' ss:Index="{index}"'
    if merge is not None:
        at += f' ss:MergeAcross="{merge}"'
    if down is not None:
        at += f' ss:MergeDown="{down}"'
    return f'<ss:Cell{at}><ss:Data ss:Type="String">{text}</ss:Data>{extra}</ss:Cell>'


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
        table = extraction.read_tables(_sheet(_row(_cell("a", merge=1) + _cell("c")))).tables[0]
        assert table.rows[0].cells == ("a", None, "c")

    def test_a_span_of_four_leaves_the_next_cell_at_column_six(self):
        # The width the observed H Mart banner row merges across.
        table = extraction.read_tables(_sheet(_row(_cell("banner", merge=4) + _cell("f")))).tables[
            0
        ]
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
            _sheet(
                _row(
                    f'<ss:Cell ss:MergeAcross="{span}">'
                    '<ss:Data ss:Type="String">a</ss:Data></ss:Cell>' + _cell("b")
                )
            )
        ).tables[0]
        assert table.rows[0].cells == ("a", "b")

    def test_a_merged_cell_does_not_widen_the_row_it_ends(self):
        # Nothing follows the span, so nothing is placed in it. Padding here
        # would make a one-cell banner row report five columns and put
        # `_check_declared` at odds with a sheet that is not wrong.
        table = extraction.read_tables(_sheet(_row(_cell("banner", merge=4)))).tables[0]
        assert table.rows[0].cells == ("banner",)


class TestAMergedCellDoesNotCostABasketItsCents:
    """The failure this is really about, at the adapter's own boundary."""

    def _document(self, merge: bool) -> str:
        # Branch spans Branch and Amount, so Amount is inside the merge and the
        # points value sits at column 5 where the header says Point.
        branch = _cell("SOME BRANCH", merge=1) if merge else _cell("SOME BRANCH")
        data = _cell("40100200300") + _cell("2019-03-04 11:07:00.0") + branch
        if not merge:
            data += _cell("12.34")
        data += _cell("12")
        return _sheet(
            _row("".join(_cell(h) for h in HEADERS)) + _row(data),
            attrs='ss:ExpandedColumnCount="5" ss:ExpandedRowCount="2"',
        )

    def test_the_points_value_is_not_read_as_the_amount(self, tmp_path):
        parsed = HMartAdapter().parse(bundle(tmp_path, self._document(merge=True)))
        (txn,) = parsed.transactions
        # Shifted, this held 12.0 — the amount rounded — and looked like a
        # perfectly ordinary basket total.
        assert txn.total_pre_discount is None
        assert any("no readable amount" in w.message for w in parsed.warnings)

    def test_the_unmerged_row_is_read_the_way_it_always_was(self, tmp_path):
        parsed = HMartAdapter().parse(bundle(tmp_path, self._document(merge=False)))
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
                _cell("40100200300")
                + _cell("2019-03-04 11:07:00.0")
                + _cell("SOME BRANCH")
                + _cell("12.34")
                + _cell("12")
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
            _sheet(
                _row(
                    '<ss:Cell><ss:Data xmlns:html="http://www.w3.org/TR/REC-html40"'
                    ' ss:Type="String">a<html:B>b</html:B>c</ss:Data></ss:Cell>'
                )
            )
        ).tables[0]
        assert table.rows[0].cells == ("abc",)

    def test_a_child_after_ss_data_does_not_displace_the_value(self):
        table = extraction.read_tables(
            _sheet(_row(_cell("v", extra='<ss:NamedCell ss:Name="Print_Titles" />')))
        ).tables[0]
        assert table.rows[0].cells == ("v",)


class TestMergeDown:
    """A cell spanning downward, and the rows it reaches into.

    The rule is the one `MergeAcross` follows, turned ninety degrees: the
    positions a merged region covers are omitted from the sequence rather than
    written blank. Confirmed against the SpreadsheetML specification rather than
    against the corpus, which contains none — #44 is explicit that a fix resting
    on the spec is worth more here than one resting on a sample of one.
    """

    def test_a_column_held_from_above_is_skipped_in_the_row_beneath(self):
        # `a` spans this row and the next, so the next row writes only `d`,
        # and `d` belongs in column 2. Read as the first column, it lands in
        # `a`'s — and every value after it follows one to the left.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", down=1) + _cell("b")) + _row(_cell("d")))
        ).tables[0]
        assert table.rows[0].cells == ("a", "b")
        assert table.rows[1].cells == (None, "d")

    def test_the_span_ends_when_it_says_it_does(self):
        # MergeDown="1" reaches exactly one row below. The row after that is
        # ordinary, and holding the span open costs it its first column.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", down=1)) + _row(_cell("b")) + _row(_cell("c")))
        ).tables[0]
        assert table.rows[1].cells == (None, "b")
        assert table.rows[2].cells == ("c",)

    def test_a_span_of_three_holds_every_row_it_covers(self):
        rows = _row(_cell("a", down=3)) + "".join(_row(_cell(v)) for v in "bcde")
        table = extraction.read_tables(_sheet(rows)).tables[0]
        assert [r.cells for r in table.rows[1:4]] == [(None, "b"), (None, "c"), (None, "d")]
        assert table.rows[4].cells == ("e",)

    def test_a_region_merged_both_ways_blocks_every_column_it_covers(self):
        # MergeAcross="1" and MergeDown="1" is a 2x2 region: columns 1 and 2 of
        # the row beneath are both inside it, so `c` is column 3.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", merge=1, down=1)) + _row(_cell("c")))
        ).tables[0]
        assert table.rows[1].cells == (None, None, "c")

    def test_an_explicit_index_still_wins_over_a_span_from_above(self):
        # ss:Index names a real column, so it is never adjusted — the same rule
        # that already holds against MergeAcross.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", down=1)) + _row(_cell("held", index=1) + _cell("b")))
        ).tables[0]
        assert table.rows[1].cells == ("held", "b")

    def test_two_open_spans_are_both_stepped_over(self):
        table = extraction.read_tables(
            _sheet(_row(_cell("a", down=1) + _cell("b", down=1) + _cell("c")) + _row(_cell("f")))
        ).tables[0]
        assert table.rows[1].cells == (None, None, "f")

    def test_a_row_index_that_skips_past_a_span_leaves_it_behind(self):
        # `ss:Index` on a Row jumps rows. A countdown would still be holding
        # this span open when the read arrives three rows further down, which is
        # why the span is keyed on the last row it covers.
        table = extraction.read_tables(
            _sheet(_row(_cell("a", down=1)) + _row(_cell("far"), index=6))
        ).tables[0]
        assert table.rows[1].number == 6
        assert table.rows[1].cells == ("far",)

    @pytest.mark.parametrize("span", ["-2", "not a number", ""])
    def test_an_unusable_span_is_ignored_rather_than_blocking_backwards(self, span):
        table = extraction.read_tables(
            _sheet(
                _row(
                    f'<ss:Cell ss:MergeDown="{span}">'
                    '<ss:Data ss:Type="String">a</ss:Data></ss:Cell>'
                )
                + _row(_cell("b"))
            )
        ).tables[0]
        assert table.rows[1].cells == ("b",)

    def test_a_span_does_not_reach_into_the_next_worksheet(self):
        document = (
            f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{SS}">'
            f'<ss:Worksheet ss:Name="one"><ss:Table>{_row(_cell("a", down=3))}'
            f"</ss:Table></ss:Worksheet>"
            f'<ss:Worksheet ss:Name="two"><ss:Table>{_row(_cell("b"))}'
            f"</ss:Table></ss:Worksheet></ss:Workbook>"
        )
        second = extraction.read_tables(document).tables[1]
        assert second.rows[0].cells == ("b",)


class TestAMergeDownDoesNotCostABasketItsCents:
    """The same failure as the MergeAcross case, arriving from the row above.

    `Point == round(Amount)` is what makes this quiet: a row shifted one column
    left reads the points value as the amount, and the points value is the
    amount rounded. The result is a plausible basket total that is wrong by less
    than a dollar, which no arithmetic downstream can catch.
    """

    def _document(self) -> str:
        header = _row("".join(_cell(h) for h in HEADERS))
        # Smartcard spans both data rows, so the second row omits it and every
        # value in it lands one column to the left: the date reads as the card,
        # the branch as the date, and the points as the amount.
        first = _row(
            _cell("40100200300", down=1)
            + _cell("2019-03-04 11:07:00.0")
            + _cell("SOME BRANCH")
            + _cell("12.34")
            + _cell("12")
        )
        second = _row(
            _cell("2019-03-05 09:15:00.0") + _cell("SOME BRANCH") + _cell("56.78") + _cell("57")
        )
        return _sheet(
            header + first + second,
            attrs='ss:ExpandedColumnCount="5" ss:ExpandedRowCount="3"',
        )

    def test_the_row_under_a_span_keeps_its_own_amount(self, tmp_path):
        parsed = HMartAdapter().parse(bundle(tmp_path, self._document()))
        totals = sorted(t.total_pre_discount for t in parsed.transactions)
        # Shifted, the second basket read 57.0 — its points value, which is its
        # amount rounded — and looked like an ordinary total.
        assert totals == pytest.approx([12.34, 56.78])
