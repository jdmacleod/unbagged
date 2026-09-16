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

from tests import receiptimage
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

    def test_a_trim_that_would_lose_a_real_line_does_not_reconcile(self):
        """What keeps the ordering from costing anything.

        Candidates are tried most-trims-first, so the join that discards this
        seam is offered before the one that keeps it. It is not taken: dropping
        a line the shopper paid for changes the sum, and a join whose sum is
        wrong never passes the gate at all."""

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


class TestTheOtherFreeVariableInTheGate:
    """Found by the adversarial re-check on 2026-09-16.

    Pinning the balance to the page left `foots()` with one equation and two
    unknowns the model still controlled. It is `subtotal + tax - balance`, so an
    answer can quote the printed total back correctly — it reads the same
    pixels — and let `tax` absorb whatever the basket was inflated by.
    """

    def page(self, **kw):
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(line("RICE", "19.00"),),
            balance=Decimal("20.00"),
            complete=True,
            **kw,
        )

    FABRICATED = {
        "lines": [
            {"description": "PLATINUM WATCH", "amount": "1000.00"},
            {"description": "CAVIAR TIN", "amount": "250.00"},
        ],
        "tax": "-1230.00",
        "balance": "20.00",
    }

    def test_a_negative_tax_is_refused_outright(self):
        """Measured before the fix: this basket was stored against a page
        printing 20.00, because -1230.00 made the arithmetic come out."""
        assert rc.from_reply(self.FABRICATED, self.page(tax_inferred=True)) is None

    def test_a_tax_larger_than_the_total_is_refused(self):
        deflating = dict(self.FABRICATED, tax="25.00")
        assert rc.from_reply(deflating, self.page(tax_inferred=True)) is None

    def test_a_line_worth_more_than_the_whole_receipt_is_refused(self):
        """It used to be built and then quarantined by the sum. Refusing it
        here is stronger: the sum says nothing about its parts, so a pair of
        offsetting lines at a thousand each would have passed."""
        assert rc.from_reply(self.FABRICATED, self.page(tax=Decimal("1.00"))) is None

    def test_the_page_wins_where_the_page_could_be_read(self):
        """The tax comes off the page, not out of the reply."""
        honest = {
            "lines": [{"description": "RICE", "amount": "19.00"}],
            "tax": "-1230.00",
            "balance": "20.00",
        }
        found = rc.from_reply(honest, self.page(tax=Decimal("1.00")))
        assert found.tax == Decimal("1.00"), "not the reply's -1230.00"

    def test_a_bounded_tax_is_still_accepted_where_the_page_could_not(self):
        honest = {
            "lines": [
                {"description": "RICE", "amount": "9.00"},
                {"description": "T", "amount": "10.00"},
            ],
            "tax": "1.00",
            "balance": "20.00",
        }
        found = rc.from_reply(honest, self.page(tax=Decimal("1.00"), tax_inferred=True))
        assert rc.foots(found) is None
        assert found.subtotal == Decimal("19.00")

    def test_an_answer_may_not_return_a_basket_of_any_length(self):
        flood = {
            "lines": [{"description": "X", "amount": "0.01"}] * (rc.MAX_REPLY_LINES + 1),
            "tax": "0.00",
            "balance": "20.00",
        }
        assert rc.from_reply(flood, self.page(tax=Decimal("0.00"))) is None

    def test_what_the_page_knew_about_its_tax_line_survives_the_answer(self):
        """`_settle_tax_against` opens on `tax_inferred`, so losing it here
        would take the statement out of the adjudication entirely."""
        honest = {
            "lines": [{"description": "RICE", "amount": "19.00"}],
            "tax": "1.00",
            "balance": "20.00",
        }
        found = rc.from_reply(honest, self.page(tax=Decimal("1.00"), tax_inferred=True))
        assert found.tax_inferred is True


