"""Reading an H Mart receipt out of a capture of one.

Two kinds of test here, deliberately. The ones about arithmetic and stitching
build a `Receipt` directly, because what they assert has nothing to do with
pixels and should not be able to fail because of them. The ones about layout go
through a drawn page and the real engine, because the layout IS the thing they
assert.

Every claim below was wrong once against the real corpus, and each was wrong
quietly: a receipt that still added up out of numbers taken from the wrong
rows, a product named after the flag beside it, a visit split in two.
"""

from decimal import Decimal

import pytest

from tests.receiptimage import build_receipt
from unbagged import transcription as tr
from unbagged.adapters.hmart import receipt as rc
from unbagged.transcription import words as tr_words

needs_engine = pytest.mark.skipif(
    not tr_words.available(), reason=f"{tr_words.ENGINE} is not installed"
)


def line(description, amount, **kwargs) -> rc.ReceiptLine:
    return rc.ReceiptLine(description=description, amount=Decimal(amount), **kwargs)


def receipt(lines, *, tax="0.00", balance=None, complete=True) -> rc.Receipt:
    total = balance if balance is not None else sum(Decimal(a) for _, a in lines)
    return rc.Receipt(
        captures=("capture.png",),
        lines=tuple(line(d, a) for d, a in lines),
        tax=Decimal(tax),
        balance=Decimal(str(total)),
        complete=complete,
    )


def read(rows, **kwargs) -> rc.Receipt:
    page = build_receipt(rows, **kwargs)
    return rc.read_capture(tr.transcribe(page), "capture.png")


#: Foots: 6.99 + 18.99 - 3.75 + 0.00 tax = 22.23.
BASKET = [
    ("", "Customer ID: 40100200300", None),
    ("", "PEELED GARLIC 1 LB", "6.99"),
    ("", "RICE 15LB", "18.99"),
    ("2.50 lb @ 1.50 / lb", "", None),
    ("WT", "SC - CHK BNLS", "-3.75"),
    ("", "TAX", "0.00"),
    ("***", "BALANCE", "22.23"),
    ("", "CREDIT CARD", "22.23"),
    ("", "2019-03-04 11:07:00  2  118  0042", None),
    ("", "Card Number : *********0000", None),
]


class TestTheGate:
    """`foots` is what makes reading these by machine defensible at all."""

    def test_a_basket_that_adds_up_reconciles(self):
        assert rc.foots(receipt([("A", "6.99"), ("B", "18.99")], tax="0.00")) is None

    def test_tax_is_part_of_the_balance_and_not_of_the_lines(self):
        """The points statement reports a visit's PRE-tax total.

        Three real visits looked like disagreements between the two responses
        until tax was accounted for, by 0.20, 0.70 and 1.26 — each exactly that
        receipt's own tax line. Reading the balance as the figure to reconcile
        against the spreadsheet would have quarantined all three.
        """
        basket = receipt([("A", "10.00")], tax="0.70", balance="10.70")
        assert rc.foots(basket) is None
        assert basket.subtotal == Decimal("10.00")

    def test_a_missing_line_does_not_reconcile(self):
        basket = receipt([("A", "10.00")], tax="0.00", balance="15.00")
        assert rc.foots(basket) == Decimal("-5.00")

    def test_a_receipt_with_no_total_never_claims_to_reconcile(self):
        """There is nothing to check it against, which is not the same as passing."""
        half = rc.Receipt(captures=("a.png",), lines=(line("A", "10.00"),), complete=False)
        assert rc.foots(half) is not None


class TestStitchingTwoCaptures:
    def test_a_repeated_seam_line_is_not_counted_twice(self):
        head = rc.Receipt(captures=("a.png",), lines=(line("A", "1.00"), line("B", "2.00")))
        tail = rc.Receipt(
            captures=("b.png",),
            lines=(line("B", "2.00"), line("C", "3.00")),
            tax=Decimal("0.00"),
            balance=Decimal("6.00"),
            complete=True,
        )
        joined = rc.stitch([head, tail])
        assert rc.foots(joined) is None
        assert [str(line.amount) for line in joined.lines] == ["1.00", "2.00", "3.00"]

    def test_the_same_product_bought_twice_is_not_mistaken_for_a_seam(self):
        """The ambiguity that cannot be read off the page.

        One repeated line at a seam is either the overlap or the same product
        scanned twice, and both are ordinary. Trimming it always loses a line
        the shopper paid for; never trimming it always invents one. The receipt
        decides, by which reading adds up to the total it states.
        """
        head = rc.Receipt(captures=("a.png",), lines=(line("A", "1.00"), line("B", "2.00")))
        tail = rc.Receipt(
            captures=("b.png",),
            lines=(line("B", "2.00"), line("C", "3.00")),
            tax=Decimal("0.00"),
            balance=Decimal("8.00"),  # the shopper really did buy two of B
            complete=True,
        )
        joined = rc.stitch([head, tail])
        assert rc.foots(joined) is None
        assert [str(line.amount) for line in joined.lines] == ["1.00", "2.00", "2.00", "3.00"]

    def test_when_no_reading_reconciles_the_receipt_is_still_coherent(self):
        """A caller about to raise a warning needs something to name."""
        head = rc.Receipt(captures=("a.png",), lines=(line("A", "1.00"),))
        tail = rc.Receipt(
            captures=("b.png",),
            lines=(line("C", "3.00"),),
            balance=Decimal("99.00"),
            complete=True,
        )
        joined = rc.stitch([head, tail])
        assert rc.foots(joined) is not None
        assert joined.captures == ("a.png", "b.png")


