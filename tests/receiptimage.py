"""A receipt-shaped page, drawn rather than committed.

The same reasoning as `minipdf.py`: committing real captures is not an option,
and the thing under test is a reader, so what it needs is input with the
*shape* of the real thing rather than the real thing itself.

Deliberately not a copy of any real capture. What it reproduces is the
geometry the reader depends on — a description column at a fixed left edge, a
right-aligned amount column, and a line pitch wide enough to tell rows apart —
plus, on request, the two things that broke the reader when it met the real
corpus: an outsized glyph, and a freehand scrawl drawn across the page.
"""

from __future__ import annotations

import io

#: Matches the real captures closely enough that a tolerance tuned on one
#: works on the other, and is not any of their widths.
PAGE_WIDTH = 525
LINE_PITCH = 21
TOP_MARGIN = 18
DESCRIPTION_X = 104
RIGHT_MARGIN = 13

#: The colours a real capture is drawn in. The scrawl is pure blue and shares
#: none of them, which is the whole reason it can be masked away.
INK = (0, 45, 140)
MONEY_INK = (181, 93, 0)
BLACK = (0, 0, 0)
SCRAWL = (0, 0, 255)


def build_receipt(rows, *, width=PAGE_WIDTH, scrawl=False, clip_digits=0, cut_off=False) -> bytes:
    """A page of `(left, description, amount)` rows, as PNG bytes.

    `left` is the flag column — `WT`, `CL`, `***`, or a qualifier like
    `2.50 lb @ 1.50 / lb`. `amount` may be None for a row that carries none.

    `clip_digits` trims that many digit-widths off the right edge AFTER drawing,
    which is what one real capture did to itself: every amount lost its last
    digit, so the page is legible, plausible and unreadable. Expressed in digits
    rather than pixels so it stays a description of the damage — "the last digit
    is gone" — and not a number tuned until a test went green.
    """
    from PIL import Image, ImageDraw, ImageFont

    # Pillow's own scalable default, so the page needs no font file and reads
    # the same on a contributor's machine and in CI. At the bitmap default size
    # the engine reads the amounts exactly and the descriptions barely at all,
    # which would make every test here a test of the amount column only.
    font = ImageFont.load_default(size=14)

    # `cut_off` ends the page flush against its last row, which is what the
    # screen does to a receipt too tall to fit: the capture stops mid-list with
    # no margin under it. That edge is the only thing telling the top half of a
    # receipt apart from a whole one, so a builder without it cannot produce
    # the input the reader has to handle.
    height = LINE_PITCH * len(rows) + (TOP_MARGIN if cut_off else TOP_MARGIN * 2)
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    for index, (left, description, amount) in enumerate(rows):
        y = TOP_MARGIN + index * LINE_PITCH
        if left:
            draw.text((8, y), left, fill=BLACK, font=font)
        if description:
            draw.text((DESCRIPTION_X, y), description, fill=INK, font=font)
        if amount is not None:
            text = f"$ {amount}"
            span = draw.textlength(text, font=font)
            draw.text((width - RIGHT_MARGIN - span, y), text, fill=MONEY_INK, font=font)

    if scrawl:
        # Several strokes, at the angle and weight of the real thing, crossing
        # both columns so that masking has to be what recovers them.
        for offset in range(0, width, 60):
            draw.line(
                [(offset, height), (offset + height // 2, 0)],
                fill=SCRAWL,
                width=1,
            )

    if clip_digits:
        lost = round(draw.textlength("0", font=font) * clip_digits) + RIGHT_MARGIN
        image = image.crop((0, 0, width - lost, height))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
