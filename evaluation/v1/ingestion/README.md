# Ingestion suite (v1)

How we know ingestion is correct (AI-02).

| Case | Where it runs | Passes when |
| --- | --- | --- |
| Every bad config in `bad_configs/` is refused, and the first line of each file names the expected error | CI (`tests/quote_ai/test_ingest_config.py`) | Each file fails the config schema with that error |
| The repo's config is valid, and the generated bundle resources match it | CI (`test_ingest_config.py`, `test_ingest_engine.py`) | No error; no stale resource file |
| Replay, throttling, the run log | CI (`test_ingest_landing.py`, `test_ingest_api.py`, `test_ingest_engine.py`) | The same release landed twice is recorded once; a republished file is refused; 429 and 5xx are retried, then fail with a clear error; failed runs are logged as failed |
| `one_row_per_key` | Workspace, the `checks` task of every ingestion job (`quote_ai.ingest.checks`) | 0 duplicate keys in every bronze table, after any replay |
| `load_id_present` | Workspace, `checks` task | 0 rows without `_load_id` |
| `load_id_logged` | Workspace, `checks` task | 0 load IDs without a succeeded (or partial) run in `ops.ingestion_log` |

**Adding a bad config case:** put a YAML file in `bad_configs/`. Its first line is `# expect: <text of the
error>`.
