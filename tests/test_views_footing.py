"""The footing check: do a basket's lines add up to the retailer's own total?

Built entirely from fabricated transactions, so each shape can be stated
directly rather than drawn. The synthetic report generator now reproduces all
three outcomes at the proportions one real response measured — it previously made
every basket foot exactly, which left this check reachable only from here — and
`tests/test_api.py` asserts the fixture still reaches each one.

The sign convention is load-bearing. The Timeline row renders "over by" for a
positive delta and "under by" for a negative one, and those describe opposite
inconsistencies in a response, so getting the sign backwards would label every
marked basket with the wrong one.

The check does not attribute a cause and neither do these tests. Spot-checking
one real response by hand found the difference sitting in the supplied data
rather than in the parse, but that is a fact about one retailer, not a rule.
A future response could disagree for a different reason, and the check has to
keep reporting it either way.
"""

import pytest

from unbagged import db, repository, views
from unbagged.models import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    ParseResult,
    Provenance,
    RequestMeta,
    SourceDocument,
    Transaction,
    TxnItem,
)

PROV = Provenance(source_document_id=1, page=1, locator="$.customer[0].basket[0]")
DOC = SourceDocument("report.pdf", "a" * 64, "application/pdf")
WHEN = "2024-01-02T10:00:00"


def _basket(stated: float | None, *amounts: float) -> Transaction:
    return Transaction(
        occurred_at=WHEN,
        total_pre_discount=stated,
        provenance=PROV,
        items=tuple(
            TxnItem(description_raw="ITEM", upc="00000000001", retail_amt=a, loyalty_amt=a)
            for a in amounts
        ),
    )


def _stored(conn, stated: float | None, *amounts: float) -> dict:
    """Persist one fabricated basket and read it back through the timeline."""
    result = ParseResult(
        request=RequestMeta(retailer_id="kroger", display_name="Kroger", statute="CCPA"),
        transactions=(_basket(stated, *amounts),),
        # The timeline nulls every figure unless this category was provided.
        disclosures=(
            Disclosure(
                category=DisclosureCategory.SPECIFIC_PIECES,
                status=DisclosureStatus.PROVIDED,
                provenance=PROV,
            ),
        ),
    )
    request_id = repository.save_parse_result(conn, result, documents=[DOC])
    baskets = views.timeline(conn, request_id)["baskets"]
    assert len(baskets) == 1
    return baskets[0]


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "footing.sqlite")
    db.migrate(connection)
    yield connection
    connection.close()


