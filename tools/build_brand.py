#!/usr/bin/env python3
"""Produce the served brand assets in frontend/public/ from the sources in resources/.

    make brand          regenerate and write
    make brand-check    fail if a served file is not what this produces

Two reasons this is a build step rather than a copy.

**The metadata.** The source SVGs and PNGs carry a C2PA content-credential
manifest that dwarfs the artwork: 93% of `unbagged-logo-small.svg`, 90% of
`unbagged-logo.svg` and 64% of `apple-touch-icon-180.png`. Browsers ignore
`<metadata>` and PNG ancillary chunks, so it is inert; it also names `c2pa.org`
and carries a provenance record, in a build that reaches no other host and says
so in its own README. Stripped on the way out rather than at the source, so the
record survives where the asset is authored and never reaches the wire.

**The drift.** Two copies of an image in one repository diverge the moment
somebody edits one. `--check` compares both directions: every file this produces
must match what is committed, and every committed file must be one this produces.
A served icon quietly a version behind its source is exactly the kind of thing
nobody notices until it is wrong in a screenshot.

`--check` asserts a *property of the bytes*, not just equality with what is
committed. Both sides of an equality check run through this module, so equality
alone stays green forever if the stripper silently stops stripping — one
`<metadata id="...">` from an editor that writes attributes is enough. The
cleanliness assertions below are what actually hold the guarantee; the
equality check only catches drift.

PNGs are compared by decoded pixels rather than by byte, because the compressed
bytes are a property of whichever zlib the local Pillow wheel happens to bundle.
A byte comparison pins every contributor to one build of an image library, and
puts anyone on a platform with no Pillow wheel into permanent unfixable DRIFT on
a file they never touched.

Icons are committed rather than generated during the image build, because the
Dockerfile's frontend stage is node:22-slim with no Python and cairosvg is in no
dependency group. Same reasoning as the synthetic fixture.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "resources"
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"

# An editor that writes `<metadata id="metadata7">` (Inkscape does) is not
# matched by a bare-tag pattern, so the attribute case is part of the pattern
# rather than a thing the cleanliness check discovers later.
METADATA = re.compile(r"[ \t]*<metadata(?:\s[^>]*)?>.*?</metadata>\s*\n?", re.S)
# The manifest declares its own namespace on the root element. Removing the
# manifest and leaving the declaration behind ships a logo that still names
# c2pa.org — a dangling reference to a host this app otherwise never mentions,
# and a puzzle for whoever reads the file next.
C2PA_NS = re.compile(r'\s+xmlns:c2pa(?::\w+)?="[^"]*"')

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

# PNG chunks that carry pixels or the information needed to draw them. Anything
# else is where a manifest, an ICC description or an EXIF block lives.
PNG_PIXEL_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS"}

# An SVG is served same-origin as a navigable document, so it is a script
# execution context, not just a picture. These never appear in artwork; they
# appear when a generator or optimiser round-trip puts them there.
SVG_FORBIDDEN = (
    (re.compile(r"<metadata\b", re.I), "a <metadata> element"),
    (re.compile(r"c2pa", re.I), "a c2pa reference"),
    (re.compile(r"<script\b", re.I), "a <script> element"),
    (re.compile(r"<foreignObject\b", re.I), "a <foreignObject> element"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "an inline event handler"),
    (re.compile(r'(?:href|xlink:href)\s*=\s*["\'](?:https?:)?//', re.I), "an off-origin href"),
)


def strip_svg(data: bytes) -> bytes:
    """Drop the C2PA manifest. Browsers ignore <metadata>; so should the wire."""
    text = METADATA.sub("", data.decode("utf-8"))
    return C2PA_NS.sub("", text).encode("utf-8")


def strip_png(data: bytes) -> bytes:
    """Re-save without ancillary chunks, which is where the manifest lives."""
    from PIL import Image

    # Copy and empty `info` rather than rebuild from `tobytes()`. The rebuild
    # looked cleaner — nothing to re-emit because nothing was carried — but
    # `Image.frombytes` on a P-mode image attaches a *default* palette to what
    # `tobytes()` returned as palette indices, so a paletted icon came back
    # black. Same loss for tRNS transparency. `copy()` keeps the palette,
    # clearing `info` drops the manifest and Pillow's unprompted iCCP re-emit,
    # and transparency is put back by hand because it is pixel data wearing an
    # `info` key.
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        bare = image.copy()
        transparency = image.info.get("transparency")
        bare.info = {}
        if transparency is not None:
            bare.info["transparency"] = transparency
        out = io.BytesIO()
        bare.save(out, "PNG", optimize=True)
    return out.getvalue()


def png_chunks(data: bytes) -> list[bytes]:
    """The chunk types in a PNG stream, in order."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG stream")
    chunks: list[bytes] = []
    offset = 8
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset:offset + 4])
        kind = data[offset + 4:offset + 8]
        chunks.append(kind)
        offset += 12 + length
        if kind == b"IEND":
            break
    return chunks


