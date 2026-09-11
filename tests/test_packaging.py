"""Guards on the packaging configuration.

These assert properties of files rather than of code, because the highest-
consequence line in this repository is a port binding in a YAML file, and a
review will not catch it going missing during an unrelated edit.
"""

import importlib
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
COMPOSE = ROOT / "docker-compose.yml"
DEV_COMPOSE = ROOT / "docker-compose.dev.yml"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
GITIGNORE = ROOT / ".gitignore"


class ComposeLoader(yaml.SafeLoader):
    """A SafeLoader that understands Compose's own tags.

    `!override` and `!reset` are Compose merge directives, not YAML types, and
    safe_load refuses to construct them. A test that cannot read the file it is
    guarding is not a guard, so the loader learns the two tags rather than the
    compose file avoiding them.
    """


ComposeLoader.add_constructor(
    "!override",
    lambda loader, node: (
        loader.construct_sequence(node)
        if isinstance(node, yaml.SequenceNode)
        else loader.construct_mapping(node)
    ),
)
ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def services(path: Path) -> dict:
    # ComposeLoader subclasses yaml.SafeLoader (see above); the rule matches on
    # the loader's name, not its base class.
    return yaml.load(
        path.read_text(encoding="utf-8"),
        Loader=ComposeLoader,  # noqa: S506 - ComposeLoader subclasses SafeLoader
    )["services"]


