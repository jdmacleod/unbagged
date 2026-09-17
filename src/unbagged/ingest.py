"""Taking files from the user and turning them into a stored request.

This is the one place where uploaded bytes touch the disk, so it is also where
the data-handling rules are enforced rather than assumed:

* files land under `data/incoming/`, which is gitignored, outside the Docker
  build context, and covered by a pre-commit hook
* every file is hashed on the way in, and the same document is never ingested
  into the same request twice
* nothing is written anywhere else, and nothing leaves the machine unless a
  local vision model was configured and pointed somewhere other than this one,
  which `transcription/ollama.py` refuses until it is acknowledged explicitly

"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from unbagged import repository
from unbagged.adapters.registry import Match, registry
from unbagged.extraction import ZIP_MAGICS, ExtractionError, extract, probe
from unbagged.models import AdapterError, ParseResult, SourceBundle, SourceDocument

DEFAULT_INCOMING = Path("data/incoming")
INCOMING_ENV = "UNBAGGED_INCOMING"

# Enough to stop a hostile filename from escaping the incoming directory or
# colliding with a sibling. The original name is kept in the database.
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_NAME_LENGTH = 80

#: What an archive is allowed to become once it is open.
#:
#: The upload cap in `api.py` is 64 MB and it bounds the COMPRESSED bytes, which
#: is the wrong end of a decompression bomb: a few hundred kilobytes of zeroes
#: expands to gigabytes. These bound what comes out.
#:
#: 256 MB is four times what may be uploaded uncompressed, so nothing a person
#: could have sent as loose files is refused for arriving zipped. A bomb runs at
#: a thousand to one and more, so it is nowhere near this.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024

#: A response of screen captures is dozens of files; the observed one is 46.
#: This is generous against that and still a bound.
MAX_ARCHIVE_MEMBERS = 512

#: Metadata an archiver adds that is not part of anybody's response. Skipped
#: silently rather than reported: a macOS zip carries a `__MACOSX` shadow of
#: every file in it, and naming each one would bury the upload's real warnings
#: under a pile of notes about resource forks.
ARCHIVE_NOISE = ("__MACOSX/", ".DS_Store", "Thumbs.db", "desktop.ini")


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


#: The namespace an OOXML package declares inside `[Content_Types].xml`.
OOXML_CONTENT_TYPES = b"http://schemas.openxmlformats.org/package/2006/content-types"

#: What an ODF package's `mimetype` entry holds.
ODF_MIMETYPE_PREFIX = b"application/vnd.oasis.opendocument"


def looks_like_archive(content: bytes) -> bool:
    """Is this an archive of a response, rather than a single document?

    All three zip openings count, not only the one that carries a member: an
    empty archive is `PK\x05\x06` alone, and answering it as "a .zip file, which
    is not supported" would be the one sentence that is untrue. It is expanded
    like any other and refused for holding nothing readable, which is what it
    holds.

    **Zip-shaped is not the same as an archive**, and this is the second place
    that has to know it. `extraction.classify` tests `archive` LAST among its
    content checks and its comment says exactly why: `.xlsx`, `.docx` and `.odt`
    are zips, so a magic-bytes test placed first claims every one of them. This
    runs BEFORE `classify` ever sees the file — `store_upload_many` expands an
    archive into documents, and classification happens per document afterwards —
    so ordering cannot save it here and the check has to be made directly.

    Measured before this: a workbook dropped on the upload area was expanded
    into `[Content_Types].xml`, `workbook.xml` and `sheet1.xml`, three documents
    named after nothing the reader recognises, instead of reaching the message
    in `extraction.py` that tells them to save it as XML Spreadsheet 2003. The
    same test inside an archive refused a response for "containing another
    archive" and told the reader to unpack a spreadsheet.

    Decided by what is INSIDE, not by the filename, because the filename is the
    least reliable thing about a file that reached here through a mail client.
    """
    if content[:4] not in ZIP_MAGICS:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return not _is_office_package(archive)
    except (zipfile.BadZipFile, OSError):
        # Zip-shaped and unopenable. Still an archive as far as routing goes:
        # `_archive_members` produces the honest "could not be opened" message,
        # which is better than falling through to "unsupported format".
        return True


def _is_office_package(archive: zipfile.ZipFile) -> bool:
    """Is this zip an OOXML or ODF document rather than a bag of files?

    Asked of the package's STRUCTURE, not of an entry's name. A response archive
    is free to contain a file called `mimetype`, and matching the name alone
    would store that whole response as one document — which then reaches
    `extraction.classify`, is recognised as a zip, and is refused as "an archive
    inside an archive": a wrong answer wearing a confident message.

    Both formats declare themselves and both declarations are cheap to read:

    * **ODF** writes `mimetype` FIRST and stores it uncompressed, precisely so a
      reader can identify the package from the first bytes of the file without
      inflating anything. The content names the document type.
    * **OOXML** carries `[Content_Types].xml` at the root, declaring the package
      content-types namespace.

    Anything else zip-shaped is an archive, which is the safe direction: being
    expanded is recoverable, being stored whole and then refused is not.
    """
    names = archive.namelist()

    if names and names[0] == "mimetype":
        try:
            info = archive.getinfo("mimetype")
            # Stored, not deflated — the property that makes it readable without
            # inflating, and the one a coincidental `mimetype` will not have.
            if info.compress_type == zipfile.ZIP_STORED:
                return archive.read("mimetype").startswith(ODF_MIMETYPE_PREFIX)
        except (KeyError, zipfile.BadZipFile, OSError):
            return False

    if "[Content_Types].xml" in names:
        try:
            return OOXML_CONTENT_TYPES in archive.read("[Content_Types].xml")
        except (KeyError, zipfile.BadZipFile, OSError):
            return False

    return False


def store_upload_many(
    filename: str,
    content: bytes,
    *,
    directory: Path | None = None,
    budget: int = MAX_ARCHIVE_BYTES,
) -> list[StoredFile]:
    """One uploaded file, stored — or an archive, stored as its members.

    A response can arrive as a folder of screen captures, a page or two per
    visit, dozens of files. It arrives as a zip, and the alternative to this is
    telling the reader to unpack it and select every file by hand: miss one and
    the response is short a visit, with nothing on screen saying which.

    **Members become separate documents, never one.** `SourceBundle` is what
    carries per-document provenance, and `_document_id_resolver` maps a bundle
    index onto a stored row — so flattening an archive into a single document
    would put every record in it behind one citation, and a reader following one
    would land on a zip rather than on the page their basket came from.

    `budget` is what this archive may expand to. The caller passes what is LEFT
    of the request's allowance rather than the whole of it, because the cap has
    to hold across an upload and not merely within one file: ten archives each
    honouring a per-file cap is ten times the cap on disk.
    """
    if not looks_like_archive(content):
        return [store_upload(filename, content, directory=directory)]
    return [
        store_upload(name, member, directory=directory)
        for name, member in _archive_members(filename, content, budget)
    ]


def _archive_members(filename: str, content: bytes, budget: int) -> list[tuple[str, bytes]]:
    """What is inside an archive, once it has been shown to be safe to open.

    Every guard here bounds something the archive itself declares, because an
    archive is a description of files written by whoever sent it and none of it
    is true until it has been read.

    * **A member whose name escapes the extraction root is refused** — absolute
      paths, `..`, and Windows drive letters. Nothing here writes to a member's
      own name (`store_upload` hashes and sanitises it), so this is defence in
      depth rather than the only thing standing in the way. It is refused
      loudly: a response should not contain one, and quietly dropping it would
      hide that it did.
    * **The entry count is bounded before anything is filtered.** Counting only
      the members that survived filtering meant an archive could carry hundreds
      of thousands of directory and metadata entries and never reach the cap,
      and every one of them was still iterated.
    * **No member is read whole.** The uncompressed total is capped, which the
      upload limit cannot do — it bounds the compressed bytes, and that is the
      wrong end of a bomb. Reading the member and THEN measuring it defeats the
      cap it is enforcing: a single entry inside a 64 MB archive expands to
      gigabytes in memory before any comparison happens. Each member is read to
      one byte past what is left of the budget, so what is held at any moment is
      bounded by the budget rather than by what the archive claims.
    * **An archive inside an archive is refused rather than recursed.** It is a
      real shape and a reader can unpack it; recursion here would be a second
      unbounded thing to bound.

    `extraction.py` refuses a DTD before parsing XML for the same class of
    reason, and that guard is the model this follows.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise IngestError(
            f"{filename} looks like a zip but could not be opened ({exc}). "
            "If it was downloaded, the download may be incomplete."
        ) from exc

    if len(entries) > MAX_ARCHIVE_MEMBERS:
        raise IngestError(
            f"{filename} declares more than {MAX_ARCHIVE_MEMBERS} entries. A "
            "right-to-know response is a document, not a data lake."
        )

    found: list[tuple[str, bytes]] = []
    remaining = budget
    for info in entries:
        name = info.filename
        if info.is_dir() or _is_noise(name):
            continue
        if _escapes(name):
            raise IngestError(
                f"{filename} contains an entry whose name points outside the "
                f"archive ({name!r}). Nothing in it has been read."
            )
        try:
            with archive.open(info) as handle:
                # One byte past the budget is all it takes to prove the budget
                # is blown, and is the most this will ever hold for one member.
                member = handle.read(remaining + 1)
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            # RuntimeError is what `zipfile` raises for an encrypted member,
            # which is a thing a person can fix and should be told about.
            raise IngestError(
                f"{filename} could not be unpacked ({exc}). If it is password "
                "protected, unpack it yourself and upload what is inside."
            ) from exc

        if len(member) > remaining:
            # The REMAINING budget, not the constant. One upload may hold several
            # archives and they share the allowance, so the second one to blow it
            # was being told it exceeded 256 MB when what it actually exceeded
            # was whatever the first one left — a number the reader could not
            # reconcile with the file in front of them.
            raise IngestError(
                f"{filename} expands to more than {_size(remaining)}, which is what is "
                f"left of the {_size(MAX_ARCHIVE_BYTES)} one upload may unpack to in "
                "total. Nothing in it has been kept."
            )
        remaining -= len(member)
        if not member:
            # An empty file carries nothing, and `store_upload` refuses one by
            # name. Skipped rather than raised: an archive holding a stray empty
            # file is not a response anybody needs to fix.
            continue
        if looks_like_archive(member):
            raise IngestError(
                f"{filename} contains another archive ({Path(name).name!r}). "
                "Unpack the inner one and upload what is in it."
            )
        found.append((name, member))

    if not found:
        raise IngestError(f"{filename} is an archive with nothing readable in it.")
    names = _names_for(found)
    return [(name, member) for name, (_, member) in zip(names, found, strict=True)]


