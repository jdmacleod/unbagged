# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the UI.
#
# Node exists only here. The runtime image has no Node, no npm and no
# node_modules; it gets a directory of static files.
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build/frontend
# Dependencies first, so a source-only change does not reinstall them.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# --outDir overrides vite.config.ts, which points at the Python package in a
# checkout. Here the assets are copied into the runtime image instead.
RUN npm run build -- --outDir /static --emptyOutDir


# ---------------------------------------------------------------------------
# Stage 2 — the runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UNBAGGED_DB=/data/db/unbagged.sqlite \
    UNBAGGED_INCOMING=/data/incoming \
    UNBAGGED_STATIC=/app/static

WORKDIR /app

# gosu drops privileges in the entrypoint. ~2 MB, and the alternative is either
# running the app as root or failing on every Linux bind mount.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# Dependency metadata first: the layer that installs 80 MB of wheels should not
# be invalidated by editing a docstring.
COPY pyproject.toml README.md LICENSE ./
COPY src/unbagged/__init__.py ./src/unbagged/
RUN pip install --no-cache-dir .

COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

COPY --from=frontend /static /app/static

# The app runs as uid 10001. There is deliberately no `USER` line: the container
# starts as root so the entrypoint can chown the bind-mounted /data, then drops
# to unbagged with gosu before exec'ing anything. Asserting the effective uid is
# 10001 is therefore a RUNTIME test (tests/container/), not a grep for `USER` in
# this file — a grep would still pass if the drop were removed.
RUN useradd --create-home --uid 10001 unbagged \
    && mkdir -p /data/db /data/incoming \
    && chown -R unbagged:unbagged /data /app

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

# Binds 0.0.0.0 *inside the container only*. What decides who can reach it is
# the port publishing in docker-compose.yml, which is 127.0.0.1-scoped.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "unbagged.api:app", "--host", "0.0.0.0", "--port", "8000"]


# ---------------------------------------------------------------------------
# Stage 3 — development.
#
# Reinstalls the package editable so /app/src is what actually gets imported.
# Without this, `pip install .` puts the package in site-packages, /app is not
# on sys.path, and mounting ./src over /app/src changes nothing: uvicorn
# --reload notices the host edit, restarts, and re-imports the same unchanged
# copy. The reload message appears and the behaviour does not change, which is
# worse than no reload at all.
#
# Used only by docker-compose.dev.yml. The runtime stage above is what ships.
# ---------------------------------------------------------------------------
FROM runtime AS dev

# pip needs to write to site-packages, and this stage still starts as root
# (the entrypoint drops privileges at run time, exactly as in runtime).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps -e .

CMD ["uvicorn", "unbagged.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload", \
     "--reload-dir", "/app/src"]