class TestWhatAStitchedReceiptRemembers:
    """Found by the adversarial re-check on 2026-09-16.

    `_joined` built a fresh `Receipt` and named every field but two, so a
    stitched receipt always claimed the word TAX had been read. That is the one
    state that stops the statement adjudicating — so a tall receipt whose TAX
    line could not be read lost its last purchase, footed anyway, and was then
    reported as the retailer contradicting itself.
    """

    def test_a_stitched_receipt_still_says_it_guessed_at_its_tax(self):
        head = rc.Receipt(captures=("a.png",), lines=(line("A", "1.00"),))
        tail = rc.Receipt(
            captures=("b.png",),
            lines=(line("B", "2.00"),),
            tax=Decimal("3.00"),
            balance=Decimal("6.00"),
            complete=True,
            tax_inferred=True,
            tax_description="MILK",
        )
        joined = rc.stitch([head, tail])
        assert joined.tax_inferred is True
        assert joined.with_tax_as_item().lines[-1].description == "MILK"


class TestThePurchaseTakenForTaxKeepsItsName:
    """Found by the adversarial re-check on 2026-09-16.

    Restoring it nameless put a blank row in the basket and left the product
    out of Products and Prices, which key on a name. The retailer printed one.
    """

    @needs_engine
    def test_the_restored_line_carries_the_description_from_the_page(self):
        page = rc.read_capture(
            tr.transcribe(
                build_receipt(
                    [
                        ("", "Customer ID: 40100200300", None),
                        ("", "APPLE", "1.00"),
                        ("", "MILK", "3.00"),
                        ("***", "BALANCE", "4.00"),
                        ("", "CREDIT CARD", "4.00"),
                        ("", "2019-03-04 11:07:00  2  118  0042", None),
                    ]
                )
            ),
            "Transaction_030419.png",
        )
        assert page.tax_inferred is True
        assert page.with_tax_as_item().lines[-1].description == "MILK"


class TestChoosingBetweenTwoJoinsThatBothAddUp:
    """Found by the adversarial re-check on 2026-09-16.

    Fewest-trims-first was wrong: a product and its `CL` cancellation straddling
    a seam sum to zero, so trimming nothing reconciles exactly as well as
    trimming the real overlap and sorts ahead of it. Two lines the shopper was
    never charged for were stored, and the product index counted the positive
    one as a second purchase.
    """

    def part(self, rows, **kw):
        return rc.Receipt(
            captures=("c.png",),
            lines=tuple(line(name, amount) for name, amount in rows),
            **kw,
        )

    def test_the_measured_overlap_is_preferred_over_one_that_duplicates(self):
        joined = rc.stitch(
            [
                self.part([("RICE", "10.00"), ("TOFU", "5.00"), ("CL TOFU", "-5.00")]),
                self.part([("TOFU", "5.00"), ("CL TOFU", "-5.00"), ("SOAP", "4.00")]),
                self.part(
                    [("SOAP", "4.00"), ("MILK", "1.00")],
                    tax=Decimal("0.00"),
                    balance=Decimal("19.00"),
                    complete=True,
                ),
            ]
        )
        assert rc.foots(joined) is None
        # The shopper really did buy two bars of soap, one at the bottom of one
        # capture and one at the top of the next. Trimming that pair would have
        # changed the sum, and a join whose sum is wrong never reconciles — so
        # only the duplicated TOFU pair, which nets to zero, was ever at risk.
        assert [item.description for item in joined.lines] == [
            "RICE",
            "TOFU",
            "CL TOFU",
            "SOAP",
            "SOAP",
            "MILK",
        ]

    def test_the_search_is_bounded_by_how_many_files_were_uploaded(self):
        """The space is a product over the seams, and the seam count comes from
        the upload. Eight parts with 20-line seams is 1.8 billion joins."""
        parts = [self.part([(f"X{n}", "1.00") for n in range(20)]) for _ in range(8)]
        parts.append(
            self.part(
                [("LAST", "1.00")], tax=Decimal("0.00"), balance=Decimal("2.00"), complete=True
            )
        )
        joined = rc.stitch(parts)
        assert joined is not None, "it returns, rather than enumerating for an hour"


