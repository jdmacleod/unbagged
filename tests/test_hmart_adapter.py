"""The H Mart adapter, against its own fixture.

Counts are asserted against the fixture's actual contents rather than magic
numbers wherever possible: the failure that matters most is silently losing a
record while cleaning up the input, and a hardcoded number cannot catch it.
"""

import re
from decimal import Decimal
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
from unbagged.transcription import words as tr_words

needs_engine = pytest.mark.skipif(
    not tr_words.available(), reason=f"{tr_words.ENGINE} is not installed"
)

FIXTURE = (
    Path(__file__).parent.parent
    / "src"
    / "unbagged"
    / "adapters"
    / "hmart"
    / "fixtures"
    / "synthetic_history.xls"
)


def bundle(tmp_path, text: str, name: str = "history.xls") -> SourceBundle:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return SourceBundle(documents=(SourceDocument(name, "0" * 64, path=str(path)),))


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
        assert HMartAdapter().sniff(bundle(tmp_path, source.replace("Branch", "Store"))) == 0.0

    def test_a_spent_budget_says_so_instead_of_scoring_a_bare_zero(self, tmp_path, source):
        # "I did not look far enough" and "this is not my format" are different
        # answers that both score 0.0. The reason is what keeps them apart.
        padded = source.replace("<ss:Worksheet", "<!-- " + "x" * 300_000 + " --><ss:Worksheet", 1)
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
        """The points statement on its own says nothing about any basket.

        Still true after the receipt captures arrived, and the reason it is
        worth keeping: line items now reach this adapter, from a second kind of
        document, and none of them may appear on a bundle that holds only the
        statement. A visit itemised out of a spreadsheet that lists no items
        would be an invention — see `tests/test_hmart_receipts.py` and
        `TestWhenTheCapturesArrive` below for the half that does have them.
        """
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


