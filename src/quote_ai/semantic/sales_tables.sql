-- The sales facts the metric views read (AI-10): one row per quote, one row per order line. Demo data, written by
-- src/quote_ai/jobs/semantic_job.py seed from quote_ai.mock.sales (the quote history of AI-03, plus expired,
-- cancelled and open quotes, and an order for every won quote). Row level: the metric views aggregate at query time.
-- {sales} is catalog.schema, filled in from the bundle's schema names (quote_ai.storage.sql_files).

CREATE TABLE IF NOT EXISTS {sales}.quote_fact (
  quote_id        STRING NOT NULL COMMENT 'The quote',
  account_id      STRING COMMENT 'Customer account ID (customer.account_id)',
  account_name    STRING COMMENT 'Customer account name, e.g. Lakeside Regional Clinic (copied from customer)',
  region          STRING COMMENT 'Sales region of the account: Midwest, Northeast, West or South',
  rep_id          STRING COMMENT 'The rep who owns the quote (rep.rep_id)',
  sku_id          STRING COMMENT 'The quoted DBxLab SKU (dbxlab_sku.sku_id)',
  product_family  STRING COMMENT 'The DBxLab product family, e.g. GLOVE-A (dbxlab_product.product_id)',
  material        STRING COMMENT 'Glove material: nitrile, latex, vinyl or polyisoprene',
  size            STRING COMMENT 'Glove size',
  quantity        INT    COMMENT 'Selling units quoted (boxes)',
  unit            STRING COMMENT 'The selling unit: box',
  list_price      DECIMAL(10,2) COMMENT 'Price book list price per unit',
  unit_price      DECIMAL(10,2) COMMENT 'Quoted price per unit',
  unit_cost       DECIMAL(10,2) COMMENT 'DBxLab cost per unit. Confidential: masked for reps (AI-24)',
  discount_pct    DOUBLE COMMENT 'Discount against list price for this quote (1 - unit_price / list_price)',
  approval_needed BOOLEAN COMMENT 'The discount was above the rep limit, so a sales manager had to approve',
  state           STRING COMMENT 'Quote state: DRAFT, PRICED, PENDING_APPROVAL, ISSUED, EXPIRED, CANCELLED, ...',
  outcome         STRING COMMENT 'WON, LOST or EXPIRED once decided; null while open or when cancelled',
  requested_at    TIMESTAMP COMMENT 'When the request was received',
  issued_at       TIMESTAMP COMMENT 'When the quote was issued; null if never issued',
  decided_at      TIMESTAMP COMMENT 'When the outcome was known',
  demo_data       BOOLEAN COMMENT 'Always true: synthetic'
) COMMENT 'Quotes, one row per quote (demo data): the source of the metric view quote_metrics. Use the metric view for win rate, discount, turnaround and approvals; never re-implement them.';

CREATE TABLE IF NOT EXISTS {sales}.order_line (
  order_id    STRING NOT NULL COMMENT 'Sales order',
  line_no     INT    COMMENT 'Line number',
  quote_id    STRING COMMENT 'The won quote the order came from (quote_fact.quote_id)',
  account_id  STRING COMMENT 'Customer account ID (customer.account_id)',
  rep_id      STRING COMMENT 'The rep who owns the account',
  sku_id      STRING COMMENT 'The DBxLab SKU ordered (dbxlab_sku.sku_id)',
  order_date  DATE   COMMENT 'When the customer ordered. "Bought" and "sales" mean orders, not quotes',
  quantity    INT    COMMENT 'Selling units ordered (boxes)',
  unit        STRING COMMENT 'The selling unit: box',
  unit_price  DECIMAL(10,2) COMMENT 'Price paid per unit',
  list_price  DECIMAL(10,2) COMMENT 'List price per unit at the time',
  unit_cost   DECIMAL(10,2) COMMENT 'DBxLab cost per unit. Confidential: masked for reps (AI-24)',
  demo_data   BOOLEAN COMMENT 'Always true: synthetic'
) COMMENT 'What customers bought (demo data): one row per order line. Sales, purchases and "bought" mean this table, not quotes. The source of the metric view order_metrics.';

ALTER TABLE {sales}.quote_fact SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {sales}.order_line SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
