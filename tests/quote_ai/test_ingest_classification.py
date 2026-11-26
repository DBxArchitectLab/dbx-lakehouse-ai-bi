"""Exact category evidence and fail-closed publication, without requiring Spark."""
import json
import os
import sys
from copy import deepcopy
from itertools import permutations
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quote_ai.ingest.classification import (
    CLASSIFICATION_TABLE,
    INCLUDED_VIEW,
    classify_record,
    refresh_classification,
    select_latest,
    transform_dataframes,
)
from quote_ai.ingest.config import GloveClassification, load_config
from quote_ai.ingest.engine import Names


@pytest.fixture
def rules():
    return GloveClassification(version="gloves-1", rules=[
        {"rule_id": "exam", "field": "product_code", "value": "LZA", "decision": "included",
         "verified": True, "reviewed_by": "reviewer", "evidence_url": "https://example.org/exam"},
        {"rule_id": "not_glove", "field": "gmdn_code", "value": "999", "decision": "excluded",
         "verified": True, "reviewed_by": "reviewer", "evidence_url": "https://example.org/other"},
        {"rule_id": "term", "field": "gmdn_term", "value": "Surgical glove", "decision": "included",
         "verified": True, "reviewed_by": "reviewer", "evidence_url": "https://example.org/term"},
        {"rule_id": "pending", "field": "product_code", "value": "XYZ", "decision": "excluded",
         "evidence_url": "https://example.org/pending"},
    ])


@pytest.fixture
def daily_payload():
    return {
        "publicDeviceRecordKey": "rk-1", "publicVersionNumber": "2", "publicVersionStatus": "PUBLISHED",
        "identifiers": {"identifier": [
            {"deviceId": "package-di", "deviceIdType": "Package"},
            {"deviceId": "00123", "deviceIdType": "Primary"},
        ]},
        "fdaProductCodes": {"fdaProductCode": [{"productCode": " lza "}, {"productCode": "XYZ"}]},
        "gmdnTerms": {"gmdn": [{"gmdnCode": "100", "gmdnPTName": " surgical GLOVE "}]},
        "deviceCommDistributionStatus": "Not in Commercial Distribution",
        "deviceCommDistributionEndDate": "2026-11-17",
        "_release_date": "2026-11-18", "_load_id": "daily:20261118",
    }


@pytest.mark.parametrize("payload,decision,reason", [
    ({"product_code": " lZa "}, "included", "matched"),
    ({"gmdnCode": 999}, "excluded", "matched"),
    ({"gmdnPTName": " SURGICAL Glove "}, "included", "matched"),
    ({"gmdnPTName": "Surgical glove, powdered"}, "unknown", "no_match"),
    ({"product_code": "LZA EXTRA"}, "unknown", "no_match"),
    ({"product_code": "ABC"}, "unknown", "no_match"),
    ({"description": "LZA surgical glove", "brandName": "glove"}, "unknown", "missing"),
    ({}, "unknown", "missing"),
    ({"productCode": "XYZ"}, "unknown", "pending"),
    ({"productCode": "LZA", "gmdnCode": "999"}, "unknown", "conflict"),
    ({"productCode": ["LZA", "XYZ"]}, "included", "matched"),
])
def test_classify_record_exact_decisions(payload, decision, reason, rules):
    result = classify_record(payload, rules)
    assert (result["decision"], result["reason"]) == (decision, reason)
    assert result["category"] == ("glove" if decision == "included" else None)


def test_daily_nested_evidence_and_metadata(daily_payload, rules):
    before = deepcopy(daily_payload)
    result = classify_record(json.dumps(daily_payload), rules)
    assert daily_payload == before
    assert result["primary_di"] == "00123"
    assert result["public_device_record_key"] == "rk-1"
    assert result["product_codes"] == ["LZA", "XYZ"]
    assert result["gmdn_codes"] == ["100"]
    assert result["gmdn_terms"] == ["surgical glove"]
    assert result["source_release"] == "2026-11-18"
    assert result["source_load_id"] == "daily:20261118"
    assert result["device_comm_distribution_status"] == "Not in Commercial Distribution"
    assert result["device_comm_distribution_end_date"] == "2026-11-17"
    assert result["decision"] == "included"
    assert result["evidence"][0] == {
        "rule_id": "exam", "field": "product_code", "value": "LZA", "decision": "included",
        "evidence_url": "https://example.org/exam", "verified": True, "status": "verified", "reviewed_by": "reviewer",
    }
    assert result["evidence"][1]["status"] == "pending"
    assert result["classification_version"] == "gloves-1"
    assert len(result["classification_hash"]) == 64


