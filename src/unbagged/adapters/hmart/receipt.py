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
from dataclasses import dataclass, replace
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
    "split_into_receipts",
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
AMOUNT = re.compile(r"^[$§s]?\s*(?P<value>-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2})\s*[$§]?$")

#: Lines that are the receipt's own furniture rather than a purchase.
TAX = re.compile(r"\bTAX\b", re.IGNORECASE)
#: Bounded rather than `[A-Z]*`, which backtracks quadratically over a long
#: uppercase run with no closing E — and a description now reaches it from a
#: model's answer.
BALANCE = re.compile(r"\bBAL[A-Z]{0,8}E\b", re.IGNORECASE)

CARD_BLOCK = re.compile(r"^\W*Card\b", re.IGNORECASE)
#: How the receipt names what paid. Only used to recognise the tender line in a
#: MODEL's answer — the engine's reading finds it by position, because the last
#: two equal amounts on a page are the balance and its echo whatever they say.
TENDER = re.compile(r"\b(CREDIT|DEBIT|CASH|CHECK|EBT|GIFT)\b", re.IGNORECASE)


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
    #: True when the tax line was identified by POSITION alone — the line above
    #: the balance, whatever it turned out to say. See `with_tax_as_item`.
    tax_inferred: bool = False
    #: What that line actually said, kept so that restoring it as a purchase
    #: restores the name the retailer printed on it too.
    tax_description: str = ""
    #: True when the amount column runs into the right edge of the capture, so
    #: the last digit of every amount on the page is cut through. See `_clipped`.
    clipped: bool = False

    def with_tax_as_item(self) -> Receipt:
        """The same receipt, read with the tax line taken as a purchase.

        **`foots()` cannot tell these two readings apart.** It adds tax back, so
        `sum(items) + tax` is the same number either way — which is exactly how a
        receipt with no TAX line on it loses its last purchase and still
        reconciles perfectly. Measured on the real corpus: the TAX word reads off
        only 34 of 46 captures, and on at least one the line above the balance is
        a product.

        Only a figure from OUTSIDE the receipt can choose between them, and the
        points statement is one: it reports the pre-tax subtotal, so it agrees
        with exactly one of the two readings. The adapter does the choosing.
        """
        if self.tax is None:
            return self
        # With the description the line carried. It was read off the page like
        # any other; dropping it would put the purchase back in the basket as a
        # blank row and leave it out of Products and Prices, which key on a
        # name. The retailer printed one.
        restored = ReceiptLine(description=self.tax_description, amount=self.tax, row=0)
        return replace(
            self,
            lines=self.lines + (restored,),
            tax=Decimal("0.00"),
            tax_inferred=False,
            tax_description="",
        )

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

    items, tax, balance, tender, inferred, taxed = _settle(
        rows, complete=_reached_the_end(transcript)
    )

    return Receipt(
        captures=(capture,),
        lines=items,
        customer_id=customer,
        tax=tax,
        balance=balance,
        tender=tender,
        stamp=stamp,
        complete=balance is not None,
        tax_inferred=inferred,
        tax_description=taxed,
        clipped=_clipped(transcript),
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
) -> tuple[tuple[ReceiptLine, ...], Decimal | None, Decimal | None, str | None, bool, str]:
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
        return (), None, None, None, False, ""

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
        return _purchases(rows), None, None, None, False, ""

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
    tax_inferred = False
    tax_description = ""
    if balance is not None and body:
        # The line above the balance. Whether the engine could READ the word is
        # recorded, because the two readings are indistinguishable to `foots()`
        # and something outside the receipt has to choose — see
        # `Receipt.with_tax_as_item`.
        tax = body[-1].amount
        tax_inferred = not TAX.search(body[-1].description)
        tax_description = body[-1].description
        body = body[:-1]

    return _purchases(body), tax, balance, tender, tax_inferred, tax_description


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


#: How close an amount may sit to the right edge before the page is suspected of
#: having been cut through its own digits. Measured across the real corpus: the
#: one capture clipped at the source leaves 0px, and the next-tightest of the
#: other 45 leaves 4px, rising to 24px. There is nothing between 0 and 4, so
#: this is a physical fact about the capture rather than a tuned threshold.
CLIP_MARGIN = 2


