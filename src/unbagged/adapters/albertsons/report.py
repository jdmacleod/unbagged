"""Reading the Albertsons report PDF's text layer.

The PDF is rendered HTML (wkhtmltopdf): a request-details block, two paragraphs
of instructions, then key/value tables, one per data section. pdfplumber reads
it as lines, and three things about those lines are worth isolating here rather
than leaving in the adapter. All three are recorded in `NOTES.md`.

* **Bold text arrives doubled, character by character.** The renderer draws a
  bold run twice at a small offset, and the text layer keeps both copies
  interleaved, so `Loyalty Profile Details` reads as `LLooyyaallttyy
  PPrrooffiillee DDeettaaiillss`. Every heading and every label in the request
  block is bold; no value is. That makes doubling a usable signal — and a
  dangerous one if applied to values, because `1133` is a doubled `13`. So it
  is undone only on lines where every token is doubled (headings), and on the
  label tokens of the request block, which are matched against known labels.
* **A table row is `Key value` on one line**, split at the first space. A row
  whose value is empty is the key alone.
* **A cell holding several lines is split around its label.** The preference
  cell carries four `name:value` lines and its row label is vertically centred,
  so the label lands between the second and third. Those lines are grouped by
  their own keys, not by position.

The reader never raises on content: anything it does not recognise is left for
the adapter to warn about, and the adapter decides what is worth saying.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

#: The request block's labels, in the order they print. Matched rather than
#: inferred from doubling, so the block still reads if a future renderer stops
#: drawing bold twice.
REQUEST_LABELS = (
    "Request ref ID",
    "Request type",
    "Date submitted",
    "Subject type",
    "First name",
    "Last name",
)

#: Headings that open prose rather than a table.
PROSE_HEADINGS = ("Instructions", "A Guide to Your Data", "What if I Have Questions")

#: The heading that ends the prose and starts the tables.
DETAILED_REPORT = "Detailed Report"

#: A section heading as it reads once undoubled: a title ending in "Details",
#: optionally scoped to a card (`cc`) or a household (`hh`) by a bracketed id.
SECTION = re.compile(
    r"^(?P<title>[A-Za-z][A-Za-z ]*?\b[Dd]etails)"
    r"(?:\s*\[\s*(?P<kind>[a-z]+)\s*:\s*(?P<id>[^\]]*?)\s*\])?$"
)

#: A table row. Keys observed are `Snake_case` with at least one underscore, or
#: one of a few plain words; anything else on its own line is a continuation of
#: the value above it, which is what a wrapped address would look like.
ROW = re.compile(
    r"^(?P<key>[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+|Address|Phone|Email)(?:\s+(?P<value>.*))?$"
)

#: One line of a multi-line cell: `name:value`, lower snake case.
CELL_LINE = re.compile(r"^(?P<key>[a-z][a-z0-9_]*):(?P<value>.*)$")

#: The printed page footer, `1 / 2`.
FOOTER = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


@dataclass(frozen=True)
class Field:
    key: str
    value: str
    page: int


@dataclass
class Section:
    """One table of the detailed report."""

    title: str
    page: int
    #: `cc` (a card) or `hh` (a household), and the id it names, when the
    #: heading carries one. The response's own statement of what a table is
    #: about, and the only place it says so.
    scope_kind: str | None = None
    scope_id: str | None = None
    fields: list[Field] = field(default_factory=list)
    #: Multi-line cells, each a dict of its `name:value` lines.
    records: list[dict[str, Field]] = field(default_factory=list)

    def get(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


@dataclass
class Report:
    request: dict[str, Field] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    #: The instructions, as lines, for quoting. Never parsed for meaning beyond
    #: finding a sentence to quote.
    prose: list[str] = field(default_factory=list)
    #: Lines inside the detailed report that were neither a heading nor a row.
    #: The adapter reports how many, so a format change is loud.
    unread: list[tuple[int, str]] = field(default_factory=list)

    def section(self, title: str) -> Section | None:
        wanted = title.lower()
        return next((s for s in self.sections if s.title.lower() == wanted), None)


def is_doubled(token: str) -> bool:
    return len(token) >= 2 and len(token) % 2 == 0 and token[0::2] == token[1::2]


def undouble(token: str) -> str:
    return token[0::2] if is_doubled(token) else token


def heading(line: str) -> str | None:
    """The heading this line is, undoubled — or None if it is not one.

    A line is a heading when every token on it is doubled and it has a letter
    in it. Requiring every token is what keeps a value out: `Card_nbr 1133`
    has one doubled-looking token and one that is not.

    A line that is not doubled at all is also accepted when it reads as a known
    heading, so the reader does not depend on the renderer's bold.
    """
    tokens = line.split()
    if not tokens or not any(ch.isalpha() for ch in line):
        return None
    if all(is_doubled(token) for token in tokens):
        return " ".join(undouble(token) for token in tokens)
    plain = " ".join(tokens)
    known = (*PROSE_HEADINGS, DETAILED_REPORT, "Request details")
    if plain in known or (SECTION.match(plain) and not ROW.match(plain)):
        return plain
    return None


def lines(pages: tuple[str, ...]) -> Iterator[tuple[int, str]]:
    """Every line with the printed page it sits on; footers consumed.

    A PDF arrives as pages and a page's index is its printed number. A text
    rendering arrives as one page, and then the footers are what say where a
    page ends — so they are counted rather than discarded.
    """
    if len(pages) > 1:
        for number, page in enumerate(pages, start=1):
            for line in page.splitlines():
                if not FOOTER.match(line):
                    yield number, line
        return
    number = 1
    for line in "\n".join(pages).splitlines():
        if FOOTER.match(line):
            number += 1
            continue
        yield number, line


def read(pages: tuple[str, ...]) -> Report:  # noqa: C901, PLR0912 - baselined, may only shrink
    report = Report()
    mode = "preamble"
    current: Section | None = None
    record: dict[str, Field] | None = None

    for page, raw in lines(pages):
        line = raw.strip()
        if not line:
            continue
        title = heading(line)
        if title is not None:
            if title.lower() == "request details":
                mode = "request"
                continue
            if title in PROSE_HEADINGS:
                mode = "prose"
                continue
            if title == DETAILED_REPORT:
                mode = "report"
                continue
            match = SECTION.match(title)
            if match and mode == "report":
                current = Section(
                    title=match.group("title"),
                    page=page,
                    scope_kind=match.group("kind"),
                    scope_id=(match.group("id") or "").replace(" ", "") or None,
                )
                report.sections.append(current)
                record = None
                continue

        if mode == "request":
            report.request.update(_request_fields(line, page))
        elif mode == "prose":
            report.prose.append(line)
        elif mode == "report" and current is not None:
            if cell := CELL_LINE.match(line):
                key = cell.group("key")
                if record is None or key in record:
                    record = {}
                    current.records.append(record)
                record[key] = Field(key, cell.group("value").strip(), page)
            elif row := ROW.match(line):
                current.fields.append(
                    Field(row.group("key"), (row.group("value") or "").strip(), page)
                )
                record = None
            elif current.records and _is_cell_label(line):
                # The multi-line cell's own row label, printed between its
                # lines. Only inside such a cell: elsewhere a line shaped like
                # one is a wrapped value, and dropping it would lose part of it.
                continue
            elif current.fields and not current.records:
                # A wrapped value: joined to the row above, with the space the
                # wrap replaced. Never guessed at beyond that.
                last = current.fields[-1]
                current.fields[-1] = Field(last.key, f"{last.value} {line}".strip(), last.page)
            else:
                report.unread.append((page, line))
        elif mode == "report":
            report.unread.append((page, line))
    return report


def _is_cell_label(line: str) -> bool:
    """`Customer Preference 1` — a row label with a running number."""
    return bool(re.fullmatch(r"[A-Z][A-Za-z ]+ \d+", line))


def _request_fields(line: str, page: int) -> dict[str, Field]:
    """`Request ref ID X Request type Y` → two fields.

    Labels are found by matching known label tokens, compared undoubled; every
    other token belongs to the value of the label before it, verbatim. Values
    are never undoubled: a reference number is free to look doubled.
    """
    tokens = line.split()
    found: dict[str, list[str]] = {}
    label: str | None = None
    index = 0
    labels = sorted(REQUEST_LABELS, key=lambda text: -len(text.split()))
    while index < len(tokens):
        for candidate in labels:
            words = candidate.split()
            window = [undouble(token).lower() for token in tokens[index : index + len(words)]]
            if window == [word.lower() for word in words]:
                label = candidate
                found[label] = []
                index += len(words)
                break
        else:
            if label is not None:
                found[label].append(tokens[index])
            index += 1
    return {key: Field(key, " ".join(value), page) for key, value in found.items()}


def sentences(report: Report) -> list[str]:
    """The instructions as sentences.

    The prose wraps across lines, so it is joined before it is split. A split
    needs a full stop AND a capital after it, which keeps `(e.g., address` and
    `(i.e., PHI` whole.

    A bullet is a boundary too, and is dropped. The contact list follows the
    paragraph on what was withheld with nothing but a bullet after its last
    full stop, so without this every way to reach the privacy office was
    quoted as part of that sentence.
    """
    text = " ".join(report.prose)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])|\s*•\s*", text)
    return [s.strip() for s in parts if s.strip()]


def quote(report: Report, pattern: re.Pattern[str]) -> str | None:
    """The first sentence of the instructions matching `pattern`, whole.

    Quoted rather than summarised: a disclosure's evidence is what the response
    said, and a paraphrase would be this tool speaking for it.
    """
    return next((s for s in sentences(report) if pattern.search(s)), None)


def quote_all(report: Report, pattern: re.Pattern[str]) -> list[str]:
    """Every sentence matching `pattern`, in the order the response gives them."""
    return [s for s in sentences(report) if pattern.search(s)]
