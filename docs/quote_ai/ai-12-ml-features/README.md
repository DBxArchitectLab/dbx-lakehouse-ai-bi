# AI-12: From quote history to ML features

Trustworthy inputs for win prediction: a label that matches the business metric, feature tables in Unity Catalog with
point-in-time lookups, a training set split by time, and tests that refuse a leak before a model ever sees it.

**An educational simulation.** DBxLab's quote history is synthetic demo data. The outcomes are generated from the
planted signal below, so Season 3 teaches the method and can check whether a model finds the signal. It cannot prove
that win prediction works for a real sales team.

## The planted signal (written down before any model, checked in AI-13)

```
p(win) = sigmoid( 1.0
                  - 8    * (unit_price / customer_current_price - 1)     price against what the customer pays today
                  - 0.12 * max(lead_time_days - 10, 0)                   each day later than 10 days
                  - 0.9  * (0 if in_stock else 1) )                       out of stock
```

`quote_ai.mock.catalog.SIGNAL` and `win_probability`. Won and lost quotes are drawn from it (4,000 quotes over two
years, October 2024 to September 2026). The 70 expired quotes (spread evenly over the two years) get a context drawn
from the same ranges, kept only where the signal says "lost": an expired quote is a silent loss. Nothing else (the
account, the rep, the glove, the quantity, the discount band) moves the outcome. A model that finds price against the
customer's current price, lead time and stock as its top drivers has found the signal.

## The label

`won = 1` for WON, `0` for LOST or EXPIRED; CANCELLED and open quotes are left out. The same definition as the
win-rate metric ([decision 0003](../decisions/0003-win-rate-definition.md)); a test reads the metric view file and
fails if they drift apart. Source: `quote_ai_sales.quote_fact` (AI-10). Win rate on the history: 66%.

## The features, all known when the quote was sent

| Group | Feature | From | Table |
| --- | --- | --- | --- |
| Price | `price_vs_list_pct` | the quote and the price book at sent_at | `quote_context_features` |
| | `price_vs_customer_current_pct` | the proposed price and the customer's price at sent_at | on-demand function |
| Delivery | `lead_time_days`, `stock_cover_days` | the inventory snapshot at sent_at | `sku_features` |
| Account | `orders_12m`, `win_rate_12m`, `days_since_last_order` | orders and decided quotes before sent_at | `account_features` |
| Product | `material`, `use_type`, `chemo_verified` | the SKU model; verified chemo evidence at sent_at | `sku_features` |
| Context | `quantity_boxes`, `needs_approval` | the quote as sent (`request_features`) | `quote_context_features` |
| **Excluded** | `final_negotiated_price`, `days_to_decision` | known only after the outcome | never |

Point in time, and two subtleties the tests pin down: `win_rate_12m` counts quotes *decided* before as_of (a quote
sent earlier but decided later is not known yet), and an order counts from the day after its date.
`chemo_verified` uses `verified_at`, so on the historical quotes it is false: the reports were verified in AI-09,
after those quotes. That is correct, not a bug.

`price_vs_customer_current_pct` is a Unity Catalog Python function (`feature_tables.sql`), a `FeatureFunction` in
the training set, computed from the proposed price and the looked-up current price, in training and in serving
alike. `request_features()` builds the quote's own values for a past quote, for nightly scoring and for a what-if
price: one definition, no training-serving skew.

## The split

| Split | sent_at | Quotes |
| --- | --- | --- |
| train | up to 2026-04-30 | 3,216 |
| valid | 2026-05-01 to 2026-06-30 | 340 |
| test | 2026-07-01 to 2026-09-30 | 512 |

## Files

| File | What |
| --- | --- |
| `src/quote_ai/mock/generate_dbxlab.py` | The history with sent_at and the outcome, customer prices and inventory over time; the drift month (AI-14) |
| `src/quote_ai/ml/features.py` | Label, feature rows, point-in-time lookups, training set, split, the leak checks; Feature Engineering adapters |
| `src/quote_ai/ml/feature_tables.sql` | History tables, labels, the three feature tables (TIMESERIES keys), the function, win_training_set |
| `src/quote_ai/ml/quick.py` | A dependency-free logistic regression for the leak demo |
| `src/quote_ai/jobs/ml_job.py` | setup, seed, features, training-set (and AI-13/14's commands) |
| `resources/quote_ai/quote_ai_ml_features.job.yml` | `quote_ai_ml_features`, nightly |
| `notebooks/quote_ai/12_ml_features.py` | The demo: Lakeside's history, the label, the leak, point in time |
| `tests/quote_ai/test_features.py` | Point in time, leakage, the label, the split, the leak demo |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run quote_ai_semantic -t dev        # quote_fact and order_line, if not there yet (AI-10)
databricks bundle run quote_ai_ml_features -t dev
```

## How we know it's correct

- Passes: Lakeside's quote `QH-02875`, sent 2026-03-10: `win_rate_12m` looked up as of that moment, price and stock
  from the snapshot an hour before, no final price, no days to decision; it sits in train.
- Fails: the same quote by a plain join on account_id (today's `win_rate_12m`) plus `final_negotiated_price`.
  `check_training_set` refuses it; on a random split it scores AUC near 1.0, on a time split still near 1.0.
- The job also cross-checks Feature Engineering's training set against the pure builder (`features.compare`) and
  fails on any difference.

## Design notes

- The script calls the source "the quote history"; it is `quote_ai_sales.quote_history`, which AI-03 created with
  fewer columns. AI-12 replaces it (the setup step drops the old shape; the product job no longer seeds it).
- The history was extended from 600 quotes over 20 months to 4,000 over two years (the script's "two years"), and the
  expired quotes spread over the whole period. With 600 quotes the test months held 59 quotes and the label had a
  step (no expired quote after February 2026), so AUC and calibration could not be measured against AI-13's 0.05
  thresholds. AI-10's dashboards and AI-11's Genie answers move with it (their tests compute answers from the data).
- The customer's current price is observed when each quote is prepared, an hour before it is sent (and today for each
  account; Lakeside pays $8.00). The draft calls the source "the customer's last paid price".
- The draft's sketch puts `price_vs_customer_current_pct` in a feature table; it depends on the proposed price, so it
  is an on-demand function.

## Verify before recording

- Feature Engineering in Unity Catalog: `FeatureEngineeringClient`, `write_table(mode="merge")` on a table whose
  primary key has a `TIMESERIES` column, `FeatureLookup(timestamp_lookup_key=...)`, `FeatureFunction`,
  `create_training_set(exclude_columns=...)`; whether point-in-time lookups need a runtime version or work on
  serverless job compute.
- `CREATE FUNCTION ... LANGUAGE PYTHON` in the quote_ai_ml schema, run as sp-dbx-lakehouse.
- On screen: the quote history is synthetic demo data; win prediction is an educational simulation with a planted
  signal.