class TestPortBinding:
    """Without the 127.0.0.1 prefix Docker publishes on every interface and goes
    straight through the host firewall, putting two years of someone's groceries
    on their local network."""

    @pytest.mark.parametrize("path", [COMPOSE, DEV_COMPOSE])
    def test_every_published_port_is_loopback_only(self, path):
        for name, service in services(path).items():
            for published in service.get("ports") or []:
                assert str(published).startswith("127.0.0.1:"), f"{path.name}:{name}"

    def test_the_interface_is_not_configurable_even_though_the_port_is(self):
        """The port became an environment knob; the interface must not.

        `127.0.0.1:` has to sit OUTSIDE the interpolation. If it ever moves
        inside, one stray value in a .env file publishes someone's grocery
        history to their whole network, and nothing would say so.
        """
        for path in (COMPOSE, DEV_COMPOSE):
            for service in services(path).values():
                for published in service.get("ports") or []:
                    text = str(published)
                    prefix = text.split("${", 1)[0]
                    assert prefix.startswith("127.0.0.1:"), (
                        f"{text!r} lets the interface be overridden"
                    )

    def test_the_app_is_published_where_the_readme_says(self):
        ports = [str(p) for p in services(COMPOSE)["unbagged"]["ports"]]
        assert any(p.startswith("127.0.0.1:") and p.endswith(":8000") for p in ports)
        # The default inside the interpolation is what a reader of the README
        # will actually get, so the two must not drift apart.
        assert any("8420" in p for p in ports)
        assert "8420" in (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_configurable_port_is_documented(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "UNBAGGED_PORT" in example
        assert "UNBAGGED_PORT" in COMPOSE.read_text(encoding="utf-8")


class TestDataHandling:
    def test_the_data_directory_is_a_bind_mount_not_a_copy(self):
        # Real reports must never end up in an image layer.
        volumes = services(COMPOSE)["unbagged"]["volumes"]
        assert "./data:/data" in [str(v) for v in volumes]

    def test_the_dockerfile_never_copies_the_data_directory(self):
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("COPY") and "--from=" not in stripped:
                assert not stripped.split()[1].startswith(("data", "./data", "/data"))

    def _dockerignore_entries(self) -> set[str]:
        return {
            line.strip()
            for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    def _gitignore_entries(self) -> set[str]:
        return {
            line.strip()
            for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    def test_the_dockerignore_excludes_the_data_directory(self):
        entries = self._dockerignore_entries()
        assert {"data/", "output/"} <= entries
        # `make reset` renames ./data to data.bak-<timestamp> and leaves it in
        # the checkout, holding every report and the database under a name
        # `data/` does not match.
        assert "data.bak-*/" in entries
        # Screenshots of the app rendering real reports.
        assert ".gstack/" in entries

    def test_every_dockerignore_wildcard_is_depth_qualified(self):
        """A bare `*.pdf` in .dockerignore excludes ./report.pdf and nothing below it.

        This is the one place the two ignore formats look identical and behave
        differently. A .gitignore pattern containing no slash matches at every
        depth; a .dockerignore pattern is matched against the whole relative path,
        so `*.pdf` never sees `data.bak-2026/incoming/report.pdf`. Copying
        .gitignore's lines across verbatim — which is what "mirrors .gitignore" in
        the header used to mean — left every report format excluded at the root
        only, and sent the rest to the daemon.
        """
        offenders = sorted(
            e for e in self._dockerignore_entries() if e.startswith("*") and not e.startswith("**/")
        )
        assert not offenders, (
            f"depth-limited .dockerignore patterns: {offenders}. "
            "Prefix each with '**/' so it matches at every depth."
        )

    def test_the_two_ignore_files_deny_the_same_report_formats(self):
        """Neither file may learn a report format the other has not.

        They protect the same bytes by different routes: .gitignore keeps a real
        report out of the repository, .dockerignore keeps it out of the build
        context and the daemon's cache. A format denied in one and not the other
        is a hole in whichever half was forgotten.
        """
        git_suffixes = {e.lstrip("!*") for e in self._gitignore_entries() if e.startswith("*.")}
        docker_suffixes = {
            e.removeprefix("**/").lstrip("*") for e in self._dockerignore_entries() if "*." in e
        }
        report_formats = {
            ".pdf",
            ".zip",
            ".csv",
            ".tsv",
            ".xls",
            ".xlsx",
            ".ods",
            ".doc",
            ".docx",
            ".eml",
            ".mbox",
            ".7z",
            ".rar",
            ".tar",
            ".tgz",
        }
        assert report_formats <= git_suffixes, (
            f".gitignore is missing: {sorted(report_formats - git_suffixes)}"
        )
        assert report_formats <= docker_suffixes, (
            f".dockerignore is missing: {sorted(report_formats - docker_suffixes)}"
        )


class TestImageShape:
    def test_privileges_are_dropped_before_the_app_starts(self):
        """The static half of the non-root guarantee.

        This used to grep for `USER unbagged`. That line is gone on purpose: the
        container starts as root so the entrypoint can chown the bind-mounted
        /data, then drops. Grepping for USER would now fail, and "fixing" it by
        deleting the assertion would silently retire the guarantee. So this
        checks the drop exists, and
        tests/container/test_runtime.py::test_the_app_process_runs_as_uid_10001
        checks the effective uid at runtime, which is the claim that matters.
        """
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        assert "exec $as_app" in entrypoint or "exec gosu" in entrypoint
        assert "gosu" in DOCKERFILE.read_text(encoding="utf-8")

    def test_the_runtime_uid_is_asserted_somewhere_that_runs_it(self):
        # Guard against the container tier being deleted and this file being
        # left believing it still covers the non-root guarantee.
        runtime_tests = ROOT / "tests" / "container" / "test_runtime.py"
        assert runtime_tests.is_file()
        assert "os.getuid()" in runtime_tests.read_text(encoding="utf-8")

    def test_the_ui_is_built_in_a_separate_stage(self):
        # Node exists only in the builder; the runtime gets a directory of files.
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "FROM node:" in text
        assert "COPY --from=frontend" in text
        runtime = text.split("AS runtime", 1)[1]
        assert "npm" not in runtime

    def test_the_default_build_pins_the_runtime_stage(self):
        """A build with no target builds the LAST stage in the Dockerfile.

        The dev stage sits last (it is `FROM runtime`, so it has to). Without an
        explicit target, `docker compose up` built and shipped dev — editable
        install, --reload, and a source tree mounted over the package. Caught by
        the container tier, which found itself watching for file changes in what
        was supposed to be the production image.
        """
        build = services(COMPOSE)["unbagged"]["build"]
        assert isinstance(build, dict), "build must name a target, not just a context"
        assert build.get("target") == "runtime"

        stages = [
            line.split(" AS ")[1].strip()
            for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
            if line.startswith("FROM ") and " AS " in line
        ]
        assert stages[-1] != "runtime", (
            "runtime is now the last stage, so this guard is testing nothing; "
            "either keep dev last or remove the pin deliberately"
        )

    def test_the_default_path_starts_exactly_one_service(self):
        # Individuals are the audience. A Postgres sidecar and a separate
        # frontend container are friction with no payoff at this scale.
        assert list(services(COMPOSE)) == ["unbagged"]

    def test_the_dev_overlay_adds_the_vite_server(self):
        assert "web" in services(DEV_COMPOSE)

    def test_the_dev_stack_does_not_write_to_the_checkout_it_mounts(self):
        """Starting the app must not edit a tracked file.

        `./frontend` is bind-mounted into the dev stack, so it is the real
        checkout rather than a copy, and anything the container writes there
        lands in `git status`. The web service runs `npm install` on every
        start: this image carries npm 10, a lockfile written by npm 11 records
        a `libc` field per optional platform dependency that npm 10 does not
        know about, and it drops all of them — 54 lines here. `make dev` then
        edits `frontend/package-lock.json` as a side effect of starting, the
        change reverses the next time anyone runs npm on the host, and the file
        it rewrites is the one `npm ci` builds the shipped image from.

        Asserted as the relationship rather than as a flag: the flag matters
        only because the path is mounted. A dev stack that stopped mounting the
        checkout, or stopped installing into it, would not need it.
        """
        web = services(DEV_COMPOSE)["web"]
        mounted = {
            v.split(":")[1]
            for v in (web.get("volumes") or [])
            if isinstance(v, str) and v.startswith("./") and v.count(":") >= 1
        }
        command = " ".join(web["command"]) if isinstance(web["command"], list) else web["command"]
        if "npm install" not in command:
            return  # npm ci and friends write nothing; nothing to guard
        writes_into_checkout = any(
            m == web["working_dir"] or m.startswith(web["working_dir"] + "/") for m in mounted
        )
        assert not writes_into_checkout or "--no-save" in command, (
            f"the web service installs into {web['working_dir']}, which is the mounted "
            f"checkout, without --no-save; `make dev` will rewrite the lockfile. "
            f"command: {command}"
        )

    def test_dev_mode_publishes_exactly_one_url(self):
        """Dev used to publish both 5173 and 8420, and 8420 served the UI bundle
        frozen into the image at build time. Nothing distinguished them in a
        browser, so an edit appeared to do nothing."""
        dev = services(DEV_COMPOSE)
        published = [p for s in dev.values() for p in (s.get("ports") or [])]
        assert len(published) == 1, f"dev should publish one URL, got {published}"
        assert "5173" in str(published[0])


class TestVersionIsOneNumber:
    """`VERSION` and what the app reports must be the same string.

    The footer exists for one reason: with no telemetry, no crash reporting and
    no update check, a person filing an issue has no other way to say what they
    are running. A footer that reports a stale number is worse than no footer,
    because it is confidently wrong and it sends the maintainer to the wrong
    commit.

    Nothing asserted this, and it went wrong the first time it could: bumping
    `VERSION` to 0.10.0 left a running dev server reporting 0.9.0, because
    `__version__` reads install-time metadata rather than the file. Nothing in
    the suite noticed.
    """

    def test_the_package_reports_the_version_file(self):
        from unbagged import __version__

        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert __version__ == declared, (
            f"VERSION says {declared} but the package reports {__version__}."
        )

    def test_the_version_file_wins_over_stale_install_metadata(self):
        """The file is authoritative in a checkout, so a bump takes effect
        immediately. This used to require `pip install -e .`, and forgetting it
        left the footer reporting the previous release."""
        from unbagged import _read_version

        original = (ROOT / "VERSION").read_text(encoding="utf-8")
        try:
            (ROOT / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            assert _read_version() == "9.9.9"
        finally:
            (ROOT / "VERSION").write_text(original, encoding="utf-8")

    def test_the_api_reports_the_same_version(self):
        """The footer reads /api/health. A person filing a bug quotes what the
        footer says, so it has to be the same string as everything else."""
        from unbagged import __version__, api

        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert api.app.version == declared == __version__

    def test_the_version_file_is_a_bare_semver_string(self):
        """The Dockerfile and pyproject both read this file directly, so a
        stray comment or a `v` prefix breaks the image build rather than a
        test."""
        declared = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", declared), (
            f"VERSION is {declared!r}; expected a bare semver like 0.10.0"
        )


class TestToolsAreInvokedAsModules:
    """Issue #29: `tools/` resolves as a package only when the repo root is on
    the path, and running a script by path puts the script's OWN directory there
    instead.

    `pyproject.toml` sets `pythonpath = ["src", "."]` for pytest, so
    `from tools import X` works under test and nowhere else. `make_lock.py` had
    a hand-rolled `sys.path.insert(ROOT)` in its `__main__` block to work
    around exactly this, for one script.

    `python -m tools.X` puts the working directory on the path, so every caller
    uses that form now. Asserted here rather than left to habit: the failure is
    silent under pytest and only appears when someone runs the real command.
    """

    CALLERS = (
        "Makefile",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        ".pre-commit-config.yaml",
    )

    def test_nothing_invokes_a_tool_by_path(self):
        offenders = []
        for name in self.CALLERS:
            path = ROOT / name
            if not path.exists():
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"(python[0-9.]*|\$\(PY\))\s+tools/[a-z_]+\.py", line):
                    offenders.append(f"{name}:{number}: {line.strip()}")
        assert not offenders, "invoke these as `python -m tools.X`:\n" + "\n".join(offenders)

    def test_every_tool_imports_as_a_module(self):
        """The other half. A caller using `-m` is no good if the module does not
        import — and one that imports only under pytest is the bug itself."""
        failures = []
        for script in sorted((ROOT / "tools").glob("*.py")):
            module = f"tools.{script.stem}"
            try:
                importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001 - reporting, not handling
                failures.append(f"{module}: {exc}")
        assert not failures, "\n".join(failures)


# Markup that can sit in front of a command without changing what it is: a
# Makefile recipe tab, a YAML comment marker, a markdown list bullet or table
# cell, a backtick. Stripping it is what lets one scan read every file that
# tells a person how to start the app, whatever it is written in.
LAUNCH_MARKUP = re.compile(r"^[\s|>*+-]*(?:#+\s*)?`*")

# The command, anchored to the start of the stripped line. Anchoring is the
# whole classifier: a line that *begins* with the command is an instruction to
# run it, while one that mentions it mid-sentence is prose about it. That
# distinction is what keeps this guard off `CONTRIBUTING.md`'s description of
# how the project is distributed, off the handoff's note on how many services
# the default path starts, and off the docstring above that explains a bug the
# command used to have.
LAUNCH_COMMAND = re.compile(r"^(?:docker compose|\$\(COMPOSE\)|\$\(DEV_COMPOSE\))[^`\n]*?\bup\b")

SKIP_DIRS = {".git", ".venv", "node_modules", "data", "__pycache__", "build", "dist"}


def launch_commands() -> list[tuple[str, int, str]]:
    """Every line in the repository that tells someone to start the app."""
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue  # a binary or unreadable file cannot carry an instruction
        for number, line in enumerate(lines, 1):
            if LAUNCH_COMMAND.match(LAUNCH_MARKUP.sub("", line)):
                found.append((str(path.relative_to(ROOT)), number, line.strip()))
    return found


class TestEveryStartCommandRebuilds:
    """Starting the app has to build it first, everywhere that is written down.

    Compose reuses an image already tagged `unbagged:local`. It does not notice
    that the checkout moved, so a start command without `--build` serves
    whatever was built last: after a pull, the previous release's code under the
    current source tree, with its own migrations and its own bugs.

    The footer is the only thing that shows it, and the footer is not wrong —
    the container really is the version it names. That is what makes this
    expensive to spot: every link in the version chain is correct, and
    `TestVersionIsOneNumber` below passes, because the number is read at runtime
    from the running package. It is the package that is old.

    `tests/container/` never caught it either, and could not have: its `image`
    fixture runs `docker build` itself, so the tier has only ever tested a
    freshly built image. Building is exactly the step the user was missing.

    Scoped to the relationship rather than to a list of files: every line that
    *starts* a start command must carry `--build`, wherever it is written. A
    new README, a new make target or a new doc is covered the day it is added,
    which a named set of files would not be.
    """

    def test_every_start_command_carries_build(self):
        missing = [
            f"{path}:{number}  {line}"
            for path, number, line in launch_commands()
            if "--build" not in line
        ]
        assert not missing, (
            "These start the app without rebuilding it, so they serve whatever "
            "image was built last:\n" + "\n".join(missing)
        )

    def test_the_scan_still_finds_the_commands_it_is_guarding(self):
        """A `for` loop over nothing passes.

        This guard is a `∀`, so it reports success on an empty scan — and the
        scan is a regex over file text, which stops matching the moment the
        Makefile renames `$(COMPOSE)` or the README moves. `check_signers.py`
        shipped that defect four times over (issue #32), verifying an empty set
        of tags and calling it clean, so the floor is asserted here rather than
        assumed.
        """
        found = launch_commands()
        files = {path for path, _, _ in found}
        assert "Makefile" in files, (
            f"the scan no longer sees the Makefile's start targets; found {sorted(files)}"
        )
        assert "README.md" in files, (
            f"the scan no longer sees the README's quickstart; found {sorted(files)}"
        )
        assert len(found) >= 7, (
            f"the scan found only {len(found)} start commands, which is fewer than "
            "the repository had when this guard was written — it has probably "
            "stopped matching rather than the commands having gone"
        )
