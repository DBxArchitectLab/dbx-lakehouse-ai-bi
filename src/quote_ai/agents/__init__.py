"""QuoteAI's agent (Season 5, AI-18 to AI-21). The agent chooses tools and proposes; the workflow and QuoteService
commit. Nothing in this package writes a quote.

  tools.py         the tool specs the model reads and their executors (the shared rules and stores)
  reliability.py   call_tool: timeout, retries with back-off, per-turn dedupe, partial failure
  loop_by_hand.py  the tool-calling loop in plain Python (the teaching version, and the one the agent runs)
  agent.py         QuoteAgent: the same loop as an MLflow ResponsesAgent
  boundary.py      the answer checked against the tool outputs; proposals; writes in a trace
  standin.py       a deterministic stand-in for the chat model: tests, CI suites and the local demo
  mcp.py, skills/  managed MCP servers and the quoting-playbook Agent Skill (AI-19)
  memory.py        conversation memory and confirmed customer facts, on Lakebase (AI-20)
  server.py        the agent's HTTP server on the Agent Runtime (Databricks Apps, AI-20)
  orchestrator.py, specialists/   rule-based routing to catalog, knowledge, pricing and analyst (AI-21)
"""
