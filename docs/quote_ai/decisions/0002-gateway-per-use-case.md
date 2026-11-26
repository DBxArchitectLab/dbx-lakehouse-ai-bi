# 0002: One Unity Gateway model service per use case, configured in the bundle

- **Episode:** AI-06, Unity Gateway
- **Status:** Accepted

## Context

After AI-05, notebooks and jobs called pay-per-token endpoints directly. Nobody could say who called, how much it
cost, or whether personal data was sent. More teams and the app (AI-07) are about to call models.

## Decision 1: one model service per use case

Options: (a) one shared service; (b) one per use case. **Choice: (b)** `quote_ai_extract`, `quote_ai_chat`,
`quote_ai_embed`, each named with the target (`quote_ai_chat_dev`, `quote_ai_chat_prod`).

- Access differs per job: reps and the app call chat; the pipelines' identity calls extract and embed; the app calls
  extract for one request at a time. Access is `EXECUTE`, granted like a table grant.
- Limits differ: 20 requests a minute per user on chat; a token budget per minute for the batch identity on extract
  and embed (sized from AI-04's token counts). Over the limit: HTTP 429.
- Guardrails differ: the chat service blocks personal data in the input (a service policy, Beta).
- Cost is attributed per use case from the usage table, without tagging every call.

## Decision 2: where the configuration lives

Options: UI clicks; Terraform in dbx-platform-infra-azure; the bundle. **Choice: the bundle** for the model services
(`resources/quote_ai/quote_ai_*.model_service.yml`), promoted dev -> prod with the application; account-level budget
policies stay in Terraform, because budgets belong to the platform.

## Consequences

- Code names a model service only through `quote_ai.llm.endpoints`; changing a model is a variable change
  (`quote_ai_chat_model`, ...) in `databricks.yml`.
- Payloads are logged to inference tables in `quote_ai_monitoring` (customer request text, demo data here): access to
  that schema is restricted.
- Service policies are not yet a bundle key (CLI v1.22.1): the PII policy is attached in Unity Gateway and checked by
  the Model access suite until the bundle can carry it.
- Prod services are callable only by the prod app and pipeline identities.
