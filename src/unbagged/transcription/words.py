"""Words and their positions on an image, from the OCR engine.

This is the whole of what the engine is asked for. Everything above it —
which words form a line, which line is a total, whether the total is right —
is the caller's, because those are questions about a document rather than
about pixels.

The engine is driven as a subprocess rather than through a binding. The
binding in wide use is a thin wrapper around the same subprocess call, and a
runtime dependency that exists to build an argv is a dependency that has to
be locked, hashed, audited and upgraded for no behaviour of its own.
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: The OCR engine's own name. Not configurable: a second engine would read
#: differently, and a transcript is cached under the name of what produced it.
ENGINE = "tesseract"

#: Page segmentation mode 6 — "a single uniform block of text".
#:
#: Not 4 ("a single column of text of variable sizes") and not 11 ("sparse
#: text"), both of which were measured on the same captures: 11 splits every
#: line into its own paragraph and loses the y-ordering that groups a line,
#: and 4 finds column breaks inside a description and hyphenates across them.
PSM = "6"

#: What a number may be made of. Handed to the engine when re-reading a column
#: known to hold money, and the single highest-value setting in this module.
#:
#: Unrestricted, the engine drops the minus sign on a negative amount often
#: enough to matter: measured across 46 real captures, `$-3.68` came back as
#: `$3.68` and `$-3.71` as `$371` — a discount silently becoming a charge, and
#: a decimal point silently becoming a factor of a hundred. Restricted, every
#: amount on every one of those captures came back exact.
MONEY_ALPHABET = "0123456789.$- "

#: Seconds. A page of this size reads in well under one; anything approaching
#: this is a hung subprocess, not a slow one.
TIMEOUT_SECONDS = 30


class OcrUnavailable(RuntimeError):
    """The OCR engine is not installed, or would not run."""


@dataclass(frozen=True)
class Word:
    """One word, where it sat, and how sure the engine was of it.

    `confidence` is the engine's own 0-100 score. It is not trusted as a
    verdict anywhere — the arithmetic is what decides whether a reading is
    kept — but it is a good signal for which reading to report when one has
    to be blamed, and the engine scores its own misreads low: on the capture
    where `$-3.68` came back as `$3.68`, the confidence on that word was 2.
    """

    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def middle(self) -> float:
        """Vertical centre. What lines are clustered on."""
        return self.top + self.height / 2

    def shifted(self, dx: int, dy: int) -> Word:
        """The same word in the parent image's coordinates.

        A word read out of a crop knows only where it sat in the crop. Without
        this, a re-read column's words cannot be matched back to the lines they
        belong to.
        """
        from dataclasses import replace

        return replace(self, left=self.left + dx, top=self.top + dy)


def available() -> bool:
    """Is the engine installed? Cheap, and never raises."""
    return shutil.which(ENGINE) is not None


def version() -> str | None:
    """The engine's version string, or None if it will not run."""
    if not available():
        return None
    try:
        out = subprocess.run(  # noqa: S603 - argv is built here, never from input
            [ENGINE, "--version"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.stdout.splitlines()[0] if out.stdout else ""
    return first.strip() or None


def read(png: bytes, *, alphabet: str | None = None) -> tuple[Word, ...]:
    """Every word the engine finds in one PNG, with its box.

    `alphabet` restricts what a character may be read as. Use it wherever the
    content is known to be of one kind — see `MONEY_ALPHABET` — and leave it
    off where it is not, because a restriction that is wrong turns a word the
    engine could have read into one it cannot.
    """
    if not available():
        raise OcrUnavailable(
            f"{ENGINE} is not installed, so an image cannot be read. "
            "Install it with `brew install tesseract` or "
            "`apt-get install tesseract-ocr`."
        )
    argv = [ENGINE, "stdin", "stdout", "--psm", PSM, "tsv"]
    if alphabet:
        argv += ["-c", f"tessedit_char_whitelist={alphabet}"]
    try:
        out = subprocess.run(  # noqa: S603 - argv is built here, never from input
            argv,
            input=png,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise OcrUnavailable(f"{ENGINE} failed to read an image: {stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrUnavailable(
            f"{ENGINE} did not finish within {TIMEOUT_SECONDS}s on an image"
        ) from exc
    except OSError as exc:
        raise OcrUnavailable(f"{ENGINE} could not be run: {exc}") from exc
    return _parse_tsv(out.stdout.decode("utf-8", "replace"))


def _parse_tsv(text: str) -> tuple[Word, ...]:
    """The engine's TSV, minus the rows that are not words.

    The engine emits a row per page, block, paragraph and line as well as per
    word; only the last carries text. `QUOTE_NONE` because a word may legally
    be a bare double quote, which `csv` would otherwise take as an opening
    quote and swallow the rest of the file into.
    """
    words = []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        content = (row.get("text") or "").strip()
        if not content:
            continue
        try:
            words.append(
                Word(
                    text=content,
                    left=int(row["left"]),
                    top=int(row["top"]),
                    width=int(row["width"]),
                    height=int(row["height"]),
                    confidence=float(row["conf"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            # A malformed row costs that word, never the page. The engine has
            # been seen to emit a short row at the end of its output.
            log.debug("skipping unreadable TSV row: %r", row)
    return tuple(words)
