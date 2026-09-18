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


class TestAProductWithNoCode:
    """A retailer may disclose what you bought without disclosing a code for it.

    H Mart's receipts carry a description and an amount and no code anywhere.
    Both product views keyed on `upc IS NOT NULL`, so they showed nothing at all
    over 388 disclosed line items — this tool's answer to "what did you buy" was
    an empty page, about the one response that had answered it.
    """

    def _request(self, conn, items):
        from unbagged.models import TxnItem

        return repository.save_parse_result(
            conn,
            ParseResult(
                request=RequestMeta(retailer_id="r", display_name="R"),
                disclosures=(
                    Disclosure(
                        category=DisclosureCategory.SPECIFIC_PIECES,
                        status=DisclosureStatus.PARTIAL,
                        provenance=PROV,
                    ),
                ),
                transactions=(
                    Transaction(
                        occurred_at="2019-03-04T10:00:00",
                        total_pre_discount=sum(amount for _, _, amount in items),
                        items=tuple(
                            TxnItem(description_raw=name, upc=upc, retail_amt=amount)
                            for name, upc, amount in items
                        ),
                    ),
                ),
            ),
        )

    def test_a_named_product_with_no_code_is_still_a_product(self, conn):
        request_id = self._request(conn, [("PEELED GARLIC 1 LB", None, 6.99)])
        index = views.product_index(conn, request_id)
        assert index["total_products"] == 1
        assert index["products"][0]["description"] == "PEELED GARLIC 1 LB"
        # Null, not the name repeated. It is rendered as a numeral in the mono
        # stack, so a name standing in for it would be the name twice, once in
        # a face chosen for digits.
        assert index["products"][0]["upc"] is None

    def test_a_code_is_still_what_identifies_a_product_where_one_exists(self, conn):
        """Two spellings of one code are one product; that must not regress.

        The whole point of a code is that it survives the retailer renaming
        something between visits, and keying on the name would split it in two.
        """
        request_id = self._request(
            conn, [("BANANAS", "00000001", 1.00), ("BANANA", "00000001", 1.00)]
        )
        index = views.product_index(conn, request_id)
        assert index["total_products"] == 1

    def test_a_line_with_no_readable_name_names_no_product(self, conn):
        """The same rule the zero-value placeholder rows already get.

        The line is real — its amount was read and checked like every other, and
        it counts towards the basket. There is simply nothing to call it, and an
        entry in an index of what you bought has to be something a reader can
        recognise.
        """
        request_id = self._request(conn, [("", None, 4.00), ("MINT", None, 1.00)])
        index = views.product_index(conn, request_id)
        assert [p["description"] for p in index["products"]] == ["MINT"]

    def test_the_headline_count_matches_the_page_under_it(self, conn):
        """A relationship, not two expected numbers.

        `stats` counted products its own product list did not contain: it
        admitted lines that are only ever negative, which `product_index` has
        always refused so that a refund cannot create an entry for something you
        gave back. Three different answers to "what is a product" lived in this
        module and the docstring already said they must not.
        """
        request_id = self._request(conn, [("MINT", None, 1.00), ("SC - MINT", None, -0.50)])
        assert (
            views.stats(conn, request_id)["distinct_products"]
            == (views.product_index(conn, request_id)["total_products"])
        )


class TestARequestThatIsOnlyPartlyItemised:
    """A retailer that answered twice, covering different ground each time."""

    def _mixed(self, conn, itemised: int, total: int):
        from unbagged.models import TxnItem

        return repository.save_parse_result(
            conn,
            ParseResult(
                request=RequestMeta(retailer_id="r", display_name="R"),
                disclosures=(
                    Disclosure(
                        category=DisclosureCategory.SPECIFIC_PIECES,
                        status=DisclosureStatus.PARTIAL,
                        provenance=PROV,
                    ),
                ),
                transactions=tuple(
                    Transaction(
                        occurred_at=f"2026-01-{n + 1:02d}T10:00:00",
                        total_pre_discount=5.00,
                        items=(
                            (TxnItem(description_raw="MINT", retail_amt=5.00),)
                            if n < itemised
                            else ()
                        ),
                    )
                    for n in range(total)
                ),
            ),
        )

    def test_the_count_of_itemised_visits_is_reported(self, conn):
        """`lines_disclosed` is one bit and cannot say "two thirds".

        Without this the timeline's explanation vanished on a mixed response —
        the bit reads true — and the visits with nothing to show became rows
        that would not open, with nothing on screen saying why.
        """
        stats = views.stats(conn, self._mixed(conn, itemised=2, total=5))
        assert stats["lines_disclosed"] is True
        assert stats["itemised_count"] == 2
        assert stats["basket_count"] == 5

    def test_a_fully_itemised_response_has_nothing_to_qualify(self, conn):
        stats = views.stats(conn, self._mixed(conn, itemised=3, total=3))
        assert stats["itemised_count"] == stats["basket_count"] == 3

    def test_a_response_with_no_lines_says_so_the_way_it_already_did(self, conn):
        """Zero would be a second, weaker way of saying what a null already says."""
        stats = views.stats(conn, self._mixed(conn, itemised=0, total=3))
        assert stats["lines_disclosed"] is False
        assert stats["itemised_count"] is None


