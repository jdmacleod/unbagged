"""What the container actually does when it runs.

Each test here corresponds to a guarantee that a text assertion cannot make.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from tests.container.conftest import (
    REPO_ROOT,
    docker,
    requires_docker,
    requires_real_uids,
    wait_for_exit,
)

pytestmark = [pytest.mark.container, requires_docker]


class TestPrivileges:
    def test_the_app_process_runs_as_uid_10001(self, run_container):
        """The guarantee `test_the_container_does_not_run_as_root` used to make.

        That test grepped the Dockerfile for `USER unbagged`. The Dockerfile no
        longer has that line, because the entrypoint must start as root to chown
        the bind mount before dropping privileges. Grepping would now either fail
        or, if someone deleted the assertion to make it pass, silently stop
        checking anything. The effective uid of the running process is the thing
        that was always meant.
        """
        name = run_container()
        # /proc/1, not os.getuid(): `docker exec` starts a new process as the
        # image's USER (root, since the entrypoint needs it), so asking the exec
        # session its own uid measures the wrong process entirely. PID 1 is the
        # app.
        status = docker("exec", name, "cat", "/proc/1/status").stdout
        uid_line = next(ln for ln in status.splitlines() if ln.startswith("Uid:"))
        effective = uid_line.split()[1]
        assert effective == "10001", (
            f"PID 1 runs as uid {effective}, expected 10001 — privileges were not dropped"
        )

    def test_the_shipped_image_is_the_runtime_stage_not_dev(self, run_container):
        """The dev stage is last in the Dockerfile, so a target-less build picks
        it. That would ship an editable install and --reload as production."""
        name = run_container()
        logs = docker("logs", name).stdout + docker("logs", name).stderr
        assert "Will watch for changes" not in logs, (
            "the dev stage is running: docker-compose.yml must pin target: runtime"
        )
        editable = docker(
            "exec", name, "python", "-c",
            "import unbagged, os; print(os.path.dirname(unbagged.__file__))",
        ).stdout.strip()
        assert "site-packages" in editable, f"non-production install at {editable}"


class TestDataDirectory:
    @requires_real_uids
    def test_a_root_owned_data_directory_is_adopted(self, run_container, tmp_path):
        """The Linux first-run case: `./data` does not exist, the daemon creates
        it as root, and the app runs as uid 10001."""
        data = tmp_path / "rootowned"
        data.mkdir()
        subprocess.run(["sudo", "chown", "-R", "0:0", str(data)], check=False)
        name = run_container(data=data)
        time.sleep(2)
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        assert state["Running"], "container should have taken ownership and started"

    def test_an_unwritable_data_directory_stops_instead_of_looping(
        self, run_container, tmp_path
    ):
        """The crash loop, from the other side.

        Measured before the fix: 7 restarts in 12 seconds with the explanation
        scrolling past faster than anyone could read it. Restarting forever on a
        permanent configuration error is the failure; exiting once is the fix.
        """
        data = tmp_path / "readonly"
        data.mkdir()
        name = run_container(data=data, read_only_data=True, wait=False)
        state = wait_for_exit(name)
        assert state["Status"] == "exited"
        assert state["Restarting"] is False
        assert state["ExitCode"] != 0

    def test_the_failure_message_says_what_to_do(self, run_container, tmp_path):
        """Asserts the message is actionable, not that it uses one exact phrase.

        There are two ways to fail here: the directory cannot be created, and it
        exists but is not writable. A user cannot tell them apart and should not
        have to, so both must name the directory and give a command to run.
        """
        data = tmp_path / "readonly2"
        data.mkdir()
        name = run_container(data=data, read_only_data=True, wait=False)
        wait_for_exit(name)
        logs = docker("logs", name).stdout + docker("logs", name).stderr

        assert "cannot write" in logs, "the message must say what went wrong"
        assert "/data" in logs, "the message must name the directory"
        assert "chown" in logs or "chmod" in logs, (
            "the message must give a command the user can run"
        )
        # One clear message, not a wall. Before the restart policy was bounded,
        # this repeated 7 times in 12 seconds and kept going.
        assert logs.count("cannot write") <= 2, "message repeated — is it looping?"


class TestServing:
    def test_health_answers_and_the_ui_is_served(self, run_container):
        name = run_container("-p", "127.0.0.1:18801:8000")
        for _ in range(60):
            probe = docker(
                "exec", name, "python", "-c",
                "import urllib.request;"
                "print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').status)",
                check=False,
            )
            if probe.returncode == 0 and "200" in probe.stdout:
                break
            time.sleep(0.5)
        else:
            pytest.fail("health endpoint never came up")

        page = docker(
            "exec", name, "python", "-c",
            "import urllib.request;"
            "print(urllib.request.urlopen('http://127.0.0.1:8000/').read().decode())",
            check=False,
        )
        assert "<title>unbagged</title>" in page.stdout, "the built UI is not being served"

    def test_a_long_upload_does_not_block_other_requests(self, run_container):
        """The reason `create_request` is `def` and not `async def`.

        On an async endpoint the synchronous parse ran on the event loop, so a
        14 second upload stalled every other request, including the container's
        own HEALTHCHECK. This asserts the endpoint is not declared async, which
        is the property that keeps it in the threadpool.
        """
        name = run_container()
        declared = docker(
            "exec", name, "python", "-c",
            "import inspect, unbagged.api as a;"
            "print(inspect.iscoroutinefunction(a.create_request))",
        ).stdout.strip()
        assert declared == "False", (
            "create_request is async again; a slow parse will block the event loop"
        )


class TestVersionReachesTheImage:
    """The last link in the version chain.

    `tests/test_packaging.py` pins `VERSION` to `__version__` to `app.version`,
    all inside one interpreter reading one checkout. None of that proves the
    number survives a Docker build, and the image is what a user actually runs:
    it installs the package non-editable, so `__version__` falls through to the
    dist-info written during `pip install`, which is a different code path from
    the one the unit tests exercise.

    Both instances serving real data during one review session reported 0.9.0
    while VERSION said 0.10.0, and nothing in the suite noticed.
    """

    def test_the_running_image_reports_the_version_file(self, run_container):
        name = run_container()
        declared = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        # Asked of the app inside the container, not of the host's checkout.
        result = docker(
            "exec", name, "python", "-c",
            "from unbagged import __version__; print(__version__)",
        )
        reported = result.stdout.strip()
        assert reported == declared, (
            f"VERSION says {declared}, the image reports {reported}. "
            "The build did not carry the version through."
        )

    def test_the_health_endpoint_agrees(self, run_container):
        """What the footer shows. It reads /api/health, so this is the string a
        person quotes in a bug report.

        `run_container` waits for the container to be Running, which is not the
        same as uvicorn having bound its port — asking immediately raced startup
        and failed with a connection error, not a version mismatch. So this
        polls, and a timeout here means the app never came up at all.
        """
        name = run_container()
        declared = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        probe = (
            "import json,urllib.request;"
            "print(json.load(urllib.request.urlopen("
            "'http://127.0.0.1:8000/api/health'))['version'])"
        )
        reported = None
        for _ in range(60):
            result = docker("exec", name, "python", "-c", probe, check=False)
            if result.returncode == 0 and result.stdout.strip():
                reported = result.stdout.strip()
                break
            time.sleep(0.5)
        assert reported is not None, f"the app never served /api/health in {name}"
        assert reported == declared
