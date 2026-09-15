"""Reading a capture: what the engine is asked, and what is done with the answer.

Nothing here knows what a receipt is. These assert the three things the layer
promises — that a line is a line, that a number is read as a number, and that
an overlay drawn across the page is not read as one — because each of them was
wrong once against the real corpus, in a way that produced a plausible page
rather than an obviously broken one.

The pages are drawn (`tests/receiptimage.py`) rather than committed. Real
captures cannot be committed, and the reader is what is under test, so what it
needs is input with the shape of the real thing.
"""

from decimal import Decimal

import pytest

from tests.receiptimage import build_receipt
from unbagged import transcription as tr
from unbagged.transcription import image as tr_image
from unbagged.transcription import words as tr_words

pytestmark = pytest.mark.skipif(
    not tr_words.available(),
    reason=f"{tr_words.ENGINE} is not installed",
)

#: A basket that foots: 6.99 + 18.99 - 3.75 + 0.00 tax = 22.23.
BASKET = [
    ("", "Customer ID: 40100200300", None),
    ("", "PEELED GARLIC 1 LB", "6.99"),
    ("", "RICE 15LB", "18.99"),
    ("2.50 lb @ 1.50 / lb", "", None),
    ("WT", "SC - CHK BNLS", "-3.75"),
    ("", "TAX", "0.00"),
    ("***", "BALANCE", "22.23"),
    ("", "CREDIT CARD", "22.23"),
]


def amounts(transcript) -> list[Decimal]:
    """Every number in the money column, in reading order."""
    column = transcript.money_column
    assert column is not None, "no money column was found"
    found = []
    for line in transcript.lines:
        for word in line.within(column):
            text = word.text.replace("$", "").replace("§", "").strip()
            try:
                found.append(Decimal(text))
            except ArithmeticError:
                continue
    return found


class TestLinesAreLines:
    def test_one_row_drawn_is_one_line_read(self):
        assert len(tr.transcribe(build_receipt(BASKET)).lines) == len(BASKET)

    def test_an_outsized_glyph_does_not_swallow_the_row_below_it(self):
        """The `/` of a weight qualifier is three times the height of a letter.

        Clustering scaled to the tallest word in a group reached a whole line
        past itself, and merged the TAX row into the BALANCE row. The merged
        line then carried two amounts, which sorted by horizontal position into
        an order neither of them had been in — a basket that still footed, out
        of numbers taken from the wrong rows.

        Asserted as one-amount-per-line rather than as a line count: a count
        passes if two rows merge and one other splits.
        """
        transcript = tr.transcribe(build_receipt(BASKET))
        column = transcript.money_column
        assert all(len(line.within(column)) <= 2 for line in transcript.lines), (
            "a line carries at most its own currency mark and its own number"
        )
        assert (
            len(amounts(transcript)) == 6
        )  # five rows carry one, and BALANCE is echoed by the tender

    def test_rows_are_read_top_to_bottom(self):
        transcript = tr.transcribe(build_receipt(BASKET))
        middles = [line.middle for line in transcript.lines]
        assert middles == sorted(middles)


class TestNumbersAreReadAsNumbers:
    def test_a_minus_sign_survives(self):
        """The failure this whole layer is built around.

        Read without a restricted alphabet, the engine returns `$-3.68` as
        `$3.68` and `$-3.71` as `$371` — a discount becoming a charge, and a
        decimal point becoming a factor of a hundred. Both readings are
        plausible and neither is flagged.
        """
        assert Decimal("-3.75") in amounts(tr.transcribe(build_receipt(BASKET)))

    def test_the_basket_foots(self):
        found = amounts(tr.transcribe(build_receipt(BASKET)))
        *lines, balance, tender = found
        assert balance == tender == Decimal("22.23")
        assert sum(lines) == balance

    def test_the_money_column_is_found_at_any_page_width(self):
        """Captures of one screen vary by a couple of dozen pixels.

        A crop measured off one page reads nothing on another, which is silent:
        no amounts at all looks the same as a page with no amounts on it.
        """
        for width in (505, 525, 560):
            transcript = tr.transcribe(build_receipt(BASKET, width=width))
            assert amounts(transcript)[-1] == Decimal("22.23"), width

    def test_a_clipped_capture_is_not_quietly_completed(self):
        """One real capture cut the last digit off every amount in the column.

        Nothing can recover a pixel that was never taken, and this asserts only
        that the reader does not invent one: the basket must NOT foot. What
        happens next — the receipt is set aside and named — belongs to the
        adapter, and is asserted there.
        """
        found = amounts(tr.transcribe(build_receipt(BASKET, clip_digits=1)))
        assert not found or sum(found[:-2]) != found[-1]


class TestAnOverlayIsNotText:
    def test_a_scrawled_page_reads_the_same_as_a_clean_one(self):
        """Anchored to the clean page, not to how bad this file's scrawl is.

        About a dozen real captures carry a freehand scrawl across them, drawn
        in a pure blue the page itself never uses. The guarantee masking makes
        is that the overlay is irrelevant — so the clean page is the ground
        truth, and asserting against it cannot be satisfied by tuning the
        scrawl. Measured on one real capture: 14 lines summing to 85.28 against
        a stated 100.23 before masking, 16 summing to 100.23 exactly after.
        """
        clean = amounts(tr.transcribe(build_receipt(BASKET)))
        scrawled = amounts(tr.transcribe(build_receipt(BASKET, scrawl=True)))
        assert scrawled == clean

    def test_without_the_mask_the_same_scrawl_does_damage(self):
        """The control. Without it the test above passes on a harmless scrawl."""
        page = build_receipt(BASKET, scrawl=True)
        original = tr_image.OVERLAY_COLOURS
        tr_image.OVERLAY_COLOURS = ()
        try:
            unmasked = amounts(tr.transcribe(page))
        finally:
            tr_image.OVERLAY_COLOURS = original
        assert unmasked != amounts(tr.transcribe(build_receipt(BASKET)))

    def test_masking_costs_a_clean_page_nothing(self):
        """The other half: a page with no overlay must read the same either way."""
        page = build_receipt(BASKET)
        masked = amounts(tr.transcribe(page))

        original = tr_image.OVERLAY_COLOURS
        tr_image.OVERLAY_COLOURS = ()
        try:
            unmasked = amounts(tr.transcribe(page))
        finally:
            tr_image.OVERLAY_COLOURS = original

        assert masked == unmasked


class TestWhenTheEngineIsNotThere:
    def test_it_says_what_to_install(self, monkeypatch):
        """An absent engine is a setup problem, and the message has to say so."""
        monkeypatch.setattr(tr_words.shutil, "which", lambda _: None)
        with pytest.raises(tr_words.OcrUnavailable) as excinfo:
            tr_words.read(b"")
        assert "install" in str(excinfo.value).lower()