class TestTheHeadlineFigureOnAMixedResponse:
    """Found by /ship's design specialist on 2026-09-16.

    `total_paid` is summed from line items, so on a response that itemised only
    some of its visits it covered only those — and sat beside a "Visits" figure
    counting all of them. On the real response it summed 43 of 67 and understated
    by roughly a thousand dollars, with nothing on screen naming its scope. The
    largest number on the page is the last one that should need a footnote.
    """

    def _mixed(self, conn, itemised: int, total: int, each: float):
        from unbagged.models import TxnItem

        return repository.save_parse_result(
            conn,
            ParseResult(
                request=RequestMeta(retailer_id="r", display_name="R"),
                disclosures=(
                    Disclosure(
                        category=DisclosureCategory.SPECIFIC_PIECES,
                        status=DisclosureStatus.PARTIAL,
                        provenance=PROV,
                    ),
                ),
                transactions=tuple(
                    Transaction(
                        occurred_at=f"2019-03-{n + 1:02d}T10:00:00",
                        total_pre_discount=each,
                        items=(
                            (TxnItem(description_raw="MINT", retail_amt=each),)
                            if n < itemised
                            else ()
                        ),
                    )
                    for n in range(total)
                ),
            ),
        )

    def test_the_headline_covers_every_visit_not_just_the_itemised_ones(self, conn):
        """A relationship, not a literal: it equals what the retailer stated
        across all of them, never the partial sum."""
        request_id = self._mixed(conn, itemised=2, total=5, each=10.0)
        stats = views.stats(conn, request_id)
        assert stats["basket_count"] == 5
        assert stats["itemised_count"] == 2
        assert stats["total_paid"] == stats["total_stated"] == 50.0
        assert stats["total_paid"] != 20.0, "this is the partial sum the bug showed"

    def test_a_fully_itemised_response_still_sums_its_lines(self, conn):
        """The control. Without it the fix above passes by always using the
        stated total, which would throw away every loyalty price."""
        request_id = self._mixed(conn, itemised=3, total=3, each=10.0)
        stats = views.stats(conn, request_id)
        assert stats["itemised_count"] == stats["basket_count"]
        assert stats["total_paid"] == 30.0

    def test_the_narrower_figures_keep_the_scope_they_describe(self, conn):
        """`total_shelf` and `total_saved` are line-item facts and stay so —
        only the headline changes, because only it is read as covering the
        visits counted beside it."""
        request_id = self._mixed(conn, itemised=2, total=5, each=10.0)
        stats = views.stats(conn, request_id)
        assert stats["total_shelf"] == 20.0
        assert stats["line_count"] == 2


