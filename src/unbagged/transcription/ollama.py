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

DEFAULT_MODEL = "qwen2.5vl:7b"

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})

#: Seconds. A vision call on a page this size runs in tens of seconds on a
#: laptop GPU and minutes on a cold CPU; this is a hang, not a slow answer.
TIMEOUT_SECONDS = 180
PREFLIGHT_TIMEOUT_SECONDS = 10

#: Context the encoded image spends, which a prompt measured in characters
#: cannot see. Over-estimated on purpose: too large costs memory, too small
#: gets the prompt rejected outright with nothing to read in the reply.
IMAGE_TOKENS = 3072
NUM_CTX_FLOOR = 8192

#: Flat on purpose. Quantised models in this size class return empty arrays at
#: intermediate levels of a nested schema, so the whole receipt is one object
#: with one list in it.
RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["description", "amount"],
            },
        },
        "tax": {"type": "string"},
        "balance": {"type": "string"},
    },
    "required": ["lines", "tax", "balance"],
}

PROMPT = (
    "This is a screen capture of a supermarket receipt. Transcribe every "
    "purchase line exactly as printed: the product description in the middle "
    "column and its amount in the right column. Keep a minus sign where one is "
    "printed — a negative line is a discount or a cancelled item and it matters. "
    "Do not merge lines. Do not include the TAX line or the BALANCE line or the "
    "payment method among the purchases; report the tax and the balance in "
    "their own fields. Report amounts as decimal numbers with two places and no "
    "currency symbol. Ignore anything written over the receipt by hand, and "
    "ignore the card details at the bottom entirely."
)


class Reachability(Enum):
    OFF = "off"  # not configured; the deterministic reader is on its own
    OK = "ok"
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
    return Availability(Reachability.OK, "", host, model)


def _present(model: str, names: set[str]) -> bool:
    """Is this model pulled?

    Tolerant of the two ways people write a tag: `qwen2.5vl` for a pulled
    `qwen2.5vl:7b`, and a bare name for an implicit `:latest`.
    """
    if model in names or (model if ":" in model else f"{model}:latest") in names:
        return True
    return any(name.split(":", 1)[0] == model for name in names)


def read_receipt(pages: list[bytes], where: Availability, opener=None) -> dict | None:
    """Ask the model what is on this page. None if it will not say usefully.

    Returns the raw reply. It is a claim, not a reading: the caller has to put
    it through the same arithmetic the deterministic transcriber's answer goes
    through, and throw it away if it does not hold.
    """
    if not where.usable or where.host is None:
        return None
    payload = {
        "model": where.model,
        "messages": [
            {
                "role": "user",
                "content": PROMPT,
                # Every capture of one receipt in one call, so a model asked
                # about a receipt split across two screens sees both halves and
                # can give one answer for the whole of it.
                "images": [base64.b64encode(page).decode("ascii") for page in pages],
            }
        ],
        "stream": False,
        # The full schema, not the string "json". Constrained decoding is what
        # keeps a small model from answering in prose about the receipt.
        "format": RECEIPT_SCHEMA,
        "options": {
            "temperature": 0,
            "num_ctx": NUM_CTX_FLOOR + IMAGE_TOKENS * len(pages),
        },
        # Suppresses a reasoning channel where it is honoured. Builds that
        # predate the field reject the whole request, so it is dropped and
        # retried once rather than losing the call to a version difference.
        "think": False,
    }
    try:
        body = _post(f"{where.host.rstrip('/')}/api/chat", payload, opener)
    except _Rejected:
        payload.pop("think")
        try:
            body = _post(f"{where.host.rstrip('/')}/api/chat", payload, opener)
        except Exception:  # noqa: BLE001 - a model that will not answer is not a failed upload
            return None
    except Exception:  # noqa: BLE001
        log.debug("vision read failed", exc_info=True)
        return None

    return _content(body)


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
        if exc.code == 400:
            raise _Rejected(str(exc)) from exc
        raise


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
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise ConnectionError(str(exc)) from exc
