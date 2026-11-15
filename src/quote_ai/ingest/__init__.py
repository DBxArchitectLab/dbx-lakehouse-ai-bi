"""QuoteAI ingestion (AI-02): a config-driven framework. config/quote_ai/ingest/sources.yml says what to load; this
package
knows how. Plain-Python modules (config, lineage, http, gudid_client, download, log, engine, checks, bundle_gen) are
tested without Spark; spark_io, api_source and bronze_pipeline run on Databricks.
"""
