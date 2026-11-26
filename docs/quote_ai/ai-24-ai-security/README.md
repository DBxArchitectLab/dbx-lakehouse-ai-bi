# AI-24: AI security and governance

Quality isn't safety. A threat model with a test for every threat (decision record 0006); the back end queries as the
signed-in rep, replacing the filter by rep kept in code since AI-07; row filters, a cost mask and per-tool grants in
Unity Catalog; guardrails on the gateway as a second layer; and two suites in the gate, data authorization and model
access, run separately.

| File | What |
| --- | --- |
| `src/quote_ai/app/backend/deps.py` | `identity`, `as_rep` (the rep's data as the rep), `user_ws` (the forwarded token), `app_ws` (audit and eval only) |
| `src/quote_ai/app/backend/auth.py` | `User.token`: X-Forwarded-Access-Token, never logged or returned |
| `src/quote_ai/storage/user_data.py` | `SqlUserData` (queries and Genie with the rep's token), `DemoUserData` (the same rules locally) |
| `src/quote_ai/rules/access.py` | The rules in Python: `Identity`, `owned_accounts`, `filter_rows`, `can_see_cost`, `mask_row` |
| `src/quote_ai/app/backend/routers/quotes.py` | `check_account`: 403 "no access to this account", decided as the rep; the code filter `q.rep != user.id` is deleted |
| `routers/price.py`, `approvals.py`, `ask_data.py` | The price book and Genie as the rep; margin for managers only |
| `src/quote_ai/rules/pricing.py` | `floor_price` (the margin floor with cost masked), `cost` optional, `margin_pct` None when masked |
| `src/quote_ai/rules/sql/row_filters.sql`, `column_masks.sql`, `grants.sql` | account_filter, rep_filter; mask_cost; the groups' SELECT |
| `src/quote_ai/product/sku_model.sql`, `rules/sql/tools.sql` | The price book view: cost for managers only, `floor_price`; price_quote passes it |
| `config/quote_ai/tool_grants.yml`, `src/quote_ai/agents/grants.py` | Per-tool EXECUTE per specialist; `findings` (sec-05); the knowledge specialist loses price_quote |
| `src/quote_ai/jobs/security_job.py`, `resources/quote_ai/quote_ai_security_setup.job.yml` | filters, masks, grants, audit |
| `src/quote_ai/jobs/product_job.py`, `quote_ai_app_grants.job.yml` | The app's identity cut down: SELECT (not MODIFY) on the Delta quote copy; REVOKE price book, sales facts, metric views; eval for feedback |
| `resources/quote_ai/quote_ai_app.app.yml` | `user_api_scopes: [sql, dashboards.genie]` |
| `resources/quote_ai/quote_ai_gateway_guardrails.notes.md` | Safety and PII on input and output, rate limits, per service |
| `evaluation/quote_ai/v1/data_authorization/` | sec-01 to sec-05, `lakeside_injection.eml`; `evaluation/quote_ai/security.py` runs each as a named identity through the API |
| `evaluation/quote_ai/v1/model_access/cases.jsonl` | + sec-06 (PII), sec-07 (long output) |
| `evaluation/quote_ai/v1/thresholds.yml`, `VERSION` | data_authorization required (runs: ci); package 1.2.0 |
| `docs/quote_ai/decisions/0006-threat-model.md` | The threat model, the decision, what stays on the app's identity |
| `tests/quote_ai/test_pricing_injection.py`, `test_security.py` | The injected email; the rules, the SQL, the grants, the token, the suite and its two breaks |

## Deploy and run

```
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_product_model          # setup: the price book view with floor_price
databricks bundle run -t dev quote_ai_security_setup
databricks bundle run -t dev quote_ai_app_grants
databricks bundle run quote_ai_eval -t dev --params suites=data_authorization,model_access
python evaluation/quote_ai/run_eval.py --suite data_authorization
python evaluation/quote_ai/run_eval.py --suite data_authorization --variant app_identity    # the gap: unauthorized 2
pytest tests/quote_ai/test_pricing_injection.py
```

## Verify before recording

- Databricks Apps user authorization: `user_api_scopes` and the scope names, the `x-forwarded-access-token` header,
  `WorkspaceClient(host, token, auth_type="pat")` and `databricks-sql-connector` `access_token=` with it.
- Row filter and column mask syntax; `is_account_group_member` and `current_user()` in a filter, a mapping-table
  subquery, and in a dynamic view (the price book).
- Unity Gateway guardrail options (safety, PII) and whether the bundle can declare them.
- `system.query.history` / `system.access.audit` columns and retention (`security_job.py audit`).
- The demo groups exist in dev only; no real customer data anywhere.

## Differences from the draft

- The draft's row filter on `price_book` by `account_owner` and mask on `price_book.unit_cost`: the price book here is
  a view over AI-02 bronze, priced per tier (no account column), and row filters and masks can't be set on a view. So
  the account filter is on the per-account tables Genie and the metric views read (quote_fact, order_line, and
  rep_account), the mask on their `unit_cost`, and the price book view masks its own `cost` inline (managers only)
  and adds `floor_price`, so pricing as the rep still checks the margin floor.
- Quotes live on Lakebase (AI-20), where Unity Catalog row filters don't apply: "may this rep open this quote" is
  asked of Unity Catalog as the rep (the accounts `rep_account`'s row filter returns), then the quote is read by
  QuoteService. The draft's "quote by account owner" row filter is that check.
- sec-01 runs as rep A (who owns Lakeside), not rep B as on the slide: as rep B the email never gets past "no access
  to this account", so the injection would not be tested. sec-03 is the rep B case.
- The draft lists `resources/quote_ai/quote_ai_gateway.serving.yml`; the model services are `model_services`
  resources and service policies are not a bundle key in the CLI this repo pins (AI-06), so the guardrails are a
  notes file until they are. The draft's 60 calls per user per minute vs the 20 declared since AI-06: unchanged here.
- The script says the app's identity keeps only audit writes and eval reads; it also keeps the product model and
  GUDID gold (the same for every rep), the account list for knowledge retrieval, and Lakebase (quote writes through
  QuoteService). Listed in decision record 0006.
- The knowledge specialist loses `price_quote` (least privilege); sec-05 is the demo's "grant it on purpose" break.
