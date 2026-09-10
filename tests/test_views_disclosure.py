"""When a count may be written as zero, and when it must be an em dash.

`compare()` renders three inventory figures — identifiers held, inferred
attributes, and how many of those were bought in from elsewhere. Each of them is
a claim about a retailer the moment it renders a number, and a zero is the
strongest claim of the three: it says the company answered and the answer was
none.

The predicate that gates them used to be `disclosed_specific_pieces()`, which
asks whether the response disclosed *purchases*. That is a different question,
and H Mart is where the difference showed: its adapter grades SPECIFIC_PIECES
`partial` on purpose — every visit's date, store and total, nothing about what
was in any basket — so a purchase predicate answered yes and Compare printed
"Inferred attributes 0" over a response that never mentioned inferences.

Two directions matter and both are tested here. Gating too little states a zero
the response never supported. Gating too much hides a number the response
plainly gave: H Mart discloses a card number, so on that same column the
identifier count must stay 1 while the inference count becomes a dash.

Built from fabricated parse results rather than fixtures, because the point is
to drive all four disclosure statuses and no fixture carries more than one.
"""

import pytest

from unbagged import db, repository, views
from unbagged.models import (
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    Identity,
    IdType,
    Inference,
    InferenceOrigin,
    ParseResult,
    Provenance,
    RequestMeta,
    SourceDocument,
    Transaction,
)

PROV = Provenance(source_document_id=1, page=1, locator="$.customer[0]")
DOC = SourceDocument("report.pdf", "b" * 64, "application/pdf")
WHEN = "2024-01-02T10:00:00"


def _save(
    conn,
    status: DisclosureStatus | None,
    *,
    purchases: int = 0,
    identities: int = 0,
    inferences: int = 0,
    retailer: str = "kroger",
) -> int:
    """Persist one response with the disclosure grade and row counts asked for.

    `status=None` writes no disclosure row at all, which is not the same as
    ABSENT: one is a category the adapter looked for and did not find, the other
    is a response nothing has graded.
    """
    result = ParseResult(
        request=RequestMeta(retailer_id=retailer, display_name=retailer.title(), statute="CCPA"),
        transactions=tuple(
            Transaction(occurred_at=WHEN, total_pre_discount=10.0, provenance=PROV)
            for _ in range(purchases)
        ),
        identities=tuple(
            Identity(id_type=IdType.LOYALTY_CARD, value=f"card-{i}", provenance=PROV)
            for i in range(identities)
        ),
        inferences=tuple(
            Inference(
                label=f"score_{i}",
                value_raw="0.5",
                origin=InferenceOrigin.FIRST_PARTY_MODEL,
                provenance=PROV,
            )
            for i in range(inferences)
        ),
        disclosures=(
            ()
            if status is None
            else (
                Disclosure(
                    category=DisclosureCategory.SPECIFIC_PIECES,
                    status=status,
                    provenance=PROV,
                ),
            )
        ),
    )
    return repository.save_parse_result(conn, result, documents=[DOC])


def _row(conn, request_id: int) -> dict:
    rows = {r["id"]: r for r in views.compare(conn)["requests"]}
    return rows[request_id]


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "disclosure.sqlite")
    db.migrate(connection)
    yield connection
    connection.close()


class TestZeroIsClaimable:
    """The predicate on its own, across every grade a disclosure can carry."""

    def test_provided_licenses_a_zero(self, conn):
        request_id = _save(conn, DisclosureStatus.PROVIDED)
        assert views.zero_is_claimable(conn, request_id) is True

    def test_partial_does_not(self, conn):
        """`legal-basis.md` grades partial as addressed but incompletely.

        An inventory of things the response did not enumerate is exactly the
        incomplete part, so "none" is not a claim this response made.
        """
        request_id = _save(conn, DisclosureStatus.PARTIAL, purchases=3)
        assert views.zero_is_claimable(conn, request_id) is False

    def test_absent_does_not(self, conn):
        request_id = _save(conn, DisclosureStatus.ABSENT)
        assert views.zero_is_claimable(conn, request_id) is False

    def test_a_response_with_no_disclosure_row_does_not(self, conn):
        request_id = _save(conn, None)
        assert views.zero_is_claimable(conn, request_id) is False

    def test_it_is_not_the_same_question_as_disclosed_specific_pieces(self, conn):
        """The two predicates must disagree on partial-with-purchases.

        This is the whole reason the second one exists. If a refactor ever
        collapses them, this fails rather than the H Mart rendering quietly
        regressing.
        """
        request_id = _save(conn, DisclosureStatus.PARTIAL, purchases=3)
        assert views.disclosed_specific_pieces(conn, request_id) is True
        assert views.zero_is_claimable(conn, request_id) is False


class TestCompareCounts:
    """What the three inventory cells actually render."""

    def test_a_zero_under_partial_is_not_disclosed(self, conn):
        """The H Mart case. Regression guard for issue #43.

        Purchases disclosed, inferences never addressed. Rendering 0 states
        that the retailer holds no inferences, which the response did not say.
        """
        request_id = _save(conn, DisclosureStatus.PARTIAL, purchases=3)
        row = _row(conn, request_id)
        assert row["inference_count"] is None
        assert row["appended_inference_count"] is None

    def test_a_non_zero_count_survives_partial(self, conn):
        """The overcorrection guard, and the other half of the H Mart column.

        H Mart discloses a card number. Gating the whole figure rather than the
        zero would turn a disclosed 1 into an em dash, which is the same
        overclaim pointing the other way.
        """
        request_id = _save(conn, DisclosureStatus.PARTIAL, purchases=3, identities=1)
        row = _row(conn, request_id)
        assert row["identifier_count"] == 1, "a disclosed fact, whatever the grade"
        assert row["inference_count"] is None, "on the same column, at the same time"

    def test_a_genuine_zero_survives_provided(self, conn):
        """The gate must not swallow real zeros.

        A retailer that answered the category in full and holds no inferences
        said something, and the app has to be able to report it.
        """
        request_id = _save(conn, DisclosureStatus.PROVIDED, purchases=3)
        row = _row(conn, request_id)
        assert row["inference_count"] == 0
        assert row["identifier_count"] == 0

    def test_counts_render_even_when_purchases_were_not_disclosed(self, conn):
        """A response can hold identifiers and no baskets.

        `disclosed_specific_pieces()` answers no here, and under the old gate
        that nulled the identifier count — a purchase predicate deciding an
        inventory question. The rows exist and are disclosed, so they render.
        """
        request_id = _save(conn, DisclosureStatus.ABSENT, identities=2, inferences=1)
        row = _row(conn, request_id)
        assert row["disclosed"] is False
        assert row["identifier_count"] == 2
        assert row["inference_count"] == 1

    def test_the_purchase_figures_are_untouched(self, conn):
        """`disclosed` still governs the headline numbers, and still nulls them.

        Changing that predicate instead of adding a sibling would blank H Mart's
        Timeline and put "disclosed no data" over a column holding 3 visits.
        """
        request_id = _save(conn, DisclosureStatus.ABSENT, purchases=3)
        row = _row(conn, request_id)
        assert row["disclosed"] is False
        assert row["visits"] is None
        assert row["total_paid"] is None
