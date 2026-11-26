# AI Search endpoint and index (AI-15): created by the job, not declared here

Databricks AI Search (formerly Vector Search) endpoints and Delta Sync indexes are not bundle resources in this
bundle yet. The `quote_ai_search_index` job creates them when they don't exist and syncs them every night
(`src/quote_ai/search/index.py`, called by `src/quote_ai/jobs/search_job.py index`):

| Object | Name | Notes |
| --- | --- | --- |
| Endpoint | `quote_ai_search` | One per workspace (dev and dev-shared share it; prod has its own). Standard. |
| Index | `<catalog>.quote_ai_ai.sku_search_index` | Delta Sync on `quote_ai_ai.sku_search_doc`, primary key `sku_id`, embedding source `search_text`, TRIGGERED |
| Index | `<catalog>.quote_ai_ai.knowledge_index` | Delta Sync on `quote_ai_ai.knowledge_chunks` (AI-16, `quote_ai_knowledge_index` job) |
| Embeddings | `quote_ai_embed_<target>` | The Unity Gateway model service (AI-06); paid per changed row, not per query |

In a personal `dev` copy the schema is `dev_<user>_quote_ai_ai`, so the indexes are too; the endpoint is shared.

Identities: the job (and so the sync) runs as sp-dbx-lakehouse in dev-shared and prod. The app's service principal
gets `SELECT` on the index and `EXECUTE` on `check_eligibility` / `check_fulfilment` from the job's `grants` task.

Verify before recording:
- whether AI Search endpoints and indexes can be declared in `resources/` now (then move them here and drop the
  create-if-missing code);
- `create_delta_sync_index` with `embedding_model_endpoint_name` set to a Unity Gateway model service name;
- reranking and hybrid weighting options in the region (`quote_ai.search.retriever.reranker_arg`);
- the privilege for querying an index (`SELECT` on the index as a table securable).

Delete (dev only): `VectorSearchClient().delete_index(endpoint_name="quote_ai_search", index_name=...)`.