def test_full_join_shape_and_xml_scalar_values(rules):
    result = classify_record({"device": {"PrimaryDI": "00123", "public_device_record_key": "rk-1"},
                              "product_code": ["LZA", "LZA"], "gmdn_pt_name": [{"_VALUE": "Surgical glove"}]}, rules)
    assert result["decision"] == "included"
    assert result["product_codes"] == ["LZA"]
    assert result["gmdn_terms"] == ["surgical glove"]
    assert result["primary_di"] == "00123"


@pytest.mark.parametrize("container", [
    "unrelated", "history", "relatedDevices", "vendorDocuments", "_rescued_data", "_metadata",
])
@pytest.mark.parametrize("as_array", [False, True])
def test_classify_record_ignores_unrelated_nested_evidence_and_metadata(container, as_array, rules):
    unrelated = {
        "productCode": "LZA", "gmdnCode": "999", "gmdnPTName": "Surgical glove",
        "primaryDI": "unrelated-di", "publicDeviceRecordKey": "unrelated-key",
        "publicVersionNumber": "99", "deviceCommDistributionStatus": "unrelated-status",
        "identifiers": {"identifier": {"deviceIdType": "Primary", "deviceId": "another-di"}},
        "device": {"productCode": "LZA"},
    }
    payload = {
        "primaryDI": "current-di", "publicDeviceRecordKey": "current-key", "publicVersionNumber": "2",
        "deviceCommDistributionStatus": "current-status", "product_code": "OTHER",
        container: [unrelated] if as_array else unrelated,
    }
    result = classify_record(payload, rules)
    assert (result["decision"], result["reason"]) == ("unknown", "no_match")
    assert result["product_codes"] == ["OTHER"]
    assert result["gmdn_codes"] == result["gmdn_terms"] == result["evidence"] == []
    assert result["primary_di"] == "current-di"
    assert result["public_device_record_key"] == "current-key"
    assert result["public_version_number"] == "2"
    assert result["device_comm_distribution_status"] == "current-status"
    assert result["validation_error"] is None
    assert classify_record({container: unrelated}, rules)["reason"] == "missing"


@pytest.mark.parametrize("wrappers", [(), ("device",), ("gudid",), ("gudid", "device")])
@pytest.mark.parametrize("snake_keys", [False, True])
def test_classify_record_accepts_only_documented_daily_wrappers(daily_payload, wrappers, snake_keys, rules):
    payload = deepcopy(daily_payload)
    if snake_keys:
        payload["fda_product_codes"] = {"fda_product_code": {"product_code": {"_VALUE": " lza "}}}
        del payload["fdaProductCodes"]
        payload["gmdn_terms"] = {"gmdn": {
            "gmdn_code": {"#text": "100"}, "gmdn_pt_name": {"_value": " surgical GLOVE "},
        }}
        del payload["gmdnTerms"]
        payload["identifiers"] = {"identifier": {
            "device_id_type": {"_VALUE": "Primary"}, "device_id": {"#text": "00123"},
        }}
    for wrapper in reversed(wrappers):
        payload = {wrapper: payload}
    payload.update({"_release_date": "2026-11-18", "_load_id": "daily:20261118"})
    before = deepcopy(payload)
    result = classify_record(payload, rules)
    assert payload == before
    assert result["decision"] == "included"
    assert result["primary_di"] == "00123"
    assert result["public_device_record_key"] == "rk-1"
    assert result["product_codes"] == (["LZA"] if snake_keys else ["LZA", "XYZ"])
    assert result["gmdn_codes"] == ["100"]
    assert result["gmdn_terms"] == ["surgical glove"]
    assert result["source_release"] == "2026-11-18"
    assert result["source_load_id"] == "daily:20261118"
    assert result["validation_error"] is None