def _clipped(transcript: Transcript) -> bool:
    """Does the amount column run into the right edge of the page?

    Worth knowing because of how the failure presents. A clip does not blank
    the last digit, it cuts through it, and the engine reads the surviving part
    as SOME digit — so an amount comes back altered in its last place, with
    nothing marking it as changed. Five amounts on one page were corrupted that
    way. Where the fragment resolves to no digit at all the amount stops
    matching the shape of one and the row is dropped whole, which on that page
    took the BALANCE line with it.


    So the page fails in two ways at once and neither says what happened: some
    amounts are quietly wrong, and the total that would have caught them is
    gone. The margin is the only honest signal, and it is a fact about pixels
    rather than about anything the engine believed.
    """
    if transcript.money_column is None:
        return False
    # AMOUNTS, not every word that lands in the column. The column is located
    # with a margin of left padding, so it also catches the trailing number
    # group on the stamp line, the tender's wording and the card block — any of
    # which can touch the edge of a page whose amounts are nowhere near it. The
    # calibration below was measured on amount ink; measuring anything else
    # made the threshold uncalibratable and told readers a healthy capture was
    # cut off.
    right = max(
        (
            word.right
            for line in transcript.lines
            if not _furniture_line(line, transcript.money_column)
            for word in line.within(transcript.money_column)
            if any(character.isdigit() for character in word.text)
        ),
        default=None,
    )
    return right is not None and (transcript.width - right) < CLIP_MARGIN


def _furniture_line(line: Line, column: Box) -> bool:
    """The stamp and the card block, which `read_capture` also steps over.

    They are the reason this cannot simply take the rightmost word in the
    column: the stamp ends in a run of digits and the card block in a masked
    number, either of which can sit against the edge of a page whose amounts
    are well clear of it.

    Any word carrying a digit counts on the lines that remain, rather than only
    a well-formed amount. A clip severe enough to stop every amount parsing is
    the case this flag exists for, and requiring a parse would switch it off
    exactly there.
    """
    left = _left_of_money(line, column).strip()
    # The same three `read_capture` steps over. The customer line is here for
    # the same reason as the other two: it ends in a long run of digits, and a
    # long enough one reaches the edge of a page whose amounts do not.
    return bool(STAMP.search(left) or CARD_BLOCK.match(left) or CUSTOMER.search(left))


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
            # Hoisted: the inner bound does not depend on the word being tested,
            # and recomputing it per candidate made this quadratic in the words
            # left of the money column for no reason.
            leftmost = min(word.left for word in outside)
            starts.append(max(w.left for w in outside if w.left <= leftmost + 2))
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


#: How many joinings of one receipt's captures are worth enumerating.
MAX_JOINS = 4096


def _candidates(parts: list[Receipt]):
    """Every joining of `parts` worth checking.

    One trim PER SEAM, not one cap across all of them. Applying a single cap
    everywhere was wrong in a way that only shows with three parts: with real
    overlaps of 2 and 0, every cap either dropped lines or duplicated them, and
    the correct join was produced by no candidate at all. The greedy pass found
    it — but greedy is exactly what fails when one transcribed description
    differs by a character at the true seam, which is the case this search
    exists for.

    Ordered MOST-trims-first, which reverses what this did a commit ago. Every
    trim offered here is at most the longest exact run of (description, amount)
    across the seam, so each one is an overlap that was measured rather than
    guessed — and between two joins that both reconcile, the one that treats a
    measured run as the seam beats the one that treats it as a coincidence.

    Fewest-first broke on the case it was written for. A product and its `CL`
    cancellation straddling a seam sum to zero, so trimming nothing adds up
    exactly as well as trimming the real overlap — and sorted first, so two
    lines the shopper was never charged for were stored, one of which the
    product index counts as a second purchase. Ordering cannot lose a real
    line to this: a trim that discards something genuinely bought changes the
    sum, and a join whose sum is wrong never reconciles at all.


    Bounded, because the seam count comes from how many files were uploaded and
    the space is a product over them. Six parts with 8-line seams is already
    59,049 joins; eight parts with 20-line seams is 1.8 billion, on filenames
    the uploader chooses — a hang and an out-of-memory, on a synchronous upload
    handler. Past the bound only the two readings worth having are tried, the
    full measured overlap and no overlap at all, and a receipt that needs a
    third stays quarantined, which is the outcome this path is for.

    """
    from itertools import product

    seams = [_seam(parts[i].lines, parts[i + 1].lines) for i in range(len(parts) - 1)]
    if not seams:
        yield parts[0]
        return
    space = 1

    for seam in seams:
        space *= seam + 1
    if space > MAX_JOINS:
        yield _joined(parts, trims=tuple(seams))
        yield _joined(parts, trims=(0,) * len(seams))
        return
    for trims in sorted(product(*(range(s + 1) for s in seams)), key=sum, reverse=True):
        yield _joined(parts, trims=trims)


