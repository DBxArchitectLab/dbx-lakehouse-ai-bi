# AI-20: Memory, sessions and the Agent Runtime

Short-term memory is the conversation on a quote; long-term memory is what a rep confirmed about the customer. State
is data, not chat: quotes move to Lakebase (same states, same rules, one transaction per move), and a read-only Delta
copy keeps analytics unchanged. The agent runs on the Agent Runtime on Databricks Apps and fails well: a GUDID
outage falls back to the lakehouse copy, labelled, and stale data never issues a quote.

| File | What |
| --- | --- |
| `src/quote_ai/rules/sql/lakebase_schema.sql` | Postgres: quote, quote_line, quote_version, quote_event, idempotency_key, approval (AI-21), customer_memory, conversation. The same statements run on SQLite in the tests |
| `src/quote_ai/storage/lakebase.py` | `Db` (one transaction per block; psycopg on Lakebase with an OAuth token, sqlite3 in tests), `apply_schema` |
| `src/quote_ai/storage/lakebase_repository.py` | `LakebaseQuoteRepository`: row lock, revision check ("quote changed; reload"), idempotency key, quote, lines, version, approval, event in one transaction; `ReadOnlyRepository` for the migration's pause |
| `src/quote_ai/rules/quote_states.py` | Unchanged states (AI-21 adds APPROVED -> PENDING_APPROVAL for a changed version) |
| `src/quote_ai/services/quote_service.py` | `issue()`: the freshness policy, `StaleEvidence` naming each stale input and its date; `confirm_fact`; `confirm(..., from_memory=...)` |
| `src/quote_ai/rules/freshness.py` | `stale_details`: "gudid as of 2026-11-12 09:00 UTC" (GUDID-derived evidence 48 h, stock 24 h, `config/quote_ai/policy.yml`) |
| `src/quote_ai/agents/memory.py` | `load_context(db, rep, quote_id)`, `Memory`: messages, confirmed facts, isolation per rep, 30-day retention, `suggested_requirements` |
| `src/quote_ai/agents/tools.py` | `get_device`: the live record first (3 s), the lakehouse copy as a labelled fallback; `propose_memory` |
| `src/quote_ai/ops/migrate_quotes_to_lakebase.py` | Pause, copy, prove counts and checksums per table (or roll back), switch, sync |
| `src/quote_ai/storage/lakebase_sync.sql`, `src/quote_ai/jobs/lakebase_job.py` | The read-only Delta copy under the AI-07 names; schema, migrate, sync, purge |
| `resources/quote_ai/quote_ai_lakebase.database.yml` | The database catalog (instance `quote-ai-db`, database `quote_ai`) and the jobs `quote_ai_lakebase`, `quote_ai_lakebase_migrate` |
| `src/quote_ai/agents/server.py`, `src/quote_ai/agent_app/`, `resources/quote_ai/quote_ai_agent.app.yml` | The agent app `quote-ai-agent-<target>` on the Agent Runtime; `RemoteAgent`, the QuoteAI app's client |
| `resources/quote_ai/quote_ai_app.app.yml`, `src/quote_ai/app/app.yaml` | The QuoteAI app gets the Lakebase database resource; `QUOTE_AI_STORE=lakebase` |
| `src/quote_ai/app/backend/routers/quotes.py`, `chat.py` | My quotes from Lakebase (`?open=true`), `GET /api/quotes/{id}/resume`; the chat saves the conversation, `POST /api/chat/{id}/memory` |
| `src/quote_ai/app/frontend/src/pages/MyQuotes.tsx` | List and reopen quotes; the chat panel reloads the conversation |
| `tests/quote_ai/test_memory.py`, `test_freshness.py`, `test_lakebase_repository.py`, `test_quote_states.py` | Isolation, confirm-before-save; stale inputs block ISSUED; QuoteService on Lakebase, the migration's proof, the agent app; the state table |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_lakebase                  # schema (the sync waits for the migration)
# migration: redeploy the app with QUOTE_AI_READ_ONLY=1, then
databricks bundle run -t dev quote_ai_lakebase_migrate          # copy + counts and checksums per table, or roll back
# switch: QUOTE_AI_STORE=lakebase (app.yaml), unset QUOTE_AI_READ_ONLY, deploy
python scripts/quote_ai/build_app.py                            # vendors quote_ai into both apps
databricks bundle run -t dev quote_ai_agent                     # the agent on the Agent Runtime
databricks bundle run -t dev quote_ai_app                       # start the app
pytest tests/quote_ai/test_quote_states.py tests/quote_ai/test_memory.py
```

## Verify before recording

- Lakebase on Azure in the region; the instance in dbx-platform-infra-azure; the `database_catalogs` bundle keys.
- Synced tables from Lakebase to Delta (direction and mode). Until confirmed, `quote_ai_lakebase` refreshes the
  Delta copy from the registered database catalog every 15 minutes, and only after the migration ran.
- The Agent Runtime's server contract on Databricks Apps; app-to-app calls with an OAuth token; the PG* variables a
  database resource sets; `generate_database_credential`.
- Built-in agent memory or session features worth using instead of our tables.

## Differences from the draft

- The draft's app resource for the agent is a serving endpoint; QuoteAI follows the platform direction: the agent is
  its own Databricks App on the Agent Runtime (`quote_ai_agent.app.yml`) that grants the QuoteAI app CAN_USE, and the
  chat route calls it (`RemoteAgent`, URL from the app name).
- The script says "synced tables take over sales.quote": implemented as a sync job over the registered database
  catalog until Lakebase-to-Delta synced tables are confirmed (see above).
- customer_memory carries owner_rep and shared, so isolation is in the table (the script mentions the owner rep).
- `QuoteService.confirm_fact` writes confirmed facts ("writes go through QuoteService", slide 11).
