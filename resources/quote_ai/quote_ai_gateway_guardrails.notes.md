# Unity Gateway guardrails (AI-24): the second layer

The first layer is identity, Unity Catalog and the rules (decision record 0006). The guardrails on the model services
are the second, per service and per environment:

| Service | Input | Output | Rate limit |
| --- | --- | --- | --- |
| `quote_ai_chat_<target>` | safety on; personal data (PII) **blocked** | safety on; PII **masked**; `max_tokens` from `config/quote_ai/models.yml` | 20 per user per minute (`quote_ai_chat.model_service.yml`) |
| `quote_ai_extract_<target>` | safety on; PII **blocked** | PII masked | as declared in its model service file |
| `quote_ai_chat_small_<target>` | as chat | as chat | as declared |

The draft's sketch (verify the syntax before recording; Unity Gateway service policies are not a bundle key in CLI
v1.22.1, see `quote_ai_chat.model_service.yml`):

```
guardrails:
  input:  {safety: true, pii: {behavior: BLOCK}}
  output: {safety: true, pii: {behavior: MASK}}
rate_limits:
  - {calls: 60, key: user, renewal_period: minute}
```

Create the policy `quote_ai_guardrails` in Unity Gateway with these settings, attach it to each service, and record
it in the model service files once the bundle supports it. Tested by the Model access suite: `sec-06` (PII) and
`sec-07` (a long dump), run as each identity by `notebooks/quote_ai/06_gateway_tests.py`, outcomes into
`run_eval.py --suite model_access --outcomes`.
