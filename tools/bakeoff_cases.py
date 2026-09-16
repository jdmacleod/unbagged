#!/usr/bin/env python3
"""The synthetic receipts a vision bake-off is scored against, and the scoring.

**No real capture is an input here, and none may ever be.** Every page this
module produces is drawn by `tests/receiptimage.py` from rows written in this
file, which is what makes a measured answer publishable: the ground truth is
the source code, so a score can be recorded, a rendered page can be looked at,
and neither carries anything belonging to anybody. A capture's *filename* alone
encodes a date, so there is no safe way to point this at the real corpus and no
flag that offers to.

The cases are not a general OCR benchmark. Each one is a hazard the reader was
actually built against, and the scores are the figures the adapter's gate
depends on rather than accuracy in the abstract:

- **Does the printed total come back?** `receipt.from_reply` refuses any answer
  whose balance does not match the page's own, so a model that reads every line
  and no total is useless here however well it reads.
- **Do the amounts survive `receipt._decimal`?** They did not: a 30B model
  returned every amount behind a colon, `_decimal` returned None for all of
  them, and one unreadable amount voids a whole answer — so the tier read every
  page correctly and stored nothing, ever. Reading and parsing are scored apart
  for that reason.
- **Is the furniture filtered?** TAX, BALANCE, the tender line and the weight
  qualifier are the receipt talking about itself. The prompt forbids them twice
  and models return them anyway; `from_reply` drops them. The rate says how
  much a prompt is worth, which is the thing worth knowing.
- **Do minus signs survive?** A discount read as a charge is the worst single
  error this project can make.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

# `receiptimage` lives with the tests because the tests are its other caller.
# Drawing a second copy here would be a second thing to keep faithful, and the
# fixture being unfaithful is a failure this project has already had. Imported
# as `tests.receiptimage` rather than off a path into the directory, so a test
# importing this module gets the same module object the tests already hold.
from tests.receiptimage import build_receipt  # noqa: E402

from unbagged.adapters.hmart.receipt import _is_furniture  # noqa: E402

#: How alike two descriptions must read before they are the same line. Low on
#: purpose: this scores whether the model found the line, and a model that
#: returns `MILK 2% GAL` as `Milk 2% Gallon` has found it. The amount has to
#: match exactly, which is what actually pins the pairing.
SIMILAR_ENOUGH = 0.6

#: The first number in a string, however it is dressed. Deliberately more
#: permissive than `receipt._decimal`, because the gap between the two IS a
#: measurement: `_decimal` accepts a leading `$`, `§`, `s` or `:` and nothing
#: else, so an amount this finds and `_decimal` refuses is a line the model read
#: and the lane threw away.
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")

#: Anything a model put in front of a number when the prompt said not to.
_LEADING = re.compile(r"^([^\d\-]+)")


@dataclass(frozen=True)
class Line:
    """One printed row, and what it is.

    `kind` is the ground truth the scoring rests on, which is why the rows are
    written here rather than derived from a rendered page: `buy` is a purchase
    and belongs in the answer, everything else is furniture and does not.
    """

    left: str
    description: str
    amount: str | None
    kind: str  # buy | tax | balance | tender | qualifier


@dataclass(frozen=True)
class Case:
    """A page, what it is for, and what a correct reading of it says."""

    name: str
    why: str
    lines: tuple[Line, ...]
    build: dict = field(default_factory=dict)

    def png(self) -> bytes:
        return build_receipt(
            [(line.left, line.description, line.amount) for line in self.lines],
            **self.build,
        )

    @property
    def purchases(self) -> tuple[tuple[str, Decimal], ...]:
        return tuple(
            (line.description, Decimal(line.amount))
            for line in self.lines
            if line.kind == "buy" and line.amount is not None
        )

    @property
    def balance(self) -> Decimal | None:
        for line in self.lines:
            if line.kind == "balance" and line.amount is not None:
                return Decimal(line.amount)
        return None

    @property
    def tax(self) -> Decimal | None:
        for line in self.lines:
            if line.kind == "tax" and line.amount is not None:
                return Decimal(line.amount)
        return None

    @property
    def negatives(self) -> tuple[tuple[str, Decimal], ...]:
        return tuple((name, amount) for name, amount in self.purchases if amount < 0)


def _buy(description: str, amount: str, left: str = "") -> Line:
    return Line(left, description, amount, "buy")


# A basket with the shapes the real pages carry: a plain item, a flagged one, a
# truncated name ending in the cut the receipt itself prints, and a name long
# enough to crowd the amount column.
_BASKET = (
    _buy("MILK 2% GAL", "4.99"),
    _buy("BLH B FRESH POTATO.", "3.49"),
    _buy("BANANA", "1.27", left="WT"),
    _buy("MRNG HI-CHEW GRN A.", "2.15"),
    _buy("ORGANIC BABY SPINACH", "5.99"),
    _buy("CHOBANI GREEK YOGURT", "1.29"),
    _buy("SPARKLING WATER 12PK", "6.49"),
    _buy("FROZEN DUMPLING PORK", "8.99"),
)

_TAIL = (
    Line("", "TAX", "2.94", "tax"),
    Line("***", "BALANCE", "37.60", "balance"),
    Line("", "CREDIT", "37.60", "tender"),
)

CASES: tuple[Case, ...] = (
    Case(
        name="plain",
        why="An ordinary page. The floor: a model that cannot read this reads nothing.",
        lines=_BASKET + _TAIL,
    ),
    Case(
        name="discount",
        why=(
            "Two negative lines. A discount read as a charge is the worst single "
            "error this project can make, and whole-page OCR was abandoned over "
            "exactly this."
        ),
        lines=(
            _BASKET[:4]
            + (
                _buy("SC - MILK PROMO", "-1.50"),
                _buy("SC - SPEND 30 SAVE 5", "-5.00"),
                _buy("CANCEL ITEM", "-3.49", left="CL"),
            )
            + (
                Line("", "TAX", "0.61", "tax"),
                Line("***", "BALANCE", "2.52", "balance"),
                Line("", "CREDIT", "2.52", "tender"),
            )
        ),
    ),
    Case(
        name="weighed",
        why=(
            "Weight qualifiers above the items they describe. The prompt forbids "
            "listing them and says so explicitly; a 30B model listed them anyway, "
            "one of them carrying a copy of the amount below it."
        ),
        lines=(
            Line("2.50 lb @ 1.50 / lb", "", None, "qualifier"),
            _buy("RED SEEDLESS GRAPE", "3.75", left="WT"),
            Line("1.54 lb @ 3.99 / lb", "", None, "qualifier"),
            _buy("BEEF CHUCK ROLL", "6.14", left="WT"),
            Line("3 @ 0.99", "", None, "qualifier"),
            _buy("INSTANT RAMEN CUP", "2.97"),
            _buy("MILK 2% GAL", "4.99"),
            Line("", "TAX", "1.43", "tax"),
            Line("***", "BALANCE", "19.28", "balance"),
            Line("", "CREDIT", "19.28", "tender"),
        ),
    ),
    Case(
        name="scrawl",
        why=(
            "A freehand scrawl across both columns, which a dozen of the real "
            "captures carry. The deterministic reader masks it by colour; a model "
            "gets it unmasked, which is the harder job."
        ),
        lines=_BASKET + _TAIL,
        build={"scrawl": True},
    ),
    Case(
        name="clipped",
        why=(
            "The right edge cut through the last digit of every amount. Nothing is "
            "stored from such a page (#89) because the printed total went with the "
            "digits, but a model that reads the fragments is still worth knowing "
            "about if that issue ever gets an answer."
        ),
        lines=_BASKET + _TAIL,
        build={"clip_digits": 1},
    ),
    Case(
        name="cut_off",
        why=(
            "The top half of a taller receipt: the page stops mid-basket with no "
            "margin under it and no total on it. The gate necessarily refuses this "
            "— scored on reading alone, which is what a second capture would join to."
        ),
        lines=_BASKET,
        build={"cut_off": True},
    ),
)

CASES_BY_NAME = {case.name: case for case in CASES}


def loose(value: object) -> Decimal | None:
    """The number in a model's answer, however it dressed it.

    Not what the lane accepts — `receipt._decimal` is that, and is stricter.
    This is "did the model read the digits", which has to be asked separately or
    a model that reads perfectly and writes `: 4.99` scores as a model that
    cannot read.
    """
    found = _NUMBER.search(str(value or ""))
    if found is None:
        return None
    try:
        return Decimal(found.group().replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def marks_in(value: object) -> str:
    """Whatever a model put in front of a number when told to send none."""
    found = _LEADING.match(str(value or "").strip())
    return found.group(1).strip() if found else ""


@dataclass
class Score:
    """One model's answer to one page, measured.

    Every field is a count, a ratio or a boolean. Nothing here holds text from
    an answer except `marks`, which holds the punctuation a model put in front
    of a number and is the finding that the tier was silently dead.
    """

    case: str
    returned: int = 0
    matched: int = 0
    expected: int = 0
    furniture: int = 0
    strict_ok: int = 0
    marks: tuple[str, ...] = ()
    total_ok: bool = False
    total_seen: bool = False
    signs_ok: bool = True
    #: `from_reply` returned a receipt. Not the whole gate — see `_gate`.
    accepted: bool | None = None
    #: The lane's verdict: accepted AND the arithmetic holds.
    gate_ok: bool | None = None
    seconds: float = 0.0
    failed: str = ""

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 0.0

    @property
    def strict(self) -> float:
        return self.strict_ok / self.returned if self.returned else 0.0


def score(reply: dict | None, case: Case, seconds: float, failed: str = "") -> Score:
    """Measure one answer against the page it was given.

    Reading and parsing are scored apart, and the furniture count is taken with
    `receipt._is_furniture` rather than a second definition written here — the
    question is how much of what the model sends the production filter has to
    throw away, so it has to be the production filter that decides.
    """
    result = Score(case=case.name, expected=len(case.purchases), seconds=seconds, failed=failed)
    if reply is None:
        result.failed = failed or "no answer"
        return result

    rows = reply.get("lines")
    if not isinstance(rows, list):
        result.failed = "no lines array"
        return result

    from unbagged.adapters.hmart.receipt import _decimal

    marks: set[str] = set()
    wanted = list(case.purchases)
    returned: list[tuple[str, Decimal | None]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        result.returned += 1
        description = str(row.get("description") or "").strip()
        raw = row.get("amount")
        mark = marks_in(raw)
        if mark:
            marks.add(mark)
        if _decimal(raw) is not None:
            result.strict_ok += 1
        amount = loose(raw)
        # The production filter's own verdict, so the rate is the rate the lane
        # actually pays. A row with no readable amount cannot be furniture-tested
        # on its amount, and `_is_furniture` needs one, so it gets zero.
        if _is_furniture(description, amount or Decimal("0.00"), reply):
            result.furniture += 1
            continue
        returned.append((description, amount))

    result.marks = tuple(sorted(marks))

    # Greedy, amount first. Two lines at the same price are ordinary on these
    # receipts, so the description is what separates them once the amount has
    # narrowed the field.
    for name, amount in wanted:
        best, score_of_best = None, SIMILAR_ENOUGH
        for candidate in returned:
            if candidate[1] != amount:
                continue
            ratio = SequenceMatcher(None, name.upper(), candidate[0].upper()).ratio()
            if ratio >= score_of_best:
                best, score_of_best = candidate, ratio
        if best is not None:
            result.matched += 1
            returned.remove(best)

    # A negative the model returned as a positive is not a miss, it is a wrong
    # answer of the kind that reaches the database looking correct.
    for _, amount in case.negatives:
        if not any(candidate[1] == amount for candidate in rows_amounts(rows)):
            result.signs_ok = False

    claimed = loose(reply.get("balance"))
    result.total_seen = claimed is not None
    result.total_ok = claimed is not None and claimed == case.balance
    return result


def rows_amounts(rows: list) -> list[tuple[str, Decimal | None]]:
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append((str(row.get("description") or ""), loose(row.get("amount"))))
    return out
