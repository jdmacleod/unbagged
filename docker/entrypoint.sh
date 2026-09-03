#!/bin/sh
# The data directory is a bind mount from the host, so it arrives owned by
# whoever created it. Create the subdirectories we need and carry on; if they
# are not writable, say so plainly rather than failing with a traceback three
# frames into SQLite.
set -e

for dir in "${UNBAGGED_DB%/*}" "$UNBAGGED_INCOMING"; do
    mkdir -p "$dir" 2>/dev/null || true
    if [ ! -w "$dir" ]; then
        echo "unbagged: cannot write to $dir." >&2
        echo "  The ./data directory is bind-mounted from your machine and the" >&2
        echo "  container runs as uid 10001. Fix it on the host with:" >&2
        echo "    chmod -R a+rwX ./data" >&2
        exit 1
    fi
done

exec "$@"
