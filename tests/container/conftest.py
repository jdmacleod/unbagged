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
import platform
import shutil
import subprocess
import time
import uuid
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


requires_docker = pytest.mark.skipif(
    not have_docker(), reason="Docker is not available"
)
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


def wait_for_exit(name: str, seconds: int = 30) -> dict:
    """Block until the container is not running, then return its State."""
    for _ in range(seconds * 2):
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        if state["Status"] in ("exited", "dead"):
            return state
        time.sleep(0.5)
    return json.loads(docker("inspect", name).stdout)[0]["State"]
