-- Stop `request.id` being handed out twice.
--
-- `id INTEGER PRIMARY KEY` is a rowid alias, and SQLite reuses the highest
-- rowid once its row is deleted. Delete the newest response and upload another,
-- and the new one is handed the id the old one had. Two things then go wrong,
-- and both were filed:
--
--   * #54 — the upload report panel is pinned to the response it describes by
--     id and retailer. A reused id from the SAME retailer passes both halves,
--     so the panel shows one response's counts and warnings over another's.
--   * #64 — the same id in the URL. `?r=5` is pushed into browser history, the
--     response is removed, the next upload takes id 5, and Back onto the older
--     entry renders a different response than the one that entry meant.
--
-- Guarding each surface separately was the alternative, and it is the shape
-- that produced both issues: the id-only check was tightened to id-and-retailer
-- for #47, which closed one case and left the other. AUTOINCREMENT closes the
-- class instead — the id is never reused, so no consumer has to prove it is
-- looking at the response it thinks it is.
--
-- The cost is a table rebuild, because SQLite cannot add AUTOINCREMENT in
-- place. `db.migrate()` disables foreign keys around every migration for this
-- reason and runs `foreign_key_check` afterwards; see the docstring there.
-- Without that, `DROP TABLE request` below performs an implicit DELETE FROM,
-- fires the seven ON DELETE CASCADEs pointing at it, and empties the database.
--
-- Existing ids are preserved, so URLs and bookmarks that already name a
-- response keep working. `sqlite_sequence` picks up the highest of them, which
-- is what makes the next id follow rather than collide.

CREATE TABLE request_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retailer_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    report_reference TEXT,              -- retailer's own report ID
    submitted_at TEXT,
    received_at TEXT,
    statute TEXT DEFAULT 'CCPA',
    period_start TEXT,
    period_end TEXT,
    adapter_schema_version INTEGER
);

INSERT INTO request_new (
    id, retailer_id, display_name, report_reference, submitted_at,
    received_at, statute, period_start, period_end, adapter_schema_version
)
SELECT
    id, retailer_id, display_name, report_reference, submitted_at,
    received_at, statute, period_start, period_end, adapter_schema_version
FROM request;

DROP TABLE request;

ALTER TABLE request_new RENAME TO request;
