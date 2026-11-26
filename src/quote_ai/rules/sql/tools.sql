-- The agent's seven tools as Unity Catalog functions in quote_ai_ai (AI-18). {ai}, {product}, {sales}, {gudid_gold}
-- are catalog.schema from the bundle; __QUOTE_AI_WHEEL__ is the quote_ai wheel in a volume (as in
-- check_eligibility.sql); __WIN_ENDPOINT__ the win model's serving endpoint (AI-14); __REP_LIMIT__ the rep's discount
-- limit from config/quote_ai/policy.yml. Each COMMENT is the description the model reads: it says what the tool is
-- for and what it is not for.
--
--   search_candidates  this file          check_eligibility  check_eligibility.sql (AI-15)
--   get_evidence       this file          check_fulfilment   check_fulfilment.sql (AI-15)
--   get_device         this file          price_quote        this file (price_rules: quote_ai.rules.uc.price_json)
--   win_probability    this file
--
-- One implementation, many callers: the Python in quote_ai.rules runs in SQL, in the app and in pytest. EXECUTE is
-- granted to the agent's identity and the sales group only (src/quote_ai/jobs/agent_job.py grants); no function
-- writes a table.
-- Verify before recording: vector_search() as a table function (index, query_text, num_results), SQL table functions
-- as agent tools through the managed MCP server, and ai_query on a classic serving endpoint with a struct request.

CREATE OR REPLACE FUNCTION {ai}.search_candidates(need STRING)
RETURNS TABLE (sku_id STRING, product_id STRING, catalog_number STRING, size STRING, search_text STRING)
COMMENT 'Candidates for a request; not a decision. Finds DBxLab glove products whose catalog text matches a need (AI Search, hybrid). Every candidate must still pass check_eligibility before it is offered. Not for documents or explanations.'
RETURN SELECT sku_id, product_id, catalog_number, size, search_text
       FROM vector_search(index => '{ai}.sku_search_index', query_text => need, num_results => 20);

CREATE OR REPLACE FUNCTION {ai}.get_evidence(product_id STRING, attribute STRING)
RETURNS TABLE (attribute_name STRING, attribute_value STRING, unit STRING, source_type STRING, source_id STRING,
               document_id STRING, verification_status STRING)
COMMENT 'The evidence rows behind one product attribute (chemo_tested, latex_free, powder_free, material): value, source document and verification status. Only verified rows are facts; the rest are claims. attribute NULL: every attribute.'
RETURN SELECT e.attribute_name, e.attribute_value, e.unit, e.source_type, e.source_id, e.document_id,
              e.verification_status
       FROM {product}.glove_evidence e
       WHERE e.product_id = get_evidence.product_id
         AND (get_evidence.attribute IS NULL OR e.attribute_name = get_evidence.attribute);

CREATE OR REPLACE FUNCTION {ai}.get_device(product_id STRING)
RETURNS TABLE (primary_di STRING, brand_name STRING, device_description STRING, labeled_no_nrl STRING,
               labeled_contains_nrl STRING, device_sterile STRING, device_record_status STRING, release_date STRING)
COMMENT 'The GUDID device record of a product supplier device (brand, description, latex labelling, sterility, record status) from the lakehouse copy, with its release date. For the freshest record of one device use gudid_live_lookup.'
RETURN SELECT g.primary_di, g.brand_name, g.device_description, CAST(g.labeled_no_nrl AS STRING),
              CAST(g.labeled_contains_nrl AS STRING), CAST(g.device_sterile AS STRING), g.device_record_status,
              CAST(g._release_date AS STRING)
       FROM {product}.dbxlab_sku s JOIN {gudid_gold}.glove_devices g ON g.primary_di = s.supplier_di
       WHERE s.product_id = get_device.product_id
       LIMIT 1;

CREATE OR REPLACE FUNCTION {ai}.price_rules(book_json STRING, packaging_json STRING, lines_json STRING,
                                            account_tier STRING)
RETURNS STRING
LANGUAGE PYTHON
ENVIRONMENT (dependencies = '["__QUOTE_AI_WHEEL__", "pydantic>=2.7", "pyyaml>=6"]', environment_version = 'None')
COMMENT 'Deterministic pricing (quote_ai.rules.pricing.price_quote) of quote lines from price book rows and packaging as JSON: unit conversion, volume tier, margin floor. Returns JSON lines with price_book_id, or a refusal.'
AS $$
from quote_ai.rules.uc import price_json
return price_json(book_json, packaging_json, lines_json, account_tier)
$$;

CREATE OR REPLACE FUNCTION {ai}.price_quote(product_id STRING, lines_json STRING, account_tier STRING)
RETURNS STRING
COMMENT 'Prices every quote line (JSON: size, quantity, unit) for one product from the current price book: unit conversion, account tier, volume tier, margin floor. The only source of a price. Returns JSON lines, each with its price_book_id.'
RETURN {ai}.price_rules(
  (SELECT to_json(collect_list(named_struct('id', b.price_book_id, 'sku_id', b.sku_id, 'tier', b.tier,
                                            'min_units', b.min_units, 'list_price', CAST(b.list_price AS DOUBLE),
                                            'unit_price', CAST(b.unit_price AS DOUBLE), 'cost', CAST(b.cost AS DOUBLE),
                                            'min_margin', CAST(b.min_margin AS DOUBLE),
                                            'floor_price', CAST(b.floor_price AS DOUBLE))))
   FROM {sales}.price_book b JOIN {product}.catalog_skus c ON c.sku_id = b.sku_id
   WHERE c.product_id = price_quote.product_id
     AND b._release_date = (SELECT max(_release_date) FROM {sales}.price_book)),
  (SELECT to_json(collect_list(named_struct('sku_id', c.sku_id, 'size', c.size, 'unit', c.unit,
                                            'gloves_per_unit', c.gloves_per_unit)))
   FROM {product}.catalog_skus c WHERE c.product_id = price_quote.product_id),
  lines_json,
  account_tier
);

CREATE OR REPLACE FUNCTION {ai}.win_probability(account_id STRING, sku_id STRING, unit_price DOUBLE,
                                                list_price DOUBLE, quantity_boxes INT)
RETURNS DOUBLE
COMMENT 'The win model (AI-14, demo data): the chance the customer accepts a quote of this SKU and quantity at this price. Informs the rep; never sets the price. Call once per price.'
RETURN element_at(ai_query(
  '__WIN_ENDPOINT__',
  named_struct('account_id', account_id, 'sku_id', sku_id, 'unit_price', unit_price, 'list_price', list_price,
               'quantity_boxes', quantity_boxes,
               'price_vs_list_pct', round(100 * (unit_price / list_price - 1), 2),
               'needs_approval', (1 - unit_price / list_price) > __REP_LIMIT__),
  returnType => 'ARRAY<DOUBLE>'), 2);
