-- Grants for the people (AI-24): with user authorization the back end queries AS THE REP, so the sales groups need
-- SELECT on what the rep's screens and Genie read; the row filter and the mask (row_filters.sql, column_masks.sql)
-- decide which rows and columns. Applied by the quote_ai_security_setup job (src/quote_ai/jobs/security_job.py
-- grants), with the per-tool EXECUTE grants from config/quote_ai/tool_grants.yml and the app's service principal cut
-- down to what the app itself needs (jobs/product_job.py app_grants). {REPS} and {MANAGERS} are the group names, filled in by the job.
-- Verify before recording: the group names in the account, and SELECT on a metric view.

GRANT USE CATALOG ON CATALOG {catalog} TO `{REPS}`;
GRANT USE SCHEMA ON SCHEMA {sales} TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.price_book TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.customer TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.rep_account TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.quote_fact TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.order_line TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.quote_metrics TO `{REPS}`;
GRANT SELECT ON TABLE {sales}.order_metrics TO `{REPS}`;
GRANT USE SCHEMA ON SCHEMA {gudid_silver} TO `{REPS}`;
GRANT SELECT ON TABLE {gudid_silver}.device_size TO `{REPS}`;

GRANT USE CATALOG ON CATALOG {catalog} TO `{MANAGERS}`;
GRANT USE SCHEMA ON SCHEMA {sales} TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.price_book TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.customer TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.rep_account TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.quote_fact TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.order_line TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.quote_metrics TO `{MANAGERS}`;
GRANT SELECT ON TABLE {sales}.order_metrics TO `{MANAGERS}`;
GRANT USE SCHEMA ON SCHEMA {gudid_silver} TO `{MANAGERS}`;
GRANT SELECT ON TABLE {gudid_silver}.device_size TO `{MANAGERS}`;
