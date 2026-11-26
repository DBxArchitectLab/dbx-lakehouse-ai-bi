-- Order metrics (AI-10): what customers bought, defined once, owned by Sales Ops. margin_pct reads unit_cost, which is
-- masked for reps in AI-24; the measure is kept apart from the revenue measures so the mask is easy.
--   SELECT account, MEASURE(revenue), MEASURE(avg_unit_price) FROM {sales}.order_metrics GROUP BY account
-- {sales} and {product} are catalog.schema from the bundle. Run by src/quote_ai/jobs/semantic_job.py metrics.
-- Verify before recording: a SQL query as the metric view source (else a view, order_line_enriched).

CREATE OR REPLACE VIEW {sales}.order_metrics
WITH METRICS
LANGUAGE YAML
COMMENT 'DBxLab order metrics (demo data), owned by Sales Ops: orders, units, revenue, price paid, margin. Sales and bought mean orders.'
AS $$
version: 1.1
source: SELECT o.*, c.name AS account_name, c.region, s.product_id AS product_family, s.material FROM {sales}.order_line o JOIN {sales}.customer c ON c.account_id = o.account_id JOIN {product}.dbxlab_sku s ON s.sku_id = o.sku_id
comment: One row per order line, with the account and the product it belongs to.
dimensions:
  - name: region
    expr: region
    comment: Sales region of the account
  - name: rep
    expr: rep_id
    comment: The rep who owns the account
  - name: account
    expr: account_name
    comment: Customer account name, e.g. Lakeside Regional Clinic
  - name: product_family
    expr: product_family
    comment: DBxLab product family, e.g. GLOVE-A
  - name: material
    expr: material
    comment: Glove material
  - name: sku
    expr: sku_id
    comment: The DBxLab SKU ordered
  - name: month
    expr: date_trunc('MONTH', order_date)
    comment: The month of the order
measures:
  - name: orders
    expr: COUNT(DISTINCT order_id)
    comment: Number of orders
  - name: units
    expr: SUM(quantity)
    comment: Selling units ordered (boxes)
  - name: revenue
    expr: SUM(unit_price * quantity)
    comment: Revenue in USD (price paid x units)
  - name: avg_unit_price
    expr: SUM(unit_price * quantity) / NULLIF(SUM(quantity), 0)
    comment: Average price paid per unit, weighted by units
  - name: margin_pct
    expr: SUM((unit_price - unit_cost) * quantity) / NULLIF(SUM(unit_price * quantity), 0)
    comment: (revenue - cost) / revenue. Reads cost, masked for reps in AI-24
$$