@needs_engine
class TestTheLayoutOfAPage:
    def test_a_quantity_qualifies_the_line_below_it_not_above(self):
        """`0.57 lb @ 1.49 / lb` then a line at $0.85; 0.57 x 1.49 is 0.849.

        Read as trailing detail of the line above, every weighed item is
        attached to the wrong product — and the basket still foots, because the
        amounts are untouched. Nothing on screen would look wrong.
        """
        found = read(BASKET)
        weighed = [line for line in found.lines if line.quantity is not None]
        assert len(weighed) == 1
        # Identified by its amount, not its name: which line the quantity
        # landed on is the claim, and an assertion on transcribed text would
        # also fail on how the engine happened to space a hyphen.
        assert weighed[0].amount == Decimal("-3.75")
        assert weighed[0].quantity == Decimal("2.50")
        assert weighed[0].unit_price == Decimal("1.50")

    def test_the_flag_beside_a_line_is_not_part_of_its_name(self):
        """`WT` comes back from the engine as `wr`, and it sits on the same row.

        Taking everything left of the amount as the description put that on the
        front of a product name, where it would have reached `description_raw`,
        the product index and every price series drawn from one.
        """
        assert all(not line.description.startswith(("wr", "WT")) for line in read(BASKET).lines)

    def test_a_discount_stays_its_own_negative_line(self):
        """Not folded into the price of the thing it discounts.

        `models.py` has the long version: a real report carried 517 of 790
        lines whose loyalty price equalled the retail one, and reading those as
        discounts renders two thirds of a shopping history at $0.00.
        """
        found = read(BASKET)
        assert Decimal("-3.75") in [line.amount for line in found.lines]
        assert len(found.lines) == 3

    def test_the_furniture_is_not_a_purchase(self):
        found = read(BASKET)
        names = [line.description for line in found.lines]
        assert not any("BALANCE" in n or "TAX" in n or "CREDIT" in n for n in names)
        assert found.tax == Decimal("0.00")
        assert found.balance == Decimal("22.23")
        assert found.tender == "CREDIT CARD"

    def test_nothing_from_the_card_block_is_read(self):
        """It is on the page. It is not asked for, and it is not taken.

        Asserted against the digits THIS page carries, not a remembered value.
        This test once named four digits that the fixture no longer contained,
        so it passed without checking anything — the shape of a guard that
        asserts a constant instead of a relationship.
        """
        tail = next(row[1].rsplit("*", 1)[-1] for row in BASKET if "Card Number" in (row[1] or ""))
        found = read(BASKET)
        assert tail, "the fixture must carry a card block or this proves nothing"
        assert tail not in repr(found)
        assert all("Card" not in line.description for line in found.lines)

    def test_the_customer_id_and_the_stamp_are_recovered(self):
        found = read(BASKET)
        assert found.customer_id == "40100200300"
        assert found.stamp is not None
        assert found.stamp.occurred_at == "2019-03-04T11:07:00"
        assert found.stamp.lane == "2"

    def test_a_page_that_reconciles_end_to_end(self):
        assert rc.foots(read(BASKET)) is None


@needs_engine
class TestACaptureThatRanOutOfScreen:
    #: The same basket, cut off mid-list: no tax, no total, no timestamp.
    HEAD = BASKET[:3]

    def test_it_is_not_taken_for_a_finished_receipt(self):
        assert read(self.HEAD, cut_off=True).complete is False

    def test_a_coincidence_at_the_cut_is_not_read_as_a_total(self):
        """Two products that happen to cost the same look exactly like a total
        and the tender echoing it.

        A real head capture ends on two lines at 2.99. Read positionally, that
        is a balance — so the capture was taken as complete, its other half was
        never joined to it, and one visit became two.
        """
        cut = [
            ("", "Customer ID: 40100200300", None),
            ("", "WERTHER CHEWY CARA", "2.99"),
            ("", "HANA KOMON CANDY", "2.99"),
        ]
        half = read(cut, cut_off=True)
        assert half.complete is False
        assert half.balance is None
        assert len(half.lines) == 2

    def test_a_finished_receipt_is_not_taken_for_half_of_one(self):
        """The control. Without it the two tests above pass on always-False."""
        assert read(BASKET).complete is True


