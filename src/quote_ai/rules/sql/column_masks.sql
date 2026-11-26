-- Column masks (AI-24): DBxLab's cost, and so the margin, for sales managers only. Applied by the
-- quote_ai_security_setup job (src/quote_ai/jobs/security_job.py masks). The Python twin: quote_ai.rules.access
-- (can_see_cost, mask_row). order_metrics' margin_pct reads unit_cost, so it is NULL for a rep too (AI-10's measure,
-- unchanged). The price book is a view: it masks its cost column with the same rule inline and shows floor_price
-- instead (src/quote_ai/product/sku_model.sql), so a rep is priced with the margin floor but never sees cost.
-- Verify before recording: SET MASK with a DECIMAL(10,2) column and the function's parameter type.

CREATE OR REPLACE FUNCTION {sales}.mask_cost(v DECIMAL(10,2))
RETURNS DECIMAL(10,2)
COMMENT 'Column mask (AI-24): the cost for DBxLab_Sales_Managers, NULL for everyone else.'
RETURN CASE WHEN is_account_group_member('DBxLab_Sales_Managers') THEN v ELSE NULL END;

ALTER TABLE {sales}.quote_fact ALTER COLUMN unit_cost SET MASK {sales}.mask_cost;
ALTER TABLE {sales}.order_line ALTER COLUMN unit_cost SET MASK {sales}.mask_cost;
