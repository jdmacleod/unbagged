"""The browser tier's drop helper, tested without a browser.

`drop()` stopped being a two-line wrapper the moment it grew a retry: it now
decides whether a drop reached a live handler, whether a send is already in
flight, and when to give up. That is branching logic guarding a diagnostic, and
a defect in it fails in the direction that hides problems — a helper that
wrongly reports success turns a real regression into a green run.

The browser tier itself cannot cover this. Every one of its call sites
exercises the path where the first attempt works; the race this recovers from
appeared about once per full-tier run and never on demand. So the page is faked
here and the branches are driven directly, in the fast tier, at no container
cost.
"""

from __future__ import annotations

import pytest

from tests.container.browser import BUSY_GRACE_MS, DROP_ATTEMPTS, drop


class FakePage:
    """Enough of a Playwright page for `drop()`, and nothing more.

    `sends` is the script: one entry per attempt, True meaning that attempt's
    `set_input_files` reaches a live handler and the browser issues the upload.
    `busy` is what `aria-busy` reports once the script is exhausted.
    """

    def __init__(self, sends: list[bool], *, busy: bool = False, late: bool = False):
        self.sends = list(sends)
        self.busy = busy
        self.late = late
        self.handlers: list = []
        self.attempts = 0
        self.slept_ms = 0

    # -- the surface `drop()` uses -------------------------------------------
    def on(self, event, handler):
        assert event == "request"
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        assert event == "request"
        self.handlers.remove(handler)

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_timeout(self, ms):
        self.slept_ms += ms
        # A late request: the send did start, its POST simply arrives after the
        # settle window. `drop()` must see it rather than re-dropping on top.
        if self.late and self.slept_ms > BUSY_GRACE_MS / 2:
            self.late = False
            self._fire()

    # -- the fake's own machinery --------------------------------------------
    def _fire(self):
        for handler in list(self.handlers):
            handler(FakeRequest())

    def _set_files(self):
        landed = self.sends.pop(0) if self.sends else False
        if landed:
            self._fire()


class FakeLocator:
    def __init__(self, page: FakePage, selector: str):
        self.page = page
        self.selector = selector

    def wait_for(self, **_):
        return None

    def count(self) -> int:
        assert self.selector == '[aria-busy="true"]'
        return 1 if self.page.busy else 0

    def set_input_files(self, files):
        # `drop()` clears before it assigns; only the assignment can land.
        if files:
            self.page.attempts += 1
            self.page._set_files()


class FakeRequest:
    method = "POST"
    url = "http://localhost:8420/api/requests"


class TestADropThatReachesALiveHandler:
    def test_the_first_attempt_is_the_only_one(self):
        page = FakePage([True])
        drop(page, "sc_030419.png")
        assert page.attempts == 1

    def test_it_does_not_pay_a_poll_before_looking(self):
        """The request usually beats the poll.

        Sleeping a tick before the first check taxed every green drop in the
        tier, at every one of this helper's call sites.
        """
        page = FakePage([True])
        drop(page, "sc_030419.png")
        assert page.slept_ms == 0

    def test_the_listener_is_removed_again(self):
        page = FakePage([True])
        drop(page, "sc_030419.png")
        assert page.handlers == []


class TestADropThatReachedNothing:
    def test_it_is_made_again(self):
        """The first attempt landed on an input React was replacing."""
        page = FakePage([False, True])
        drop(page, "sc_030419.png")
        assert page.attempts == 2

    def test_it_gives_up_by_name_rather_than_hanging(self):
        page = FakePage([False] * DROP_ATTEMPTS)
        with pytest.raises(AssertionError, match="no upload request after"):
            drop(page, "sc_030419.png")
        assert page.attempts == DROP_ATTEMPTS

    def test_the_listener_is_removed_even_then(self):
        page = FakePage([False] * DROP_ATTEMPTS)
        with pytest.raises(AssertionError):
            drop(page, "sc_030419.png")
        assert page.handlers == []


class TestADropOnTopOfALiveUpload:
    """The failure the ORDER of the checks exists to prevent.

    `send()` refuses a second file while one is in flight, so re-dropping on a
    live upload would replace the answer under test with "Still reading the last
    file" — and a request that merely arrived late would be credited to the
    attempt that had already re-dropped, which is two POSTs where
    `TestOneDropIsOneUpload` asserts one.
    """

    def test_a_late_request_is_waited_for_rather_than_re_dropped(self):
        page = FakePage([False], busy=True, late=True)
        drop(page, "sc_030419.png")
        assert page.attempts == 1, "a second drop would have been a second upload"

    def test_a_busy_page_that_never_sends_is_reported_not_believed(self):
        """A stuck `aria-busy` is the shape of the #48 mutex bug.

        Taking busy as proof of success on its own would turn that regression
        into a green run, which is the opposite of what this helper is for.
        """
        page = FakePage([False], busy=True)
        with pytest.raises(AssertionError, match="issue #48"):
            drop(page, "sc_030419.png")
        assert page.attempts == 1
