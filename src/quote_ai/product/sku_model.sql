-- DBxLab's product model (AI-03): what DBxLab sells, and how each SKU maps to a GUDID device at one package level.
-- Every table has a comment: it is the context Genie, AI Search and the agent read. DBxLab tables are demo data.
-- {product}, {sales} and {sales_bronze} are catalog.schema, filled in by the job from the bundle's schema names
-- (quote_ai.storage.sql_files). Run by: src/quote_ai/jobs/product_job.py setup.
-- Four kinds of truth: GUDID silver is source truth; product_device_mapping rows that a person verified, and the price
-- book, are business truth. Only business truth decides eligibility, fulfilment and price.

CREATE TABLE IF NOT EXISTS {product}.dbxlab_product (
  product_id    STRING  NOT NULL COMMENT 'DBxLab product family, e.g. GLOVE-A',
  name          STRING  COMMENT 'Name shown to reps',
  material      STRING  COMMENT 'nitrile, latex, vinyl or polyisoprene, as DBxLab sells it',
  intended_use  STRING  COMMENT 'exam or surgical',
  powder_free   BOOLEAN COMMENT 'As DBxLab sells it; eligibility still needs verified evidence (AI-08)',
  latex_free    BOOLEAN COMMENT 'As DBxLab sells it (GUDID natural rubber latex labeling); eligibility needs verified evidence',
  demo_data     BOOLEAN COMMENT 'Always true: synthetic DBxLab data'
) COMMENT 'DBxLab Medical Supply product families (demo data). A product has one SKU per size and selling unit.';

CREATE TABLE IF NOT EXISTS {product}.dbxlab_sku (
  sku_id                  STRING  NOT NULL COMMENT 'What DBxLab sells and prices, e.g. GLOVE-A-M',
  product_id              STRING  NOT NULL COMMENT 'The product family (dbxlab_product)',
  catalog_number          STRING  COMMENT 'DBxLab catalog number, e.g. DBX-NE-M-100: nitrile exam, size M, box of 100',
  size                    STRING  COMMENT 'XS, S, M, L, XL or XXL',
  material                STRING  COMMENT 'nitrile, latex, vinyl or polyisoprene',
  intended_use            STRING  COMMENT 'exam or surgical',
  unit                    STRING  COMMENT 'The selling unit the customer orders: box or case',
  gloves_per_unit         INT     COMMENT 'Gloves in one selling unit; must equal the verified mapping''s count',
  sellable                BOOLEAN COMMENT 'Whether DBxLab currently sells the SKU',
  supplier_catalog_number STRING  COMMENT 'The supplier catalog number, matched against GUDID catalog_number',
  supplier_di             STRING  COMMENT 'The GUDID primary DI of the supplier device (demo binding, AI-03)',
  demo_data               BOOLEAN COMMENT 'Always true: synthetic DBxLab data'
) COMMENT 'DBxLab SKUs (demo data): one per product, size and selling unit. A SKU is offered only through a verified product_device_mapping.';

CREATE TABLE IF NOT EXISTS {product}.product_device_mapping (
  mapping_id          STRING NOT NULL COMMENT 'sku_id|package_di',
  sku_id              STRING COMMENT 'The DBxLab SKU; null when GUDID has a package DBxLab does not sell',
  product_id          STRING COMMENT 'The DBxLab product family',
  primary_di          STRING COMMENT 'GUDID base device identifier',
  package_di          STRING NOT NULL COMMENT 'GUDID device identifier at the package level of the selling unit',
  unit_level          STRING COMMENT 'each, box or case, from the GUDID package hierarchy',
  gloves_per_unit     INT    COMMENT 'Package quantities multiplied down to the base device; null if unresolved',
  mapping_method      STRING COMMENT 'auto:catalog_number+package_level, or reviewed:... once a person decided',
  mapping_confidence  STRING COMMENT 'high, medium or low, from the automatic match',
  verification_status STRING COMMENT 'unverified, verified, superseded or rejected. Only verified is offered in a quote',
  package_fingerprint STRING COMMENT 'Hash of the GUDID package levels when matched; a change supersedes a verified mapping',
  reason              STRING COMMENT 'Why the match is not high confidence, or why it was superseded or rejected',
  verified_by         STRING COMMENT 'The person who verified or rejected the mapping',
  verified_at         TIMESTAMP COMMENT 'When',
  updated_at          TIMESTAMP COMMENT 'Last written by the matching run or a reviewer'
) COMMENT 'SKU <-> GUDID device at its package level (business truth once verified). A person verifies each mapping; a GUDID package change sends it back to review.';

