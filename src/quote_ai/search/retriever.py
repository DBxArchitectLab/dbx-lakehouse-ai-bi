"""The retriever: hybrid queries and filters over Databricks AI Search (formerly Vector Search) indexes.

Retrieval finds candidates; it never decides. A filter on size or distribution status only narrows what the
eligibility engine is shown: the engine checks the same things again, from verified evidence, every time (AI-15).

  search_candidates   the SKU index: the rep's need as text, filtered by size and distribution (AI-15)
  candidate_products  the hits as products, in rank order: what the eligibility engine receives
  retrieve_chunks     the knowledge index: chunks for a question, scoped to products, and filtered by account
                      BEFORE retrieval, so a customer's document never reaches another rep's prompt (AI-16, AI-17)
  RetrieverConfig     the knobs AI-17 measures: query type, chunking, reranking, k (VARIANTS)

`index` is anything with similarity_search(query_text=..., columns=..., filters=..., num_results=...,
query_type=...): a databricks-vectorsearch index (index.get_index) or quote_ai.search.local.InMemoryIndex.
"""
from __future__ import annotations

from dataclasses import dataclass

PUBLIC = "public"     # the account_id of a chunk anyone may read (a filter can't match NULL, so it is a value)
GENERAL = "*"         # the product_id of a chunk about no product in particular (the playbook, GMDN notes)
SKU_COLUMNS = ["sku_id", "product_id", "catalog_number", "size", "search_text"]
CHUNK_COLUMNS = ["chunk_id", "doc_id", "doc_version", "product_id", "account_id", "section", "truth_layer", "text"]


@dataclass(frozen=True)
class RetrieverConfig:
    name: str
    query_type: str = "HYBRID"     # "ANN" (vector only) or "HYBRID" (keyword + vector)
    chunking: str = "section"      # knowledge index: "section" (AI-16) or "fixed500" (the baseline)
    rerank: bool = False
    k: int = 20
    hybrid_weight: float | None = None   # AI-23: the keyword share of a hybrid query (None: the index's default)


# The variants AI-17 compares (one MLflow run each). hybrid_section is AI-16's retriever.
VARIANTS = {
    "baseline": RetrieverConfig("baseline", "ANN", "fixed500", False),
    "hybrid": RetrieverConfig("hybrid", "HYBRID", "fixed500", False),
    "hybrid_section": RetrieverConfig("hybrid_section", "HYBRID", "section", False),
    "hybrid_rerank": RetrieverConfig("hybrid_rerank", "HYBRID", "section", True),
    # AI-23, E2: the hybrid weight tuned toward meaning (keywords 0.1: messy requests rarely use catalog words) and the
    # reranker on top
    "hybrid_weighted_rerank": RetrieverConfig("hybrid_weighted_rerank", "HYBRID", "section", True, 20, 0.1),
}


def hybrid_kwargs(weight: float | None) -> dict:
    """The hybrid weight as a similarity_search argument. Verify before recording whether Databricks AI Search
    exposes a keyword/vector weight for HYBRID queries, and its name; until then only the local index takes it, and
    with None nothing is sent."""
    return {} if weight is None else {"hybrid_weight": weight}
CHOSEN = "hybrid_section"


def reranker_arg(enabled: bool):
    """What to pass as similarity_search(reranker=...). Verify before recording: databricks-vectorsearch's
    `from databricks.vector_search.reranker import DatabricksReranker` and columns_to_rerank, and that reranking
    is available in the region; the local index takes any truthy value."""
    if not enabled:
        return None
    try:
        from databricks.vector_search.reranker import DatabricksReranker
        return DatabricksReranker(columns_to_rerank=["search_text"])
    except Exception:  # noqa: BLE001  no client installed (tests, the local demo)
        return True


def rows(result: dict) -> list[dict]:
    """A similarity_search answer as dicts (the manifest names the columns; the last one is the score)."""
    cols = [c["name"] for c in result.get("manifest", {}).get("columns", [])]
    return [dict(zip(cols, r)) for r in result.get("result", {}).get("data_array", []) or []]


def search_candidates(index, need: str, sizes: list[str] | None = None, k: int = 20, query_type: str = "HYBRID",
                      rerank: bool = False, in_distribution: bool | None = True,
                      hybrid_weight: float | None = None) -> list[dict]:
    """SKU hits for a need, best first. The filters only narrow candidates: the engine re-checks size and
    distribution from evidence. Verify before recording: the filters dict syntax (a list is IN) for Delta Sync
    indexes, and query_type="HYBRID"."""
    filters: dict = {}
    if sizes:
        filters["size"] = list(sizes)
    if in_distribution is not None:
        filters["in_commercial_distribution"] = in_distribution
    kwargs = {"query_text": need, "columns": SKU_COLUMNS, "filters": filters, "num_results": k,
              "query_type": query_type} | hybrid_kwargs(hybrid_weight)
    rr = reranker_arg(rerank)
    if rr is not None:
        kwargs["reranker"] = rr
    return rows(index.similarity_search(**kwargs))


def candidate_products(hits: list[dict], max_products: int = 8) -> list[str]:
    """Distinct products in the order of their best hit: the candidates the eligibility engine checks."""
    out: list[str] = []
    for h in hits:
        if h["product_id"] not in out:
            out.append(h["product_id"])
    return out[:max_products]


def representative_skus(hits: list[dict], size: str | None = None) -> list[str]:
    """One SKU per product in rank order (the size asked first, if the product has it): what Recall@5 counts."""
    out: dict[str, str] = {}
    for h in hits:
        pid = h["product_id"]
        if pid not in out or (size and h["size"] == size and not out[pid].endswith(f"-{size}")):
            out[pid] = h["sku_id"]
    return list(out.values())


def need_text(req) -> str:
    """The rep's confirmed requirements as a search query (quote_ai.domain.models.Requirements)."""
    parts = []
    if req.powder_free:
        parts.append("powder-free")
    if req.material:
        parts.append(req.material)
    if req.intended_use:
        parts.append(req.intended_use)
    parts.append("gloves")
    if req.chemo_tested:
        parts.append("chemo-tested")
    if req.latex_free:
        parts.append("latex-free")
    if req.sterile:
        parts.append("sterile")
    return " ".join(parts)


def account_filter(accounts: list[str] | None) -> list[str]:
    """The accounts a caller may read: their own (from their identity, never the request text) plus public."""
    return sorted(set(accounts or [])) + [PUBLIC]


def retrieve_chunks(index, question: str, product_ids: list[str] | None = None, k: int = 8,
                    accounts: list[str] | None = None, query_type: str = "HYBRID", rerank: bool = False,
                    chunking: str | None = None) -> list[dict]:
    """Chunks for a question, scoped to the products (plus general chunks) and to the caller's accounts (plus
    public). Both filters run inside the index query, before anything is retrieved. chunking selects the chunk set:
    knowledge_chunks holds both (section, AI-16's; fixed500, AI-17's baseline), so one index serves every variant."""
    filters: dict = {"account_id": account_filter(accounts)}
    if product_ids:
        filters["product_id"] = list(product_ids) + [GENERAL]
    if chunking:
        filters["chunking"] = chunking
    kwargs = {"query_text": question, "columns": CHUNK_COLUMNS, "filters": filters, "num_results": k,
              "query_type": query_type}
    rr = reranker_arg(rerank)
    if rr is not None:
        kwargs["reranker"] = rr
    return rows(index.similarity_search(**kwargs))
