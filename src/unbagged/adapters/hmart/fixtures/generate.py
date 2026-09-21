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
from decimal import Decimal

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
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cell(value: str, index: int | None = None, style: str | None = None) -> str:
    """One cell. Every value is `ss:Type="String"`, as the real export emits."""
    at = f' ss:StyleID="{style}"' if style else ""
    at += f' ss:Index="{index}"' if index is not None else ""
    return f'      <ss:Cell{at}><ss:Data ss:Type="String">{_escape(value)}</ss:Data></ss:Cell>'


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


# ---------------------------------------------------------------------------
# The second half of the response: screen captures of the receipt viewer
# ---------------------------------------------------------------------------
#
# The statement says what a visit cost. The captures say what was in it, and the
# adapter stores a basket only where the two agree — `sum(lines)` against the
# statement's `Amount`, and `sum(lines) + TAX` against the receipt's own printed
# BALANCE. So these are drawn FROM the visits above rather than beside them.
#
# Far fewer than the statement has rows, and that is deliberate twice over. The
# real reply carried 46 captures against 67 statement rows, so totals-only visits
# are the normal case and not a defect to be generated away. And every capture
# costs an OCR pass at ingest: at one per visit the fixture would take minutes to
# read, which would make it useless for the screenshot run and for the container
# tier both.

#: Invented product names. Romanised rather than in Hangul, which is a known gap
#: and is recorded as one in NOTES.md: the shipped reader runs tesseract with an
#: English alphabet, so a Hangul name would transcribe to noise and the fixture
#: would be asserting that the reader mangles names. The label cleaner's
#: non-Latin path is covered directly instead, in `tests/test_views_labels.py`.
#:
#: Chosen to survive OCR at the size these pages are drawn: no size token welded
#: to a digit. `14OZ` reads back as `140Z` and `5LB` as `SLB`, which is faithful
#: to what the engine does and would put two spellings of one product in the
#: index for a reason that has nothing to do with the retailer.
CAPTURE_PRODUCTS = (
    "GREEN ONION BUNCH",
    "FIRM TOFU",
    "NAPA KIMCHI",
    "TOASTED SESAME OIL",
    "SHORT GRAIN RICE",
    "SWEET POTATO NOODLE",
    "GOCHUJANG PASTE",
    "DOENJANG PASTE",
    "FRESH GARLIC",
    "KOREAN PEAR",
    "ENOKI MUSHROOM",
    "PERILLA LEAF",
    "DRIED ANCHOVY",
    "ROASTED SEAWEED",
    "RICE CAKE STICK",
    "SOFT TOFU TUBE",
    "MUNG BEAN SPROUT",
    "DAIKON RADISH",
    "FISH CAKE SHEET",
    "BLACK BEAN SAUCE",
    "INSTANT RAMEN PACK",
    "BARLEY TEA BAG",
    "CITRON TEA JAR",
    "FROZEN MANDU",
    "PORK BELLY SLICE",
    "BEEF BRISKET SLICE",
    "SQUID WHOLE",
    "MACKEREL FILLET",
    "QUAIL EGG TIN",
    "CORN SILK TEA",
)

#: The flag column, which the viewer prints beside some lines and not others.
#: `WT` on a weighed line, `CL` where the card took a price off.
CAPTURE_FLAGS = ("", "", "", "", "WT", "CL")

#: How many captures to draw, and how the corpus divides. The counts are small
#: and absolute rather than shares, because each one exists to reach a specific
#: branch of the reader and one is enough to reach it.
ORDINARY_CAPTURES = 18
#: Captures carrying a freehand scrawl in pure blue, which is masked to white
#: before the engine sees the page. A dozen of the real 46 carry one.
SCRAWLED_CAPTURES = 4
#: One receipt too tall for the screen, captured in two overlapping halves that
#: have to be stitched back into one basket.
SPLIT_CAPTURES = 1
#: The data row that opens a pair of visits sharing one calendar date. Two trips
#: to the shop in one day, at different times and for different amounts, which the
#: real statement contains and which the capture filenames cannot distinguish from
#: the two halves of one tall receipt above. It has to exist in the STATEMENT for
#: the captures to have anything to pair with: naming two captures for one date
#: while each prints its own, different, stamp describes nothing that could have
#: happened, and the adapter correctly reads it as one visit and a stray.
SAME_DAY_FIRST_ROW = 61
#: One capture clipped at the source, where the amount column runs into the right
#: edge and every amount loses its last digit. Nothing is stored from it: the gate
#: needs a figure the reader had no hand in and the clip took the only one. The
#: visit keeps the total the statement gave it.
CLIPPED_CAPTURES = 1

#: Larger than the size the tests draw at, and measured rather than chosen. At
#: `receiptimage.FONT_SIZE` the engine loses the decimal point in an amount about
#: once in thirty pages: `4.81` comes back as `481`, which matches no amount
#: pattern, so the line is dropped and the basket misses by exactly it. Over 80
#: drawn pages: 14 reconciled 79 times, 16 reconciled 79, 17 reconciled 80. 17 is
#: the size used here — the failures it removes are the reader meeting a glyph it
#: cannot resolve, which is a real hazard worth a test and not worth spending a
#: whole visit's contents on in a fixture whose job is to show the format.
CAPTURE_FONT_SIZE = 17


