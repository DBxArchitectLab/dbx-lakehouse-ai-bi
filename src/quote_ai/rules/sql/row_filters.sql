-- Row filters (AI-24): who sees which account's rows. Applied by the quote_ai_security_setup job
-- (src/quote_ai/jobs/security_job.py filters); changed only through pull requests. The same rule in Python, for the
-- demo and CI: quote_ai.rules.access (owned_accounts, filter_rows), kept equal by tests/quote_ai/test_security.py.
-- These only work because the back end queries AS THE REP (user authorization, app/backend/deps.py): queried as the
-- app's service principal, current_user() is the app, not the rep.
-- {sales} is catalog.schema of quote_ai_sales. Verify before recording: is_account_group_member and current_user()
-- in a row filter function, a subquery on a mapping table inside it, and SET ROW FILTER on a table with a CDF.

-- A manager sees every account; anyone else only an account they own (rep_account: rep_id is the part of the email
-- before @). The price book has no account column (prices are per tier): its protection is the cost mask.
CREATE OR REPLACE FUNCTION {sales}.account_filter(account_id STRING)
RETURNS BOOLEAN
COMMENT 'Row filter (AI-24): DBxLab_Sales_Managers see every account; a rep only the accounts they own (rep_account).'
RETURN is_account_group_member('DBxLab_Sales_Managers')
    OR EXISTS (SELECT 1 FROM {sales}.rep_account r
               WHERE r.account_id = account_filter.account_id
                 AND r.rep_id = lower(split_part(current_user(), '@', 1)));

-- The ownership table itself: a rep sees their own rows (so "my accounts" is one SELECT as the rep).
CREATE OR REPLACE FUNCTION {sales}.rep_filter(rep_id STRING)
RETURNS BOOLEAN
COMMENT 'Row filter (AI-24): DBxLab_Sales_Managers see every rep''s accounts; a rep only their own.'
RETURN is_account_group_member('DBxLab_Sales_Managers') OR rep_id = lower(split_part(current_user(), '@', 1));

-- What Genie and the metric views read (quote_metrics and order_metrics are views over these: filtered too).
ALTER TABLE {sales}.quote_fact SET ROW FILTER {sales}.account_filter ON (account_id);
ALTER TABLE {sales}.order_line SET ROW FILTER {sales}.account_filter ON (account_id);
ALTER TABLE {sales}.rep_account SET ROW FILTER {sales}.rep_filter ON (rep_id);
