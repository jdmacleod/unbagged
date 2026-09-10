"""The first upload reports on itself. Asserted in a real browser.

Regression: ISSUE-001 — the prominent first-run uploader and the footer one are
different positions in the React tree, one inside `<main>` and one after it, so
no state survives between them. A first upload is precisely what swaps one for
the other, which meant the panel describing that upload was created and
destroyed in the same instant.
Found by /qa on 2026-09-09 against the v0.13.0 image.
Report: .gstack/qa-reports/qa-report-release-0.13.0-2026-09-09.md

What was lost is the only report a person ever gets on the parse: which retailer
matched, the "check this is the retailer you meant" caveat on a weak match, and
every `ParseWarning` the adapter raised. The warning that matters most here is
the one a bundle holding two retailers produces — the adapter keeps the response
it recognises and names the file it did not use, and that line was destroyed
before it could be read. A second upload showed all of it correctly, which is
why nothing noticed: the broken path is the one every user takes exactly once.

Why this tier. The bug is not in a function, it is in where state lives, so no
unit test can reach it: `logic.test.ts` tests pure functions and there is no DOM
renderer in the frontend suite. It needs a real browser, a real upload and a
genuinely empty app, which is what this module builds.

Both fixtures are synthetic and committed. Uploading them together is the
two-retailer bundle case exactly: the H Mart sniff scores 0.9 against Kroger's
0.8, so H Mart wins the bundle and the Kroger file is named in a warning.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

import pytest

from tests.container.conftest import REPO_ROOT, docker, requires_docker

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason='needs a browser: pip install -e ".[dev,browser]" && playwright install chromium',
)

pytestmark = [pytest.mark.container, requires_docker]

# No fixed port. `test_layout.py` can pin one because it holds a single
# module-scoped container; this fixture is function-scoped and starts a fresh
# container per test, and a fixed port turns that into three failure modes: a
# hard-killed pytest leaks a container still holding the port, so every later
# run fails on bind with an opaque CalledProcessError (`docker()` captures
# stderr); rm-to-release is not instantaneous, so back-to-back binds flake; and
# any `-n auto` collides by construction. Docker picks the port, the fixture
# reads it back. `conftest.run_container` already avoids the same trap with
# uuid names and no published port.

KROGER = (
    REPO_ROOT / "src" / "unbagged" / "adapters" / "kroger"
    / "fixtures" / "synthetic_report.txt"
)
HMART = (
    REPO_ROOT / "src" / "unbagged" / "adapters" / "hmart"
    / "fixtures" / "synthetic_history.xls"
)

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


def _wait_for_health(base: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
                if json.load(response).get("status") == "ok":
                    return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    raise AssertionError(f"the app never answered on {base}")


@pytest.fixture()
def empty_app(image, tmp_path) -> Iterator[str]:
    """A container with nothing ingested.

    Function-scoped and unseeded, which is the whole point: `seeded_app` in
    `test_layout.py` ingests before yielding, so the first-run uploader it would
    hand back has already been replaced by the footer one.

    The `try` opens BEFORE the run so a container that starts and then reports
    failure is still torn down; wrapping only the wait leaks it by name.
    """
    data = tmp_path / "data"
    (data / "db").mkdir(parents=True, exist_ok=True)
    (data / "incoming").mkdir(parents=True, exist_ok=True)
    name = f"unbagged-first-upload-{uuid.uuid4().hex[:8]}"
    try:
        docker(
            "run", "-d", "--name", name,
            "-v", f"{data}:/data",
            # Empty host port: Docker assigns a free one, read back below.
            "-p", "127.0.0.1::8000",
            image,
        )
        # `docker port` answers "<host>:<port>", and the host half varies by
        # daemon. Only the port is wanted: the publish above already pinned the
        # interface to loopback, so reusing the reported host would just be a
        # second, less reliable source for something already decided.
        published = docker("port", name, "8000/tcp").stdout.strip().splitlines()[0]
        base = f"http://127.0.0.1:{published.rsplit(':', 1)[-1]}"
        _wait_for_health(base)
        yield base
    finally:
        docker("rm", "-f", name, check=False)


@pytest.fixture()
def page(empty_app):
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        try:
            tab = instance.new_page()
            tab.goto(empty_app, wait_until="networkidle")
            yield tab
            tab.close()
        finally:
            instance.close()


def _upload(page, *paths) -> None:
    """Upload, then wait for the app to SETTLE — not for the panel to appear.

    Waiting on the panel alone asserts the wrong instant. It appears as soon as
    the parse returns, and the reload that follows swaps the whole first-run
    branch out and the report in; during that the panel is legitimately absent
    for a frame or two. The bug was that it never came back.

    "Add another response" renders only once a request exists, so it marks the
    far side of exactly the transition this regression is about. Asserting after
    it is asserting that the result survived the swap.
    """
    page.set_input_files("input[type=file]", [str(x) for x in paths])
    page.wait_for_selector("text=Add another response", timeout=120_000)
    page.wait_for_load_state("networkidle")


def _upload_again(page, *paths) -> None:
    """A second upload, waited on by the retailer selector.

    "Add another response" is already on screen by then, so it no longer marks
    anything. The selector renders only once a second response exists, which
    puts the wait on the far side of the second reload the same way.
    """
    page.set_input_files("input[type=file]", [str(x) for x in paths])
    page.wait_for_selector("select[aria-label]", timeout=120_000)
    page.wait_for_load_state("networkidle")


def _panel(page) -> str:
    """Just the "Read as …" line, not the whole page.

    Every retailer already loaded is named in the selector above, so asserting
    against `body` cannot tell "the panel says H Mart" from "H Mart exists".
    """
    return page.locator("p", has_text="Read as").first.inner_text()


class TestTheFirstUploadReportsOnItself:
    def test_a_bundle_holding_two_retailers_names_the_file_it_dropped(self, page):
        """The failure this was written for.

        Uploading both fixtures keeps H Mart and discards Kroger entirely. If
        the only line saying so is unmounted with the uploader that produced
        it, a person sees a complete-looking H Mart report and no indication
        that a whole second response went missing.
        """
        _upload(page, KROGER, HMART)
        body = page.inner_text("body")
        assert "Read as" in body
        assert "H Mart" in body
        # Named, not merely counted. The filename is what tells the reader
        # WHICH response is missing from the report they are about to read.
        assert KROGER.name in body
        assert "nothing from it is in this report" in body

    def test_the_match_and_its_confidence_survive(self, page):
        """The weak-match caveat rides the same panel.

        A first upload is the one most likely to be the wrong file, so losing
        "check this is the retailer you meant" is worse here than anywhere.
        """
        _upload(page, HMART)
        body = page.inner_text("body")
        assert "Read as" in body
        assert "H Mart" in body
        assert "%" in body

    def test_the_counts_survive(self, page):
        """`67 visits · 0 line items · …` — the parse in one line.

        Zero line items is the H Mart shape and is worth seeing at upload time:
        it is the difference between a response that was read badly and one
        that never itemised anything.
        """
        _upload(page, HMART)
        body = page.inner_text("body")
        # The joined form, not the bare words: "visits" and "line items" also
        # label the Timeline's own stats, so asserting them alone passes
        # against the broken build. This line exists only in the upload panel.
        assert re.search(
            r"[\d,]+ visits · [\d,]+ line items · \d+ identifiers", body
        ), body[:400]

    def test_a_single_clean_upload_reports_no_warnings(self, page):
        """The other side of it: nothing invented when nothing went wrong."""
        _upload(page, KROGER)
        body = page.inner_text("body")
        assert "Read as" in body
        assert "nothing from it is in this report" not in body

    def test_nothing_is_reported_before_anything_is_uploaded(self, page):
        """The null case, which is the state the panel starts in.

        `lastUpload` begins null and the prominent uploader is handed it, so a
        pristine first run must show the invitation and no report. Worth
        asserting rather than assuming: the panel is now fed by state that
        outlives every uploader on the page, and a report with nothing behind it
        would be a report about a parse that never happened.
        """
        body = page.inner_text("body")
        assert "Start with a retailer" in body
        assert "Read as" not in body

    def test_a_weak_match_is_reported_as_a_guess(self, page, tmp_path):
        """The caveat, on the upload most likely to need it.

        A letter belongs to no retailer, so the fallback adapter takes it at 0.1
        against a threshold of 0.25. That is the branch where the panel stops
        saying "match" and says "uncertain match" instead, and adds the sentence
        telling the reader to check the file before believing anything in it —
        the sentence that mattered most on a first upload and was the first
        thing the unmount destroyed.
        """
        letter = tmp_path / "response-letter.txt"
        letter.write_text(LETTER, encoding="utf-8")
        _upload(page, letter)
        body = page.inner_text("body")
        assert "uncertain match" in body
        assert "10%" in body
        assert "Low confidence." in body

    def test_a_warning_with_no_locator_renders_without_one(self, page, tmp_path):
        """The other half of the `w.locator` conditional.

        The bundle case covers a warning that names a file. The fallback
        adapter's warning names nothing, and a null locator rendering as an
        empty mono span — or as the word "null" — is the failure mode. Asserted
        on where the line ENDS, because that is where the locator would appear.
        """
        letter = tmp_path / "response-letter.txt"
        letter.write_text(LETTER, encoding="utf-8")
        _upload(page, letter)
        lines = [
            text
            for text in page.eval_on_selector_all("li", "els => els.map(e => e.innerText)")
            if "No adapter recognised this response" in text
        ]
        assert lines, "the fallback adapter's warning never reached the panel"
        assert lines[0].strip().endswith("docs/writing-an-adapter.md.")


class TestTheUploadsAfterTheFirst:
    """The footer uploader, which is where every later upload happens.

    It is the half of the swap that survives, so it is also the half whose
    behaviour changed least — but the result it renders is now somebody else's
    state, and these are the paths where that distinction can go wrong.
    """

    def test_a_second_upload_replaces_the_report(self, page):
        """State held by the parent still has to be state, not a one-shot.

        Hoisting `result` out of `Upload` is only correct if the footer uploader
        can still overwrite it. If `onDone` stopped landing — or landed on the
        wrong owner — the panel would sit on the first upload's report for the
        rest of the session, and the reader would be looking at a description of
        Kroger while reading H Mart.
        """
        _upload(page, KROGER)
        assert "Kroger" in _panel(page)
        _upload_again(page, HMART)
        panel = _panel(page)
        assert "H Mart" in panel
        assert "Kroger" not in panel

    def test_a_refused_upload_says_why(self, page, empty_app):
        """The error path, which shares the panel with the report.

        Dropping the same report twice is the commonest way to reach it — a long
        parse gives no immediate sign of progress, so people drop the file again
        — and the server refuses it by name. The refusal has to be shown; a
        silent no-op reads as the second drop having worked.
        """
        _upload(page, KROGER)
        assert "Read as" in page.inner_text("body")

        page.set_input_files("input[type=file]", [str(KROGER)])
        page.wait_for_selector("text=already loaded this response", timeout=120_000)
        body = page.inner_text("body")
        assert "remove the existing one" in body

        # The refusal must stand ALONE. Leaving the previous success panel
        # beside it reads as the second drop having partly worked, which is the
        # opposite of what happened — and the report now outlives the uploader,
        # so nothing clears it unless `send()` says so.
        assert "Read as" not in body

        # One request, not two. Asked of the server rather than of the DOM: the
        # retailer selector only appears at two responses, so its absence would
        # also be satisfied by a second request whose reload had not yet landed.
        with urllib.request.urlopen(f"{empty_app}/api/requests", timeout=10) as r:
            assert len(json.load(r)["requests"]) == 1

    def test_removing_the_last_response_clears_the_panel(self, page):
        """The other end of the result's new lifetime.

        Caught reviewing the fix rather than by it: moving the result to `App`
        made it outlive the uploader, which is the point, but it also made it
        outlive the RESPONSE. Removing the last one drops the reader back to
        the first-run screen, and a panel there reading "Read as H Mart ·
        108 visits" describes something that was just deleted — a claim about
        a response that no longer exists, in an app built not to make them.
        """
        _upload(page, HMART)
        assert "Read as" in page.inner_text("body")

        page.get_by_role("button", name="Remove this response").click()
        page.get_by_role("button", name="Remove H Mart").click()
        page.wait_for_selector("text=Drop it here", timeout=30_000)

        body = page.inner_text("body")
        # The empty state is correct; the panel above it must not survive.
        assert "Drop it here" in body
        assert "Read as" not in body
