"""The shape of an H Mart receipt, as it appears in a screen capture.

Kept separate from the adapter for the reason `kroger/reader.py` gives: this is
the part that is purely about the format, and the part that changes when the
retailer changes their point-of-sale software. It knows nothing about
disclosures, transactions or the database.

The store could only supply captures of its receipt viewer, one or two per
visit. A page reads:

    Customer ID: <smartcard>
    <flag>  <description>                       $ <amount>
    <quantity> lb @ <unit price> / lb
    <flag>  <description>                       $ <amount>
            TAX                                 $ <amount>
    ***     BALANCE                             $ <amount>
            <tender>                            $ <amount>
    <timestamp>  <lane>  <n>  <n>
    Card Number : …

Three things about that layout are load-bearing:

**A quantity line comes BEFORE the amount it qualifies.** `0.57 lb @ 1.49 / lb`
then a line at `$0.85`; 0.57 x 1.49 is 0.849. Read as trailing detail of the
line above it, every weighed item is attached to the wrong product.

**A discount and a cancellation are their own lines, and negative.** A weight
discount is flagged `WT` and a voided item `CL`, each rendered as a separate
negative line mirroring the positive one. They are kept as separate lines, the
way `kroger/adapter.py` keeps a return, and they are NOT folded into a price:
`models.py` has the long version of why a discount folded into `loyalty_amt` is
the worst available answer.

**The flag column is not read.** It is two glyphs of 10px type, and the engine
returns `WT` as `wr` and `***` as `week`, `nee`, `ANE` and `AK` across the real
corpus. Nothing here needs it — a line's meaning is carried by its description
and the sign of its amount, both of which read exactly — so reading it would be
buying a way to be wrong for no capability. It is recorded here rather than in
a comment nobody finds, because "why is the flag missing" is a fair question.

Nothing from the card block is read. It is on the page; it is not asked for,
not transcribed and not stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from unbagged.transcription import Box, Line, Transcript

__all__ = [
    "Receipt",
    "ReceiptLine",
    "Stamp",
    "capture_date",
    "foots",
    "from_reply",
    "group_by_visit",
    "read_capture",
    "stitch",
]

#: The header that starts a receipt. Also how a capture is known to be the
#: first of a pair rather than the second.
CUSTOMER = re.compile(r"Customer\s*ID\s*[:;]?\s*(\d[\d\s]*)", re.IGNORECASE)

#: `2.50 lb @ 1.50 / lb` and `3 @ 0.99`.
#:
#: The unit between the two numbers is matched as "not a digit and not an @"
#: rather than as a word. It is never read — the two numbers are what the line
#: is for — and it is the least legible thing on the line: `lb` comes back from
#: the engine as `Ib`, `1b` and `\b` depending on the capture. A pattern that
#: has to recognise the unit fails on the numbers beside it, for a token it was
#: going to discard.
QUANTITY = re.compile(
    r"^(?P<count>\d+(?:\.\d+)?)\s*[^\d@]{0,6}@\s*(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: `2019-03-04 11:07:00  2  118  0042`. The three trailing numbers are the lane,
#: a sequence and the receipt number; only the first and last are recorded, and
#: the middle one is left alone because nothing here knows what it counts.
STAMP = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})"
    r"\s+(?P<lane>\d{1,3})\s+(?P<sequence>\d{1,4})\s+(?P<number>\d{1,6})"
)

#: An amount, with whatever the engine made of the currency mark in front of it.
#: `§` is what it returns for `$` often enough to be worth naming.
AMOUNT = re.compile(r"^[$§s]?\s*(?P<value>-?\d+\.\d{2})\s*[$§]?$")

#: Lines that are the receipt's own furniture rather than a purchase.
TAX = re.compile(r"\bTAX\b", re.IGNORECASE)
BALANCE = re.compile(r"\bBAL[A-Z]*E\b", re.IGNORECASE)
CARD_BLOCK = re.compile(r"^\W*Card\b", re.IGNORECASE)


@dataclass(frozen=True)
class Stamp:
    """The line a receipt ends with. What joins a capture to a visit."""

    occurred_at: str
    lane: str
    number: str

    @property
    def date(self) -> str:
        return self.occurred_at[:10]


@dataclass(frozen=True)
class ReceiptLine:
    """One purchase, discount or cancellation.

    `quantity` and `unit_price` are None where the receipt stated neither. Not
    1 and not the amount: a receipt that does not say how many is not saying
    one, and filling it in would put a number on screen the retailer never
    disclosed.
    """

    description: str
    amount: Decimal
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    #: 1-based, within its own capture. The `locator` a record cites.
    row: int = 0


@dataclass(frozen=True)
class Receipt:
    """One visit, from one or more captures of it."""

    captures: tuple[str, ...]
    lines: tuple[ReceiptLine, ...]
    customer_id: str | None = None
    tax: Decimal | None = None
    balance: Decimal | None = None
    tender: str | None = None
    stamp: Stamp | None = None
    #: A capture with no balance on it is the top half of a taller receipt.
    complete: bool = False

    @property
    def subtotal(self) -> Decimal:
        """What the lines come to, before tax.

        The figure that reconciles against the points statement, which reports
        a visit's pre-tax total rather than what was paid.
        """
        return sum((line.amount for line in self.lines), Decimal("0.00"))


def read_capture(transcript: Transcript, capture: str) -> Receipt:
    """One capture, as far as it goes.

    A capture that ends before the balance is not an error — it is the top half
    of a receipt too tall for one screen, and `stitch` puts it back together.
    """
    description_x = _description_column(transcript)
    rows: list[_Row] = []
    customer = None
    stamp = None
    pending: tuple[Decimal, Decimal] | None = None

    for number, line in enumerate(transcript.lines, start=1):
        left = _left_of_money(line, transcript.money_column)
        if CARD_BLOCK.match(left.strip()):
            continue
        if stamp is None and (found := STAMP.search(left)):
            stamp = _stamp(found)
            continue
        if customer is None and (found := CUSTOMER.search(left)):
            customer = re.sub(r"\s+", "", found.group(1))
            continue

        amount = _amount_in(line, transcript.money_column)
        if amount is None:
            if found := QUANTITY.match(left.strip()):
                pending = _quantity(found)
            continue

        quantity, unit_price = pending or (None, None)
        pending = None
        rows.append(
            _Row(
                description=_description(line, transcript.money_column, description_x),
                amount=amount,
                quantity=quantity,
                unit_price=unit_price,
                row=number,
            )
        )

    items, tax, balance, tender = _settle(rows, complete=_reached_the_end(transcript))
    return Receipt(
        captures=(capture,),
        lines=items,
        customer_id=customer,
        tax=tax,
        balance=balance,
        tender=tender,
        stamp=stamp,
        complete=balance is not None,
    )


@dataclass(frozen=True)
class _Row:
    """An amount-bearing line, before it is known what kind of line it is."""

    description: str
    amount: Decimal
    quantity: Decimal | None
    unit_price: Decimal | None
    row: int


def _settle(
    rows: list[_Row], *, complete: bool
) -> tuple[tuple[ReceiptLine, ...], Decimal | None, Decimal | None, str | None]:
    """Split the amount lines into purchases, tax, the balance and the tender.

    By POSITION first and by what the line says second, which is the opposite
    of the obvious order and the only one that works. `BALANCE` comes back from
    the engine as `BALANGE`, `ANE wu`, `Serr` and `x` across the real corpus —
    it is set in the same 10px type as everything else and sits next to a `***`
    that smears into it — so a reader that needs to recognise the word fails on
    a seventh of the captures, and fails by finding no total at all rather than
    by finding a wrong one.

    The positions are fixed and the arithmetic checks them: the tender repeats
    the balance, so the last two equal amounts on a receipt are those two, and
    tax is the line above them. If that reading is wrong the receipt will not
    foot, and a receipt that does not foot is set aside rather than stored —
    so the cost of this being fooled is a quarantined visit, never a wrong one.
    """
    if not rows:
        return (), None, None, None
    if not complete:
        # The top half of a taller receipt: it runs off the bottom of the
        # screen mid-list, so every amount on it is a purchase and none of the
        # furniture below has been reached yet.
        #
        # Decided by whether the capture reached the END of the page rather
        # than by looking for a total, because the positional rule below cannot
        # tell a real balance from a coincidence: one real head capture ends on
        # two products that happen to cost the same, which reads exactly like a
        # balance and its tender echo. It was taken as a complete receipt, the
        # second half was never joined to it, and the visit split in two.
        return _purchases(rows), None, None, None

    tender = None
    balance = None
    body = rows
    if len(rows) >= 2 and rows[-1].amount == rows[-2].amount:
        tender = rows[-1].description or None
        balance = rows[-1].amount
        body = rows[:-2]
    elif BALANCE.search(rows[-1].description):
        balance = rows[-1].amount
        body = rows[:-1]

    tax = None
    if balance is not None and body:
        # The line above the balance, whatever the engine made of the word.
        tax = body[-1].amount
        body = body[:-1]

    return _purchases(body), tax, balance, tender


def _purchases(rows) -> tuple[ReceiptLine, ...]:
    return tuple(
        ReceiptLine(
            description=row.description,
            amount=row.amount,
            quantity=row.quantity,
            unit_price=row.unit_price,
            row=row.row,
        )
        for row in rows
    )


def _reached_the_end(transcript: Transcript) -> bool:
    """Did this capture run to the bottom of the receipt, or off the screen?

    Answered from where the last amount SITS, not from what is written near it.
    A finished receipt has a timestamp and usually a card block below its
    total; a capture that ran out of screen ends on a product line pressed
    against the bottom edge.

    Measured across the 46 real captures: the two that are the top half of a
    taller receipt leave 3 and 4 pixels below their last amount, and every one
    of the other 44 leaves between 44 and 207. There is no crowding around the
    line, so this is a physical fact about the capture rather than a threshold
    that had to be tuned.

    Deliberately not "is there a line below it", which is the same idea read
    through the engine: 22 of the 44 finished captures carry exactly one line
    down there, so a single unread timestamp would make a complete receipt look
    like half of one. It is kept as a second way to say yes, because a line
    being read below the total is also proof the total was not the last thing
    on the page.

    And deliberately not "is there a timestamp": the timestamp is the least
    legible text on the page at this size and reads cleanly off about two
    thirds of the captures. Requiring it declared a third of them incomplete.
    """
    if transcript.money_column is None:
        return False
    money = [line for line in transcript.lines if _has_amount(line, transcript.money_column)]
    if not money:
        return False
    last = max(line.bottom for line in money)
    room = _line_height(transcript)
    return (transcript.height - last) > room or any(line.top > last for line in transcript.lines)


def _line_height(transcript: Transcript) -> float:
    """The page's typical glyph height, so the test above scales with the type."""
    heights = sorted(word.height for line in transcript.lines for word in line.words)
    return heights[len(heights) // 2] if heights else 10.0


def _has_amount(line: Line, column: Box) -> bool:
    return any(AMOUNT.match(word.text.strip()) for word in line.within(column))


def _description_column(transcript: Transcript) -> int:
    """Where the description column starts, in this capture's own coordinates.

    Found rather than assumed, for the same reason the money column is: the
    captures differ in width, and a receipt viewer is not obliged to keep its
    margins across a software update.

    Taken as the most common left edge among the first words of lines that
    carry an amount — the product rows, which are the majority of any receipt
    and all set flush to the same stop. Anything left of it is the flag column.
    """
    starts: list[int] = []
    for line in transcript.lines:
        if transcript.money_column is None or not line.within(transcript.money_column):
            continue
        outside = [w for w in line.words if w.left + w.width / 2 < transcript.money_column.left]
        if outside:
            starts.append(
                max(w.left for w in outside if w.left <= min(o.left for o in outside) + 2)
            )
    if not starts:
        return 0
    return max(set(starts), key=starts.count)


def _description(line: Line, column: Box | None, description_x: int) -> str:
    """The description column alone, without the flag beside it.

    The flag — `WT` for a weight discount, `CL` for a cancellation, `***` for
    the balance — is deliberately dropped rather than read; the module
    docstring has the reasoning. Dropping it has to happen HERE, though: it
    sits on the same line, so "everything left of the money column" sweeps it
    into the description and puts `wr` on the front of a product name that
    never had one.
    """
    words = line.words if column is None else [w for w in line.words if not _in_money(w, column)]
    kept = [w.text for w in words if w.left + 2 >= description_x]
    return _clean(" ".join(kept))


def _in_money(word, column: Box) -> bool:
    return word.left + word.width / 2 >= column.left


def stitch(parts: list[Receipt]) -> Receipt:
    """Join the captures of one receipt, in the order they were taken.

    A tall receipt is captured in two passes of the same screen, and the second
    usually starts a line or two above where the first ended, so the seam
    carries a repeated run of lines.

    How long that run is cannot be decided by looking at it. A single repeated
    line is genuinely ambiguous: it is either the seam, or the same product
    scanned twice — both are ordinary, and picking wrong either invents a line
    or loses one a shopper paid for. Guessing costs real money in both
    directions.

    So it is not guessed. Every plausible overlap is tried and the one that
    makes the receipt reconcile against its own stated balance is taken. The
    receipt itself settles it, which is the same answer the rest of this path
    gives to every other question it cannot read its way out of.

    When none of them reconcile, the longest exact run is returned, so the
    caller has a coherent receipt to name in the warning it is about to raise.
    """
    if len(parts) == 1:
        return parts[0]

    longest = _joined(parts, greedy=True)
    if foots(longest) is None:
        return longest
    for candidate in _candidates(parts):
        if foots(candidate) is None:
            return candidate
    return longest


def _candidates(parts: list[Receipt]):
    """Every joining of `parts` worth checking, most-trimmed first."""
    seams = [_seam(parts[i].lines, parts[i + 1].lines) for i in range(len(parts) - 1)]
    for trim in range(max(seams, default=0), -1, -1):
        yield _joined(parts, cap=trim)


def _joined(parts: list[Receipt], *, greedy: bool = False, cap: int = 0) -> Receipt:
    joined = parts[0]
    for part in parts[1:]:
        overlap = _seam(joined.lines, part.lines) if greedy else min(cap, len(part.lines))
        joined = Receipt(
            captures=joined.captures + part.captures,
            lines=joined.lines + part.lines[overlap:],
            customer_id=joined.customer_id or part.customer_id,
            tax=part.tax if part.tax is not None else joined.tax,
            balance=part.balance if part.balance is not None else joined.balance,
            tender=part.tender or joined.tender,
            stamp=part.stamp or joined.stamp,
            complete=part.balance is not None or joined.complete,
        )
    return joined


def foots(receipt: Receipt) -> Decimal | None:
    """By how much the lines miss the stated balance, or None if they meet it.

    The gate the whole path turns on. A receipt states its own total, so a
    transcription of it can be checked against something the transcriber had no
    hand in — which is what makes reading these by machine defensible at all.

    Returns None for "this reconciles" and a signed difference otherwise. A
    receipt with no balance on it cannot be checked and is not claimed to
    reconcile: it returns its own subtotal, which is never None.
    """
    if receipt.balance is None:
        return receipt.subtotal
    difference = receipt.subtotal + (receipt.tax or Decimal("0.00")) - receipt.balance
    return None if difference == 0 else difference


def _seam(before: tuple[ReceiptLine, ...], after: tuple[ReceiptLine, ...]) -> int:
    """The longest exact run `after` begins with and `before` ends with.

    A candidate, not a verdict — `stitch` decides which overlap is real by
    whether the resulting receipt reconciles.
    """
    limit = min(len(before), len(after))
    for length in range(limit, 0, -1):
        tail = [(line.description, line.amount) for line in before[-length:]]
        head = [(line.description, line.amount) for line in after[:length]]
        if tail == head:
            return length
    return 0


def _left_of_money(line: Line, column: Box | None) -> str:
    """Everything on the line that is not in the money column."""
    if column is None:
        return line.text
    return " ".join(word.text for word in line.words if word.left + word.width / 2 < column.left)


def _amount_in(line: Line, column: Box | None) -> Decimal | None:
    """The line's amount, or None if it carries none.

    A currency mark read as its own word is skipped rather than failing the
    line: the engine splits `$ 12.48` into two words about half the time and
    into one the rest, and which it chose is not a fact about the receipt.
    """
    if column is None:
        return None
    for word in line.within(column):
        if found := AMOUNT.match(word.text.strip()):
            try:
                return Decimal(found.group("value"))
            except InvalidOperation:
                return None
    return None


def _quantity(found: re.Match[str]) -> tuple[Decimal, Decimal] | None:
    try:
        return Decimal(found.group("count")), Decimal(found.group("price"))
    except InvalidOperation:
        return None


def _stamp(found: re.Match[str]) -> Stamp | None:
    try:
        moment = datetime.strptime(  # noqa: DTZ007 - a wall clock, deliberately
            f"{found.group('date')} {found.group('time')}", "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        # A digit the engine got wrong, not a receipt that is wrong. The caller
        # has the capture's filename to fall back on, which carries the date too.
        return None
    return Stamp(
        occurred_at=moment.strftime("%Y-%m-%dT%H:%M:%S"),
        lane=found.group("lane"),
        number=found.group("number"),
    )


#: Marks the engine adds at the ends of a line, which a product name cannot
#: carry. NOT the full stop: these receipts truncate a long name and print the
#: cut, so `BLH B FRESH POTATO.` and `MRNG HI-CHEW GRN A.` end in one because
#: the receipt does. Stripping it would edit what the retailer printed.
LEADING_NOISE = "'`‘’\""
TRAILING_NOISE = ":,;"


def _clean(text: str) -> str:
    """Trim the punctuation the engine adds, and nothing the receipt printed.

    `'SC - MRN CHK BNLS` and `‘SUKOYAKA BRW RICE` are what it returns for a line
    starting with a tall letter, and a stray `:` at the end is what it makes of
    the gap before the amount column. Neither mark is on the receipt, so leaving
    them in puts characters in `description_raw` the retailer never printed —
    the never-mutate rule pointed the other way.

    It is not cosmetic. A name is what identifies a product where the retailer
    disclosed no code, so one stray character splits a product in two: the real
    response has `AVOCADO HASS` bought three times and `AVOCADO HASS:` bought
    twice, listed as two things on a page whose whole subject is what you buy
    repeatedly.

    Both lists are deliberately short. A mark that might be something the
    receipt printed is left alone — see `TRAILING_NOISE`.
    """
    return text.strip().lstrip(LEADING_NOISE).rstrip(TRAILING_NOISE).strip()


#: `Transaction_030419.png`, and `Transaction_030419_01.png` for the first of
#: two captures of one receipt. The six digits are the date as MMDDYY.
#:
#: The examples here and in NOTES.md are shaped like the real names and are
#: not any of them: a capture's filename encodes a date somebody shopped, and
#: CONTRIBUTING.md counts naming a trip as identifying whether or not the
#: basket comes with it.
#:
#: Matched loosely on the stem before it and anchored on the six digits,
#: because the name is whatever the store's software produced and whatever
#: travelled through mail on the way here, and nothing here should fail over a
#: prefix.
CAPTURE_NAME = re.compile(r"^(?P<visit>.*?_(?P<date>\d{6}))(?:_(?P<part>\d{1,2}))?$")


def capture_date(filename: str) -> str | None:
    """The date in a capture's filename, as `YYYY-MM-DD`, or None.

    The receipt prints its own timestamp, and that is the better source — but
    it is the least legible line on the page, and a digit of it comes back
    wrong on about a ninth of the real captures (`2022-62-21`, `2821-07-18`).
    The filename carries the same date in a form that cannot be misread, so the
    two corroborate each other.

    A two-digit year, which cannot be helped: it is what the name contains.
    Read as 20YY, which will be wrong in 2100 and is right for every response
    this will see.
    """
    found = CAPTURE_NAME.match(_stem(filename))
    if not found:
        return None
    month, day, year = (found.group("date")[i : i + 2] for i in (0, 2, 4))
    try:
        datetime.strptime(f"20{year}-{month}-{day}", "%Y-%m-%d")  # noqa: DTZ007
    except ValueError:
        return None
    return f"20{year}-{month}-{day}"


def group_by_visit(filenames: list[str]) -> list[list[str]]:
    """Captures grouped into the visits they are of, each in capture order.

    A grouping to TRY, not a conclusion. Two captures sharing a date are the
    two halves of one tall receipt about as often as they are two trips to the
    shop on one day — the real corpus has both, and their filenames look the
    same. Whether a group is really one receipt is settled by reading them:
    a capture that reached the end of a page is a receipt on its own.
    """
    visits: dict[str, list[str]] = {}
    for filename in sorted(filenames):
        found = CAPTURE_NAME.match(_stem(filename))
        visits.setdefault(found.group("visit") if found else _stem(filename), []).append(filename)
    return list(visits.values())


def _stem(filename: str) -> str:
    return filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def from_reply(reply: dict, like: Receipt) -> Receipt | None:
    """A receipt built from a model's answer, keeping what was read off the page.

    A candidate, and nothing more. The caller puts it through `foots` exactly as
    it does the engine's reading, and throws it away unless it holds — which is
    the whole basis for asking a model anything here. A model asked to read a
    number always returns one, confidently, and nothing in its reply separates a
    reading from an invention. The receipt's own printed total is the one fact in
    the room the model had no hand in.

    The timestamp, the customer number and the captures come from `like` rather
    than from the reply. They were read off the page by something deterministic
    and they are what the visit is joined on; a model is asked about the part
    that was genuinely unreadable, not invited to restate the rest.
    """
    lines = []
    for index, row in enumerate(reply.get("lines") or [], start=1):
        if not isinstance(row, dict):
            return None
        amount = _decimal(row.get("amount"))
        if amount is None:
            # One unreadable amount voids the answer rather than costing one
            # line. A basket missing a line still adds up if the model also
            # adjusted the total, and skipping quietly is how that gets stored.
            return None
        lines.append(
            ReceiptLine(
                description=_clean(str(row.get("description") or "")),
                amount=amount,
                row=index,
            )
        )
    tax = _decimal(reply.get("tax"))
    balance = _decimal(reply.get("balance"))
    if not lines or balance is None:
        return None
    return Receipt(
        captures=like.captures,
        lines=tuple(lines),
        customer_id=like.customer_id,
        tax=tax if tax is not None else Decimal("0.00"),
        balance=balance,
        tender=like.tender,
        stamp=like.stamp,
        complete=True,
    )


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip()).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, ValueError):
        return None
