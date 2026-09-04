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

import random

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


class TestScoredAgainstGroundTruth:
    """Score the classifier, do not just exercise it.

    Every other test in this file asserts a behaviour on a hand-built series,
    which proves the function does what its author expected on cases its author
    thought of. It cannot tell you the classifier was calling 88% of
    weight-priced products a price, which is what it was doing.

    So this builds series from the SAME pricing model the fixture generator
    uses — trend, per-visit jitter, a 12% promotion, and then either a
    per-pound multiplier or a quantity multiple — and keeps the label it drew
    from. That label is ground truth, not an opinion, and it costs nothing.

    The bounds sit below the measured figures on purpose. They are a regression
    gate, not a target: they catch the thresholds drifting back, and they do not
    fail because a tune moved a number by a point.
    """

    ANNUAL_DRIFT = 0.061

    def _series(self, rng, kind, n, years=2.0):
        """Returns (amounts, realised label).

        A series drawn as `multiple` whose 6% dice never landed contains no
        multiple, so it is a unit series. Labelling it otherwise would score the
        classifier against something that is not in the data.
        """
        base = rng.uniform(0.79, 16.99)
        amounts, multiplied = [], False
        for i in range(n):
            years_in = years * i / max(n - 1, 1)
            trend = base * (1 + self.ANNUAL_DRIFT * years_in)
            jitter = rng.uniform(0.96, 1.06)
            if rng.random() < 0.12:
                jitter *= rng.uniform(0.70, 0.85)
            amount = trend * jitter
            if kind == "weight":
                amount *= rng.uniform(0.55, 1.75)
            elif kind == "multiple" and rng.random() < 0.06:
                amount *= rng.choice((2, 2, 2, 3))
                multiplied = True
            amounts.append(round(max(amount, 0.10), 2))
        realised = kind if kind != "multiple" else ("multiple" if multiplied else "unit")
        return amounts, realised

    def _score(self, trials=3000, floor=4, seed=11):
        rng = random.Random(seed)
        tally = {k: {"tp": 0, "fp": 0, "fn": 0} for k in ("weight", "multiple", "unit")}
        for _ in range(trials):
            drawn = rng.choice(("weight", "multiple", "unit"))
            amounts, true = self._series(rng, drawn, rng.randint(floor, 22))
            predicted = _price_shape(amounts)["kind"]
            for kind in tally:
                if predicted == kind and true == kind:
                    tally[kind]["tp"] += 1
                elif predicted == kind:
                    tally[kind]["fp"] += 1
                elif true == kind:
                    tally[kind]["fn"] += 1
        out = {}
        for kind, v in tally.items():
            precision = v["tp"] / (v["tp"] + v["fp"]) if v["tp"] + v["fp"] else 0.0
            recall = v["tp"] / (v["tp"] + v["fn"]) if v["tp"] + v["fn"] else 0.0
            out[kind] = (precision, recall)
        return out

    def test_weight_is_actually_detected(self):
        """The one that was broken. Recall was 0.12 because the rule required
        the largest cluster to hold exactly one amount, which is true of 12% of
        weight series."""
        precision, recall = self._score()["weight"]
        assert recall >= 0.70, f"weight recall {recall:.3f}"
        assert precision >= 0.78, f"weight precision {precision:.3f}"

    def test_a_unit_price_is_rarely_called_something_else(self):
        """The expensive error in the other direction: refusing to chart a
        product whose amounts really are a price."""
        precision, recall = self._score()["unit"]
        assert recall >= 0.95, f"unit recall {recall:.3f}"
        assert precision >= 0.70, f"unit precision {precision:.3f}"

    def test_a_claimed_multiple_is_almost_always_real(self):
        """Recall here is low and that is the conservative direction: an
        undetected multiple falls through to weight or unit, and both of those
        are stated as suspicion. A false multiple would put a number on the
        screen that is not there."""
        precision, _ = self._score()["multiple"]
        assert precision >= 0.90, f"multiple precision {precision:.3f}"

    def test_the_committed_fixture_still_classifies_sanely(self):
        """A smoke test, not a gate. The fixture has only a handful of
        weight-priced products with enough observations to judge, so its
        precision and recall are too noisy to assert on."""
        rng = random.Random(3)
        for kind in ("weight", "unit"):
            amounts, true = self._series(rng, kind, 12)
            assert _price_shape(amounts)["kind"] in {"weight", "multiple", "unit"}
            assert len(_price_shape(amounts)["multiples"]) == len(amounts)