@pytest.mark.parametrize("payload", [
    {"device": {"device": {"productCode": "LZA"}}},
    {"gudid": {"gudid": {"device": {"productCode": "LZA"}}}},
    {"device": {"gudid": {"productCode": "LZA"}}},
    {"device": [{"productCode": "LZA"}]},
    {"fdaProductCodes": {"unrelated": {"productCode": "LZA"}}},
    {"fdaProductCodes": {"fdaProductCode": {"history": {"productCode": "LZA"}}}},
    {"gmdnTerms": {"history": {"gmdnCode": "999"}}},
    {"gmdnTerms": {"gmdn": {"vendorDocument": {"gmdnPTName": "Surgical glove"}}}},
    {"identifiers": {"identifier": {"deviceIdType": "Primary", "deviceId": "00123",
                                  "productCode": "LZA"}}},
])
def test_classify_record_does_not_descend_past_allowed_paths(payload, rules):
    result = classify_record(payload, rules)
    assert (result["decision"], result["reason"]) == ("unknown", "missing")
    assert result["evidence"] == []


@pytest.mark.parametrize("product_codes", [[], ["OTHER"], ["LZA"]])
def test_classify_record_full_join_uses_root_evidence_only(product_codes, daily_payload, rules):
    payload = {
        "device": {**daily_payload, "PrimaryDI": "00123", "productCode": "LZA", "gmdnCode": "999"},
        "product_code": product_codes, "gmdn_code": ["100"], "gmdn_pt_name": [],
        "_release_date": "2026-11-18", "_load_id": "full-load",
    }
    result = classify_record(payload, rules)
    assert result["product_codes"] == product_codes
    assert result["gmdn_codes"] == ["100"]
    assert result["gmdn_terms"] == []
    assert result["decision"] == ("included" if product_codes == ["LZA"] else "unknown")
    assert result["primary_di"] == "00123"
    assert result["public_device_record_key"] == "rk-1"
    assert result["device_comm_distribution_end_date"] == "2026-11-17"
    assert result["source_load_id"] == "full-load"
    assert result["validation_error"] is None


def test_classify_record_identifier_type_is_only_read_at_identifier_path(rules):
    result = classify_record({
        "deviceIdType": "Primary", "deviceId": "root-di",
        "identifiers": {"identifier": [
            {"deviceIdType": "Package", "deviceId": "package-di"},
            {"deviceIdType": "Secondary", "deviceId": "secondary-di"},
            {"history": {"deviceIdType": "Primary", "deviceId": "historical-di"}},
        ]},
    }, rules)
    assert result["primary_di"] is None
    assert result["validation_error"] is None


@pytest.mark.parametrize("payload", [
    {"_rescued_data": {"productCode": "LZA"}},
    {"_rescued_data": '{"productCode":"LZA"}'},
    {"other": '{"productCode":"LZA"}'},
    {"_metadata": {"productCode": "LZA"}},
    {"description": "productCode LZA"},
])
def test_rescued_data_and_free_text_are_not_evidence(payload, rules):
    assert classify_record(payload, rules)["reason"] == "missing"


def test_ambiguous_identity_is_flagged(rules):
    result = classify_record({"primaryDI": "one", "identifiers": {"identifier": {
        "deviceIdType": "Primary", "deviceId": "two"}}}, rules)
    assert result["validation_error"] == "ambiguous fields: primary_di"


@pytest.mark.parametrize("release", ["yesterday", "2026-99-99"])
def test_invalid_release_is_flagged(release, rules):
    assert classify_record({"_release_date": release}, rules)["validation_error"] == "invalid _release_date"


def test_rules_hash_is_order_independent_but_review_sensitive(rules):
    first = classify_record({}, rules)["classification_hash"]
    reordered = rules.model_copy(deep=True)
    reordered.rules.reverse()
    assert classify_record({}, reordered)["classification_hash"] == first
    reordered.rules[0].reviewed_by = "someone-else"
    assert classify_record({}, reordered)["classification_hash"] != first


def _snapshot(rules, release="2026-11-17", kind="full", **payload):
    return {**classify_record({"primary_di": "00123", "public_device_record_key": "rk-1",
                              "_release_date": release, **payload}, rules), "source_kind": kind}


@pytest.mark.parametrize("identity_change", [{}, {"primary_di": None}, {"primary_di": "new-di"},
                                            {"public_device_record_key": "new-key"}])
def test_latest_daily_unknown_replaces_whole_full_decision(rules, identity_change):
    full = _snapshot(rules, product_code="LZA")
    daily = _snapshot(rules, "2026-11-18", "daily", publicVersionStatus="REMOVED", **identity_change)
    for rows in permutations([full, daily]):
        assert select_latest(rows) == [daily]
    assert daily["decision"] == "unknown"
    assert daily["evidence"] == []


