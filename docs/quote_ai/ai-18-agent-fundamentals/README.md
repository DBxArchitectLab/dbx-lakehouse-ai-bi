# AI-18: Agent fundamentals, the tool-calling loop by hand

An agent is a loop: the model reads the conversation and the tool specs, asks for a tool call, our code runs it, the
result goes back, and the loop repeats until it can answer. The agent chooses the tools; the workflow owns the quote.
It never overrides a rule result and never commits a change: its only quote tool is `propose_transition`, which
writes nothing.

| File | What |
| --- | --- |
| `src/quote_ai/rules/pricing.py` | `price_quote(sku_id, qty, unit, account_tier, book, packaging)`: units from packaging (never assumed), the effective-dated tier row, the margin floor, the price book row ID. `QuoteService.price` prices every line with it: PRICED means price_quote's number |
| `src/quote_ai/rules/sql/tools.sql` | The tools as UC functions in quote_ai_ai: search_candidates, get_evidence, get_device, price_quote (+ price_rules), win_probability; check_eligibility / check_fulfilment from AI-15. Each COMMENT is the description the model reads |
| `src/quote_ai/agents/tools.py` | `TOOL_SPECS` (name, description, typed parameters) and `Toolbox` (the executors over the shared rules and stores); `WRITE_TOOLS` is empty |
| `src/quote_ai/agents/reliability.py` | `call_tool`: 10 s timeout, 3 retries on transient errors only (1, 2, 4 s plus jitter), then `ToolUnavailable`; a rule answer is never retried |
| `src/quote_ai/agents/loop_by_hand.py` | `run` / `run_stream`: the loop with its stop conditions (answered, 8 steps, no progress, a tool still failing); `SYSTEM`; the answer checked against the tool outputs |
| `src/quote_ai/agents/boundary.py` | `check_answer` (prices and eligibility must equal the tools'), `proposals`, `writes_in_trace` |
| `src/quote_ai/agents/agent.py` | `QuoteAgent`, an MLflow `ResponsesAgent`: `predict` / `predict_stream` run the same loop; `events()` for the app |
| `src/quote_ai/agents/standin.py` | A deterministic stand-in for the chat model: tests, CI suites, the local demo (labelled; never ships) |
| `src/quote_ai/agents/runtime.py` | The tools' dependencies (demo or workspace) and the model client |
| `src/quote_ai/jobs/agent_job.py`, `resources/quote_ai/quote_ai_agent_registration.job.yml` | The functions, QuoteAgent logged and registered as `quote_ai_ai.quote_agent`, EXECUTE grants |
| `src/quote_ai/app/backend/routers/chat.py` | `POST /api/chat/{quote_id}`: a StreamingResponse of server-sent events (step, token, proposal, done with the trace ID) |
| `src/quote_ai/app/frontend/src/components/ChatPanel.tsx` | The chat panel on the Quote page: reads the stream with fetch and a reader (a POST, so not EventSource) |
| `tests/quote_ai/test_pricing.py`, `test_tools.py`, `test_agent_boundary.py`, `test_reliability.py` | Units, tiers, margin; each tool alone; the agent cannot commit; timeouts, retries, stops |
| `evaluation/quote_ai/v1/pricing`, `reliability`, `workflow` | The first pricing, reliability and tool-selection cases |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_agent_registration
python -m quote_ai.agents.loop_by_hand --request lakeside-001                    # stand-in model, from src/
python -m quote_ai.agents.loop_by_hand --request lakeside-001 --endpoint quote_ai_chat_dev
python -m quote_ai.agents.loop_by_hand --message "What if size M is out of stock?"
python evaluation/quote_ai/run_eval.py --suite pricing
python evaluation/quote_ai/run_eval.py --suite reliability
```

Locally (stand-in): search_candidates -> check_eligibility -> check_fulfilment -> price_quote (A, C, D: the four
lines at $8.60 from the price book) -> win_probability -> the answer, "Options in order: 1. GLOVE-A, 2. GLOVE-D,
3. GLOVE-C". The follow-up, "What if size M is out of stock?", runs check_fulfilment with GLOVE-A-M assumed out of
stock, then check_eligibility: Glove D ships in full, Glove C is an alternative date, each priced by price_quote.

## Verify before recording

- `ResponsesAgent`, `create_text_output_item`, `create_function_call_item`, `create_text_delta` and models-from-code
  logging in MLflow 3 (`agent.py`, `jobs/agent_job.py`).
- Tool calling of the Foundation Model behind quote_ai_chat in the region, through the OpenAI-compatible client.
- `vector_search()` as a table function and `ai_query` on the win model endpoint in `tools.sql`.

## Differences from the draft

- `price_quote` keeps the draft's signature and returns the line as a dict; the row lookup it used before is now
  `book_price` (its callers changed with it).
- `check_fulfilment` takes `unavailable_skus` (e.g. GLOVE-A-M) for the "what if size M runs out" question, so only
  the glove on the quote loses its stock.
- The app's chat imports QuoteAgent in-process until AI-20 (as the draft says); the stand-in model is the default only
  where no workspace is configured (`QUOTE_AI_DEMO=1`, tests).
- The Catalog Explorer shot names "ai functions": the schema is `quote_ai_ai` (isolation rule).