class TestWhatAModelActuallySendsBack:
    """Found by /investigate on 2026-09-16, against a live 30B vision model.

    The prompt asks for amounts with no currency symbol and says twice not to
    return the TAX or BALANCE lines among the purchases. The model did both
    anyway. Asking more firmly is not a fix: a reader that works only when the
    model obeys is a reader that does not work.
    """

    def page(self):
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(line("RICE", "19.00"),),
            balance=Decimal("39.74"),
            tax=Decimal("0.00"),
            complete=True,
        )

    def test_the_currency_mark_it_sends_is_not_an_unreadable_amount(self):
        """Every amount came back as `: 7.50`. `_decimal` returned None for all
        of them, and one unreadable amount voids the whole answer — so the
        adjudication tier read every page correctly and stored nothing, ever."""
        assert rc._decimal(": 7.50") == Decimal("7.50")
        assert rc._decimal(": -3.75") == Decimal("-3.75")

    def test_a_stray_character_inside_a_number_still_voids_it(self):
        """Only the leading mark is tolerated. `7.5O` is not 7.50 here."""
        assert rc._decimal("7.5O") is None
        assert rc._decimal("7.5 0") is None

    def test_the_receipts_own_furniture_is_not_taken_for_a_purchase(self):
        """It returned 14 rows for an 11-item receipt: the TAX line, the
        BALANCE line, and the weight qualifier above a weighed item carrying a
        copy of the amount below it. They summed to more than twice the total."""
        reply = {
            "lines": [
                {"description": "THAI BASIL", "amount": ": 5.20"},
                {"description": "1.54 lb @ 3.99 / lb", "amount": ": 6.14"},
                {"description": "CHINESE BROCCOLI", "amount": ": 6.14"},
                {"description": "TAX", "amount": ": 0.00"},
                {"description": "*** BALANCE", "amount": ": 39.74"},
                {"description": "CREDIT CARD", "amount": ": 39.74"},
            ],
            "tax": "0.00",
            "balance": "39.74",
        }
        found = rc.from_reply(reply, self.page())
        assert [item.description for item in found.lines] == ["THAI BASIL", "CHINESE BROCCOLI"]
        assert found.subtotal == Decimal("11.34")

    def furniture(self, text, amount="1.00", balance="99.99"):
        return rc._is_furniture(text, Decimal(amount), {"balance": balance})

    def test_a_weight_qualifier_is_recognised_however_it_is_written(self):
        for text in ("1.54 lb @ 3.99 / lb", "2 @ 1.50", "0.47 lb @ 1.49 / lb"):
            assert self.furniture(text), text
        for text in ("THAI BASIL", "WT DISCOUNT", "CL TOFU", "PK 90% LEAN GROUND"):
            assert not self.furniture(text), text

    def test_a_product_named_after_a_tender_is_still_a_purchase(self):
        """`TENDER` is a whole-word search, and a shop that sells gift cards
        prints `GIFT CARD 25` as an ordinary line. Matching the word alone
        deleted it from the basket in silence; the sum then came up short and
        the visit was quarantined for arithmetic that looked wrong for a reason
        nobody could see."""
        for text in ("GIFT CARD 25", "EBT ELIGIBLE RICE", "CREDIT - RETURNED ITEM", "CASH BACK"):
            assert not self.furniture(text, amount="25.00", balance="99.99"), text

    def test_the_tender_line_is_recognised_by_its_echo_of_the_balance(self):
        """What actually identifies it, and how `_settle` finds it too."""
        assert self.furniture("CREDIT CARD", amount="99.99", balance="99.99")
        assert not self.furniture("CREDIT CARD", amount="12.00", balance="99.99")


