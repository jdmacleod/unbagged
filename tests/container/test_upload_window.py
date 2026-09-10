"""The ten to thirty seconds between dropping a file and reading the report.

`#58` made an upload select the response it created, which is the fix that makes
this window matter: `<main>` is now replaced under the reader every time a file
lands. The adversarial pass on that change mapped what was still wrong around
it, and this module is that map, asserted in a browser.

Seven behaviours, all of them about the same window:

  * the upload lands on the view the reader is on WHEN IT LANDS, not the one
    they were on when they dropped the file (#59)
  * nothing renders the oldest response while the request list is re-read (#60)
  * an upload and a removal are mutations, not navigations, so neither leaves a
    history entry that Back cannot use (#61)
  * something is announced and focus goes somewhere, so the reader — especially
    one using a screen reader — knows the upload finished (#62)
  * a failed list read never deletes the report or the app (#63)
  * this tab finds out when another one removes a response (#65)
  * two events in one tick send one upload, not two (#48)

Why this tier. Every one of these is a claim about a sequence of real network
responses landing against a real DOM: a stale closure resolving 25 seconds after
it was created, a history entry, a focus move, an aborted fetch. `logic.test.ts`
covers the pure decisions underneath them — `resolveCurrent`, `isSameEntry`,
`announceUpload` — and cannot reach any of the wiring, because there is no DOM
renderer in the frontend suite.

`page.route()` fault injection appears here for the first time in this tier; the
#58 coverage audit recorded its absence as the reason #63 had no test.
"""

from __future__ import annotations

import json
import re
import urllib.request

import pytest

from tests.container.browser import (
    HMART,
    KROGER,
    LETTER,
    announced,
    panel,
    return_to_tab,
    selected,
    showing,
    upload,
    upload_again,
)
from tests.container.browser import (
    playwright_expect as expect,
)
from tests.container.conftest import requires_docker

pytestmark = [pytest.mark.container, requires_docker]

# One Pattern object, reused for every route/unroute.
#
# `page.unroute()` matches the handler by the pattern it was registered with,
# and `re.Pattern` defines no `__eq__` — so two `re.compile()` calls with the
# same literal only unroute each other because CPython's `re._cache` happens to
# hand back the identical object. That is an implementation detail to be
# depending on, and a typo in any one copy would break unroute silently.
REQUESTS_ROUTE = re.compile(r"/api/requests(\?.*)?$")


def _entries(page) -> int:
    """How many history entries this session has accumulated.

    The honest measure of "was that a navigation". A pushed entry that renders
    identically to the one before it is a Back press that appears to do nothing.
    """
    return page.evaluate("history.length")


