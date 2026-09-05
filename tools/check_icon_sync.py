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

**What this proves, and what it does not.** It proves that each source that
moved was accompanied by something it produces, matched per source rather than
as one pool: editing one SVG while touching a raster belonging to the other does
not count. It does not prove they are correct — regenerate wrongly
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
DERIVED_SUFFIXES = (".png", ".ico")

# Which source produces which files, read off build_icons.py. Per source, not a
# single pool: an earlier version asked only "did any source change and any
# generated file change", which passes when a pull request edits one SVG and
# touches a raster belonging to the other. The first SVG's icons then stay stale
# with the gate green, which is the bug this exists to catch wearing a hat.
#
# A test asserts this mapping against what build_icons.py actually writes, and
# a second asserts the attribution rather than just the coverage, so a swapped
# pair cannot pass either.
PRODUCES: dict[str, tuple[str, ...]] = {
    "resources/unbagged-logo.svg": (
        "resources/icon-512.png",
        "resources/apple-touch-icon-180.png",
    ),
    "resources/unbagged-logo-small.svg": (
        "resources/favicon-16.png",
        "resources/favicon-32.png",
        "resources/favicon-48.png",
        "resources/favicon.ico",
    ),
}


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


def changed_sources(paths: list[str]) -> list[str]:
    """The watched source SVGs among `paths`."""
    return sorted(
        p for p in paths
        if p.startswith(f"{WATCHED_DIR}/") and p.endswith(SOURCE_SUFFIX)
    )


def unaccompanied(paths: list[str], reasons: dict[str, str]) -> list[str]:
    """Sources that moved with none of what they produce, and no reason given.

    Per source. `reasons` is keyed by source path, so a reason written for one
    SVG cannot excuse an edit to the other — which a range-wide flag did, since
    it flattened every commit message into a single yes.
    """
    changed = set(paths)
    stale = []
    for source in changed_sources(paths):
        if source in reasons:
            continue
        produced = PRODUCES.get(source)
        if produced is None:
            # An unmapped source is an unwatched source, which is the whole bug.
            stale.append(source)
            continue
        if not changed.intersection(produced):
            stale.append(source)
    return stale


# The escape hatch, and why it has to exist.
#
# An SVG edit that changes no rendered pixel — a comment, a reformat, an id
# attribute — regenerates to byte-identical rasters. Git then shows no change to
# the derived files, and a check that only asks "did they move together" fails
# with nothing the contributor can do to satisfy it short of a pointless edit.
# That is how a gate gets disabled instead of fixed.
#
# So a commit may carry a reason, in the shape the PII scanner already uses for
# its suppressions. It names the source it excuses:
#
#     icons-unchanged: unbagged-logo.svg reformatted, renders identically
#
# Naming the file is not ceremony. An unscoped marker excused every source in
# the range, so a render-neutral edit in one commit waved through a later commit
# that moved pixels and skipped the regenerate. A bare marker does not count.
ESCAPE = "icons-unchanged:"


def escape_reasons(base: str) -> dict[str, str]:
    """`icons-unchanged: <source> <reason>` lines, keyed by the source named."""
    result = subprocess.run(
        ["git", "log", "--format=%B", f"{base}..HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return parse_reasons(result.stdout)


def parse_reasons(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        marker = line.strip()
        if not marker.lower().startswith(ESCAPE):
            continue
        rest = marker[len(ESCAPE):].strip()
        if not rest:
            continue
        named, _, reason = rest.partition(" ")
        named = named.rstrip(":")
        path = f"{WATCHED_DIR}/{named}" if "/" not in named else named
        if path in PRODUCES and reason.strip():
            found[path] = reason.strip()
    return found


def run(paths: list[str], reasons: dict[str, str] | None = None) -> int:
    reasons = reasons or {}
    sources = changed_sources(paths)
    if not sources:
        print("check_icon_sync: no source SVG changed")
        return 0

    stale = unaccompanied(paths, reasons)
    for source, reason in sorted(reasons.items()):
        if source in sources:
            print(f'check_icon_sync: {source} allowed by "{reason}"')
    if not stale:
        print(f"check_icon_sync: {len(sources)} source(s) changed, "
              "each with what it produces or a stated reason")
        return 0

    print("check_icon_sync: a source SVG changed and nothing it produces did",
          file=sys.stderr)
    for source in stale:
        produced = PRODUCES.get(source)
        if produced is None:
            print(f"  UNMAPPED  {source}  (not in PRODUCES, so nothing watches it)",
                  file=sys.stderr)
            continue
        print(f"  STALE  {source}", file=sys.stderr)
        for name in produced:
            print(f"           expected one of: {name}", file=sys.stderr)
    print(
        "\nThe icons in resources/ are still the previous artwork, and "
        "`make brand`\nwill copy them to frontend/public/ without complaint — "
        "the served copies\nmatch the rasters, and the rasters are the thing "
        "that is stale.\n\n"
        "    pip install cairosvg          # not a project dependency\n"
        "    cd resources && python build_icons.py\n"
        "    make brand\n\n"
        "If the edit genuinely changes no pixel, the rasters regenerate identical\n"
        "and there is nothing to commit. Say so in a commit message, naming the\n"
        "file, and this passes:\n\n"
        f"    {ESCAPE} unbagged-logo.svg reformatted the path data, renders identically\n\n"
        "The name and the reason are both required.",
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
