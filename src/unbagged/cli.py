"""unbagged command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from unbagged import __version__
from unbagged.sanitize import sanitize_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unbagged")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    san = sub.add_parser(
        "sanitize",
        help="reduce a report to a structure-only skeleton safe to attach to an issue",
    )
    san.add_argument("file", type=Path)
    san.add_argument("-o", "--output", type=Path, help="default: stdout")

    args = parser.parse_args(argv)

    if args.command == "sanitize":
        try:
            skeleton = sanitize_file(args.file)
        except (OSError, ValueError) as exc:
            print(f"unbagged sanitize: {exc}", file=sys.stderr)
            return 1
        rendered = json.dumps(skeleton, indent=2, sort_keys=False)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print(f"wrote {args.output}")
        else:
            print(rendered)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
