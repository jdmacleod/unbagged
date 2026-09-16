"""A local vision model, asked only about the captures arithmetic rejected.

The deterministic engine reads 43 of the 44 real receipts into baskets that
reconcile against the totals the receipts print on themselves. This is for the
rest: a handful of pages per response, not all of them, which is what makes a
model call affordable inside the request a person is waiting on.

**Its answer is accepted only if it makes the receipt add up.** That is not a
courtesy check — it is the entire basis for using a model here at all. A
language model asked to read a number will always return one, confidently, and
nothing in its reply distinguishes a reading from an invention. The receipt
states its own total, so there is one fact in the room the model had no hand
in, and it is the only thing allowed to decide.

That is also why this asks one model rather than two. The reference
implementation this is ported from (`~/Projects/financial-dashboard`,
`engine/ocr.py`) requires two models from different families to agree, because
on a scanned bank statement there is nothing to check an answer against and a
model agreeing with itself is not evidence. Here there is: the receipt's own
total, and the points statement's separate figure for the same visit. The
arithmetic does the work corroboration does there, and does it better.

Nothing here knows what a receipt is. The prompt and the schema are the
caller's — see `adapters/hmart/vision.py` — which is what the package docstring
one level up promises and what this module used to quietly break by holding a
receipt prompt of its own.

Off unless configured. `docs/handoff.md` §6.9 permits an outbound call that is
opt-in, off by default, and clearly labelled as sending data off-device; this
is written to that shape. A loopback host is the intended case. A host that is
not loopback would receive images of your receipts, and is refused until you
say plainly that you want that.

Driven with `urllib` from the standard library rather than an HTTP client or
the vendor's SDK. The shipped image installs from a hash-pinned lock, and
neither would earn its place there for one POST.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Where the model is. Unset means the whole path is off, which is the default.
HOST_ENV = "UNBAGGED_OLLAMA_HOST"
MODEL_ENV = "UNBAGGED_OLLAMA_VISION_MODEL"
#: Acknowledgement that a non-loopback host will receive your receipts.
REMOTE_ENV = "UNBAGGED_ALLOW_REMOTE_OLLAMA"

#: Measured, not chosen. `src/unbagged/adapters/hmart/NOTES.md` carries the
#: bake-off this came out of: twelve vision models over six receipt-shaped
#: pages, scored through this lane. Three clear the gate on every page it can be
#: cleared on, and this is the fastest of them at a ~10s median against a 600s
#: budget for a whole response.
#:
#: It is one host on one day, and a re-pulled tag can be a different build.
#: `python -m tools.bakeoff_vision` is how to check rather than assume.
DEFAULT_MODEL = "minicpm-v4.5:8b"

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})

#: Seconds. A vision call on a page this size runs in tens of seconds on a
#: laptop GPU and minutes on a cold CPU; this is a hang, not a slow answer.
TIMEOUT_SECONDS = 180
PREFLIGHT_TIMEOUT_SECONDS = 10

#: The most of a model's reply this will read.
#:
#: The one inbound channel from a process outside this app — and, with the
#: remote acknowledgement given, from another machine. Every other failure of
#: this transport is mapped to a ConnectionError the caller handles; an
#: unbounded body was not bounded at all, and got read fully into memory inside
#: a request someone is waiting on. A transcribed receipt is kilobytes.
MAX_REPLY_BYTES = 4 * 1024 * 1024

#: Context the encoded image spends, which a prompt measured in characters
#: cannot see. Over-estimated on purpose: too large costs memory, too small
#: gets the prompt rejected outright with nothing to read in the reply.
IMAGE_TOKENS = 3072
NUM_CTX_FLOOR = 8192

#: How many times a rejected-for-size prompt may be retried with a larger window.
#:
#: Bounded, because each attempt costs a full model call. Two doublings take the
#: budget from one image's worth to four, which covers a taller capture than any
#: observed without letting a pathological page loop.
MAX_SIZED_RETRIES = 2

#: Substrings a server uses to say the prompt overran the context window.
#:
#: llama.cpp and Ollama phrase this differently across builds, so several are
#: matched. Only consulted on a failure, so a false match costs one bounded
#: retry and nothing else.
#:
#: The reference implementation this is ported from also infers truncation by
#: comparing `prompt_eval_count` against a character-count estimate of the
#: prompt. That does not transfer here: this prompt is a few hundred characters
#: and one or more IMAGES, and the image tokens are most of the budget and
#: invisible to any character estimate. The signals that do transfer are the
#: server saying so, and the reply saying it stopped for length.
_OVERFLOW_MARKERS = (
    "exceed_context_size",
    "exceeds context",
    "context length",
    "context window",
    "context size",
    "num_ctx",
    "too large for this model",
)


class Reachability(Enum):
    OFF = "off"  # not configured; the deterministic reader is on its own
    OK = "ok"
    NO_VISION = "no_vision"  # pulled, but the server says it cannot take images
    REFUSED = "refused"  # a remote host, unacknowledged
    UNREACHABLE = "unreachable"
    MODEL_MISSING = "model_missing"


@dataclass(frozen=True)
class Availability:
    """Whether a model can be asked, and what to say if not.

    `message` is written for a person and is empty when the answer is OK. The
    caller prints it as a parse warning, so it reaches the reader of a response
    rather than a log nobody opens.
    """

    status: Reachability
    message: str = ""
    host: str | None = None
    model: str = DEFAULT_MODEL

    @property
    def usable(self) -> bool:
        return self.status is Reachability.OK


def configured_host() -> str | None:
    host = (os.environ.get(HOST_ENV) or "").strip()
    return host or None


def configured_model() -> str:
    return (os.environ.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_loopback(host: str) -> bool:
    """Is this host on this machine?

    Fails CLOSED. Ollama's own convention is a host with no scheme
    (`192.168.1.34:11434`), which `urlparse` puts entirely in `.path` while
    leaving `.hostname` as None — so a misparse must read as remote, or the
    acknowledgement below can be stepped around by writing the address the way
    the vendor's own documentation writes it.

    An address written the way a URL requires works in both families:
    `localhost:11434`, `http://127.0.0.1:11434`, `[::1]:11434`. A bare
    unbracketed `::1` is not a valid authority and is read as remote, which is
    the safe direction — it refuses until acknowledged rather than sending
    anywhere unasked.
    """
    normalised = host if "://" in host else f"http://{host}"
    try:
        name = urlparse(normalised).hostname
    except ValueError:
        return False
    return name in LOOPBACK if name else False


def availability(opener=None) -> Availability:
    """Can a model be asked about a receipt? Never raises.

    One GET, and four different answers, because each of them has a different
    next step and telling someone the wrong one sends them to solve a problem
    they do not have.
    """
    host = configured_host()
    model = configured_model()
    if host is None:
        return Availability(Reachability.OFF, model=model)
    if not is_loopback(host) and not _truthy(os.environ.get(REMOTE_ENV)):
        return Availability(
            Reachability.REFUSED,
            f"{HOST_ENV} is set to {host}, which is not this machine. Reading a "
            "receipt there would send an image of it — your shopping, your card's "
            f"last digits, the shop and the hour — to that host. Set {REMOTE_ENV}=true "
            "if that is what you want.",
            host,
            model,
        )

    try:
        body = _get(f"{host.rstrip('/')}/api/tags", PREFLIGHT_TIMEOUT_SECONDS, opener)
    except Exception as exc:  # noqa: BLE001 - a preflight has no failure it may raise on
        return Availability(
            Reachability.UNREACHABLE,
            f"No model answered at {host} ({type(exc).__name__}). Start it with "
            "`ollama serve`. Nothing else about this response is affected.",
            host,
            model,
        )

    names = {str(entry.get("name", "")) for entry in (body.get("models") or [])}
    if not _present(model, names):
        return Availability(
            Reachability.MODEL_MISSING,
            f"{host} is answering but does not have {model!r}. Pull it with "
            f"`ollama pull {model}`, or name one you have in {MODEL_ENV}.",
            host,
            model,
        )
    if not _can_see(host, model, opener):
        return Availability(
            Reachability.NO_VISION,
            f"{model!r} is pulled on {host} but cannot read images. Name a "
            f"vision model in {MODEL_ENV} — without one, a receipt the "
            "deterministic reader could not resolve is simply set aside.",
            host,
            model,
        )
    return Availability(Reachability.OK, "", host, model)


def _can_see(host: str, model: str, opener=None) -> bool:
    """Does this model take images?

    Asked of the server, never inferred from the name. A model's FAMILY is not
    a vision signal — a text-only sibling shares the family of a multimodal one,
    so `qwen2.5` tells you nothing about `qwen2.5vl`. `/api/show` reports what
    the server actually loaded.

    Fails OPEN: a server too old to report capabilities should not have its
    model refused on that account. Getting this wrong in that direction costs a
    misleading message; the other direction costs the feature entirely.

    Without it, a text-only model named by mistake passes the preflight, every
    receipt comes back unreadable, and the reader is told "could not read it"
    rather than "that model cannot see".
    """
    try:
        body = _post_to(
            f"{host.rstrip('/')}/api/show", {"model": model}, PREFLIGHT_TIMEOUT_SECONDS, opener
        )
    except Exception:  # noqa: BLE001 - a preflight has no failure it may raise on
        return True
    capabilities = body.get("capabilities")
    if not isinstance(capabilities, list):
        return True
    return "vision" in capabilities


def _present(model: str, names: set[str]) -> bool:
    """Is this model pulled?

    Tolerant of the two ways people write a tag: `qwen2.5vl` for a pulled
    `qwen2.5vl:7b`, and a bare name for an implicit `:latest`.
    """
    if model in names or (model if ":" in model else f"{model}:latest") in names:
        return True
    return any(name.split(":", 1)[0] == model for name in names)


def ask(
    pages: list[bytes],
    prompt: str,
    schema: dict,
    where: Availability,
    opener=None,
) -> dict | None:
    """Put one question about some images to the local model. None on any failure.

    Generic on purpose: the prompt and the schema come from the caller, because
    this package reads pixels and does not know what a receipt is. Keeping a
    receipt prompt here made the package docstring's own boundary claim false —
    `adapters/hmart/vision.py` is where that knowledge belongs.

    Returns the raw reply. It is a claim, not a reading: whatever asked has to
    put it through a check the model had no hand in, and throw it away if it
    does not hold.
    """
    if not where.usable or where.host is None:
        return None
    payload = {
        "model": where.model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                # Every capture of one receipt in one call, so a model asked
                # about a receipt split across two screens sees both halves and
                # can give one answer for the whole of it.
                "images": [base64.b64encode(page).decode("ascii") for page in pages],
            }
        ],
        "stream": False,
        # The full schema, not the string "json". Constrained decoding is what
        # keeps a small model from answering in prose about the receipt.
        "format": schema,
        "options": {
            "temperature": 0,
            "num_ctx": NUM_CTX_FLOOR + IMAGE_TOKENS * len(pages),
        },
        # Suppresses a reasoning channel where it is honoured. Builds that
        # predate the field reject the whole request, so it is dropped and
        # retried once rather than losing the call to a version difference.
        "think": False,
    }
    url = f"{where.host.rstrip('/')}/api/chat"
    attempt = 0
    while attempt <= MAX_SIZED_RETRIES:
        try:
            body = _post(url, payload, opener)
        except _Rejected as exc:
            # The overflow markers are tested FIRST. `think` is on the payload by
            # construction, so a rule that checked for it first read every 400 as
            # "this build predates the field" — including the one 400 the sizing
            # retries exist for. A modern server rejecting for size got its
            # window doubled once instead of twice, burned an attempt on the
            # misdiagnosis, and lost the reasoning-channel suppression for the
            # rest of the call.
            if _is_overflow(exc):
                if attempt >= MAX_SIZED_RETRIES:
                    return None
                payload["options"]["num_ctx"] *= 2
                attempt += 1
                continue
            if "think" in payload:
                # A build that predates the field rejects the whole request.
                # Dropping it is a version fix, not an attempt at the answer, so
                # it does not count against the sizing budget.
                payload.pop("think")
                continue
            return None
        except Exception:  # noqa: BLE001 - a model that will not answer is not a failed upload
            log.debug("vision read failed", exc_info=True)
            return None

        if body.get("done_reason") == "length":
            # The model ran out of window mid-answer. Its `lines` array is a
            # prefix of a basket it never finished, and a short basket is
            # exactly what the gate cannot see — it reconciles against whatever
            # total came with it. Grow the window and ask again rather than
            # reading a truncated answer; out of retries, return nothing at all.
            # Falling through to `_content` on the last attempt returned the
            # truncated answer this branch exists to refuse.
            if attempt >= MAX_SIZED_RETRIES:
                return None
            payload["options"]["num_ctx"] *= 2
            attempt += 1
            continue
        return _content(body)
    return None


def _is_overflow(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)


def _content(body: dict) -> dict | None:
    """The JSON object in the reply, from wherever the model put it.

    A vision model under a schema constraint has been seen to leave `content`
    empty and route its whole answer through a separate reasoning channel. That
    channel is read only when the model says it FINISHED — `done_reason` of
    `length` means the answer was cut off mid-thought, and harvesting numbers
    from a reply the model never committed to is exactly the invention the
    arithmetic gate exists to catch. Better to return nothing.
    """
    message = body.get("message") or {}
    text = (message.get("content") or "").strip()
    if not text and body.get("done_reason") == "stop":
        text = (message.get("thinking") or "").strip()
    if not text:
        return None
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


class _Rejected(RuntimeError):
    """The server refused the request outright."""


def _get(url: str, timeout: float, opener=None) -> dict:
    return _send(urllib.request.Request(url, method="GET"), timeout, opener)  # noqa: S310


def _post_to(url: str, payload: dict, timeout: float, opener=None) -> dict:
    """A POST with an explicit timeout. The preflight must not wait a model out."""
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _send(request, timeout, opener)


def _post(url: str, payload: dict, opener=None) -> dict:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        return _send(request, TIMEOUT_SECONDS, opener)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 500):
            # 500 as well as 400. A build that reports a context overflow as a
            # server error got no sizing retry at all, and the difference
            # between the two codes is a version, not a different problem.
            raise _Rejected(f"{exc} {_detail(exc)}") from exc
        raise


def _detail(exc: urllib.error.HTTPError) -> str:
    """What the server actually said.

    `str(HTTPError)` is the status line and the reason phrase — "HTTP Error 400:
    Bad Request" — and nothing else. Ollama reports WHY in the JSON body, so
    matching the overflow markers against the exception's text matched nothing
    a real server has ever sent, and the sizing retry could not fire outside the
    test suite.
    """
    try:
        return exc.read(_MAX_ERROR_BYTES).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a body that will not read is not worse than none
        return ""


#: An error body is a sentence. Anything past this is not a reason.
_MAX_ERROR_BYTES = 4096


def _send(request, timeout: float, opener=None) -> dict:
    """One round trip.

    `opener` is the seam tests inject at. There is no recorded-transport layer
    to install here, so it is a plain callable taking the request and the
    timeout — which is also what keeps the test suite from ever reaching a
    model on the machine it runs on.
    """
    if opener is not None:
        return opener(request, timeout)
    # Every transport failure has to become one kind of thing the caller
    # catches. A host that goes to sleep mid-request raises neither a connection
    # error nor a timeout, and an unmapped one escapes and fails the upload
    # rather than one receipt.
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(MAX_REPLY_BYTES + 1)
            if len(body) > MAX_REPLY_BYTES:
                raise ConnectionError(
                    f"the model at this host sent more than {MAX_REPLY_BYTES} bytes"
                )
            return json.loads(body.decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise ConnectionError(str(exc)) from exc