def _rows(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/api/requests", timeout=10) as response:
        return json.load(response)["requests"]


def _remove_elsewhere(base: str, request_id: int) -> None:
    """Delete a response the way another tab would, without driving one.

    A second browser page would work and would be more literal, but the thing
    under test is what THIS tab does about it, and a second page adds a second
    set of timing to a test that is about the first. The app has no server-side
    session, so an API call is the same event.
    """
    request = urllib.request.Request(
        f"{base}/api/requests/{request_id}", method="DELETE"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status in (200, 204)


def _fail_list_reads(page) -> None:
    """Break `GET /api/requests` and nothing else.

    The POST that creates a response shares the path, so routing the path alone
    would break uploads too and the test would be asserting the wrong failure.
    """
    def handler(route, request):
        if request.method == "GET":
            route.abort("failed")
        else:
            route.continue_()

    page.route(REQUESTS_ROUTE, handler)


class TestTheViewTheUploadLandsOn:
    def test_an_upload_lands_on_the_tab_you_are_on_now(self, page):
        """Not the tab you were on when you dropped the file. Issue #59.

        `uploadFinished` is redefined every render and reaches `go()` → `href()`,
        which merged from render-time state. `Upload.send` captures its `onDone`
        at DROP time and calls it after `await api.upload(files)` — 10 to 30
        seconds for a real report. So the tab was replayed from the render where
        the drop happened, and any navigation during the wait was silently
        reverted: the reader drops a file on Products, wanders to Compliance to
        read while it parses, and is moved back to Products for a response they
        were not looking at.

        Reproduced without needing a slow parse. The bug is the stale closure,
        not the duration — a handler created on Products and invoked after a
        move to Compliance is the whole of it, and holding the file input's
        `change` until after the move is the same sequence with the seconds
        taken out.
        """
        upload(page, HMART)

        # Land on a tab, then hand the drop a closure created there.
        page.get_by_role("button", name=re.compile(r"^Products")).click()
        assert "tab=products" in page.url

        # Hold the POST open, so the move below happens with the upload in
        # flight — exactly the window `Upload.send` is awaiting in. The route is
        # STASHED rather than left unresolved-and-forgotten: releasing it by
        # hand below is the only deterministic release. `page.unroute()` does
        # happen to let a paused route go, but only as a side effect of the
        # interception list emptying (`_unroute_internal` never calls
        # `route.stop()`), and depending on that also emits an unhandled
        # CancelledError at page close.
        held: list = []
        page.route(
            REQUESTS_ROUTE,
            lambda route, request: (
                route.continue_() if request.method == "GET" else held.append(route)
            ),
        )
        page.set_input_files("input[type=file]", [str(KROGER)])
        page.wait_for_selector("text=Reading the response", timeout=30_000)
        assert held, "the POST was never actually held, so nothing was in flight"

        # The reader wanders off to read something while it parses.
        page.get_by_role("button", name=re.compile(r"^Compliance")).click()
        assert "tab=compliance" in page.url

        # Now let the upload land — the one drop, released, not a second one.
        held[0].continue_()
        page.wait_for_selector("select[aria-label]", timeout=120_000)
        page.wait_for_load_state("networkidle")

        assert "tab=compliance" in page.url, (
            "the upload replayed the tab from the render where the drop happened"
        )
        assert (
            page.get_by_role(
                "button", name=re.compile(r"^Compliance")
            ).get_attribute("aria-current")
            == "page"
        )

    def test_nothing_is_fetched_for_the_oldest_response_while_the_list_reloads(
        self, page, empty_app, tmp_path
    ):
        """Issue #60, asserted on the consequence rather than on the flash.

        `useAsync.reload` retains `data`, so after an upload sets the selection
        to the new id there is at least one render against the OLD rows. The new
        id was unrecognised there, the fallback fired, and `rows[0]` — the OLDEST
        response, since `list_requests` is `ORDER BY id` — rendered: a beat of
        the wrong retailer, a selector naming it, and a full round of view
        fetches against the wrong request id.

        The flash is milliseconds and racing it would flake. The wasted fetches
        are not: they either happened or they did not, and the network log says
        which. It also names the real cost, since each one is a query against a
        threadpool the synchronous upload endpoint is competing for.

        **Two responses before the one under test, and that is the whole trick.**
        Parked on the OLDEST — which is what one prior upload leaves you on —
        this test passes against the broken build: the pre-fix fallback resolves
        to `rows[0]`, the reader is already on `rows[0]`, so `current` never
        changes, `Timeline`'s key never changes, nothing remounts and no stray
        fetch is issued. The bug needs the reader parked somewhere the fallback
        would MOVE them away from.
        """
        upload(page, HMART)
        upload_again(page, KROGER)
        oldest = _rows(empty_app)[0]["id"]
        assert selected(page) != str(oldest), (
            "the reader must be parked off the oldest response, or the "
            "fallback has nowhere to move them and the test proves nothing"
        )

        seen: list[str] = []
        page.on("request", lambda r: seen.append(r.url))

        # A third response, so the settling window has two rows to fall back
        # through. The letter is a fresh file, which matters: a re-drop of one
        # already stored is refused, and an input already holding those exact
        # files fires no change event at all.
        letter = tmp_path / "third-response.txt"
        letter.write_text(LETTER, encoding="utf-8")
        page.set_input_files("input[type=file]", [str(letter)])
        page.wait_for_function(
            "n => document.querySelectorAll('select[aria-label] option').length >= n",
            arg=3,
        )
        page.wait_for_load_state("networkidle")

        # Views fetch `/api/requests/<id>/<view>`. Anything shaped like that for
        # the response being LEFT is a render that should never have happened.
        stray = [url for url in seen if re.search(rf"/api/requests/{oldest}/", url)]
        assert not stray, f"rendered the response being left behind: {stray}"


class TestUploadsAndRemovalsAreNotNavigations:
    def test_an_upload_leaves_no_history_entry_to_go_back_to(self, page):
        """Issue #61, the no-op Back.

        A session starts at `/` with no `?r=`, which already resolves to the only
        response through the `rows[0]` fallback. The first upload pushed
        `?tab=timeline&r=1`, so Back returned to the load entry and rendered
        exactly the same thing. The reader pressed Back, nothing happened,
        pressed again, and left the app.

        An upload is a mutation that happens to change what is on screen. The
        reader did not ask to go anywhere, so there is nowhere to go back to,
        and the entry count is the direct statement of that.
        """
        before = _entries(page)
        upload(page, HMART)
        # The URL still names it, which is what keeps a bookmark and a manual
        # reload landing correctly. It is the ENTRY that is not new.
        assert selected(page) != ""
        assert _entries(page) == before

        upload_again(page, KROGER)
        assert _entries(page) == before

    def test_back_never_lands_on_the_response_you_just_removed(self, page):
        """Issue #61, the dangling `?r=`.

        Upload, notice it was the wrong file, remove it — #58's own narrative,
        and the sequence its change makes most likely. The upload pushed `?r=7`
        and the removal pushed `?tab=timeline` on top, so Back landed on `?r=7`
        for a response that no longer existed. `resolveCurrent` kept it from
        crashing, but the URL then claimed id 7 while the view showed something
        else — the exact URL-versus-view divergence #58 exists to close — and a
        link copied from that state was wrong.

        The tab click in the middle is load-bearing. Uploads and removals no
        longer make entries at all, so without a real navigation to walk back to
        this would only assert that Back leaves the app, which is true of an
        empty history for reasons that have nothing to do with the bug. With it,
        the pre-fix history is five entries deep and Back lands squarely on the
        response that was just deleted.
        """
        upload(page, HMART)

        # A genuine navigation: this is the entry Back should find.
        page.get_by_role("button", name=re.compile(r"^Profile")).click()
        assert "tab=profile" in page.url

        upload_again(page, KROGER)
        removed = selected(page)
        assert removed != ""

        page.get_by_role("button", name="Remove this response").click()
        page.get_by_role("button", name="Remove Kroger").click()
        page.wait_for_selector("select[aria-label]", state="detached", timeout=30_000)
        page.wait_for_load_state("networkidle")

        page.go_back()
        page.wait_for_load_state("networkidle")

        assert selected(page) != removed, (
            "Back walked onto the response that was just deleted"
        )
        # And it landed somewhere real, rather than out of the app entirely.
        showing(page, "H Mart")

    def test_a_tab_you_are_already_on_is_not_a_new_entry(self, page):
        """The same phantom by another route.

        `go` replaces rather than pushes when the entry would land on what is
        already shown. Clicking the current tab is the reachable case, and it is
        one click, so a reader can stack a dozen of them without noticing and
        then find Back inert a dozen times.
        """
        upload(page, HMART)
        page.get_by_role("button", name=re.compile(r"^Profile")).click()
        settled = _entries(page)

        page.get_by_role("button", name=re.compile(r"^Profile")).click()
        page.get_by_role("button", name=re.compile(r"^Profile")).click()

        assert _entries(page) == settled
        assert "tab=profile" in page.url


class TestTheReaderIsToldTheUploadFinished:
    def test_the_outcome_is_announced_in_a_live_region(self, page):
        """Issue #62, the half that is invisible to everyone who can see.

        `grep` over `frontend/src` found scroll or focus handling only in
        Timeline's month rail and Compliance's draft field. Nothing ran on a
        response switch, and there was no `aria-live` region or `role="status"`
        anywhere in the app — so for a screen reader user the entire main region
        was replaced with no signal that anything had happened at all. The
        upload they started produced silence.
        """
        upload(page, KROGER)
        said = announced(page)
        assert said, "the upload finished and the app said nothing"
        assert "Read as Kroger" in said[-1]
        # The counts, so the announcement is the panel and not just its heading.
        assert "visits" in said[-1]
        assert "line items" in said[-1]

    def test_a_weak_match_is_announced_as_one(self, page, tmp_path):
        """The caveat is the part a reader who cannot see the panel most needs.

        A low-confidence match is the one case where the retailer named may not
        be the retailer meant. Saying "Read as …" and stopping would announce
        the claim and withhold the doubt.
        """
        letter = tmp_path / "response-letter.txt"
        letter.write_text(LETTER, encoding="utf-8")
        upload(page, letter)
        assert "uncertain match" in announced(page)[-1]

    def test_focus_moves_to_the_report(self, page):
        """The other half of #62, and the one sighted readers feel.

        The footer uploader means the reader is scrolled to the BOTTOM of a long
        document when they drop a file. The response that replaces `<main>` can
        be a very different length — a 25-row capped timeline against a "no
        purchase data" letter that renders three paragraphs — so the document
        height changes by a large factor and the browser clamps `scrollY` to
        somewhere arbitrary, including past the report being waited for.
        """
        upload(page, KROGER)
        focused = page.evaluate("document.activeElement?.innerText ?? ''")
        assert "Read as" in focused, (
            f"focus stayed where the reader was not: {focused[:120]!r}"
        )

    def test_a_refusal_is_announced_and_does_not_leave_a_success_behind(
        self, page
    ):
        """The failure path, which was silent twice over.

        `send()` never calls `onDone` on the error path, so the caller cannot
        announce a refusal — the error box is Upload's own state, and it carries
        `role="alert"` for that reason. The second half is the stale one: the
        polite region went on saying "Read as Kroger. 127 visits" underneath the
        refusal, which is the spoken version of the panel bug this suite already
        covers, and reads as though the second drop partly worked.
        """
        upload(page, KROGER)
        assert "Read as Kroger" in announced(page)[-1]
        said_before = len(announced(page))

        page.set_input_files("input[type=file]", [str(KROGER)])
        page.wait_for_selector("text=already loaded this response", timeout=120_000)

        assert (
            "already loaded this response"
            in page.locator("[role=alert]").inner_text()
        )
        # And the success is not still standing underneath it. Asked of the
        # RECORD rather than of the region's current text: the clear timer
        # empties that on its own, so reading it races the timer and passes for
        # the wrong reason whenever the round trip runs long.
        assert len(announced(page)) == said_before, (
            "the refusal produced a new announcement, or re-announced the "
            "success it replaced"
        )

    def test_a_removal_is_announced_too(self, page):
        """Removal replaces `<main>` the same way and was equally silent."""
        upload(page, HMART)
        upload_again(page, KROGER)
        page.get_by_role("button", name="Remove this response").click()
        page.get_by_role("button", name="Remove Kroger").click()
        page.wait_for_selector("select[aria-label]", state="detached", timeout=30_000)
        assert "Removed Kroger" in announced(page)[-1]


class TestAFailedListReadLosesNothing:
    def test_the_report_survives_a_failed_reload(self, page):
        """Issue #63, the whole of it.

        `useAsync`'s catch set `data: null`, so ANY failed `GET /api/requests` —
        a backend restart, a 500, a dropped connection during the reload that
        follows an upload — emptied `rows`. The reader lost the parse report they
        had just waited 10 to 30 seconds for and was returned to the first-run
        screen, for a response committed in the database and perfectly fine. And
        `reload` was wired to no retry control, so the only way out was a browser
        reload.
        """
        upload(page, KROGER)
        assert "Kroger" in panel(page)

        _fail_list_reads(page)
        # The list is re-read whenever the tab comes back; see #65 below. Any
        # trigger would do — this one needs no second response to exist.
        return_to_tab(page)
        page.wait_for_selector("text=could not be re-read", timeout=30_000)

        body = page.inner_text("body")
        assert "Read as" in body, "the failed read deleted the report"
        assert "Kroger" in panel(page)
        # And it did not collapse to the first-run screen for a response that
        # is sitting in the database.
        assert "Start with a retailer" not in body

    def test_a_failed_reload_offers_a_way_back(self, page):
        """The retry, and the recovery it actually performs."""
        upload(page, KROGER)
        _fail_list_reads(page)
        return_to_tab(page)
        page.wait_for_selector("text=could not be re-read", timeout=30_000)

        page.unroute(REQUESTS_ROUTE)
        page.get_by_role("button", name="Try again").click()
        page.wait_for_selector("text=could not be re-read", state="detached", timeout=30_000)
        assert "Kroger" in panel(page)

    def test_a_failed_first_read_does_not_claim_there_is_nothing(self, page):
        """The other side of the distinction, and the reason it is worth drawing.

        An empty list and a failed fetch rendered identically. "Start with a
        retailer's response" is a claim that there are none, and a read that
        failed is not evidence of that — the app is telling the reader their
        archive is empty on the strength of a dropped connection.
        """
        upload(page, KROGER)
        _fail_list_reads(page)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=Try again", timeout=30_000)

        body = page.inner_text("body")
        assert "Start with a retailer" not in body
        assert "Drop it here" not in body


class TestAFailedReadNeverSpeaksForDataItCannotSee:
    """The seams the #63 retention fix opened, found by the review army.

    Keeping data through a failed read is what stops a dropped connection
    deleting the reader's report. The cost is that "we could not look" now has
    to be told apart from "we looked and it is gone" everywhere the retained
    data is used — and in three places it was not.
    """

    def test_a_failed_read_does_not_resurrect_a_removed_response(self, page, empty_app):
        """The worst state the diff could reach, and it was reachable.

        A SUCCESSFUL read that does not contain the row is positive evidence the
        response is gone. The error arm ignored `rows` entirely, so it overrode
        that evidence rather than merely covering its absence: read succeeds and
        correctly hides the report, a later read FAILS, and the report returns —
        the app volunteering "Read as Kroger. 127 visits" out loud, into the live
        region, and re-stealing focus, for a response it watched disappear one
        read earlier. In an app built never to claim what it cannot see, that is
        the claim it must never make.
        """
        upload(page, KROGER)
        assert "Kroger" in panel(page)
        removed = int(selected(page))

        _remove_elsewhere(empty_app, removed)
        return_to_tab(page)
        page.wait_for_selector("text=Read as", state="detached", timeout=30_000)
        said_before = len(announced(page))

        # Now break the list. The evidence of absence must survive it.
        _fail_list_reads(page)
        return_to_tab(page)
        page.wait_for_selector("text=could not be re-read", timeout=30_000)

        body = page.inner_text("body")
        # A successful read already told us the archive is empty, so this is the
        # first-run invitation with a caveat, not the red "we know nothing" box.
        assert "Start with a retailer" in body
        assert "Read as" not in body, "a failed read brought a dead report back"
        assert "Kroger" not in body
        assert len(announced(page)) == said_before, (
            "the app re-announced a response it had already watched disappear"
        )

    def test_a_failed_read_does_not_wipe_the_response_the_url_asked_for(
        self, page, empty_app
    ):
        """A bookmark must survive a backend restart.

        The URL-correction effect guarded on `requests.loading` and `settling`
        and its comment claimed that excluded a failed read. It did not: a
        failure leaves `loading` false, `settling` cleared and `error` set, so it
        fell straight through, `rows` was empty, `current` was null, and the id
        the reader actually asked for was replaced out of the address bar. "Try
        again" then landed them on a different response with no record of the
        request.
        """
        upload(page, HMART)
        upload_again(page, KROGER)
        wanted = selected(page)
        assert wanted != ""

        _fail_list_reads(page)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=Try again", timeout=30_000)

        assert selected(page) == wanted, (
            "the failed read rewrote the address bar and lost the response the "
            "reader asked for"
        )

        # And recovering puts them back on it, rather than on the oldest.
        page.unroute(REQUESTS_ROUTE)
        page.get_by_role("button", name="Try again").click()
        page.wait_for_selector("select[aria-label]", timeout=30_000)
        assert selected(page) == wanted

    def test_a_failed_read_does_not_replace_a_running_upload_with_a_red_box(
        self, page
    ):
        """Red means "this is not recoverable". A live parse is not that.

        First run, drop a long file, alt-tab away and back while it parses: the
        tab-visibility refetch fires, the read fails, and the prominent uploader
        — spinner, elapsed seconds and all — was unmounted and replaced by the
        red box, for an upload that was perfectly fine and still going. It also
        destroyed `Upload`'s in-flight guard mid-POST, which is the mutex #48
        exists to hold.
        """
        held: list = []
        page.route(
            REQUESTS_ROUTE,
            lambda route, request: (
                route.abort("failed") if request.method == "GET" else held.append(route)
            ),
        )
        page.set_input_files("input[type=file]", [str(KROGER)])
        page.wait_for_selector("text=Reading the response", timeout=30_000)
        assert held, "the POST was never held, so no upload was in flight"

        # The reader tabs away and back while it parses. The read fails.
        return_to_tab(page)

        assert "Reading the response" in page.inner_text("body"), (
            "a failed list read unmounted a running upload"
        )

        held[0].continue_()
        page.wait_for_selector("text=Read as", timeout=120_000)

    def test_a_removal_is_not_offered_against_a_list_that_could_not_be_re_read(
        self, page
    ):
        """Retained data is rendered in one place and ACTED on in the same one.

        `RemoveRequest` issues an irreversible DELETE against whatever row the
        retained list hands it. Before retention a failed read emptied `rows` and
        this control vanished on its own; after it, the control stayed live
        underneath a caveat reassuring the reader that "the response you are
        reading is unaffected" while offering to delete it.
        """
        upload(page, KROGER)
        assert page.get_by_role("button", name="Remove this response").is_visible()

        _fail_list_reads(page)
        return_to_tab(page)
        page.wait_for_selector("text=could not be re-read", timeout=30_000)

        assert not page.get_by_role(
            "button", name="Remove this response"
        ).is_visible(), "offered an irreversible delete against a stale list"

        # And it comes back the moment the list can be read again.
        page.unroute(REQUESTS_ROUTE)
        page.get_by_role("button", name="Try again").click()
        expect(
            page.get_by_role("button", name="Remove this response")
        ).to_be_visible()

    def test_the_report_survives_the_reload_that_follows_its_own_upload(self, page):
        """The sequence `useAsync`'s own comment names as the motivating case.

        Both other failed-read tests break the list only after an upload has
        fully settled, so `settling` is already null and only the error arm is
        exercised. This is the state where `settling` and `error` are live at
        once — and it is what proves the read counter increments on the FAILURE
        path too, because otherwise `settling` never clears, the announcement
        never fires and the URL correction stays frozen for the session.
        """
        _fail_list_reads(page)  # GET only; the POST still goes through
        page.set_input_files("input[type=file]", [str(KROGER)])
        page.wait_for_selector("text=Read as", timeout=120_000)
        page.wait_for_selector("text=could not be re-read", timeout=30_000)

        assert "Kroger" in panel(page)
        assert selected(page) != "", "the new response lost its place in the URL"
        # And it does not invite the reader to start with a response, having
        # just taken one from them.
        assert "Start with a retailer" not in page.inner_text("body")
        assert "Add another response" in page.inner_text("body")
        # `settling` cleared, so the arrival effect ran despite the failure.
        assert announced(page) and "Read as Kroger" in announced(page)[-1]


class TestThisTabFindsOutAboutTheOtherOne:
    def test_a_response_removed_elsewhere_stops_being_offered(self, page, empty_app):
        """Issue #65.

        There was no cross-tab or background refresh anywhere in the frontend:
        `setInterval`, `storage`, `BroadcastChannel` and `visibilitychange`
        matched exactly one line, the seconds counter in the upload spinner. So
        tab A showing response 7 never learned that tab B had removed it —
        `rows` still held it, the selection stayed on it, and every view fetch
        404'd into an ErrorBox while the selector went on offering it. The only
        recovery was picking another response or reloading the page, neither
        signposted. `make reset` does the same to a single open tab.

        #58 aggravated it: the selection now lands on the newest response rather
        than drifting to `rows[0]`, so the reader is reliably parked on the row
        most likely to be the one they remove elsewhere.
        """
        upload(page, HMART)
        upload_again(page, KROGER)
        newest = int(selected(page))
        assert "Kroger" in page.inner_text("body")

        _remove_elsewhere(empty_app, newest)

        # Nothing has told this tab yet, which is the state the bug lived in.
        assert selected(page) == str(newest)

        return_to_tab(page)
        page.wait_for_selector("select[aria-label]", state="detached", timeout=30_000)
        page.wait_for_load_state("networkidle")

        assert selected(page) != str(newest), (
            "the URL went on naming a response this tab no longer has"
        )
        showing(page, "H Mart")


class TestOneDropIsOneUpload:
    def test_two_change_events_in_one_tick_send_one_request(self, page):
        """Issue #48.

        The in-flight flag was render state, and every entry point tested it out
        of a render closure, so two events dispatched before React re-rendered
        both read the stale `false` and both passed. Two POSTs went out; the
        loser's `finally` unlocked the drop zone while the winner was still in
        flight, and whichever `onDone` resolved last won the result panel. The
        server dedupes on content hash, so the damage is a confusing error and a
        possibly mismatched panel rather than a second copy — which is why this
        was filed as not urgent, not as not real.

        `change` is the sharpest version: `onDrop` and `onClick` at least tested
        `busy`, and `onChange` tested nothing at all, so the file-picker path
        double-submitted on any repeat event regardless of timing.

        **Both events go inside ONE `page.evaluate`, and that is the whole
        point.** Doing `set_input_files` and then a separate `page.evaluate` is
        two CDP round-trips with a full React render between them, so the second
        event arrives with `busy` already `true` — which a render-state guard
        catches perfectly well. That version passed against the bug. Dispatching
        twice in the same task is the only shape that distinguishes a ref from
        state, because it is the only one where React has not re-rendered in
        between, which is the entire reason #48 needed a ref.
        """
        posts: list[str] = []
        page.on(
            "request",
            lambda r: posts.append(r.url) if r.method == "POST" else None,
        )

        # Arm the input, then fire twice with nothing in between.
        page.set_input_files("input[type=file]", [str(KROGER)])
        page.evaluate(
            """() => {
              const el = document.querySelector('input[type=file]');
              const fire = () => el.dispatchEvent(new Event('change', { bubbles: true }));
              fire();
              fire();
            }"""
        )
        page.wait_for_selector("text=Add another response", timeout=120_000)
        page.wait_for_load_state("networkidle")

        assert len(posts) == 1, f"one drop sent {len(posts)} uploads: {posts}"
        # And the one that went out is the one whose report is on screen.
        assert "Kroger" in panel(page)

    def test_the_drop_zone_still_works_after_a_refusal(self, page):
        """The mutex has to be RELEASED, and nothing was asserting that.

        `inFlight` is a ref, so React does not reset it — only the `finally`
        does. If that reset ever leaks out, the drop zone is permanently dead
        and looks completely fine being dead: `busy` is render state and is back
        to `false`, so the box is enabled, the cursor is normal, and every later
        drop returns silently. Every test in this suite would stay green.

        The failure path is the one to check, because it is the one that does
        not go through the success return.
        """
        upload(page, KROGER)
        page.set_input_files("input[type=file]", [str(KROGER)])
        page.wait_for_selector("text=already loaded this response", timeout=120_000)

        # The other fixture, because an input already holding those exact files
        # fires no change event at all.
        upload_again(page, HMART)
        assert "H Mart" in panel(page)
