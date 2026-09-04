"""Guarantees about the built UI that a code review would not catch.

These skip when the frontend has not been built. CI builds it first, so the
guarantees hold where it matters.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
STATIC = Path(__file__).parent.parent / "src" / "unbagged" / "static"
INDEX = STATIC / "index.html"

# An absolute URL in a src/href is a request to somewhere else. Anything the page
# needs must be same-origin, so these attributes must always start with "/".
ABSOLUTE_SRC = re.compile(r'(?:src|href)\s*=\s*"(?:https?:)?//', re.IGNORECASE)
CSS_REMOTE_URL = re.compile(r"url\(\s*['\"]?(?:https?:)?//", re.IGNORECASE)

pytestmark = pytest.mark.skipif(
    not INDEX.is_file(),
    reason="frontend not built; run `make build-frontend`",
)


def test_the_page_loads_nothing_from_another_origin():
    """Zero CDN requests, permanently.

    Not a performance preference. A request to a third party tells that third
    party you are reading a report about yourself right now, and the app has to
    work with the network cable pulled out.
    """
    assert not ABSOLUTE_SRC.search(INDEX.read_text(encoding="utf-8"))


def test_stylesheets_fetch_no_remote_fonts_or_images():
    for css in STATIC.rglob("*.css"):
        assert not CSS_REMOTE_URL.search(css.read_text(encoding="utf-8")), css.name


def test_no_font_files_are_shipped_at_all():
    # The cheapest way to keep "no external font requests" true forever is to
    # use a system stack and ship no font files to get wrong later.
    assert not list(STATIC.rglob("*.woff*"))
    assert not list(STATIC.rglob("*.ttf"))


def test_the_entry_point_is_served_from_our_own_assets():
    html = INDEX.read_text(encoding="utf-8")
    assert re.search(r'src="/assets/[^"]+\.js"', html)


def test_touch_targets_are_raised_on_coarse_pointers():
    """Every interactive element sits at 27-32px, against a 44px minimum.

    Keyed on `pointer: coarse` rather than viewport width on purpose: this is a
    dense data app and a narrow window on a laptop should keep the tight layout,
    while a phone gets targets a thumb can hit.

    Asserted against the built CSS because headless Chromium reports the host
    pointer rather than the emulated viewport, so the rule cannot be exercised by
    resizing the window. Verified once by hand by applying the same selector
    unconditionally: 11 elements under 44px went to 0, smallest 26px to 44px.
    """
    css = "".join(f.read_text(encoding="utf-8") for f in STATIC.rglob("*.css"))
    assert "pointer:coarse" in css.replace(" ", ""), (
        "the coarse-pointer touch-target block is gone"
    )
    assert "min-height:44px" in css.replace(" ", "")


def test_motion_is_reducible():
    """The app animates; a reduced-motion preference is not a styling whim."""
    css = "".join(f.read_text(encoding="utf-8") for f in STATIC.rglob("*.css"))
    assert "prefers-reduced-motion" in css


# The full form of this guard needs a layout engine: "no view scrolls the page
# horizontally at 375px" is a statement about rendered boxes. The project has no
# browser harness, so what follows pins the one cause that has actually bitten,
# twice, and is invisible in review.
def test_scroll_containers_establish_a_containing_block():
    """`.scroll-x` must be positioned, or absolutely positioned children escape.

    Compare scrolled the whole page sideways by 130px at 375px while the box it
    lived in scrolled correctly. The cause was not the table: it was Tailwind's
    `.sr-only`, which is `position: absolute`, resolving against the body
    because `.scroll-x` was `position: static`. Its x offset inside a scrolled
    480px track then counted toward the document's own scroll width, and a
    getBoundingClientRect sweep showed a 1px-wide screen-reader label as the
    widest thing on the page.

    One declaration fixes it and nothing in review would catch its removal.
    """
    css = "\n".join(f.read_text(encoding="utf-8") for f in STATIC.rglob("*.css"))
    assert ".scroll-x" in css, "the scroll-x utility is not in the shipped CSS"
    block = re.search(r"\.scroll-x\s*\{([^}]*)\}", css)
    assert block, "could not find the .scroll-x rule"
    body = block.group(1)
    assert "position:relative" in body.replace(" ", ""), (
        f".scroll-x must be position:relative, got: {body.strip()!r}"
    )
    assert "overflow-x:auto" in body.replace(" ", "")


def test_wide_tables_are_wrapped_in_a_scroll_container():
    """A fixed `min-w-[...]` track is wider than a phone by construction, so it
    has to sit inside something that scrolls. Checked per file: every source
    that sets one must also use `scroll-x`."""
    src = ROOT / "frontend" / "src"
    offenders = []
    for f in src.rglob("*.tsx"):
        text = f.read_text(encoding="utf-8")
        if re.search(r"min-w-\[\d", text) and "scroll-x" not in text:
            offenders.append(f.name)
    assert not offenders, f"fixed min-width with no scroll container: {offenders}"