class TestFooting:
    def test_a_basket_that_foots_reports_a_zero_delta(self, conn):
        assert _stored(conn, 6.00, 1.00, 2.00, 3.00)["stated_pre_discount_delta"] == 0.0

    def test_a_line_the_stated_total_left_out_reads_positive(self, conn):
        """The retailer itemised a charge and then excluded it from its own total.

        A statutory bag fee is the case that surfaced this: the line is present,
        the stated total does not count it, and the lines therefore come to more.
        Confirmed against the source, so the app reports it rather than trying
        to reconcile the two numbers on the retailer's behalf.
        """
        delta = _stored(conn, 6.00, 1.00, 2.00, 3.00, 0.10)["stated_pre_discount_delta"]
        assert delta == pytest.approx(0.10)
        assert delta > 0, "over, not under"

    def test_lines_short_of_the_stated_total_read_negative(self, conn):
        """The other direction: the stated total exceeds the itemised lines."""
        delta = _stored(conn, 6.00, 1.00, 2.00)["stated_pre_discount_delta"]
        assert delta == pytest.approx(-3.00)
        assert delta < 0, "under, not over"

    def test_no_stated_total_is_null_rather_than_a_zero_delta(self, conn):
        """Nothing to check against is not the same as checking out clean.

        A zero here would put a basket the retailer never totalled into the same
        bucket as one whose arithmetic was verified.
        """
        assert _stored(conn, None, 1.00, 2.00)["stated_pre_discount_delta"] is None

    def test_rounding_noise_does_not_trip_the_flag(self, conn):
        """Three lines that sum to a repeating float still foot.

        The UI flags at one cent. Anything below that is an artefact of adding
        currency as floats, and flagging it would badge every row.
        """
        delta = _stored(conn, 0.30, 0.10, 0.10, 0.10)["stated_pre_discount_delta"]
        assert abs(delta) < 0.01

    def test_the_fee_case_does_not_disturb_the_other_totals(self, conn):
        """An excluded fee is a finding about the total, not about the basket.

        Shelf and paid still come from the lines, so an under-stated total must
        not quietly pull them down with it.
        """
        basket = _stored(conn, 6.00, 1.00, 2.00, 3.00, 0.10)
        assert basket["shelf_total"] == pytest.approx(6.10)
        assert basket["paid_total"] == pytest.approx(6.10)
        assert basket["item_count"] == 4

    def test_a_basket_with_no_lines_has_nothing_to_foot(self, conn):
        """A total with no lines under it is not a basket that failed to add up.

        Before this, the comparison ran anyway: shelf summed to zero against a
        stated total, so every basket in a response that disclosed no line items
        reported a shortfall the size of the whole basket, and the UI marked all
        of them as not reconciling. The retailer had simply never itemised.
        """
        basket = _stored(conn, 41.10)
        assert basket["item_count"] == 0
        assert basket["lines_disclosed"] is False
        assert basket["stated_pre_discount_delta"] is None
        # And the line-derived figures are absent rather than zero: a zero here
        # would say the basket cost nothing, which the response never stated.
        assert basket["shelf_total"] is None
        assert basket["paid_total"] is None
        assert basket["saved_total"] is None
        # The retailer's own number survives, because it is the only real money
        # in a response like this.
        assert basket["total_pre_discount"] == pytest.approx(41.10)

    def test_lines_that_fall_short_of_the_stated_total_still_read_negative(self, conn):
        """The case the item-count discriminator must not swallow.

        Keying the "nothing to foot" branch off the delta equalling minus the
        stated total would have caught this basket too, and this shortfall is a
        real disagreement in the response that the reader should see.
        """
        basket = _stored(conn, 10.00, 3.00, 4.00)
        assert basket["lines_disclosed"] is True
        assert basket["stated_pre_discount_delta"] == pytest.approx(-3.00)


class TestShelfStandIn:
    """Baskets where some lines disclose a shelf price and some do not.

    A line with no shelf price stands in at what was paid, so it adds nothing
    to a saving. A basket with no shelf price on any line has no shelf total
    and no saving at all. The header is the sum of the rows under it.
    """

    @staticmethod
    def _store(conn) -> int:
        def basket(when: str, *lines: tuple[float | None, float]) -> Transaction:
            return Transaction(
                occurred_at=when,
                provenance=PROV,
                items=tuple(
                    TxnItem(
                        description_raw="ITEM", upc=f"0000000000{n}", retail_amt=r, loyalty_amt=p
                    )
                    for n, (r, p) in enumerate(lines)
                ),
            )

        result = ParseResult(
            request=RequestMeta(retailer_id="kroger", display_name="Kroger", statute="CCPA"),
            transactions=(
                basket("2024-01-02", (2.00, 1.50), (None, 3.00)),
                basket("2024-01-03", (None, 4.00)),
            ),
            disclosures=(
                Disclosure(
                    category=DisclosureCategory.SPECIFIC_PIECES,
                    status=DisclosureStatus.PROVIDED,
                    provenance=PROV,
                ),
            ),
        )
        return repository.save_parse_result(conn, result, documents=[DOC])

    def test_rows_stand_a_missing_shelf_price_in_at_what_was_paid(self, conn):
        mixed, paid_only = views.timeline(conn, self._store(conn))["baskets"]
        assert (mixed["shelf_total"], mixed["paid_total"], mixed["saved_total"]) == (
            5.00,
            4.50,
            0.50,
        )
        assert (paid_only["shelf_total"], paid_only["saved_total"]) == (None, None)
        assert paid_only["paid_total"] == 4.00

    def test_the_detail_agrees_with_its_row(self, conn):
        mixed, paid_only = views.timeline(conn, self._store(conn))["baskets"]
        for row in (mixed, paid_only):
            detail = views.transaction_detail(conn, row["id"])
            assert (detail["shelf_total"], detail["paid_total"], detail["saved_total"]) == (
                row["shelf_total"],
                row["paid_total"],
                row["saved_total"],
            )

    def test_the_header_is_the_sum_of_the_rows(self, conn):
        timeline = views.timeline(conn, self._store(conn))
        stats, baskets = timeline["stats"], timeline["baskets"]
        assert stats["total_paid"] == pytest.approx(sum(b["paid_total"] for b in baskets))
        assert stats["total_shelf"] == pytest.approx(
            sum(b["shelf_total"] for b in baskets if b["shelf_total"] is not None)
        )
        assert stats["total_saved"] == pytest.approx(
            sum(b["saved_total"] for b in baskets if b["saved_total"] is not None)
        )

    def test_no_internal_count_reaches_a_response(self, conn):
        timeline = views.timeline(conn, self._store(conn))
        assert "shelf_lines" not in timeline["stats"]
        for row in timeline["baskets"]:
            assert "shelf_lines" not in row
            assert "shelf_lines" not in views.transaction_detail(conn, row["id"])


