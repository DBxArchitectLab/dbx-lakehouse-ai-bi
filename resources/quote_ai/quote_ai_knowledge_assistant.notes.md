# The Knowledge Assistant (AI-16): how the "buy" option is created

Agent Bricks Knowledge Assistant is not declared as a bundle resource here (verify whether it can be now). It is
created once per workspace, in the UI or through the API, on the same sources as the build chain:

| Setting | Value |
| --- | --- |
| Name | `quote_ai_knowledge_assistant` (dev); `quote_ai_knowledge_assistant_prod` in prod |
| Sources | the `quote_ai_documents` volume folders `specs/`, `test_reports/`, `marketing/` (synthetic supplier PDFs), and the playbook and GMDN notes exported as text files to `knowledge/` (from `quote_ai.mock.knowledge`) |
| Not given | the customers' price sheets: the assistant has no per-account filter, so customer-confidential documents stay out of it |
| Instructions | the rules of the `compare_explain` prompt (`src/quote_ai/llm/prompts.py`): only the documents, cite every statement, say "no evidence on file", a description's claim is a claim |
| Permissions | CAN_QUERY for sp-dbx-lakehouse (the evaluation runs) and, if the demo uses it, the app's service principal |

Steps (UI): Agents > Knowledge Assistant > Create; add the volume folders as knowledge sources; paste the
instructions; wait for the sync; copy the endpoint name.

Ask it: `quote_ai.llm.knowledge_assistant.ask_ka(question, endpoint)`; score it on the same questions as the build
chain: `python evaluation/quote_ai/run_eval.py --suite comparison --variant buy --ka-endpoint <name>`. In the app,
set `QUOTE_AI_KA_ENDPOINT` (and add the endpoint as an app resource with CAN_QUERY) to show it side by side.

Verify before recording: availability in the Azure region, the create API, how citations come back (file names or
URLs per sentence), the Responses API call, and cost per question. The decision and its reasons:
`docs/quote_ai/decisions/0003-rag-build-or-buy.md`.
