-- The glove devices (AI-03): one definition of "a glove" that every later episode reads.
-- The category is AI-02's: verified FDA product codes (LZA, LZC, LYY) in quote_ai_ops.glove_classification, published
-- as the view quote_ai_ops.glove_devices (included rows only). No keyword filter. A category is not eligibility: a
-- latex glove (LYY) is a glove; latex-free needs its own evidence.
-- {gudid_silver}, {gudid_gold} and {ops} are catalog.schema, filled in from the bundle's schema names.
-- Run by src/quote_ai/jobs/product_job.py setup, after the quote_ai_gudid pipeline.

CREATE OR REPLACE VIEW {gudid_gold}.glove_devices
COMMENT 'GUDID glove devices (public data): the current, published version of every device AI-02 classified as a glove, with its category evidence. Source truth, not eligibility.'
AS SELECT d.*, g.category, g.product_codes, g.classification_version, g.classification_hash
FROM {gudid_silver}.device d
JOIN (SELECT primary_di, max(category) AS category, max(product_codes) AS product_codes,
             max(classification_version) AS classification_version, max(classification_hash) AS classification_hash
      FROM {ops}.glove_devices GROUP BY primary_di) g
  ON g.primary_di = d.primary_di
WHERE d.device_record_status = 'Published';

CREATE OR REPLACE VIEW {gudid_gold}.glove_packages
COMMENT 'Package levels of the glove devices (public data): each identifier, what it contains and how many. The input to gloves per unit.'
AS SELECT p.*
FROM {gudid_silver}.package p
WHERE p.primary_di IN (SELECT primary_di FROM {gudid_gold}.glove_devices);
