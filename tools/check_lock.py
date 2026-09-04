#!/usr/bin/env python3
"""Fail when docker/requirements.txt no longer covers what pyproject.toml declares.

The lock is what the shipped image installs, with hashes; pyproject.toml is what
the project says it depends on. They are written at different times by different
people, so they drift, and the drift is silent: adding a dependency to
pyproject.toml and forgetting to relock produces an image missing a package, and
raising a floor above the pinned version produces an image that quietly does not
satisfy the project's own stated requirement.

Regenerating the lock to compare would be the thorough check, but it needs Docker
and a network round trip, which is too slow to run on every push. This is the
fast half: it reads both files and checks the three things that actually go wrong.

    tools/check_lock.py        exit 1 on any finding

For the thorough version, `make lock` and see whether the file changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "docker" / "requirements.txt"

DEPENDENCIES_BLOCK = re.compile(r"^dependencies = \[(.*?)^\]", re.S | re.M)
# name, optional [extras], then the rest of the specifier.
REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(?P<spec>.*)$")
LOCK_PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s\;]+)")


def normalise(name: str) -> str:
    """PEP 503: pdfminer.six and pdfminer-six are the same distribution."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared() -> list[tuple[str, str]]:
    """(normalised name, version specifier) for each runtime dependency."""
    block = DEPENDENCIES_BLOCK.search(PYPROJECT.read_text(encoding="utf-8"))
    if block is None:
        raise SystemExit("check_lock: no [project.dependencies] in pyproject.toml")
    out = []
    for line in block.group(1).splitlines():
        spec = line.split("#")[0].strip().rstrip(",").strip().strip('"').strip("'")
        if not spec:
            continue
        m = REQUIREMENT.match(spec)
        if m is None:
            raise SystemExit(f"check_lock: cannot parse requirement {spec!r}")
        out.append((normalise(m.group("name")), m.group("spec").strip()))
    return out


def locked() -> dict[str, tuple[str, bool]]:
    """{normalised name: (version, has_hash)} from the lock file."""
    entries: dict[str, tuple[str, bool]] = {}
    current: str | None = None
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LOCK_PIN.match(line)
        if m:
            current = normalise(m.group("name"))
            # A pin and its hashes may share a line or be continued onto the next.
            entries[current] = (m.group("version"), "--hash=" in raw)
        elif current and "--hash=" in line:
            version, _ = entries[current]
            entries[current] = (version, True)
    return entries


def satisfies(version: str, spec: str) -> bool:
    """Does the pinned version satisfy the declared specifier?"""
    if not spec:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except ImportError:  # pragma: no cover - packaging ships with pip
        print("check_lock: needs `packaging` (pip install -e '.[dev]')", file=sys.stderr)
        raise SystemExit(2) from None
    return Version(version) in SpecifierSet(spec)


def _display(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise. `relative_to` raises on
    anything outside the root, which is every path a test hands it."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def findings() -> list[str]:
    if not LOCK.is_file():
        return [f"{_display(LOCK)} is missing. Run `make lock`."]

    pins = locked()
    problems: list[str] = []

    for name, spec in declared():
        if name not in pins:
            problems.append(
                f"{name} is declared in pyproject.toml and absent from the lock. "
                "Run `make lock`."
            )
            continue
        version, _ = pins[name]
        if not satisfies(version, spec):
            problems.append(
                f"{name} is pinned at {version}, which does not satisfy the declared "
                f"{name}{spec}. Run `make lock`."
            )

    unhashed = sorted(n for n, (_, has_hash) in pins.items() if not has_hash)
    if unhashed:
        problems.append(
            "these locked packages carry no --hash, so `pip install --require-hashes` "
            f"would refuse the whole file: {', '.join(unhashed)}"
        )

    if not pins:
        problems.append("the lock contains no pinned packages at all.")

    return problems


def main() -> int:
    problems = findings()
    if not problems:
        print(f"check_lock: ok — {len(locked())} packages pinned and hashed")
        return 0
    print(f"check_lock: {len(problems)} finding(s)\n", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
