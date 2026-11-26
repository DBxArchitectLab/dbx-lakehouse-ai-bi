-- Quote state in Delta (AI-07): quote, quote_line and quote_event in the sales schema. Lakebase takes over in AI-20
-- with the same columns; QuoteService doesn't change. Written only by QuoteService through
-- quote_ai.storage.delta_repository (the app's service principal has MODIFY; nobody writes to these by hand).
-- {sales} is catalog.schema, filled in from the bundle's schema names. Run by src/quote_ai/jobs/product_job.py setup.
-- JSON columns are STRING for now (verify VARIANT support on the app's warehouse before switching).

CREATE TABLE IF NOT EXISTS {sales}.quote (
  quote_id       STRING NOT NULL COMMENT 'The quote',
  version        INT    COMMENT 'Terms version: requirements, lines and price; an approval binds one version',
  revision       INT    COMMENT 'Row revision for optimistic locking; every write increments it',
  account        STRING COMMENT 'Customer account',
  rep            STRING COMMENT 'Owner: the rep who read the request',
  state          STRING COMMENT 'DRAFT, REQUIREMENTS_CONFIRMED, SHORTLISTED, PRICED, PENDING_APPROVAL, APPROVED, ISSUED, REJECTED, EXPIRED, CANCELLED (AI-Planning 4.2)',
  request_text   STRING COMMENT 'The request as the customer wrote it (customer data; demo data here)',
  requirements   STRING COMMENT 'Requirements of the current version as JSON (r1 extracted, r2 confirmed, r3 accepted alternative)',
  product_id     STRING COMMENT 'The product on the quote, once shortlisted',
  total          DECIMAL(12,2) COMMENT 'Sum of the lines, once priced',
  price_method   STRING COMMENT 'v1_list_price (AI-07, simple rules) or price_quote (AI-18)',
  doc            STRING COMMENT 'The whole quote as JSON (quote_ai.domain.models.Quote): what QuoteService reads back',
  created_at     TIMESTAMP COMMENT 'When the request was read',
  updated_at     TIMESTAMP COMMENT 'Last change'
) COMMENT 'Quotes (demo data): one row per quote, its state and current version. State per AI-Planning section 4.2; changed only by QuoteService.';

CREATE TABLE IF NOT EXISTS {sales}.quote_line (
  quote_id       STRING NOT NULL COMMENT 'The quote',
  version        INT    COMMENT 'The quote version these lines belong to',
  line_no        INT    COMMENT 'Line number, one per size',
  size           STRING COMMENT 'Glove size',
  quantity       INT    COMMENT 'Selling units on the line',
  unit           STRING COMMENT 'The selling unit (box, case)',
  sku_id         STRING COMMENT 'DBxLab SKU, once shortlisted',
  unit_price     DECIMAL(10,2) COMMENT 'Price per unit, once priced',
  list_price     DECIMAL(10,2) COMMENT 'The price book list price per unit',
  price_book_id  STRING COMMENT 'The price book row used'
) COMMENT 'Quote lines (demo data) of the current version of each quote: one per size, from the rep''s confirmation.';

CREATE TABLE IF NOT EXISTS {sales}.quote_event (
  quote_id        STRING NOT NULL COMMENT 'The quote',
  quote_version   INT    COMMENT 'Quote version after the move',
  revision        INT    COMMENT 'Row revision after the move',
  action          STRING COMMENT 'create, confirm, quick_quote_match, quick_quote_price, shortlist, price, submit, approve, reject, issue, ...',
  from_state      STRING COMMENT 'State before (null for create)',
  to_state        STRING COMMENT 'State after',
  actor           STRING COMMENT 'Who made the move: the signed-in user, never the model',
  at              TIMESTAMP COMMENT 'When',
  trace_id        STRING COMMENT 'The MLflow trace of the request that made the move',
  idempotency_key STRING COMMENT 'Submit, approve and issue: a retried call with the same key does not act twice',
  detail          STRING COMMENT 'What the move decided, as JSON'
) COMMENT 'Append-only history of quote state changes: one row per allowed move, none for a refused one.';

ALTER TABLE {sales}.quote SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {sales}.quote_line SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {sales}.quote_event SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