class TestCountOrMeasure:
    """`quantity` holds a count for Albertsons and a weight for H Mart, and one
    line cannot tell 2.00 lb from two items. The response decides."""

    @staticmethod
    def _history(conn, quantities: list[float]) -> list[dict]:
        transactions = tuple(
            Transaction(
                occurred_at=f"2024-01-{day:02d}",
                provenance=PROV,
                items=(
                    TxnItem(
                        description_raw="ITEM",
                        upc="00000000001",
                        quantity=q,
                        retail_amt=None,
                        loyalty_amt=3.00 * (q if q >= 1 else 1),
                    ),
                ),
            )
            for day, q in enumerate(quantities, start=1)
        )
        result = ParseResult(
            request=RequestMeta(retailer_id="kroger", display_name="Kroger", statute="CCPA"),
            transactions=transactions,
            disclosures=(
                Disclosure(
                    category=DisclosureCategory.SPECIFIC_PIECES,
                    status=DisclosureStatus.PROVIDED,
                    provenance=PROV,
                ),
            ),
        )
        request_id = repository.save_parse_result(conn, result, documents=[DOC])
        (product,) = views.price_history(conn, request_id, min_observations=2)["products"]
        return product["points"]

    def test_whole_counts_are_divided(self, conn):
        points = self._history(conn, [1, 2, 3, 1])
        assert [p["count"] for p in points] == [None, 2, 3, None]
        assert {p["unit_paid"] for p in points} == {3.0}

    def test_a_response_with_any_fraction_divides_by_nothing(self, conn):
        # 2.0 here is a weight of exactly two pounds, not two items.
        points = self._history(conn, [0.57, 2.0, 1.25, 2.0])
        assert all(p["count"] is None for p in points)
        assert [p["unit_paid"] for p in points] == [p["paid_amt"] for p in points]


class TestFloatNoiseIsStillACount:
    def test_one_artefact_does_not_turn_counts_into_measures(self, conn):
        points = TestCountOrMeasure._history(conn, [1, 2.0000000000000004, 3, 1])
        assert [p["count"] for p in points] == [None, 2, 3, None]


class TestCompareCountsOnlyKnownCategories:
    """Third-pass finding: Compare counted distinct category strings, so a row
    for a category the enum does not name made seven assessed look like eight,
    where Compliance, which keys on the enum, said one went unassessed."""

    def test_an_unknown_category_does_not_complete_the_set(self, conn):
        assessed = list(DisclosureCategory)[:-1]
        result = ParseResult(
            request=RequestMeta(retailer_id="kroger", display_name="Kroger", statute="CCPA"),
            disclosures=tuple(
                Disclosure(category=c, status=DisclosureStatus.ABSENT, provenance=PROV)
                for c in assessed
            ),
        )
        request_id = repository.save_parse_result(conn, result, documents=[DOC])
        conn.execute(
            "INSERT INTO disclosure (request_id, category, status) VALUES (?, ?, ?)",
            (request_id, "retired_category", DisclosureStatus.ABSENT.value),
        )
        (row,) = views.compliance(conn)["rows"]
        assert row["unassessed_count"] == 1
        (compared,) = views.compare(conn)["requests"]
        assert compared["absent_disclosures"] is None
