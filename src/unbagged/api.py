"""The read API, plus upload.

Local-first and single-user: no accounts, no sessions, no telemetry, no outbound
requests. The server binds to 127.0.0.1 by default (see `main()`), because the
alternative is publishing someone's two years of groceries to their LAN.

Endpoints are shaped by the four views rather than by the tables; see views.py.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from unbagged import __version__, db, ingest, letters, repository, views
from unbagged.adapters import registry
from unbagged.models import AdapterError

log = logging.getLogger(__name__)

# Uploads are read into memory to be hashed. A right-to-know response is a
# document, not a data lake; anything past this is not one.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

STATIC_DIR_ENV = "UNBAGGED_STATIC"
DEFAULT_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrate once at startup, then hand out a connection per request. SQLite
    # objects belong to the thread that created them, and FastAPI runs sync
    # endpoints in a threadpool, so a single shared connection is not an option.
    # Opening one per request costs microseconds and keeps WAL doing its job.
    conn = db.connect()
    try:
        db.migrate(conn)
    finally:
        conn.close()
    yield


app = FastAPI(
    title="unbagged",
    version=__version__,
    summary="Read what the grocery store knows about you.",
    lifespan=lifespan,
)


# What a served document is allowed to do. Every source is `self` because the
# build genuinely needs nothing else: `index.html` carries one external module
# script and one external stylesheet, both under /assets, and the built CSS has
# no `url()`, no `@font-face` and no `data:` URI. There are no font files on
# disk — the mono is a CSS stack — and React's `style` prop sets properties
# through the CSSOM rather than emitting a style attribute, which `style-src`
# does not gate. So no `'unsafe-inline'` is needed anywhere, which is the rare
# case where a strict policy costs nothing.
#
# `img-src 'self'` deliberately omits `data:`. Nothing in the app loads a data:
# image, and `frontend/vite.config.ts` sets `assetsInlineLimit: 0` so nothing
# starts to. Allowing it would widen the directive that most limits what
# injected markup can pull in, to permit something no page uses.
#
# `form-action 'none'` because there is not one `<form>` element in the UI;
# uploads go through the API. A form is otherwise a way to send data somewhere
# else that `connect-src` never sees.
#
# The reason any of this matters here: the SPA route serves every file in the
# bundle, including `/unbagged-logo.svg`, and a top-level navigation to an SVG
# executes its script in this origin, next to report data in the same browser
# profile. `tools/build_brand.py --check` already refuses a served SVG carrying
# a script, so this is defence that does not depend on that check being right.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "img-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'none'",
))

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
)


class SecurityHeaders:
    """Attach the security headers to every HTTP response.

    A plain ASGI wrapper rather than `@app.middleware("http")`. Both are
    correct — BaseHTTPMiddleware was measured on starlette 1.6.0 and preserved
    Content-Length and Accept-Ranges on a 2 MB FileResponse — but it wraps each
    response in an extra task and re-emits it as a streaming response, which is
    machinery this does not need to set two constant headers, and which is one
    more thing to reason about the next time background tasks or exception
    handling change.

    Setting them here rather than per route is what makes the guarantee whole:
    the app answers on three paths, and only a wrapper sees all three.

        request ──▶ SecurityHeaders ──┬──▶ API routes        JSON, 200/400/422
                                      ├──▶ /assets mount     StaticFiles
                                      └──▶ SPA route         FileResponse
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                # Replace rather than append: a route that sets its own copy
                # later would otherwise ship two, and browsers enforce the
                # intersection of every policy they are given.
                ours = {name for name, _ in SECURITY_HEADERS}
                kept = [(k, v) for k, v in message["headers"] if k.lower() not in ours]
                message["headers"] = kept + list(SECURITY_HEADERS)
            await send(message)

        await self.app(scope, receive, with_headers)


def _headers_for_a_response_the_middleware_never_sees() -> dict[str, str]:
    return {name.decode(): value.decode() for name, value in SECURITY_HEADERS}


async def unhandled_error(_request: Any, exc: Exception) -> JSONResponse:
    """The one response `SecurityHeaders` cannot decorate, so it sets its own.

    Starlette builds the stack as ServerErrorMiddleware -> user middleware ->
    router. An exception nothing else catches unwinds past the wrapper, and
    ServerErrorMiddleware emits its 500 through the original `send` — so the
    wrapper never sees the message and the response ships with no policy at all.
    Measured: 500 with `content-security-policy: None` before this existed.

    Registering a handler does not move the response back inside the wrapper;
    it still leaves by the outer path. What it does is let the response carry
    the headers itself, from the same constant, so there is one policy in the
    codebase and not two that can drift.

    The body says nothing about what failed. A traceback in an error page is a
    disclosure, and this app's errors are about a document on someone's disk.
    """
    log.exception("unhandled error serving a request", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers=_headers_for_a_response_the_middleware_never_sees(),
    )


