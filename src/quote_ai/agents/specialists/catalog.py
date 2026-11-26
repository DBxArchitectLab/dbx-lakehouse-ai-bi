"""The catalog specialist (AI-21): which DBxLab gloves fit, can ship and are on file. UC function tools only."""
from __future__ import annotations

from quote_ai.agents.agent import QuoteAgent

TOOLS = ["search_candidates", "check_eligibility", "get_evidence", "get_device", "gudid_live_lookup",
         "check_fulfilment"]
PROMPT = ("You are DBxLab's catalog specialist. Find candidate gloves, check eligibility and fulfilment, and read the "
          "evidence and device records. You do not price and you do not change quotes.")


def make(deps, client=None, endpoint: str | None = None) -> QuoteAgent:
    return QuoteAgent(deps=deps, client=client, endpoint=endpoint, tools=TOOLS, prompt=PROMPT)
