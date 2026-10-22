-- A follow-up says who it is for (#37).
--
-- `description` is written to the reader. `question` is the sentence to put to
-- the retailer, and is what the follow-up letter asks; NULL means the follow-up
-- is advice to the reader and never reaches a letter. Responses stored before
-- this column existed keep NULL, so their letters read exactly as they did
-- until the response is uploaded again.
ALTER TABLE follow_up ADD COLUMN question TEXT;