def add_security_headers(application: FastAPI = app) -> None:
    """Install the headers on `application`.

    A function rather than a bare `app.add_middleware` call because tests build
    their own app: the fast test job never builds the frontend, so `api.app` has
    no static routes there and `mount_frontend(FastAPI())` against a tmp_path
    bundle is the only way to exercise a FileResponse. Wiring the middleware
    only onto the module-level app would leave the header missing from exactly
    the app that covers that path.
    """
    application.add_middleware(SecurityHeaders)
    # Covers the 500 the middleware structurally cannot reach. See above.
    application.add_exception_handler(Exception, unhandled_error)


add_security_headers()


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@app.exception_handler(ingest.IngestError)
async def _ingest_error(_request, exc: ingest.IngestError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(AdapterError)
async def _adapter_error(_request, exc: AdapterError) -> JSONResponse:
    # Adapter messages are written to be shown to a person verbatim.
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _require_request(conn: sqlite3.Connection, request_id: int) -> dict[str, Any]:
    meta = repository.get_request(conn, request_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"No request with id {request_id}.")
    return {"id": request_id, **meta.__dict__}


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.get("/api/adapters")
def adapters() -> dict[str, Any]:
    """Which retailers are supported, for the upload form's hint field."""
    return {
        "adapters": [
            {
                "retailer_id": a.retailer_id,
                "display_name": a.display_name,
                "schema_version": a.schema_version,
            }
            for a in registry.all()
        ]
    }


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


@app.get("/api/requests")
def list_requests(conn: Conn) -> dict[str, Any]:
    return {
        "requests": [
            {"id": request_id, **meta.__dict__}
            for request_id, meta in repository.list_requests(conn)
        ]
    }


@app.post("/api/requests", status_code=201)
def create_request(
    conn: Conn,
    files: Annotated[list[UploadFile], File()],
    declared_retailer: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Upload one retailer's response and parse it.

    The declared retailer is a hint, not an instruction: the adapter is chosen by
    what the documents contain.

    Deliberately `def`, not `async def`. Parsing a real report is around 14
    seconds of pdfplumber, and on an `async def` endpoint that runs on the event
    loop, so every other request queues behind it — including the container's own
    HEALTHCHECK, which then times out and reports the container unhealthy while
    it is merely busy. A sync endpoint runs in the threadpool instead, so the
    upload is slow for the person uploading and for nobody else.

    `upload.file.read()` rather than `await upload.read()` for the same reason:
    it is the synchronous accessor on the same spooled file.
    """
    stored = []
    total = 0
    for upload in files:
        content = upload.file.read()
        total += len(content)
        if total > MAX_UPLOAD_BYTES:
            raise ingest.IngestError(
                f"The upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. A "
                "right-to-know response is a document, not a data lake — if this "
                "is an archive, extract it and upload the report itself."
            )
        stored.append(ingest.store_upload(upload.filename or "upload", content))

    result = ingest.ingest(conn, stored, declared_retailer=declared_retailer)
    return {
        "request_id": result.request_id,
        "retailer_id": result.match.adapter.retailer_id,
        "display_name": result.match.adapter.display_name,
        "confidence": round(result.match.confidence, 2),
        # A weak match is presented as a guess rather than a finding.
        "confident": result.match.is_confident,
        "summary": result.summary,
        "warnings": [
            {"severity": str(w.severity), "message": w.message, "locator": w.locator}
            for w in result.result.warnings
        ],
    }


@app.get("/api/requests/{request_id}")
def get_request(conn: Conn, request_id: int) -> dict[str, Any]:
    meta = _require_request(conn, request_id)
    return {
        **meta,
        "documents": [d.__dict__ for d in repository.get_documents(conn, request_id)],
        "warnings": [
            {"severity": str(w.severity), "message": w.message, "locator": w.locator}
            for w in repository.get_warnings(conn, request_id)
        ],
    }


@app.delete("/api/requests/{request_id}", status_code=204)
def delete_request(conn: Conn, request_id: int) -> None:
    _require_request(conn, request_id)
    repository.delete_request(conn, request_id)


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


@app.get("/api/requests/{request_id}/timeline")
def timeline(
    conn: Conn,
    request_id: int,
    store: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: Annotated[str | None, Query(description="match line description or UPC")] = None,
) -> dict[str, Any]:
    _require_request(conn, request_id)
    return views.timeline(
        conn, request_id, store=store, date_from=date_from, date_to=date_to, query=q
    )


@app.get("/api/transactions/{txn_id}")
def transaction(conn: Conn, txn_id: int) -> dict[str, Any]:
    detail = views.transaction_detail(conn, txn_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No transaction with id {txn_id}.")
    return detail


@app.get("/api/requests/{request_id}/profile")
def profile(conn: Conn, request_id: int) -> dict[str, Any]:
    _require_request(conn, request_id)
    return views.profile(conn, request_id)


@app.get("/api/compliance")
def compliance(conn: Conn) -> dict[str, Any]:
    return views.compliance(conn)


@app.get("/api/compare")
def compare(conn: Conn) -> dict[str, Any]:
    return views.compare(conn)


@app.get("/api/requests/{request_id}/price-history")
def price_history(
    conn: Conn,
    request_id: int,
    min_observations: Annotated[int, Query(ge=2, le=100)] = 3,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
) -> dict[str, Any]:
    _require_request(conn, request_id)
    return views.price_history(
        conn, request_id, min_observations=min_observations, limit=limit
    )


@app.get("/api/requests/{request_id}/product-index")
def product_index(
    conn: Conn,
    request_id: int,
    q: Annotated[str | None, Query(description="match product name or UPC")] = None,
    min_purchases: Annotated[int, Query(ge=1, le=100)] = 1,
    limit: Annotated[int, Query(ge=1, le=5000)] = views.INDEX_LIMIT,
) -> dict[str, Any]:
    _require_request(conn, request_id)
    return views.product_index(
        conn, request_id, query=q, min_purchases=min_purchases, limit=limit
    )


@app.get("/api/requests/{request_id}/follow-up-letter")
def follow_up_letter(conn: Conn, request_id: int) -> dict[str, Any]:
    """Draft a supplemental request naming the categories that went unanswered.

    Plain text the user reads and sends themselves. The tool never sends
    anything; see letters.py.
    """
    _require_request(conn, request_id)
    meta = repository.get_request(conn, request_id)
    return letters.draft_follow_up(
        meta,
        repository.get_disclosures(conn, request_id),
        repository.get_follow_ups(conn, request_id),
    )


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------


def static_dir() -> Path:
    return Path(os.environ.get(STATIC_DIR_ENV) or DEFAULT_STATIC_DIR)


def mount_frontend(application: FastAPI = app) -> bool:
    """Serve the built UI from the same process, if it has been built.

    One container, one service: a separate frontend container would be friction
    with no payoff for a single local user.
    """
    directory = static_dir()
    root = directory.resolve()
    if not (root / "index.html").is_file():
        return False

    if (root / "assets").is_dir():
        application.mount(
            "/assets", StaticFiles(directory=root / "assets"), name="assets"
        )

    # Every file the bundle contains, mapped from its request path to the Path
    # that serves it. The request then only *selects* an entry, so no path is
    # ever built out of user input — the handler below has nothing to validate
    # because it constructs nothing.
    #
    # `is_file()` follows symlinks, so a link inside the bundle pointing out of
    # it reads as a perfectly good file and would be served. Each entry is
    # therefore resolved and checked for containment here. That is the same
    # guard the handler used to run per request, moved to startup where it runs
    # on paths that came from the filesystem rather than from a request. It is
    # not decoration: without it this map serves the link's target, which is a
    # real traversal escape and the one no static analyser has ever flagged. The
    # symlink case in TestStaticRouteTraversal is what caught it.
    #
    # The bundle is written at image build time and does not change under a
    # running container, so reading it once is a fact about the image rather
    # than a cache. Hashed assets never reach this map at all: /assets is a
    # StaticFiles mount that reads the filesystem per request. What remains here
    # is index.html and any root-level file a build emits beside it, so a new
    # one added while `make serve` is running needs a restart to be served.
    servable: dict[str, Path] = {}
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved.is_relative_to(root):
            servable[candidate.relative_to(root).as_posix()] = resolved
    shell = root / "index.html"

    @application.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = servable.get(path)
        if target is not None:
            return FileResponse(target)
        # Anything else is a client-side route, so the shell answers it.
        return FileResponse(shell)

    return True


mount_frontend()
