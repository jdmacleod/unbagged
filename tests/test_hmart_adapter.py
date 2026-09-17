"""The H Mart adapter, against its own fixture.

Counts are asserted against the fixture's actual contents rather than magic
numbers wherever possible: the failure that matters most is silently losing a
record while cleaning up the input, and a hardcoded number cannot catch it.
"""

import re
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from unbagged.adapters.hmart import receipt as rc
from unbagged.adapters.hmart.adapter import HMartAdapter, _settle_tax_against
from unbagged.extraction import read_tables
from unbagged.models import (
    DisclosureCategory,
    DisclosureStatus,
    FollowUpKind,
    IdType,
    InferenceOrigin,
    SourceBundle,
    SourceDocument,
    Transaction,
)
from unbagged.transcription import words as tr_words

#: The adapter MODULE, which cannot be reached by name.
#:
#: `unbagged.adapters.hmart.adapter` resolves to the exported HMartAdapter
#: INSTANCE — the package `__init__` rebinds that attribute — so both
#: `import ... as mod` and a monkeypatch string target land on the instance and
#: fail with AttributeError. `sys.modules` holds the real module.
HMART = sys.modules["unbagged.adapters.hmart.adapter"]
adapter_module = HMART

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


class TestThePointColumnCarriesNothingNew:
    """`Point == Amount` rounded, emitted as a first-party inference.

    A checkable statement about the quality of the disclosure: the retailer sent
    a column it could have computed from another column in the same file. Its
    own published terms state the rule independently — "$1 of purchase
    (excluding tax) equals 1 point".

    The thing these tests are really about is the rounding rule. Python's
    `round()` is half-even; a Java-backed portal is near-certainly half-up. If
    the adapter asserted `point == round(amount)` and the fixture generator
    produced its points with `round()` — which it does — the pair would agree
    with itself and prove nothing whatever about the format.
    """

    SS = "urn:schemas-microsoft-com:office:spreadsheet"
    HEADERS = ("Smartcard", "Date of Purchase", "Branch", "Amount", "Point")

    def _sheet(self, rows: list[tuple[str, str]]) -> str:
        def cell(text: str) -> str:
            return f'<ss:Cell><ss:Data ss:Type="String">{text}</ss:Data></ss:Cell>'

        def row(cells: tuple[str, ...]) -> str:
            return "<ss:Row>" + "".join(cell(c) for c in cells) + "</ss:Row>"

        body = "".join(
            row(("40100200300", f"2019-03-{day:02d} 11:07:00.0", "SOME BRANCH", amount, point))
            for day, (amount, point) in enumerate(rows, start=1)
        )
        return (
            f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{self.SS}">'
            f'<ss:Worksheet ss:Name="Workbook"><ss:Table>'
            f"{row(self.HEADERS)}{body}"
            f"</ss:Table></ss:Worksheet></ss:Workbook>"
        )

    def _inferences(self, tmp_path, rows):
        return HMartAdapter().parse(bundle(tmp_path, self._sheet(rows))).inferences

    def test_the_relationship_is_reported_as_a_first_party_inference(self, parsed):
        (found,) = [i for i in parsed.inferences if i.label == "Point"]
        assert found.origin is InferenceOrigin.FIRST_PARTY_MODEL
        # The whole finding: the retailer disclosed a column derivable from
        # another column it also disclosed.
        assert found.derivable_from_txns is True
        assert "rounded" in found.value_raw
        # The Point header cell itself. A reader following a citation about a
        # named column should land on that column, not on the corner of the
        # sheet — which is what the response-wide citation would have given.
        assert found.provenance.locator.endswith("!E2")

    def test_the_claim_holds_whichever_way_a_tie_was_broken(self, tmp_path):
        """The point of the exercise.

        A half-cent amount is the ONLY row that tells half-up from half-even,
        and the observed response has never contained one. So the claim is the
        thing both rules agree on — a rounded value is within half a unit of
        what it was rounded from — and both readings of a tie satisfy it.
        """
        half_even = self._inferences(tmp_path, [("12.50", "12"), ("1.00", "1"), ("3.00", "3")])
        half_up = self._inferences(tmp_path, [("12.50", "13"), ("1.00", "1"), ("3.00", "3")])
        assert len(half_even) == 1
        assert len(half_up) == 1

    def test_a_column_that_is_not_the_amount_rounded_says_nothing(self, tmp_path):
        # Off by more than half on one row out of three. One row where it does
        # not hold is the interesting case; a claim that tolerated it would be
        # worth less than no claim.
        assert self._inferences(tmp_path, [("1.00", "1"), ("2.00", "2"), ("3.00", "9")]) == ()

    def test_a_point_that_is_not_a_whole_number_says_nothing(self, tmp_path):
        # Whatever that column is, it is not a rounded anything.
        assert self._inferences(tmp_path, [("1.00", "1.5"), ("2.00", "2"), ("3.00", "3")]) == ()

    @pytest.mark.parametrize("poison", ["NaN", "sNaN", "Infinity", "-Infinity"])
    def test_a_non_number_that_decimal_accepts_does_not_abort_the_upload(self, tmp_path, poison):
        """`Decimal` parses all four of these and none of them is a number.

        A quiet NaN compares false against everything, so it slips past a bounds
        check rather than failing it. Arithmetic on a signalling NaN raises
        `InvalidOperation` from wherever it is finally touched — which, before
        `_exact` checked `is_finite`, escaped as an `AdapterError` and lost the
        entire upload. This adapter's contract is to degrade with a warning and
        never raise.
        """
        parsed = HMartAdapter().parse(
            bundle(
                tmp_path,
                self._sheet([("1.00", "1"), (poison, "2"), ("3.00", "3"), ("4.00", "4")]),
            )
        )
        # The point is that we got here at all rather than out through an
        # exception. The row is unreadable, so no claim is made about the column.
        assert parsed.inferences == ()
        assert parsed.transactions

    @pytest.mark.parametrize("poison", ["NaN", "sNaN", "Infinity"])
    def test_the_same_holds_in_the_point_column(self, tmp_path, poison):
        parsed = HMartAdapter().parse(
            bundle(
                tmp_path,
                self._sheet([("1.00", "1"), ("2.00", poison), ("3.00", "3"), ("4.00", "4")]),
            )
        )
        assert parsed.inferences == ()

    def test_a_cell_that_cannot_be_read_is_not_a_cell_to_pass_over(self, tmp_path):
        """Present and unreadable is not the same as absent.

        Skipping both alike let three good pairs and one garbage cell still
        emit the claim, which overstates what was actually checked: the
        contract is that the relationship holds on every row carrying BOTH
        values, and a row with junk in it carries both.
        """
        assert (
            self._inferences(
                tmp_path, [("1.00", "1"), ("not a number", "2"), ("3.00", "3"), ("4.00", "4")]
            )
            == ()
        )

    def test_a_blank_cell_is_passed_over_rather_than_refused(self, tmp_path):
        """The other half of that distinction, and the ordinary sparse row.

        A row that carries only one of the two has nothing to say either way, so
        it is skipped and the rows that do carry both still support the claim.
        """

        def cell(text):
            return f'<ss:Cell><ss:Data ss:Type="String">{text}</ss:Data></ss:Cell>'

        rows = [("1.00", "1"), ("2.00", "2"), ("3.00", "3")]
        body = "".join(
            "<ss:Row>"
            + "".join(
                cell(c) for c in ("40100200300", f"2019-03-0{n} 11:07:00.0", "SOME BRANCH", a, p)
            )
            + "</ss:Row>"
            for n, (a, p) in enumerate(rows, start=1)
        )
        # A fourth visit with an amount and no points cell at all.
        body += (
            "<ss:Row>"
            + "".join(
                cell(c) for c in ("40100200300", "2019-03-09 11:07:00.0", "SOME BRANCH", "9.00")
            )
            + "</ss:Row>"
        )
        header = "<ss:Row>" + "".join(cell(h) for h in self.HEADERS) + "</ss:Row>"
        document = (
            f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{self.SS}">'
            f'<ss:Worksheet ss:Name="Workbook"><ss:Table>{header}{body}'
            f"</ss:Table></ss:Worksheet></ss:Workbook>"
        )
        (found,) = HMartAdapter().parse(bundle(tmp_path, document)).inferences
        assert "on all 3 rows" in found.value_raw

    def test_too_few_rows_to_mean_anything_says_nothing(self, tmp_path):
        assert self._inferences(tmp_path, [("1.00", "1"), ("2.00", "2")]) == ()

    def test_a_statement_whose_point_cells_are_empty_claims_nothing(self, tmp_path):
        """The column is there and holds nothing, so there is nothing to say.

        SpreadsheetML omits an empty cell rather than writing it blank, which is
        why this is spelled as a four-cell row rather than a fifth empty one.
        """

        def cell(text):
            return f'<ss:Cell><ss:Data ss:Type="String">{text}</ss:Data></ss:Cell>'

        rows = "".join(
            "<ss:Row>"
            + "".join(
                cell(c)
                for c in ("40100200300", f"2019-03-{day:02d} 11:07:00.0", "SOME BRANCH", "1.00")
            )
            + "</ss:Row>"
            for day in range(1, 5)
        )
        header = "<ss:Row>" + "".join(cell(h) for h in self.HEADERS) + "</ss:Row>"
        document = (
            f'<?xml version="1.0" encoding="utf-8"?><ss:Workbook xmlns:ss="{self.SS}">'
            f'<ss:Worksheet ss:Name="Workbook"><ss:Table>{header}{rows}'
            f"</ss:Table></ss:Worksheet></ss:Workbook>"
        )
        parsed = HMartAdapter().parse(bundle(tmp_path, document))
        assert parsed.transactions, "the rows must still read as visits"
        assert parsed.inferences == ()


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
        assert parsed.transactions[0].division_code == "2"

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