class TestWhenTheCapturesArrive:
    """The second half of the response: screen captures of the receipt viewer.

    The two halves answer different questions — the statement says what every
    visit cost, the captures say what some of them held — so the join between
    them is the whole point, and the gate on that join is arithmetic. A basket
    read out of 10px type by a machine is attached to a visit only when its
    lines add up to the total the receipt prints on itself.

    Built against the committed fixture's own first row rather than numbers
    written here, so a change to the generator cannot leave this asserting
    against a visit that no longer exists.
    """

    @pytest.fixture()
    def visit(self, source) -> dict:
        from unbagged.extraction import read_tables

        row = read_tables(source).tables[0].rows[2]
        stamp = row.value(2)[:19]
        return {
            "stamp": stamp,
            "date": stamp[:10],
            "name": f"Transaction_{stamp[5:7]}{stamp[8:10]}{stamp[2:4]}.png",
            "amount": Decimal(row.value(4)),
        }

    def capture(self, tmp_path, visit, *, lines=None, balance=None, name=None) -> SourceDocument:
        """A capture of one receipt.

        `lines` and `balance` are separate so a receipt that does NOT add up can
        be built. They default to agreeing with each other and with the visit.
        """
        from tests.receiptimage import build_receipt

        amounts = lines if lines is not None else [visit["amount"]]
        total = balance if balance is not None else sum(amounts)
        rows = [("", "Customer ID: 40100200300", None)]
        rows += [("", f"ITEM {n}", str(a)) for n, a in enumerate(amounts, start=1)]
        rows += [
            ("", "TAX", "0.00"),
            ("***", "BALANCE", str(total)),
            ("", "CREDIT CARD", str(total)),
            ("", f"{visit['stamp'].replace('T', ' ')}  2  118  0042", None),
            ("", "Card Number : *********0000", None),
        ]
        path = tmp_path / (name or visit["name"])
        path.write_bytes(build_receipt(rows))
        return SourceDocument(original_filename=path.name, sha256="0" * 64, path=str(path), id=1)

    def visit_row(self, parsed, visit):
        return next(
            txn
            for txn in parsed.transactions
            if txn.occurred_at == visit["stamp"].replace(" ", "T")
        )

    def parse(self, tmp_path, source, *documents):
        history = tmp_path / "history.xls"
        history.write_text(source, encoding="utf-8")
        spreadsheet = SourceDocument(
            original_filename="history.xls", sha256="1" * 64, path=str(history), id=0
        )
        return HMartAdapter().parse(SourceBundle(documents=(spreadsheet, *documents)))

    @needs_engine
    def test_a_receipt_that_adds_up_itemises_its_visit(self, tmp_path, source, visit):
        parsed = self.parse(tmp_path, source, self.capture(tmp_path, visit))
        itemised = [txn for txn in parsed.transactions if txn.items]
        assert len(itemised) == 1
        assert itemised[0].occurred_at == visit["stamp"].replace(" ", "T")
        assert itemised[0].items[0].retail_amt == float(visit["amount"])

    @needs_engine
    def test_the_lines_sum_to_the_total_the_statement_already_gave(self, tmp_path, source, visit):
        """The two halves of the response have to agree, or one of them is wrong.

        `total_pre_discount` stays the statement's figure and the lines come
        from the receipt, so this is the only assertion here that compares the
        two documents against each other rather than either against itself.
        """
        parsed = self.parse(tmp_path, source, self.capture(tmp_path, visit))
        txn = next(t for t in parsed.transactions if t.items)
        assert sum(item.retail_amt for item in txn.items) == txn.total_pre_discount

    @needs_engine
    def test_a_receipt_that_does_not_add_up_stores_nothing_from_itself(
        self, tmp_path, source, visit
    ):
        """The gate. A basket read wrongly is worse than one not read at all.

        The capture states a total its own lines do not come to, which is what
        a misread digit looks like from the outside. The visit must keep the
        total-only row it already had, and the file must be named — silently
        dropping it would leave a reader believing the response was read whole.
        """
        broken = self.capture(tmp_path, visit, lines=[Decimal("1.00")], balance=visit["amount"])
        parsed = self.parse(tmp_path, source, broken)
        matched = self.visit_row(parsed, visit)
        assert not matched.items
        assert any(visit["name"] in w.message for w in parsed.warnings)

    @needs_engine
    def test_a_receipt_that_disagrees_with_the_statement_is_not_stored_either(
        self, tmp_path, source, visit
    ):
        """It adds up, and it adds up to the wrong number.

        A receipt reconciling against itself only proves the transcription is
        self-consistent. The statement is a second, independent account of what
        the visit cost, and where the two disagree one of them was misread —
        which of them cannot be known from here.

        Stored anyway, this shows on the timeline as a basket "under by" the
        difference, beside a note telling the reader the difference is in the
        response as supplied. It would not be.
        """
        wrong = self.capture(tmp_path, visit, lines=[Decimal("1.00")])
        parsed = self.parse(tmp_path, source, wrong)
        assert not self.visit_row(parsed, visit).items
        assert any("disagree" in w.message for w in parsed.warnings)

    @needs_engine
    def test_a_capture_of_a_visit_the_statement_does_not_list_is_not_added(
        self, tmp_path, source, visit
    ):
        """Two responses that disagree about which visits happened.

        Adding the visit would double a trip to the shop whenever the join
        merely failed, and a doubled total is invisible on screen. Refusing it
        loses a basket and says so, which a reader can act on.
        """
        orphan = {**visit, "stamp": "1999-01-01T00:00:00", "name": "Transaction_010199.png"}
        parsed = self.parse(tmp_path, source, self.capture(tmp_path, orphan))
        assert all(not txn.items for txn in parsed.transactions)
        assert any("Transaction_010199.png" in w.message for w in parsed.warnings)

    def test_a_capture_is_not_reported_as_an_unreadable_spreadsheet(self, tmp_path, source, visit):
        """It is not one, and saying so 46 times buried the warning that mattered.

        No engine needed: this asserts what is NOT said, and the accounting that
        says it runs before any capture is read.
        """
        parsed = self.parse(tmp_path, source, self.capture(tmp_path, visit))
        assert not any("could not be read at all" in w.message for w in parsed.warnings)


