"""The analyst (AI-21): the DBxLab sales analyst Genie Agent (AI-11) as the one tool, through the managed MCP server
for Genie in the workspace (AI-19). Buying history, prices paid, win rates; read-only."""
from __future__ import annotations

from quote_ai.agents.agent import QuoteAgent

TOOLS = ["ask_genie"]
PROMPT = ("You are DBxLab's sales analyst. Answer questions about what accounts bought, at what price, and win rates, "
          "only from the Genie tool's result, with its SQL. If it is unavailable, say so; never estimate.")


def make(deps, client=None, endpoint: str | None = None) -> QuoteAgent:
    return QuoteAgent(deps=deps, client=client, endpoint=endpoint, tools=TOOLS, prompt=PROMPT)