class TestWhenTheStatementSettlesTheTaxLine:
    """The statement is the only figure that can choose between two readings.

    Found by the adversarial pass on 2026-09-16. `foots()` adds tax back, so it
    reconciles whether the line above the balance was tax or a purchase. The
    points statement reports the PRE-tax subtotal, so it agrees with exactly one
    of them.
    """

    def receipt(self, *, lines, tax, balance, inferred):
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=tuple(rc.ReceiptLine(f"ITEM {n}", a) for n, a in enumerate(lines, 1)),
            tax=tax,
            balance=balance,
            complete=True,
            tax_inferred=inferred,
        )

    def txn(self, stated):
        from unbagged.models import Transaction

        return Transaction(occurred_at="2019-03-04T11:07:00", total_pre_discount=float(stated))

    def test_a_purchase_eaten_as_tax_is_given_back(self):
        """The statement says 6.00 pre-tax; the reading that kept only 3.00 of
        purchases is the wrong one, and nothing inside the receipt could tell."""
        misread = self.receipt(
            lines=[Decimal("1.00"), Decimal("2.00")],
            tax=Decimal("3.00"),
            balance=Decimal("6.00"),
            inferred=True,
        )
        settled = _settle_tax_against(self.txn(Decimal("6.00")), misread)
        assert settled.subtotal == Decimal("6.00")
        assert settled.tax == Decimal("0.00")

    def test_a_real_tax_line_is_left_alone(self):
        """The statement agrees with the as-read reading, so nothing moves."""
        correct = self.receipt(
            lines=[Decimal("1.00"), Decimal("2.00")],
            tax=Decimal("0.50"),
            balance=Decimal("3.50"),
            inferred=True,
        )
        settled = _settle_tax_against(self.txn(Decimal("3.00")), correct)
        assert settled.tax == Decimal("0.50")
        assert settled.subtotal == Decimal("3.00")

    def test_a_receipt_that_printed_the_word_is_never_reinterpreted(self):
        """A legible TAX line disagreeing with the statement has a different
        problem, and `_disagrees` should report it rather than have it quietly
        reinterpreted into agreement."""
        read_it = self.receipt(
            lines=[Decimal("1.00")],
            tax=Decimal("5.00"),
            balance=Decimal("6.00"),
            inferred=False,
        )
        settled = _settle_tax_against(self.txn(Decimal("6.00")), read_it)
        assert settled is read_it

    def test_neither_reading_agreeing_changes_nothing(self):
        """So `_disagrees` still sees it and quarantines the visit."""
        wrong = self.receipt(
            lines=[Decimal("1.00")],
            tax=Decimal("2.00"),
            balance=Decimal("3.00"),
            inferred=True,
        )
        settled = _settle_tax_against(self.txn(Decimal("99.00")), wrong)
        assert settled is wrong


