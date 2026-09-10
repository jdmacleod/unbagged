"""Shared harness for the browser tier: a real container, a real page.

Split out when a second module needed it. It lived inside `test_first_upload.py`
while that was the only browser test, and the alternative to moving it was a
second copy of a sixty-line container fixture — which is how two fixtures that
are supposed to start the same app drift into starting two different ones.

The `empty_app` and `page` fixtures themselves are in `conftest.py`, because
pytest resolves fixtures by name from there and importing them into each module
means importing a name nothing appears to use.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tests.container.conftest import REPO_ROOT

playwright_expect = pytest.importorskip("playwright.sync_api", reason="needs a browser").expect

KROGER = (
    REPO_ROOT / "src" / "unbagged" / "adapters" / "kroger" / "fixtures" / "synthetic_report.txt"
)
HMART = REPO_ROOT / "src" / "unbagged" / "adapters" / "hmart" / "fixtures" / "synthetic_history.xls"

# Boilerplate belonging to no retailer, written here rather than committed
# because that is exactly what it is for: nothing recognises it, so the fallback
# adapter takes it at 0.1 and the panel has to say the match is a guess. It also
# produces the one warning in the codebase that carries no locator, which is the
# other half of the `w.locator` conditional the bundle case covers.
# `tests/test_generic_adapter.py` builds its letter the same way at the unit tier.
LETTER = """\
Dear Customer,

Thank you for your request under the California Consumer Privacy Act. We have
reviewed our records and are responding within the statutory period.

The categories of personal information we collect include identifiers and
commercial information. We do not sell personal information to third parties.

Sincerely,
The Privacy Team
"""


def drop(page, *paths) -> None:
    """Put files on the upload input and guarantee a change event.

    `set_input_files` fires nothing when the input already holds exactly those
    files, and several tests here deliberately re-drop the same report to reach
    the duplicate-refusal path. Whether that fired at all depended on the
    `<Upload>` component having remounted in between — it is mounted at one
    position while there are no responses and another once there is one — so the
    second drop worked most of the time and hung for 120 seconds when the DOM
    query won the race.

    Clearing first makes the next assignment a change whatever the input was
    holding. Measured before this: five different tests in this tier failed that
    way across a day's runs, each passing in isolation and on re-run, which is
    the "fails opaquely" half of issue #51.
    """
    page.set_input_files("input[type=file]", [])
    page.set_input_files("input[type=file]", [str(x) for x in paths])


def upload(page, *paths) -> None:
    """Upload, then wait for the app to SETTLE — not for the panel to appear.

    Waiting on the panel alone asserts the wrong instant. It appears as soon as
    the parse returns, and the reload that follows swaps the whole first-run
    branch out and the report in; during that the panel is legitimately absent
    for a frame or two. The bug was that it never came back.

    "Add another response" renders only once a request exists, so it marks the
    far side of exactly the transition this regression is about. Asserting after
    it is asserting that the result survived the swap.
    """
    drop(page, *paths)
    page.wait_for_selector("text=Add another response", timeout=120_000)
    page.wait_for_load_state("networkidle")


def upload_again(page, *paths) -> None:
    """A second upload, waited on by the retailer selector.

    "Add another response" is already on screen by then, so it no longer marks
    anything. The selector renders only once a second response exists, which
    puts the wait on the far side of the second reload the same way.
    """
    drop(page, *paths)
    page.wait_for_selector("select[aria-label]", timeout=120_000)
    page.wait_for_load_state("networkidle")


def selected(page) -> str:
    """Which response the page is on, read from `?r=` rather than from the DOM.

    The URL is what `go()` writes and what a reload or a shared link replays, so
    it is the honest place to ask. It also answers at ONE response, where the
    retailer selector does not exist yet — the selector can only be asked once
    there are two, which is one upload too late for half of these.
    """
    return parse_qs(urlparse(page.url).query).get("r", [""])[0]


def panel(page) -> str:
    """Just the "Read as …" line, not the whole page.

    Every retailer already loaded is named in the selector above, so asserting
    against `body` cannot tell "the panel says H Mart" from "H Mart exists".
    """
    return page.locator("p", has_text="Read as").first.inner_text()


def announced(page) -> list[str]:
    """Everything the app has said out loud, in order.

    Recorded by an init script in the `page` fixture; see the note there for why
    the region's current text is the wrong thing to read.
    """
    return page.evaluate("window.__announced ?? []")


def return_to_tab(page) -> None:
    """The reader came back to this tab.

    A synthetic `visibilitychange` rather than a real backgrounding: Playwright
    cannot hide a page without a second one stealing focus, and the app listens
    for the event, not for the reason behind it. This is the only trigger the
    suite has for the cross-tab refresh, so it is worth naming once rather than
    spelling the JS out at each call site.
    """
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def showing(page, retailer: str) -> None:
    """Assert which response is on screen, via the destructive control.

    With one response loaded the retailer selector does not render and the
    timeline never prints the retailer's name, so there is nowhere else on the
    page that says which response is being shown. The confirmation names it.
    """
    assert "Start with a retailer" not in page.inner_text("body")
    page.get_by_role("button", name="Remove this response").click()
    playwright_expect(page.get_by_role("button", name=f"Remove {retailer}")).to_be_visible()