class TestThePriceHistoryContract:
    """`price_history` changed SQL and gained a `key` field with no test at any
    tier. Found by /ship's testing and coverage passes on 2026-09-16.

    The frontend keys its rows, its selection and its category colours on `key`
    (`PriceHistory.tsx`). If it stopped being emitted, every Python test and the
    TypeScript build still pass and the UI silently renders duplicate undefined
    React keys with every dot the same colour.
    """

    def _bought(self, conn, purchases):
        from unbagged.models import TxnItem

        return repository.save_parse_result(
            conn,
            ParseResult(
                request=RequestMeta(retailer_id="r", display_name="R"),
                disclosures=(
                    Disclosure(
                        category=DisclosureCategory.SPECIFIC_PIECES,
                        status=DisclosureStatus.PARTIAL,
                        provenance=PROV,
                    ),
                ),
                transactions=tuple(
                    Transaction(
                        occurred_at=f"2019-03-{day:02d}T10:00:00",
                        total_pre_discount=amount,
                        items=(TxnItem(description_raw=name, upc=upc, retail_amt=amount),),
                    )
                    for day, (name, upc, amount) in enumerate(purchases, start=1)
                ),
            ),
        )

    def test_a_product_with_no_code_gets_a_price_series(self, conn):
        request_id = self._bought(conn, [("GREEN ONION", None, 0.49), ("GREEN ONION", None, 0.79)])
        series = views.price_history(conn, request_id, min_observations=2)["products"]
        assert len(series) == 1
        assert series[0]["description"] == "GREEN ONION"
        assert series[0]["upc"] is None, "absent, not filled with the name"

    def test_every_series_carries_an_identity_the_view_can_key_on(self, conn):
        """A relationship, not a literal: as many distinct keys as series."""
        request_id = self._bought(
            conn,
            [
                ("MINT", None, 1.0),
                ("MINT", None, 1.5),
                ("BASIL", "00000001", 2.0),
                ("BASIL", "00000001", 2.5),
            ],
        )
        series = views.price_history(conn, request_id, min_observations=2)["products"]
        assert all(entry.get("key") for entry in series)
        assert len({entry["key"] for entry in series}) == len(series)

    def test_the_index_and_the_series_agree_on_what_a_product_is(self, conn):
        """The two views key products the same way, which is the whole reason
        `PRODUCT_KEY` exists as one definition."""
        request_id = self._bought(conn, [("MINT", None, 1.0), ("MINT", None, 1.5)])
        index_keys = {p["key"] for p in views.product_index(conn, request_id)["products"]}
        series_keys = {
            s["key"] for s in views.price_history(conn, request_id, min_observations=2)["products"]
        }
        assert series_keys <= index_keys


