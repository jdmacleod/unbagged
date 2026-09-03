"""Structure-preserving skeletons for bug reports.

Someone whose Safeway report will not parse needs to show a maintainer the *shape*
of the file without sending their groceries to the internet. This module reduces a
report to that shape:

* object keys are retained, because keys are the retailer's schema, not the user's data
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


def skeleton_json(node: Any) -> Any:
    if isinstance(node, dict):
        return {str(k): skeleton_json(v) for k, v in node.items()}
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
            "name": name,
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
