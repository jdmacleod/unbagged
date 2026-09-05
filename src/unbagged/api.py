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
