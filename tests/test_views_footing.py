"""The footing check: do a basket's lines add up to the retailer's own total?

Built entirely from fabricated transactions. The synthetic report generator
makes every basket foot exactly, which is the right default for a fixture but
means it cannot exercise the case this check exists for.

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
            TxnItem(description_raw="ITEM", upc="00000000001",
                    retail_amt=a, loyalty_amt=a)
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
