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
from collections.abc import Iterator
from typing import Any


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


def iter_json(text: str) -> Iterator[tuple[int, int, Any]]:
    """Yield `(start, end, parsed)` for every span that parses.

    A span that does not parse is skipped rather than raised: one corrupt
    section must not cost the others. Use `unparseable_spans` to report them.
    """
    for start, end in json_spans(text):
        try:
            yield start, end, json.loads(text[start:end])
        except json.JSONDecodeError:
            continue


def unparseable_spans(text: str) -> list[tuple[int, int]]:
    """JSON-shaped regions that did not parse, so a caller can warn about them."""
    bad = []
    for start, end in json_spans(text):
        try:
            json.loads(text[start:end])
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
