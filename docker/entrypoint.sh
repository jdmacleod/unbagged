#!/bin/sh
# Prepare the bind-mounted data directory, then drop to the app user.
#
# The container starts as root for exactly one reason: `./data` is a bind mount
# from the host, and on Linux the daemon creates a missing bind-mount source as
# root:root. A container that started as uid 10001 could not write to it, so the
# documented three-command quickstart failed on the platform where most
# privacy-minded users run. Taking ownership needs root; running the app does
# not, so we drop before exec.
#
#   root  ->  mkdir + chown /data  ->  gosu unbagged  ->  uvicorn (uid 10001)
#
# Nothing after the `exec gosu` line runs with privileges.
set -e

APP_USER=unbagged
APP_UID=10001

if [ "$(id -u)" = "0" ]; then
    as_app="gosu $APP_USER"
else
    # Already unprivileged (someone passed --user). Skip the chown and just
    # check we can write, so the diagnostic below still fires.
    as_app=""
fi

for dir in "${UNBAGGED_DB%/*}" "$UNBAGGED_INCOMING"; do
    [ -n "$dir" ] || continue
    mkdir -p "$dir" 2>/dev/null || true

    if [ ! -d "$dir" ]; then
        echo "unbagged: cannot write to $dir — it does not exist and could not" >&2
        echo "  be created." >&2
        echo "" >&2
        echo "  ./data is bind-mounted from your machine, and this usually means" >&2
        echo "  the mount is read-only. Check the volumes: line in" >&2
        echo "  docker-compose.yml, then on the host:" >&2
        echo "" >&2
        echo "    mkdir -p ./data/db ./data/incoming" >&2
        echo "    chown -R $APP_UID:$APP_UID ./data   # or: chmod -R a+rwX ./data" >&2
        echo "" >&2
        echo "  The container will stop rather than retry, so this message stays" >&2
        echo "  on screen." >&2
        exit 1
    fi

    # Best effort: a read-only mount, an NFS share with root squash, or a
    # userns-remapped daemon can all refuse this. Failing here is not fatal on
    # its own, because what actually matters is whether the app user can write.
    #
    # Only when the top directory is not already ours, so a growing report
    # directory and a multi-megabyte SQLite file are not re-walked on every
    # start. -h changes symlinks themselves rather than their targets: this is a
    # host-controlled directory, and following a link out of it would let a
    # symlink in ./data chown something elsewhere on the user's disk.
    if [ "$(id -u)" = "0" ] && [ "$(stat -c '%u' "$dir")" != "$APP_UID" ]; then
        chown -Rh "$APP_UID:$APP_UID" "$dir" 2>/dev/null || true
    fi

    if ! $as_app test -w "$dir"; then
        echo "unbagged: cannot write to $dir." >&2
        echo "" >&2
        echo "  The ./data directory is bind-mounted from your machine and the app" >&2
        echo "  runs as uid $APP_UID. Taking ownership of it was attempted and did not" >&2
        echo "  work, which usually means the mount is read-only, lives on a share" >&2
        echo "  that refuses chown (NFS with root squash), or the Docker daemon runs" >&2
        echo "  with user namespace remapping." >&2
        echo "" >&2
        echo "  Fix it on the host:" >&2
        echo "    chown -R $APP_UID:$APP_UID ./data   # or: chmod -R a+rwX ./data" >&2
        echo "" >&2
        echo "  The container will stop rather than retry, so this message stays" >&2
        echo "  on screen." >&2
        exit 1
    fi
done

if [ -n "$as_app" ]; then
    exec $as_app "$@"
fi
exec "$@"
