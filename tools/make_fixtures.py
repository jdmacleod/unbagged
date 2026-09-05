#!/usr/bin/env python3
"""Regenerate every adapter's synthetic fixtures.

Each adapter ships ``fixtures/generate.py`` exposing ``generate(seed) -> {filename:
content}``. This script runs them all and writes the results next to the generator.

    make fixtures          regenerate and write
    make fixtures-check    regenerate and fail on any difference

The check is not housekeeping. Committed fixtures being byte-identical to what a
fixed seed produces is what proves nobody dropped a real report into a fixtures
directory — a guarantee no pattern scanner can give. tools/scan_pii.py relies on
it: a few address-shaped rules stand down inside generated fixture directories
precisely because this check covers them.

That only holds if the check covers every committed file, not just the filenames
the generator happens to name. It compares both directions: each produced file
must match byte for byte, and each committed file must be one a generator
produces. A file that reproduces from no seed is the exact shape of the accident
this project exists to prevent.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTERS_DIR = REPO_ROOT / "src" / "unbagged" / "adapters"
GENERATOR_NAME = "generate.py"

# Committed files a fixtures directory may hold that the generator does not produce.
# Everything else has to come out of generate(), or it is unexplained.
ALLOWED_EXTRAS = {GENERATOR_NAME, "__init__.py", "NOTES.md", "README.md"}


def find_generators() -> list[Path]:
    return sorted(ADAPTERS_DIR.glob(f"*/fixtures/{GENERATOR_NAME}"))


def load(path: Path) -> ModuleType:
    # Loaded by path rather than imported: fixtures/ is deliberately not a package,
    # so a generator can never be pulled in by application code at runtime.
    name = f"_fixture_generator_{path.parent.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load generator {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "generate"):
        raise RuntimeError(f"{path} must define generate(seed) -> dict[str, str]")
    return module


def tracked_fixture_files() -> dict[Path, set[str]]:
    """Every committed file under an adapter's fixtures/, grouped by directory.

    Read from git rather than the filesystem on purpose: an uncommitted scratch
    file in a fixtures directory harms nobody, and a *committed* one is the whole
    problem. Untracked files are the author's business; tracked ones are everyone's.
    """
    listed = subprocess.run(
        ["git", "ls-files", "--", "src/unbagged/adapters/*/fixtures/*"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    ).stdout.split()
    grouped: dict[Path, set[str]] = {}
    for name in listed:
        path = REPO_ROOT / name
        grouped.setdefault(path.parent, set()).add(path.name)
    return grouped


def unexplained_files(produced_by_dir: dict[Path, set[str]]) -> list[str]:
    """Committed fixture files that no generator produces.

    This is the half of the guarantee that used to be missing. Comparing only the
    filenames a generator names proves those files are synthetic and says nothing
    at all about anything else sitting beside them — so a real report added to a
    fixtures directory reproduced no check, tripped no scanner rule that had been
    stood down *because* of this check, and was committed clean.
    """
    stray: list[str] = []
    for directory, committed in sorted(tracked_fixture_files().items()):
        expected = produced_by_dir.get(directory, set()) | ALLOWED_EXTRAS
        for name in sorted(committed - expected):
            stray.append(str((directory / name).relative_to(REPO_ROOT)))
    return stray


def run(check: bool = False, seed: int | None = None) -> int:
    generators = find_generators()
    if not generators:
        print("make_fixtures: no generators found", file=sys.stderr)
        return 1

    drift: list[str] = []
    produced_by_dir: dict[Path, set[str]] = {}
    written = 0
    for generator_path in generators:
        module = load(generator_path)
        retailer = generator_path.parent.parent.name
        produced = module.generate(seed) if seed is not None else module.generate()
        produced_by_dir.setdefault(generator_path.parent, set()).update(produced)
        for filename, content in produced.items():
            target = generator_path.parent / filename
            rel = target.relative_to(REPO_ROOT)
            if check:
                existing = target.read_text(encoding="utf-8") if target.exists() else None
                if existing != content:
                    drift.append(str(rel))
                    print(f"  DRIFT  {rel}", file=sys.stderr)
                else:
                    print(f"  ok     {rel}")
            else:
                target.write_text(content, encoding="utf-8")
                written += 1
                print(f"  wrote  {rel}  ({len(content):,} bytes, {retailer})")

    if check:
        stray = unexplained_files(produced_by_dir)
        for rel in stray:
            print(f"  STRAY  {rel}", file=sys.stderr)
        if drift:
            print(
                f"\nmake_fixtures: {len(drift)} fixture(s) differ from what the "
                "generator produces.\nEither regenerate them with `make fixtures`, or "
                "work out why a committed\nfixture is not generator output — a real "
                "report in a fixtures directory\nlooks exactly like this.",
                file=sys.stderr,
            )
        if stray:
            print(
                f"\nmake_fixtures: {len(stray)} committed file(s) in a fixtures "
                "directory that no\ngenerator produces. A fixtures directory is "
                "re-included by .gitignore and\nexempted by the scanner, so a file "
                "nobody can reproduce from a seed has\nbypassed every safeguard this "
                "project has. Add it to the generator, or\ndelete it — and if it came "
                "out of a real report, see CONTRIBUTING.md\nbefore doing anything else.",
                file=sys.stderr,
            )
        if drift or stray:
            return 1
        print(f"\nmake_fixtures: {len(generators)} generator(s) reproduce their fixtures")
        return 0

    print(f"\nmake_fixtures: wrote {written} file(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if a committed fixture differs from generator output")
    parser.add_argument("--seed", type=int, default=None,
                        help="override the generator's default seed")
    args = parser.parse_args(argv)
    return run(check=args.check, seed=args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
