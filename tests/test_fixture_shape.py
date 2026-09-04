"""The fixture has to have the *shape* of a real shopping history, not just its format.

Three separate bugs in this project came from the generator modelling something
the real Kroger format never emits, and a fourth came from it modelling a
distribution no shopper has. A uniform draw over a small catalogue produced a
fixture whose median product was bought five times and where 4.9% of products
were bought exactly once; the real response measured 68% and a median of one.

That difference is not cosmetic. The product index is designed around the long
tail — two thirds of the screen is single-purchase products — so a fixture
without one cannot exercise the view, and no test, QA pass or design review
touches the case the design exists to handle.

These bounds are wide on purpose. They are here to catch the tail vanishing,
not to pin an exact draw.
"""

import statistics
from collections import Counter

import pytest

from unbagged import views


@pytest.fixture(scope="module")
def purchases(fixture_conn_module) -> Counter:
    """upc -> times bought, over the priced lines of the synthetic report."""
    conn, request_id = fixture_conn_module
    rows = conn.execute(
        "SELECT i.upc FROM txn_item i JOIN txn t ON t.id = i.txn_id "
        "WHERE t.request_id = ? AND i.upc IS NOT NULL AND i.retail_amt > 0",
        (request_id,),
    ).fetchall()
    return Counter(row["upc"] for row in rows)


class TestLongTail:
    def test_most_products_were_bought_exactly_once(self, purchases):
        """The single most load-bearing property. Real report: 68%."""
        once = sum(1 for n in purchases.values() if n == 1)
        share = once / len(purchases)
        assert 0.55 <= share <= 0.80, (
            f"{share:.1%} of products bought once; the real response measured 68%. "
            "A fixture without a long tail cannot exercise the product index."
        )

    def test_the_median_product_was_bought_once(self, purchases):
        assert statistics.median(purchases.values()) == 1

    def test_there_are_roughly_two_priced_lines_per_distinct_product(self, purchases):
        """Real report: 762 lines across 379 products."""
        ratio = sum(purchases.values()) / len(purchases)
        assert 1.7 <= ratio <= 2.6, f"{ratio:.2f} lines per product"

    def test_the_catalogue_is_far_larger_than_the_history(self):
        """A store sells more than a household buys. Without this the tail
        cannot exist: every tail draw would collide with a product already
        bought."""
        from pathlib import Path

        from tools import make_fixtures

        module = make_fixtures.load(
            Path("src/unbagged/adapters/kroger/fixtures/generate.py")
        )
        assert len(module.CATALOGUE) > 2000


class TestOneLinePerProductPerTrip:
    """A trip puts a product on exactly one line.

    Buying two of something is one line at twice the amount. Measured across a
    real response, a UPC appearing twice in one basket happens on 0 of 762
    product-days — and a whole days-vs-lines UI model plus the copy "three items
    look like three separate trips" was once written from a fixture that did the
    opposite. The generator's comment claimed this was fixed when only the
    multi-buy *amount* had been; the draw was still with replacement, and
    weighting it made the collisions worse.
    """

    def test_no_product_appears_twice_in_one_basket(self, fixture_conn_module):
        conn, request_id = fixture_conn_module
        duplicated = conn.execute(
            """
            SELECT COUNT(*) AS n FROM (
                SELECT i.txn_id, i.upc, COUNT(*) AS c
                FROM txn_item i JOIN txn t ON t.id = i.txn_id
                WHERE t.request_id = ? AND i.upc IS NOT NULL AND i.retail_amt <> 0
                GROUP BY i.txn_id, i.upc HAVING c > 1)
            """,
            (request_id,),
        ).fetchone()["n"]
        assert duplicated == 0

    def test_the_placeholder_rows_are_still_there(self, fixture_conn_module):
        """The exception, and it is in the real export too: zero-value rows that
        name no product. They repeat within a basket and must keep doing so."""
        conn, request_id = fixture_conn_module
        placeholders = conn.execute(
            "SELECT COUNT(*) AS n FROM txn_item i JOIN txn t ON t.id = i.txn_id "
            "WHERE t.request_id = ? AND i.retail_amt = 0",
            (request_id,),
        ).fetchone()["n"]
        assert placeholders > 0


class TestSizeTiersAreAllExercised:
    """The index quantises purchase counts onto five absolute tiers. A fixture
    that cannot populate a tier cannot test how that tier renders, which is the
    fixture-fiction failure this project has hit three times."""

    TIERS = ((12, 10**6), (7, 11), (4, 6), (2, 3), (1, 1))

    def test_every_tier_has_members(self, purchases):
        populations = {
            lo: sum(1 for n in purchases.values() if lo <= n <= hi)
            for lo, hi in self.TIERS
        }
        empty = [lo for lo, count in populations.items() if count < 3]
        assert not empty, f"tiers with fewer than 3 members: {empty} ({populations})"

    def test_the_top_tier_is_reached(self, purchases):
        """Real report peaked at 22 purchases against a median of 1."""
        assert max(purchases.values()) >= 12

    def test_the_staples_are_not_all_the_same_product(self, fixture_conn_module):
        """The weekly staples must be spread across the shop.

        The draw weights were first assigned to a contiguous slice of the
        catalogue, and the catalogue is built department by department, so the
        heaviest band was the first ten entries — ten sizes of the same fruit.
        The index rendered nine BANANAS variants at the largest size and nothing
        else, which is not a shopping history, it is an artefact of the
        generator's loop order.
        """
        conn, request_id = fixture_conn_module
        rows = conn.execute(
            """
            SELECT i.description_raw AS name, COUNT(*) AS n
            FROM txn_item i JOIN txn t ON t.id = i.txn_id
            WHERE t.request_id = ? AND i.upc IS NOT NULL AND i.retail_amt > 0
            GROUP BY i.upc HAVING n >= 12
            """,
            (request_id,),
        ).fetchall()
        assert len(rows) >= 3, "not enough top-tier products to judge"
        # The last word is a pack size; what precedes it is the product.
        families = {" ".join(row["name"].split()[:-1]) for row in rows}
        assert len(families) > len(rows) / 2, (
            f"top tier is dominated by one product: {sorted(families)}"
        )


class TestTheIndexViewSeesTheTail:
    """Scored against the view, not against the generator. The generator
    deciding a distribution is not evidence that the query preserves it."""

    def test_product_index_reports_the_tail(self, fixture_conn_module):
        conn, request_id = fixture_conn_module
        result = views.product_index(conn, request_id)
        assert result["product_count"] > 200
        share = result["bought_once"] / result["product_count"]
        assert 0.55 <= share <= 0.80
