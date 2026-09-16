"""The synthetic pages a vision bake-off is scored against.

The bake-off itself needs a host and a pulled model, so it is not a test and
never will be. What IS testable is the thing a measurement rests on: that each
page reconciles, so `foots` passing or failing is a fact about the model rather
than about a typo in a fixture, and that the scoring separates reading from
parsing — which is the distinction the vision tier was silently dead for want
of.
"""

from decimal import Decimal

from tools.bakeoff_cases import CASES, CASES_BY_NAME, loose, marks_in, score


def perfect(case) -> dict:
    """The answer a flawless model would give to this page."""
    return {
        "lines": [{"description": name, "amount": str(amount)} for name, amount in case.purchases],
        "tax": str(case.tax or "0.00"),
        "balance": str(case.balance) if case.balance is not None else "",
    }


class TestEveryPageReconciles:
    def test_the_lines_and_the_tax_come_to_the_printed_balance(self):
        """Otherwise a model is scored against a page that does not add up.

        `from_reply` gates on the printed total and `foots` on the arithmetic,
        so a fixture whose own numbers disagree makes every gate result a
        measurement of the typo. This caught one: a discounted basket whose
        balance was 2.68 against lines and tax of 2.52.
        """
        for case in CASES:
            if case.balance is None:
                continue
            subtotal = sum((amount for _, amount in case.purchases), Decimal("0.00"))
            assert subtotal + (case.tax or Decimal("0.00")) == case.balance, case.name

    def test_the_cut_off_page_has_no_total_on_it(self):
        """It is the top half of a taller receipt, which is what makes it a case."""
        assert CASES_BY_NAME["cut_off"].balance is None

    def test_every_page_draws(self):
        for case in CASES:
            assert case.png().startswith(b"\x89PNG"), case.name

    def test_the_discount_page_actually_carries_negatives(self):
        assert CASES_BY_NAME["discount"].negatives


class TestScoringSeparatesReadingFromParsing:
    """The finding that the tier had never worked, kept as a test.

    A 30B model returned every amount behind a colon. It had read the page
    perfectly; `receipt._decimal` refused all of them and `from_reply` voids a
    whole answer on one unreadable amount, so the tier stored nothing, ever. A
    bake-off that scored those two together would have called that model blind.
    """

    def test_a_perfect_answer_scores_full_marks(self):
        case = CASES_BY_NAME["plain"]
        result = score(perfect(case), case, 1.0)
        assert result.recall == 1.0
        assert result.strict == 1.0
        assert result.total_ok
        assert not result.marks

    def test_a_currency_mark_costs_the_parse_but_not_the_reading(self):
        case = CASES_BY_NAME["plain"]
        reply = perfect(case)
        for row in reply["lines"]:
            row["amount"] = "€" + row["amount"]
        result = score(reply, case, 1.0)
        assert result.recall == 1.0, "the model read every line"
        assert result.strict == 0.0, "and the lane would have thrown all of them away"
        assert result.marks == ("€",)

    def test_a_mark_the_lane_tolerates_costs_nothing(self):
        """`_decimal` strips `$`, `§`, `s` and `:` from the front, and only those."""
        case = CASES_BY_NAME["plain"]
        reply = perfect(case)
        for row in reply["lines"]:
            row["amount"] = "$ " + row["amount"]
        result = score(reply, case, 1.0)
        assert result.strict == 1.0
        assert result.marks == ("$",)


class TestScoringCountsWhatTheGateCaresAbout:
    def test_furniture_is_counted_and_kept_out_of_recall(self):
        """TAX, BALANCE and the tender are the receipt talking about itself.

        Counted with the production filter rather than a second definition, so
        the rate is the rate the lane actually pays.
        """
        case = CASES_BY_NAME["plain"]
        reply = perfect(case)
        reply["lines"] += [
            {"description": "TAX", "amount": str(case.tax)},
            {"description": "BALANCE", "amount": str(case.balance)},
            {"description": "CREDIT", "amount": str(case.balance)},
        ]
        result = score(reply, case, 1.0)
        assert result.furniture == 3
        assert result.recall == 1.0

    def test_a_weight_qualifier_is_furniture_too(self):
        case = CASES_BY_NAME["weighed"]
        reply = perfect(case)
        reply["lines"].insert(0, {"description": "2.50 lb @ 1.50 / lb", "amount": "3.75"})
        assert score(reply, case, 1.0).furniture == 1

    def test_a_wrong_total_fails_even_on_a_perfect_basket(self):
        """The gate rests on the printed total and nothing else substitutes."""
        case = CASES_BY_NAME["plain"]
        reply = perfect(case)
        reply["balance"] = "99.99"
        result = score(reply, case, 1.0)
        assert result.recall == 1.0
        assert result.total_seen
        assert not result.total_ok

    def test_a_discount_returned_as_a_charge_is_caught(self):
        """The worst single error this project can make, so it is scored apart."""
        case = CASES_BY_NAME["discount"]
        reply = perfect(case)
        for row in reply["lines"]:
            row["amount"] = row["amount"].lstrip("-")
        assert not score(reply, case, 1.0).signs_ok

    def test_a_missing_line_costs_recall(self):
        case = CASES_BY_NAME["plain"]
        reply = perfect(case)
        reply["lines"].pop()
        result = score(reply, case, 1.0)
        assert result.matched == result.expected - 1

    def test_no_answer_is_recorded_rather_than_raised(self):
        result = score(None, CASES_BY_NAME["plain"], 0.0)
        assert result.failed
        assert result.recall == 0.0


class TestTheLooseParser:
    def test_it_finds_a_number_however_it_is_dressed(self):
        assert loose(": 4.99") == Decimal("4.99")
        assert loose("$4.99") == Decimal("4.99")
        assert loose("-1.50") == Decimal("-1.50")
        assert loose("1,234.00") == Decimal("1234.00")

    def test_it_gives_up_on_a_non_number(self):
        assert loose("") is None
        assert loose(None) is None
        assert loose("unreadable") is None

    def test_marks_are_reported_as_sent(self):
        assert marks_in(": 4.99") == ":"
        assert marks_in("4.99") == ""