class TestACaptureCutThroughItsOwnAmounts:
    """Found by /investigate on 2026-09-16.

    One capture in the real corpus is 518px wide where the rest are 519-542, and
    its amount column runs into the right edge. The clip does not blank the last
    digit, it cuts through it, and the engine reads the surviving sliver as SOME
    digit.
    """

    def page(self, **kw):
        rows = [
            ("", "Customer ID: 40100200300", None),
            ("", "APPLE", "7.49"),
            ("", "PEAR", "5.20"),
            ("", "TAX", "0.00"),
            ("***", "BALANCE", "12.69"),
            ("", "CREDIT CARD", "12.69"),
            ("", "2019-03-04 11:07:00  2  118  0042", None),
        ]
        return rc.read_capture(tr.transcribe(build_receipt(rows, **kw)), "Transaction_030419.png")

    @needs_engine
    def test_a_clipped_capture_says_so(self):
        assert self.page(clip_digits=1).clipped is True

    @needs_engine
    def test_an_ordinary_capture_does_not(self):
        """No false positive: the real corpus separates 0px from 4px with
        nothing in between, and 45 of 46 captures sit at 4px or wider."""
        assert self.page().clipped is False

    def test_one_clipped_half_clips_the_join(self):
        head = rc.Receipt(captures=("a.png",), lines=(line("A", "1.00"),), clipped=True)
        tail = rc.Receipt(
            captures=("b.png",),
            lines=(line("B", "2.00"),),
            tax=Decimal("0.00"),
            balance=Decimal("3.00"),
            complete=True,
        )
        assert rc.stitch([head, tail]).clipped is True


class TestWhenThePageHasNoTotalLeftToCheckAgainst:
    """Found by /investigate on 2026-09-16.

    The two failures are one failure. A capture clipped at its right edge loses
    the last digit of every amount AND of the printed total, so the page that
    most needs a second reader is the page with nothing left to check one
    against. Measured: the model read that page perfectly and its answer was
    refused, because the engine had lost the balance.

    The statement is the way out, and it is not a relaxation: it is a separate
    document the model never saw, so it checks the answer exactly as the printed
    total does.
    """

    HONEST = {
        "lines": [
            {"description": "APPLE", "amount": ": 7.49"},
            {"description": "PEAR", "amount": ": 5.20"},
        ],
        "tax": "1.00",
        "balance": "13.69",
    }

    def unreadable(self, **kw):
        """A page the engine got no total off: the clip took it."""
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(line("APPLE", "7.45"),),
            balance=None,
            complete=False,
            clipped=True,
            **kw,
        )

    def test_with_no_anchor_it_is_still_refused(self):
        """The contract that held before this existed, and still holds."""
        assert rc.from_reply(self.HONEST, self.unreadable()) is None

    def test_the_statements_total_lets_the_answer_be_checked(self):
        found = rc.from_reply(self.HONEST, self.unreadable(), anchor=Decimal("12.69"))
        assert found is not None
        assert rc.foots(found) is None
        assert found.subtotal == Decimal("12.69")
        assert found.balance_from_statement is True

    def test_an_answer_that_misses_the_statement_is_refused_by_the_gate(self):
        """Which is what makes a weak anchor safe: a wrong one does not attach
        a basket to the wrong visit, it fails the arithmetic."""
        found = rc.from_reply(self.HONEST, self.unreadable(), anchor=Decimal("99.00"))
        assert rc.foots(found) is not None

    def test_the_model_cannot_move_the_result_with_its_tax(self):
        """`balance` is set to `anchor + tax`, so `foots` reduces to
        `subtotal - anchor` and the one figure still authored by the model
        cancels out of the check entirely."""
        for claimed_tax in ("0.00", "1.00", "5.00"):
            reply = dict(self.HONEST, tax=claimed_tax)
            found = rc.from_reply(reply, self.unreadable(), anchor=Decimal("12.69"))
            assert rc.foots(found) is None, claimed_tax

    def test_a_tax_outside_what_a_tax_can_be_is_still_refused(self):
        """The bound is against the anchor when there is no printed total, so
        the path without one does not quietly skip it."""
        assert (
            rc.from_reply(
                dict(self.HONEST, tax="-50.00"), self.unreadable(), anchor=Decimal("12.69")
            )
            is None
        )

    def test_a_page_that_DID_print_a_total_ignores_the_anchor(self):
        """The printed total wins where there is one. An anchor must never be
        able to override the page against its own evidence."""
        printed = rc.Receipt(
            captures=("a.png",),
            lines=(line("APPLE", "7.49"),),
            balance=Decimal("13.69"),
            tax=Decimal("1.00"),
            complete=True,
        )
        found = rc.from_reply(self.HONEST, printed, anchor=Decimal("99.00"))
        assert found.balance == Decimal("13.69")
        assert found.balance_from_statement is False


