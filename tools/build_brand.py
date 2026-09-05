#!/usr/bin/env python3
"""Produce the served brand assets in frontend/public/ from the sources in resources/.

    make brand          regenerate and write
    make brand-check    fail if a served file is not what this produces

Two reasons this is a build step rather than a copy.

**The metadata.** The source SVGs and PNGs carry a C2PA content-credential
manifest that dwarfs the artwork: 93% of `unbagged-logo-small.svg` and 94% of
`favicon-48.png` is base64 manifest. It is inert — browsers ignore `<metadata>`
and PNG ancillary chunks — but the favicon is fetched on every page load, so
shipping it means serving several KB of provenance record forever. Stripped on
the way out rather than at the source, so the record survives where the asset is
authored and never reaches the wire.

**The drift.** Two copies of an image in one repository diverge the moment
somebody edits one. `--check` compares both directions: every file this produces
must match what is committed, and every committed file must be one this produces.
A served icon quietly a version behind its source is exactly the kind of thing
nobody notices until it is wrong in a screenshot.

Icons are committed rather than generated during the image build, because the
Dockerfile's frontend stage is node:22-slim with no Python and cairosvg is in no
dependency group. Same reasoning as the synthetic fixture.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "resources"
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"

METADATA = re.compile(r"[ \t]*<metadata>.*?</metadata>\n?", re.S)
# The manifest declares its own namespace on the root element. Removing the
# manifest and leaving the declaration behind ships a logo that still names
# c2pa.org — a dangling reference to a host this app otherwise never mentions,
# and a puzzle for whoever reads the file next.
C2PA_NS = re.compile(r'\s+xmlns:c2pa="[^"]*"')

# What the app actually serves. Deliberately not everything in resources/:
# icon-512.png is for the README and a GitHub avatar, and favicon-16/32/48 are
# already bundled inside favicon.ico, so shipping them would be dead weight on
# a page that never asks for them.
SERVED = (
    "favicon.ico",                 # the tab, everywhere
    "unbagged-logo-small.svg",     # the tab, where SVG favicons are supported
    "apple-touch-icon-180.png",    # iOS home screen
    "unbagged-logo.svg",           # the first-run screen
)


def strip_svg(data: bytes) -> bytes:
    """Drop the C2PA manifest. Browsers ignore <metadata>; so should the wire."""
    text = METADATA.sub("", data.decode("utf-8"))
    return C2PA_NS.sub("", text).encode("utf-8")


def strip_png(data: bytes) -> bytes:
    """Re-save without ancillary chunks, which is where the manifest lives."""
    from PIL import Image

    # Pillow does not carry ancillary chunks across a save unless handed a
    # `pnginfo`, so re-saving is the strip. No pixel is touched.
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        out = io.BytesIO()
        image.save(out, "PNG", optimize=True)
    return out.getvalue()


def build_one(name: str) -> bytes:
    source = SOURCE_DIR / name
    if not source.is_file():
        raise SystemExit(f"build_brand: no source at {source.relative_to(REPO_ROOT)}")
    data = source.read_bytes()
    if name.endswith(".svg"):
        return strip_svg(data)
    if name.endswith(".png"):
        return strip_png(data)
    return data  # .ico carries no manifest; ship the bytes as authored


def build() -> dict[str, bytes]:
    return {name: build_one(name) for name in SERVED}


def run(check: bool = False) -> int:
    produced = build()
    problems: list[str] = []

    for name, data in produced.items():
        target = PUBLIC_DIR / name
        rel = target.relative_to(REPO_ROOT)
        if check:
            existing = target.read_bytes() if target.exists() else None
            if existing != data:
                problems.append(f"  DRIFT  {rel}")
            else:
                saved = (SOURCE_DIR / name).stat().st_size - len(data)
                print(f"  ok     {rel}  ({len(data):,}B, {saved:,}B of metadata dropped)")
        else:
            PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            saved = (SOURCE_DIR / name).stat().st_size - len(data)
            print(f"  wrote  {rel}  ({len(data):,}B, {saved:,}B of metadata dropped)")

    # The other direction: nothing served that this does not produce.
    if PUBLIC_DIR.is_dir():
        for stray in sorted(PUBLIC_DIR.iterdir()):
            if stray.is_file() and stray.name not in produced:
                problems.append(f"  STRAY  {stray.relative_to(REPO_ROOT)}")

    if not check:
        print(f"\nbuild_brand: wrote {len(produced)} file(s)")
        return 0

    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(
            f"\nbuild_brand: {len(problems)} problem(s). Either run `make brand`, or "
            "work out why\na served asset is not what the source produces — a stripped "
            "copy drifting from\nits source is an icon that is quietly a version behind.",
            file=sys.stderr,
        )
        return 1
    print(f"\nbuild_brand: {len(produced)} served asset(s) match their sources")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if a served asset differs from what the source produces")
    return run(check=parser.parse_args(argv).check)


if __name__ == "__main__":
    raise SystemExit(main())
