#!/usr/bin/env python3
"""Hard-fail a commit that stages anything under data/.

data/ is gitignored, so reaching this hook means someone used `git add -f` or
`git add -A` against a modified ignore rule. Either way it is the highest-severity
mistake this project can make, so it gets its own hook rather than living as one
rule inside the PII scanner.
"""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_ROOTS = ("data", "output")


def main(argv: list[str]) -> int:
    offenders = [
        p for p in argv
        if Path(p).parts[:1] and Path(p).parts[0] in FORBIDDEN_ROOTS
    ]
    if not offenders:
        return 0

    print("Refusing to commit files under a data directory:\n", file=sys.stderr)
    for p in offenders:
        print(f"  {p}", file=sys.stderr)
    print(
        "\nThese paths hold real right-to-know responses. Git history is forever"
        "\nwithout `git filter-repo`. Unstage them with:"
        "\n\n  git restore --staged " + " ".join(offenders) + "\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
