import pytest

from tests.factories import DOCUMENT, sample_result
from unbagged import db, repository
from unbagged.models import (
    DisclosureCategory,
    DisclosureStatus,
    InferenceOrigin,
    ParseResult,
    RequestMeta,
    Scope,
)


@pytest.fixture
def conn(tmp_path):
    with db.open_db(tmp_path / "test.sqlite") as c:
        yield c


class TestRoundTrip:
    """The M1 acceptance criterion: a hand-built ParseResult survives storage."""

    def test_a_parse_result_round_trips_intact(self, conn):
        original = sample_result()  # provenance points at document id 1
        request_id = repository.save_parse_result(conn, original, documents=[DOCUMENT])
        loaded = repository.load_parse_result(conn, request_id)
        assert loaded == original

    def test_provenance_survives(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        # Every cell the UI can show must be traceable back to a document.
        for record in (*loaded.identities, *loaded.transactions,
                       *loaded.inferences, *loaded.disclosures):
            assert record.provenance.locator

    def test_line_items_stay_with_their_basket(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        assert [len(t.items) for t in loaded.transactions] == [3, 1]
        assert loaded.item_count() == 4

    def test_negative_amounts_are_not_filtered(self, conn):
        # Returns and voids are real transactions (HANDOFF.md section 4).
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        assert any(t.total_pre_discount < 0 for t in loaded.transactions)
        assert any(i.retail_amt < 0 for t in loaded.transactions for i in t.items)

    def test_placeholder_rows_are_kept(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        descriptions = [i.description_raw for t in loaded.transactions for i in t.items]
        assert "UNKNOWN" in descriptions

    def test_absence_survives_as_a_finding(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        absent = {d.category for d in loaded.disclosures
                  if d.status is DisclosureStatus.ABSENT}
        assert DisclosureCategory.SOURCES in absent
        assert loaded.missing_categories() == ()

    def test_an_empty_result_round_trips(self, conn):
        meta = RequestMeta(retailer_id="hmart", display_name="H Mart")
        request_id = repository.save_parse_result(conn, ParseResult(request=meta))
        loaded = repository.load_parse_result(conn, request_id)
        assert loaded == ParseResult(request=meta)


class TestTypes:
    def test_enums_come_back_as_enums(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        assert loaded.inferences[1].origin is InferenceOrigin.APPENDED_THIRD_PARTY
        assert loaded.inferences[1].subject is Scope.HOUSEHOLD

    def test_tri_state_derivability_survives(self, conn):
        # True / False / unknown are three different claims; SQLite stores the
        # first two as 1/0, and NULL must not collapse into False.
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        loaded = repository.load_parse_result(conn, request_id)
        assert [f.derivable_from_txns for f in loaded.inferences] == [True, False]

    def test_an_unrecognised_stored_value_does_not_break_reads(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(doc_id=None))
        conn.execute("UPDATE txn SET channel = 'curbside' WHERE request_id = ?",
                     (request_id,))
        # A retailer inventing a channel should not make the database unreadable.
        loaded = repository.load_parse_result(conn, request_id)
        assert loaded.transactions[0].channel == "curbside"


class TestDocuments:
    def test_documents_get_ids_for_adapters_to_reference(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(),
                                                  documents=[DOCUMENT])
        docs = repository.get_documents(conn, request_id)
        assert len(docs) == 1
        assert docs[0].id is not None
        assert docs[0].sha256 == DOCUMENT.sha256


class TestMultipleRequests:
    def test_requests_stay_separable(self, conn):
        first = repository.save_parse_result(conn, sample_result(doc_id=None))
        second = repository.save_parse_result(
            conn, ParseResult(request=RequestMeta("safeway", "Safeway"))
        )
        assert first != second
        assert len(repository.list_requests(conn)) == 2
        assert repository.load_parse_result(conn, second).transactions == ()

    def test_deleting_a_request_removes_only_that_request(self, conn):
        first = repository.save_parse_result(conn, sample_result(doc_id=None))
        second = repository.save_parse_result(conn, sample_result(doc_id=None))
        repository.delete_request(conn, first)
        assert repository.load_parse_result(conn, first) is None
        assert repository.load_parse_result(conn, second) is not None
        assert conn.execute("SELECT count(*) c FROM txn_item").fetchone()["c"] == 4

    def test_loading_an_unknown_request_returns_none(self, conn):
        assert repository.load_parse_result(conn, 999) is None


class TestAtomicity:
    def test_a_failed_write_leaves_no_partial_request(self, conn, monkeypatch):
        # Half a report is indistinguishable from a report that was that short.
        def boom(*args, **kwargs):
            raise RuntimeError("adapter blew up mid-write")

        monkeypatch.setattr(repository, "insert_documents", boom)
        with pytest.raises(RuntimeError):
            repository.save_parse_result(conn, sample_result(), documents=[DOCUMENT])
        assert conn.execute("SELECT count(*) c FROM request").fetchone()["c"] == 0
        assert conn.execute("SELECT count(*) c FROM txn").fetchone()["c"] == 0


class TestProvenancePage:
    """HANDOFF.md section 4 rule 1 requires a page alongside the document and
    locator; the section 5 tables omit it, so migration 002 adds it."""

    def test_page_survives_the_round_trip(self, conn):
        request_id = repository.save_parse_result(conn, sample_result(), documents=[DOCUMENT])
        loaded = repository.load_parse_result(conn, request_id)
        assert loaded.identities[0].provenance.page == 3
        assert loaded.transactions[0].provenance.page == 5
        assert loaded.inferences[0].provenance.page == 3
        assert loaded.disclosures[0].provenance.page == 3

    def test_page_is_optional(self, conn):
        # A CSV bundle has no pages, and that is not a parse failure.
        result = ParseResult(request=RequestMeta("safeway", "Safeway"))
        request_id = repository.save_parse_result(conn, result)
        assert repository.load_parse_result(conn, request_id) == result
