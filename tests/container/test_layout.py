"""No view scrolls the page sideways. Asserted in a real browser.

Twice now a view has scrolled the whole page horizontally on a phone and nothing
caught it. Compare overflowed by 130px and Timeline by 33px, both invisible in
review, and Timeline's was a *regression*: the fix had lived in a layout trick a
later rewrite replaced, so grepping for it still passed.

`tests/test_frontend_build.py` pins the two causes that are known — a scroll
container must establish a containing block, a fixed `min-w-[…]` must sit inside
one — and those are cheap and worth having. They cannot catch a cause nobody has
met yet, because horizontal overflow is a statement about rendered boxes and
neither a regex nor jsdom lays anything out.

So this drives Chromium. Three things make it worth its cost:

**It measures states, not routes.** The widest layouts appear after an
interaction: a basket expanded into its receipt table, the follow-up letter
drafted, a filter that matches nothing. A sweep of six URLs would miss all of
them.

**It forces a basket that does not foot.** The generator makes every basket foot
exactly — 0 of 127 in the committed fixture — so the widest form of the timeline
row, the one carrying a dotted marker and an "under by $8.14" note beside the two
amounts, never renders. A layout test against the fixture as-is would pass while
never exercising the row most likely to overflow. That is the fixture-fiction
pattern this project has hit four times, so the test creates the case rather than
hoping for it.

**It runs against the image**, not a dev server, because the image is what a user
runs and its bundle is built separately.

Skips loudly without the browser: `pip install -e ".[dev,browser]"` then
`playwright install chromium`.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.container.conftest import REPO_ROOT, docker, requires_docker

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason='needs a browser: pip install -e ".[dev,browser]" && playwright install chromium',
)

pytestmark = [pytest.mark.container, requires_docker]

FIXTURE = (
    REPO_ROOT / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)

# Published on loopback so the browser can reach it. The container's own port is
# not published by default, and every other test in this tier uses `docker exec`.
PORT = 8523
BASE = f"http://127.0.0.1:{PORT}"

VIEWPORTS = [
    (320, 800),   # the narrowest phone anyone still carries
    (375, 812),   # where both regressions were found
    (414, 896),
    (768, 1024),  # where the timeline row switches to one line
    (1280, 900),
]

VIEWS = ["timeline", "profile", "compliance", "compare", "prices", "products"]


def _wait_for_health(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as response:
                if json.load(response).get("status") == "ok":
                    return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    raise AssertionError(f"the app never answered on {BASE}")


def _upload_fixture() -> int:
    """Ingest the synthetic report so the views have something to lay out.

    Without this every view renders its empty state and the test measures six
    blank pages. `run_container` mounts a scratch data directory on purpose.
    """
    boundary = "----unbagged-layout-test"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="files"; '
            b'filename="synthetic_report.txt"\r\n',
            b"Content-Type: text/plain\r\n\r\n",
            FIXTURE.read_bytes(),
            f"\r\n--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="declared_retailer"\r\n\r\n',
            b"kroger\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        f"{BASE}/api/requests",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["request_id"]


def _make_one_basket_not_foot(name: str) -> None:
    """Give the timeline its widest row.

    The row gains a dotted left marker and an "under by $8.14" note beside the
    two money columns only when a basket's lines disagree with the total the
    retailer stated for it. The generator makes every basket foot, so this edits
    the stored total directly — the same shape as a real response, which is
    where the discrepancy actually comes from.

    Applied to the earliest transaction so it lands on the first page of rows.
    """
    docker(
        "exec",
        name,
        "python",
        "-c",
        "import sqlite3;"
        "c=sqlite3.connect('/data/db/unbagged.sqlite');"
        "c.execute('UPDATE txn SET total_pre_discount = total_pre_discount + 8.14 "
        "WHERE id = (SELECT MIN(id) FROM txn)');"
        "c.commit()",
    )


def _excess(page) -> int:
    return page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


@pytest.fixture(scope="module")
def seeded_app(request, image) -> str:
    """One container, one ingest, shared by every check in this module.

    Module-scoped because ingesting parses a 13,000-line report and starting a
    container costs seconds; the assertions are read-only. Depends on `image`
    so the tier's build runs first rather than assuming the tag exists.
    """
    name = f"unbagged-layout-{int(time.time())}"
    data = Path(request.config.rootdir) / ".pytest-layout-data"
    (data / "db").mkdir(parents=True, exist_ok=True)
    (data / "incoming").mkdir(parents=True, exist_ok=True)
    docker(
        "run", "-d", "--name", name,
        "-v", f"{data}:/data",
        "-p", f"127.0.0.1:{PORT}:8000",
        image,
    )
    try:
        _wait_for_health()
        _upload_fixture()
        _make_one_basket_not_foot(name)
        yield BASE
    finally:
        docker("rm", "-f", name, check=False)
        _remove_data_dir(data, image)


def _remove_data_dir(data: Path, image: str) -> None:
    """Delete the bind-mounted data directory, which the container owns by then.

    The entrypoint chowns /data to uid 10001 so the unprivileged app can write to
    it. On Linux that is a real chown, and the host user then cannot unlink
    anything under incoming/ — unlink needs write permission on the *directory*,
    and the directory now belongs to 10001. On Docker Desktop the file-sharing
    layer maps ownership back to the host user, so the chown is invisible from the
    host and a teardown that fails on every Linux machine passes on every
    developer's laptop. This tier only ever ran on laptops until the repository
    had somewhere to push to; its first run on a Linux runner failed here.

    So the removal happens where the ownership is: inside a container, as root.
    `find -mindepth 1 -delete` rather than `rm -rf /data/*`, which leaves dotfiles
    behind and cannot remove the mount point itself either way. The now-empty top
    directory was created by the host and is still the host's to remove.
    """
    docker(
        "run", "--rm", "--user", "0", "--entrypoint", "sh",
        "-v", f"{data}:/data", image,
        "-c", "find /data -mindepth 1 -delete",
        check=False,
    )
    shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(scope="module")
def browser(seeded_app):
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


class TestNoViewScrollsSideways:
    def test_every_view_at_every_width(self, browser, seeded_app):
        """Six views, five widths. The assertion is on the document, not on any
        element: wide content is allowed to scroll inside its own box, and does
        on Compare and the receipt. What is never allowed is the page moving."""
        failures = []
        for width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            try:
                for view in VIEWS:
                    page.goto(f"{seeded_app}/?tab={view}&r=1", wait_until="networkidle")
                    excess = _excess(page)
                    if excess > 0:
                        failures.append(f"{view} at {width}px overflows by {excess}px")
            finally:
                page.close()
        assert not failures, "\n".join(failures)

    def test_the_widest_timeline_row_fits(self, browser, seeded_app):
        """The row a basket that does not foot produces, which the fixture
        cannot make on its own. It carries a dotted marker and an extra note
        beside the two money columns, and it is the widest form of the row that
        broke twice."""
        page = browser.new_page(viewport={"width": 375, "height": 812})
        try:
            page.goto(f"{seeded_app}/?tab=timeline&r=1", wait_until="networkidle")
            marked = page.locator("main button[aria-expanded]").filter(
                has_text="under by"
            )
            assert marked.count() >= 1, (
                "no unreconciled row rendered; the setup that creates one has drifted, "
                "so this test is no longer measuring the case it exists for"
            )
            assert _excess(page) == 0
        finally:
            page.close()

    @pytest.mark.parametrize(
        "view,setup",
        [
            # The receipt table: the widest thing in the app, and it is supposed
            # to scroll inside its own box rather than move the page.
            ("timeline", lambda page: page.locator("main button[aria-expanded]").first.click()),
            # The follow-up letter unfurls a textarea and two controls.
            (
                "compliance",
                lambda page: page.get_by_role("button", name="Draft a follow-up").first.click(),
            ),
            # A filter matching nothing: the controls must stay, and the empty
            # state must not be wider than the page.
            (
                "products",
                lambda page: page.get_by_placeholder("product or UPC").fill("ZZZZZZ"),
            ),
            # The destructive confirmation, which is the widest button row.
            (
                "timeline",
                lambda page: page.get_by_role("button", name="Remove this response").click(),
            ),
        ],
    )
    def test_interaction_states(self, browser, seeded_app, view, setup):
        """Routes alone are not enough: the widest layouts only exist after an
        interaction, and a sweep of six URLs would miss all of them."""
        page = browser.new_page(viewport={"width": 375, "height": 812})
        try:
            page.goto(f"{seeded_app}/?tab={view}&r=1", wait_until="networkidle")
            setup(page)
            page.wait_for_timeout(600)
            assert _excess(page) == 0
        finally:
            page.close()


@requires_docker
class TestMonthRailNavigates:
    """The rail has to work twice.

    The roll renders 25 of 127 rows, so a jump to a month past that window has to
    reveal the rows before it scrolls. The first version did, and every jump after
    the first silently did nothing: revealing is a `setState` that returns the same
    limit once the rows are already there, React bails out of the re-render on an
    unchanged value, and the effect that performed the scroll never ran. Nothing in
    the unit tests could see it — the bug lived in the gap between a React bailout
    and the DOM, which is exactly what this tier is for.
    """

    def _rail(self, page):
        return page.locator('a[href^="#month-"]')

    def test_a_jump_reveals_the_rows_it_needs(self, browser, seeded_app):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(f"{seeded_app}/?tab=timeline&r=1", wait_until="networkidle")
            before = page.locator("button[aria-expanded]").count()
            assert before == 25, f"the roll should start at 25 rows, got {before}"

            rail = self._rail(page)
            rail.nth(rail.count() - 1).click()
            page.wait_for_timeout(500)
            after = page.locator("button[aria-expanded]").count()
            assert after > before, (
                f"jumping to the last month revealed nothing: still {after} rows, so "
                "the anchor was never mounted and the scroll went nowhere"
            )
        finally:
            page.close()

    def test_a_second_jump_also_moves_the_page(self, browser, seeded_app):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(f"{seeded_app}/?tab=timeline&r=1", wait_until="networkidle")
            rail = self._rail(page)
            rail.nth(rail.count() - 1).click()   # reveals; changes the limit
            page.wait_for_timeout(500)
            settled = page.evaluate("window.scrollY")

            # The regression: this one needs no reveal, so it used to no-op.
            target = rail.nth(11).get_attribute("href")
            rail.nth(11).click()
            page.wait_for_timeout(500)
            assert page.evaluate("window.scrollY") != settled, (
                "the second jump left the page where it was"
            )
            top = page.locator(target).bounding_box()["y"]
            assert 0 <= top <= 120, (
                f"{target} landed at y={top:.0f}px; a jump should put the month at the "
                "top of the viewport, below the running head"
            )
        finally:
            page.close()

    def test_the_running_head_names_the_month_you_are_in(self, browser, seeded_app):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(f"{seeded_app}/?tab=timeline&r=1", wait_until="networkidle")
            head = page.locator("div.sticky.top-0").first
            rail = self._rail(page)
            target = rail.nth(11).get_attribute("href")
            month_key = target.removeprefix("#month-")
            rail.nth(11).click()
            page.wait_for_timeout(500)

            # "Jan 25" for 2025-01: the head speaks the roll's own label, not the key.
            year = month_key[2:4]
            assert year in head.inner_text(), (
                f"the running head reads {head.inner_text()!r} after jumping to "
                f"{month_key}"
            )
            assert page.locator('a[aria-current="true"][href^="#month-"]').count() == 1
        finally:
            page.close()


@requires_docker
class TestIndexExport:
    """The saved index has to be a file a browser will open as an image.

    The unit tests cover the string this builds; nothing there can tell whether
    the result is a well-formed document, because checking that in vitest would
    mean adding jsdom to a feature whose entire justification is that it adds no
    dependency. So the check lives here, where a real browser decodes it.
    """

    def _save(self, page, seeded_app):
        page.goto(f"{seeded_app}/?tab=products&r=1", wait_until="networkidle")
        with page.expect_download() as caught:
            page.get_by_role("button", name="Save as an image").click()
        download = caught.value
        target = Path(tempfile.mkdtemp()) / (download.suggested_filename or "index.svg")
        download.save_as(target)
        return target

    def test_the_file_decodes_as_an_image(self, browser, seeded_app):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            saved = self._save(page, seeded_app)
            data_url = "data:image/svg+xml;base64," + base64.b64encode(
                saved.read_bytes()
            ).decode()
            size = page.evaluate(
                """url => new Promise(resolve => {
                    const img = new Image();
                    img.onload = () => resolve({w: img.naturalWidth, h: img.naturalHeight});
                    img.onerror = () => resolve(null);
                    img.src = url;
                })""",
                data_url,
            )
            assert size is not None, "the browser refused to decode the saved file"
            assert size["w"] > 0 and size["h"] > 0, size
            # An index of several hundred products is a tall document; a height
            # that collapsed to the heading would mean the entries never laid out.
            assert size["h"] > 400, f"the saved index is only {size['h']}px tall"
        finally:
            page.close()

    def test_it_contains_the_products_it_claims_to(self, browser, seeded_app):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            saved = self._save(page, seeded_app)
            svg = saved.read_text(encoding="utf-8")
            on_screen = page.locator("main a[href*='tab=timeline']").count()
            written = svg.count("<text")
            # Two <text> nodes are the heading and its subtitle.
            assert written - 2 >= on_screen > 0, (
                f"{on_screen} entries on screen, {written - 2} in the file — an index "
                "of everything you bought must not quietly drop entries"
            )
        finally:
            page.close()

    def test_the_saved_file_fetches_nothing(self, browser, seeded_app):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            svg = self._save(page, seeded_app).read_text(encoding="utf-8")
            without_namespace = re.sub(r'xmlns="[^"]*"', "", svg)
            assert "http://" not in without_namespace
            assert "https://" not in without_namespace
            for ref in ("<image", "xlink:href", "@import", "url("):
                assert ref not in svg, f"the saved index references {ref}"
        finally:
            page.close()
