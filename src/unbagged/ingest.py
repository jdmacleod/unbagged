"""Taking files from the user and turning them into a stored request.

This is the one place where uploaded bytes touch the disk, so it is also where
the data-handling rules are enforced rather than assumed:

* files land under `data/incoming/`, which is gitignored, outside the Docker
  build context, and covered by a pre-commit hook
* every file is hashed on the way in, and the same document is never ingested
  into the same request twice
* nothing is written anywhere else, and nothing leaves the machine
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from unbagged import repository
from unbagged.adapters.registry import Match, registry
from unbagged.models import AdapterError, ParseResult, SourceBundle, SourceDocument

DEFAULT_INCOMING = Path("data/incoming")
INCOMING_ENV = "UNBAGGED_INCOMING"

# Enough to stop a hostile filename from escaping the incoming directory or
# colliding with a sibling. The original name is kept in the database.
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_NAME_LENGTH = 80


class IngestError(Exception):
    """Something about the upload itself is wrong, phrased for the uploader."""


@dataclass(frozen=True)
class StoredFile:
    path: Path
    sha256: str
    original_filename: str
    size: int


@dataclass(frozen=True)
class IngestResult:
    request_id: int
    match: Match
    result: ParseResult

    @property
    def summary(self) -> dict[str, int]:
        return {
            "identities": len(self.result.identities),
            "transactions": len(self.result.transactions),
            "items": self.result.item_count(),
            "inferences": len(self.result.inferences),
            "disclosures": len(self.result.disclosures),
            "follow_ups": len(self.result.follow_ups),
            "warnings": len(self.result.warnings),
        }


def incoming_dir() -> Path:
    return Path(os.environ.get(INCOMING_ENV) or DEFAULT_INCOMING)


def safe_filename(name: str) -> str:
    """A filename safe to write, derived from one we did not choose."""
    name = unicodedata.normalize("NFKD", name or "")
    name = SAFE_NAME.sub("_", Path(name).name).strip("._") or "upload"
    if len(name) > MAX_NAME_LENGTH:
        stem, dot, suffix = name.rpartition(".")
        keep = MAX_NAME_LENGTH - len(suffix) - 1 if dot else MAX_NAME_LENGTH
        name = f"{stem[:keep]}.{suffix}" if dot else name[:MAX_NAME_LENGTH]
    return name


def store_upload(filename: str, content: bytes, *, directory: Path | None = None) -> StoredFile:
    """Write one uploaded file into the incoming directory and hash it."""
    if not content:
        raise IngestError(f"{filename or 'The uploaded file'} is empty.")
    target_dir = directory or incoming_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(content).hexdigest()
    safe = safe_filename(filename)
    # Prefixing with the hash makes writes idempotent and stops two uploads
    # called "report.pdf" from overwriting each other.
    path = target_dir / f"{digest[:12]}_{safe}"
    if not path.exists():
        path.write_bytes(content)
    return StoredFile(
        path=path, sha256=digest, original_filename=filename or safe, size=len(content)
    )


def bundle_from(files: list[StoredFile], declared_retailer: str | None = None) -> SourceBundle:
    documents = tuple(
        SourceDocument(
            original_filename=f.original_filename,
            sha256=f.sha256,
            path=str(f.path),
        )
        for f in files
    )
    return SourceBundle(documents=documents, declared_retailer=declared_retailer)


def ingest(
    conn: sqlite3.Connection,
    files: list[StoredFile],
    *,
    declared_retailer: str | None = None,
) -> IngestResult:
    """Store, identify, parse and persist one request.

    The adapter is chosen by content, not by what the user typed on the form:
    people mislabel, and the report itself is the evidence.
    """
    if not files:
        raise IngestError("No files were uploaded.")

    seen = {f.sha256 for f in files}
    if len(seen) != len(files):
        raise IngestError(
            "The same file was uploaded more than once. Remove the duplicate and "
            "try again — ingesting it twice would double every basket in it."
        )

    bundle = bundle_from(files, declared_retailer)
    match = registry.select(bundle)
    if match is None:
        raise IngestError(
            "No adapter recognised this response. If you know which retailer sent "
            "it, say so on the upload form; otherwise it may need a new adapter — "
            "see docs/writing-an-adapter.md."
        )

    try:
        result = match.adapter.parse(bundle)
    except AdapterError:
        raise
    except Exception as exc:  # a bug in an adapter, phrased for a person
        raise AdapterError(
            f"The {match.adapter.display_name} adapter failed while reading this "
            f"response ({exc}). Please report it with a sanitised skeleton — see "
            "CONTRIBUTING.md — and never attach the report itself."
        ) from exc

    documents = tuple(
        SourceDocument(
            original_filename=f.original_filename,
            sha256=f.sha256,
            media_type=None,
            path=str(f.path),
        )
        for f in files
    )
    request_id = _save(conn, result, documents)
    return IngestResult(request_id=request_id, match=match, result=result)


def _save(conn, result: ParseResult, documents) -> int:
    """Persist, attaching provenance to the document rows the adapter referenced.

    Adapters set `source_document_id` before the documents have database ids,
    because they have to reference something while parsing. The ids are assigned
    here, so the references are rewritten to match.
    """
    request_id = repository.save_parse_result(conn, result, documents=documents)
    stored = repository.get_documents(conn, request_id)
    if stored:
        first = stored[0].id
        with repository.transaction(conn):
            for table in ("identity", "txn", "inference", "disclosure"):
                conn.execute(
                    f"UPDATE {table} SET source_document_id = ? WHERE request_id = ?",
                    (first, request_id),
                )
    return request_id


def received_at() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