class TestWhenAModelIsAskedAboutACapture:
    """Found by /ship's testing specialist on 2026-09-16.

    `_adjudicate` had no coverage at all, and the reason is the sort that hides:
    the autouse fixture in `conftest.py` points the model host at a dead port for
    the whole suite, so `where.usable` was False in every test and the function
    returned at its first line every time. The two halves were tested in
    isolation — the transport in `test_ollama.py`, the arithmetic in
    `from_reply` — and the wiring that turns a model's answer into rows was not.
    """

    @pytest.fixture()
    def usable(self, monkeypatch):
        """A model the adapter believes it can reach."""
        from unbagged.transcription import ollama

        where = ollama.Availability(ollama.Reachability.OK, "", "localhost:11434", "a-model")
        monkeypatch.setattr(HMART.ollama, "availability", lambda: where)
        return where

    def unreadable_capture(self, tmp_path, stamp="2019-03-04 11:07:00"):
        """A page whose amounts are clipped, so the engine cannot reconcile it."""
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
                    ("", f"{stamp}  2  118  0042", None),
                ],
                clip_digits=1,
            )
        )
        return SourceDocument(original_filename=path.name, sha256="0" * 64, path=str(path), id=0)

    @needs_engine
    def test_a_reply_that_does_not_match_the_printed_total_is_not_stored(
        self, tmp_path, usable, monkeypatch
    ):
        """The gate, at the layer that actually writes rows.

        If `_adjudicate` ever stopped re-checking, or kept the model's lines on
        a receipt that does not reconcile, every other test on this branch still
        passed.
        """
        monkeypatch.setattr(
            HMART.vision,
            "read_receipt",
            lambda pages, where, opener=None: {
                "lines": [{"description": "INVENTED", "amount": "999.00"}],
                "tax": "0.00",
                "balance": "999.00",
            },
        )
        parsed = HMartAdapter().parse(SourceBundle(documents=(self.unreadable_capture(tmp_path),)))
        assert all(not txn.items for txn in parsed.transactions)
        assert not any("read it into a basket that does" in w.message for w in parsed.warnings)

    @needs_engine
    def test_a_model_that_will_not_answer_leaves_the_receipt_set_aside(
        self, tmp_path, usable, monkeypatch
    ):
        monkeypatch.setattr(
            HMART.vision,
            "read_receipt",
            lambda pages, where, opener=None: None,
        )
        parsed = HMartAdapter().parse(SourceBundle(documents=(self.unreadable_capture(tmp_path),)))
        assert all(not txn.items for txn in parsed.transactions)
        # The fixture is built with `clip_digits=1`, so the specific finding is
        # available and is the one reported: the capture is cut through its own
        # amounts. Before, this said the receipt ran past the bottom of the
        # picture and told the reader to upload a second half that does not
        # exist.
        assert any("cut off at its right edge" in w.message for w in parsed.warnings)

    @needs_engine
    def test_a_model_is_never_asked_when_none_is_reachable(self, tmp_path, monkeypatch):
        """The control, and the thing that had silently disabled this whole
        class of test: with the suite-wide dead host, `_adjudicate` returns at
        its first line and nothing below it ever runs."""
        asked = []
        monkeypatch.setattr(
            HMART.vision,
            "read_receipt",
            lambda pages, where, opener=None: asked.append(1),
        )
        HMartAdapter().parse(SourceBundle(documents=(self.unreadable_capture(tmp_path),)))
        assert asked == [], "a model was asked with no model configured"


