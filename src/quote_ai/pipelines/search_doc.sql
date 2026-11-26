-- The SKU search document (AI-15): refresh {ai}.sku_search_doc, one row per sellable SKU, by MERGE, so the Delta
-- Sync index re-embeds only what changed (change data feed is on). Run by src/quote_ai/jobs/search_job.py doc,
-- nightly after the evidence; then the index syncs. The search text is the one quote_ai.search.docs builds:
-- concat_ws(' | ', product_name, brand_name, device_description, attribute_summary).
-- Verify before recording: the glove_devices column names (brand_name, device_description, in_commercial_distribution).

CREATE OR REPLACE TEMPORARY VIEW search_doc_evidence
COMMENT 'Per SKU: the evidence as search words (every status; superseded left out)'
AS
SELECT sku_id,
       concat_ws(', ', sort_array(collect_set(CASE
         WHEN attribute_name IN ('material', 'intended_use') THEN lower(attribute_value)
         WHEN attribute_name = 'latex_free' AND attribute_value = 'true' THEN 'latex-free'
         WHEN attribute_name = 'latex_free' AND attribute_value = 'false' THEN 'contains latex'
         WHEN attribute_name = 'powder_free' AND attribute_value = 'true' THEN 'powder-free'
         WHEN attribute_name = 'powder_free' AND attribute_value = 'false' THEN 'powdered'
         WHEN attribute_name = 'chemo_tested' AND attribute_value = 'true' THEN 'chemo-tested'
         WHEN attribute_name = 'sterile' AND attribute_value = 'true' THEN 'sterile'
         WHEN attribute_name = 'sterile' AND attribute_value = 'false' THEN 'non-sterile'
         WHEN attribute_name = 'in_commercial_distribution' AND attribute_value = 'false' THEN 'discontinued'
       END))) AS attribute_summary
FROM {product}.eligibility_evidence
WHERE verification_status <> 'superseded'
GROUP BY sku_id;

CREATE OR REPLACE TEMPORARY VIEW search_doc_source
COMMENT 'The search document as it should be now: DBxLab SKUs with a verified mapping, and the wider range'
AS
SELECT s.sku_id, s.product_id, s.catalog_number, s.size, p.material AS material_family,
       coalesce(d.in_commercial_distribution, false) AS in_commercial_distribution,
       concat_ws(' | ', p.name, coalesce(d.brand_name, ''), coalesce(d.device_description, ''),
                 coalesce(e.attribute_summary, '')) AS search_text
FROM {product}.dbxlab_sku s
JOIN {product}.dbxlab_product p ON p.product_id = s.product_id
LEFT JOIN {gudid_gold}.glove_devices d ON d.primary_di = s.supplier_di
LEFT JOIN search_doc_evidence e ON e.sku_id = s.sku_id
WHERE s.sellable
  AND EXISTS (SELECT 1 FROM {product}.product_device_mapping m
              WHERE m.sku_id = s.sku_id AND m.verification_status = 'verified')
UNION ALL
SELECT r.sku_id, r.product_id, r.catalog_number, r.size, r.material,
       NOT EXISTS (SELECT 1 FROM {product}.range_evidence x WHERE x.sku_id = r.sku_id
                   AND x.attribute_name = 'in_commercial_distribution' AND x.attribute_value = 'false'),
       concat_ws(' | ', r.product_name, 'DBxLab demo', r.device_description, coalesce(e.attribute_summary, ''))
FROM {product}.dbxlab_range r
LEFT JOIN search_doc_evidence e ON e.sku_id = r.sku_id
WHERE r.sellable;

MERGE INTO {ai}.sku_search_doc t
USING search_doc_source s ON t.sku_id = s.sku_id
WHEN MATCHED AND (t.search_text <> s.search_text OR t.in_commercial_distribution <> s.in_commercial_distribution
                  OR t.size <> s.size OR t.catalog_number <> s.catalog_number) THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE THEN DELETE;