def test_ordering_release_then_numeric_version_then_daily(rules):
    rows = [
        _snapshot(rules, "2026-11-16", "daily", publicVersionNumber="999", productCode="LZA"),
        _snapshot(rules, publicVersionNumber="2", productCode="LZA"),
        _snapshot(rules, publicVersionNumber="10", productCode="LZA"),
        _snapshot(rules, kind="daily", publicVersionNumber="10"),
    ]
    for ordering in permutations(rows):
        assert select_latest(ordering) == [rows[-1]]


def test_equal_priority_contradictory_rows_are_rejected(rules):
    rows = [_snapshot(rules, productCode="LZA"), _snapshot(rules, productCode="other")]
    for ordering in permutations(rows):
        with pytest.raises(ValueError, match="ambiguous equal-priority"):
            select_latest(ordering)


def test_duplicate_loads_have_stable_tie_break(rules):
    first = {**_snapshot(rules, productCode="LZA"), "source_load_id": "a"}
    second = {**first, "source_load_id": "b"}
    assert select_latest([second, first, first]) == select_latest([first, second]) == [first]


def test_full_di_without_record_key_is_retained(rules):
    row = _snapshot(rules, public_device_record_key=None, productCode="LZA")
    assert select_latest([row]) == [row]
    with pytest.raises(ValueError, match="requires primary_di"):
        select_latest([{**row, "primary_di": None}])


def test_refresh_disabled_and_unreviewed_fail_before_spark(rules):
    spark = Mock()
    cfg = load_config()
    cfg.classification = None
    with pytest.raises(RuntimeError, match="disabled"):
        refresh_classification(spark, cfg, Names(catalog="test"), "test.ops")
    cfg.classification = rules.model_copy(deep=True)
    for rule in cfg.classification.rules:
        rule.verified = False
    with pytest.raises(RuntimeError, match="verified rule"):
        refresh_classification(spark, cfg, Names(catalog="test"), "test.ops")
    assert not spark.mock_calls


def test_missing_inputs_fail_without_spark_import(rules):
    with pytest.raises(RuntimeError, match="No classification input"):
        transform_dataframes(None, None, None, None, rules)
    with pytest.raises(RuntimeError, match="without device_raw"):
        transform_dataframes(None, Mock(), None, None, rules)


def test_refresh_writes_only_ops_and_resolves_configured_targets(rules):
    cfg = load_config()
    cfg.classification = rules
    names = Names(catalog="test", schemas={"quote_ai_gudid_bronze": "dev_bronze"})
    spark = Mock()
    spark.catalog.tableExists.side_effect = lambda target: target.endswith("daily_device_raw")
    snapshot = Mock()
    with patch("quote_ai.ingest.classification.transform_dataframes", return_value=snapshot) as transform:
        summary = refresh_classification(spark, cfg, names, "test.dev_ops")
    assert transform.call_args.args[:3] == (None, None, None)
    assert transform.call_args.args[3] is spark.table.return_value
    snapshot.write.format.assert_called_once_with("delta")
    snapshot.write.format.return_value.mode.assert_called_once_with("overwrite")
    writer = snapshot.write.format.return_value.mode.return_value.option.return_value
    writer.saveAsTable.assert_called_once_with("test.dev_ops.glove_classification")
    spark.sql.assert_called_once_with(
        "CREATE OR REPLACE VIEW `test`.`dev_ops`.`glove_devices` AS "
        "SELECT * FROM `test`.`dev_ops`.`glove_classification` WHERE decision = 'included'")
    assert summary["inputs"] == {"daily_device_raw": "test.dev_bronze.daily_device_raw"}
    assert (CLASSIFICATION_TABLE, INCLUDED_VIEW) == ("glove_classification", "glove_devices")


def test_refresh_validation_failure_does_not_publish(rules):
    cfg = load_config()
    cfg.classification = rules
    spark = Mock()
    with patch("quote_ai.ingest.classification.transform_dataframes", side_effect=RuntimeError("invalid input")):
        with pytest.raises(RuntimeError, match="invalid input"):
            refresh_classification(spark, cfg, Names(catalog="test"), "test.ops")
    spark.sql.assert_not_called()