class TestHowAnImageOnlyBundleFindsThisAdapter:
    """ISSUE-001's actual mechanism, which its regression test did not exercise.

    That test calls `HMartAdapter().parse()` directly, so it proves the adapter
    reads a capture bundle — not that the registry hands it one. An image yields
    no text, so the generic fallback scores nothing on it; if `sniff` stopped
    claiming captures, the bundle would be refused outright and the parse test
    would still pass. Found by /ship's coverage pass on 2026-09-16.
    """

    def capture(self, tmp_path, name="Transaction_030419.png"):
        from tests.receiptimage import build_receipt

        path = tmp_path / name
        path.write_bytes(
            build_receipt(
                [
                    ("", "Customer ID: 40100200300", None),
                    ("", "ITEM 1", "6.99"),
                    ("", "TAX", "0.00"),
                    ("***", "BALANCE", "6.99"),
                    ("", "CREDIT CARD", "6.99"),
                    ("", "2019-03-04 11:07:00  2  118  0042", None),
                ]
            )
        )
        return path

    def test_the_registry_picks_h_mart_for_a_bundle_of_captures(self, tmp_path):
        from unbagged import ingest
        from unbagged.adapters import registry

        stored = ingest.store_upload(
            "Transaction_030419.png", self.capture(tmp_path).read_bytes(), directory=tmp_path
        )
        match = registry.select(ingest.bundle_from([stored]))
        assert match is not None, "no adapter claimed a bundle of receipt captures"
        assert match.adapter.retailer_id == "hmart"
        assert match.confidence == adapter_module.CAPTURE_CONFIDENCE

    def test_it_scores_well_under_a_matching_header_row(self, tmp_path, source):
        """A filename convention is not a positive identification, and the two
        must not be worth the same."""
        assert adapter_module.CAPTURE_CONFIDENCE < 0.9

    def test_an_image_with_no_capture_date_in_its_name_is_not_claimed(self, tmp_path):
        from unbagged import ingest

        stored = ingest.store_upload(
            "holiday-photo.png", self.capture(tmp_path).read_bytes(), directory=tmp_path
        )
        assert HMartAdapter().sniff(ingest.bundle_from([stored])) == 0.0


class TestWhenTheEngineIsNotInstalled:
    """One message for the upload, not one per capture — and the response still
    ingests. Found by /ship's coverage pass: the `OcrUnavailable` branch had no
    test, which is the quiet failure the container test's own docstring names.
    """

    def test_one_message_for_the_whole_upload_and_the_statement_survives(
        self, tmp_path, source, monkeypatch
    ):
        from tests.receiptimage import build_receipt
        from unbagged.transcription import OcrUnavailable

        def refuse(_data):
            raise OcrUnavailable("tesseract is not installed, so an image cannot be read.")

        monkeypatch.setattr(HMART, "transcribe", refuse)

        history = tmp_path / "history.xls"
        history.write_text(source, encoding="utf-8")
        captures = []
        for name in ("Transaction_030419.png", "Transaction_040419.png"):
            path = tmp_path / name
            path.write_bytes(build_receipt([("", "ITEM", "1.00")]))
            captures.append(
                SourceDocument(original_filename=name, sha256=name.ljust(64, "0"), path=str(path))
            )
        parsed = HMartAdapter().parse(
            SourceBundle(
                documents=(
                    SourceDocument(
                        original_filename="history.xls",
                        sha256="1" * 64,
                        path=str(history),
                        id=0,
                    ),
                    *captures,
                )
            )
        )
        engine = [w for w in parsed.warnings if "install" in w.message.lower()]
        assert len(engine) == 1, "one message about the machine, not one per capture"
        assert len(parsed.transactions) > 1, "the statement was lost with the captures"


