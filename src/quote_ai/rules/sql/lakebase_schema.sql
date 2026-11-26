-- dialect: postgres
-- QuoteAI's transactional store on Lakebase (AI-20, AI-21): database quote_ai on the instance quote-ai-db, registered
-- in Unity Catalog. Lakebase is the source of truth for live quotes and the agent's memory; the Delta tables in
-- quote_ai_sales (quote, quote_line, quote_event) become a read-only synced copy for the metric views, Genie and the
-- win model. Only QuoteService writes the quote tables (quote_ai.storage.lakebase_repository: the quote, its lines,
-- its version, its approval, its event and the idempotency key in ONE transaction); the agent only reads, through
-- the app. Plain names: the database carries the prefix.
-- The same statements run on SQLite in the unit tests (COMMENT ON is skipped there), so keep to the SQL both accept.
-- Applied by: python src/quote_ai/jobs/lakebase_job.py schema (quote_ai_lakebase job).

CREATE TABLE IF NOT EXISTS quote (
  quote_id     text PRIMARY KEY,
  account_id   text NOT NULL,
  owner_rep    text NOT NULL,
  state        text NOT NULL,
  version      integer NOT NULL DEFAULT 1,
  revision     integer NOT NULL DEFAULT 1,
  total        numeric(12, 2),
  doc          text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   timestamptz NOT NULL
);
COMMENT ON TABLE quote IS 'Live quotes (AI-20): one row per quote, its state, terms version and row revision (optimistic locking); doc is the whole quote as JSON. Written only by QuoteService.';
CREATE INDEX IF NOT EXISTS quote_owner_updated ON quote (owner_rep, updated_at);

CREATE TABLE IF NOT EXISTS quote_line (
  quote_id      text NOT NULL REFERENCES quote (quote_id),
  version       integer NOT NULL,
  line_no       integer NOT NULL,
  size          text NOT NULL,
  quantity      integer NOT NULL,
  unit          text NOT NULL,
  sku_id        text,
  unit_price    numeric(12, 2),
  list_price    numeric(12, 2),
  price_book_id text,
  PRIMARY KEY (quote_id, line_no)
);
COMMENT ON TABLE quote_line IS 'The current lines of each quote (one per size), with the price book row each price came from.';

CREATE TABLE IF NOT EXISTS quote_version (
  quote_id     text NOT NULL REFERENCES quote (quote_id),
  version      integer NOT NULL,
  state        text NOT NULL,
  terms_hash   text NOT NULL,
  doc          text NOT NULL,
  created_at   timestamptz NOT NULL,
  PRIMARY KEY (quote_id, version)
);
COMMENT ON TABLE quote_version IS 'Every terms version of a quote (requirements, lines, prices, evidence snapshot) and its terms hash: what an approval is bound to.';

CREATE TABLE IF NOT EXISTS quote_event (
  quote_id        text NOT NULL REFERENCES quote (quote_id),
  quote_version   integer NOT NULL,
  revision        integer NOT NULL,
  action          text NOT NULL,
  from_state      text,
  to_state        text NOT NULL,
  actor           text NOT NULL,
  at              timestamptz NOT NULL,
  trace_id        text,
  idempotency_key text,
  detail          text,
  PRIMARY KEY (quote_id, revision)
);
COMMENT ON TABLE quote_event IS 'The audit trail: one row per move, with who, which version, the MLflow trace and the idempotency key. Synced to Delta for reporting.';

CREATE TABLE IF NOT EXISTS idempotency_key (
  idempotency_key text PRIMARY KEY,
  quote_id        text NOT NULL,
  action          text NOT NULL,
  revision        integer NOT NULL,
  created_at      timestamptz NOT NULL
);
COMMENT ON TABLE idempotency_key IS 'AI-21: one row per click on submit, approve and issue; a repeated key returns the first result, never a second action.';

CREATE TABLE IF NOT EXISTS approval (
  quote_id        text NOT NULL REFERENCES quote (quote_id),
  version         integer NOT NULL,
  terms_hash      text NOT NULL,
  approver        text NOT NULL,
  decision        text NOT NULL,
  decided_at      timestamptz NOT NULL,
  idempotency_key text,
  UNIQUE (quote_id, version, terms_hash, decision)
);
COMMENT ON TABLE approval IS 'AI-21: a manager decision (or the policy within the rep limit) bound to one quote version and its terms hash: exactly one approval per version and hash.';

CREATE TABLE IF NOT EXISTS customer_memory (
  account_id   text NOT NULL,
  fact_key     text NOT NULL,
  fact_value   text NOT NULL,
  owner_rep    text NOT NULL,
  shared       boolean NOT NULL DEFAULT false,
  confirmed_by text NOT NULL,
  confirmed_at timestamptz NOT NULL,
  PRIMARY KEY (account_id, fact_key)
);
COMMENT ON TABLE customer_memory IS 'Long-term memory (AI-20): facts a rep confirmed about a customer (Lakeside is always latex-free). No row without confirmed_by; visible to its owner rep, or to all when shared. Kept until the rep or the account owner removes it.';

CREATE TABLE IF NOT EXISTS conversation (
  quote_id     text NOT NULL,
  owner_rep    text NOT NULL,
  seq          integer NOT NULL,
  role         text NOT NULL,
  content      text NOT NULL,
  created_at   timestamptz NOT NULL,
  PRIMARY KEY (quote_id, owner_rep, seq)
);
COMMENT ON TABLE conversation IS 'Short-term memory (AI-20): the chat on a quote, per rep; resumed when the quote is reopened. Kept 30 days.';
