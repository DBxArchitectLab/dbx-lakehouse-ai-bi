# AI-15: Product search and the eligibility engine

Search finds candidates; rules decide. Databricks AI Search (formerly Vector Search) finds gloves by meaning and by
exact catalog number; the eligibility engine checks every stated hard requirement against verified evidence and
answers verified_match, verified_mismatch or unknown (never a match). Fulfilment is a separate rule: full, partial,
backorder or an alternative date; a late glove is an alternative that needs the customer's acceptance.

| File | What |
| --- | --- |
| `src/quote_ai/rules/eligibility.py` | The engine: one named check per requirement (`CHECKS`, `stated`), `status_of`; reasons and the claims that did not count |
| `src/quote_ai/rules/fulfilment.py` | + `alternative_date` (`earliest_full_day`), a missing stock row is never on time, `summary` |
| `src/quote_ai/rules/uc.py` | The rules as JSON in, JSON out: what the Unity Catalog Python functions call |
| `src/quote_ai/rules/sql/check_eligibility.sql`, `check_fulfilment.sql` | `quote_ai_ai.check_eligibility(sku_id, requirements_json)` and `check_fulfilment(sku_id, lines_json, delivery_days)` |
| `src/quote_ai/search/docs.py`, `pipelines/search_doc.sql` | The search document, one row per sellable SKU; refreshed by MERGE (CDF on) |
| `src/quote_ai/search/index.py` | Endpoint `quote_ai_search`, Delta Sync index `sku_search_index`, TRIGGERED, embeddings from `quote_ai_embed_<target>` |
| `src/quote_ai/search/retriever.py` | Hybrid query with size and distribution pre-filters; candidates per product |
| `src/quote_ai/search/local.py` | An in-memory index with the same answer shape (tests, the local demo, the suites) |
| `src/quote_ai/search/search_tables.sql` | `dbxlab_range`, `range_evidence`, views `catalog_skus`, `eligibility_evidence`; `sku_search_doc` |
| `src/quote_ai/mock/range.py` | The wider range, Gloves G to R (demo data, catalog-only) |
| `src/quote_ai/services/shortlist.py` | `build_shortlist`: search, then the rules (`LocalRules` or `SqlRules`, the UC functions) |
| `src/quote_ai/services/quote_service.py` | + `shortlist_candidates`: the only path to SHORTLISTED from the engine |
| `src/quote_ai/app/backend/routers/shortlist.py` | `POST /api/shortlist/{quote_id}`: Find products |
| `src/quote_ai/app/frontend/src/pages/Shortlist.tsx` | Eligible / Needs evidence / Excluded, a chip per check, the fulfilment column |
| `src/quote_ai/jobs/search_job.py`, `resources/quote_ai/quote_ai_search_index.job.yml` | Nightly: setup, seed, doc, sync_index, functions, grants; volume `quote_ai_packages` |
| `resources/quote_ai/quote_ai_search_index.notes.md` | Why the endpoint and index are created by the job |
| `notebooks/quote_ai/15_search.py` | The demo's three searches and the function |
| `tests/quote_ai/test_eligibility.py`, `test_fulfilment.py`, `test_search.py`, `test_app_season4.py` | The Eligibility and Fulfilment suites' assertions, search, the route |
| `evaluation/quote_ai/v1/eligibility/`, `fulfilment/` | Eligibility cases (20 here, 60 after AI-17) and the fulfilment fixtures |

## Evidence the contract needed (changes to AI-08 and AI-09 code)

The requirement contract says material and powder-free come from the spec sheet and exam use from the FDA product
code. Before AI-15 those attributes existed only as AI-read (unverified) values, so no glove could be eligible.

- `product.evidence.gudid_rows`: a device's FDA product code gives `intended_use` (LZA, LYY, LYZ, LZC exam; KGO
  surgical), verified like the other structured GUDID fields; `pipelines/attributes.py` joins the codes from
  `quote_ai_ops.glove_devices`.
- `product.evidence.spec_rows`: a spec sheet's `Material` and `Powder-free` become evidence rows, verified only when
  the document passes AI-09's five checks. The synthetic spec sheets carry both lines; the AI-09 truth file
  (`evaluation/quote_ai/v1/product_facts/documents.jsonl`) was regenerated; the documents pipeline asks for two
  more labels.

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_mock_documents        # the evidence views, if not run since this change
databricks bundle run -t dev quote_ai_attributes            # intended_use from the product codes (full refresh)
databricks bundle run -t dev quote_ai_search_index
python -m pytest -q tests/quote_ai/test_eligibility.py
```

## What to verify in the workspace

- UC Python functions with `ENVIRONMENT (dependencies = [<wheel in a volume>])`, and SQL functions whose body reads
  tables through scalar subqueries; otherwise inline the rules call differently (decide before recording).
- The real Gloves A to F are bound to real GUDID devices (AI-03's demo binding): their latex labelling and product
  codes are whatever those devices say. The local demo uses synthetic devices with the episode's values.
- `glove_devices` column names used by `search_doc.sql`; `in_commercial_distribution` vs the silver name.
- `SqlEvidenceStore.snapshot` (DESCRIBE HISTORY through the warehouse) and the index privilege for the app.
- The demo's "8 candidates": locally they are A, B, C, D, E, G, H, N (G and H excluded for size, N for material
  and use, E for latex). In the workspace the ranking comes from the real embeddings.