class TestWhenTwoVisitsFitTheSameCapture:
    """Found by the adversarial re-check on 2026-09-16.

    Where the printed timestamp cannot be read, a capture is placed by the date
    in its filename and its own total. `_match`'s own docstring calls the same
    basket bought twice an ordinary thing — and on one day, the date and the
    total are all the fallback has. Both visits fit; the first was taken,
    silently, and a real basket went against the wrong trip.
    """

    def visit(self, at: str, amount: float) -> Transaction:
        return Transaction(occurred_at=at, total_pre_discount=amount)

    def receipt(self, amount: str) -> rc.Receipt:
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(rc.ReceiptLine("RICE", Decimal(amount)),),
            tax=Decimal("0.00"),
            balance=Decimal(amount),
            complete=True,
        )

    def test_two_visits_on_one_day_for_one_total_match_neither(self):
        visits = [
            self.visit("2019-03-04T09:12:00", 12.34),
            self.visit("2019-03-04T17:40:00", 12.34),
        ]
        assert HMART._match(self.receipt("12.34"), visits, {}) == (None, False)

    def test_one_visit_that_fits_is_still_matched(self):
        visits = [
            self.visit("2019-03-04T09:12:00", 12.34),
            self.visit("2019-03-04T17:40:00", 99.00),
        ]
        assert HMART._match(self.receipt("12.34"), visits, {}) == (0, True)

    def test_a_visit_already_taken_does_not_make_the_rest_ambiguous(self):
        visits = [
            self.visit("2019-03-04T09:12:00", 12.34),
            self.visit("2019-03-04T17:40:00", 12.34),
        ]
        found, by_filename = HMART._match(self.receipt("12.34"), visits, {0: visits[0]})
        assert (found, by_filename) == (1, True)


class TestWhatAWeakMatchIsAllowedToRecord:
    """Found by the adversarial re-check on 2026-09-16.

    The lane and the transaction number share a line with the timestamp and are
    set in the same 10px type. A stamp whose timestamp could not be matched is
    not a stamp whose other two fields can be relied on — and those two were
    being stored as fact, with nothing on screen marking them as the weaker
    reading.
    """

    def receipt(self) -> rc.Receipt:
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(rc.ReceiptLine("RICE", Decimal("12.34")),),
            tax=Decimal("0.00"),
            balance=Decimal("12.34"),
            complete=True,
            stamp=rc.Stamp(occurred_at="2019-03-04T11:07:00", lane="2", number="0042"),
        )

    def test_a_trusted_stamp_still_records_its_lane(self):
        txn = HMART._with_items(Transaction(occurred_at="x"), self.receipt(), {})
        assert (txn.division_code, txn.external_order_id) == ("2", "0042")

    def test_a_stamp_the_join_would_not_use_does_not_become_a_stored_fact(self):
        txn = HMART._with_items(
            Transaction(occurred_at="x"), self.receipt(), {}, stamp_trusted=False
        )
        assert (txn.division_code, txn.external_order_id) == (None, None)


class TestWhatACaptureWithNoTotalIsTold:
    """Found by the adversarial re-check on 2026-09-16.

    `balance is None` is the ordinary top-half-of-a-tall-receipt case as well as
    the not-a-receipt case, and both were being sent the same sentence. One of
    them has an action attached to it: find the other half.
    """

    def message(self, lines) -> str:
        found = rc.Receipt(captures=("Transaction_030419.png",), lines=tuple(lines))
        return HMART._unreconciled(found, Decimal("0.00"), _NoModel(), statement=False)

    def test_a_page_full_of_products_is_told_to_upload_the_rest(self):
        said = self.message([rc.ReceiptLine("RICE", Decimal("10.00"))])
        assert "Upload the rest of the receipt" in said
        assert "no lines, no total" not in said

    def test_a_page_with_nothing_on_it_is_told_that_instead(self):
        said = self.message([])
        assert "no lines, no total" in said


class _NoModel:
    usable = False
    model = None


