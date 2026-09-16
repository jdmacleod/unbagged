"""Preparing a capture for the OCR engine, and assembling what comes back.

Everything here is about pixels and geometry. Nothing here knows what a
receipt is; `adapters/hmart/receipt.py` is where that lives.

Pillow rather than a new dependency: it is already in the shipped image,
hash-pinned, because pdfplumber brings it.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from unbagged.transcription.words import Word

log = logging.getLogger(__name__)

#: Colours drawn OVER a capture rather than by it, masked back to the paper
#: before the engine sees the page.
#:
#: A dozen of the 46 real H Mart captures carry a freehand scrawl across them.
#: It is exactly `#0000FF` — pure blue — while the page itself is drawn in
#: `#002D8C` navy, `#B55D00` ochre and black, so the scrawl shares no colour
#: with any glyph and removing it costs nothing that was ever text. Measured on
#: one capture: fourteen lines short of its stated total before the mask, and
#: every line reading exactly to that total after it.

#:
#: An EXACT match, not a near one. A tolerance wide enough to be worth having
#: starts eating the navy the descriptions are set in.
OVERLAY_COLOURS: tuple[tuple[int, int, int], ...] = ((0, 0, 255),)

PAPER = (255, 255, 255)

#: The most pixels a capture may decode to.
#:
#: A screen capture of a receipt is around 550x2000 — roughly a megapixel. The
#: upload cap is 64 MB of BYTES (`api.py`), which says nothing about what those
#: bytes decode to: a solid-colour PNG under a megabyte expands to hundreds of
#: megapixels, and `convert("RGB")` then `mask_overlay`'s several full-size
#: buffers put peak allocation in the gigabytes. Pillow's own default only warns
#: below twice its limit, and an OOM kill of the container is not catchable.
#:
#: Twenty megapixels is twenty times the real thing and far under the cliff.
MAX_PIXELS = 20_000_000

#: The engine reads small type better with more pixels under it. 3x was the
#: smallest factor that read the amount column exactly on every real capture;
#: beyond it accuracy is flat and the subprocess gets slower.
UPSCALE = 3

#: Two words belong to the same line when their vertical centres are within
#: this fraction of the page's TYPICAL glyph height.
#:
#: Of the typical height, not of the taller of the two words being compared.
#: A `/` on these captures is 29 pixels tall against a typical 10, and the
#: lines are 20 apart — so a tolerance scaled to the tallest word in a group
#: reaches past the next line and merges the two. That merge is quiet and
#: expensive: the TAX row and the BALANCE row became one line carrying two
#: amounts, which then sorted by horizontal position into an order neither of
#: them was ever in.
LINE_TOLERANCE = 0.8


@dataclass(frozen=True)
class Box:
    """A rectangle in image coordinates, left-top-right-bottom."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class Line:
    """Words sharing a horizontal band, in reading order."""

    words: tuple[Word, ...]

    @property
    def top(self) -> int:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> int:
        return max(w.bottom for w in self.words)

    @property
    def middle(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def within(self, box: Box) -> tuple[Word, ...]:
        """The words of this line whose centre sits inside `box` horizontally."""
        return tuple(w for w in self.words if box.left <= w.left + w.width / 2 <= box.right)


def load(data: bytes):
    """One capture, as RGB, with any overlay masked back to paper.

    Returns a Pillow image. Imported here rather than at module scope so that
    importing this package does not pull an image library into a process that
    is only ever going to read a spreadsheet.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    pixels = image.size[0] * image.size[1]
    if pixels > MAX_PIXELS:
        # Checked BEFORE `load()`, which is what actually decodes: the point is
        # to refuse the allocation, not to survive it.
        raise ValueError(
            f"image is {pixels // 1_000_000} megapixels, over the "
            f"{MAX_PIXELS // 1_000_000} this reads"
        )
    image.load()
    image = image.convert("RGB")
    return mask_overlay(image)


def mask_overlay(image):
    """Paint every overlay colour back to paper.

    Done with Pillow's own point operation per channel rather than a pixel
    loop: the loop is ~0.5s on a capture this size and this is ~2ms, and the
    46 of them are read inside a request a person is waiting on.
    """
    from PIL import Image

    for colour in OVERLAY_COLOURS:
        r, g, b = (
            channel.point(lambda v, c=want: 255 if v == c else 0)
            for channel, want in zip(image.split(), colour, strict=True)
        )
        hit = Image.merge("RGB", (r, g, b)).convert("L").point(lambda v: 255 if v == 255 else 0)
        image.paste(PAPER, mask=hit.convert("1"))
    return image


def to_png(image, *, box: Box | None = None, upscale: int = 1) -> bytes:
    """A region of the image, greyscaled and enlarged, as PNG bytes.

    Greyscale before enlarging, not after: the engine thresholds on luminance,
    and enlarging in colour first interpolates the ochre an amount is set in
    towards the paper around it.
    """
    from PIL import Image

    region = image.crop((box.left, box.top, box.right, box.bottom)) if box else image
    region = region.convert("L")
    if upscale > 1:
        region = region.resize((region.width * upscale, region.height * upscale), Image.LANCZOS)
    buffer = io.BytesIO()
    region.save(buffer, format="PNG")
    return buffer.getvalue()


def assemble(words: Iterable[Word]) -> tuple[Line, ...]:
    """Group words into lines by vertical position, top to bottom.

    Clustered on the vertical CENTRE rather than the top edge: a line holding
    both a tall glyph and a short one has two different tops and one middle,
    and grouping on the top splits it in two.

    Compared against the running MEDIAN centre of the group rather than its
    last or tallest member, and with a tolerance taken from the page's typical
    glyph height rather than from either word — see `LINE_TOLERANCE`. Both are
    the same defence: one outsized glyph must not be able to decide where a
    line ends.
    """
    ordered = sorted(words, key=lambda w: (w.middle, w.left))
    if not ordered:
        return ()
    tolerance = _typical_height(ordered) * LINE_TOLERANCE
    lines: list[list[Word]] = []
    for word in ordered:
        if lines and abs(word.middle - _median_middle(lines[-1])) <= tolerance:
            lines[-1].append(word)
            continue
        lines.append([word])
    return tuple(Line(tuple(sorted(group, key=lambda w: w.left))) for group in lines)


def _typical_height(words: Sequence[Word]) -> float:
    """The median word height on the page. The scale everything else is in."""
    heights = sorted(word.height for word in words)
    return heights[len(heights) // 2] or 1.0


def _median_middle(group: Sequence[Word]) -> float:
    middles = sorted(word.middle for word in group)
    return middles[len(middles) // 2]


def rightmost_column(lines: Sequence[Line], *, width: int) -> Box | None:
    """Where the right-aligned column of a table-shaped page sits.

    Found rather than assumed. The captures this was built for vary from 519
    to 542 pixels wide, so a crop measured off one of them reads NOTHING on
    another — the column is defined by its relationship to the right edge of
    the page, not by an offset from the left.

    The column is taken as everything right of the leftmost word that ends
    near the right margin, with a margin's slack, so a long amount and a short
    one are both inside it.
    """
    trailing = [
        word for line in lines for word in line.words if word.right >= width - _margin(width)
    ]
    if not trailing:
        return None
    # Padded by a whole margin, not a pixel or two. A right-aligned amount may
    # be tokenised as one word (`$12.48`) or two (`$` then `12.48`), and only
    # the number ends near the right edge — so the leftmost trailing word marks
    # where the NUMBERS start, and a currency mark sits to the left of that.
    left = min(word.left for word in trailing) - _margin(width)
    return Box(left=max(0, left), top=0, right=width, bottom=max(line.bottom for line in lines) + 4)


def _margin(width: int) -> int:
    """How close to the right edge a word has to end to count as right-aligned.

    A proportion rather than a constant, so it holds across capture widths.
    """
    return max(8, width // 20)
