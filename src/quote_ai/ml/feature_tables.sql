-- Win prediction, the inputs (AI-12). Run by src/quote_ai/jobs/ml_job.py setup; {sales} and {ml} are catalog.schema,
-- filled in from the bundle's schema names (quote_ai.storage.sql_files). Everything here is demo data: the quote
-- history is synthetic, with outcomes from a documented, planted signal (docs/quote_ai/ai-12-ml-features/README.md).

-- The synthetic history the features are built from (quote_ai.mock.generate_dbxlab, written by ml_job.py seed)
CREATE TABLE IF NOT EXISTS {sales}.quote_history (
  quote_id               STRING NOT NULL COMMENT 'The quote (quote_fact.quote_id)',
  account_id             STRING COMMENT 'Customer account ID',
  account_name           STRING COMMENT 'Customer account name, e.g. Lakeside Regional Clinic',
  rep_id                 STRING COMMENT 'The rep who owned the quote',
  sku_id                 STRING COMMENT 'The quoted DBxLab SKU',
  quantity               INT    COMMENT 'Boxes quoted',
  unit                   STRING COMMENT 'The selling unit: box',
  unit_price             DOUBLE COMMENT 'Our price per box, as sent',
  list_price             DOUBLE COMMENT 'Price book list price per box at sent_at',
  customer_current_price DOUBLE COMMENT 'What the customer paid per box at the time (observed when the quote was prepared)',
  lead_time_days         INT    COMMENT 'Days we could deliver in, at sent_at',
  stock_available        INT    COMMENT 'Boxes of the SKU available when the quote was prepared',
  in_stock               BOOLEAN COMMENT 'Stock covered the quantity when the quote was prepared',
  sent_at                TIMESTAMP COMMENT 'When the quote was sent: every feature is taken as of this moment',
  decided_at             TIMESTAMP COMMENT 'When the outcome was known. Known only after the outcome',
  outcome                STRING COMMENT 'WON, LOST or EXPIRED. The label: won = 1, lost or expired = 0 (ADR 0003)',
  final_negotiated_price DOUBLE COMMENT 'The price finally paid; only for won quotes. Known only after the outcome: never a feature (the leak of AI-12)',
  days_to_decision       INT    COMMENT 'Days from sent to decided. Known only after the outcome: never a feature',
  demo_data              BOOLEAN COMMENT 'Always true: synthetic'
) COMMENT 'Decided quotes with what was known when each was sent and what was only known afterwards (demo data, an educational simulation with a planted signal). Not a feature table: the features come from the ml schema.';

CREATE TABLE IF NOT EXISTS {sales}.customer_price_history (
  account_id    STRING NOT NULL COMMENT 'Customer account ID',
  observed_at   TIMESTAMP NOT NULL COMMENT 'When the price was observed',
  current_price DOUBLE COMMENT 'What the customer pays per box (its current supplier or contract)',
  source        STRING COMMENT 'quote preparation, account review, or drift demo (AI-14)',
  demo_data     BOOLEAN COMMENT 'Always true: synthetic'
) COMMENT 'What each customer pays per box, over time (demo data). The source of account_features.customer_current_price.';

CREATE TABLE IF NOT EXISTS {sales}.inventory_history (
  sku_id         STRING NOT NULL COMMENT 'DBxLab SKU',
  as_of          TIMESTAMP NOT NULL COMMENT 'When the snapshot was taken',
  available      INT    COMMENT 'Boxes available',
  lead_time_days INT    COMMENT 'Days to deliver',
  demo_data      BOOLEAN COMMENT 'Always true: synthetic'
) COMMENT 'Inventory snapshots over time (demo data): the past that AI-02''s daily inventory file does not keep. The source of sku_features, with the ingested inventory.';

-- The label: same definition as the win-rate metric (ADR 0003)
CREATE TABLE IF NOT EXISTS {ml}.quote_labels (
  quote_id   STRING NOT NULL COMMENT 'The quote',
  account_id STRING COMMENT 'Lookup key of account_features',
  sku_id     STRING COMMENT 'Lookup key of sku_features',
  sent_at    TIMESTAMP COMMENT 'Timestamp lookup key: features are looked up as of this moment',
  won        INT    COMMENT 'The label: 1 won; 0 lost or expired. Cancelled and open quotes are left out (ADR 0003)'
) COMMENT 'Win labels (demo data): one row per decided quote from quote_fact, the same definition as the win-rate metric.';

-- Feature tables: a primary key with a timestamp key, so lookups are point in time
CREATE TABLE IF NOT EXISTS {ml}.account_features (
  account_id             STRING NOT NULL COMMENT 'Customer account ID',
  as_of                  TIMESTAMP NOT NULL COMMENT 'The values below are what was known at this moment, from earlier events only',
  orders_12m             INT    COMMENT 'Order lines in the 12 months before as_of',
  win_rate_12m           DOUBLE COMMENT 'Won over decided quotes in the 12 months before as_of (expired counts as lost); null when none',
  days_since_last_order  INT    COMMENT 'Days from the last known order to as_of; null when none',
  customer_current_price DOUBLE COMMENT 'What the customer paid per box at as_of (latest observation)',
  CONSTRAINT account_features_pk PRIMARY KEY (account_id, as_of TIMESERIES)
) COMMENT 'Account history as of each day and each current-price observation (demo data). Looked up as of sent_at; a plain join on account_id would leak today''s values.';