class TestACaptureAloneCannotSayWhichLineWasTax:
    """Found by the automated review on 2026-09-16.

    `foots()` adds tax back, so a receipt reconciles whether the line above its
    balance was tax or the last thing in the basket. On the statement path the
    stated subtotal settles it. On a captures-only upload nothing does — and the
    visit was stored one line short, with its total short by the same amount and
    the lines summing to it exactly, so nothing on screen showed a gap.
    """

    def receipt(self, *, inferred: bool) -> rc.Receipt:
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(rc.ReceiptLine("RICE", Decimal("10.00")),),
            tax=Decimal("3.00"),
            balance=Decimal("13.00"),
            complete=True,
            tax_inferred=inferred,
            stamp=rc.Stamp(occurred_at="2019-03-04T11:07:00", lane="2", number="0042"),
        )

    def said(self, *, inferred: bool):
        warnings = HMART.WarningCollector()
        txn = HMART._as_transaction(self.receipt(inferred=inferred), {}, warnings)
        return txn, [warning.message for warning in warnings.as_tuple()]

    def test_the_visit_is_kept_and_the_doubt_is_named(self):
        txn, said = self.said(inferred=True)
        assert txn is not None, "refusing it would cost a real basket for a label"
        assert any("with no label this could read" in message for message in said)
        assert any("short that one line" in message for message in said)

    def test_a_receipt_that_printed_the_word_says_nothing(self):
        txn, said = self.said(inferred=False)
        assert txn is not None
        assert said == []


class TestWhenOneCaptureFailsAndTheRestDoNot:
    """Found by the automated review on 2026-09-16.

    `OcrUnavailable` is not only "the engine is not installed": it also carries
    a per-image timeout and a nonzero exit on one file. The report then said
    none of the captures could be read, in the same report as the baskets that
    were.
    """

    @needs_engine
    def test_the_message_counts_what_failed_rather_than_the_whole_upload(
        self, tmp_path, source, monkeypatch
    ):
        from unbagged.transcription import OcrUnavailable

        holder = TestWhenTheCapturesArrive()
        visit = {
            "stamp": "2019-03-04 11:07:00",
            "date": "2019-03-04",
            "name": "Transaction_030419.png",
            "amount": Decimal("12.34"),
        }
        good = holder.capture(tmp_path, visit)
        bad = holder.capture(tmp_path, visit, name="Transaction_030519.png")

        real = HMART.transcribe
        seen: list[int] = []

        def one_of_them_times_out(png, **kw):
            seen.append(1)
            if len(seen) > 1:
                raise OcrUnavailable("tesseract did not finish within 60s on an image")
            return real(png, **kw)

        monkeypatch.setattr(HMART, "transcribe", one_of_them_times_out)
        parsed = holder.parse(tmp_path, source, good, bad)
        said = " ".join(warning.message for warning in parsed.warnings)
        assert "1 of 2 receipt captures" in said
        assert "none could be read" not in said


class TestSayingWhoseMistakeItWas:
    """Found by /investigate on 2026-09-16.

    Two captures in the real corpus print a timestamp that reached no visit.
    Both were this tool misreading the stamp — one minute digit, one year digit
    — and the retailer was consistent throughout. The warning said the capture
    'prints a timestamp that matches no visit in the statement', which reads as
    the response contradicting itself. Blaming a response for our own OCR is
    the failure this adapter exists to avoid.
    """

    @needs_engine
    def test_an_unmatched_stamp_is_reported_as_a_misreading(self, tmp_path, source, monkeypatch):
        holder = TestWhenTheCapturesArrive()
        # Built from the committed fixture's own first row, like the suite
        # above: a hand-written visit matches no statement row, and the receipt
        # then goes down the unmatched path instead of the one under test.
        row = read_tables(source).tables[0].rows[2]
        stamp = row.value(2)[:19]
        visit = {
            "stamp": stamp,
            "date": stamp[:10],
            "name": f"Transaction_{stamp[5:7]}{stamp[8:10]}{stamp[2:4]}.png",
            "amount": Decimal(row.value(4)),
        }
        cap = holder.capture(tmp_path, visit)

        real = rc.read_capture

        def stamp_misread(transcript, capture):
            page = real(transcript, capture)
            # The same instant with one digit of the year wrong, which is what
            # the engine actually did to one real capture.
            broken = stamp[0] + "9" + stamp[2:].replace(" ", "T")

            return replace(page, stamp=rc.Stamp(occurred_at=broken, lane="2", number="0042"))

        monkeypatch.setattr(HMART.rc, "read_capture", stamp_misread)
        parsed = holder.parse(tmp_path, source, cap)
        said = " ".join(w.message for w in parsed.warnings)
        assert "could not read into any visit" in said
        assert "misread digit" in said
        assert "matches no visit in the statement" not in said


