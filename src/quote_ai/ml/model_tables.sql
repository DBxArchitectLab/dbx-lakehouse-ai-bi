-- Win prediction, the model's records (AI-13, AI-14). Run by src/quote_ai/jobs/ml_job.py setup; {ml} is
-- catalog.schema from the bundle. The model itself is quote_ai_ml.win_model (Unity Catalog, registered by the
-- register job); the monitor's tables land in quote_ai_monitoring.

CREATE TABLE IF NOT EXISTS {ml}.model_selection (
  selected_at          TIMESTAMP COMMENT 'When the training job applied the selection rule',
  training_set_version BIGINT COMMENT 'Delta version of win_training_set every candidate was trained on',
  git_commit           STRING COMMENT 'The commit the training job ran',
  status               STRING COMMENT 'selected, or none: no candidate passed (the baseline stays)',
  run_id               STRING COMMENT 'The MLflow run of the winner; null when none',
  run_name             STRING COMMENT 'rule, logreg, automl or gbt',
  baseline_test_auc    DOUBLE COMMENT 'The bar: the rule baseline''s AUC on the test months',
  reason               STRING COMMENT 'Why, in words',
  candidates           STRING COMMENT 'Every candidate''s metrics and top drivers, as JSON'
) COMMENT 'The win model selection rule applied after each training run (docs/quote_ai/decisions/adr-win-model-selection.md). Synthetic held-out months: an educational simulation.';

CREATE TABLE IF NOT EXISTS {ml}.model_promotion (
  decided_at       TIMESTAMP COMMENT 'When the register job decided',
  model_version    STRING COMMENT 'The challenger: the newly registered version of win_model',
  run_id           STRING COMMENT 'The MLflow run it was registered from',
  challenger_auc   DOUBLE COMMENT 'Challenger AUC on the latest month of decided quotes',
  champion_version STRING COMMENT 'The champion it was compared with; null for the first version',
  champion_auc     DOUBLE COMMENT 'Champion AUC on the same month',
  promoted         BOOLEAN COMMENT 'True when @champion moved to the challenger',
  reason           STRING COMMENT 'Why, in words'
) COMMENT 'Every promotion decision of the win_model in this schema: the audit trail of the @champion alias (moved only by the register job).';

CREATE TABLE IF NOT EXISTS {ml}.open_quote_scores (
  scored_at                     TIMESTAMP COMMENT 'When the nightly job scored (the monitor''s timestamp)',
  quote_id                      STRING COMMENT 'The open quote',
  source                        STRING COMMENT 'app (quote, AI-07) or sales_history (quote_fact, AI-10)',
  account_id                    STRING COMMENT 'Customer account ID',
  sku_id                        STRING COMMENT 'The quoted SKU (the quote''s biggest line)',
  unit_price                    DOUBLE COMMENT 'The price on the quote',
  model_version                 STRING COMMENT 'The win_model version behind @champion when scored',
  prediction                    DOUBLE COMMENT 'Win chance, 0 to 1: a simulation on demo data. Informs the rep; never sets the price',
  won                           INT    COMMENT 'The outcome once decided (null while open): the monitor''s label',
  price_vs_list_pct             DOUBLE COMMENT 'Input: price against list, percent',
  needs_approval                BOOLEAN COMMENT 'Input: discount above the rep limit',
  price_vs_customer_current_pct DOUBLE COMMENT 'Input (looked up): price against what the customer pays today, percent',
  lead_time_days                INT    COMMENT 'Input (looked up): days to deliver',
  stock_cover_days              DOUBLE COMMENT 'Input (looked up): days of stock',
  win_rate_12m                  DOUBLE COMMENT 'Input (looked up): the account''s 12-month win rate'
) COMMENT 'Nightly win chance of every open quote, one row per quote per run, scored by win_model@champion (demo data, a simulation). Watched by data and model quality monitoring.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

ALTER TABLE {ml}.model_selection SET TAGS ('app' = 'quote_ai');
ALTER TABLE {ml}.model_promotion SET TAGS ('app' = 'quote_ai');
ALTER TABLE {ml}.open_quote_scores SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
