#!/usr/bin/env python3
"""Regenerate docker/requirements.txt, the runtime lock for the shipped image.

    make lock

Runs pip-compile inside python:3.12-slim on linux/amd64, because that is what the
image is. Hashes are per-wheel: a lock compiled in a macOS venv pins macOS wheels,
and `pip install --require-hashes` in the build then rejects every one of them.
Generating it anywhere but the target platform produces a file that looks right
and cannot be installed, so this tool does not offer a local fallback.

Only `[project.dependencies]` is locked. The contributor path — `pip install -e
".[dev]"` — stays a floor-based resolve on whatever platform the contributor is
on; making them fight per-wheel hashes to run the tests would buy nothing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "docker" / "requirements.txt"
IMAGE = "python:3.12-slim"
PLATFORM = "linux/amd64"

DEPENDENCIES_BLOCK = re.compile(r"^dependencies = \[(.*?)^\]", re.S | re.M)

HEADER = '''#
# The runtime dependency lock for the shipped image. GENERATED — do not hand-edit.
#
#     make lock          regenerate from pyproject.toml's [project.dependencies]
#     make lock-check    fail if this file no longer covers what pyproject declares
#
# Why this file exists
# --------------------
# The Dockerfile used to run `pip install .`, which resolved whatever PyPI served
# that minute: every package below, none pinned, none hash-checked, installed into
# the container that reads people's right-to-know responses. A single compromised
# release anywhere in that graph executed at build time and then ran against the
# data. frontend/package-lock.json already gave the UI this guarantee; this is the
# Python half.
#
# Why it lives in docker/ and not at the repo root
# -----------------------------------------------
# It is the *image's* lock, not the contributor's. Hashes are per-wheel and these
# are linux/amd64 wheels, so `pip install -r` against this file on a macOS
# checkout fails to find a matching wheel — which is why it is not sitting at the
# root looking like the file you are supposed to install from. The contributor
# path is unchanged and stays a floor-based resolve:
#
#     pip install -e ".[dev]"
#
# Regenerating therefore has to happen on the target platform, which `make lock`
# does by running pip-compile inside {image} on {platform}. Doing it in a local
# venv pins the wrong wheels and the build then fails on every hash, which is the
# loud failure mode rather than the quiet one.
#
# Dependabot updates this file (see .github/dependabot.yml, the /docker entry).
# A pin nobody bumps decays into an unpatched dependency, which is a worse
# posture than floors.
#
'''


def runtime_requirements() -> list[str]:
    block = DEPENDENCIES_BLOCK.search(PYPROJECT.read_text(encoding="utf-8"))
    if block is None:
        raise SystemExit("make_lock: no [project.dependencies] in pyproject.toml")
    out = []
    for line in block.group(1).splitlines():
        spec = line.split("#")[0].strip().rstrip(",").strip().strip('"').strip("'")
        if spec:
            out.append(spec)
    if not out:
        raise SystemExit("make_lock: [project.dependencies] is empty")
    return out


def compile_in_container(workdir: Path) -> Path:
    (workdir / "requirements.in").write_text(
        "\n".join(runtime_requirements()) + "\n", encoding="utf-8"
    )
    if shutil.which("docker") is None:
        raise SystemExit(
            "make_lock: needs Docker. The lock has to be compiled on "
            f"{PLATFORM}; see this file's docstring for why there is no local "
            "fallback."
        )
    print(f"make_lock: compiling inside {IMAGE} on {PLATFORM}")
    subprocess.run(
        [
            "docker", "run", "--rm", "--platform", PLATFORM,
            "-v", f"{workdir}:/w", "-w", "/w", IMAGE,
            "sh", "-c",
            "pip install -q --no-cache-dir pip-tools && "
            "pip-compile --quiet --generate-hashes --strip-extras "
            "--output-file /w/out.txt /w/requirements.in",
        ],
        check=True,
    )
    return workdir / "out.txt"


def write_lock(compiled: Path) -> int:
    body = [
        line for line in compiled.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    header = HEADER.format(image=IMAGE, platform=PLATFORM)
    LOCK.write_text(header + "\n".join(body) + "\n", encoding="utf-8")
    return sum(1 for line in body if re.match(r"^[A-Za-z0-9]", line))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        compiled = compile_in_container(Path(tmp))
        count = write_lock(compiled)
    print(f"make_lock: wrote {LOCK.relative_to(REPO_ROOT)} — {count} packages")
    from tools.check_lock import main as check  # noqa: PLC0415
    return check()


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