class TestWhenTheModelsAnswerIsAccepted:
    """Found by /ship's coverage pass on 2026-09-16.

    Every test on this branch stopped at a refusal. `_adjudicate` returning an
    answer, the sentence naming the figure that answer was checked against, and
    the rebind that puts the model's basket in front of the join were reached
    by nothing — so the half of this path that actually WRITES rows was the
    untested half, including the whole of the statement-anchored route the
    branch was opened for.
    """

    @pytest.fixture()
    def usable(self, monkeypatch):
        """A model the adapter believes it can reach."""
        from unbagged.transcription import ollama

        where = ollama.Availability(ollama.Reachability.OK, "", "localhost:11434", "a-model")
        monkeypatch.setattr(HMART.ollama, "availability", lambda: where)
        return where

    @pytest.fixture()
    def holder(self):
        """The suite above, for its capture builder and its statement bundle."""
        return TestWhenTheCapturesArrive()

    @pytest.fixture()
    def visit(self, source) -> dict:
        row = read_tables(source).tables[0].rows[2]
        stamp = row.value(2)[:19]
        return {
            "stamp": stamp,
            "date": stamp[:10],
            "name": f"Transaction_{stamp[5:7]}{stamp[8:10]}{stamp[2:4]}.png",
            "amount": Decimal(row.value(4)),
        }

    def clipped(self, tmp_path, visit) -> SourceDocument:
        """A capture cut through its own amount column.

        The page the anchor exists for: the clip takes the last digit of every
        amount AND the printed total, so the engine reads no balance off it and
        there is nothing on the page left to check a second reader against.
        """
        from tests.receiptimage import build_receipt

        path = tmp_path / visit["name"]
        path.write_bytes(
            build_receipt(
                [
                    ("", "Customer ID: 40100200300", None),
                    ("", "ITEM 1", str(visit["amount"])),
                    ("", "TAX", "0.00"),
                    ("***", "BALANCE", str(visit["amount"])),
                    ("", "CREDIT CARD", str(visit["amount"])),
                    ("", f"{visit['stamp']}  2  118  0042", None),
                ],
                clip_digits=1,
            )
        )
        return SourceDocument(original_filename=path.name, sha256="0" * 64, path=str(path), id=1)

    def answers(self, monkeypatch, reply):
        monkeypatch.setattr(HMART.vision, "read_receipt", lambda pages, where, opener=None: reply)

    def reads_like_the_real_clip(self, monkeypatch, visit):
        """Stand in the shape the real clipped capture produces.

        The drawn clip is harsher than the real one: `clip_digits=1` removes a
        whole digit-width, so nothing parses at all and the page has no second
        fact on it to corroborate against — which the gate now refuses, rightly.
        A real clip slices THROUGH the last glyph, so most amounts still read
        with a corrupted final digit and only the worst rows are lost. That is
        the state the anchor route exists for, and OCR's own fidelity is pinned
        at the unit level, so it is stood in here rather than drawn.
        """
        half = (visit["amount"] / 2).quantize(Decimal("0.01"))
        real = rc.Receipt(
            captures=(visit["name"],),
            # The last digit corrupted, the way a clip corrupts it.
            lines=(
                rc.ReceiptLine("ITEM ONE", half - Decimal("0.04")),
                rc.ReceiptLine("ITEM TWO", visit["amount"] - half - Decimal("0.04")),
            ),
            balance=None,
            complete=False,
            clipped=True,
        )
        monkeypatch.setattr(HMART.rc, "read_capture", lambda transcript, capture: real)

    def honest(self, visit) -> dict:
        """Two lines summing to what the statement says the visit cost."""
        half = (visit["amount"] / 2).quantize(Decimal("0.01"))
        return {
            "lines": [
                {"description": "ITEM ONE", "amount": f": {half}"},
                {"description": "ITEM TWO", "amount": f": {visit['amount'] - half}"},
            ],
            "tax": "0.00",
            "balance": str(visit["amount"]),
        }

    @needs_engine
    def test_a_clipped_capture_is_quarantined_and_the_visit_keeps_its_total(
        self, tmp_path, source, visit, usable, monkeypatch, holder
    ):
        """The contract after four rounds of review withdrew the anchored route.

        The model may read the page perfectly; its answer is still not stored,
        because a page with no printed total has nothing on it a reading can be
        checked against. What the reader gets instead is the truth: which
        capture, why, and that the visit is not lost — it keeps the figure the
        statement gave it, which no reading of the picture can move.
        """
        self.answers(monkeypatch, self.honest(visit))
        self.reads_like_the_real_clip(monkeypatch, visit)
        parsed = holder.parse(tmp_path, source, self.clipped(tmp_path, visit))
        txn = holder.visit_row(parsed, visit)
        assert txn.items == (), "nothing the model said is stored"
        assert txn.total_pre_discount == pytest.approx(float(visit["amount"])), (
            "and the visit still carries what the statement said it cost"
        )

    @needs_engine
    def test_the_reader_is_told_which_capture_and_why(
        self, tmp_path, source, visit, usable, monkeypatch, holder
    ):
        self.answers(monkeypatch, self.honest(visit))
        self.reads_like_the_real_clip(monkeypatch, visit)
        parsed = holder.parse(tmp_path, source, self.clipped(tmp_path, visit))
        said = " ".join(w.message for w in parsed.warnings)
        assert "cut off at its right edge" in said
        assert "The visit still carries the total the points statement gave for it." in said
        assert "read it into a basket that does" not in said, "nothing was kept"

    @needs_engine
    def test_an_answer_checked_against_the_printed_total_says_so(
        self, tmp_path, source, visit, usable, monkeypatch, holder
    ):
        """The other half of the same sentence. A page that DID print a total
        is checked against it, and the anchor never enters."""
        misread = holder.capture(tmp_path, visit, lines=[Decimal("5.00")], balance=visit["amount"])
        self.answers(monkeypatch, self.honest(visit))
        parsed = holder.parse(tmp_path, source, misread)
        said = " ".join(w.message for w in parsed.warnings)
        assert "the total printed on the receipt" in said
        assert holder.visit_row(parsed, visit).items

    @needs_engine
    def test_a_fabricated_basket_is_refused_on_the_anchored_path(
        self, tmp_path, source, visit, usable, monkeypatch, holder
    ):
        """The safety property, at the layer that writes rows. The anchor is
        matched on a date alone, which is only safe because a wrong one fails
        the arithmetic rather than attaching a basket to the wrong visit."""
        self.answers(
            monkeypatch,
            {
                "lines": [{"description": "INVENTED", "amount": ": 999.00"}],
                "tax": "0.00",
                "balance": ": 999.00",
            },
        )
        parsed = holder.parse(tmp_path, source, self.clipped(tmp_path, visit))
        assert all(not txn.items for txn in parsed.transactions)
        said = " ".join(w.message for w in parsed.warnings)
        assert "read it into a basket that does" not in said
        assert "cut off at its right edge" in said

    @needs_engine
    def test_a_model_authored_tax_cannot_buy_a_wrong_basket(
        self, tmp_path, source, visit, usable, monkeypatch, holder
    ):
        """The hole `_tax_for` was written to close, on the route that has no
        printed total. The balance is set to `anchor + tax`, so `foots` reduces
        to `subtotal - anchor` and a tax sized to absorb the difference cancels
        out of the check instead of paying for it."""
        inflated = visit["amount"] + Decimal("8.00")
        self.answers(
            monkeypatch,
            {
                "lines": [{"description": "INVENTED", "amount": f": {inflated}"}],
                # Within the range a tax may occupy, and exactly the residual.
                "tax": "8.00",
                "balance": str(inflated),
            },
        )
        parsed = holder.parse(tmp_path, source, self.clipped(tmp_path, visit))
        assert all(not txn.items for txn in parsed.transactions)
        assert not any("read it into a basket that does" in w.message for w in parsed.warnings)

    @needs_engine
    def test_the_cap_on_how_many_pages_a_model_is_asked_about_holds(
        self, tmp_path, source, visit, usable, monkeypatch, holder
    ):
        """The other side of the same `if`, and the only thing standing between
        an upload of unreadable captures and a call per capture. With the cap
        spent, the page is set aside unread rather than sent."""
        asked = []
        monkeypatch.setattr(HMART, "MAX_ADJUDICATIONS", 0)
        monkeypatch.setattr(
            HMART.vision,
            "read_receipt",
            lambda pages, where, opener=None: asked.append(1),
        )
        parsed = holder.parse(tmp_path, source, self.clipped(tmp_path, visit))
        assert asked == [], "a model was asked past the cap"
        assert all(not txn.items for txn in parsed.transactions)


