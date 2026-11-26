-- The eligibility engine as Unity Catalog functions (AI-15). {ai} and {product} are catalog.schema from the bundle;
-- __QUOTE_AI_WHEEL__ is the quote_ai wheel in a volume, put there by src/quote_ai/jobs/search_job.py functions.
-- One implementation (quote_ai.rules.eligibility, through quote_ai.rules.uc), many callers: SQL, notebooks, the
-- app's back end (through its warehouse) and, from AI-18, the agent's check_eligibility tool.
-- Verify before recording: LANGUAGE PYTHON with ENVIRONMENT (dependencies, environment_version) for a wheel in a
-- volume; SQL functions whose body reads tables through scalar subqueries; to_json(collect_list(struct(*))).

CREATE OR REPLACE FUNCTION {ai}.eligibility_rules(requirements_json STRING, skus_json STRING, evidence_json STRING,
                                                  evidence_snapshot STRING)
RETURNS STRING
LANGUAGE PYTHON
ENVIRONMENT (dependencies = '["__QUOTE_AI_WHEEL__", "pydantic>=2.7"]', environment_version = 'None')
COMMENT 'Deterministic three-state eligibility (verified_match, verified_mismatch, unknown) for one product, from its SKUs and evidence as JSON. Returns JSON. Unknown is never a match.'
AS $$
from quote_ai.rules.uc import eligibility_json
return eligibility_json(requirements_json, skus_json, evidence_json, evidence_snapshot)
$$;

CREATE OR REPLACE FUNCTION {ai}.check_eligibility(sku_id STRING, requirements_json STRING)
RETURNS STRING
COMMENT 'Eligibility of the product of one SKU against the confirmed requirements (JSON). Reads catalog_skus and eligibility_evidence (verified rows decide) and returns JSON: status and one check per stated requirement.'
RETURN {ai}.eligibility_rules(
  requirements_json,
  (SELECT to_json(collect_list(struct(c.*))) FROM {product}.catalog_skus c
   WHERE c.product_id = (SELECT max(x.product_id) FROM {product}.catalog_skus x WHERE x.sku_id = check_eligibility.sku_id)),
  (SELECT to_json(collect_list(struct(e.*))) FROM {product}.eligibility_evidence e
   WHERE e.product_id = (SELECT max(x.product_id) FROM {product}.catalog_skus x WHERE x.sku_id = check_eligibility.sku_id)),
  'eligibility_evidence@current'
);
