# AI-11: Genie Agents, the DBxLab sales analyst

A Genie Agent (formerly a Genie space) answers sales and catalog questions in plain language, on the metric views of
AI-10, the SKU model and the GUDID glove devices. Its context is code: comments on every table it reads, a YAML
definition with instructions, ontology terms and example SQL, and a 40-question benchmark scored on results before
and after curation.

| File | What |
| --- | --- |
| `src/quote_ai/semantic/genie/sales_analyst.yml` | The agent: tables and the columns it uses, instructions (sales, catalog, limits), ontology terms, example SQL |
| `src/quote_ai/semantic/genie/deploy_genie.py` | Create or update the agent from the YAML; share it with the Sales groups |
| `src/quote_ai/semantic/genie/client.py` | `ask()`: the Conversation API; `answer_from()`: text, SQL and rows |
| `src/quote_ai/semantic/comments.sql` | Comments for the views and GUDID tables the agent reads |
| `resources/quote_ai/quote_ai_genie.job.yml` | `quote_ai_genie`: comments, then deploy |
| `evaluation/quote_ai/v1/genie/cases.jsonl` | 40 cases (25 sales, 15 catalog), expected SQL, compared on results |
| `evaluation/quote_ai/run_eval.py` | `--suite genie` |
| `src/quote_ai/app/backend/routers/ask_data.py`, `frontend/src/pages/AskData.tsx` | Ask the data: answer, table, SQL |
| `resources/quote_ai/quote_ai_app.app.yml` | + the Genie space resource (CAN_RUN) |
| `tests/quote_ai/test_genie.py` | Comment coverage, the benchmark's tables, the agent JSON, answer parsing, scoring |

## Deploy and run

```
databricks bundle run quote_ai_semantic -t dev
databricks bundle run quote_ai_genie -t dev          # prints the space ID
databricks bundle deploy -t dev --var quote_ai_genie_space_id=<id>   # the app's Ask the data panel
databricks bundle run quote_ai_app_grants -t dev
python evaluation/quote_ai/run_eval.py --suite genie --catalog dbxarchitectlab_dev --space-id <id> --warehouse-id <id>
```

The uncurated start (demo step 1): create a space by hand with the tables only, no comments and no examples, and run
the same benchmark against it (`--space-id` of that space).

## Identity

In Genie itself, the agent runs queries as the asking user, so Unity Catalog decides what a rep or a manager sees.
In the app, until AI-24 the back end asks as the app's service principal, which is granted only what the agent reads
(`product_job.py app_grants`). `quote_fact` and `order_line` still carry `unit_cost`, and `order_metrics` has
`margin_pct`: column masks for reps arrive in AI-24.

## Design notes

- The agent reads `quote_fact` and `order_line` (the synthetic history) rather than the app's live `quote` and
  `quote_line`, which join with the AI-20 sync; and `quote_ai_gudid_silver.device_size`, so the size 7.5 question
  can be answered (the glove subset is exam gloves: LZA, LZC, LYY, so the honest answer is small or zero).
- The customer name column is `customer.name` (the draft's example says `account_name`).
- The benchmark is also loaded into the agent (`benchmarks` in the serialized space), so it runs in the Genie UI.

## Manual steps until the API carries them

Verify what `serialized_space` accepts (GET a space with `include_serialized_space=true`). If ontology terms,
benchmarks or the sharing call are not accepted, set them in the UI from the YAML (it stays the source of truth) and
note it here.

## Verify before recording

- Product names: Genie Agents, Genie Ontology; what the ontology covers today.
- Genie API: `create_space` / `update_space` with `serialized_space`; permissions object type `genie`.
- The `genie_space` app resource keys; `start_conversation_and_wait` and `get_message_attachment_query_result`.
- `COMMENT ON COLUMN` on views and pipeline tables.
- On screen: sales data is synthetic demo data.
