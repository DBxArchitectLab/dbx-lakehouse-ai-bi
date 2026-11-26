-- The analytics copy of the live quotes (AI-20): Lakebase is the source of truth; these Delta tables in {sales} are a
-- read-only copy under the names AI-07 created, so the metric views (AI-10), Genie (AI-11) and the win model
-- (AI-12 to AI-14) don't change. Nobody writes them except this sync.
-- __LAKEBASE__ is the Lakebase database registered in Unity Catalog (the database catalog quote_ai_lakebase_<target>,
-- schema public), filled in by src/quote_ai/jobs/lakebase_job.py sync.
-- Verify before recording: whether synced tables can run in this direction (Lakebase to Delta) in the region; if they
-- can, they replace this job and keep the same names and columns. Until then a scheduled full refresh (the tables
-- are small) through the registered catalog.

CREATE OR REPLACE TABLE {sales}.quote
COMMENT 'QuoteAI quotes (AI-07), from AI-20 a read-only copy of Lakebase quote: one row per quote, its state, version, revision and the whole quote as JSON (doc).'
AS SELECT quote_id,
          version,
          revision,
          get_json_object(doc, '$.account') AS account,
          owner_rep AS rep,
          state,
          get_json_object(doc, '$.request_text') AS request_text,
          get_json_object(doc, '$.requirements') AS requirements,
          get_json_object(doc, '$.product_id') AS product_id,
          CAST(total AS DOUBLE) AS total,
          get_json_object(doc, '$.price_method') AS price_method,
          doc,
          created_at,
          updated_at
   FROM __LAKEBASE__.quote;

CREATE OR REPLACE TABLE {sales}.quote_line
COMMENT 'QuoteAI quote lines, from AI-20 a read-only copy of Lakebase quote_line (one per size, with the price book row).'
AS SELECT quote_id, version, line_no, size, quantity, unit, sku_id, CAST(unit_price AS DOUBLE) AS unit_price,
          CAST(list_price AS DOUBLE) AS list_price, price_book_id
   FROM __LAKEBASE__.quote_line;

CREATE OR REPLACE TABLE {sales}.quote_event
COMMENT 'QuoteAI audit trail, from AI-20 a read-only copy of Lakebase quote_event: every move with who, which version, the trace and the idempotency key.'
AS SELECT quote_id, quote_version, revision, action, from_state, to_state, actor, at, trace_id, idempotency_key, detail
   FROM __LAKEBASE__.quote_event;

CREATE OR REPLACE TABLE {sales}.quote_approval
COMMENT 'QuoteAI approvals (AI-21), a read-only copy of Lakebase approval: each decision bound to a quote version and its terms hash, for the audit report.'
AS SELECT quote_id, version, terms_hash, approver, decision, decided_at, idempotency_key
   FROM __LAKEBASE__.approval;
