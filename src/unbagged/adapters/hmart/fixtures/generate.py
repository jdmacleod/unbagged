"""Generate a structurally faithful, entirely fabricated H Mart export.

Seeded from the *structure* of one real response, never from its values. Every
value here is invented. Nothing in this file was copied from a real export.

What the adapter has to survive, and therefore what this reproduces:

* SpreadsheetML 2003 under an ``.xls`` extension, with the element order the
  real export uses: DocumentProperties, ExcelWorkbook, Styles, then Worksheet
* a banner row above the header row whose single cell is **merged across the
  full width** with ``ss:MergeAcross``, carries its text inside ``html:B`` /
  ``html:U`` / ``html:Font`` markup, and is followed by an ``ss:NamedCell``
  sibling rather than ending at its ``ss:Data``. All three are what the real
  export writes, and all three stand between a reader and the header row. The
  merge is the one that bites: a merged cell occupies the columns it spans and
  those columns are not written, so a reader taking the next element as the
  next column shifts every value after it — the same corruption ``ss:Index``
  causes, from a different attribute. The banner was previously generated as a
  plain single cell, which is exactly why nothing caught it.

  **This row cannot catch the shift on its own, and neither can the real
  export.** Nothing follows the banner's merged cell, so there is no value left
  to displace; a reader that ignores ``ss:MergeAcross`` entirely reads this
  fixture correctly. What it reproduces is the shape arriving at all. The
  displacement is exercised in ``tests/test_merged_cells.py`` against
  documents built for it, because putting a merged cell mid-row here would
  mean a data row that legitimately has no amount, and the fixture-wide
  invariant that every visit carries a total is worth more than the overlap
* ``ss:StyleID`` on every cell, and the ``ss:Column`` widths the real export
  writes ahead of its rows
* the worksheet's structural siblings — ``ss:Names`` before the table and
  ``x:WorksheetOptions`` after it — because they are what a reader meets
  immediately before the first row and immediately after the last
* ``ss:Type="String"`` on every cell, including dates, amounts and points
* a Java ``Timestamp.toString()`` date: ``YYYY-MM-DD HH:MM:SS.0``, seconds
  always ``00``, so the real resolution is minutes
* an amount whose trailing zero is stripped, so a one-decimal form occurs
* ``Point == round(Amount)`` on every row
* more than one branch, unevenly distributed, and one long enough to exercise
  the basket row's measured width budget
* a multi-year window with months carrying no purchase at all
* **sparse rows using ``ss:Index``**, which the real file does not contain. It
  is generated anyway because the format permits it and a reader that ignores
  it shifts every column after the gap: measured on a constructed row, the
  amount lands in the store field and a basket total drops to its rounded
  points value, with no error anywhere. A fixture that cannot produce a shape
  cannot test the code that handles it.

Two deliberate departures from the observed file, both recorded in NOTES.md:

* the real export is a single line with no newlines at all. This is written
  newline-separated, because every ``scan_pii`` finding in a one-line file
  reports line 1 and an inline ``pii-scan: allow`` suppression becomes
  impossible. ``tests/test_hmart_adapter.py`` feeds the reader the newline-free
  form, so the dimension the fixture is unfaithful in is still covered.
* the scale differs from the reference file, as the Kroger generator's does, so
  the fixture cannot be mistaken for a reproduction of a real response.

One class of difference is left in place on purpose: the bodies of the styles
themselves — ``ss:Font``, ``ss:Border``, ``ss:Interior``, ``ss:NumberFormat``.
Nothing reads them, they cannot move a value into the wrong column, and
generating them would add bytes and diff noise to every regeneration for no
coverage. The style *identifiers* are reproduced, because those appear as an
attribute on every cell.

Output is deterministic: the same seed produces byte-identical text.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

DEFAULT_SEED = 20260101
FILENAME = "synthetic_history.xls"

SS = "urn:schemas-microsoft-com:office:spreadsheet"

HEADERS = ("Smartcard", "Date of Purchase", "Branch", "Amount", "Point")

# The style names the real export uses. Carried because they put an attribute
# on every cell, which is what a reader walking `ss:Cell` children has to step
# over, and because the banner's `title` is the cell that is merged.
BANNER_STYLE = "title"
HEADER_STYLE = "headercell"
ROW_STYLES = ("odd", "even")

#: Invented, and deliberately not a heading any real export uses.
BANNER_TEXT = "Smart Card Purchase History"

# Places that do not exist, so a fabricated branch cannot name a real store.
# The long one is deliberate: the basket row's width budget is measured at
# 375px and a store NAME is longer than the numeric code it was measured with.
BRANCHES = ("Thistlewick", "Kelmarsh Cross", "Ombersley Reach North")

# Weighted so one branch dominates, as the real export's did.
BRANCH_WEIGHTS = (0.80, 0.15, 0.05)

# 11 digits, as observed. Deliberately fails a Luhn check.
#
# At this width it matches neither scanner rule — PAYMENT_CARD wants 13-19
# digits and LOYALTY_NUMBER 12-14 — so no Luhn check is reached today and the
# trap cannot fire. The constraint exists because the scale is meant to differ
# from the reference file, which makes a width change plausible, and
# PAYMENT_CARD stays armed inside a generated fixtures directory while
# LOYALTY_NUMBER stands down. Free insurance against a future edit.
SMARTCARD = "40100200300"  # pii-scan: allow fabricated card, fails Luhn by construction


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _cell(value: str, index: int | None = None, style: str | None = None) -> str:
    """One cell. Every value is `ss:Type="String"`, as the real export emits."""
    at = f' ss:StyleID="{style}"' if style else ""
    at += f' ss:Index="{index}"' if index is not None else ""
    return (
        f"      <ss:Cell{at}><ss:Data ss:Type=\"String\">"
        f"{_escape(value)}</ss:Data></ss:Cell>"
    )


def _banner_cell() -> str:
    """The banner exactly as the export writes it, which is three hazards.

    Merged across the remaining four columns, so anything placed after it in
    the row belongs at column 6; its text wrapped in HTML markup, so a reader
    taking only `ss:Data.text` sees an empty string; and an `ss:NamedCell`
    sibling after the `ss:Data`, so `ss:Cell` is not a one-child element.
    """
    return (
        f'      <ss:Cell ss:StyleID="{BANNER_STYLE}"'
        f' ss:MergeAcross="{len(HEADERS) - 1}">'
        '<ss:Data xmlns:html="http://www.w3.org/TR/REC-html40" ss:Type="String">'
        f'<html:B><html:U><html:Font html:Size="14">{_escape(BANNER_TEXT)}'
        "</html:Font></html:U></html:B></ss:Data>"
        '<ss:NamedCell ss:Name="Print_Titles" /></ss:Cell>'
    )


def _amount(rng: random.Random) -> tuple[str, int]:
    """An amount and its points.

    One in eleven amounts drops its trailing zero, which the real export does:
    the column is not reliably two decimal places and a parser that assumes it
    is will trip on the first one that is not.
    """
    cents = rng.randint(1000, 14000)
    points = round(cents / 100)
    text = f"{cents / 100:.2f}"
    if text.endswith("0") and rng.random() < 1 / 11:
        text = text[:-1]
    return text, points


def build(seed: int = DEFAULT_SEED) -> str:
    rng = random.Random(seed)

    # A window wider than the reference file's, with whole months missing.
    start = datetime(2018, 1, 1, tzinfo=UTC)
    # Nothing dated after the window closes: an archive of purchases that have
    # not happened yet would be a strange thing to hand a reader.
    end = datetime(2026, 8, 31, tzinfo=UTC)
    rows: list[str] = []

    # The banner row: one merged, marked-up cell spanning the full width.
    rows.append(
        "    <ss:Row ss:Height=\"38\">\n" + _banner_cell() + "\n    </ss:Row>"
    )
    rows.append(
        "    <ss:Row ss:AutoFitHeight=\"1\">\n"
        + "\n".join(_cell(header, style=HEADER_STYLE) for header in HEADERS)
        + "\n    </ss:Row>"
    )

    when = start
    data_rows = 0
    sparse_at = {17, 53}          # rows that omit Branch and declare ss:Index
    empty_row_at = 40             # a row that declares its own number, skipping one
    row_number = 3                # 1 banner, 2 header
    while data_rows < 200:
        # Between 4 and 52 days on, so some months carry several visits and
        # some carry none at all.
        when += timedelta(days=rng.randint(4, 52), minutes=rng.randint(0, 1439))
        when = when.replace(second=0, microsecond=0)
        if when > end:
            break
        data_rows += 1
        branch = rng.choices(BRANCHES, weights=BRANCH_WEIGHTS, k=1)[0]
        amount, points = _amount(rng)
        stamp = when.strftime("%Y-%m-%d %H:%M:%S") + ".0"

        declared = None
        if data_rows == empty_row_at:
            # The row names its own position, leaving a gap above it.
            row_number += 1
            declared = row_number

        # The export banks its rows, so every cell carries one of two styles.
        style = ROW_STYLES[data_rows % len(ROW_STYLES)]
        if data_rows in sparse_at:
            # Branch omitted. The next cell declares column 4, which is what a
            # positional reader gets wrong.
            cells = [
                _cell(SMARTCARD, style=style),
                _cell(stamp, style=style),
                _cell(amount, index=4, style=style),
                _cell(str(points), style=style),
            ]
        else:
            cells = [
                _cell(SMARTCARD, style=style),
                _cell(stamp, style=style),
                _cell(branch, style=style),
                _cell(amount, style=style),
                _cell(str(points), style=style),
            ]

        at = f' ss:Index="{declared}"' if declared else ""
        rows.append(f"    <ss:Row{at}>\n" + "\n".join(cells) + "\n    </ss:Row>")
        row_number += 1

    body = "\n".join(rows)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<ss:Workbook xmlns:ss="{SS}"'
        ' xmlns:x="urn:schemas-microsoft-com:office:excel"'
        ' xmlns:o="urn:schemas-microsoft-com:office:office">\n'
        "  <o:DocumentProperties><o:Title>Workbook</o:Title></o:DocumentProperties>\n"
        "  <ss:ExcelWorkbook>"
        "<ss:WindowHeight>9000</ss:WindowHeight>"
        "<ss:WindowWidth>50000</ss:WindowWidth>"
        "<ss:ProtectStructure>false</ss:ProtectStructure>"
        "<ss:ProtectWindows>false</ss:ProtectWindows>"
        "</ss:ExcelWorkbook>\n"
        "  <ss:Styles>\n"
        '    <ss:Style ss:ID="Default"/>\n'
        + "".join(
            f'    <ss:Style ss:ID="{style}"/>\n'
            for style in (BANNER_STYLE, HEADER_STYLE, *ROW_STYLES)
        )
        + "  </ss:Styles>\n"
        '  <ss:Worksheet ss:Name="Workbook">\n'
        # Between the worksheet's start tag and its table, where a reader that
        # takes the first child of a worksheet to be its table meets it first.
        '    <ss:Names><ss:NamedRange ss:Name="Print_Titles"'
        ' ss:RefersTo="=\'Workbook\'!R1:R2" /></ss:Names>\n'
        f'    <ss:Table x:FullRows="1" x:FullColumns="1"'
        f' ss:ExpandedColumnCount="{len(HEADERS)}"'
        f' ss:ExpandedRowCount="{row_number - 1}">\n'
        # Column widths, which the export writes ahead of its rows. They are
        # children of ss:Table and not of any row, so a reader that walks a
        # row's children has to not meet them — which is worth generating.
        + "".join(
            '      <ss:Column ss:AutoFitWidth="1" ss:Width="164" />\n'
            for _ in HEADERS
        )
        + f"{body}\n"
        "    </ss:Table>\n"
        # Print setup, after the table and still inside the worksheet. Nothing
        # reads it; it is generated because it is the last thing between the
        # final row and the end of the sheet, which is where a reader deciding
        # it has finished can get the answer wrong.
        "    <x:WorksheetOptions><x:PageSetup>"
        '<x:Layout x:CenterHorizontal="1" x:Orientation="Portrait" />'
        "</x:PageSetup><x:FitToPage /><x:Print>"
        "<x:PrintErrors>Blank</x:PrintErrors><x:FitWidth>1</x:FitWidth>"
        "<x:ValidPrinterInfo /><x:VerticalResolution>600</x:VerticalResolution>"
        "</x:Print><x:Selected /><x:DoNotDisplayGridlines />"
        "<x:ProtectObjects>False</x:ProtectObjects>"
        "<x:ProtectScenarios>False</x:ProtectScenarios>"
        "</x:WorksheetOptions>\n"
        "  </ss:Worksheet>\n"
        "</ss:Workbook>\n"
    )


def generate(seed: int = DEFAULT_SEED) -> dict[str, str]:
    """Entry point for tools/make_fixtures.py: filename -> content."""
    if _luhn_ok(SMARTCARD):
        raise ValueError(
            "the fabricated smartcard passes a Luhn check, which makes it "
            "indistinguishable in shape from a payment card"
        )
    return {FILENAME: build(seed)}
