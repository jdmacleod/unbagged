"""Finding JSON embedded in a document's text.

Retailers put structured data inside PDFs, so recovering it means locating
`{...}` regions in prose. A regex cannot do this: `\\{.*?\\}` stops at the first
closing brace and so never matches a nested object, and `\\{.*\\}` swallows
everything between the first and last brace in the file. Both failures are
silent, which is the worst kind.

Scanning brace depth handles nesting, and tracking string state means a brace
inside a JSON string — or in prose mentioning "{}" — does not count.

Generic on purpose: it started in the Kroger reader, and the second consumer
(`tools/build_denylist.py`) hit the same nesting bug independently.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

# A comma with nothing but whitespace between it and a closing brace or bracket.
TRAILING_COMMA = re.compile(r",\s*([}\]])")


def scan_braces(text: str) -> tuple[list[tuple[int, int]], int | None]:
    """Top-level `{...}` spans, plus where an unclosed one began.

    The second value is what makes truncation visible: a document cut off
    mid-object has an opening brace that never closes, and saying so is far more
    use than silently returning three sections where there should be four.
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
    return spans, (start if depth > 0 and start >= 0 else None)


def json_spans(text: str) -> list[tuple[int, int]]:
    return scan_braces(text)[0]


def unterminated_span(text: str) -> int | None:
    """Offset at which an unclosed JSON block begins, if the text is truncated."""
    return scan_braces(text)[1]


def repair(fragment: str) -> str:
    """Fix the two ways a report's JSON arrives invalid, and no others.

    Both come from the real world rather than from theory:

    * **A newline inside a string.** PDF text extraction wraps long lines, and a
      wrapped line inside a JSON string key is not legal JSON. Joined with a
      space, which is what the wrap replaced.
    * **A trailing comma** before a closing brace or bracket. Retailers emit
      them; `json` refuses them.

    Deliberately not a general-purpose JSON fixer. Anything cleverer starts
    guessing at what the retailer meant, and a parser that silently invents
    structure is worse than one that reports it could not read the section.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in fragment:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch in "\r\n":
                # A wrapped line inside a string: the newline stood in for a space.
                if out and out[-1] != " ":
                    out.append(" ")
                continue
        elif ch == '"':
            in_string = True
        out.append(ch)

    # Trailing commas, now that strings are single-line and cannot hide one.
    repaired = "".join(out)
    return TRAILING_COMMA.sub(r"\1", repaired)


def _loads(fragment: str) -> Any:
    """Parse, repairing once if the first attempt fails."""
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        return json.loads(repair(fragment))


def iter_json(text: str) -> Iterator[tuple[int, int, Any]]:
    """Yield `(start, end, parsed)` for every span that parses.

    A span that does not parse is skipped rather than raised: one corrupt
    section must not cost the others. Use `unparseable_spans` to report them.
    """
    for start, end in json_spans(text):
        try:
            yield start, end, _loads(text[start:end])
        except json.JSONDecodeError:
            continue


def unparseable_spans(text: str) -> list[tuple[int, int]]:
    """JSON-shaped regions that did not parse, so a caller can warn about them."""
    bad = []
    for start, end in json_spans(text):
        try:
            _loads(text[start:end])
        except json.JSONDecodeError:
            bad.append((start, end))
    return bad


def walk(node: Any) -> Iterator[tuple[str, Any]]:
    """Every (key, scalar value) pair in a parsed structure, at any depth."""
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, (dict, list)):
                    stack.append(value)
                else:
                    yield str(key), value
        elif isinstance(current, list):
            stack.extend(current)