@needs_engine
class TestACaptureNothingCanRecover:
    def test_a_clipped_column_does_not_reconcile(self):
        """One real capture cut the last digit off every amount in the column.

        The page is legible and every figure on it is plausible; `$ 7.49` reads
        as `$ 7.4`. No engine and no model can return a pixel that was never
        taken, so the only correct behaviour is to fail to reconcile and be set
        aside — which is asserted here, and acted on in the adapter.
        """
        assert rc.foots(read(BASKET, clip_digits=1)) is not None


class TestWhatIsTrimmedFromAName:
    """A name identifies a product where the retailer disclosed no code.

    So a stray character is not cosmetic: it splits one product into two on a
    page whose whole subject is what you buy repeatedly. The real response has
    `AVOCADO HASS` bought three times and `AVOCADO HASS:` bought twice.
    """

    def test_a_mark_the_engine_added_is_trimmed(self):
        assert rc._clean("‘SUKOYAKA BRW RICE") == "SUKOYAKA BRW RICE"
        assert rc._clean("AVOCADO HASS:") == "AVOCADO HASS"
        assert rc._clean("BABY SALMON,") == "BABY SALMON"

    def test_a_mark_the_receipt_printed_is_left_alone(self):
        """These receipts truncate a long name and print the cut.

        The full stop is on the page, so trimming it would edit what the
        retailer printed — which is the failure the trimming above prevents,
        pointed the other way.
        """
        assert rc._clean("BLH B FRESH POTATO.") == "BLH B FRESH POTATO."
        assert rc._clean("MRNG HI-CHEW GRN A.") == "MRNG HI-CHEW GRN A."
        assert rc._clean("SC - MRN CHK BNLS") == "SC - MRN CHK BNLS"


@needs_engine
class TestWhichLineWasTheTax:
    """Found by the adversarial pass on 2026-09-16.

    `foots()` adds tax back, so `sum(items) + tax` is the same number whether
    the line above the balance was tax or a purchase. A receipt with no TAX line
    therefore loses its last purchase and reconciles perfectly — the exact
    "foots and is still wrong" case the gate exists to prevent, and one the gate
    is structurally blind to. Measured on the real corpus: the TAX word reads
    off only 34 of 46 captures, and on at least one the line above the balance
    is a product.
    """

    def page(self, rows):
        return rc.read_capture(tr.transcribe(build_receipt(rows)), "Transaction_030419.png")

    NO_TAX_LINE = [
        ("", "Customer ID: 40100200300", None),
        ("", "APPLE", "1.00"),
        ("", "PEAR", "2.00"),
        ("", "MILK", "3.00"),
        ("***", "BALANCE", "6.00"),
        ("", "CREDIT CARD", "6.00"),
        ("", "2019-03-04 11:07:00  2  118  0042", None),
    ]

    def test_a_receipt_with_no_tax_line_still_foots_after_eating_a_purchase(self):
        """The bug, asserted as it actually behaves.

        This is NOT the fix — it records that the gate cannot see this, which is
        why something outside the receipt has to choose.
        """
        found = self.page(self.NO_TAX_LINE)
        assert rc.foots(found) is None, "the gate reconciles either reading"
        assert found.tax == Decimal("3.00"), "the purchase was taken as tax"

    def test_the_receipt_says_whether_it_read_the_word_or_guessed(self):
        """What lets the adapter know it has a choice to make."""
        assert self.page(self.NO_TAX_LINE).tax_inferred is True

    def test_a_receipt_that_prints_TAX_is_not_a_guess(self):
        with_tax = [
            ("", "Customer ID: 40100200300", None),
            ("", "APPLE", "1.00"),
            ("", "TAX", "0.00"),
            ("***", "BALANCE", "1.00"),
            ("", "CREDIT CARD", "1.00"),
            ("", "2019-03-04 11:07:00  2  118  0042", None),
        ]
        found = self.page(with_tax)
        assert found.tax_inferred is False
        assert found.tax == Decimal("0.00")

    def test_the_other_reading_is_available_and_keeps_the_purchase(self):
        restored = self.page(self.NO_TAX_LINE).with_tax_as_item()
        assert restored.tax == Decimal("0.00")
        assert sum(line.amount for line in restored.lines) == Decimal("6.00")
        assert rc.foots(restored) is None, "both readings reconcile — that is the point"