def test_refresh_discovers_renamed_targets_from_entity_patterns(rules):
    cfg = load_config()
    cfg.classification = rules
    for source_name in (rules.full_source, rules.daily_source):
        for table in cfg.source(source_name).tables:
            table.target = table.target.replace("_raw", "_custom")
    spark = Mock()
    spark.catalog.tableExists.return_value = True
    with patch("quote_ai.ingest.classification.transform_dataframes", return_value=Mock()):
        summary = refresh_classification(spark, cfg, Names(catalog="test"), "test.ops")
    assert summary["inputs"]["device_raw"] == "test.quote_ai_gudid_bronze.device_custom"
    assert summary["inputs"]["daily_device_raw"] == "test.quote_ai_gudid_bronze.daily_device_custom"


class _Expression:
    def __init__(self, trace="column"):
        self.trace = trace

    def __getattr__(self, method):
        return lambda *args: _Expression(f"{self.trace}.{method}{tuple(str(arg) for arg in args)}")

    def __str__(self):
        return self.trace

    def _binary(self, other):
        return _Expression(f"({self.trace}, {other})")

    __eq__ = __ne__ = __and__ = __or__ = __le__ = _binary


class _Frame:
    def __init__(self, columns, operations, counts):
        self.columns = columns
        self.operations = operations
        self.counts = counts

    def toDF(self, *columns):
        return _Frame(list(columns), self.operations, self.counts)

    def __getattr__(self, method):
        def operation(*args):
            self.operations.append((method, args))
            if method == "count":
                return next(self.counts)
            if method == "select" and args == ("result.*",):
                columns = list(classify_record({}, GloveClassification(version="test", rules=[{
                    "rule_id": "test", "field": "product_code", "value": "X", "evidence_url": "https://example.org",
                }])).keys())
                return _Frame(columns, self.operations, self.counts)
            if method == "withColumn":
                return _Frame([*self.columns, args[0]], self.operations, self.counts)
            return self
        return operation


@pytest.fixture
def spark_plan(monkeypatch):
    operations = []
    sql = ModuleType("pyspark.sql")
    functions = Mock()
    for name in ("col", "lit", "struct", "from_json", "to_json", "sort_array", "collect_set",
                 "length", "when", "max", "min", "coalesce", "row_number"):
        setattr(functions, name, lambda *args, name=name: _Expression(f"{name}{tuple(str(arg) for arg in args)}"))
    functions.udf.side_effect = lambda function, schema: lambda column: _Expression("udf")
    sql.functions = functions
    sql.Window = SimpleNamespace(partitionBy=lambda *args: _Expression(f"partitionBy{tuple(str(arg) for arg in args)}"))
    types = ModuleType("pyspark.sql.types")
    types.StringType = Mock()
    for name, module in (("pyspark", ModuleType("pyspark")), ("pyspark.sql", sql), ("pyspark.sql.types", types)):
        monkeypatch.setitem(sys.modules, name, module)
    return operations, functions


def test_transform_full_join_plan_is_release_scoped_and_distributed(rules, spark_plan):
    operations, functions = spark_plan
    counts = iter([0, 0])
    base = _Frame(["PrimaryDI", "_release_date", "_load_id"], operations, counts)
    products = _Frame(["primary_di", "_release_date", "product_code"], operations, counts)
    terms = _Frame(["primary_di", "_release_date", "gmdn_pt_name", "gmdn_code"], operations, counts)
    daily = _Frame(["_release_date", "identifiers", "publicDeviceRecordKey"], operations, counts)
    transform_dataframes(base, products, terms, daily, rules)
    joins = [args for method, args in operations if method == "join"]
    assert len(joins) == 2
    assert all(args[1:] == (["primary_di", "_release_date"], "left") for args in joins)
    assert sum(method == "unionByName" for method, _ in operations) == 1
    assert [args for method, args in operations if method == "limit"] == [(1,), (1,)]
    assert not any(method in {"collect", "toPandas", "write"} for method, _ in operations)
    priorities = [args[1].trace for method, args in operations if method == "withColumn" and args[0] == "priority"]
    assert "source_release" in priorities[0] and "decimal(38,0)" in priorities[0] and "source_kind" in priorities[0]
    udf_function = functions.udf.call_args.args[0]
    assert json.loads(udf_function('{"productCode":"LZA"}'))["decision"] == "included"


@pytest.mark.parametrize("missing", ["primary_di", "_release_date"])
def test_transform_missing_base_columns_fails_before_actions(rules, spark_plan, missing):
    operations, _ = spark_plan
    base = _Frame([key for key in ("primary_di", "_release_date") if key != missing], operations, iter([]))
    products = _Frame(["primary_di", "_release_date", "product_code"], operations, iter([]))
    terms = _Frame(["primary_di", "_release_date", "gmdn_pt_name"], operations, iter([]))
    with pytest.raises(RuntimeError, match="missing required columns"):
        transform_dataframes(base, products, terms, None, rules)
    assert operations == []


