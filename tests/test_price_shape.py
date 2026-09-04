"""Telling a bigger basket apart from a bigger price.

A line in a Kroger response carries an amount and nothing else. No quantity, no
weight. So one amount at twice another can mean the price doubled, or it can
mean two of the thing went in the trolley, and the response does not say which.
Charting the second case as the first reports a two-can trip as 100% inflation.

`_price_shape` guesses which is which from the shape of a product's own amounts.
Every case below is fabricated. They are the arguments this function has to get
right, written down so a later tweak to one threshold cannot quietly break
another.

Nothing here is provable from the response. The classifier states a suspicion
and the view is expected to word it as one.
"""

import pytest

from unbagged.views import _price_shape


def kinds(*amounts: float) -> str:
    return _price_shape(list(amounts))["kind"]


class TestUnitPrices:
    def test_a_stable_price_with_small_drift_is_a_unit_price(self):
        assert kinds(2.19, 2.25, 2.19, 2.29, 2.35) == "unit"

    def test_a_price_that_doubles_gradually_is_still_a_unit_price(self):
        """The hardest case, and the one that makes the whole thing delicate.

        Ending at twice the starting price looks exactly like buying two, so the
        multiple test alone flags it. What separates them is the ground in
        between: a price that doubles is observed at the values on the way, and
        a second item appears from nowhere at exactly twice.
        """
        shape = _price_shape([2.00, 2.40, 2.90, 3.50, 4.00])
        assert shape["kind"] == "unit"
        assert not any(shape["multiples"]), "a gradual climb is not a quantity buy"

    def test_a_steady_decline_is_a_unit_price(self):
        assert kinds(9.00, 8.20, 7.40, 6.10, 5.50) == "unit"

    def test_a_narrow_spread_that_never_repeats_is_not_weight(self):
        """Four cents of variation is a price, not a scale reading."""
        assert kinds(5.12, 5.20, 5.31, 5.18) == "unit"


class TestQuantity:
    def test_one_trip_at_exactly_twice_the_usual_price_reads_as_two_items(self):
        shape = _price_shape([2.19, 2.19, 4.38, 2.19, 2.25])
        assert shape["kind"] == "multiple"
        assert shape["multiples"] == [None, None, 2, None, None]

    def test_the_commonest_amount_is_not_assumed_to_be_one_item(self):
        """A product usually bought in twos.

        The pair is the commonest amount, so taking the mode as the unit would
        make the single purchase read as a half-price sale. The smaller amount
        divides the mode exactly, which is what gives it away.
        """
        shape = _price_shape([4.38, 4.38, 4.38, 2.19])
        assert shape["kind"] == "multiple"
        assert shape["base"] == pytest.approx(2.19)
        assert shape["multiples"] == [2, 2, 2, None]


class TestWeight:
    def test_amounts_that_never_repeat_and_jump_around_read_as_weight(self):
        """What a per-pound item does: a different amount every trip.

        There is no unit price to recover here, and the view is expected to say
        so rather than draw a line through it.
        """
        assert kinds(5.12, 7.83, 6.41, 9.02, 4.77) == "weight"
        assert kinds(5.12, 7.83, 6.41, 9.02, 4.77, 8.10, 5.90) == "weight"

    def test_too_few_samples_to_call_it_weight(self):
        """Three readings is not enough to tell a scale from a price change."""
        assert kinds(5.12, 7.83, 6.41) != "weight"


class TestDegenerate:
    def test_no_amounts(self):
        assert _price_shape([])["kind"] == "unit"

    def test_one_amount(self):
        shape = _price_shape([3.00])
        assert shape["kind"] == "unit"
        assert shape["multiples"] == [None]

    def test_every_amount_identical(self):
        shape = _price_shape([2.50, 2.50, 2.50, 2.50])
        assert shape["kind"] == "unit"
        assert shape["base"] == pytest.approx(2.50)
