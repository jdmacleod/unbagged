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

from tests import factories
from unbagged import views
from unbagged.extraction import probe
from unbagged.models import SourceDocument


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

        module = make_fixtures.load(Path("src/unbagged/adapters/kroger/fixtures/generate.py"))
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
            lo: sum(1 for n in purchases.values() if lo <= n <= hi) for lo, hi in self.TIERS
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


class TestTheFactoryDescribesSomethingTheAppCanMake:
    """A factory may be small. It may not describe a document ingest cannot produce.

    `tests/factories.py::DOCUMENT` claimed `application/pdf` and 48 pages while
    `ingest()` stored NULL for both (#46). Every test touching a stored document
    got values the production path could not produce, and the trap was armed
    rather than sprung only because nothing read them yet.

    Checked against the same probe `ingest()` uses, over a real file of the type
    the factory claims, so the two cannot drift apart again. This is the guard
    #32 asks for: a relationship, not a restatement.
    """

    def test_the_factory_document_matches_what_ingest_would_store(self, tmp_path):
        path = tmp_path / factories.DOCUMENT.original_filename
        path.write_text("A response, as text.\n", encoding="utf-8")
        facts = probe(SourceDocument(original_filename=path.name, sha256="a" * 64, path=str(path)))
        assert facts is not None, "the factory claims a type the probe cannot read"
        assert factories.DOCUMENT.media_type == facts.media_type
        assert factories.DOCUMENT.page_count == facts.page_count

    def test_it_would_have_caught_the_original(self, tmp_path):
        """The bug this replaces, stated so the guard cannot be quietly loosened.

        The old factory claimed a 48-page PDF. A real one-page text file cannot
        produce that, and the assertion above is what says so.
        """
        path = tmp_path / "synthetic_report.txt"
        path.write_text("A response, as text.\n", encoding="utf-8")
        facts = probe(SourceDocument(original_filename=path.name, sha256="a" * 64, path=str(path)))
        assert (facts.media_type, facts.page_count) != ("application/pdf", 48)


