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
# `make reset` renames ./data to data.bak-<timestamp> and leaves it in the
# checkout. It holds every report and the database — the same bytes as data/,
# under a name `data` does not equal. .gitignore denies it, which means a staged
# path from there arrived by `git add -f`, which is precisely the case this hook
# exists for.
FORBIDDEN_PREFIXES = ("data.bak-", "data.bak.")


def forbidden(path: str) -> bool:
    parts = Path(path).parts
    if not parts:
        return False
    root = parts[0]
    return root in FORBIDDEN_ROOTS or root.startswith(FORBIDDEN_PREFIXES)


def main(argv: list[str]) -> int:
    offenders = [p for p in argv if forbidden(p)]
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
