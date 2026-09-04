"""Structure-preserving skeletons for bug reports.

Someone whose Safeway report will not parse needs to show a maintainer the *shape*
of the file without sending their groceries to the internet. This module reduces a
report to that shape:

* object keys are retained, because keys are the retailer's schema, not the user's
  data — except when a key is itself an identifier, which is a real shape and not a
  hypothetical one: in a Kroger identity blob the loyalty card numbers are the
  *keys* of ``loyaltyCards``, and ``tests/test_fixtures.py`` asserts exactly that.
  A key that looks like an identifier is masked; the rest of the schema survives
* string leaves become ``<str:len=N>``
* numbers are bucketed to their order of magnitude
* dates are coarsened to the month
* text is reduced to punctuation and whitespace, with alphanumeric runs masked

The bias is deliberately toward losing information. A skeleton that is too coarse
costs one round trip on an issue; a skeleton that leaks costs someone their address.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-\d{2}\b")
US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
ALNUM_RUN = re.compile(r"[A-Za-z]+|\d+")

# Keys that are identifiers rather than field names. Deliberately narrow: the
# schema is the reason a skeleton is worth sending at all, so masking a real field
# name costs a round trip on an issue for no gain. These three shapes are the ones
# a retailer actually keys a map by.
#
# The patterns mirror tools/scan_pii.py rather than importing it. The direction of
# the dependency is the point: tools/ is repo tooling that is not shipped, and the
# application must not import from it. tests/test_sanitize.py asserts the two agree
# on the cases that matter, so the duplication cannot drift silently.
IDENTIFIER_KEY = re.compile(
    r"""^(?:
          \d{9,}                                    # loyalty cards, household ids
        | [0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}
        | [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}
    )$""",
    re.X,
)


def bucket_number(value: float | int) -> str:
    """Order of magnitude only. 4.99 and 7.15 both become <num:1e0>."""
    if value == 0:
        return "<num:0>"
    sign = "-" if value < 0 else ""
    exponent = int(math.floor(math.log10(abs(value))))
    return f"<num:{sign}1e{exponent}>"


def coarsen_date(text: str) -> str | None:
    """Return YYYY-MM if the whole string is a date, else None."""
    stripped = text.strip()
    m = ISO_DATE.fullmatch(stripped)
    if m:
        return f"<date:{m.group(1)}-{m.group(2)}>"
    m = US_DATE.fullmatch(stripped)
    if m:
        return f"<date:{m.group(3)}-{int(m.group(1)):02d}>"
    return None


def skeleton_string(text: str) -> str:
    return coarsen_date(text) or f"<str:len={len(text)}>"


def skeleton_key(key: str) -> str:
    """A dict key, kept as-is unless it is an identifier rather than a field name.

    Keys carry the schema, which is what makes a skeleton useful, so the default
    is to keep them. The exception is a map keyed *by* the user's data. The
    masked form keeps the length, so the shape of the map is still legible and a
    maintainer can still see that the keys were 13-digit numbers.
    """
    if IDENTIFIER_KEY.match(key):
        return f"<key:len={len(key)}>"
    return key


def skeleton_json(node: Any) -> Any:
    if isinstance(node, dict):
        return {skeleton_key(str(k)): skeleton_json(v) for k, v in node.items()}
    if isinstance(node, list):
        # Collapse homogeneous lists: 54 identical baskets say nothing 2 do not.
        shaped = [skeleton_json(v) for v in node]
        if len(shaped) > 2 and all(s == shaped[0] for s in shaped[1:]):
            return [shaped[0], f"<repeated:{len(shaped)}>"]
        return shaped
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, (int, float)):
        return bucket_number(node)
    return skeleton_string(str(node))


def skeleton_text(text: str) -> list[str]:
    """Keep punctuation, whitespace and line breaks; mask everything else."""
    out = []
    for line in text.splitlines():
        out.append(ALNUM_RUN.sub(lambda m: ("a" if m.group(0)[0].isalpha() else "9")
                                 + f"{len(m.group(0))}", line))
    return out


def skeleton_csv(text: str) -> dict[str, Any]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"format": "csv", "rows": 0, "columns": []}
    header, *body = rows
    columns = []
    for i, name in enumerate(header):
        values = [r[i] for r in body if i < len(r)]
        columns.append({
            # A header is a field name in every CSV anyone has sent, but it costs
            # nothing to route it through the same rule as an object key.
            "name": skeleton_key(name),
            "non_empty": sum(1 for v in values if v.strip()),
            "sample_shape": skeleton_string(values[0]) if values else None,
        })
    return {"format": "csv", "rows": len(body), "columns": columns}


def sanitize_text(text: str, *, filename: str = "") -> dict[str, Any]:
    """Dispatch on content, not on the extension alone — retailers mislabel files."""
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return {"format": "json", "body": skeleton_json(json.loads(text))}
        except json.JSONDecodeError:
            pass
    if filename.lower().endswith(".csv") or _looks_like_csv(text):
        return skeleton_csv(text)
    return {"format": "text", "lines": skeleton_text(text)}


def _looks_like_csv(text: str) -> bool:
    head = text.splitlines()[:5]
    if len(head) < 2:
        return False
    counts = {line.count(",") for line in head}
    return len(counts) == 1 and counts.pop() >= 2


def sanitize_file(path: Path) -> dict[str, Any]:
    # Existence first. Checking the suffix first meant a typo'd path was
    # reported as ".pdf input is not supported yet", which names a problem the
    # caller does not have and a fix that will not work — they go extracting
    # text from a file that was never there.
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"{path} is a directory; pass one report file")
    if path.suffix.lower() in {".pdf", ".zip"}:
        raise ValueError(
            f"{path.suffix} input is not supported yet — extract the text or the "
            "individual files first, then sanitize those."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "source_bytes": path.stat().st_size,
        "source_suffix": path.suffix.lower(),
        **sanitize_text(text, filename=path.name),
    }
