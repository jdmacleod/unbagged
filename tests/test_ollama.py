"""Asking a local model about a page the engine could not read.

**No test here reaches a model.** Every one injects its own transport, and the
autouse fixture points the host at an address nothing answers on, so a
contributor with `ollama serve` running gets the same result as CI. The
reference implementation this is ported from records that lesson the hard way:
without it, a test suite silently exercised a developer's live model and wrote
to their real config directory.

What is asserted is the shape of the request, the four answers a preflight can
give, and the one rule that matters — a model's reading is kept only when it
makes the receipt add up.
"""

import json
from decimal import Decimal

import pytest

from unbagged.adapters.hmart import receipt as rc
from unbagged.adapters.hmart import vision
from unbagged.transcription import ollama


@pytest.fixture(autouse=True)
def _no_model_name_from_the_environment(monkeypatch):
    """`conftest.py` already points the host at nothing; this clears the model.

    A contributor who has named a model in their own environment would
    otherwise see the preflight tests below assert against that name.
    """
    monkeypatch.delenv(ollama.MODEL_ENV, raising=False)


def replies(*bodies):
    """A transport serving canned replies in order, recording what it was sent."""
    sent = []

    def opener(request, timeout):
        sent.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": json.loads(request.data) if request.data else None,
                "timeout": timeout,
            }
        )
        body = bodies[min(len(sent) - 1, len(bodies) - 1)]
        if isinstance(body, Exception):
            raise body
        return body

    opener.sent = sent
    return opener


def chat(payload: dict) -> dict:
    return {"message": {"role": "assistant", "content": json.dumps(payload)}, "done": True}


#: A host with the shipped default pulled. Named off `DEFAULT_MODEL` rather than
#: written out, because these tests are about the preflight and the transport
#: and not about which model was measured fastest this year — pinning the tag
#: here made eleven of them fail when the bake-off changed it.
TAGS = {"models": [{"name": ollama.DEFAULT_MODEL}]}


