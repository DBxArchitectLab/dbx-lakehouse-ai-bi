"""Which customer accounts a signed-in rep owns (AI-17): what the knowledge retrieval may show them.

The account filter is taken from the caller's identity (the email Databricks Apps forwards), never from the request
text, and applied inside the AI Search query, before retrieval (quote_ai.search.retriever.retrieve_chunks). Until
AI-24 the app queries as its own service principal and this code applies the filter; AI-24 moves it into Unity
Catalog with user authorization, so the platform enforces it too.

  SqlAccounts    {sales}.rep_account (written by the quote_ai_knowledge_index job from quote_ai.mock.knowledge)
  DemoAccounts   quote_ai.mock.knowledge.REP_ACCOUNTS
"""
from __future__ import annotations


def rep_id(user: str) -> str:
    """rep.alice@dbxlab.example -> rep.alice"""
    return user.split("@", 1)[0].strip().lower()


class DemoAccounts:
    def __call__(self, user: str) -> list[str]:
        from quote_ai.mock import knowledge
        return knowledge.accounts_of(user)


class SqlAccounts:
    def __init__(self, sql, names: dict[str, str]) -> None:
        self.sql, self.n = sql, names

    def __call__(self, user: str) -> list[str]:
        rows = self.sql.query(f"SELECT account_id FROM {self.n['sales']}.rep_account WHERE rep_id = :r",
                              {"r": rep_id(user)})
        return sorted(r["account_id"] for r in rows)