class TestWhatTheIndexCallsAProduct:
    """Labels, and the lines that turn out not to be products at all.

    Every name here is invented. The SHAPES come from measurement — a price
    printed into the name field, deposit vocabulary standing alone, a drink
    carrying the same word beside a size — but no value resembles anything in
    the corpus, because `tools/scan_pii.py` has no rule for a product name and
    the habit is the only guard. See `CONTRIBUTING.md`.
    """

    def _bought(self, conn, purchases):
        from unbagged.models import TxnItem

        return repository.save_parse_result(
            conn,
            ParseResult(
                request=RequestMeta(retailer_id="r", display_name="R"),
                disclosures=(
                    Disclosure(
                        category=DisclosureCategory.SPECIFIC_PIECES,
                        status=DisclosureStatus.PARTIAL,
                        provenance=PROV,
                    ),
                ),
                transactions=tuple(
                    Transaction(
                        occurred_at=f"2019-03-{day:02d}T10:00:00",
                        total_pre_discount=amount,
                        items=(TxnItem(description_raw=name, upc=upc, retail_amt=amount),),
                    )
                    for day, (name, upc, amount) in enumerate(purchases, start=1)
                ),
            ),
        )

    def test_a_price_printed_into_the_name_does_not_reach_the_reader(self, conn):
        request_id = self._bought(conn, [("$4.99 OAT MILK HALF GAL", "00000001", 4.99)])

        product = views.product_index(conn, request_id)["products"][0]

        assert product["description"] == "OAT MILK HALF GAL"

    def test_the_printed_spelling_travels_beside_the_label(self, conn):
        """The timeline filters by name where a retailer disclosed no codes, and
        it matches `description_raw`. `App.tsx` sends THIS field.

        Not a regression test, and worth being honest about why. The cleaned
        label would also find the visit today: cleaning only ever strips a
        prefix, so the label stays a substring of the printed name and the
        timeline's LIKE is a substring test. Nothing declares that, nothing
        tests it from the other side, and a cleaner that touched the middle or
        the end of a name would break every name-filtered timeline in silence.
        Matching on what the retailer printed does not rest on the accident.
        """
        request_id = self._bought(conn, [("$4.99 OAT MILK HALF GAL", None, 4.99)])

        product = views.product_index(conn, request_id)["products"][0]

        assert product["match_name"] == "$4.99 OAT MILK HALF GAL"
        assert product["description"] == "OAT MILK HALF GAL"
        # The field really does find the visit, which is the part that matters.
        found = views.timeline(conn, request_id, query=product["match_name"])
        assert len(found["baskets"]) == 1

    def test_a_deposit_line_is_not_something_you_bought(self, conn):
        request_id = self._bought(
            conn, [("$0.05 CRV DEPOSIT", "00000001", 0.05), ("SOURDOUGH BOULE", None, 5.00)]
        )

        index = views.product_index(conn, request_id)

        assert [p["description"] for p in index["products"]] == ["SOURDOUGH BOULE"]
        assert index["set_aside"] == 1

    def test_a_drink_carrying_the_same_word_is_still_a_product(self, conn):
        """The expensive mistake. These carry codes and are bought repeatedly;
        matching the word rather than the whole name deletes real purchases.
        """
        request_id = self._bought(conn, [("2.5 LITR 12 CRV", "00000002", 3.49)])

        index = views.product_index(conn, request_id)

        assert [p["description"] for p in index["products"]] == ["2.5 LITR 12 CRV"]
        assert index["set_aside"] == 0

    def test_a_name_that_is_only_punctuation_names_no_product(self, conn):
        request_id = self._bought(conn, [("©", None, 2.00), ("SOURDOUGH BOULE", None, 5.00)])

        index = views.product_index(conn, request_id)

        assert [p["description"] for p in index["products"]] == ["SOURDOUGH BOULE"]
        assert index["set_aside"] == 1

    def test_a_percentage_in_a_name_survives(self, conn):
        request_id = self._bought(conn, [("2% MILK GALLON", None, 3.19)])

        index = views.product_index(conn, request_id)

        assert [p["description"] for p in index["products"]] == ["2% MILK GALLON"]

    def test_two_rows_that_would_read_alike_keep_what_was_printed(self, conn):
        """Cleaning is abandoned for both rather than applied to one.

        They are two products — different keys, so the retailer distinguished
        them — and one name twice in an alphabetical list reads as double
        counting. Appending the code instead is what the 2026-09-15 decisions
        row forbids, and it would reach the downloadable index poster.
        """
        request_id = self._bought(
            conn, [("$1.99 RYE LOAF", "00000001", 1.99), ("RYE LOAF", "00000002", 2.49)]
        )

        labels = {p["description"] for p in views.product_index(conn, request_id)["products"]}

        assert labels == {"$1.99 RYE LOAF", "RYE LOAF"}

    def test_prices_and_products_call_one_product_by_one_name(self, conn):
        """**Regression.** The rule lived in two places and only one was
        changed, so the same product showed a tidy name on one tab and the
        printed name on the next, with nothing on screen to explain it.
        """
        request_id = self._bought(
            conn, [("$4.99 OAT MILK", "00000001", 4.99), ("$5.49 OAT MILK", "00000001", 5.49)]
        )

        indexed = views.product_index(conn, request_id)["products"][0]["description"]
        priced = views.price_history(conn, request_id, min_observations=2)["products"][0][
            "description"
        ]

        assert indexed == priced == "OAT MILK"

    def test_prices_refuses_a_deposit_line_the_index_already_refused(self, conn):
        """Regression: Prices charted a price series for a line Products had
        set aside, so one tab said it was not a product while the tab beside it
        tracked what it cost over four visits.

        The refusal was written into `product_index` only. `price_history` took
        the shared label and never asked whether the thing it had labelled was
        a product, which is the same split D3 closed for the label itself.
        """
        request_id = self._bought(
            conn,
            [
                ("$0.05 CRV DEPOSIT", "00000001", 0.05),
                ("$0.05 CRV DEPOSIT", "00000001", 0.05),
                ("SOURDOUGH BOULE", None, 5.00),
                ("SOURDOUGH BOULE", None, 5.50),
            ],
        )

        priced = views.price_history(conn, request_id, min_observations=2)["products"]

        assert [p["description"] for p in priced] == ["SOURDOUGH BOULE"]

    def test_the_headline_count_still_matches_the_page_after_a_line_is_set_aside(self, conn):
        """**Regression.** `stats` counted in SQL over the raw text while the
        page filtered in Python, so the figure stayed at the pre-label number
        and the list under it was shorter. The module docstring already warned
        that several answers to "what is a product" must not live here.
        """
        request_id = self._bought(
            conn, [("$0.05 CRV DEPOSIT", "00000001", 0.05), ("SOURDOUGH BOULE", None, 5.00)]
        )

        index = views.product_index(conn, request_id)

        assert views.stats(conn, request_id)["distinct_products"] == index["total_products"]
        assert index["total_products"] == len(index["products"])

    def test_prices_keeps_printed_names_where_cleaning_would_read_alike(self, conn):
        """The collision rule is the index's AND the chart's.

        Two series under one name is worse on Prices than in a list: the reader
        is comparing prices and has nothing to tell the lines apart. The guard
        was written into `product_index` only, so Products restored the printed
        names while Prices showed both series as one.
        """
        request_id = self._bought(
            conn,
            [
                ("$1.99 RYE LOAF", "00000001", 1.99),
                ("$1.99 RYE LOAF", "00000001", 2.49),
                ("RYE LOAF", "00000002", 3.99),
                ("RYE LOAF", "00000002", 4.49),
            ],
        )

        priced = views.price_history(conn, request_id, min_observations=2)["products"]

        assert sorted(p["description"] for p in priced) == ["$1.99 RYE LOAF", "RYE LOAF"]

    def test_prices_refuses_a_name_that_is_only_punctuation(self, conn):
        """The other half of the same refusal. Only the vocabulary half had a
        test, so this one could regress without anything failing.
        """
        request_id = self._bought(
            conn,
            [
                ("\u00a9", None, 2.00),
                ("\u00a9", None, 2.50),
                ("SOURDOUGH BOULE", None, 5.00),
                ("SOURDOUGH BOULE", None, 5.50),
            ],
        )

        priced = views.price_history(conn, request_id, min_observations=2)["products"]

        assert [p["description"] for p in priced] == ["SOURDOUGH BOULE"]

    def test_a_response_that_is_only_charges_lists_nothing_and_says_why(self, conn):
        """The boundary where every row is set aside.

        `total_products` reaches zero while the response really did disclose
        lines, so the page must not read as "they disclosed nothing" — the
        count is the only thing on screen that separates the two.
        """
        request_id = self._bought(
            conn, [("CRV", None, 0.05), ("$0.05 CRV DEPOSIT", "00000001", 0.05)]
        )

        index = views.product_index(conn, request_id)

        assert index["products"] == []
        assert index["total_products"] == 0
        assert index["set_aside"] == 2
        assert index["lines_disclosed"] is True, "the response DID disclose lines"
        assert views.stats(conn, request_id)["distinct_products"] == 0

    def test_a_stray_character_does_not_split_one_product_into_two(self, conn):
        """Regression: a leading bracket made one item read as two products.

        `PRODUCT_KEY` falls back to the printed name where no code was
        disclosed, so two spellings became two keys with the visits divided
        between them. The collision rule then refused to clean either label,
        because cleaning would have shown one name on two rows — so what
        reached the reader was a bracket, a split count, and a timeline that
        disagreed with the page: the substring search from the clean spelling
        already reached the bracketed one, so a row saying two purchases
        opened three visits.
        """
        request_id = self._bought(
            conn,
            [
                ("(CHINESE BROCCOLI", None, 3.99),
                ("CHINESE BROCCOLI", None, 3.99),
                ("CHINESE BROCCOLI", None, 4.49),
            ],
        )

        index = views.product_index(conn, request_id)

        assert [p["description"] for p in index["products"]] == ["CHINESE BROCCOLI"]
        assert index["products"][0]["purchases"] == 3, "all three visits, on one row"
        assert index["total_products"] == 1

    def test_two_coded_products_sharing_a_name_stay_two_products(self, conn):
        """The guard on the join, and the case that makes it narrow.

        A code is the retailer distinguishing two items itself. Measured on the
        corpus, one response carries 22 groups of same-named products and every
        one of them is coded; joining those would merge genuinely different
        items on the strength of a shared label.
        """
        request_id = self._bought(
            conn, [("RICE CAKE", "00000001", 2.99), ("RICE CAKE", "00000002", 3.49)]
        )

        index = views.product_index(conn, request_id)

        assert index["total_products"] == 2
        assert [p["description"] for p in index["products"]] == ["RICE CAKE", "RICE CAKE"]

    def test_the_joined_row_keeps_a_name_that_finds_every_visit(self, conn):
        """The merged entry's `match_name` has to reach both spellings, or the
        join fixes the count and breaks the link in the same move.
        """
        request_id = self._bought(
            conn, [("(CHINESE BROCCOLI", None, 3.99), ("CHINESE BROCCOLI", None, 4.49)]
        )

        product = views.product_index(conn, request_id)["products"][0]
        found = views.timeline(conn, request_id, query=product["match_name"])

        assert len(found["baskets"]) == 2, "both visits, from the one handle"

    def test_a_joined_row_carries_a_handle_that_finds_every_visit_it_counts(self, conn):
        """Regression: the join recreated the bug it was written to fix.

        Where no member of a joined group carries the clean spelling, the
        handle used to be one member's printed name, which matches only its own
        rows. The row then said two purchases and its timeline opened one —
        page disagreeing with timeline, one shape over from the split this
        function exists to close. Asserted as the relationship, not a literal:
        whatever the row claims, the handle has to find that many visits.
        """
        request_id = self._bought(conn, [("(RYE LOAF", None, 1.99), ("$1.00 RYE LOAF", None, 2.49)])

        product = views.product_index(conn, request_id)["products"][0]
        found = views.timeline(conn, request_id, query=product["match_name"])

        assert product["purchases"] == 2
        assert len(found["baskets"]) == product["purchases"]

    def test_prices_joins_a_split_product_the_way_products_does(self, conn):
        """Regression: Products joined and Prices did not, so one item rendered
        as one product on one tab and two price series on the next.

        Fourth time this pair has come apart over a rule only one of them
        applied, which is why the decision lives in one place now.
        """
        request_id = self._bought(
            conn,
            [
                ("(RYE LOAF", None, 1.0),
                ("RYE LOAF", None, 1.5),
                ("(RYE LOAF", None, 1.2),
                ("RYE LOAF", None, 1.7),
            ],
        )

        indexed = views.product_index(conn, request_id)
        priced = views.price_history(conn, request_id, min_observations=2)["products"]

        assert indexed["total_products"] == 1
        assert len(priced) == 1
        assert priced[0]["description"] == indexed["products"][0]["description"]

    def test_a_mixed_group_does_not_join(self, conn):
        """One coded, one not. The code is the retailer distinguishing them, so
        the join stays out of it and the reader still sees the printed names.

        The residual case this change does not close, pinned so it is a
        decision rather than an oversight.
        """
        request_id = self._bought(conn, [("(RYE LOAF", None, 1.99), ("RYE LOAF", "00000001", 2.49)])

        assert views.product_index(conn, request_id)["total_products"] == 2

    def test_three_spellings_of_one_name_join_into_one(self, conn):
        request_id = self._bought(
            conn,
            [("(RYE LOAF", None, 1.0), ("RYE LOAF", None, 1.5), ("$1.00 RYE LOAF", None, 2.0)],
        )

        index = views.product_index(conn, request_id)

        assert index["total_products"] == 1
        assert index["products"][0]["purchases"] == 3

    def test_a_joined_row_spans_the_dates_of_everything_in_it(self, conn):
        """`coverage_end`, `stale_before` and the stopped flag all read these,
        so a dropped min or max moves three things downstream.
        """
        request_id = self._bought(conn, [("(RYE LOAF", None, 1.0), ("RYE LOAF", None, 1.5)])

        product = views.product_index(conn, request_id)["products"][0]

        assert product["first_seen"] == "2019-03-01"
        assert product["last_seen"] == "2019-03-02"

    def test_two_unreadable_names_are_two_refusals_not_one_join(self, conn):
        """The fallback that keeps the empty-label group from collapsing.

        Every name that cleans to nothing would otherwise land in one group
        and, being uncoded, be joined into a single entry — turning N refusals
        into 1 and moving `set_aside`, which is the figure that reconciles the
        headline count.
        """
        request_id = self._bought(
            conn, [("(", None, 1.0), ("*", None, 1.0), ("SOURDOUGH BOULE", None, 5.0)]
        )

        index = views.product_index(conn, request_id)

        assert [p["description"] for p in index["products"]] == ["SOURDOUGH BOULE"]
        assert index["set_aside"] == 2, "two unreadable names, two refusals"

    def test_a_reader_can_still_search_for_what_the_retailer_printed(self, conn):
        """Typing the price prefix off the receipt has to find the row it came
        from, even though that string is no longer on screen.
        """
        request_id = self._bought(conn, [("$4.99 OAT MILK HALF GAL", None, 4.99)])

        found = views.product_index(conn, request_id, query="$4.99")["products"]

        assert [p["description"] for p in found] == ["OAT MILK HALF GAL"]
