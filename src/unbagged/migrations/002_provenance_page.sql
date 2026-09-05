-- Add the page component of provenance.
--
-- docs/handoff.md §4 rule 1 requires every emitted record to carry source_document_id,
-- page and locator, but the §5 table definitions carry only the first and last.
-- Without page, "where did this come from" can name the document and the JSON
-- path but cannot tell the user which page of a 48-page PDF to open — which is
-- the question someone holding a printout actually has.

ALTER TABLE identity ADD COLUMN page INTEGER;
ALTER TABLE txn ADD COLUMN page INTEGER;
ALTER TABLE inference ADD COLUMN page INTEGER;
ALTER TABLE disclosure ADD COLUMN page INTEGER;