def _split_amount(rng: random.Random, total: Decimal, parts: int) -> list[Decimal]:
    """`total` divided into `parts` line amounts that sum to it exactly.

    Exactly, not nearly. The adapter compares the summed lines against the
    statement's figure and refuses the basket on any difference at all, so a
    rounding residue here would generate a fixture that reaches only the refusal
    path. The remainder lands on the last line rather than being spread, which is
    what keeps the sum exact.
    """
    cents = int(total * 100)
    # Every line needs at least 25 cents, so a basket cannot be divided into more
    # parts than it has money for.
    parts = max(1, min(parts, cents // 25))
    if parts == 1:
        return [total]
    cuts = sorted(rng.sample(range(25, cents - 25 * (parts - 1)), parts - 1))
    amounts, previous = [], 0
    for index, cut in enumerate(cuts):
        amounts.append(Decimal(cut + 25 * index - previous) / 100)
        previous = cut + 25 * index
    amounts.append(Decimal(cents - previous) / 100)
    return amounts


def _receipt_rows(rng: random.Random, visit: dict, amounts: list[Decimal]) -> list[tuple]:
    """The page, in the order the viewer prints it.

    Customer ID, the purchase lines, TAX, the balance, the tender echoing it, the
    stamp, then the card block. The two equal amounts at the foot are what tells
    the reader which line is the total, and the card block is there because the
    reader has to stop before it — nothing from it is ever transcribed.
    """
    subtotal = sum(amounts)
    # A tax line the statement's figure excludes, which is what makes the
    # statement a pre-tax subtotal and the receipt's balance the amount paid.
    tax = (subtotal * Decimal("0.0875")).quantize(Decimal("0.01"))
    balance = subtotal + tax
    names = rng.sample(CAPTURE_PRODUCTS, k=len(amounts))
    rows: list[tuple] = [("", f"Customer ID: {SMARTCARD}", None)]
    for name, amount in zip(names, amounts, strict=True):
        rows.append((rng.choice(CAPTURE_FLAGS), name, f"{amount:.2f}"))
    rows += [
        ("", "TAX", f"{tax:.2f}"),
        ("***", "BALANCE", f"{balance:.2f}"),
        ("", rng.choice(("CREDIT", "DEBIT", "CASH")), f"{balance:.2f}"),
        ("", visit["when"].strftime("%Y-%m-%d %H:%M:%S") + "  2  118  0042", None),
        ("", "Card Number : *********0000", None),
    ]
    return rows


def _capture_name(when, part: int | None = None) -> str:
    """`Transaction_MMDDYY.png`, and `_NN` for one receipt captured in halves.

    Two digits of year, which is what the viewer writes and what
    `receipt.capture_date` reads back.
    """
    stem = "Transaction_" + when.strftime("%m%d%y")
    return f"{stem}_{part:02d}.png" if part is not None else f"{stem}.png"


def _captures(seed: int, visits: list[dict]) -> dict[str, bytes]:
    """The captures, drawn from the visits the statement already states.

    Its own rng stream, seeded apart from the spreadsheet's, so that changing how
    many captures are drawn cannot move a single figure in the statement. The two
    halves of a response are written by different systems and a change to one
    should not rewrite the other.
    """
    from tests.receiptimage import build_receipt

    rng = random.Random(seed + 1)
    # Only visits that state a branch, spread across the window rather than taken
    # from its head: a capture run that all lands in one month cannot show what a
    # partly-itemised history looks like.
    eligible = [visit for visit in visits if visit["branch"] and not visit.get("same_day")]
    wanted = ORDINARY_CAPTURES + SCRAWLED_CAPTURES + SPLIT_CAPTURES + CLIPPED_CAPTURES
    step = max(1, len(eligible) // wanted)
    chosen = eligible[::step][:wanted]

    drawn: dict[str, bytes] = {}
    for index, visit in enumerate(chosen):
        amounts = _split_amount(rng, Decimal(visit["amount"]), rng.randint(3, 9))
        rows = _receipt_rows(rng, visit, amounts)
        name = _capture_name(visit["when"])

        if index < CLIPPED_CAPTURES:
            # Clipped at the source. Drawn narrow as well, because the real
            # clipped page is the narrowest in its corpus and the two together
            # are what `_clipped` measures.
            drawn[name] = build_receipt(rows, width=505, clip_digits=1, font_size=CAPTURE_FONT_SIZE)
        elif index < CLIPPED_CAPTURES + SPLIT_CAPTURES and len(amounts) >= 5:
            # One receipt, two captures. The first half is cut off flush against
            # its last line, which is the only thing distinguishing it from a
            # whole receipt, and the halves overlap by one line — which is either
            # the seam or the same product scanned twice, and the reader decides
            # which by whether the result reconciles.
            head = rows[: 1 + 3]
            tail = rows[3:]
            drawn[_capture_name(visit["when"], 0)] = build_receipt(
                head, cut_off=True, font_size=CAPTURE_FONT_SIZE
            )
            drawn[_capture_name(visit["when"], 1)] = build_receipt(
                tail, font_size=CAPTURE_FONT_SIZE
            )
        elif index < CLIPPED_CAPTURES + SPLIT_CAPTURES + SCRAWLED_CAPTURES:
            drawn[name] = build_receipt(rows, scrawl=True, font_size=CAPTURE_FONT_SIZE)
        else:
            drawn[name] = build_receipt(rows, font_size=CAPTURE_FONT_SIZE)

    # The two trips that share a date. Both captures are named for that date and
    # carry a `_NN` part, which is byte for byte the naming the split receipt
    # above uses — so the reader cannot tell from the names whether it is holding
    # one tall receipt or two separate baskets, and has to decide by reading them.
    for part, visit in enumerate(visit for visit in visits if visit.get("same_day")):
        amounts = _split_amount(rng, Decimal(visit["amount"]), rng.randint(3, 6))
        rows = _receipt_rows(rng, visit, amounts)
        drawn[_capture_name(visit["when"], part)] = build_receipt(rows, font_size=CAPTURE_FONT_SIZE)

    return drawn


def build(seed: int = DEFAULT_SEED) -> tuple[str, list[dict]]:
    """The spreadsheet, and the visits it states.

    Returns both because the captures have to reconcile against these exact
    figures: a receipt whose lines do not sum to the statement's `Amount` for the
    same visit is refused by the adapter, which is the whole point of the gate.
    Drawing the two independently would produce a fixture that exercises only the
    refusal path.
    """
    rng = random.Random(seed)
    visits: list[dict] = []

    # A window wider than the reference file's, with whole months missing.
    start = datetime(2018, 1, 1, tzinfo=UTC)
    # Nothing dated after the window closes: an archive of purchases that have
    # not happened yet would be a strange thing to hand a reader.
    end = datetime(2026, 8, 31, tzinfo=UTC)
    rows: list[str] = []

    # The banner row: one merged, marked-up cell spanning the full width.
    rows.append('    <ss:Row ss:Height="38">\n' + _banner_cell() + "\n    </ss:Row>")
    rows.append(
        '    <ss:Row ss:AutoFitHeight="1">\n'
        + "\n".join(_cell(header, style=HEADER_STYLE) for header in HEADERS)
        + "\n    </ss:Row>"
    )

    when = start
    data_rows = 0
    sparse_at = {17, 53}  # rows that omit Branch and declare ss:Index
    empty_row_at = 40  # a row that declares its own number, skipping one
    row_number = 3  # 1 banner, 2 header
    while data_rows < 200:
        # Between 4 and 52 days on, so some months carry several visits and
        # some carry none at all.
        if data_rows == SAME_DAY_FIRST_ROW - 1:
            # The first of the same-day pair, pinned to a morning so the second
            # has somewhere later in the day to sit.
            when += timedelta(days=rng.randint(4, 52))
            when = when.replace(hour=rng.randint(8, 11), minute=rng.randint(0, 59))
        elif data_rows == SAME_DAY_FIRST_ROW:
            # The second trip, same date, a later hour. Not a repeated
            # (timestamp, branch) pair — the real statement has none of those —
            # just a repeated date.
            when = when.replace(hour=rng.randint(16, 20), minute=rng.randint(0, 59))
        else:
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
        # A sparse row states no branch, so it is not a candidate for a capture:
        # the pairing is on timestamp and amount, but a visit with no branch is
        # already exercising a different shape and stacking the two would make a
        # failure ambiguous.
        visits.append(
            {
                "when": when,
                "branch": None if data_rows in sparse_at else branch,
                "amount": amount,
                "points": points,
                "same_day": data_rows in (SAME_DAY_FIRST_ROW, SAME_DAY_FIRST_ROW + 1),
            }
        )
        row_number += 1

    body = "\n".join(rows)
    document = (
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
        " ss:RefersTo=\"='Workbook'!R1:R2\" /></ss:Names>\n"
        f'    <ss:Table x:FullRows="1" x:FullColumns="1"'
        f' ss:ExpandedColumnCount="{len(HEADERS)}"'
        f' ss:ExpandedRowCount="{row_number - 1}">\n'
        # Column widths, which the export writes ahead of its rows. They are
        # children of ss:Table and not of any row, so a reader that walks a
        # row's children has to not meet them — which is worth generating.
        + "".join('      <ss:Column ss:AutoFitWidth="1" ss:Width="164" />\n' for _ in HEADERS)
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
    return document, visits


def generate(seed: int = DEFAULT_SEED) -> dict[str, str | bytes]:
    """Entry point for tools/make_fixtures.py: filename -> content.

    The statement is text and the captures are PNG bytes, which is why
    `make_fixtures` accepts both. The captures are compared by decoded pixels
    rather than by byte for the reason recorded there.
    """
    if _luhn_ok(SMARTCARD):
        raise ValueError(
            "the fabricated smartcard passes a Luhn check, which makes it "
            "indistinguishable in shape from a payment card"
        )
    document, visits = build(seed)
    return {FILENAME: document, **_captures(seed, visits)}
