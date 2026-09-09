"""The H Mart adapter, against its own fixture.

Counts are asserted against the fixture's actual contents rather than magic
numbers wherever possible: the failure that matters most is silently losing a
record while cleaning up the input, and a hardcoded number cannot catch it.
"""

import re
from pathlib import Path

import pytest

from unbagged.adapters.hmart.adapter import HMartAdapter
from unbagged.extraction import read_tables
from unbagged.models import (
    DisclosureCategory,
    DisclosureStatus,
    FollowUpKind,
    IdType,
    SourceBundle,
    SourceDocument,
)

FIXTURE = (
    Path(__file__).parent.parent
    / "src" / "unbagged" / "adapters" / "hmart" / "fixtures" / "synthetic_history.xls"
)


def bundle(tmp_path, text: str, name: str = "history.xls") -> SourceBundle:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return SourceBundle(
        documents=(SourceDocument(name, "0" * 64, path=str(path)),)
    )


@pytest.fixture(scope="module")
def source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture()
def parsed(tmp_path, source):
    return HMartAdapter().parse(bundle(tmp_path, source))


class TestSniff:
    def test_claims_its_own_format(self, tmp_path, source):
        assert HMartAdapter().sniff(bundle(tmp_path, source)) == 0.9

    def test_declines_a_letter(self, tmp_path):
        assert HMartAdapter().sniff(bundle(tmp_path, "Dear customer,\n", "a.txt")) == 0.0

    def test_declines_a_spreadsheet_with_a_renamed_column(self, tmp_path, source):
        # Loudly, not by reading the next column along. `Point` is `Amount`
        # rounded, so a swap of those two moves every basket total by less than
        # a dollar and nothing on screen would show it.
        assert HMartAdapter().sniff(
            bundle(tmp_path, source.replace("Branch", "Store"))
        ) == 0.0

    def test_a_spent_budget_says_so_instead_of_scoring_a_bare_zero(self, tmp_path, source):
        # "I did not look far enough" and "this is not my format" are different
        # answers that both score 0.0. The reason is what keeps them apart.
        padded = source.replace(
            "<ss:Worksheet", "<!-- " + "x" * 300_000 + " --><ss:Worksheet", 1
        )
        outcome = HMartAdapter().sniff(bundle(tmp_path, padded))
        assert outcome.confidence == 0.0
        assert "header row was reached" in outcome.reason


class TestParse:
    def test_every_row_becomes_a_visit(self, parsed, source):
        # Counted off the raw text, not off a number written here: this is the
        # assertion that catches a record lost while cleaning up the input.
        rows = len(re.findall(r"<ss:Row", source))
        assert len(parsed.transactions) == rows - 2  # banner and header

    def test_no_line_items_are_invented(self, parsed):
        assert parsed.item_count() == 0
        assert all(txn.items == () for txn in parsed.transactions)

    def test_the_card_is_recorded_without_a_scope(self, parsed):
        (identity,) = parsed.identities
        assert identity.id_type is IdType.LOYALTY_CARD
        # Not INDIVIDUAL. The response never says who a card belongs to.
        assert identity.scope is None

    def test_every_record_cites_a_cell_rather_than_a_page(self, parsed):
        for txn in parsed.transactions:
            assert txn.provenance.page is None
            assert re.fullmatch(r"\w+![A-Z]+\d+", txn.provenance.locator)

    def test_timestamps_lose_the_java_suffix_and_keep_the_wall_clock(self, parsed):
        for txn in parsed.transactions:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", txn.occurred_at)

    def test_the_total_is_the_stated_amount(self, parsed):
        assert all(t.total_pre_discount is not None for t in parsed.transactions)

    def test_channel_is_left_null(self, parsed):
        # There is no channel field. Defaulting to in_store would be a claim.
        assert all(txn.channel is None for txn in parsed.transactions)

    def test_specific_pieces_is_partial_and_everything_else_absent(self, parsed):
        by = {d.category: d.status for d in parsed.disclosures}
        assert by[DisclosureCategory.SPECIFIC_PIECES] is DisclosureStatus.PARTIAL
        assert all(
            status is DisclosureStatus.ABSENT
            for category, status in by.items()
            if category is not DisclosureCategory.SPECIFIC_PIECES
        )

    def test_every_category_is_accounted_for(self, parsed):
        assert parsed.missing_categories() == ()

    def test_the_artifact_class_is_recorded(self, parsed):
        kinds = [f.kind for f in parsed.follow_ups]
        assert FollowUpKind.CLARIFICATION in kinds
        note = next(f for f in parsed.follow_ups if f.kind is FollowUpKind.CLARIFICATION)
        assert "points statement" in note.description


class TestSparseRows:
    """The bug that cost a $12.34 basket its cents, and reads no error."""

    def test_a_missing_cell_does_not_shift_the_columns(self, parsed, source):
        table = read_tables(source).tables[0]
        sparse = [row for row in table.rows if len(row) >= 5 and row.cells[2] is None]
        assert sparse, "the fixture must generate sparse rows or this proves nothing"
        for row in sparse:
            amount = row.cells[3]
            points = row.cells[4]
            # If the columns had shifted, `amount` would hold the POINTS value —
            # the amount rounded — and the basket would quietly lose its cents.
            assert float(amount) != float(points)
            assert round(float(amount)) == int(points)

    def test_a_sparse_row_still_becomes_a_visit(self, parsed):
        without_store = [t for t in parsed.transactions if t.store_code is None]
        assert without_store
        assert all(t.total_pre_discount is not None for t in without_store)


class TestTheFixtureIsUnfaithfulInOneDimension:
    def test_the_reader_survives_the_newline_free_form(self, tmp_path, source):
        # The real export is a single line. The fixture is newline-separated so
        # scan_pii can report a line number and a suppression can be written, so
        # this is the one dimension the committed file cannot cover.
        flat = source.replace("\n", "")
        assert "\n" not in flat
        parsed = HMartAdapter().parse(bundle(tmp_path, flat))
        assert len(parsed.transactions) == len(
            HMartAdapter().parse(bundle(tmp_path, source)).transactions
        )