class TestWhatTheAnchoredGateStillRefuses:
    """Found by /ship's coverage pass on 2026-09-16.

    The route that stands the statement's figure in for a printed total has two
    free variables where the printed route has one, and each of them was
    reachable by a path no test walked: a reply that names no tax at all, a tax
    bounded against the anchor rather than against a balance, and an answer that
    the furniture filter empties. Every one of them has to end in a refusal or
    in a basket that was checked.
    """

    def unreadable(self):
        """A page the engine got no total off: the clip took it."""
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(line("APPLE", "7.45"),),
            balance=None,
            complete=False,
            clipped=True,
        )

    def test_an_answer_that_names_no_tax_is_checked_against_the_anchor_alone(self):
        """A reply with no `tax` field at all, against a page that read none
        either. Nothing is known about tax from either side, so it is nothing —
        and the check is then the statement's figure against the lines."""
        reply = {
            "lines": [
                {"description": "APPLE", "amount": ": 7.49"},
                {"description": "PEAR", "amount": ": 5.20"},
            ]
        }
        found = rc.from_reply(reply, self.unreadable(), anchor=Decimal("12.69"))
        assert found is not None
        assert found.tax == Decimal("0.00")
        assert found.balance == Decimal("12.69")
        assert rc.foots(found) is None

    def test_a_tax_larger_than_the_statements_total_is_refused(self):
        """The ceiling is the anchor where there is no printed total, so the
        inflating direction is closed on this route too: an answer reaching for
        a tax bigger than the whole visit is reaching for the residual."""
        reply = {
            "lines": [{"description": "APPLE", "amount": ": 7.49"}],
            "tax": "50.00",
            "balance": "57.49",
        }
        assert rc.from_reply(reply, self.unreadable(), anchor=Decimal("12.69")) is None

    def test_an_answer_of_nothing_but_furniture_is_no_answer(self):
        """Dropping the receipt's own furniture must not be able to leave an
        empty basket that reconciles against a visit by accident."""
        reply = {
            "lines": [
                {"description": "TAX", "amount": ": 0.00"},
                {"description": "*** BALANCE", "amount": ": 12.69"},
                {"description": "CREDIT CARD", "amount": ": 12.69"},
            ],
            "tax": "0.00",
            "balance": "12.69",
        }
        assert rc.from_reply(reply, self.unreadable(), anchor=Decimal("12.69")) is None

    def test_a_reply_row_that_is_not_a_row_voids_the_whole_answer(self):
        """Rather than costing one line. A basket missing a line still adds up
        if the rest of the answer was written to match, and skipping quietly is
        how that gets stored."""
        reply = {
            "lines": [
                {"description": "APPLE", "amount": ": 7.49"},
                "PEAR : 5.20",
            ],
            "tax": "0.00",
            "balance": "12.69",
        }
        assert rc.from_reply(reply, self.unreadable(), anchor=Decimal("12.69")) is None


class TestWhereTheClipThresholdSits:
    """Found by /ship's testing specialist on 2026-09-16.

    `CLIP_MARGIN` is justified by a measurement — 0px on the one clipped
    capture, 4px on the tightest of the other 45 — and nothing pinned it. The
    only negative test used the drawing default of 13px, six times the
    discriminating distance, so widening the constant to 6 left the whole suite
    green while it told readers that 45 of 46 good captures were cut off.
    """

    def page(self, **kw):
        rows = [
            ("", "Customer ID: 40100200300", None),
            ("", "APPLE", "7.49"),
            ("", "TAX", "0.00"),
            ("***", "BALANCE", "7.49"),
            ("", "CREDIT CARD", "7.49"),
            ("", "2019-03-04 11:07:00  2  118  0042", None),
        ]
        return rc.read_capture(tr.transcribe(build_receipt(rows, **kw)), "Transaction_030419.png")

    @needs_engine
    def test_the_tightest_good_capture_is_not_called_clipped(self, monkeypatch):
        """4px is the narrowest margin in the real corpus that is NOT a clip."""
        monkeypatch.setattr(receiptimage, "RIGHT_MARGIN", 4)
        assert self.page().clipped is False

    @needs_engine
    def test_a_page_flush_against_its_amounts_is(self, monkeypatch):
        monkeypatch.setattr(receiptimage, "RIGHT_MARGIN", 0)
        assert self.page().clipped is True


