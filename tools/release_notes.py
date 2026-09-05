#!/usr/bin/env python3
"""Extract a release's notes from CHANGELOG.md, and refuse to guess.

    python tools/release_notes.py v0.12.0

Prints the body of that version's CHANGELOG section on stdout, for a release
workflow to hand to `gh release create --notes-file`.

The point is that release notes are *extracted*, never retyped. A human writing
them again at tag time produces a second description of the same release, and
the two drift — usually in the direction of the notes being cheerier than the
CHANGELOG, because one is written to announce and the other to record.

Three ways it refuses, all of them things that would otherwise ship as a release
nobody notices is wrong:

    tag names a version CHANGELOG does not describe  -> exit 1
    VERSION file disagrees with the tag              -> exit 1
    the section exists but is empty                  -> exit 1

The second is the one worth having. Tagging a commit whose VERSION file says
something else produces a release whose own artifacts contradict its name, and
nothing downstream would catch it — the app reports `__version__` from the
VERSION file, so the running software would name a version that was never
released.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
VERSION_FILE = REPO_ROOT / "VERSION"

# `## [0.12.0] - 2026-09-05`, and the next `## [` that ends it. Unreleased is a
# heading like any other here, which is deliberate: tagging a version whose
# section is still called Unreleased should fail, and it does, because the tag
# will not match.
SECTION = "## ["


def strip_prefix(tag: str) -> str:
    """`v0.12.0` and `0.12.0` both mean the same release."""
    return tag[1:] if tag.startswith("v") else tag


def extract(text: str, version: str) -> str | None:
    """The body under `## [version]`, or None if there is no such heading."""
    heading = re.compile(rf"^## \[{re.escape(version)}\]", re.M)
    match = heading.search(text)
    if match is None:
        return None
    rest = text[match.end():]
    # Past the rest of the heading line, then up to the next version heading.
    rest = rest.split("\n", 1)[1] if "\n" in rest else ""
    nxt = rest.find(f"\n{SECTION}")
    return (rest if nxt == -1 else rest[:nxt]).strip()


def declared_version() -> str:
    return VERSION_FILE.read_text().strip()


def run(tag: str) -> int:
    version = strip_prefix(tag)
    declared = declared_version()
    if version != declared:
        print(
            f"release_notes: tag {tag} names {version}, but VERSION says {declared}.\n"
            "The running app reports its version from that file, so this would "
            "publish a\nrelease whose own software disagrees with its name.",
            file=sys.stderr,
        )
        return 1

    body = extract(CHANGELOG.read_text(encoding="utf-8"), version)
    if body is None:
        headings = re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(), re.M)
        print(
            f"release_notes: CHANGELOG.md has no section for {version}.\n"
            f"It describes: {', '.join(headings[:6])}\n"
            "Add the section before tagging — a release with no notes is a "
            "release nobody\ncan read.",
            file=sys.stderr,
        )
        return 1
    if not body:
        print(
            f"release_notes: the section for {version} is empty.",
            file=sys.stderr,
        )
        return 1

    print(body)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the tag being released, e.g. v0.12.0")
    return run(parser.parse_args(argv).tag)


if __name__ == "__main__":
    raise SystemExit(main())