class TestAClippedPageIsRefusedOnTheClip:
    """Found by the automated review on PR #90.

    The clipping signal was consulted only inside `_unreconciled`, which runs
    only once the arithmetic has already failed. But a clip cuts through the
    last digit of every amount and the fragment resolves to some OTHER digit —
    so a page can add up while every figure on it is wrong, and the one signal
    saying "these digits are unreliable" was being read only where the digits
    had already given themselves away.
    """

    def clipped(self, **kw):
        return rc.Receipt(
            captures=("Transaction_030419.png",),
            lines=(rc.ReceiptLine("A", Decimal("5.00")), rc.ReceiptLine("B", Decimal("5.00"))),
            tax=Decimal("0.00"),
            balance=Decimal("10.00"),
            complete=True,
            clipped=True,
            **kw,
        )

    class _NoModel:
        usable = False
        model = None

    def test_the_sum_passing_does_not_make_the_digits_trustworthy(self):
        found = self.clipped()
        assert rc.foots(found) is None, "the arithmetic is self-consistent"
        said = HMART._unreconciled(found, None, self._NoModel(), statement=True)
        assert "cut off at its right edge" in said

    def test_and_it_does_not_claim_a_total_it_still_has_was_lost(self):
        """The other clipped shape says the total went with the digits. On this
        page it did not, and saying so would be false."""
        said = HMART._unreconciled(self.clipped(), None, self._NoModel(), statement=True)
        assert "including the total's" not in said
        assert "cannot be trusted" in said

    def test_the_visit_is_told_it_keeps_its_total(self):
        said = HMART._unreconciled(self.clipped(), None, self._NoModel(), statement=True)
        assert "still carries the total the points statement gave for it" in said