def _joined(parts: list[Receipt], *, greedy: bool = False, trims: tuple = ()) -> Receipt:
    joined = parts[0]
    for index, part in enumerate(parts[1:]):
        if greedy:
            overlap = _seam(joined.lines, part.lines)
        else:
            overlap = min(trims[index] if index < len(trims) else 0, len(part.lines))
        joined = Receipt(
            captures=joined.captures + part.captures,
            lines=joined.lines + part.lines[overlap:],
            customer_id=joined.customer_id or part.customer_id,
            tax=part.tax if part.tax is not None else joined.tax,
            balance=part.balance if part.balance is not None else joined.balance,
            tender=part.tender or joined.tender,
            stamp=part.stamp or joined.stamp,
            complete=part.balance is not None or joined.complete,
            # Carried with the tax it describes. Dropping these defaulted a
            # stitched receipt to "the word TAX was read", which is the one
            # state that stops the statement adjudicating — so a tall receipt
            # whose TAX line could not be read lost its last purchase, footed
            # anyway, and then disagreed with the statement. The adapter
            # reported that as the retailer contradicting itself.
            tax_inferred=(part.tax_inferred if part.tax is not None else joined.tax_inferred),
            tax_description=(
                part.tax_description if part.tax is not None else joined.tax_description
            ),
            # One clipped half clips the join: the amounts it contributed are
            # cut whatever the other half looked like.
            clipped=joined.clipped or part.clipped,
            # Named even though stitching happens before any model is asked, so
            # it is always False here today. Every field this function forgot
            # has become a bug — `tax_inferred` once, `clipped` a second time —
            # and the cost of naming one that cannot yet be set is nothing.
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
                return Decimal(found.group("value").replace(",", ""))
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
    unreadable on 15 of the 43 real visits — measured through this reader, not
    the prototype that suggested five.
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

    The timestamp, the customer number, the captures, **the balance and the
    tax** come from `like` rather than from the reply. Those last two are the
    whole gate: taking the balance out of the reply too made `foots()` a check
    of three model-authored numbers against each other, so any self-consistent
    JSON passed — a fabricated basket worth an order of magnitude more than the
    page's own total was accepted. The printed total is the one fact the model had

    no hand in, and it only works as a check if it comes from the page. `_tax_for`
    has the same argument for the other free variable, and why bounding it is
    enough where the page could not state it.


    A reply whose balance disagrees with the printed one is refused outright
    rather than corrected: the two read the same pixels and reached different
    numbers, and nothing here can say which is right.

    **A page whose own balance could not be read stores nothing**, and the
    reason is worth keeping because it was tried the other way first.

    A capture clipped at its right edge loses the printed total along with the
    last digit of every amount, so the page that most needs a second reader is
    the page with nothing left to check one against. The statement's total for
    that visit looks like the missing fact — a separate document the model never
    saw — and it is not enough. With `balance` set to `anchor + tax`, `foots()`
    reduces to `subtotal == anchor`, which is the SAME comparison `_disagrees`
    makes downstream: one constraint, one scalar, wearing two hats.

    Four rounds of adversarial review each found a different way through it, and
    each fix opened the next. A wholly invented line worth exactly the statement
    total. An engine reading of nothing, which corroborated everything. A lone
    figure matched by the model's own tax. Finally a floor of two matched lines,
    defeated by matching two trivial ones: ten amounts of a tenth each gated one
    invented line of 4,999, and 99.98% of the stored basket was fabricated.

    The pattern is structural rather than a missing bound. Every second fact
    available here comes from the engine's own partial reading, which is weak by
    construction — a clip is why the route exists at all — and influenced by
    whoever supplied the capture, who also supplied the statement the anchor
    comes from. Bounding the answer cannot manufacture an independent
    measurement, so the answer is not stored. The visit keeps the total the
    statement gave it, which is a fact no reading of the picture can move, and
    the warning says the capture could not be recovered and why.

    The captures arrive by mail from outside the trust boundary, and text
    rendered into one is an instruction the model may read.
    """

    lines = []
    rows = reply.get("lines") or []
    if not isinstance(rows, list) or len(rows) > MAX_REPLY_LINES:
        # The byte cap upstream is generous enough to hold tens of thousands of
        # short rows, and every one of them would become a `TxnItem`.
        return None
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            return None
        description = _clean(str(row.get("description") or ""))[:MAX_DESCRIPTION]
        amount = _decimal(row.get("amount"))
        if amount is None:
            # One unreadable amount voids the answer rather than costing one
            # line. A basket missing a line still adds up if the model also
            # adjusted the total, and skipping quietly is how that gets stored.
            #
            # Checked BEFORE the furniture test, not after. The other order let
            # a row the model happened to name `TAX` carry any garbage it liked
            # and be dropped in silence, which took the rule out of the only
            # part of the answer that is not checked by arithmetic.
            return None
        if _is_furniture(description, amount, reply):
            # The prompt asks for purchases only and says so twice. A 30B model
            # returned the TAX line, the BALANCE line and the weight qualifier
            # above a weighed item as three more purchases, the last of them
            # carrying a copy of the amount below it — 14 rows for an 11-item
            # receipt, summing to more than twice what was paid. Nothing here
            # trusts the answer, so nothing here should depend on it obeying:
            # these are the receipt's own furniture and the format layer
            # already knows their shape.
            continue
        lines.append(ReceiptLine(description=description, amount=amount, row=index))
    if not lines:
        return None

    ceiling = like.balance
    if ceiling is None:
        # Nothing printed to check the answer against. See the docstring for
        # what was tried in place of this and why it is not there any more.
        return None

    tax = _tax_for(reply, like, ceiling)
    if tax is None:
        return None
    # Measured against the page's total OR against what the engine itself got
    # off the page, whichever is larger.
    #
    # A receipt can legitimately total nothing — a product and its cancellation
    # net to zero — and against a bare `ceiling * MAX_GROSS` that is zero, so
    # every non-empty basket fails and a correct reading of a voided visit can
    # never be stored. A fixed floor does not help either: the gross of such a
    # visit is twice an ordinary item's price, which no constant covers.
    #
    # The engine's own gross does. It is a measurement of how much money this
    # page has on it, taken by a reader the model had no hand in, so it bounds
    # the answer's magnitude on a page whose total cannot. It is only ever a
    # magnitude bound here — the printed total is still the gate.
    engine_gross = sum((abs(item.amount) for item in like.lines), Decimal("0.00"))
    allowed = max(ceiling, engine_gross) * MAX_GROSS

    if sum((abs(item.amount) for item in lines), Decimal("0.00")) > allowed:
        # The gate is a check on the SUM, and a sum says nothing about its
        # parts: a pair of offsetting lines at a thousand pounds each nets to
        # zero, passes every arithmetic check there is, and puts two invented
        # products into Products and Prices. `_tax_for`'s docstring used to
        # claim that was already impossible. It was not.
        #
        # Measured rather than guessed, and measured on the GROSS rather than
        # on any single line, because a discount legitimately makes one line
        # bigger than the whole receipt — an item priced above the balance is an
        # ordinary page once its discount is counted. Across the real corpus the
        # worst gross-to-total ratio is 2.2 and the worst single line is 0.95 of
        # its own receipt; the offsetting-pair attack runs at 21.

        return None

    claimed = _decimal(reply.get("balance"))

    if claimed is None or claimed != like.balance:
        return None
    # `replace`, not a fresh `Receipt`: enumerating the fields by hand is how
    # this dropped `tax_inferred` once and `clipped` a second time. Every field
    # not named here is the page's own, which is what it should be.
    return replace(like, lines=tuple(lines), tax=tax, complete=True)


#: The most lines one answer may claim. A page holds about thirty; the reply
#: cap is in bytes, and bytes buy a great many short rows.
MAX_REPLY_LINES = 200


#: How many times its own total a basket's gross may come to before the reading
#: stops being a reading. Discounts are negative lines, so the gross legitimately
#: exceeds the total: measured across the real corpus the worst is 2.2. Five
#: leaves that more than double the headroom and still refuses the offsetting
#: pair that made this bound necessary, which runs at twenty-one.
MAX_GROSS = 5


#: The longest a line's name may be. A receipt line is twenty or thirty

#: characters; the transport cap is four megabytes, and the row cap is a count,
#: so nothing between them stopped one answer putting a megabyte of model-authored
#: text into `description_raw` — which is also the key a product is identified by
#: where the retailer disclosed no code. Measured: a 50,000-character name was
#: stored verbatim.
MAX_DESCRIPTION = 200


def _is_furniture(description: str, amount: Decimal, reply: dict) -> bool:
    """A line that is the receipt talking about itself, not something bought.

    The tender line is recognised by its AMOUNT, not by its wording. Matching
    the word alone dropped real purchases: `TENDER` is a whole-word search for
    CREDIT, DEBIT, CASH, CHECK, EBT or GIFT, and a shop that sells gift cards
    prints `GIFT CARD 25` as an ordinary line. It was deleted from the basket
    in silence, the sum then came up short, and the visit was quarantined for
    arithmetic that looked wrong for a reason nobody could see. What actually
    identifies the tender line is that it repeats the balance — which is how
    `_settle` finds it in the engine's own reading, by position and by the
    echo, never by the word.
    """
    text = description.strip()
    if TAX.search(text) or BALANCE.search(text) or QUANTITY.match(text):
        return True
    return bool(TENDER.search(text)) and amount == _decimal(reply.get("balance"))


def _tax_for(reply: dict, like: Receipt, ceiling: Decimal) -> Decimal | None:
    """The tax to check this answer against, or None to refuse the answer.

    Pinning the balance alone left the gate with one equation and two unknowns
    the model controls. `foots()` is `subtotal + tax - balance`, so a reply can
    quote the printed total back correctly — it can read the page too — and let
    `tax` absorb whatever the basket was inflated by. Measured: lines of 1000.00
    and 250.00 against a page printing 20.00, with `tax` returned as -1230.00,
    reconciled and stored. Same hole as the balance, one field over.

    So tax comes off the page wherever the page could be read. Where it could
    not, the reply's figure is accepted only within the range a tax can occupy:
    at least nothing, at most `ceiling`.

    `ceiling` is the total printed on the page, which is the only route that
    stores anything — see `from_reply` for the one that was withdrawn and why.

    Note what this bound does NOT do, because this docstring used to claim it:
    it bounds the SUM, and a sum says nothing about its parts. A pair of
    offsetting lines nets to zero and walks straight through it. `from_reply`
    bounds the gross for that.
    """

    if like.tax is not None and not like.tax_inferred:
        return like.tax
    tax = _decimal(reply.get("tax"))
    if tax is None:
        return Decimal("0.00") if like.tax is None else like.tax
    if tax < 0 or tax > ceiling:
        # Not a tax. An answer reaching for one of these is reaching for the
        # residual, which is the thing the printed total exists to pin.
        return None
    return tax


#: What a currency mark comes back as. The engine returns `§` and `s` for `$`
#: often enough to be named, and a vision model asked for "no currency symbol"
#: returns a colon in front of the number — it transcribes the mark it can see
#: rather than dropping

#: it. Measured against the corpus: every amount in a 30B model's answer came
#: back with a leading colon, `_decimal` returned None for all of them, and
#: `from_reply` voids the whole answer on one unreadable amount — so the
#: adjudication tier read every page correctly and stored nothing, ever.
_CURRENCY_MARKS = "$§s:"


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        # Only from the FRONT, and only marks. A stray character in the middle
        # of a number still voids it: a letter O where a zero belongs is not a
        # number here, it is a reading

        # nobody should act on.
        found = Decimal(text.lstrip(_CURRENCY_MARKS).strip())

        # `Decimal("NaN")` parses without raising, and NaN compares false
        # against everything — so it survives to the gate and only fails there
        # by accident. The gate should not be load-bearing for type safety.
        if not found.is_finite():
            return None
        return found.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def split_into_receipts(parts: list[Receipt]) -> list[Receipt]:
    """Which of these captures are of the same receipt.

    A group shares a filename date, and that is a candidate grouping rather than
    an answer: two captures of one day are the halves of one tall receipt about
    as often as they are two trips to the shop.

    Partitioned at every capture that reached the end of a page. A part that did
    NOT reach the end is the top half of whatever follows it, so it joins
    forward; a part that did is the end of its own receipt.

    The rule this replaces was "all parts complete, or stitch them all", which
    fused a whole day into one receipt as soon as a single capture among them
    was unreadable — two real visits, one of them captured twice, gave three
    parts, one incomplete, and all three were stitched into a basket that could
    not possibly foot. Both visits were then quarantined under one warning.
    """
    receipts: list[Receipt] = []
    run: list[Receipt] = []
    for part in parts:
        run.append(part)
        if part.complete:
            receipts.append(stitch(run) if len(run) > 1 else run[0])
            run = []
    if run:
        receipts.append(stitch(run) if len(run) > 1 else run[0])
    return receipts