class TestCapturesWithNoStatementBesideThem:
    """Regression: ISSUE-001 — captures alone were claimed, then refused.

    Found by /qa on 2026-09-15.
    Report: .gstack/qa-reports/qa-report-unbagged-2026-09-15.md

    `sniff` scores a bundle of captures 0.4 so it beats the generic fallback,
    which an image scores nothing on. `parse` then raised "None of the uploaded
    files could be read as a spreadsheet… in Excel choose Save As and pick XML
    Spreadsheet 2003" — told to a reader who had just uploaded screenshots.
    The comment on CAPTURE_CONFIDENCE claimed the score existed so a bundle of
    captures would be read rather than refused; it was refused, with a worse
    message.

    A receipt is a record of a visit on its own. Without a statement there is
    nothing to join it to and nothing it could double, so it becomes its own.
    """

    STAMP = "2019-03-04 11:07:00"

    def capture(self, tmp_path, *, name, stamp=None, lines=None, balance=None):
        from tests.receiptimage import build_receipt

        amounts = lines if lines is not None else [Decimal("6.99")]
        total = balance if balance is not None else sum(amounts)
        rows = [("", "Customer ID: 40100200300", None)]
        rows += [("", f"ITEM {n}", str(a)) for n, a in enumerate(amounts, start=1)]
        rows += [
            ("", "TAX", "0.00"),
            ("***", "BALANCE", str(total)),
            ("", "CREDIT CARD", str(total)),
            ("", f"{stamp or self.STAMP}  2  118  0042", None),
        ]
        path = tmp_path / name
        path.write_bytes(build_receipt(rows))
        return SourceDocument(
            original_filename=name, sha256=name.ljust(64, "0"), path=str(path), id=0
        )

    @needs_engine
    def test_a_bundle_of_captures_is_read_rather_than_refused(self, tmp_path):
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(self.capture(tmp_path, name="Transaction_030419.png"),))
        )
        assert len(parsed.transactions) == 1
        assert parsed.transactions[0].occurred_at == "2019-03-04T11:07:00"
        assert parsed.item_count() == 1

    @needs_engine
    def test_the_store_is_left_null_because_a_capture_does_not_name_one(self, tmp_path):
        """The statement names the branch. The lane on the receipt is not a store.

        Lane numbers 1-8 appear against both branches in the real response, so
        recording one as the store would put a number where a place belongs.
        """
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(self.capture(tmp_path, name="Transaction_030419.png"),))
        )
        assert parsed.transactions[0].store_code is None
        assert parsed.transactions[0].division_code == "3"

    @needs_engine
    def test_the_evidence_stops_claiming_a_store_was_disclosed(self, tmp_path):
        """What the Compliance view quotes has to describe the response.

        The sentence was inherited from the statement path and said every visit
        came "with a date, a store and a total" over visits that carry no store
        at all.
        """
        parsed = HMartAdapter().parse(
            SourceBundle(documents=(self.capture(tmp_path, name="Transaction_030419.png"),))
        )
        specific = next(
            d for d in parsed.disclosures if d.category is DisclosureCategory.SPECIFIC_PIECES
        )
        assert "a date and a total" in specific.evidence
        assert "a store" not in specific.evidence

    @needs_engine
    def test_a_timestamp_disagreeing_with_the_filename_is_not_stored(self, tmp_path):
        """Regression: ISSUE-002 — the only cross-check this path has.

        Found by /qa on 2026-09-15.

        Two records of one date, from different parts of the response: the
        timestamp the till printed and the date the store's export put in the
        filename. On the statement path a misread timestamp is caught by the
        amount not matching the visit it lands on. Here there is no statement,
        so without this a misread digit files a visit under the wrong day and
        nothing on screen ever says so.
        """
        parsed = HMartAdapter().parse(
            SourceBundle(
                documents=(
                    self.capture(
                        tmp_path, name="Transaction_030419.png", stamp="2019-08-21 11:07:00"
                    ),
                )
            )
        )
        assert parsed.transactions == ()
        assert any("misread" in w.message for w in parsed.warnings)

    @needs_engine
    def test_a_receipt_with_no_readable_timestamp_is_not_placed_at_midnight(self, tmp_path):
        """The date is solid and the hour is not, and the timeline shows hours.

        A visit at 00:00 reads as a fact about when someone shopped. The
        spreadsheet path skips a row with no readable date for the same reason.
        """
        from tests.receiptimage import build_receipt

        path = tmp_path / "Transaction_030419.png"
        path.write_bytes(
            build_receipt(
                [
                    ("", "Customer ID: 40100200300", None),
                    ("", "ITEM 1", "6.99"),
                    ("", "TAX", "0.00"),
                    ("***", "BALANCE", "6.99"),
                    ("", "CREDIT CARD", "6.99"),
                    ("", "Card Number : *********0000", None),
                ]
            )
        )
        parsed = HMartAdapter().parse(
            SourceBundle(
                documents=(
                    SourceDocument(
                        original_filename=path.name, sha256="0" * 64, path=str(path), id=0
                    ),
                )
            )
        )
        assert parsed.transactions == ()
        assert any("no hour to file it under" in w.message for w in parsed.warnings)


