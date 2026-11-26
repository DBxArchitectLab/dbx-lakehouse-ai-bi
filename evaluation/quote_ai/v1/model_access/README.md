# Model access suite (AI-06)

One allowed call and three that Unity Gateway must refuse, on `quote_ai_chat_<target>`:

| Case | As | Expected |
| --- | --- | --- |
| allowed | a member of `DBxLab_Sales_Reps` | answered; one usage row (who, service, tokens) |
| no-grant | a user outside the reps group | HTTP 403 |
| over-the-limit | a rep, 25 calls in a minute | the first 20 answered, then HTTP 429 |
| blocked-content | a rep, a request with an email address and a phone number | blocked by the PII service policy |

All four must appear in the usage table with the caller's identity. Run by `notebooks/quote_ai/06_gateway_tests.py`
(each case as the identity in the table); the outcome logic is `quote_ai.llm.gateway_checks`, unit-tested in
`tests/quote_ai/test_endpoints.py`. Model access is tested apart from data authorization (AI-24). The contact details
in the blocked case are made up.

**AI-24 adds two gateway cases** from the threat model (decision record 0006): `sec-06` (personal data in a request:
blocked on the way in, nothing in the inference table) and `sec-07` (a request for a very long dump: stopped by the
output guardrail and the token limit). Model access stays its own suite, run separately from data authorization: the
gateway decides who may call a model; Unity Catalog decides what data a caller sees.
