-- The model-call cache (AI-23, experiment E4): one row per key, written by quote_ai.llm.cache.SqlCacheStore.
-- The key covers the call kind, the prompt version, the model service and the GUDID publicVersionNumber, so a changed
-- record, prompt or model is a miss. Never a row with a price, stock or account in it (cache.cacheable refuses them).
-- {ai} is catalog.schema of quote_ai_ai.
CREATE TABLE IF NOT EXISTS {ai}.llm_cache (
  cache_key   STRING NOT NULL COMMENT 'sha256 of kind|version|payload (quote_ai.llm.cache.cache_key)',
  kind        STRING COMMENT 'The call: glove_attributes, embedding, extract_requirements, ...',
  version     STRING COMMENT 'prompt version | model service | gudid:<publicVersionNumber>',
  value       STRING COMMENT 'The answer, as JSON',
  created_at  TIMESTAMP COMMENT 'When it was first asked'
) COMMENT 'Answers of model calls on unchanged inputs (AI-23). No price, stock or account data.';

ALTER TABLE {ai}.llm_cache SET TAGS ('app' = 'quote_ai');