def ico_png_streams(data: bytes) -> list[bytes]:
    """Every embedded PNG stream in an ICO. A modern .ico is a container of them."""
    streams: list[bytes] = []
    if len(data) < 6:
        return streams
    _, kind, count = struct.unpack("<HHH", data[:6])
    if kind != 1:
        return streams
    for index in range(count):
        entry = 6 + index * 16
        if entry + 16 > len(data):
            break
        size, offset = struct.unpack("<II", data[entry + 8:entry + 16])
        blob = data[offset:offset + size]
        if blob.startswith(b"\x89PNG\r\n\x1a\n"):
            streams.append(blob)
    return streams


def uncleanliness(name: str, data: bytes) -> list[str]:
    """What is wrong with these bytes, judged on their own and not by comparison.

    This is the check that survives the stripper breaking. Equality between two
    outputs of the same function is green whether or not the function still
    does anything.
    """
    faults: list[str] = []
    if name.endswith(".svg"):
        text = data.decode("utf-8", "replace")
        faults.extend(f"carries {why}" for pattern, why in SVG_FORBIDDEN if pattern.search(text))
    elif name.endswith(".png"):
        extra = sorted({c.decode("ascii", "replace") for c in png_chunks(data)} -
                       {c.decode("ascii") for c in PNG_PIXEL_CHUNKS})
        faults.extend(f"carries a {kind} chunk" for kind in extra)
    elif name.endswith(".ico"):
        if b"c2pa" in data:
            faults.append("carries a c2pa reference")
        for index, stream in enumerate(ico_png_streams(data)):
            extra = sorted({c.decode("ascii", "replace") for c in png_chunks(stream)} -
                           {c.decode("ascii") for c in PNG_PIXEL_CHUNKS})
            faults.extend(f"image {index} carries a {kind} chunk" for kind in extra)
    return faults


