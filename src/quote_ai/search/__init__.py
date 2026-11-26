"""Search (Season 4): the SKU search document and Databricks AI Search (formerly Vector Search) indexes over it and
over the knowledge chunks; the retriever finds candidates, the rules (quote_ai.rules) decide.

  docs.py       the search document: one row per sellable SKU, its search text (AI-15)
  chunks.py     section-aware chunks of supplier documents, the playbook and GMDN notes (AI-16)
  local.py      an in-memory index with the same similarity_search answer shape: tests, the local demo, the suites
  retriever.py  hybrid query + filters; candidates per product; chunks per question, filtered by account (AI-15-17)
  index.py      create and sync the Delta Sync indexes (the quote_ai_search_index and quote_ai_knowledge_index jobs)
"""
