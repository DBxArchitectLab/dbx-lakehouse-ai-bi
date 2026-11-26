# AI-07: QuoteAI v1, the first app

A rep pastes a request, answers what the extraction flagged as missing, confirms the requirements, and gets a first
price by simple rules, clearly labelled. Every change goes through `QuoteService`; every allowed move writes one
`quote_event` row, and a refused move writes none.

| File | What |
| --- | --- |
| `src/quote_ai/rules/quote_states.py` | The states and the ALLOWED table; `transition()` refuses anything else |
| `src/quote_ai/services/quote_service.py` | The one door: `read_request`, `confirm`, `quick_quote`, `change_state`, `mine`, all through `_move` |
| `src/quote_ai/rules/simple_rules.py` | v1 quick quote: exact match on the SKU model, list price x units (replaced in AI-15/16/18/21) |
| `src/quote_ai/rules/sql/quote_tables.sql` | `quote_ai_sales.quote`, `quote_line`, `quote_event` |
| `src/quote_ai/storage/delta_repository.py` | QuoteRepository on Delta through the app's warehouse; optimistic locking by revision |
| `src/quote_ai/storage/product_catalog.py` | Offerable SKUs, the price book, lookup by catalog number |
| `src/quote_ai/app/backend/` | FastAPI: `main.py`, `routers/quotes.py`, `routers/products.py`, `auth.py`, `context.py` |
| `src/quote_ai/app/frontend/src/pages/` | NewRequest, ConfirmRequirements, MyQuotes, ProductLookup (React + Vite) |
| `src/quote_ai/app/app.yaml` | `uvicorn backend.main:app`; every value from an app resource |
| `resources/quote_ai/quote_ai_app.app.yml` | The app: warehouse CAN_USE, extract model service EXECUTE, reps CAN_USE |
| `resources/quote_ai/quote_ai_app_grants.job.yml` | The app's table grants (SELECT on the model, MODIFY on the quote tables) |

## The API

| Route | Move |
| --- | --- |
| `POST /api/quotes` | read the request (extraction through `quote_ai_extract`) -> DRAFT |
| `POST /api/quotes/{id}/confirm` | quantity per size, unit, gloves per unit -> REQUIREMENTS_CONFIRMED (r2, one line per size) |
| `POST /api/quotes/{id}/quick-quote` | v1 simple rules -> SHORTLISTED -> PRICED |
| `POST /api/quotes/{id}/state` | a move by state alone: the ALLOWED table decides; `REQUIREMENTS_CONFIRMED -> ISSUED` is 409 |
| `GET /api/quotes`, `GET /api/quotes/{id}` | my quotes with their events |
| `GET /api/products/lookup?catalog_number=` | the DBxLab SKU and unit, and the GUDID record |

Errors: a move the table refuses, or a stale revision, is 409 with the reason; a rule that refuses (lines that don't
add up to the request) is 422; an extraction outside the schema is 502. Every write carries the revision the screen
showed.

## Deploy and run

```
python -m pytest -q tests/quote_ai                         # states, QuoteService, Delta repository, the API
python scripts/quote_ai/build_app.py                       # React build + vendored quote_ai package (needs Node 20)
databricks bundle validate -t dev
databricks bundle deploy -t dev --var quote_ai_warehouse_id=<warehouse id>
databricks bundle run -t dev quote_ai_product_model        # tables, including quote, quote_line, quote_event
databricks bundle run -t dev quote_ai_app_grants           # the app's table grants
databricks bundle run -t dev quote_ai_app                  # starts the app
```

Local, without a workspace (synthetic catalog, in-memory quotes; the extraction still needs a model endpoint):

```
cd src/quote_ai/app
QUOTE_AI_DEMO=1 QUOTE_AI_LOCAL_USER=rep.alice QUOTE_AI_PROMPT_URI=... uvicorn backend.main:app --port 8000
npm --prefix frontend run dev
```

## v1, honestly labelled

The quick quote matches exactly on the SKU model (material, use, powder-free, latex-free as DBxLab sells them, and a
sellable SKU per size with a verified mapping and the confirmed glove count), then prices list x units. It shows
"v1: simple rules, demo data"; chemo testing, sterility and delivery show "not checked in v1", never a pass. v1 stops
at PRICED: no approval, no Issue button. With the tracer gloves, A to D match (E is not latex-free; F is a box of 200),
and Glove A is chosen at $8.60 list: 400 boxes, $3,440.

## Verify before recording

- Apps bundle keys (`uc_securable` with `MODEL_SERVICE` and `VOLUME`, `experiment`) and `valueFrom` for them; the
  `${resources.apps.quote_ai_app.service_principal_client_id}` reference in the grants job.
- Headers the app receives for the signed-in user (`X-Forwarded-Email`); on-behalf-of-user status (AI-24).
- The app's port (8000 here) and serving `frontend/dist` from FastAPI.
- `databricks-sql-connector` named parameters and `num_affected_rows` from UPDATE; VARIANT for the JSON columns.
- MLflow tracing inside an app: the experiment resource, `mlflow.start_span`, the trace ID on the active span.
- Delta writes the quote row, its lines and its event as three statements: Lakebase makes them one transaction in
  AI-20.