class TestWhatTheClipCheckMustNotMeasure:
    """Found by /ship's red team on 2026-09-16.

    `_clipped` took the rightmost word ANYWHERE in the money column. The column
    is located with a margin of left padding, so it also catches the trailing
    number group on the stamp line and the card block — either of which can sit
    against the edge of a page whose amounts are well clear of it. The docstring
    and NOTES.md both said it measured amounts, and the calibration was made on
    amount ink, so the code and its own justification disagreed.
    """

    @needs_engine
    def test_a_stamp_running_to_the_edge_is_not_a_clip(self, monkeypatch):
        rows = [
            ("", "Customer ID: 40100200300", None),
            ("", "APPLE", "7.49"),
            ("", "TAX", "0.00"),
            ("***", "BALANCE", "7.49"),
            ("", "CREDIT CARD", "7.49"),
            # Long enough that it runs past where the amounts stop.
            ("", "2019-03-04 11:07:00  2  118  0042  0000000", None),
        ]
        page = rc.read_capture(tr.transcribe(build_receipt(rows)), "Transaction_030419.png")
        assert page.balance == Decimal("7.49"), "the page read fine"
        assert page.clipped is False, "the stamp is not the amount column"


class TestWhatAnEngineReadingOfNothingCorroborates:
    """Found attacking the fix on 2026-09-16, and it is the same hole again.

    `_corroborates` walks the amounts the engine read. An engine reading of
    NOTHING means the loop never runs and every answer passes — which put the
    anchored route straight back to the single equation it was written to
    escape. Probed: with the engine reading no lines, a one-line answer worth
    exactly the statement's total was accepted.
    """

    def page(self, engine_amounts):
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=tuple(line(f"I{i}", a) for i, a in enumerate(engine_amounts)),
            balance=None,
            complete=False,
            clipped=True,
        )

    def answer(self, amounts):
        return {
            "lines": [{"description": f"M{i}", "amount": a} for i, a in enumerate(amounts)],
            "tax": "0.00",
            "balance": "100.23",
        }

    def test_a_page_nothing_could_be_read_off_has_no_second_fact(self):
        found = rc.from_reply(self.answer(["100.23"]), self.page([]), anchor=Decimal("100.23"))
        assert found is None

    def test_an_answer_that_replaces_every_amount_the_engine_read_is_refused(self):
        engine = [f"{10.00 + i:.2f}" for i in range(10)]
        found = rc.from_reply(self.answer(["100.23"]), self.page(engine), anchor=Decimal("100.23"))
        assert found is None

    def test_an_answer_may_not_bury_the_engines_reading_in_invented_lines(self):
        """One matched amount does not license a hundred unmatched ones."""
        flood = ["7.49"] + ["0.9274"] * 100
        found = rc.from_reply(self.answer(flood), self.page(["7.45"]), anchor=Decimal("100.23"))
        assert found is None

    def test_the_shape_a_real_clip_produces_is_still_accepted(self):
        """The clip corrupts last digits and destroys a row or two; it does not
        invent lines. Ten read, eleven claimed, every read amount reappearing."""
        engine = [f"{5.00 + i:.2f}" for i in range(10)]
        claimed = engine + ["5.23"]
        total = sum(Decimal(a) for a in claimed)
        reply = {
            "lines": [{"description": f"M{i}", "amount": a} for i, a in enumerate(claimed)],
            "tax": "0.00",
            "balance": str(total),
        }
        found = rc.from_reply(reply, self.page(engine), anchor=total)
        assert found is not None
        assert rc.foots(found) is None
