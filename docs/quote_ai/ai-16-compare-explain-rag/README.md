# AI-16: Compare and explain: RAG, build or buy

QuoteAI v2. The rep compares the eligible gloves side by side and asks questions; every value in the table comes from
data (evidence, price book, stock), every glove's status from the AI-15 rules, and every sentence of an explanation
cites its source and names its layer of truth. Nothing found -> "no evidence on file". No quality score.

| File | What |
| --- | --- |
| `src/quote_ai/search/chunks.py` | Section-aware chunks (title kept inside), fixed500 chunks (the AI-17 baseline); chunk ID, account, truth layer |
| `src/quote_ai/mock/knowledge.py` | Playbook, GMDN notes, customer price sheets (demo data), rep accounts; `sources` for the job and the demo |
| `src/quote_ai/search/knowledge_tables.sql` | `quote_ai_ai.knowledge_chunks` (CDF on), `quote_ai_sales.rep_account` |
| `src/quote_ai/jobs/knowledge_job.py`, `resources/quote_ai/quote_ai_knowledge_index.job.yml` | setup, accounts, chunk (from the parsed supplier PDFs), sync `knowledge_index`, grants |
| `src/quote_ai/search/index.py`, `retriever.py` | + `knowledge_index`; `retrieve_chunks(question, products, k, accounts)` |
| `src/quote_ai/product/compare.py` | The comparison table from data; `table_markdown`; `cell_layer` |
| `src/quote_ai/llm/prompts.py` | + `compare_explain` v1 (Prompt Registry) |
| `src/quote_ai/llm/rag.py` | `explain_comparison` (traced), `ground`, `rule_statements`, the extractive stand-in, `llm_generate` |
| `src/quote_ai/llm/knowledge_assistant.py`, `resources/quote_ai/quote_ai_knowledge_assistant.notes.md` | The buy option |
| `src/quote_ai/app/backend/routers/compare.py` | `GET /api/compare/{id}`, `POST /api/compare/{id}/ask` (build or buy, cached per quote version) |
| `src/quote_ai/app/frontend/src/pages/Compare.tsx` | The compare view, Explain the differences, Ask about this product, citations |
| `resources/quote_ai/quote_ai_app.app.yml`, `app.yaml` | + `chat-service` (EXECUTE on `quote_ai_chat_<target>`) |
| `tests/quote_ai/test_compare.py`, `test_app_season4.py` | Table = source rows, no score, statements cited and layered, the routes |
| `evaluation/quote_ai/v1/comparison/cases.jsonl` | C-001 to C-020 (AI-17 adds 10) |
| `docs/quote_ai/decisions/0003-rag-build-or-buy.md` | Build or buy |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_knowledge_index
python -m quote_ai.llm.prompts --catalog dbxarchitectlab_dev --name compare_explain --version v1 --alias production
python evaluation/quote_ai/run_eval.py --suite comparison --variant build --endpoint quote_ai_chat_dev
python evaluation/quote_ai/run_eval.py --suite comparison --variant buy --ka-endpoint quote_ai_knowledge_assistant
```

## How the answers stay true

- The model's truth-layer label is never trusted: `ground` takes the layer from what is cited (a chunk's layer comes
  from the evidence: business truth only for a document version with verified evidence; a table cell's from its
  status). A citation that wasn't retrieved drops the statement and is reported.
- What the rules say is missing is a cited statement of its own: `rule:GLOVE-B:chemo_tested` -> "no chemo test report
  on file, so it is not offered for this until a verified document arrives".
- Glove C keeps its label from the rules ("alternative, needs acceptance"); Glove B is listed under needs evidence,
  not compared as an option.

## Differences from the draft

- The demo's amber "tensile strength: unverified" is, in the demo data, **length** (AI-read from the description):
  every spec-sheet value is verified (signed off by demo:qa). An unverified tensile value needs a spec sheet without
  its sign-off.
- Chunking runs in the knowledge job from `parsed_documents`, not inside the documents pipeline, so the pipeline
  keeps its tables; the chunker is the shared `quote_ai.search.chunks`.
- The comparison is cached in the app per quote version, question, scope and the rep's accounts (in memory; a
  Lakebase cache arrives with AI-20).

## Verify in the workspace

Knowledge Assistant availability, creation and response shape; `mlflow.genai.load_prompt`; the OpenAI-compatible
client for the chat service with `response_format`; the AI Search filter on `account_id` and `product_id` lists.