class TestWhetherAModelMayBeAsked:
    def test_nothing_configured_is_off_rather_than_broken(self, monkeypatch):
        """The default. A response still ingests; the engine reads it alone."""
        monkeypatch.delenv(ollama.HOST_ENV, raising=False)
        found = ollama.availability()
        assert found.status is ollama.Reachability.OFF
        assert found.message == ""

    def test_a_host_that_is_not_this_machine_is_refused_until_acknowledged(self, monkeypatch):
        """It would receive images of your receipts, so you have to say so.

        `docs/handoff.md` §6.9 allows an outbound call that is opt-in, off by
        default and clearly labelled. This is the label.
        """
        monkeypatch.setenv(ollama.HOST_ENV, "192.168.1.34:11434")
        found = ollama.availability(opener=replies(TAGS))
        assert found.status is ollama.Reachability.REFUSED
        assert ollama.REMOTE_ENV in found.message

    def test_an_acknowledged_remote_host_is_allowed(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "192.168.1.34:11434")
        monkeypatch.setenv(ollama.REMOTE_ENV, "true")
        assert ollama.availability(opener=replies(TAGS)).usable

    @pytest.mark.parametrize(
        "host",
        ["localhost:11434", "http://127.0.0.1:11434", "[::1]:11434"],
    )
    def test_this_machine_needs_no_acknowledgement(self, monkeypatch, host):
        monkeypatch.setenv(ollama.HOST_ENV, host)
        assert ollama.availability(opener=replies(TAGS)).usable

    def test_an_address_it_cannot_parse_is_treated_as_remote(self):
        """Fails CLOSED.

        Ollama's own convention is a scheme-less host, which `urlparse` leaves
        with no hostname at all. A misparse read as loopback is a way to send
        receipts to another machine by writing the address the way the vendor's
        documentation writes it.
        """
        assert ollama.is_loopback("nonsense::") is False

    def test_a_host_that_does_not_answer_says_how_to_start_one(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        found = ollama.availability(opener=replies(ConnectionError("refused")))
        assert found.status is ollama.Reachability.UNREACHABLE
        assert "ollama serve" in found.message

    def test_a_model_that_is_not_pulled_says_how_to_pull_it(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        monkeypatch.setenv(ollama.MODEL_ENV, "some-other-model")
        found = ollama.availability(opener=replies(TAGS))
        assert found.status is ollama.Reachability.MODEL_MISSING
        assert "ollama pull some-other-model" in found.message

    def test_a_family_name_finds_the_tag_that_was_pulled(self, monkeypatch):
        """People write `minicpm-v4.5` as often as `minicpm-v4.5:8b`."""
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        monkeypatch.setenv(ollama.MODEL_ENV, ollama.DEFAULT_MODEL.split(":")[0])
        assert ollama.availability(opener=replies(TAGS)).usable


class TestFindingTheAnswerInTheReply:
    """Where the object is, rather than assuming it is the whole reply.

    Constrained decoding usually does make the reply one bare JSON object, but
    not always: a model that wraps its answer in a code fence returned nothing
    at all, which reads downstream as a model that could not answer rather than
    one that answered in a fence.
    """

    @pytest.fixture()
    def where(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        return ollama.availability(opener=replies(TAGS))

    def _reply(self, where, text):
        opener = replies({"message": {"content": text}, "done": True, "done_reason": "stop"})
        return vision.read_receipt([b"png"], where, opener=opener)

    def test_a_bare_object_is_read(self, where):
        assert self._reply(where, '{"lines": [], "tax": "0", "balance": "1"}') is not None

    def test_an_object_inside_a_code_fence_is_read(self, where):
        found = self._reply(where, '```json\n{"lines": [], "tax": "0", "balance": "1.00"}\n```')
        assert found is not None
        assert found["balance"] == "1.00"

    def test_a_brace_inside_a_description_does_not_end_the_object(self, where):
        """Depth counting that ignores string state stops at the wrong brace.

        `jsonscan` is the repository's string-aware scanner and exists because
        two other readers hit this independently.
        """
        found = self._reply(
            where,
            'Here you go: {"lines": [{"description": "ODD {NAME", "amount": "1.00"}],'
            ' "tax": "0", "balance": "1.00"}',
        )
        assert found is not None
        assert found["lines"][0]["description"] == "ODD {NAME"

    def test_prose_with_no_object_in_it_is_no_answer(self, where):
        assert self._reply(where, "I am unable to read this receipt.") is None


class TestAskingWithoutASchema:
    """`schema=None`, which no caller in the application may pass.

    It exists for `tools/bakeoff_vision.py`, which measures what a model does
    with the schema against what it does without — and that comparison is only
    worth anything if the schema is the only difference between the two calls.
    """

    @pytest.fixture()
    def where(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        return ollama.availability(opener=replies(TAGS))

    def test_no_format_is_sent_at_all(self, where):
        opener = replies(chat({"lines": [], "tax": "0", "balance": "0"}))
        ollama.ask([b"png"], "read it", None, where, opener=opener)
        assert "format" not in opener.sent[0]["body"]

    def test_everything_else_is_the_call_the_lane_makes(self, where):
        """The point of the parameter: one difference, not two."""
        with_schema = replies(chat({"lines": [], "tax": "0", "balance": "0"}))
        without = replies(chat({"lines": [], "tax": "0", "balance": "0"}))
        ollama.ask([b"png"], "read it", vision.RECEIPT_SCHEMA, where, opener=with_schema)
        ollama.ask([b"png"], "read it", None, where, opener=without)
        a = dict(with_schema.sent[0]["body"])
        b = dict(without.sent[0]["body"])
        assert a.pop("format") == vision.RECEIPT_SCHEMA
        assert a == b


class TestWhatIsAsked:
    @pytest.fixture()
    def where(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        return ollama.availability(opener=replies(TAGS))

    def test_the_request_is_the_one_a_small_model_can_answer(self, where):
        opener = replies(chat({"lines": [], "tax": "0.00", "balance": "0.00"}))
        vision.read_receipt([b"png"], where, opener=opener)
        (call,) = opener.sent
        assert call["url"].endswith("/api/chat"), (
            "not /api/generate: it mangles a constrained reply"
        )
        body = call["body"]
        assert body["stream"] is False
        assert body["options"]["temperature"] == 0
        # The whole schema, not the string "json". Constrained decoding is what
        # stops a small model answering in prose about the receipt.
        assert body["format"] == vision.RECEIPT_SCHEMA
        assert len(body["messages"][0]["images"]) == 1

    def test_every_capture_of_one_receipt_goes_in_one_call(self, where):
        """A receipt split across two screens gets one answer, not two halves."""
        opener = replies(chat({"lines": [], "tax": "0.00", "balance": "0.00"}))
        vision.read_receipt([b"top", b"bottom"], where, opener=opener)
        body = opener.sent[0]["body"]
        assert len(body["messages"][0]["images"]) == 2
        # The image spends context a character count of the prompt cannot see.
        # Under-budget it and the server rejects the prompt outright.
        assert body["options"]["num_ctx"] >= ollama.IMAGE_TOKENS * 2

    def test_a_build_that_rejects_think_is_retried_without_it(self, where):
        """Older builds 400 on the field. Losing the call to that would be silly."""
        import urllib.error

        rejection = urllib.error.HTTPError("u", 400, "bad", None, None)
        opener = replies(rejection, chat({"lines": [], "tax": "0", "balance": "0"}))
        vision.read_receipt([b"png"], where, opener=opener)
        assert len(opener.sent) == 2
        assert "think" in opener.sent[0]["body"]
        assert "think" not in opener.sent[1]["body"]

    def test_an_answer_routed_through_a_reasoning_channel_is_read(self, where):
        """Observed: a vision model under a schema constraint leaves `content`
        empty and puts the whole answer in `thinking`."""
        body = {
            "message": {"content": "", "thinking": json.dumps({"lines": [], "balance": "1.00"})},
            "done_reason": "stop",
        }
        assert vision.read_receipt([b"png"], where, opener=replies(body)) is not None

    def test_an_answer_that_was_cut_off_is_not_read(self, where):
        """`length` means it never finished. Its numbers were never committed to.

        Harvesting them is exactly the invention the arithmetic gate exists to
        catch — better to return nothing and let the receipt be set aside.
        """
        body = {
            "message": {"content": "", "thinking": '{"lines": [{"amount": "3.'},
            "done_reason": "length",
        }
        assert vision.read_receipt([b"png"], where, opener=replies(body)) is None

    def test_a_model_that_will_not_answer_costs_one_receipt_and_not_the_upload(self, where):
        assert vision.read_receipt([b"png"], where, opener=replies(ConnectionError("gone"))) is None

    def test_nothing_is_sent_when_no_model_may_be_asked(self, monkeypatch):
        monkeypatch.delenv(ollama.HOST_ENV, raising=False)
        opener = replies(chat({"lines": []}))
        assert vision.read_receipt([b"png"], ollama.availability(), opener=opener) is None
        assert opener.sent == [], "a request was made with no model configured"


class TestWhatIsDoneWithTheAnswer:
    """The rule the whole path turns on."""

    #: A page the engine DID read a total off. That number is the gate.
    LIKE = rc.Receipt(
        captures=("a.png",),
        lines=(rc.ReceiptLine("REAL", Decimal("87.65")),),
        customer_id="40100200300",
        tax=Decimal("0.00"),
        balance=Decimal("87.65"),
        complete=True,
    )

    def test_a_reading_that_matches_the_printed_total_becomes_a_receipt(self):
        found = rc.from_reply(
            {
                "lines": [
                    {"description": "PEELED GARLIC 1 LB", "amount": "91.40"},
                    {"description": "SC - CHK BNLS", "amount": "-3.75"},
                ],
                "tax": "0.00",
                "balance": "87.65",
            },
            self.LIKE,
        )
        assert rc.foots(found) is None
        assert found.customer_id == "40100200300", "what was read off the page is kept"

    def test_an_invented_basket_is_refused_however_self_consistent(self):
        """Found by the adversarial pass. The gate was checking the model
        against its own numbers.

        `from_reply` took `balance` out of the reply too, so `foots()` compared
        three model-authored figures to each other and any self-consistent JSON
        passed. A fabricated basket of 1000.00 was accepted against a page the
        engine had read as 87.65 — while the module docstring claimed the
        printed total was "the one fact in the room the model had no hand in".
        """
        assert (
            rc.from_reply(
                {
                    "lines": [{"description": "FABRICATED", "amount": "1000.00"}],
                    "tax": "0.00",
                    "balance": "1000.00",
                },
                self.LIKE,
            )
            is None
        )

    def test_a_page_with_no_printed_total_is_never_adjudicated(self):
        """Nothing to check the answer against, so there is no answer to keep.

        Also the only thing standing between a capture and prompt injection:
        the images arrive by mail from outside the trust boundary, and a page
        the engine found no receipt on is exactly where text rendered into the
        image would have nothing contradicting it.
        """
        blank = rc.Receipt(captures=("a.png",), lines=(), balance=None)
        assert (
            rc.from_reply(
                {
                    "lines": [{"description": "X", "amount": "5.00"}],
                    "tax": "0.00",
                    "balance": "5.00",
                },
                blank,
            )
            is None
        )

    def test_a_reading_whose_lines_do_not_reach_the_printed_total_is_rejected(self):
        found = rc.from_reply(
            {"lines": [{"description": "X", "amount": "6.99"}], "tax": "0.00", "balance": "87.65"},
            self.LIKE,
        )
        assert found is not None, "the balance matched, so the shape is accepted"
        assert rc.foots(found) == Decimal("-80.66"), "and the arithmetic then refuses it"

    def test_one_unreadable_amount_voids_the_whole_answer(self):
        """Not just that line.

        A basket missing a line still adds up if the total was adjusted to
        match, and skipping quietly is how a short basket gets stored looking
        complete.
        """
        assert (
            rc.from_reply(
                {
                    "lines": [
                        {"description": "X", "amount": "6.99"},
                        {"description": "Y", "amount": "about four dollars"},
                    ],
                    "tax": "0.00",
                    "balance": "87.65",
                },
                self.LIKE,
            )
            is None
        )

    def test_a_not_a_number_amount_is_not_an_amount(self):
        """`Decimal("NaN")` parses without raising and compares false against
        everything, so it used to survive to the gate and fail there by
        accident. The gate should not be load-bearing for type safety."""
        assert (
            rc.from_reply(
                {"lines": [{"description": "X", "amount": "NaN"}], "tax": "0", "balance": "87.65"},
                self.LIKE,
            )
            is None
        )

    def test_an_answer_with_no_total_is_not_a_receipt(self):
        assert rc.from_reply({"lines": [{"description": "X", "amount": "1.00"}]}, self.LIKE) is None

    def test_an_empty_answer_is_not_a_receipt(self):
        assert rc.from_reply({"lines": [], "tax": "0.00", "balance": "87.65"}, self.LIKE) is None


def rejected(code: int, said: str):
    """An HTTP error carrying the server's own explanation in its body.

    Which is where Ollama puts it. `str(HTTPError)` is the status line and the
    reason phrase and nothing else, so an overflow marker matched against the
    exception's text matched nothing a real server has ever sent.
    """
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "u", code, "Bad Request", {}, io.BytesIO(json.dumps({"error": said}).encode())
    )


class TestGrowingTheWindowWhenTheAnswerWillNotFit:
    """Found by the adversarial re-check on 2026-09-16.

    `think` is on the payload by construction, so a rule that tested for it
    before testing the overflow markers read EVERY first rejection as "this
    build predates the field". A modern server rejecting for size got one
    doubling instead of two, burned an attempt on the misdiagnosis, and lost
    the reasoning-channel suppression for the rest of the call.
    """

    @pytest.fixture()
    def where(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        return ollama.availability(opener=replies(TAGS))

    def sizes(self, opener):
        return [sent["body"]["options"]["num_ctx"] for sent in opener.sent]

    #: A fresh exception per reply. An `HTTPError`'s body is a stream and reads
    #: once, so serving one object twice makes the second read come back empty.
    OVER = "input length 9000 exceeds context length 4096"

    def test_a_server_that_rejects_for_size_gets_both_doublings(self, where):
        opener = replies(
            rejected(400, self.OVER),
            rejected(400, self.OVER),
            chat({"lines": [], "tax": "0", "balance": "0"}),
        )

        assert vision.read_receipt([b"png"], where, opener=opener) is not None
        first, *rest = self.sizes(opener)
        assert rest == [first * 2, first * 4]

    def test_the_reasoning_channel_stays_suppressed_on_a_server_that_honours_it(self, where):
        opener = replies(rejected(400, self.OVER), chat({"lines": [], "tax": "0", "balance": "0"}))

        vision.read_receipt([b"png"], where, opener=opener)
        assert all("think" in sent["body"] for sent in opener.sent)

    def test_dropping_think_does_not_cost_a_sizing_attempt(self, where):
        """It is a version fix, not an attempt at the answer."""
        opener = replies(
            rejected(400, "unknown field: think"),
            rejected(400, self.OVER),
            rejected(400, self.OVER),
            chat({"lines": [], "tax": "0", "balance": "0"}),
        )

        assert vision.read_receipt([b"png"], where, opener=opener) is not None
        assert len(opener.sent) == 4

    def test_an_overflow_reported_as_a_server_error_is_still_retried(self, where):
        """The difference between a 400 and a 500 here is a version."""
        opener = replies(
            rejected(500, "context window exceeded"),
            chat({"lines": [], "tax": "0", "balance": "0"}),
        )
        assert vision.read_receipt([b"png"], where, opener=opener) is not None
        assert len(opener.sent) == 2


class TestAnAnswerThatRanOutOfWindowEveryTime:
    """Found by the adversarial re-check on 2026-09-16.

    The `length` branch grew the window and retried, but only `while attempt <
    MAX_SIZED_RETRIES` — so on the last attempt control fell through and
    returned the truncated answer the branch exists to refuse. A short basket is
    exactly what the gate cannot see: it reconciles against whatever total came
    with it.
    """

    @pytest.fixture()
    def where(self, monkeypatch):
        monkeypatch.setenv(ollama.HOST_ENV, "localhost:11434")
        return ollama.availability(opener=replies(TAGS))

    def test_nothing_is_returned_rather_than_a_prefix_of_a_basket(self, where):
        cut_off = {
            "message": {"content": json.dumps({"lines": [{"amount": "1.00"}]})},
            "done_reason": "length",
        }
        opener = replies(cut_off, cut_off, cut_off, cut_off)
        assert vision.read_receipt([b"png"], where, opener=opener) is None
