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

    serve = sub.add_parser("serve", help="run the local web app")
    # 127.0.0.1, not 0.0.0.0. The alternative is publishing two years of someone's
    # groceries to their LAN, which has to be a deliberate choice.
    serve.add_argument("--host", default="127.0.0.1",
                       help="default 127.0.0.1; use 0.0.0.0 only if you mean it")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"unbagged: binding to {args.host}. Anyone who can reach this "
                "machine on the network can read your report.",
                file=sys.stderr,
            )
        uvicorn.run(
            "unbagged.api:app", host=args.host, port=args.port, reload=args.reload,
            log_level="info",
        )
        return 0

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
