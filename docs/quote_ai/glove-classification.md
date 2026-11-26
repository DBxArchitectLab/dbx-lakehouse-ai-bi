# Glove category classification

QuoteAI classifies structured GUDID evidence into a current glove-category snapshot before selecting daily
AccessGUDID history API requests. This is a source-category filter, not latex-free, clinical, commercial or
customer-specific eligibility. It does not recognize every glove and does not implement a commercial Silver or
Gold model. All raw file landing and file-source Bronze ingestion remain unchanged, including non-glove devices.
Only API candidate selection is narrowed.

## Current rules and evidence

The optional top-level `classification` block in [sources.yml](../../config/quote_ai/ingest/sources.yml) currently
uses version `glove-category-v1`, with three verified `product_code` inclusion rules:

| Rule | Code | FDA category | Evidence |
| --- | --- | --- | --- |
| `fda_lza` | `LZA` | Polymer patient examination glove | [FDA classification API](https://api.fda.gov/device/classification.json?search=product_code:LZA) |
| `fda_lzc` | `LZC` | Medical glove, specialty | [FDA classification API](https://api.fda.gov/device/classification.json?search=product_code:LZC) |
| `fda_lyy` | `LYY` | Latex patient examination glove | [FDA classification API](https://api.fda.gov/device/classification.json?search=product_code:LYY) |

These codes were cross-checked against the live FDA API on 2026-11-18 using
`https://api.fda.gov/device/classification.json?search=product_code:CODE`. The checked-in `verified`, `reviewed_by`
and `evidence_url` fields record that automated source lookup, not human QA, a human approval or FDA approval of
QuoteAI's decisions. Refreshing the snapshot uses the configured rules; it does not perform those FDA lookups again.

This list is deliberately not exhaustive. `LYY` is intentionally included: a latex glove is still a glove, even
when a later latex-free requirement would make it ineligible. Category inclusion is not evidence of latex-free
material, chemotherapy testing, sterility, current availability or suitability for a customer.

Rules match exact structured `product_code`, `gmdn_code` or `gmdn_term` values, with code case/whitespace
normalization and case-insensitive term matching. Only supported device fields, FDA product-code containers,
GMDN containers and primary-identifier paths are inspected. Unrelated nested objects, descriptions, historical
records and arbitrary embedded strings cannot supply evidence. The current three rules use product codes only;
GMDN evidence is retained for inspection.

| Evidence at the selected current row | Decision |
| --- | --- |
| Verified inclusion match, without a verified exclusion match | `included`, category `glove` |
| Explicit verified, reviewed `decision: excluded` match, without a verified inclusion match | `excluded` |
| Conflicting verified inclusion and exclusion matches | `unknown`, reason `conflict` |
| Pending matches only (`verified: false`) | `unknown`, reason `pending` |
| No match, or no usable category evidence | `unknown`, reason `no_match` or `missing` |

Pending rules never authorize inclusion; their matches remain audit evidence only. Unmatched codes are unknown,
not implicitly excluded. A future explicit reviewed exclusion rule is supported, but none is configured now.
Verified rules require a nonblank `reviewed_by`; refresh requires at least one verified rule.

## Inputs and current-row selection

[classification.py](../../src/quote_ai/ingest/classification.py) reads the configured full and daily sources:

- Full `device_raw` joins `product_codes_raw` and `gmdn_terms_raw` on `primary_di` **and the same `_release_date`**.
  Evidence from another release is not borrowed. If the full device table exists, both evidence tables are required.
- Daily `daily_device_raw` is traversed through nested structured XML-derived keys, including identifiers,
  product codes and GMDN values. A daily-only bootstrap is possible when no full input tables exist.
- Current rows are selected across both inputs by release date, then numeric public version, then daily over
  full on a tie, checking both DI and public record key where present. Contradictory equal-priority snapshots
  fail refresh. A newer daily `unknown` supersedes an older full `included`; there is no stale-evidence fallback.

Public version/status, publication and commercial-distribution metadata are retained when present. Distribution
status and end dates are source metadata, not suitability decisions and not additional inclusion predicates.

## Published objects and audit

The logical ops schema is `quote_ai_ops`. Catalog and logical-to-physical schema mappings are resolved through
`Names`, including the ops mapping supplied by `--schemas`; neither output is hard-coded to a DEV schema.
The current [DEV target](../../databricks.yml) and [schema resources](../../resources/quote_ai/quote_ai_ingest.schemas.yml)
resolve to:

| Object | Purpose |
| --- | --- |
| `deepwater_tst.quote_ai_ops.glove_classification` | Delta current snapshot of included, excluded and unknown rows |
| `deepwater_tst.quote_ai_ops.glove_devices` | View selecting only `decision = 'included'` from that snapshot |

Rows retain identities, codes/terms, decision, category, reason, matched rule evidence, source name/kind,
`source_release`, `source_load_id`, `classification_version` and `classification_hash`. Evidence includes each
matched rule's decision, verification status, reviewer metadata and URL.

Each refresh overwrites the Delta snapshot with schema overwrite enabled, then creates or replaces the view.
It resets the prior classification contents; this is not an append-only classification history. Preserve the
version and exact rule hash alongside [ingestion config history](data-sources.md#where-it-lands-and-the-audit-trail)
(`ingestion_config`, linked to runs by `config_hash`) when auditing a decision. The classification SHA-256 binds
the exact classification configuration, including each rule's `decision`, and is distinct from the ingestion
config hash. A version label alone does not establish that the rules are identical.

## Daily API gate

With classification configured, the generated daily job runs:

```text
setup -> land -> files -> record -> classify_gloves -> api_gudid_api_device_history -> checks
```

Only the selected `classification.api_source` is filtered (currently `gudid_api_device_history`); other API
sources keep their generic behavior. The selected source must use `ids_from.param: record_key`.
[engine.py](../../src/quote_ai/ingest/engine.py) checks for the snapshot and rejects null or stale hashes before
selecting requests. A missing snapshot or stale hash fails closed; neither falls back to unfiltered requests.

For each candidate from `daily_device_raw`, the SQL gate requires an `included` snapshot row with the same
public record key, `_load_id`, `_release_date`, public version (null-safe string comparison) and exact current
classification hash. This predicate runs **before** the 2,000-identifier batch cap. Missing exact evidence does not
authorize a request. Full-release rows can populate the category snapshot, but do not become bulk API requests:
API enrichment still starts only from the configured daily keys, never every row of the full release.
A newer daily release or higher numeric version for the same record key also blocks an older candidate,
even if an API-only retry sees an included snapshot from before the newer input arrived. Refresh classification
to authorize the newer version; the old match is never used as fallback.

The filtered path disables the successful-run release watermark so newly included daily rows can be backfilled
after a rule change. The completion cache still skips a key when an API target row exists at the candidate's
release or later. Refreshing rules therefore does not force re-fetching already cached keys. Known caveat:
an API response yielding no target rows leaves no row-based completion marker and may be requested again.

Omitting `classification` or setting it to YAML `null` (`None` in Python) is an explicit opt-out policy choice:
the generated classification task is omitted and the generic, unfiltered API path and watermark behavior return.
**Do not disable classification as a recovery workaround:** all otherwise eligible daily candidates can then
reach the API, not just included gloves. The standalone `classify` command rejects disabled classification.

## Deploy and refresh

Run from the `quote-ai` bundle directory with the configured Databricks authentication:

```bash
python scripts/quote_ai/gen_ingest_resources.py
databricks bundle validate -t dev
databricks bundle plan -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_ingest_daily
```

Deployment rebuilds the wheel through the bundle artifact build (`python -m pip wheel . --no-deps --wheel-dir dist`);
job environments consume `dist/*.whl`. Deploy the updated wheel, configuration and generated daily job together.
Rerun the **whole daily job**, including setup/config-history recording and `classify_gloves`, after changing rules
or inputs. An API-only rerun does not refresh classification and can fail on stale rules or skip unmatched rows.

The full device pattern is now exactly `device.txt`; it no longer overlaps `deviceSizes.txt`. This prevents future
cross-entity reads but does not repair an existing `device_raw` target or its Auto Loader checkpoint. If the older
`device*.txt` pattern was previously deployed, inspect the target and plan a reviewed replay/full refresh from retained
landing files if needed. Do not delete source files or production tables as an automatic rollout step.

After loading a full release with `quote_ai_ingest_once`, optionally refresh the snapshot manually before the next
daily job. The once job does not add a classification task. On a Spark-enabled Databricks runtime, from the deployed
bundle's files directory, invoke the existing entry point with the config, catalog and schema mapping:

```bash
python src/quote_ai/jobs/ingest_job.py classify \
  --config config/quote_ai/ingest/sources.yml \
  --catalog deepwater_tst \
  --schemas quote_ai_gudid_bronze=quote_ai_gudid_bronze,quote_ai_sales_bronze=quote_ai_sales_bronze,quote_ai_ops=quote_ai_ops
```

`--config` and `--catalog` are parser-required; `--schemas` is necessary when physical names differ from logical
names and should be passed explicitly as above. Use the same resource-resolved values as the deployed job for
other mappings. This is a Spark refresh, not a local no-cluster command, and it does not itself run API enrichment
or record a new setup/config-history entry. Prefer the whole daily job for a rule rollout.

## Inspect results

These queries use the current actual DEV names, not `dev_quote_ai_ops`. Substitute resolved names if deployment
mappings change.

```sql
SELECT decision, reason, classification_version, classification_hash, count(*) AS devices
FROM deepwater_tst.quote_ai_ops.glove_classification
GROUP BY decision, reason, classification_version, classification_hash
ORDER BY decision, reason;

SELECT decision, reason, count(*) AS devices
FROM deepwater_tst.quote_ai_ops.glove_classification
WHERE decision IN ('unknown', 'excluded')
GROUP BY decision, reason
ORDER BY decision, reason;

SELECT primary_di, public_device_record_key, product_codes, gmdn_codes, gmdn_terms,
       evidence, source_release, source_load_id
FROM deepwater_tst.quote_ai_ops.glove_classification
WHERE decision = 'unknown'
LIMIT 100;

SELECT count(*) AS included_devices
FROM deepwater_tst.quote_ai_ops.glove_devices;
```

## Current limitations

- Coverage is limited to the configured reviewed rules. Unknown counts are expected, not proof that the remaining
  devices are non-gloves. No automatic human approval or clinical eligibility follows from inclusion.
- Missing required input columns, incomplete full inputs, invalid release dates, missing required identities or
  ambiguous identity/metadata values fail refresh. There is no keyword fallback or silent repair.
- The real daily XML schema and its nested representation in the deployed Spark runtime are not yet certified
  end to end. Validate those runtime inputs before treating a successful local check as production evidence.
- Snapshot overwrite replaces earlier decisions; config history and retained version/hash provenance are needed
  for audit. This feature adds only the ops category snapshot/view and API gate, not commercial Silver/Gold tables.