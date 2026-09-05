#!/usr/bin/env python3
"""Capture the README screenshots from a container seeded with the synthetic fixture.

    make screenshots

The screenshots in this repository are published. The data in them must therefore
be data nobody owns, and the only way to guarantee that is to never point this at
a database that could hold a real response. So it does not take a URL: it starts
its own container on a scratch data directory, ingests
`src/unbagged/adapters/kroger/fixtures/synthetic_report.txt`, captures, and
deletes the directory. A developer's own instance on :8420 is bind-mounted to
./data and is exactly what must not be photographed.

Needs Docker and Chromium (`make setup-browser`).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "src/unbagged/adapters/kroger/fixtures/synthetic_report.txt"
OUT_DIR = REPO_ROOT / "docs" / "screenshots"
SCRATCH = REPO_ROOT / ".screenshot-data"

IMAGE = "unbagged:screenshots"
PORT = 8531
BASE = f"http://127.0.0.1:{PORT}"

# One capture per view, at a width that shows the layout the README describes.
VIEWPORT = {"width": 1280, "height": 900}
VIEWS = ("timeline", "profile", "compliance", "prices", "products")


def docker(*args: str, check: bool = True, timeout: int = 900):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


def wait_for_health(timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as r:  # noqa: S310
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit("make_screenshots: the container never became healthy")


def upload_fixture() -> None:
    boundary = "----unbagged-screenshots"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="files"; '
        b'filename="synthetic_report.txt"\r\n',
        b"Content-Type: text/plain\r\n\r\n",
        FIXTURE.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    # noqa: S310 on both lines — BASE is a 127.0.0.1 literal built above, not input.
    request = urllib.request.Request(  # noqa: S310
        f"{BASE}/api/requests", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
        if response.status != 201:
            raise SystemExit(f"make_screenshots: ingest returned {response.status}")


def remove_scratch() -> None:
    """The entrypoint chowns the mount to uid 10001, so root inside a container is
    the only thing that can clear it on Linux. Same reasoning as the layout tier."""
    if SCRATCH.exists():
        docker("run", "--rm", "--user", "0", "--entrypoint", "sh",
               "-v", f"{SCRATCH}:/data", IMAGE,
               "-c", "find /data -mindepth 1 -delete", check=False)
        shutil.rmtree(SCRATCH, ignore_errors=True)


def capture() -> list[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "make_screenshots: needs Chromium. Run `make setup-browser`."
        ) from None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        for view in VIEWS:
            page.goto(f"{BASE}/?tab={view}&r=1", wait_until="networkidle")
            page.wait_for_timeout(400)   # let the unfurl animation settle
            target = OUT_DIR / f"{view}.png"
            page.screenshot(path=str(target), full_page=False)
            before = target.stat().st_size
            after = shrink(target)
            written.append(target)
            print(f"  wrote  {target.relative_to(REPO_ROOT)}  "
                  f"({after // 1024} KB, was {before // 1024})")
        browser.close()
    return written


def shrink(path: Path) -> int:
    """Palette-quantize the capture. Every clone pays for these bytes.

    The UI is flat by design — no gradients, no photographs, around 700 distinct
    colours in a full page — so a 256-colour palette is visually identical here
    and roughly halves the file. Measured on the timeline capture: mean channel
    delta 0.01, with the largest differences confined to text antialiasing where
    they are invisible at any size the README renders. Captured at 2x for legible
    type on a high-density display, which is what makes the shrink worth doing
    rather than dropping to 1x — downscaling saved only 23%.
    """
    from PIL import Image

    image = Image.open(path).convert("RGB")
    palette = image.quantize(
        colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    palette.save(path, optimize=True, compress_level=9)
    return path.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()

    if not FIXTURE.is_file():
        raise SystemExit(f"make_screenshots: no fixture at {FIXTURE}")
    if shutil.which("docker") is None:
        raise SystemExit("make_screenshots: needs Docker")

    print(f"make_screenshots: building {IMAGE}")
    docker("build", "--target", "runtime", "-t", IMAGE, str(REPO_ROOT))

    remove_scratch()
    (SCRATCH / "db").mkdir(parents=True, exist_ok=True)
    (SCRATCH / "incoming").mkdir(parents=True, exist_ok=True)

    name = f"unbagged-screenshots-{int(time.time())}"
    docker("run", "-d", "--name", name,
           "-v", f"{SCRATCH}:/data", "-p", f"127.0.0.1:{PORT}:8000", IMAGE)
    try:
        wait_for_health()
        print("make_screenshots: ingesting the synthetic fixture")
        upload_fixture()
        written = capture()
    finally:
        docker("rm", "-f", name, check=False)
        remove_scratch()

    print(f"\nmake_screenshots: {len(written)} screenshot(s) from synthetic data only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