def _names_for(found: list[tuple[str, bytes]]) -> list[str]:
    """What each member is called once it is out of the archive.

    Its own name without the folder it sat in, because the capture reader takes
    a visit's date out of the name the store's export produced and a folder
    prefix is not part of that — **unless two members would then share a name.**

    That case loses a visit, which is the whole thing this feature exists to
    prevent. `adapter._captures` maps captures by `original_filename` and keeps
    the last of any duplicate, so flattening `visit-a/x.png` and `visit-b/x.png`
    onto one name discards a capture and its basket with it. The adapter warns,
    and tells the reader to rename the files — advice nobody can act on when it
    was the unpacking that collided them.

    Where a basename repeats, every member keeps its path instead. Nothing is
    lost by that: `receipt._stem` strips a leading path before matching, so
    `capture_date` and `group_by_visit` read a path exactly as they read a bare
    name.
    """
    bare = [Path(name).name for name, _ in found]
    if len(set(bare)) == len(bare):
        return bare
    return [name.lstrip("./") for name, _ in found]


def _size(count: int) -> str:
    """Bytes in the unit that still says something.

    Flooring the remainder to whole megabytes reported "more than 0 MB" once the
    allowance was nearly spent, which tells a reader nothing they can check their
    file against.
    """
    if count >= 1024 * 1024:
        return f"{count // (1024 * 1024)} MB"
    if count >= 1024:
        return f"{count // 1024} KB"
    return f"{count} bytes"