CREATE OR REPLACE VIEW {product}.mapping_review_queue
COMMENT 'Mappings waiting for a person: unverified and superseded, the most confident first. Nothing here is offered in a quote.'
AS SELECT * FROM {product}.product_device_mapping
WHERE verification_status IN ('unverified', 'superseded')
ORDER BY CASE mapping_confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, mapping_id;

CREATE OR REPLACE VIEW {product}.sku_offerable
COMMENT 'SKUs that can be offered in a quote: sellable, with a verified mapping whose glove count is the SKU''s.'
AS SELECT s.*, m.package_di, m.unit_level, m.verified_by, m.verified_at
FROM {product}.dbxlab_sku s
JOIN {product}.product_device_mapping m
  ON m.sku_id = s.sku_id AND m.verification_status = 'verified' AND m.gloves_per_unit = s.gloves_per_unit
WHERE s.sellable;

-- The commercial tables, typed from AI-02's bronze (the files the framework generated and landed).
-- AI-24: a dynamic view. cost is visible to DBxLab_Sales_Managers only (the same rule as the column mask mask_cost,
-- rules/sql/column_masks.sql: a mask can't be set on a view); everyone else gets floor_price, the lowest price the
-- margin allows, so pricing as the signed-in rep still checks the floor. Verify before recording:
-- is_account_group_member in a view evaluated for the querying user.
CREATE OR REPLACE VIEW {sales}.price_book
COMMENT 'DBxLab price book (demo data): effective-dated tiers and quantity breaks per SKU and selling unit, with the margin floor price; cost for sales managers only. From AI-02 bronze.'
AS SELECT price_book_id, sku_id, tier, CAST(min_units AS INT) AS min_units, unit,
          CAST(list_price AS DECIMAL(10,2)) AS list_price, CAST(unit_price AS DECIMAL(10,2)) AS unit_price,
          CASE WHEN is_account_group_member('DBxLab_Sales_Managers') THEN CAST(cost AS DECIMAL(10,2)) END AS cost,
          CAST(min_margin AS DECIMAL(5,4)) AS min_margin,
          CAST(ceil(round(CAST(cost AS DOUBLE) * (1 + CAST(min_margin AS DOUBLE)) * 100, 6)) / 100 AS DECIMAL(10,2)) AS floor_price,
          CAST(valid_from AS DATE) AS valid_from, _load_id, _release_date
FROM {sales_bronze}.price_book_raw;

CREATE OR REPLACE VIEW {sales}.inventory_snapshot
COMMENT 'DBxLab stock per SKU and snapshot (demo data): available, reserved, lead time and replenishment. From AI-02 bronze.'
AS SELECT snapshot_id, sku_id, CAST(available AS INT) AS available, CAST(reserved AS INT) AS reserved,
          CAST(lead_time_days AS INT) AS lead_time_days, CAST(replenishment_qty AS INT) AS replenishment_qty,
          CAST(NULLIF(replenishment_day, '') AS INT) AS replenishment_day, CAST(as_of AS DATE) AS as_of,
          _load_id, _release_date
FROM {sales_bronze}.inventory_raw;

CREATE OR REPLACE VIEW {sales}.customer
COMMENT 'DBxLab customer accounts (demo data): name, price tier and region. From AI-02 bronze.'
AS SELECT account_id, name, tier, region, _load_id FROM {sales_bronze}.customer_raw;

CREATE TABLE IF NOT EXISTS {sales}.rep (
  rep_id STRING NOT NULL COMMENT 'Sign-in name of a rep or manager',
  name   STRING COMMENT 'Display name',
  role   STRING COMMENT 'sales_rep or sales_manager (groups DBxLab_Sales_Reps, DBxLab_Sales_Managers)',
  region STRING COMMENT 'Sales region'
) COMMENT 'DBxLab sales team and account owners (demo data).';

-- {sales}.quote_history moved to src/quote_ai/ml/feature_tables.sql in AI-12: the win model's history, with sent_at,
-- the outcome and the columns known only afterwards (written by src/quote_ai/jobs/ml_job.py seed).

ALTER TABLE {product}.dbxlab_product SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {product}.dbxlab_sku SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {product}.product_device_mapping SET TAGS ('app' = 'quote_ai', 'truth' = 'business_truth_when_verified');
ALTER TABLE {sales}.rep SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