@pytest.mark.parametrize("counts,message", [([1], "Invalid classification input"),
                                            ([0, 1], "Ambiguous equal-priority")])
def test_transform_rejects_bad_rows_and_ambiguous_duplicates(rules, spark_plan, counts, message):
    operations, _ = spark_plan
    daily = _Frame(["_release_date"], operations, iter(counts))
    with pytest.raises(RuntimeError, match=message):
        transform_dataframes(None, None, None, daily, rules)
    assert not any(method == "write" for method, _ in operations)


def test_transform_rejects_partial_full_release(rules, spark_plan):
    with pytest.raises(RuntimeError, match="requires product_codes_raw and gmdn_terms_raw"):
        transform_dataframes(Mock(), None, None, None, rules)


@pytest.fixture(scope="module")
def local_spark(tmp_path_factory):
    if os.environ.get("QUOTE_AI_TEST_SPARK") != "1":
        pytest.skip("Set QUOTE_AI_TEST_SPARK=1 with local PySpark/Java to run row-level Spark integration tests")
    sql = pytest.importorskip("pyspark.sql")
    session = (sql.SparkSession.builder.master("local[2]").appName("quote-ai-classification-tests")
               .config("spark.ui.enabled", "false")
               .config("spark.sql.warehouse.dir", str(tmp_path_factory.mktemp("classification-warehouse")))
               .getOrCreate())
    yield session
    session.stop()


def test_spark_full_entity_evidence_and_daily_override_match_reference(local_spark, rules, daily_payload):
    base = local_spark.createDataFrame([
        ("00123", "rk-1", "2026-11-17", "full-load"),
        ("excluded", "rk-2", "2026-11-17", "full-load"),
        ("no-key", None, "2026-11-17", "full-load"),
        ("stale", "rk-4", "2026-11-17", "full-load"),
    ], "primary_di string, public_device_record_key string, _release_date string, _load_id string")
    products = local_spark.createDataFrame([
        ("00123", "LZA", "2026-11-17"), ("no-key", "LZA", "2026-11-17"), ("stale", "LZA", "2026-11-16"),
    ], "primary_di string, product_code string, _release_date string")
    terms = local_spark.createDataFrame([
        ("excluded", "999", "not a glove", "2026-11-17"),
    ], "primary_di string, gmdn_code string, gmdn_pt_name string, _release_date string")
    removed = {key: value for key, value in daily_payload.items() if key not in ("fdaProductCodes", "gmdnTerms")}
    removed["publicVersionStatus"] = "REMOVED"
    daily = local_spark.read.json(local_spark.sparkContext.parallelize([json.dumps(removed)]))
    actual = transform_dataframes(base, products, terms, daily, rules)
    rows = {row.primary_di: row.asDict(recursive=True) for row in actual.collect()}
    assert rows["00123"]["decision"] == "unknown"
    assert rows["00123"]["public_version_status"] == "REMOVED"
    assert rows["00123"]["source_kind"] == "daily"
    assert rows["00123"]["evidence"] == []
    assert rows["excluded"]["decision"] == "excluded"
    assert rows["no-key"]["decision"] == "included"
    assert rows["no-key"]["public_device_record_key"] is None
    assert rows["stale"]["reason"] == "missing"
    reference = select_latest([_snapshot(rules, product_code="LZA"),
                               {**classify_record(removed, rules), "source_kind": "daily"}])
    assert rows["00123"]["decision"] == reference[0]["decision"]
    assert rows["00123"]["source_release"].isoformat() == reference[0]["source_release"]


@pytest.mark.parametrize("missing", ["record_key", "release", "identity_conflict"])
def test_spark_daily_invalid_input_fails_closed(local_spark, rules, daily_payload, missing):
    payload = deepcopy(daily_payload)
    if missing == "record_key":
        del payload["publicDeviceRecordKey"]
    elif missing == "release":
        payload["_release_date"] = "bad-date"
    else:
        payload["primaryDI"] = "contradictory-di"
    daily = local_spark.read.json(local_spark.sparkContext.parallelize([json.dumps(payload)]))
    with pytest.raises(RuntimeError, match="Invalid classification input"):
        transform_dataframes(None, None, None, daily, rules)


