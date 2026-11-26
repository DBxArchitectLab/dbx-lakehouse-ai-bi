"""The knowledge specialist (AI-21): explanations with sources (the RAG and Knowledge Assistant of AI-16), from the
knowledge index and the evidence rows. Why is Glove C thicker; what does the chemo report say."""
from __future__ import annotations

from quote_ai.agents.agent import QuoteAgent

# AI-24, least privilege: no price_quote here. Prices are the pricing specialist's (config/quote_ai/tool_grants.yml);
# granting this specialist price_quote is the break the data authorization suite catches (sec-05).
TOOLS = ["knowledge_search", "get_evidence"]
PROMPT = ("You are DBxLab's knowledge specialist. Explain differences between products with sources from the "
          "documents and the evidence rows; cite each statement. You never state a price: prices come from the "
          "pricing specialist. You do not change quotes.")


def make(deps, client=None, endpoint: str | None = None) -> QuoteAgent:
    return QuoteAgent(deps=deps, client=client, endpoint=endpoint, tools=TOOLS, prompt=PROMPT)