def png_pixels(data: bytes) -> tuple:
    """The part of a PNG that has to match. Not the compressed bytes.

    zlib output is a property of the library the local Pillow wheel bundles, so
    comparing it byte for byte makes CI a function of the contributor's platform.
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return (image.mode, image.size, image.tobytes(),
                tuple(image.getpalette() or ()), image.info.get("transparency"))


def equivalent(name: str, produced: bytes, committed: bytes) -> bool:
    if name.endswith(".png"):
        try:
            return png_pixels(produced) == png_pixels(committed)
        except Exception:
            return False
    return produced == committed


def build_one(name: str) -> bytes:
    source = SOURCE_DIR / name
    if not source.is_file():
        raise SystemExit(f"build_brand: no source at {source.relative_to(REPO_ROOT)}")
    data = source.read_bytes()
    if name.endswith(".svg"):
        return strip_svg(data)
    if name.endswith(".png"):
        return strip_png(data)
    if name.endswith(".ico"):
        # Today's .ico is clean because build_icons.py re-renders it from
        # cairosvg rather than assembling it from resources/favicon-*.png, each
        # of which carries a caBX chunk. That is a property of the current
        # authoring script, not of this one, so `uncleanliness` checks it rather
        # than a comment asserting it.
        return data
    # Not a default. A .webp or .jpg added to SERVED and quietly passed through
    # would ship its EXIF and XMP with --check green.
    raise SystemExit(f"build_brand: no strip rule for {name}; add one before serving it")


def build() -> dict[str, bytes]:
    return {name: build_one(name) for name in SERVED}


def served_files() -> tuple[list[str], list[str]]:
    """Every file under frontend/public/, and every symlink found on the way.

    `Path.rglob` yields a symlinked directory but does not descend it, so a
    symlink was a blind spot: `public/vendor -> ../../resources` passed --check
    while Vite's copyDir, which stats through symlinks, copied the raw
    manifest-bearing sources into the build. Walking with followlinks closes the
    hole; reporting the symlink itself closes the loop it would otherwise walk.
    """
    files: list[str] = []
    links: list[str] = []
    if not PUBLIC_DIR.is_dir():
        return files, links
    for parent, dirnames, filenames in os.walk(PUBLIC_DIR, followlinks=True):
        here = Path(parent)
        for name in list(dirnames):
            if (here / name).is_symlink():
                links.append((here / name).relative_to(PUBLIC_DIR).as_posix())
                dirnames.remove(name)
        for name in filenames:
            entry = here / name
            rel = entry.relative_to(PUBLIC_DIR).as_posix()
            (links if entry.is_symlink() else files).append(rel)
    return sorted(files), sorted(links)


def run(check: bool = False) -> int:
    produced = build()
    problems: list[str] = []

    for name, data in produced.items():
        rel = (PUBLIC_DIR / name).relative_to(REPO_ROOT)
        for fault in uncleanliness(name, data):
            problems.append(f"  UNSTRIPPED  {rel}  {fault}")

    for name, data in produced.items():
        target = PUBLIC_DIR / name
        rel = target.relative_to(REPO_ROOT)
        if check:
            if not target.exists():
                problems.append(f"  MISSING  {rel}")
                continue
            committed = target.read_bytes()
            if not equivalent(name, data, committed):
                problems.append(f"  DRIFT  {rel}")
                continue
            for fault in uncleanliness(name, committed):
                problems.append(f"  UNSTRIPPED  {rel}  {fault}")
            print(f"  ok     {rel}  ({len(committed):,}B)")
        else:
            PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
            # Writing through a symlink puts bytes wherever it points, which
            # may be outside the repository entirely.
            if target.is_symlink():
                target.unlink()
            target.write_bytes(data)
            shed = (SOURCE_DIR / name).stat().st_size - len(data)
            note = f"{shed:,}B of metadata dropped" if shed > 0 else "re-encoded"
            print(f"  wrote  {rel}  ({len(data):,}B, {note})")

    # The other direction: nothing served that this does not produce.
    files, links = served_files()
    problems.extend(f"  SYMLINK  frontend/public/{rel}" for rel in links)
    problems.extend(f"  STRAY  frontend/public/{rel}" for rel in files if rel not in produced)

    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(
            f"\nbuild_brand: {len(problems)} problem(s).\n"
            "  DRIFT / MISSING  run `make brand`\n"
            "  STRAY / SYMLINK  delete it; `make brand` writes the served files but\n"
            "                   removes nothing, so it cannot clear this for you\n"
            "  UNSTRIPPED       the stripper stopped matching; fix it before shipping\n"
            "                   the manifest it was there to remove",
            file=sys.stderr,
        )
        return 1

    if not check:
        print(f"\nbuild_brand: wrote {len(produced)} file(s)")
        return 0
    print(f"\nbuild_brand: {len(produced)} served asset(s) match their sources")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if a served asset differs from what the source produces")
    return run(check=parser.parse_args(argv).check)


if __name__ == "__main__":
    raise SystemExit(main())
