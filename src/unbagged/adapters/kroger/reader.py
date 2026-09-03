"""Turning a Kroger report's text into sections and JSON blobs.

Kept separate from the adapter itself because this is the part that is purely
about the format's quirks, and the part most likely to need changing when Kroger
changes their export. See NOTES.md for what was actually observed.

Two problems have to be solved before any JSON can be parsed:

1. Bare page-number lines are interleaved into the text, including in the middle
   of the JSON blobs.
2. The blobs are separated by prose headers rather than by any delimiter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# A line that is nothing but a small number. Note that this shape also describes
# a pretty-printed JSON line holding a bare number, which is why matching is not
# enough on its own — see strip_page_markers.
PAGE_MARKER_LINE = re.compile(r"^[ \t]*(\d{1,3})[ \t]*$")

# Headers observed in the real report, in the order they appear. The purchase
# section has been seen under two different headers, so both are recognised.
SECTION_HEADERS: tuple[str, ...] = (
    "Section 1: Specific Pieces of Personal Information Collected",
    "Data we hold related to our Loyalty program:",
    "Data we hold to communicate and advertise to you in a personalized way:",
    "Email Information",
    "Data related to in-store services:",
    "Information about your purchases:",
)

LOYALTY_HEADER = "Data we hold related to our Loyalty program:"
ADVERTISING_HEADER = "Data we hold to communicate and advertise to you in a personalized way:"
EMAIL_HEADER = "Email Information"
PURCHASE_HEADERS = ("Information about your purchases:", "Data related to in-store services:")


@dataclass(frozen=True)
class PageMap:
    """Printed page number for any offset in the cleaned text.

    The printed number is what matters, not the PDF's page index: someone
    checking a provenance link is looking at a page number on a piece of paper.
    """

    breaks: tuple[tuple[int, int], ...] = ()   # (offset in cleaned text, page number)
    first_page: int = 1

    def page_of(self, offset: int) -> int:
        page = self.first_page
        for at, number in self.breaks:
            if offset >= at:
                page = number
            else:
                break
        return page


@dataclass(frozen=True)
class Section:
    header: str
    body: str
    start: int          # offset of the body within the cleaned text


@dataclass(frozen=True)
class Blob:
    """One recovered JSON document, with where it came from."""

    data: Any
    header: str
    start: int
    raw: str = field(repr=False, default="")


# A page sequence has to be evidenced, not guessed. One bare number proves
# nothing; a run of consecutive ones is a page sequence.
MIN_PAGE_CHAIN = 2


def _page_chain(candidates: list[tuple[int, int]]) -> set[int]:
    """Pick the longest run of candidates whose numbers increase by exactly one.

    `candidates` is (line index, number) in document order. Anything outside the
    chosen run is data that merely looks like a page number.
    """
    if not candidates:
        return set()
    best_length = [1] * len(candidates)
    previous = [-1] * len(candidates)
    for i in range(len(candidates)):
        for j in range(i):
            if candidates[j][1] == candidates[i][1] - 1 and best_length[j] + 1 > best_length[i]:
                best_length[i] = best_length[j] + 1
                previous[i] = j
    end = max(range(len(candidates)), key=lambda i: best_length[i])
    if best_length[end] < MIN_PAGE_CHAIN:
        return set()
    chain = set()
    while end != -1:
        chain.add(candidates[end][0])
        end = previous[end]
    return chain


def strip_page_markers(text: str) -> tuple[str, PageMap]:
    """Remove interleaved page-number lines and record where they were.

    The documented strip is a regex over bare-number lines, which also eats a
    JSON line holding a bare number — the hazard recorded in NOTES.md. Matching
    the shape is therefore only the first step: a candidate counts as a page
    marker only if it belongs to the document's longest run of consecutive
    numbers. A stray value in a pretty-printed array does not form a run with
    anything, so it survives.

    The cost is that a report short enough to print fewer than MIN_PAGE_CHAIN
    numbers keeps them in its text. That is the right way round: leaving two
    stray lines in is recoverable, silently deleting a value is not.
    """
    lines = text.splitlines(keepends=True)
    candidates: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = PAGE_MARKER_LINE.match(line.rstrip("\r\n"))
        if match and line.strip():
            candidates.append((index, int(match.group(1))))

    markers = _page_chain(candidates)
    numbers = dict(candidates)

    kept: list[str] = []
    breaks: list[tuple[int, int]] = []
    offset = 0
    first_page = 1
    for index, line in enumerate(lines):
        if index in markers:
            number = numbers[index]
            if not breaks:
                # The first marker names the page the text before it was on.
                first_page = max(number - 1, 1)
            breaks.append((offset, number))
            continue
        kept.append(line)
        offset += len(line)

    return "".join(kept), PageMap(breaks=tuple(breaks), first_page=first_page)


def find_sections(text: str) -> list[Section]:
    """Split the cleaned text on the known headers, in the order they occur.

    Unknown text between headers stays with the preceding section rather than
    being dropped: an adapter that silently discards a region it did not
    recognise cannot report that it failed to read it.
    """
    hits: list[tuple[int, str]] = []
    for header in SECTION_HEADERS:
        for match in re.finditer(re.escape(header), text):
            hits.append((match.start(), header))
    hits.sort()

    sections: list[Section] = []
    for index, (start, header) in enumerate(hits):
        body_start = start + len(header)
        body_end = hits[index + 1][0] if index + 1 < len(hits) else len(text)
        sections.append(
            Section(header=header, body=text[body_start:body_end], start=body_start)
        )
    return sections


def _json_spans(text: str) -> list[tuple[int, int]]:
    """Offsets of top-level {...} regions, found by brace depth.

    Braces inside strings do not count; a report that mentions "{}" in prose
    would otherwise swallow the rest of the document.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, i + 1))
                start = -1
    return spans


def find_blobs(text: str, sections: list[Section] | None = None) -> list[Blob]:
    """Every parseable top-level JSON document in the text, in order.

    A region that looks like JSON but does not parse is skipped rather than
    raising: the caller records it as a warning and keeps the blobs that did
    parse, because one corrupt section must not cost the other three.
    """
    sections = sections if sections is not None else find_sections(text)
    blobs: list[Blob] = []
    for start, end in _json_spans(text):
        raw = text[start:end]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blobs.append(Blob(data=data, header=header_for(sections, start), start=start, raw=raw))
    return blobs


def header_for(sections: list[Section], offset: int) -> str:
    """The header of the section an offset falls inside."""
    current = ""
    for section in sections:
        if offset >= section.start:
            current = section.header
        else:
            break
    return current


def blob_for_header(blobs: list[Blob], *headers: str) -> Blob | None:
    """The first blob under any of the given headers."""
    wanted = set(headers)
    for blob in blobs:
        if blob.header in wanted:
            return blob
    return None


def unparseable_spans(text: str) -> list[tuple[int, int]]:
    """JSON-shaped regions that did not parse, so the adapter can warn about them."""
    bad = []
    for start, end in _json_spans(text):
        try:
            json.loads(text[start:end])
        except json.JSONDecodeError:
            bad.append((start, end))
    return bad
