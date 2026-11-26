-- Comments for every table the DBxLab sales analyst reads (AI-11): the context Genie uses to write SQL. A wrong answer
-- is fixed here or in the ontology (sales_analyst.yml), not by rewording the question. Tables created with column
-- comments (quote_fact, order_line, dbxlab_product, dbxlab_sku) and the metric views (comments in their YAML) are
-- complete already; this file covers the views and the GUDID tables. CI checks that every column the agent uses has
-- a comment (tests/quote_ai/test_genie.py). {sales}, {gudid_gold}, {gudid_silver} come from the bundle.
-- Verify before recording: COMMENT ON COLUMN on a view and on a pipeline table (a pipeline refresh may reset the
-- latter; then set the comment in quote_ai_gudid_silver.py instead).

COMMENT ON TABLE {sales}.customer IS 'DBxLab customer accounts (demo data): one row per account. An account is named in name, e.g. Lakeside Regional Clinic (ACC-001).';
COMMENT ON COLUMN {sales}.customer.account_id IS 'Account ID, e.g. ACC-001; joins quote_fact.account_id and order_line.account_id';
COMMENT ON COLUMN {sales}.customer.name IS 'Account name, e.g. Lakeside Regional Clinic. "Account", "customer" and "clinic" mean this';
COMMENT ON COLUMN {sales}.customer.tier IS 'Price tier: standard or contract';
COMMENT ON COLUMN {sales}.customer.region IS 'Sales region: Midwest, Northeast, West or South';

COMMENT ON TABLE {gudid_gold}.glove_devices IS 'GUDID glove devices (public FDA data) as suppliers registered them: source truth, not DBxLab''s catalog and not eligibility. What DBxLab sells is dbxlab_sku.';
COMMENT ON COLUMN {gudid_gold}.glove_devices.primary_di IS 'GUDID primary device identifier; joins device_size.primary_di';
COMMENT ON COLUMN {gudid_gold}.glove_devices.brand_name IS 'Brand name as registered';
COMMENT ON COLUMN {gudid_gold}.glove_devices.company_name IS 'The company that registered the device (the labeler)';
COMMENT ON COLUMN {gudid_gold}.glove_devices.catalog_number IS 'The labeler''s catalog number';
COMMENT ON COLUMN {gudid_gold}.glove_devices.device_description IS 'Free text written by the labeler: a claim, not a verified fact';
COMMENT ON COLUMN {gudid_gold}.glove_devices.labeled_no_nrl IS 'Text true or false: labelled as not made with natural rubber latex (latex-free)';
COMMENT ON COLUMN {gudid_gold}.glove_devices.labeled_contains_nrl IS 'Text true or false: labelled as containing natural rubber latex';
COMMENT ON COLUMN {gudid_gold}.glove_devices.device_sterile IS 'Text true or false: sold sterile';
COMMENT ON COLUMN {gudid_gold}.glove_devices.in_commercial_distribution IS 'Whether the device is still in commercial distribution';
COMMENT ON COLUMN {gudid_gold}.glove_devices.product_codes IS 'FDA product codes of the device (LZA, LZC, LYY: the glove codes QuoteAI includes)';

COMMENT ON COLUMN {gudid_silver}.device_size.primary_di IS 'GUDID primary device identifier; joins glove_devices.primary_di';
COMMENT ON COLUMN {gudid_silver}.device_size.size_type IS 'What the size measures, as registered';
COMMENT ON COLUMN {gudid_silver}.device_size.size_value IS 'The size value, e.g. 7.5 for a surgical glove size, as text';
COMMENT ON COLUMN {gudid_silver}.device_size.size_text IS 'The size as free text, e.g. Medium or 7.5';
COMMENT ON COLUMN {gudid_silver}.device_size.size_unit IS 'The unit of size_value, when there is one';
