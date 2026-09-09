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

import pytest

from tests.container.conftest import REPO_ROOT, docker, requires_docker

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason='needs a browser: pip install -e ".[dev,browser]" && playwright install chromium',
)

pytestmark = [pytest.mark.container, requires_docker]

# Deliberately not test_layout.py's port: that module holds its container for
# the whole module and the two tiers can run in one session.
PORT = 8524
BASE = f"http://127.0.0.1:{PORT}"

KROGER = (
    REPO_ROOT / "src" / "unbagged" / "adapters" / "kroger"
    / "fixtures" / "synthetic_report.txt"
)
HMART = (
    REPO_ROOT / "src" / "unbagged" / "adapters" / "hmart"
    / "fixtures" / "synthetic_history.xls"
)


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


@pytest.fixture()
def empty_app(image, tmp_path) -> str:
    """A container with nothing ingested.

    Function-scoped and unseeded, which is the whole point: `seeded_app` in
    `test_layout.py` ingests before yielding, so the first-run uploader it would
    hand back has already been replaced by the footer one.
    """
    data = tmp_path / "data"
    (data / "db").mkdir(parents=True, exist_ok=True)
    (data / "incoming").mkdir(parents=True, exist_ok=True)
    name = f"unbagged-first-upload-{int(time.time())}"
    docker(
        "run", "-d", "--name", name,
        "-v", f"{data}:/data",
        "-p", f"127.0.0.1:{PORT}:8000",
        image,
    )
    try:
        _wait_for_health()
        yield BASE
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