CREATE TABLE IF NOT EXISTS {ml}.sku_features (
  sku_id           STRING NOT NULL COMMENT 'DBxLab SKU',
  as_of            TIMESTAMP NOT NULL COMMENT 'The values below are what was known at this moment',
  lead_time_days   INT    COMMENT 'Days to deliver, from the latest inventory snapshot',
  stock_cover_days DOUBLE COMMENT 'Boxes available over average boxes sold per day',
  material         STRING COMMENT 'Glove material (the SKU model)',
  use_type         STRING COMMENT 'Intended use: exam, surgical, ... (the SKU model)',
  chemo_verified   BOOLEAN COMMENT 'A verified chemo test report existed at as_of; AI-extracted claims never count',
  CONSTRAINT sku_features_pk PRIMARY KEY (sku_id, as_of TIMESERIES)
) COMMENT 'SKU stock, lead time and verified attributes over time (demo data). Looked up as of sent_at.';

CREATE TABLE IF NOT EXISTS {ml}.quote_context_features (
  quote_id          STRING NOT NULL COMMENT 'The quote',
  sent_at           TIMESTAMP COMMENT 'When the quote was sent',
  unit_price        DOUBLE COMMENT 'Our price per box as sent (an input, not a model feature)',
  list_price        DOUBLE COMMENT 'List price per box at sent_at (an input, not a model feature)',
  quantity_boxes    INT    COMMENT 'Boxes quoted',
  price_vs_list_pct DOUBLE COMMENT 'Our price against list, in percent (-7.0 = 7% below)',
  needs_approval    BOOLEAN COMMENT 'The discount was above the rep limit (policy.yml)',
  CONSTRAINT quote_context_features_pk PRIMARY KEY (quote_id)
) COMMENT 'Each sent quote''s own values (demo data), from quote_ai.ml.features.request_features: the same function scores a proposed price.';

-- The on-demand feature: computed from the proposed price and the looked-up current price, in training and serving
CREATE OR REPLACE FUNCTION {ml}.price_vs_customer_current_pct(unit_price DOUBLE, customer_current_price DOUBLE)
RETURNS DOUBLE
LANGUAGE PYTHON
COMMENT 'Our price against what the customer pays today, in percent (-5.0 = 5% below). Same expression as quote_ai.ml.features.pct_diff.'
AS $$
if unit_price is None or not customer_current_price:
    return None
return round((unit_price / customer_current_price - 1) * 100, 2)
$$;

-- The training set as built by Feature Engineering (point in time), with its split, for inspection and dataset tracking
CREATE TABLE IF NOT EXISTS {ml}.win_training_set (
  quote_id                      STRING NOT NULL COMMENT 'The quote',
  account_id                    STRING COMMENT 'Lookup key (not a model input)',
  sku_id                        STRING COMMENT 'Lookup key (not a model input)',
  sent_at                       TIMESTAMP COMMENT 'Timestamp lookup key (not a model input)',
  split                         STRING COMMENT 'train (to 2026-04-30), valid (2026-05 to 06), test (2026-07 to 09)',
  won                           INT    COMMENT 'The label',
  price_vs_list_pct             DOUBLE COMMENT 'Feature: our price against list, percent',
  price_vs_customer_current_pct DOUBLE COMMENT 'Feature: our price against what the customer paid at sent_at, percent',
  lead_time_days                INT    COMMENT 'Feature: days to deliver at sent_at',
  stock_cover_days              DOUBLE COMMENT 'Feature: days of stock at sent_at',
  orders_12m                    INT    COMMENT 'Feature: order lines in the 12 months before sent_at',
  win_rate_12m                  DOUBLE COMMENT 'Feature: win rate in the 12 months before sent_at',
  days_since_last_order         INT    COMMENT 'Feature: days since the last order before sent_at',
  material                      STRING COMMENT 'Feature: glove material',
  chemo_verified                BOOLEAN COMMENT 'Feature: verified chemo report at sent_at',
  use_type                      STRING COMMENT 'Feature: intended use',
  quantity_boxes                INT    COMMENT 'Feature: boxes quoted',
  needs_approval                BOOLEAN COMMENT 'Feature: discount above the rep limit'
) COMMENT 'The win model''s training set (demo data): labels with features as of sent_at, split by time. No final_negotiated_price, no days_to_decision.';

ALTER TABLE {sales}.quote_history SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {sales}.customer_price_history SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {sales}.inventory_history SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {ml}.quote_labels SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {ml}.account_features SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {ml}.sku_features SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {ml}.quote_context_features SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {ml}.win_training_set SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
