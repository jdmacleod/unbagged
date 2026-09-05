#!/usr/bin/env python3
"""Every tag verifies against the signers file committed beside it.

    python tools/check_signers.py

A signature nobody can check is decoration. `.github/allowed_signers` is what
turns a signed tag into something a person who cloned this repository can verify
without asking GitHub to vouch for it, and this asserts that the file still does
that job for every tag in the history.

**It requires at least one tag.** That is the load-bearing part rather than a
detail. A checkout without tags -- `actions/checkout` fetches none unless asked
-- would otherwise verify an empty set and report success, which is the shape
this repository has shipped four times and filed as an issue. Zero tags is
therefore a failure that names the cause, not a quiet pass.

What it catches, in the order it will happen:

    a rotation that replaces a key instead of adding one
        -> tags signed with the old key stop verifying, and they can never be
           re-signed: the tag ruleset blocks deletion and non-fast-forward
    an unsigned tag
        -> the convention is signed tags; an unsigned one is invisible to
           anyone checking provenance
    a malformed signers file
        -> verification fails open into "cannot check", which reads like
           success if nobody looks
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNERS = REPO_ROOT / ".github" / "allowed_signers"


def shown(path: Path) -> str:
    """A repo-relative path where that makes sense, the full one otherwise.

    `relative_to` raises for anything outside the tree, which turned the
    file-is-missing message — the one path where the reader most needs to be
    told what happened — into a traceback.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def entries(text: str) -> list[tuple[str, str]]:
    """The (principal, key type) pairs in a signers file, comments dropped."""
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 2:
            raise ValueError(f"not a signer entry: {line!r}")
        principal = fields[0]
        # An options field (namespaces=..., valid-after=...) may sit between the
        # principal and the key type. The key type is the first field that looks
        # like one.
        key_type = next(
            (f for f in fields[1:] if f.startswith(("ssh-", "sk-", "ecdsa-"))),
            None,
        )
        if key_type is None:
            raise ValueError(f"no key type in signer entry: {line!r}")
        found.append((principal, key_type))
    return found


def tags() -> list[str]:
    result = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/tags"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def verify(tag: str) -> tuple[bool, str]:
    """Verify one tag, with the signers file passed in rather than assumed.

    `-c` rather than relying on the caller's git config: a fresh clone and a CI
    runner have no `gpg.ssh.allowedSignersFile` set, and a check that only works
    on the machine that wrote it is not a check.
    """
    result = subprocess.run(
        ["git", "-c", f"gpg.ssh.allowedSignersFile={SIGNERS}", "verify-tag", tag],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def run() -> int:
    if not SIGNERS.is_file():
        print(f"check_signers: no signers file at {shown(SIGNERS)}",
              file=sys.stderr)
        return 1
    try:
        signers = entries(SIGNERS.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"check_signers: {exc}", file=sys.stderr)
        return 1
    if not signers:
        print("check_signers: the signers file lists nobody, so nothing can verify",
              file=sys.stderr)
        return 1

    found = tags()
    if not found:
        print(
            "check_signers: no tags in this checkout, so this verified nothing.\n"
            "A shallow or tag-less clone reaches here — `actions/checkout` fetches\n"
            "no tags unless told to. Fetch them, or this check is decoration:\n"
            "    git fetch --tags --force",
            file=sys.stderr,
        )
        return 1

    problems = []
    for tag in found:
        ok, detail = verify(tag)
        if ok:
            print(f"  ok     {tag}  {detail.splitlines()[0] if detail else ''}")
        else:
            problems.append((tag, detail))

    if problems:
        print(f"\ncheck_signers: {len(problems)} tag(s) do not verify against "
              f"{shown(SIGNERS)}", file=sys.stderr)
        for tag, detail in problems:
            print(f"  FAILED  {tag}\n          {detail}", file=sys.stderr)
        print(
            "\nIf a key was rotated, ADD the new line and keep the old one. Tags\n"
            "already signed cannot be re-signed: the tag ruleset blocks deletion\n"
            "and non-fast-forward, so removing a key orphans every tag it signed.",
            file=sys.stderr,
        )
        return 1

    print(f"\ncheck_signers: {len(found)} tag(s) verify, "
          f"{len(signers)} signer(s) trusted")
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
