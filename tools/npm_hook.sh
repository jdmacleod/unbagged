#!/usr/bin/env bash
# Run a tool from frontend/'s pinned node_modules on the files pre-commit passes
# (CODING_STANDARDS.md, "The layers").
#
#   tools/npm_hook.sh <tool> [args...] -- <file>...
#
# pre-commit passes paths relative to the repository root, but the tool runs from
# frontend/, where it finds its config; so the paths are made absolute first.
# Without `make setup-frontend` there is no tool to run, and the hook says so
# rather than passing having checked nothing.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
package="$root/frontend"

args=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    args+=("$1")
    shift
done
[ "$#" -gt 0 ] && shift

files=()
for file in "$@"; do
    files+=("$root/$file")
done
[ "${#files[@]}" -gt 0 ] || exit 0

tool="$package/node_modules/.bin/${args[0]}"
if [ ! -x "$tool" ]; then
    echo "frontend/node_modules is missing ${args[0]}: run 'make setup-frontend' once." >&2
    exit 1
fi

cd "$package"
exec "$tool" "${args[@]:1}" "${files[@]}"
