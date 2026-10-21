#!/usr/bin/env bash
# Claude Code PostToolUse hook (.claude/settings.json): format the file an Edit,
# Write or MultiEdit just touched, so an agent never argues with the formatter.
# Reads the hook's JSON on stdin. Silent on success and always exits 0: a
# formatter that cannot run must never block an edit; pre-commit and CI still
# check everything. See CODING_STANDARDS.md, "The layers".

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v jq >/dev/null 2>&1 || exit 0
file="$(jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -n "$file" ] && [ -f "$file" ] || exit 0

case "$file" in
    "$root"/*) rel="${file#"$root"/}" ;;
    /*) exit 0 ;;
    *) rel="$file" ;;
esac

# Never touch anything under a data directory: it holds real reports, and
# nothing in this repository writes there but the app (CLAUDE.md).
case "$rel" in
    data/* | data.bak-*/* | output/*) exit 0 ;;
esac

# From the repository root, so ruff reads pyproject.toml exactly as the
# pre-commit hook does.
cd "$root" || exit 0
ruff="$root/.venv/bin/ruff"

case "$rel" in
    *.py)
        [ -x "$ruff" ] || exit 0
        "$ruff" format --quiet "$rel" >/dev/null 2>&1
        "$ruff" check --quiet --fix --select I "$rel" >/dev/null 2>&1
        ;;
    frontend/*.ts | frontend/*.tsx | frontend/*.css | frontend/*.json | frontend/*.html)
        # The Prettier pinned in frontend/, with the root config and ignore file,
        # as the pre-commit hook runs it.
        prettier="$root/frontend/node_modules/.bin/prettier"
        [ -x "$prettier" ] && (cd "$root/frontend" &&
            "$prettier" --write --log-level silent --ignore-path ../.prettierignore \
                "$root/$rel" >/dev/null 2>&1)
        ;;
esac

exit 0