def _is_noise(name: str) -> bool:
    return name.startswith(ARCHIVE_NOISE) or Path(name).name in ARCHIVE_NOISE


def _escapes(name: str) -> bool:
    """Would this member's name reach outside the directory it belongs in?

    Written against the NAME as the archive states it, using both separators:
    a zip written on Windows may use backslashes, and `PurePosixPath` would read
    `..\\..\\etc` as one harmless-looking filename.
    """
    if name.startswith("/") or name.startswith("\\"):
        return True
    if re.match(r"^[A-Za-z]:", name):
        return True
    return any(part == ".." for part in re.split(r"[/\\]", name))


def _stored_document(f: StoredFile) -> SourceDocument:
    """One stored file, with what it is recorded alongside where it lives.

    `media_type` was hardcoded to None here and `page_count` was never set at
    all, so both columns were NULL for every document ever stored — while
    `repository.py` wrote and read them, `ExtractedDocument` carried them, and
    `tests/test_extraction.py` asserted them at the extraction layer. The values
    simply never travelled the last step (#46).

    Nothing reads them yet, which is why nothing looked wrong. `extraction.py`'s
    own docstring says provenance has to answer "which page of this 48-page
    PDF"; the first thing to render "page 12 of 48" would have worked in tests
    and been blank in production.

    Probed rather than extracted: the adapter has already read the text and this
    only needs the shape of the file. Measured on a 48-page PDF, 18ms against
    106ms for a second full extraction.
    """
    facts = probe(
        SourceDocument(original_filename=f.original_filename, sha256=f.sha256, path=str(f.path))
    )
    return SourceDocument(
        original_filename=f.original_filename,
        sha256=f.sha256,
        media_type=facts.media_type if facts else None,
        page_count=facts.page_count if facts else None,
        path=str(f.path),
    )


