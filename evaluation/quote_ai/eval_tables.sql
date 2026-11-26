-- The evaluation cases as Delta tables in the eval schema (AI-17), loaded from the versioned files in
-- evaluation/quote_ai/v1 by src/quote_ai/jobs/eval_job.py (the quote_ai_eval_tables job). {eval} is catalog.schema.
-- The files are the source of truth (reviewed in pull requests); the tables make them queryable next to the runs.

CREATE TABLE IF NOT EXISTS {eval}.search_cases (
  case_id        STRING NOT NULL COMMENT 'S-001 ...',
  suite_version  STRING COMMENT 'v1: the folder in evaluation/quote_ai',
  need           STRING COMMENT 'The request as a rep would type it',
  account_id     STRING COMMENT 'The customer (the permission tests use it)',
  case_json      STRING COMMENT 'The whole case: sizes, requirements, expected_skus, must_not_include',
  loaded_at      TIMESTAMP COMMENT 'When this version was loaded'
) COMMENT 'Search suite (AI-17): 50 requests with the products that should be found, written from the request, not from what search returns. Demo data.';

CREATE TABLE IF NOT EXISTS {eval}.eligibility_cases (
  case_id            STRING NOT NULL COMMENT 'E-001 ...',
  suite_version      STRING COMMENT 'v1',
  glove              STRING COMMENT 'The glove letter (GLOVE-<letter>)',
  evidence_snapshot  STRING COMMENT 'The evidence version the case runs against (product_evidence@v41, @v57)',
  status             STRING COMMENT 'Expected: eligible, needs_evidence or excluded',
  case_json          STRING COMMENT 'The whole case: requirements and an expectation per contract requirement',
  loaded_at          TIMESTAMP COMMENT 'When this version was loaded'
) COMMENT 'Eligibility suite (AI-15, AI-17): 60 cases, every contract requirement covered, each pinned to an evidence snapshot. Zero tolerance.';

CREATE TABLE IF NOT EXISTS {eval}.comparison_cases (
  case_id        STRING NOT NULL COMMENT 'C-001 ...',
  suite_version  STRING COMMENT 'v1',
  question       STRING COMMENT 'What the rep asks',
  rep            STRING COMMENT 'Who asks (their accounts filter retrieval)',
  case_json      STRING COMMENT 'The whole case: scope, expected sources, whether the answer must be no evidence on file',
  loaded_at      TIMESTAMP COMMENT 'When this version was loaded'
) COMMENT 'Comparison suite (AI-16, AI-17): 30 questions with the sources an answer should cite, including permission cases.';

ALTER TABLE {eval}.search_cases SET TAGS ('app' = 'quote_ai', 'demo_data' = 'true');
ALTER TABLE {eval}.eligibility_cases SET TAGS ('app' = 'quote_ai', 'demo_data' = 'true');
ALTER TABLE {eval}.comparison_cases SET TAGS ('app' = 'quote_ai', 'demo_data' = 'true');

-- AI-22: the gate's history and the reps' feedback.
CREATE TABLE IF NOT EXISTS {eval}.eval_results (
  run_id           STRING NOT NULL COMMENT 'One run of evaluation/quote_ai/run_eval.py --suites ...',
  suite            STRING COMMENT 'The suite (v1/thresholds.yml)',
  metric           STRING COMMENT 'The metric the release rule names',
  value            DOUBLE COMMENT 'Its value in this run; null when not run',
  threshold        STRING COMMENT 'The threshold it was checked against ({max: 0}, {min: 0.95}, ...)',
  status           STRING COMMENT 'pass, fail or not_run (an input this run did not have: the judge, review labels)',
  required         BOOLEAN COMMENT 'Whether the suite is required for a release',
  git_sha          STRING COMMENT 'The commit evaluated',
  package_version  STRING COMMENT 'evaluation/quote_ai/v1/VERSION',
  model_calls      INT COMMENT 'Judge and model calls this suite made (cost tracking)',
  cost_usd         DOUBLE COMMENT 'Their cost, where the gateway usage gives it',
  run_at           TIMESTAMP COMMENT 'When'
) COMMENT 'Every evaluation run, one row per suite and metric (AI-22): the history behind the release gate.';

CREATE TABLE IF NOT EXISTS {eval}.candidate_cases (
  candidate_id   STRING NOT NULL COMMENT 'From the feedback route',
  trace_id       STRING COMMENT 'The MLflow trace of the answer the rep rated (prompt, tool calls, output)',
  rep            STRING COMMENT 'Who gave the thumbs down (the signed-in rep)',
  quote_id       STRING COMMENT 'The quote, if the answer belonged to one',
  surface        STRING COMMENT 'Where the answer was: chat, compare, ask_data',
  comment        STRING COMMENT 'What the rep said was wrong',
  status         STRING COMMENT 'candidate, then accepted (an expected result written, added to the package in a pull request) or rejected',
  created_at     TIMESTAMP COMMENT 'When'
) COMMENT 'Thumbs-down answers from the QuoteAI app (AI-22): candidates, not tests. A person writes the expected result; only then does a case join evaluation/quote_ai/v1, in a pull request.';

ALTER TABLE {eval}.eval_results SET TAGS ('app' = 'quote_ai');
ALTER TABLE {eval}.candidate_cases SET TAGS ('app' = 'quote_ai');