class TestHMartBasketsAreComposedNotDivided:
    """A product costs about the same each time you buy it.

    Regression: ISSUE-001 — H Mart capture amounts were drawn independently of
    the products they were printed against
    Found by /qa on 2026-09-21
    Report: .gstack/qa-reports/qa-report-127-0-0-1-2026-09-21.md

    The generator divided each visit total into random pieces and named them
    afterwards, so a product's amount had nothing to do with the product.
    Measured through the shipped views, the median product's amount swung 24x
    across visits and the worst swung 101x — sesame oil at 34 cents on one trip
    and $34.35 on another.

    That is not cosmetic. `PriceHistory` classifies a product by the shape of
    its own amounts and draws a series only for the ones that behave like a unit
    price, so the fixture put 14 of 26 products in the weight bucket and left
    the view with 7 it could price. The view was being tested against data no
    receipt produces, which is the same failure mode the rest of this file
    exists for.

    Asserted against the composer rather than through OCR: the round trip costs
    an engine pass per capture and `make test` has to stay seconds, and the
    property under test belongs to the generator either way.
    """

    SPREAD_CEILING = 3.0

    @pytest.fixture(scope="class")
    @classmethod
    def composer(cls):
        from pathlib import Path

        from tools import make_fixtures

        return make_fixtures.load(Path("src/unbagged/adapters/hmart/fixtures/generate.py"))

    def _baskets(self, composer, count: int = 120):
        """Amounts by product name, over baskets drawn across the real range."""
        import random
        from decimal import Decimal

        rng = random.Random(4242)
        amounts: dict[str, list[float]] = {}
        for _ in range(count):
            # The window the statement's own amounts fall in.
            total = Decimal(str(round(rng.uniform(10.0, 140.0), 2)))
            for _flag, name, amount in composer._compose_basket(rng, total, rng.randint(3, 9)):
                if name and amount is not None:
                    amounts.setdefault(name, []).append(float(amount))
        return amounts

    def test_a_products_amount_barely_moves_between_visits(self, composer):
        amounts = self._baskets(composer)
        weighed = {name for name, _price in composer.WEIGHED_PRODUCTS}
        spreads = {
            name: max(seen) / min(seen)
            for name, seen in amounts.items()
            if len(seen) >= 3 and name not in weighed
        }
        assert len(spreads) > 10, "too few repeated products to judge the spread"
        worst = max(spreads.items(), key=lambda kv: kv[1])
        assert worst[1] <= self.SPREAD_CEILING, (
            f"{worst[0]} swings {worst[1]:.1f}x between visits; the divided-total "
            f"generator this replaced swung a median of 24x and a worst of 101x"
        )

    def test_a_weighed_line_is_allowed_to_swing(self, composer):
        """The exemption, stated so it cannot be quietly widened.

        A weighed line carries a price per pound times a weight, so its amount
        is *supposed* to vary — that is the one shape the check above must not
        forbid, and the reason it excludes these names rather than raising the
        ceiling for everybody.
        """
        assert composer.WEIGHED_PRODUCTS, "no weighed products to exempt"
        names = {name for name, _price in composer.WEIGHED_PRODUCTS}
        assert names <= {name for name, _price in composer.CAPTURE_PRODUCTS}, (
            "a weighed product must also be a catalogue product, or the index "
            "shows a name that never appears at a shelf price"
        )

    #: The window the statement's own amounts fall in, from `_amount()`.
    AMOUNT_DOMAIN = range(1000, 14001, 29)

    def _swept(self, composer):
        """Every basket the composer produces across the real amount domain.

        A sweep, not a sample. The first version of these tests drew 200 random
        baskets under one seed and passed while the composer was still emitting
        single-line baskets and weighed lines at twice their own ceiling —
        brute force over 50 seeds and this domain found 5 and 203 of them. A
        fixed seed tests the draw it happens to make, not the function.
        """
        import random
        from decimal import Decimal

        for seed in range(12):
            rng = random.Random(seed)
            for cents in self.AMOUNT_DOMAIN:
                total = Decimal(cents) / 100
                try:
                    rows = composer._compose_basket(rng, total, rng.randint(4, 9))
                except ValueError:
                    # The documented safety valve. Exercised directly below.
                    continue
                yield total, rows

    def test_every_basket_sums_to_the_total_it_was_asked_for(self, composer):
        """The constraint the composer must never trade away.

        The adapter compares the summed lines against the statement's figure for
        the same visit and refuses the basket on any difference at all, so a
        composer that produced plausible prices and a rounding residue would
        generate a fixture that reaches only the refusal path.
        """
        for total, rows in self._swept(composer):
            summed = sum(amount for _flag, _name, amount in rows if amount is not None)
            assert summed == total, f"{summed} != {total}"

    def test_no_basket_rests_its_whole_total_on_one_line(self, composer):
        """Redundancy, which is what lets a misread digit be caught.

        A single-line receipt has nothing to contradict a bad reading, so one
        smeared glyph takes the basket with it. Two scrawled captures were lost
        that way, and the fix for it did not hold: a lone product priced within
        a few cents of the whole visit still consumed it.
        """
        for total, rows in self._swept(composer):
            priced = [row for row in rows if row[2] is not None]
            assert len(priced) >= composer.MIN_PRICED_LINES, (
                f"basket of {total} rests on {len(priced)} priced line(s): {rows}"
            )

    def test_a_weighed_line_stays_within_its_own_ceiling(self, composer):
        """A weighed line may vary; it may not become a plug number.

        `WEIGHED_MAX` exists so the line stays a plausible weight of meat or
        fruit. When the composer ran out of catalogue it wrote the entire
        remainder onto that line regardless — 7.6 lb of squid, $68.83 against a
        $28.00 ceiling, which is the same implausible amount-for-product
        mismatch the spread test above exists to prevent, reached by another
        route.
        """
        weighed = {name for name, _price in composer.WEIGHED_PRODUCTS}
        for total, rows in self._swept(composer):
            for _flag, name, amount in rows:
                if name in weighed and amount is not None:
                    assert amount <= composer.WEIGHED_MAX, (
                        f"weighed line of {amount} in a basket of {total}, "
                        f"above the {composer.WEIGHED_MAX} ceiling"
                    )

    def test_it_raises_rather_than_emitting_a_basket_it_cannot_compose(self, composer):
        """The safety valve, asserted so it cannot be quietly removed.

        A generator that emits a basket it knows is wrong is how the single-line
        captures were committed: the only thing that reads these fixtures
        afterwards is an OCR pass, which cannot tell a bad shape from a good one.
        """
        import random
        from decimal import Decimal

        # More than the whole catalogue can carry, so no composition exists.
        impossible = sum(Decimal(shelf) for _name, shelf in composer.CAPTURE_PRODUCTS) * 2
        with pytest.raises(ValueError, match="could not compose"):
            composer._compose_basket(random.Random(0), impossible, 9)