class TestAFileThatIsNotWhatItLooksLike:
    """A capture can be damaged, or simply not be a receipt.

    Found by /qa on 2026-09-15.
    Report: .gstack/qa-reports/qa-report-unbagged-2026-09-15.md

    Both arrive named like a receipt and typed like an image, so both reach the
    reader. What each one costs is the point: a damaged file must cost itself
    and nothing else, and a file with no receipt on it must not be described as
    a receipt that failed to add up.
    """

    def photo(self, tmp_path, *, truncate=0):
        """A PNG that is an image and is not a receipt."""
        import io

        from PIL import Image, ImageDraw

        image = Image.new("RGB", (540, 400), (120, 160, 90))
        ImageDraw.Draw(image).ellipse((80, 80, 400, 320), fill=(200, 80, 60))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        path = tmp_path / "Transaction_030419.png"
        path.write_bytes(data[:truncate] if truncate else data)
        return SourceDocument(original_filename=path.name, sha256="0" * 64, path=str(path), id=0)

    def test_a_truncated_capture_costs_one_file_and_not_the_upload(self, tmp_path):
        """Regression: ISSUE-003 — Pillow's OSError escaped the adapter.

        `ingest` rewraps anything that is not an `AdapterError` as an adapter
        bug and fails the whole request, so one file damaged in transit lost
        every other capture in the upload with it. Handoff §4 rule 4: a
        malformed record costs one warning and one record.
        """
        parsed = HMartAdapter().parse(SourceBundle(documents=(self.photo(tmp_path, truncate=200),)))
        named = [w for w in parsed.warnings if "Transaction_030419.png" in w.message]
        assert named, "the damaged file was not named"
        assert "could not be opened as an image" in named[0].message

    def test_a_truncated_capture_does_not_stop_the_ones_beside_it(self, tmp_path, source):
        """The claim the test above cannot make on its own.

        With one file in the bundle, "costs one file" and "costs the upload"
        look identical. This is the bundle where they differ.
        """
        history = tmp_path / "history.xls"
        history.write_text(source, encoding="utf-8")
        parsed = HMartAdapter().parse(
            SourceBundle(
                documents=(
                    SourceDocument(
                        original_filename="history.xls",
                        sha256="1" * 64,
                        path=str(history),
                        id=0,
                    ),
                    self.photo(tmp_path, truncate=200),
                )
            )
        )
        assert len(parsed.transactions) > 1, "the spreadsheet was lost with the damaged capture"

    @needs_engine
    def test_a_page_with_no_receipt_on_it_is_not_a_receipt_that_failed_to_add_up(self, tmp_path):
        """Regression: ISSUE-004 — two findings were reported as one.

        A photograph produced "its lines come to +0.00 against the total
        printed on the receipt", quoting a figure that is not on the page and a
        total that does not exist. One of those messages sends a reader looking
        for a misread digit; the other tells them the file is not what they
        thought it was.
        """
        parsed = HMartAdapter().parse(SourceBundle(documents=(self.photo(tmp_path),)))
        message = next(w.message for w in parsed.warnings if "Transaction_030419.png" in w.message)
        assert "no receipt on it" in message
        assert "+0.00" not in message

    @needs_engine
    def test_no_spreadsheet_is_described_when_none_was_uploaded(self, tmp_path):
        """Regression: ISSUE-005 — a sentence about a document that was not there.

        "The spreadsheet carried a header row and no readable purchases" fired
        on an upload of captures alone. This tool asks to be read as a record of
        what a response contained; a false sentence in that record is the whole
        failure.
        """
        parsed = HMartAdapter().parse(SourceBundle(documents=(self.photo(tmp_path),)))
        assert not any("spreadsheet carried a header row" in w.message for w in parsed.warnings)
        assert any("receipt captures in this upload" in w.message for w in parsed.warnings)
