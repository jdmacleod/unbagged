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

from pathlib import Path
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


#: How many times a drop is retried when it produced no upload request.
#: Three is two more than the observed failures ever needed; the point is that
#: the retry exists at all, not the count.
DROP_ATTEMPTS = 3

#: How long to wait for the request a drop should have produced, in ms.
#: Issuing a `fetch` takes milliseconds, so this is margin rather than budget.
DROP_SETTLE_MS = 5_000

#: How long to wait once the page says it is BUSY, in ms. A send has started by
#: then, so this has to clear a real round trip — a long report is tens of
#: seconds of parsing — rather than the milliseconds a dispatch takes.
BUSY_GRACE_MS = 120_000

#: How long to wait for the file input to exist at all, in ms.
FIELD_ATTACH_MS = 30_000

#: Between polls of what the page has sent, in ms.
POLL_MS = 100


def _is_upload(request) -> bool:
    """The one POST this app makes to `/api/requests` is an upload."""
    return request.method == "POST" and request.url.rstrip("/").endswith("/requests")


def drop(page, *paths) -> None:
    """Put files on the upload input and confirm the app actually read them.

    Two failures live here, and the second one outlived the fix for the first.

    `set_input_files` fires nothing when the input already holds exactly those
    files, and several tests here deliberately re-drop the same report to reach
    the duplicate-refusal path. Clearing first makes the next assignment a
    change whatever the input was holding.

    What that did not fix is the DOM race underneath it. `<Upload>` is mounted
    at one position while there are no responses and another once there is one,
    so a drop arriving during the remount can land on an input React is in the
    middle of replacing: the event fires into a handler that has already been
    torn down, `send()` never runs, no request is issued, and the caller waits
    its full 120 seconds for an outcome nothing is coming for. Each such test
    passes alone and on re-run, which is the "fails opaquely" half of issue #51
    — and the half the clear-first fix left open. Measured across two full-tier
    runs of this suite: one failure each, a different test both times, both
    passing in isolation in twenty seconds.

    So the drop is no longer assumed to have happened. The upload request is
    watched for directly, because "did the browser POST" is the thing actually
    meant and it cannot be missed the way a transient spinner can.

    **A retry only ever happens when nothing is in flight.** The order below is
    load-bearing: request, then BUSY, then retry. `send()` refuses a second file
    while one is in flight, so re-dropping on top of a live upload would replace
    the answer under test with "Still reading the last file" — and a request
    that merely arrived late would be credited to the attempt that had already
    re-dropped, which is two POSTs where the suite asserts one. Asking the page
    whether it is busy before retrying is what rules both out.

    Busy is not taken as success on its own either. It says a send started, so
    the request is waited for rather than assumed — a stuck `aria-busy` (the
    shape of the #48 mutex bug this suite guards) would otherwise read here as a
    drop that worked.
    """
    sent: list[str] = []

    def _record(request) -> None:
        if _is_upload(request):
            sent.append(request.url)

    page.on("request", _record)

    # Re-resolved on every attempt rather than pinned to a handle: a remount is
    # the thing being recovered from, so the next try wants the NEW input.
    field = page.locator("input[type=file]")
    field.wait_for(state="attached", timeout=FIELD_ATTACH_MS)

    try:
        for _ in range(DROP_ATTEMPTS):
            before = len(sent)
            field.set_input_files([])
            field.set_input_files([str(x) for x in paths])
            if _sent_since(page, sent, before, DROP_SETTLE_MS):
                return
            if page.locator('[aria-busy="true"]').count():
                if _sent_since(page, sent, before, BUSY_GRACE_MS):
                    return
                raise AssertionError(
                    "the drop zone reports itself busy but issued no upload request — "
                    "the in-flight flag is stuck, which is the shape of issue #48"
                )
    finally:
        # By reference, so this removes THIS call's handler and not whichever
        # one happens to be last — several tests register their own.
        page.remove_listener("request", _record)

    raise AssertionError(
        f"the file input produced no upload request after {DROP_ATTEMPTS} drops of "
        f"{', '.join(Path(x).name for x in paths)} — the change event reached no "
        "live handler, so nothing was ever sent"
    )


def _sent_since(page, sent: list[str], before: int, timeout_ms: int) -> bool:
    """Has a new upload request gone out since `before`?

    Checked BEFORE the first sleep, because the request usually beats the poll:
    waiting a tick first put a fixed tax on every green drop in the tier, at
    every one of this helper's call sites.

    A recorded list rather than `page.expect_request`, which both specialists
    reached for and which is the cleaner primitive for the fast path. It only
    ever sees events that arrive after it starts listening, so a request landing
    between the settle window closing and the BUSY branch opening would be
    invisible to it — exactly the late arrival this function exists to see.
    """
    waited = 0
    while True:
        if len(sent) > before:
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(POLL_MS)
        waited += POLL_MS


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
