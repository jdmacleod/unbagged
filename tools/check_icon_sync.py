#!/usr/bin/env python3
"""Refuse a change that edits a source SVG and leaves the icons it produces behind.

    python tools/check_icon_sync.py origin/main

`resources/` holds two source SVGs and six files generated from them by
`build_icons.py`. Nothing re-runs that generator: not CI, not pre-commit, and
cairosvg is in no dependency group. `tools/build_brand.py --check` compares
`frontend/public/` against `resources/` and never `resources/` against the SVGs,
so the middle link of the chain was unguarded:

    unbagged-logo.svg ──┐
                        │  build_icons.py          resources/*.png
    unbagged-logo-      ├──────────────────────▶   favicon.ico
    small.svg ──────────┘  ▲                            │
                           │                            │ build_brand.py
                     this check                         ▼
                                                 frontend/public/*
                                                        ▲
                                                 build_brand --check

Edit an SVG, run `make brand`, push: every gate was green, because the served
copies faithfully matched rasters that were a version behind.

**What this proves, and what it does not.** It proves the derived files moved
when their source did. It does not prove they are correct — regenerate wrongly
and this passes. The stronger check regenerates with cairosvg and compares
decoded pixels, which was measured as practical (22s to install libcairo2 on a
slim image, and all six rasters reproduce pixel-identically today) and set aside
for reasons recorded in issue #28. This is the guard that needs no dependency
and no write path.

Deliberately not symmetric: a raster that changes on its own is fine. Re-running
the generator under a newer cairosvg moves the bytes without touching a source,
and that is not the mistake being caught here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHED_DIR = "resources"
SOURCE_SUFFIX = ".svg"
# Everything build_icons.py writes. `resources/README.md` states the contract
# this rests on: two source SVGs, everything else generated from them.
DERIVED_SUFFIXES = (".png", ".ico")


def changed_paths(base: str) -> list[str]:
    """Every path this branch touches relative to `base`.

    Three dots: the merge base, so a change on the base branch is not read as a
    change here. Renames and deletions count as changes, which is what we want —
    deleting a source without deleting what it produced is the same mistake.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def out_of_step(paths: list[str]) -> tuple[list[str], list[str]]:
    """The watched sources and derived files among `paths`."""
    watched = [p for p in paths if p.startswith(f"{WATCHED_DIR}/")]
    sources = sorted(p for p in watched if p.endswith(SOURCE_SUFFIX))
    derived = sorted(p for p in watched if p.endswith(DERIVED_SUFFIXES))
    return sources, derived


# The escape hatch, and why it has to exist.
#
# An SVG edit that changes no rendered pixel — a comment, a reformat, an id
# attribute — regenerates to byte-identical rasters. Git then shows no change to
# the derived files, and a check that only asks "did they move together" fails
# with nothing the contributor can do to satisfy it short of a pointless edit.
# That is how a gate gets disabled instead of fixed.
#
# So: a commit in the range may carry a reason, in the shape the PII scanner
# already uses for its suppressions. A bare marker is not enough; the text after
# the colon has to say something.
ESCAPE = "icons-unchanged:"


def escape_reasons(base: str) -> list[str]:
    """Any `icons-unchanged: <reason>` lines in this branch's commit messages."""
    result = subprocess.run(
        ["git", "log", "--format=%B", f"{base}..HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    found = []
    for line in result.stdout.splitlines():
        marker = line.strip()
        if marker.lower().startswith(ESCAPE):
            reason = marker[len(ESCAPE):].strip()
            if reason:
                found.append(reason)
    return found


def run(paths: list[str], reasons: list[str] | None = None) -> int:
    sources, derived = out_of_step(paths)
    if not sources:
        print("check_icon_sync: no source SVG changed")
        return 0
    if derived:
        print(f"check_icon_sync: {len(sources)} source(s) changed, "
              f"{len(derived)} generated file(s) changed with them")
        return 0
    if reasons:
        print("check_icon_sync: a source changed with no generated file, allowed by:")
        for reason in reasons:
            print(f"  {ESCAPE} {reason}")
        return 0

    print("check_icon_sync: a source SVG changed and nothing generated from it did",
          file=sys.stderr)
    for source in sources:
        print(f"  CHANGED  {source}", file=sys.stderr)
    print(
        "\nThe icons in resources/ are still the previous artwork, and "
        "`make brand`\nwill copy them to frontend/public/ without complaint — "
        "the served copies\nmatch the rasters, and the rasters are the thing "
        "that is stale.\n\n"
        "    pip install cairosvg          # not a project dependency\n"
        "    cd resources && python build_icons.py\n"
        "    make brand\n\n"
        "If the edit genuinely changes no pixel, the rasters regenerate identical\n"
        "and there is nothing to commit. Say so in a commit message and this\n"
        "passes:\n\n"
        f"    {ESCAPE} reformatted the path data, renders identically\n\n"
        "A reason is required — a bare marker does not count.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "base",
        help="the ref to compare against, e.g. origin/main",
    )
    args = parser.parse_args(argv)
    return run(changed_paths(args.base), escape_reasons(args.base))


if __name__ == "__main__":
    raise SystemExit(main())
