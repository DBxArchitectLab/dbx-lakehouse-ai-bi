"""Databricks AI Search (formerly Vector Search; the client and APIs keep the vector_search names): the endpoint and
the Delta Sync indexes, created and synced by jobs. Needs databricks-vectorsearch; imported lazily.

  sku_search_index    over {ai}.sku_search_doc (AI-15): embedding source search_text, primary key sku_id
  knowledge_index     over {ai}.knowledge_chunks (AI-16): embedding source text, primary key chunk_id

Both are TRIGGERED: synced after the nightly refresh, never continuous, so embedding cost is paid per change, not per
query. The embedding model is the Unity Gateway model service quote_ai_embed_<target> (quote_ai.llm.endpoints.EMBED).

AI Search endpoints and indexes are not declared as bundle resources here: the job creates them if they don't exist
(resources/quote_ai/quote_ai_search_index.notes.md). Verify before recording: VectorSearchClient.create_endpoint /
create_delta_sync_index / get_index / index.sync(), pipeline_type "TRIGGERED", embedding_model_endpoint_name taking
a Unity Gateway model service name, and whether indexes have become bundle resources.

  python -m quote_ai.search.index --catalog dbxarchitectlab_dev --schemas quote_ai_ai=quote_ai_ai --what sku
"""
from __future__ import annotations

import argparse

ENDPOINT = "quote_ai_search"
SKU = {"index": "sku_search_index", "source": "sku_search_doc", "primary_key": "sku_id", "text": "search_text"}
KNOWLEDGE = {"index": "knowledge_index", "source": "knowledge_chunks", "primary_key": "chunk_id", "text": "text"}
INDEXES = {"sku": SKU, "knowledge": KNOWLEDGE}


def full_names(ai_schema: str, what: str) -> tuple[str, str]:
    """(index, source table) full names; ai_schema is catalog.schema of quote_ai_ai."""
    spec = INDEXES[what]
    return f"{ai_schema}.{spec['index']}", f"{ai_schema}.{spec['source']}"


def client():
    from databricks.vector_search.client import VectorSearchClient
    return VectorSearchClient(disable_notice=True)


def ensure_endpoint(vsc, endpoint: str = ENDPOINT) -> None:
    try:
        vsc.get_endpoint(endpoint)
    except Exception:  # noqa: BLE001  not found
        vsc.create_endpoint(name=endpoint, endpoint_type="STANDARD")


def ensure_index(ai_schema: str, what: str, embedding_endpoint: str, endpoint: str = ENDPOINT, vsc=None):
    """The Delta Sync index on the source table, created once; afterwards the same index is returned."""
    vsc = vsc or client()
    ensure_endpoint(vsc, endpoint)
    index_name, source = full_names(ai_schema, what)
    spec = INDEXES[what]
    try:
        return vsc.get_index(endpoint_name=endpoint, index_name=index_name)
    except Exception:  # noqa: BLE001  not created yet
        return vsc.create_delta_sync_index(
            endpoint_name=endpoint, index_name=index_name, source_table_name=source,
            primary_key=spec["primary_key"], pipeline_type="TRIGGERED",
            embedding_source_column=spec["text"], embedding_model_endpoint_name=embedding_endpoint)


def sync(ai_schema: str, what: str, embedding_endpoint: str, endpoint: str = ENDPOINT) -> str:
    index = ensure_index(ai_schema, what, embedding_endpoint, endpoint)
    index.sync()
    return full_names(ai_schema, what)[0]


def get_index(ai_schema: str, what: str, endpoint: str = ENDPOINT):
    """The index to query (the app and the notebooks)."""
    return client().get_index(endpoint_name=endpoint, index_name=full_names(ai_schema, what)[0])


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--what", choices=sorted(INDEXES), default="sku")
    p.add_argument("--endpoint", default=ENDPOINT)
    p.add_argument("--embedding-endpoint", help="default: quote_ai.llm.endpoints.EMBED")
    a = p.parse_args(argv)
    from quote_ai.llm import endpoints
    from quote_ai.storage import sql_files
    names = sql_files.names_for(a.catalog, dict(x.split("=", 1) for x in a.schemas.split(",") if x))
    print("synced", sync(names["ai"], a.what, a.embedding_endpoint or endpoints.EMBED, a.endpoint))


if __name__ == "__main__":
    main()
