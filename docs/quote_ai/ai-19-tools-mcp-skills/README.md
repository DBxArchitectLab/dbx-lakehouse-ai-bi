# AI-19: Tools, MCP and Agent Skills

Tools are context. The agent gets them the standard way: managed MCP servers for Genie, AI Search and our UC
functions; the live AccessGUDID API through a governed Unity Catalog connection; DBxLab's quoting playbook as an
Agent Skill loaded on demand. And three more reliability rules: idempotent tools, duplicate calls served once, partial
failure named, never guessed.

| File | What |
| --- | --- |
| `src/quote_ai/gudid/api_client.py` | AccessGUDID v3 Device Lookup for ONE device: `parse_lookup`, `LiveGudid` (24 h cache per DI), `uc_fetch` (through the UC function), `http_fetch`, `DemoGudidApi` |
| `src/quote_ai/rules/sql/gudid_live_lookup.sql` | The HTTP connection `quote_ai_accessgudid` and `quote_ai_ai.gudid_live_lookup(di)` |
| `resources/quote_ai/quote_ai_accessgudid.connection.notes.md` | How the connection is created (SQL or Terraform), its grants and rules of use |
| `src/quote_ai/agents/mcp.py` | `server_urls`, `mcp_servers`, `discover_tools`, `McpTools`, `CompositeToolbox`: discovered, not hand-wired (`QUOTE_AI_MCP=1`) |
| `src/quote_ai/agents/agent.py` | Tools from MCP plus the skill loader (the skills listed in the system prompt; `load_skill` on demand) |
| `src/quote_ai/agents/skills/quoting-playbook/SKILL.md`, `discount-talk.md` | The playbook (demo content); `skills/__init__.py` loads it |
| `src/quote_ai/agents/reliability.py` | `Turn`: a duplicate gets the first result; a tool out of retries is `{"unavailable": ...}` and not called again; at most 5 live lookups per conversation; `ToolRefused` (PERMISSION_DENIED) never retried |
| `src/quote_ai/app/backend/routers/chat.py` | A `source` event per tool result; `BADGES`, the one table from source to badge |
| `src/quote_ai/app/frontend/src/components/SourceBadge.tsx` | The badges under an answer: Live GUDID record (retrieved today, version), Lakehouse copy (as of), Genie (show the query), Quoting playbook (section) |
| `tests/quote_ai/test_api_client.py`, `fixtures/accessgudid_device_lookup.json` | A saved v3 response parsed; no live call in CI |
| `tests/quote_ai/test_tool_access.py` | Servers scoped to the ai schema; a call outside the grants is PERMISSION_DENIED, not retried |
| `evaluation/quote_ai/v1/workflow` (W-009 to W-018), `reliability` (R-005 to R-008) | Tool choice incl. the overlap break; duplicates and partial failure |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_agent_registration          # functions --live: the connection and gudid_live_lookup
python evaluation/quote_ai/run_eval.py --suite workflow --kinds tool_choice
python evaluation/quote_ai/run_eval.py --suite workflow --kinds tool_choice --variant overlapping   # the break
python evaluation/quote_ai/run_eval.py --suite workflow --variant always_playbook                  # tokens
```

Locally (stand-in): with the overlapping `find_gloves` tool added, tool-choice accuracy on the tool-choice cases
drops from 1.00 to 0.71; removed, back to 1.00. The playbook pasted into every prompt costs more tokens per request
than loading the skill on demand (compare `tokens_per_request` of `default` and `always_playbook`).

## Verify before recording

- The managed MCP server URLs, the `databricks-mcp` client (`DatabricksMCPClient`, `list_tools`, `call_tool`), the
  tool names it reports for UC functions, and Genie MCP availability in the Azure region.
- `CREATE CONNECTION ... TYPE HTTP` options and `http_request()`; whether an external MCP server through a UC
  connection is the preferred route.
- The AccessGUDID v3 lookup path and response fields (the fixture follows the documented shape), terms of use.
- Agent Skills support in the agent framework (the local loader reads the same SKILL.md folder).

## Differences from the draft

- The connection is `quote_ai_accessgudid`, not `accessgudid` (isolation rule; the draft's GRANT line already used
  the prefixed name).
- The MCP servers are scoped to the physical `quote_ai_ai` schema (the slide says `.../{catalog}/ai`).
- `src/quote_ai/gudid/api_client.py` is new and separate from the ingestion framework's history client
  (`quote_ai/ingest/gudid_client.py`, unchanged): one live record versus bulk history.
