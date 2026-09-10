"""Tests that run a real container.

Every other packaging test in this repository asserts file *text*: it parses the
compose YAML, or greps the Dockerfile for a string. Both of the packaging bugs
this tier was written after — a crash loop and a dead hot reload — passed all of
those while being completely broken, because both are runtime behaviours.

These are slow and need Docker, so they are marked and excluded from the default
`make test`. Run them with `make test-container`.

One honest limitation, marked rather than hidden. Docker Desktop for Mac and
Windows remap bind-mount ownership, so a directory that would be root-owned and
unwritable on Linux appears writable inside the container. Any test that depends
on real uid semantics is skipped there with a visible reason: a test that passes
because the platform cannot express the failure is worse than no test, since it
reports a guarantee it never checked.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
IMAGE = "unbagged:pytest"

pytestmark = pytest.mark.container


def docker(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


def have_docker() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        docker("info")
        return True
    except Exception:
        return False


def uid_semantics_are_real() -> bool:
    """True when a bind mount preserves host ownership, as it does on Linux.

    On Docker Desktop the VM's file sharing rewrites ownership, so a test for
    "the entrypoint takes ownership of a directory it does not own" cannot fail
    and therefore proves nothing.
    """
    return platform.system() == "Linux"


#: What to tell someone whose machine cannot run these. `--with-deps` matches
#: what ci.yml installs and is the part people miss: a downloaded Chromium still
#: fails to launch without the system libraries beside it.
NEEDS_A_BROWSER = (
    'needs a browser: pip install -e ".[dev,browser]" && playwright install --with-deps chromium'
)


def browser_is_installed() -> bool:
    """Is a Chromium actually downloaded — not merely `playwright` importable?

    `pytest.importorskip("playwright.sync_api")` proves the wheel is present and
    nothing more. A runner with the package and no downloaded browser sails past
    that skip and then dies at `chromium.launch()`, at which point the skip's own
    advice ("playwright install chromium") is no longer on screen. See #51.

    Answered from the filesystem rather than by asking Playwright. The obvious
    implementation opens a `sync_playwright()` context to read
    `chromium.executable_path`, and that is the wrong thing to do here: it costs
    most of a second at import, it leaves asyncio teardown noise behind, and this
    tier already has a rule that two live sync contexts in one thread make
    Playwright refuse the second. A check that runs before every session is a bad
    place to take that risk.

    Mirrors Playwright's own registry location. `PLAYWRIGHT_BROWSERS_PATH=0`
    means "beside the package", which is the one case worth deferring on — an
    install that deliberate is one we can assume completed.

    **This answers "downloaded", not "launchable", and that is deliberate.** A
    Chromium sitting there without the system libraries it needs will still fail
    at `launch()`. Skipping the whole browser tier on that would hide a broken
    image behind a green run, which is worse than the loud failure it replaces —
    a missing optional dependency is a reason to skip, a broken environment is
    not. Raised in review on #76; the advice above names `--with-deps` for it.
    """
    if "playwright" not in sys.modules:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override == "0":
        return True
    if override:
        root = Path(override)
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "ms-playwright"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    else:
        root = Path.home() / ".cache" / "ms-playwright"

    return any(root.glob("chromium-*")) if root.is_dir() else False


requires_browser = pytest.mark.skipif(
    not browser_is_installed(),
    reason=NEEDS_A_BROWSER,
)


#: This tier's own cap, raised from the 60s default in pyproject.toml.
#:
#: Measured over a full run: the slowest test is 17.6s of call plus 8.1s of
#: setup, so 180s is about seven times the real worst case. It also has to clear
#: the longest wait a test performs on itself — `wait_for_selector` is given
#: 120s in a few places — or pytest would kill a test that was still legitimately
#: waiting. A backstop above every internal deadline, not a competitor with them.
CONTAINER_TIMEOUT_SECONDS = 180


def pytest_collection_modifyitems(items):
    """Give every test in this directory the tier's timeout.

    Applied here rather than as a decorator on each test: a cap that has to be
    remembered per test is a cap that is missing from the next one written, which
    is the shape #51 is about.

    **Scoped by path, and that is not a detail.** pytest hands this hook every
    item in the session, not only the ones under the conftest that defines it —
    and the default run collects `tests/container/` before deselecting it by
    marker. Marking unconditionally therefore put 180s on every fast-tier test
    too, overriding the 60s default with a cap three times too generous, in a
    tier where the slowest test is 0.86s. Caught in review on #76.
    """
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.path).parents:
            item.add_marker(pytest.mark.timeout(CONTAINER_TIMEOUT_SECONDS))


requires_docker = pytest.mark.skipif(not have_docker(), reason="Docker is not available")
requires_real_uids = pytest.mark.skipif(
    not uid_semantics_are_real(),
    reason=(
        "bind-mount ownership is remapped on Docker Desktop, so this cannot fail "
        "here and would pass without testing anything. Run it on Linux."
    ),
)


@pytest.fixture(scope="session")
def image() -> str:
    """Build the image once for the whole tier."""
    if not have_docker():
        pytest.skip("Docker is not available")
    # --target runtime, explicitly. The Dockerfile's last stage is `dev`, and a
    # build with no target builds the last stage, so the tier would otherwise
    # test the dev image and report the production one as fine.
    docker("build", "--target", "runtime", "-t", IMAGE, str(REPO_ROOT), timeout=900)
    return IMAGE


@pytest.fixture
def run_container(image, tmp_path):
    """Start a container against a scratch data directory; clean up after."""
    started: list[str] = []

    def start(
        *extra: str,
        data: Path | None = None,
        wait: bool = True,
        read_only_data: bool = False,
    ) -> str:
        name = f"unbagged-test-{uuid.uuid4().hex[:8]}"
        data = data or (tmp_path / "data")
        data.mkdir(parents=True, exist_ok=True)
        # `:ro` on the bind itself, not --read-only on the container: the
        # container's own filesystem being read-only leaves the bind mount
        # writable, which is not the failure being tested.
        mount = f"{data}:/data:ro" if read_only_data else f"{data}:/data"
        docker("run", "-d", "--name", name, "-v", mount, *extra, image)
        started.append(name)
        if wait:
            for _ in range(60):
                state = json.loads(docker("inspect", name).stdout)[0]["State"]
                if state["Running"] or state["Status"] == "exited":
                    break
                time.sleep(0.5)
        return name

    yield start

    for name in started:
        docker("rm", "-f", name, check=False)


@pytest.fixture(scope="session", autouse=True)
def hand_back_scratch_ownership(tmp_path_factory) -> Iterator[None]:
    """Give the whole tier's scratch tree back, once, after the last test.

    Session-scoped deliberately. The first version of this ran per fixture
    teardown, which is per test — one extra container start each, and the tier
    went from 4m38 locally to 7m26 on CI, where it then tipped two tests over
    their own 120s selector waits. A tier whose documented failure mode is
    load-dependent timeouts is the wrong place to add sixty container starts.

    `tmp_path_factory.getbasetemp()` is the parent of every `tmp_path` this run
    created, so one pass covers all of them.
    """
    yield
    if not uid_semantics_are_real():
        return
    return_ownership(IMAGE, tmp_path_factory.getbasetemp())


def return_ownership(image: str, path: Path) -> None:
    """Give the scratch tree back to whoever pytest is running as.

    `docker/entrypoint.sh` chowns `/data` to uid 10001 whenever it does not
    already own it — which is always, for a fresh `tmp_path`. Through a bind
    mount that rewrites ownership on the HOST, so the tree pytest created is no
    longer pytest's. Nothing then cleans it up: `tmp_path` retention keeps the
    last few sessions and deletes older ones, and those deletes fail with
    PermissionError unless CI runs as root. Every run leaves more behind, each
    holding a SQLite database parsed from a 413 KB fixture. See issue #50.

    One throwaway root container per fixture teardown, not per container started.
    It uses the image already built for the tier rather than pulling another, and
    overrides the entrypoint so none of the app's own startup runs.

    Invisible on macOS and Windows, where Docker Desktop remaps bind-mount
    ownership and there is nothing to give back — which is exactly the
    "passes here, fails on Linux" trap this module's docstring warns about, so
    the guard is the same one `requires_real_uids` uses.
    """
    if not uid_semantics_are_real() or not path.exists():
        return
    docker(
        "run",
        "--rm",
        "--user",
        "0:0",
        "--entrypoint",
        "chown",
        "-v",
        f"{path}:/target",
        image,
        "-R",
        f"{os.getuid()}:{os.getgid()}",
        "/target",
        check=False,
    )


def wait_for_exit(name: str, seconds: int = 30) -> dict:
    """Block until the container is not running, then return its State."""
    for _ in range(seconds * 2):
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        if state["Status"] in ("exited", "dead"):
            return state
        time.sleep(0.5)
    return json.loads(docker("inspect", name).stdout)[0]["State"]


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


# No fixed port. `test_layout.py` can pin one because it holds a single
# module-scoped container; this fixture is function-scoped and starts a fresh
# container per test, and a fixed port turns that into three failure modes: a
# hard-killed pytest leaks a container still holding the port, so every later
# run fails on bind with an opaque CalledProcessError (`docker()` captures
# stderr); rm-to-release is not instantaneous, so back-to-back binds flake; and
# any `-n auto` collides by construction. Docker picks the port, the fixture
# reads it back. `run_container` above already avoids the same trap with uuid
# names and no published port.
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
    name = f"unbagged-browser-{uuid.uuid4().hex[:8]}"
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{data}:/data",
            # Empty host port: Docker assigns a free one, read back below.
            "-p",
            "127.0.0.1::8000",
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
    """A browser on an empty app.

    Function-scoped, including the browser launch — which costs a Chromium cold
    start per test and is the slowest thing in this tier. It was hoisted to
    session scope and reverted: `test_layout.py` opens its own
    `sync_playwright()` (module-scoped, tied to its seeded app), and two sync
    context managers cannot be alive in one thread. Playwright answers the
    second with "It looks like you are using Playwright Sync API inside the
    asyncio loop", which surfaces as 14 setup ERRORS in a module that passes
    perfectly well on its own. Sharing one browser across the tier means both
    modules sharing one context manager, which is a bigger change than the
    minutes are worth; filed rather than forced.

    The playwright import is inside the fixture rather than at module scope on
    purpose: this conftest is loaded for the whole container tier, and a
    top-level import would turn "no browser installed" into a collection error
    for the container tests that never open one.
    """
    playwright_api = pytest.importorskip(
        "playwright.sync_api",
        reason=NEEDS_A_BROWSER,
    )
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        try:
            context = instance.new_context()
            tab = context.new_page()
            tab.add_init_script(_RECORD_ANNOUNCEMENTS)
            tab.goto(empty_app, wait_until="networkidle")
            yield tab
            context.close()
        finally:
            instance.close()


# Every announcement the app has ever made, in order.
#
# The live region is cleared a second and a half after it is written, because a
# live region is a message rather than a status display — so reading its current
# text is a race, and a test that wins the race is asserting the wrong thing
# anyway. What makes a screen reader speak is the CHANGE, and a region populated
# once and never touched again announces nothing. This records the changes.
#
# An init script rather than a call after `goto`, so it survives `page.reload()`
# and is installed before the app's first render either way.
_RECORD_ANNOUNCEMENTS = """
window.__announced = [];
const attach = () => {
  const live = document.querySelector('[role=status]');
  if (!live) { requestAnimationFrame(attach); return; }
  new MutationObserver(() => {
    const said = live.innerText.trim();
    if (said) window.__announced.push(said);
  }).observe(live, { childList: true, characterData: true, subtree: true });
};
attach();
"""
