-- Freshness (AI-26): how old the data behind an answer is, per source and per input. Monitoring the pipeline's
-- success is not enough; this is the data's age. The Ops page, the operating dashboard and the SQL alert read this one
-- view, so they can never disagree. The targets are in config/quote_ai/slo.yml (24 h per source); the limits that
-- refuse ISSUED are config/quote_ai/policy.yml (QuoteService.issue, AI-20).
-- {ops}, {product}, {gudid_gold}, {sales}, {monitoring}: catalog.schema from the bundle (quote_ai.storage.sql_files).
-- Unit-tested on fixtures on SQLite (tests/quote_ai/test_operations.py, quote_ai.ops.monitoring.run_local).

CREATE OR REPLACE VIEW {monitoring}.freshness
COMMENT 'QuoteAI freshness (AI-26): hours since the last successful run of every ingested source (the AI-02 ingestion log), the supplier documents waiting to become evidence, and the age of each input QuoteService checks before ISSUED.'
AS
-- every source the AI-02 framework lands: hours since its last successful run
SELECT 'source' AS kind, source AS name, max(ended_at) AS as_of,
       timestampdiff(HOUR, max(ended_at), current_timestamp()) AS age_hours
FROM {ops}.ingestion_log
WHERE status IN ('succeeded', 'no_new_files')
GROUP BY source
UNION ALL
-- supplier documents: the oldest signed-off document that is not evidence yet (0 when none is waiting)
SELECT 'source' AS kind, 'supplier_documents' AS name, min(r.registered_at) AS as_of,
       coalesce(timestampdiff(HOUR, min(r.registered_at), current_timestamp()), 0) AS age_hours
FROM {product}.document_registry r
JOIN {product}.document_qa_signoff s ON s.document_id = r.document_id AND s.document_version = r.version
LEFT JOIN (SELECT DISTINCT document_id, document_version FROM {product}.supplier_quality_evidence) e
  ON e.document_id = r.document_id AND e.document_version = r.version
WHERE r.is_current AND e.document_id IS NULL
UNION ALL
-- the inputs behind a quote, as QuoteService sees them at issue (the app's sql_inputs_as_of)
SELECT 'input' AS kind, 'gudid' AS name, max(_release_date) AS as_of,
       timestampdiff(HOUR, max(_release_date), current_timestamp()) AS age_hours
FROM {gudid_gold}.glove_devices
UNION ALL
SELECT 'input' AS kind, 'inventory' AS name, max(_release_date) AS as_of,
       timestampdiff(HOUR, max(_release_date), current_timestamp()) AS age_hours
FROM {sales}.inventory_snapshot
UNION ALL
SELECT 'input' AS kind, 'price_book' AS name, max(_release_date) AS as_of,
       timestampdiff(HOUR, max(_release_date), current_timestamp()) AS age_hours
FROM {sales}.price_book;