def bundle_from(files: list[StoredFile], declared_retailer: str | None = None) -> SourceBundle:
    """The uploaded files as an adapter sees them, each carrying its position.

    `id` is the document's **index in this bundle**, not a database id — the
    rows do not exist yet. An adapter that copies it onto a record's provenance
    is saying "this came from the third file you handed me", and `_save()` turns
    that into the real id once the documents are written. Before this, `id` was
    left None and `_save()` credited every row to the first document, which is
    invisible in a one-file bundle and wrong in every other kind.
    """
    documents = tuple(
        SourceDocument(
            original_filename=f.original_filename,
            sha256=f.sha256,
            path=str(f.path),
            id=index,
        )
        for index, f in enumerate(files)
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

    already = _already_ingested(conn, seen)
    if already:
        # The check above only covered duplicates within a single upload. Dropping
        # the same report a second time — which is what people do when a 13-second
        # parse gives no immediate sign of progress — created a second request
        # identical to the first: same retailer, same reference, same everything.
        # The selector then showed two options with the same label and no way to
        # tell them apart, Compare grew a duplicate column, and nothing in the UI
        # could delete either one. The removal control this message points at
        # now exists, at the foot of the page beside the upload box.
        raise IngestError(
            f"You have already loaded this response, as {already}. Loading it "
            "again would create a second copy you could not tell apart from the "
            "first. If you meant to replace it, remove the existing one from the "
            "foot of the page first."
        )

    bundle = bundle_from(files, declared_retailer)
    match = registry.select(bundle)
    if match is None:
        raise IngestError(_why_nothing_matched(bundle))

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

    documents = tuple(_stored_document(f) for f in files)
    request_id = _save(conn, result, documents)
    return IngestResult(request_id=request_id, match=match, result=result)


def _already_ingested(conn: sqlite3.Connection, hashes: set[str]) -> str | None:
    """Name an existing request holding any of these documents, if one exists.

    Documents are hashed on the way in, so an identical file is identical
    bytes. The unique index on source_document is per request, which stops the
    same file appearing twice inside one request and does nothing about the same
    file arriving as a second request.
    """
    if not hashes:
        return None
    placeholders = ",".join("?" for _ in hashes)
    row = conn.execute(
        f"SELECT r.display_name, r.id FROM source_document d "  # noqa: S608
        f"JOIN request r ON r.id = d.request_id "
        f"WHERE d.sha256 IN ({placeholders}) ORDER BY r.id LIMIT 1",
        tuple(hashes),
    ).fetchone()
    return f'"{row["display_name"]}"' if row else None


def _why_nothing_matched(bundle: SourceBundle) -> str:
    """Explain a failed upload using the reason the code already computed.

    Every adapter's `sniff()` swallows extraction failures, because a sniff must
    not raise. The consequence was that a .zip and a scanned PDF — the two most
    likely things to arrive after a Kroger PDF — both produced a message telling
    the user to go read the adapter-authoring guide, while `extraction.py` had
    already worked out that one needed unpacking and the other had no text
    layer. A zip no longer reaches here at all: `store_upload_many` expands one
    into its members before a bundle exists.

    Re-extracting here is deliberate: a few wasted seconds on a file that was
    never going to parse, in exchange for telling the person what is actually
    wrong with it.
    """
    reasons: list[str] = []
    readable = 0
    for document in bundle.documents:
        try:
            extract(document)
            readable += 1
        except ExtractionError as exc:
            message = str(exc)
            if message not in reasons:
                reasons.append(message)
        except Exception:  # noqa: S110 - the swallow is the behaviour, see below
            # A malformed file that fails in some other way is still unreadable;
            # it just has no message worth quoting. Deliberately not logged: this
            # runs only to explain a failed upload, and the caller already reports
            # the outcome to the person who is standing there.
            pass

    if reasons and not readable:
        return " ".join(reasons)
    if reasons:
        # A mixed bundle: some files read, some did not. Name both halves rather
        # than picking one and implying the whole upload failed for that reason.
        return (
            f"{len(reasons)} of the uploaded files could not be read: "
            + " ".join(reasons)
            + " The remaining files were readable but matched no adapter."
        )
    # Everything extracted cleanly; the format itself is simply unknown.
    return (
        "This file was readable, but no adapter recognised the format. If you "
        "know which retailer sent it, say so on the upload form; otherwise it "
        "may need a new adapter — see docs/writing-an-adapter.md."
    )


def _save(conn, result: ParseResult, documents) -> int:
    """Persist, attaching provenance to the document rows the adapter referenced.

    The index-to-id mapping lives in `repository.save_parse_result`, which is
    where the ids come into existence — see `_document_id_resolver`.

    This used to run an UPDATE afterwards setting every row to the FIRST
    document's id unconditionally. With one file in the bundle that is the same
    answer; with two it is not, and a response arriving as a spreadsheet plus a
    folder of receipt captures would have credited every line item to the
    spreadsheet — a citation pointing at a document that does not contain the
    value it cites.
    """
    return repository.save_parse_result(conn, result, documents=documents)


def received_at() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