class TestJoiningThreeOrMoreCaptures:
    """Found by the adversarial pass on 2026-09-16.

    Two captures hid both of these: with one seam, "a trim per seam" and "one
    cap for all seams" are the same thing, and a group of two is never partly
    complete. Three is where they come apart.
    """

    def part(self, names, **kw):
        return rc.Receipt(
            captures=("c.png",),
            lines=tuple(rc.ReceiptLine(n, Decimal("1.00")) for n in names),
            **kw,
        )

    def test_the_correct_join_is_reachable_when_seams_differ(self):
        """One cap across every seam could not produce it at all.

        With real overlaps of 2 and 0, every candidate either dropped lines or
        duplicated them — verified: cap 1 both duplicated Z and lost Q. The
        greedy pass happened to find the right answer, and greedy is exactly
        what fails when one transcribed description differs by a character at
        the true seam, which is the case this search exists for.
        """
        joined = rc.stitch(
            [
                self.part(["X", "Y", "Z"]),
                self.part(["Y", "Z", "P"]),
                self.part(["Q", "R"], tax=Decimal("0.00"), balance=Decimal("6.00"), complete=True),
            ]
        )
        assert [line.description for line in joined.lines] == ["X", "Y", "Z", "P", "Q", "R"]
        assert rc.foots(joined) is None

    def test_a_reading_that_keeps_more_lines_wins_a_tie(self):
        """Two trims can both reconcile — a product and its `CL` cancellation
        straddling a seam sum to zero. Between two readings that both add up,
        the one discarding less of what was paid for is the safer answer."""
        head = rc.Receipt(
            captures=("a.png",),
            lines=(rc.ReceiptLine("A", Decimal("5.00")), rc.ReceiptLine("B", Decimal("2.00"))),
        )
        tail = rc.Receipt(
            captures=("b.png",),
            lines=(rc.ReceiptLine("B", Decimal("2.00")), rc.ReceiptLine("CL B", Decimal("-2.00"))),
            tax=Decimal("0.00"),
            balance=Decimal("7.00"),
            complete=True,
        )
        joined = rc.stitch([head, tail])
        assert rc.foots(joined) is None
        assert len(joined.lines) == 4, "the trim that discarded the pair also reconciled"


class TestWhichCapturesAreOfOneVisit:
    """Found by the adversarial pass on 2026-09-16.

    Captures are grouped by the date in their filename, which is a candidate
    grouping, not an answer. The old rule — all complete, or stitch them all —
    fused a whole day into one receipt the moment a single capture among them
    was unreadable.
    """

    def part(self, names, **kw):
        return rc.Receipt(
            captures=("c.png",),
            lines=tuple(rc.ReceiptLine(n, Decimal("1.00")) for n in names),
            **kw,
        )

    def test_two_visits_one_of_them_captured_twice(self):
        """Three parts, one incomplete. Previously all three were stitched into
        one basket that could not foot, and BOTH visits were quarantined."""
        whole = self.part(["A"], tax=Decimal("0.00"), balance=Decimal("1.00"), complete=True)
        head = self.part(["B", "C"])
        tail = self.part(["D"], tax=Decimal("0.00"), balance=Decimal("3.00"), complete=True)
        found = rc.split_into_receipts([whole, head, tail])
        assert [[line.description for line in r.lines] for r in found] == [["A"], ["B", "C", "D"]]
        assert all(rc.foots(r) is None for r in found)

    def test_two_whole_receipts_stay_two(self):
        one = self.part(["A"], tax=Decimal("0.00"), balance=Decimal("1.00"), complete=True)
        two = self.part(["B"], tax=Decimal("0.00"), balance=Decimal("1.00"), complete=True)
        assert len(rc.split_into_receipts([one, two])) == 2

    def test_a_head_and_a_tail_are_one(self):
        head = self.part(["A", "B"])
        tail = self.part(["C"], tax=Decimal("0.00"), balance=Decimal("3.00"), complete=True)
        (found,) = rc.split_into_receipts([head, tail])
        assert [line.description for line in found.lines] == ["A", "B", "C"]


class TestWhichVisitAFilenameBelongsTo:
    """`group_by_visit` and `capture_date` had no direct tests."""

    def test_two_parts_of_one_receipt_group_together(self):
        names = ["Transaction_030419_02.png", "Transaction_030419_01.png"]
        assert rc.group_by_visit(names) == [sorted(names)]

    def test_a_date_the_calendar_does_not_have_is_not_a_date(self):
        assert rc.capture_date("Transaction_133199.png") is None
        assert rc.capture_date("Transaction_023019.png") is None, "30 February"
        assert rc.capture_date("Transaction_030419.png") == "2019-03-04"

    def test_a_name_with_no_date_in_it_is_not_a_capture(self):
        assert rc.capture_date("screenshot.png") is None
