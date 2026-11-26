-- Fulfilment as Unity Catalog functions (AI-15), next to check_eligibility. {ai}, {product}, {sales} from the bundle;
-- __QUOTE_AI_WHEEL__ as in check_eligibility.sql. Run only for eligible products; reads the latest inventory snapshot
-- (business truth). A late answer is an alternative that needs the customer's acceptance, never on time.

CREATE OR REPLACE FUNCTION {ai}.fulfilment_rules(skus_json STRING, lines_json STRING, stock_json STRING,
                                                 delivery_days INT, inventory_snapshot STRING)
RETURNS STRING
LANGUAGE PYTHON
ENVIRONMENT (dependencies = '["__QUOTE_AI_WHEEL__", "pydantic>=2.7"]', environment_version = 'None')
COMMENT 'Full, partial, backorder or alternative date per quote line, from the SKUs, the lines and their stock as JSON. Returns JSON with a summary and the label (on time, or alternative needs acceptance).'
AS $$
from quote_ai.rules.uc import fulfilment_json
return fulfilment_json(skus_json, lines_json, stock_json, delivery_days, inventory_snapshot)
$$;

CREATE OR REPLACE FUNCTION {ai}.check_fulfilment(sku_id STRING, lines_json STRING, delivery_days INT)
RETURNS STRING
COMMENT 'Fulfilment of the product of one SKU for the quote lines (JSON: line_no, size, quantity, unit) by a delivery day, from the latest inventory snapshot. Returns JSON.'
RETURN {ai}.fulfilment_rules(
  (SELECT to_json(collect_list(struct(c.*))) FROM {product}.catalog_skus c
   WHERE c.product_id = (SELECT max(x.product_id) FROM {product}.catalog_skus x WHERE x.sku_id = check_fulfilment.sku_id)),
  lines_json,
  (SELECT to_json(collect_list(struct(i.sku_id, i.available, i.reserved, i.lead_time_days, i.replenishment_qty,
                                      i.replenishment_day)))
   FROM {sales}.inventory_snapshot i
   WHERE i._release_date = (SELECT max(_release_date) FROM {sales}.inventory_snapshot)),
  delivery_days,
  (SELECT max(snapshot_id) FROM {sales}.inventory_snapshot
   WHERE _release_date = (SELECT max(_release_date) FROM {sales}.inventory_snapshot))
);
