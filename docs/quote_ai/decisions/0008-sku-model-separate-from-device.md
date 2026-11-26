# 0008: A SKU model separate from device identifiers

- Status: accepted (written in AI-27, capturing the choice made in AI-03).
- Episode: AI-03.

## Context

A rep sells SKUs: a glove in one size, in a selling unit (a box of 100), at a price. GUDID describes devices: a
device identifier (DI) per package level, with a quantity per package. A GUDID "package of 10" can be a case of ten
boxes or ten gloves; guessing the unit from that number turns 400 boxes into 4,000 gloves or 40 cases.

## Options

- **(a)** Quote GUDID DIs directly.
- **(b)** A DBxLab SKU model of its own, mapped to GUDID package levels, each mapping verified by a person before the
  SKU can be offered. (Chosen.)

## Decision

(b). `quote_ai_product.dbxlab_sku` holds the selling unit and gloves per unit; `device_mapping.py` proposes a match
and `mapping_review.py` keeps the review queue (auto-match, verify, superseded). A SKU is offerable only with a
verified mapping (`Sku.mapping_verified`), and units always come from the mapping, never from a package quantity.

## Consequences

- One quote line per size, on a SKU, with its unit; pricing converts units from the verified packaging (AI-18).
- A new GUDID package level makes a mapping superseded and puts it back in the review queue.
- The Mapping suite is zero tolerance: `unverified_offered = 0`.

## Tests

`tests/quote_ai/test_device_mapping.py`, the mapping suite (`run_eval.py --suite mapping`), `test_pricing.py`
(units from packaging).
