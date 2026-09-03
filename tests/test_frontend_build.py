"""Guarantees about the built UI that a code review would not catch.

These skip when the frontend has not been built. CI builds it first, so the
guarantees hold where it matters.
"""

import re
from pathlib import Path

import pytest

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
