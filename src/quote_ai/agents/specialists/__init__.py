"""The specialists of design B (AI-21): each a focused agent with a few tools and a short prompt, or no model at all.

  catalog.py    search, eligibility, evidence, the GUDID record, stock and fulfilment (UC function tools)
  knowledge.py  explanations with sources: the knowledge index (RAG, AI-16) and the evidence rows
  analyst.py    the DBxLab sales analyst Genie Agent, through MCP in the workspace (AI-11, AI-19)
  pricing.py    not an LLM: price_quote, the discount band and the win chance, by rule

Whether these beat one agent is measured, not assumed: evaluation/quote_ai/experiments/0004_one_vs_specialists.py and
docs/quote_ai/decisions/0004-orchestration-and-approval.md.
"""
