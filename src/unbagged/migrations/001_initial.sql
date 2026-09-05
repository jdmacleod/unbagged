-- Canonical schema (docs/handoff.md §5).
--
-- Every table carries request_id so a single database can hold responses from
-- several retailers and still keep them separable. Timestamps are ISO-8601 UTC
-- strings: SQLite has no date type, and the retailers' own formats vary enough
-- that normalising on write is the only way to make the timeline sortable.

CREATE TABLE request (
    id INTEGER PRIMARY KEY,
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

CREATE TABLE source_document (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    original_filename TEXT,
    sha256 TEXT NOT NULL,
    media_type TEXT,
    page_count INTEGER
);

CREATE TABLE identity (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    id_type TEXT NOT NULL,     -- loyalty_card|alternate_id|household|internal_person|email|phone|address
    value TEXT NOT NULL,
    scope TEXT,                -- individual|household
    first_seen TEXT,
    source_document_id INTEGER REFERENCES source_document(id),
    locator TEXT
);

CREATE TABLE txn (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    external_order_id TEXT,
    occurred_at TEXT NOT NULL,
    store_code TEXT,
    division_code TEXT,
    channel TEXT,              -- in_store|online|fuel|pharmacy
    tender_type TEXT,
    total_pre_discount REAL,
    source_document_id INTEGER REFERENCES source_document(id),
    locator TEXT
);

CREATE TABLE txn_item (
    id INTEGER PRIMARY KEY,
    txn_id INTEGER REFERENCES txn(id) ON DELETE CASCADE,
    description_raw TEXT NOT NULL,
    upc TEXT,
    quantity REAL,
    retail_amt REAL,
    loyalty_amt REAL,
    category TEXT,             -- nullable, filled by the enrichment pass
    category_confidence REAL
);

CREATE TABLE inference (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    value_raw TEXT NOT NULL,
    value_num REAL,
    scale TEXT,                -- categorical|ordinal_1_7|currency|count|prose
    subject TEXT,              -- individual|household
    origin TEXT NOT NULL,      -- first_party_model|appended_third_party|unknown
    derivable_from_txns INTEGER,   -- 0/1/NULL, adapter's judgment, shown as a caveat
    source_document_id INTEGER REFERENCES source_document(id),
    locator TEXT
);

CREATE TABLE disclosure (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    status TEXT NOT NULL,      -- provided|partial|absent
    evidence TEXT,             -- quoted or summarised, may be NULL when absent
    notes TEXT,
    source_document_id INTEGER REFERENCES source_document(id),
    locator TEXT
);

CREATE TABLE follow_up (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    kind TEXT,                 -- supplemental_period|missing_category|clarification
    description TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE parse_warning (
    id INTEGER PRIMARY KEY,
    request_id INTEGER REFERENCES request(id) ON DELETE CASCADE,
    severity TEXT,
    message TEXT,
    locator TEXT
);

-- Indexes follow the four views. The timeline sorts by date within a request;
-- the profile and compliance views filter by request; price history groups line
-- items by UPC across the whole coverage window.
CREATE INDEX idx_txn_request_occurred ON txn(request_id, occurred_at);
CREATE INDEX idx_txn_item_txn ON txn_item(txn_id);
CREATE INDEX idx_txn_item_upc ON txn_item(upc);
CREATE INDEX idx_identity_request ON identity(request_id);
CREATE INDEX idx_inference_request_origin ON inference(request_id, origin);
CREATE INDEX idx_disclosure_request ON disclosure(request_id, category);
CREATE INDEX idx_follow_up_request ON follow_up(request_id);
CREATE INDEX idx_parse_warning_request ON parse_warning(request_id);
CREATE INDEX idx_source_document_request ON source_document(request_id);

-- A document is ingested once. Re-uploading the same file must not silently
-- duplicate a year of baskets.
CREATE UNIQUE INDEX idx_source_document_sha ON source_document(request_id, sha256);
