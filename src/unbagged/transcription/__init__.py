"""Turning a capture of a document into lines of text it can be read from.

`extraction.py` owns bytes to text. This owns pixels to text, and the split is
the same one the rest of the project draws: a reader that knows about a format
never knows about a retailer. Nothing here knows what a receipt is.

The one thing worth understanding before changing anything: **a page is read
twice.** Once unrestricted, for the words and where they sit, and once more
over the column that holds money with the character set restricted to what a
number may be made of. The second pass exists because the first drops minus
signs — see `words.MONEY_ALPHABET` — and a discount read as a charge is the
kind of wrong this project cannot ship.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from unbagged.transcription.image import (
    UPSCALE,
    Box,
    Line,
    assemble,
    load,
    rightmost_column,
    to_png,
)
from unbagged.transcription.words import (
    ENGINE,
    MONEY_ALPHABET,
    OcrUnavailable,
    Word,
    available,
    read,
    version,
)

log = logging.getLogger(__name__)

__all__ = [
    "ENGINE",
    "Box",
    "Line",
    "OcrUnavailable",
    "Transcript",
    "UnreadableImage",
    "Word",
    "available",
    "transcribe",
    "version",
]


class UnreadableImage(ValueError):
    """These bytes are not an image this can read.

    Kept apart from `OcrUnavailable`, which is a fact about the MACHINE — no
    engine installed — and applies to every capture in the upload at once. This
    is a fact about ONE file, and the difference decides how far the damage
    spreads: a truncated capture must cost that capture and nothing else.

    Before this existed, Pillow's `OSError: image file is truncated` escaped
    `parse()`, `ingest` rewrapped it as an adapter bug, and one damaged file
    among forty-six lost the entire response.
    """


@dataclass(frozen=True)
class Transcript:
    """One capture, read.

    `lines` are in reading order. `money_column` is where the right-aligned
    numeric column was found, or None on a page that has no such column — a
    caller that needs one must check rather than assume, because "this capture
    is not the shape I expected" is a finding, not a crash.
    """

    lines: tuple[Line, ...]
    width: int
    height: int
    money_column: Box | None
    engine: str
    engine_version: str | None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def transcribe(data: bytes) -> Transcript:
    """Read one capture. Raises `OcrUnavailable` if the engine will not run."""
    try:
        image = load(data)
    except OcrUnavailable:
        raise
    except Exception as exc:
        # Pillow raises OSError, ValueError, and its own DecompressionBombError
        # across the shapes a damaged or hostile file takes, and the list grows
        # with the library. What matters to the caller is the same either way:
        # this one file cannot be read.
        raise UnreadableImage(str(exc)) from exc

    # The WHOLE pixel pipeline, not just the decode. `to_png` and
    # `_reread_money` call `convert`, `resize` and `save`, every one of which
    # raises on its own — and anything escaping here is rewrapped by `ingest` as
    # an adapter bug, which is how one damaged file among forty-six used to lose
    # the entire response. Guarding only `load()` left that hole open for every
    # failure after the first byte was read.
    try:
        page = to_png(image)
        lines = assemble(read(page))
        if not lines:
            return Transcript((), image.width, image.height, None, ENGINE, version())
        column = rightmost_column(lines, width=image.width)
        if column is not None:
            lines = _reread_money(image, lines, column, upscale=UPSCALE)
    except (OcrUnavailable, UnreadableImage):
        raise
    except Exception as exc:
        raise UnreadableImage(str(exc)) from exc
    return Transcript(lines, image.width, image.height, column, ENGINE, version())


def _reread_money(image, lines: tuple[Line, ...], column: Box, *, upscale: int) -> tuple[Line, ...]:
    """Replace each line's amount with a reading taken under the number alphabet.

    Matched back to lines by vertical overlap rather than by order. Order is
    the tempting join and it is wrong: the restricted pass may find one fewer
    line than the unrestricted one — a row holding only a description has no
    number to find — and a single missing entry shifts every amount below it
    onto the wrong line, which is a basket that still foots and is entirely
    fiction.
    """
    strip = to_png(image, box=column, upscale=upscale)
    found = read(strip, alphabet=MONEY_ALPHABET)
    # Back into the parent's coordinates: the crop's own origin, then the
    # enlargement the engine was given.
    rescaled = tuple(
        replace(
            word,
            left=column.left + word.left // upscale,
            top=column.top + word.top // upscale,
            width=max(1, word.width // upscale),
            height=max(1, word.height // upscale),
        )
        for word in found
    )

    # Each amount belongs to exactly ONE line, the one whose body it sits
    # nearest. Assignment by box overlap was the first attempt and it
    # double-counted: a line carrying a tall glyph — the `/` of `2.50 lb @
    # 1.50 / lb` — has a box half again the height of its neighbours, and it
    # swallowed the amount belonging to the line below. The basket then footed
    # 0.78 over with no line visibly wrong. Nearest-centre is one-to-one by
    # construction, so no amount can be spent twice.
    bodies = [_body(line) for line in lines]
    claimed: dict[int, list[Word]] = {}
    for word in rescaled:
        nearest = min(range(len(lines)), key=lambda i: abs(word.middle - bodies[i]))
        claimed.setdefault(nearest, []).append(word)

    out = []
    for index, line in enumerate(lines):
        kept = tuple(w for w in line.words if not _in_column(w, column))
        amounts = tuple(sorted(claimed.get(index, ()), key=lambda w: w.left))
        out.append(Line(kept + amounts) if (kept or amounts) else line)
    return tuple(out)


def _body(line: Line) -> float:
    """A line's vertical centre, resistant to one oversized glyph.

    The median of its words' centres rather than the middle of its bounding
    box: a `/` or a `$` is taller than the letters beside it, and one of them
    is enough to move a box-derived centre far enough to claim a neighbour's
    amount.
    """
    middles = sorted(word.middle for word in line.words)
    return middles[len(middles) // 2]


def _in_column(word: Word, column: Box) -> bool:
    return word.left + word.width / 2 >= column.left