def test_spark_rejects_conflicting_daily_duplicates(local_spark, rules, daily_payload):
    other = deepcopy(daily_payload)
    other["fdaProductCodes"] = {"fdaProductCode": {"productCode": "OTHER"}}
    del other["gmdnTerms"]
    first = deepcopy(daily_payload)
    first["fdaProductCodes"] = {"fdaProductCode": {"productCode": "LZA"}}
    daily = local_spark.read.json(local_spark.sparkContext.parallelize([json.dumps(first), json.dumps(other)]))
    with pytest.raises(RuntimeError, match="Ambiguous equal-priority"):
        transform_dataframes(None, None, None, daily, rules)


def test_spark_numeric_version_order_and_deterministic_replay(local_spark, rules, daily_payload):
    payloads = []
    for version, load in [("2", "first"), ("10", "b"), ("10", "a")]:
        payload = deepcopy(daily_payload)
        payload["publicVersionNumber"], payload["_load_id"] = version, load
        payloads.append(payload)
    daily = local_spark.read.json(local_spark.sparkContext.parallelize([json.dumps(row) for row in payloads]))
    rows = transform_dataframes(None, None, None, daily, rules).collect()
    assert len(rows) == 1
    assert rows[0].public_version_number == "10"
    assert rows[0].source_load_id == "a"
    expected = select_latest([{**classify_record(row, rules), "source_kind": "daily"} for row in payloads])
    assert rows[0].source_load_id == expected[0]["source_load_id"]


def test_spark_api_query_rejects_nonmatching_category_provenance(local_spark):
    """Execute the actual API selector against mixed category and lineage fixtures."""
    from quote_ai.ingest.engine import changed_ids_sql

    digest = "a" * 64
    keys = ["included", "unknown", "excluded", "stale_hash", "stale_release", "stale_load", "stale_version", "cached"]
    daily = local_spark.createDataFrame([
        (key, "2026-11-18", "daily:20261118", "2") for key in keys
    ], "public_device_record_key string, _release_date string, _load_id string, public_version_number string")
    daily.createOrReplaceTempView("filter_daily")
    categories = []
    for key in keys:
        decision = key if key in {"unknown", "excluded"} else "included"
        categories.append((key, "2026-11-17" if key == "stale_release" else "2026-11-18",
                           "old-load" if key == "stale_load" else "daily:20261118",
                           "1" if key == "stale_version" else "2", decision,
                           "b" * 64 if key == "stale_hash" else digest))
    snapshot = local_spark.createDataFrame(categories, "public_device_record_key string, source_release string, "
                                          "source_load_id string, public_version_number string, decision string, "
                                          "classification_hash string")
    snapshot.createOrReplaceTempView("filter_categories")
    history = local_spark.createDataFrame([("cached", "2026-11-18")],
                                         "public_device_record_key string, _release_date string")
    history.createOrReplaceTempView("filter_history")
    query = changed_ids_sql("filter_daily", "public_device_record_key", "filter_history",
                            "public_device_record_key", None, "filter_categories", digest)

    assert [row.id for row in local_spark.sql(query).collect()] == ["included"]


def test_spark_newer_daily_version_blocks_stale_included_snapshot(local_spark):
    """An API-only rerun must not enrich an older matched row after newer inputs arrive."""
    from quote_ai.ingest.engine import changed_ids_sql

    digest = "a" * 64
    daily = local_spark.createDataFrame([
        ("same-day", "2026-11-18", "daily:20261118", "2"),
        ("same-day", "2026-11-18", "daily:20261118", "10"),
        ("next-day", "2026-11-17", "daily:20261117", "1"),
        ("next-day", "2026-11-18", "daily:20261118", "2"),
    ], "public_device_record_key string, _release_date string, _load_id string, public_version_number string")
    daily.createOrReplaceTempView("stale_daily")
    local_spark.createDataFrame([
        ("same-day", "2026-11-18", "daily:20261118", "2", "included", digest),
        ("next-day", "2026-11-17", "daily:20261117", "1", "included", digest),
    ], "public_device_record_key string, source_release string, source_load_id string, "
       "public_version_number string, decision string, classification_hash string").createOrReplaceTempView("stale_categories")
    query = changed_ids_sql("stale_daily", "public_device_record_key", None,
                            "public_device_record_key", None, "stale_categories", digest)

    assert local_spark.sql(query).collect() == []