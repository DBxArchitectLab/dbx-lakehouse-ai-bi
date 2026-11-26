"""Reviewed category evidence in ops; never commercial eligibility or a bronze mutation."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

from quote_ai.ingest.config import GloveClassification, IngestConfig
from quote_ai.ingest.lineage import normalized_columns, qualified, snake_case

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession
    from quote_ai.ingest.engine import Names

CLASSIFICATION_TABLE = "glove_classification"
INCLUDED_VIEW = "glove_devices"
_FIELDS = ("product_code", "gmdn_code", "gmdn_term")
_ALIASES = {"gmdn_pt_name": "gmdn_term", "fda_product_code": "product_code"}
_METADATA = (
    "public_device_record_key", "public_version_number", "public_version_status",
    "device_record_status", "device_publish_date", "commercial_distribution_status",
    "commercial_distribution_end_date", "device_comm_distribution_status",
    "device_comm_distribution_end_date",
)
_SEMANTIC_COLUMNS = (
    "primary_di", "public_device_record_key", "decision", "category", "reason", "evidence",
    "product_codes", "gmdn_codes", "gmdn_terms", *_METADATA[1:],
)
_RESULT_SCHEMA = """
    primary_di STRING, public_device_record_key STRING, public_version_number STRING,
    public_version_status STRING, device_record_status STRING, device_publish_date STRING,
    commercial_distribution_status STRING, commercial_distribution_end_date STRING,
    device_comm_distribution_status STRING, device_comm_distribution_end_date STRING,
    product_codes ARRAY<STRING>, gmdn_codes ARRAY<STRING>, gmdn_terms ARRAY<STRING>,
    decision STRING, category STRING, reason STRING,
    evidence ARRAY<STRUCT<rule_id:STRING,field:STRING,value:STRING,decision:STRING,
        evidence_url:STRING,verified:BOOLEAN,status:STRING,reviewed_by:STRING>>,
    classification_version STRING, classification_hash STRING,
    source_release DATE, source_load_id STRING, validation_error STRING
"""


def _normalize(field: str, value: str) -> str:
    return value.strip().casefold() if field == "gmdn_term" else value.strip().upper()


def _scalar(value: object) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip() or None
    if isinstance(value, Mapping):
        for key in ("_VALUE", "_value", "#text"):
            if key in value:
                return _scalar(value[key])
    return None


def _extract(payload: Mapping[str, Any]) -> dict[str, set[str]]:
    """Read record fields, optionally wrapped once by gudid and/or device.

    Joined root evidence is authoritative over the full base device struct.
    Daily XML evidence is limited to fda_product_codes/fda_product_code,
    gmdn_terms/gmdn, and identifiers/identifier, never arbitrary descendants.
    """
    found: dict[str, set[str]] = {}

    def add(key: str, value: object) -> None:
        if isinstance(value, list):
            for item in value:
                add(key, item)
        else:
            scalar = _scalar(value)
            if scalar:
                found.setdefault(key, set()).add(_normalize(key, scalar) if key in _FIELDS else scalar)

    def normalized(value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {snake_case(str(key)): item for key, item in value.items() if not str(key).startswith("_")}

    def entries(record: Mapping[str, Any], container: str, member: str) -> list[dict[str, Any]]:
        value = normalized(record.get(container)).get(member)
        return [normalized(item) for item in (value if isinstance(value, list) else [value])]

    root = normalized(payload)
    wrapped = normalized(root.get("gudid"))
    records = [root, wrapped, normalized(root.get("device")), normalized(wrapped.get("device"))]
    root_evidence = any(_ALIASES.get(key, key) in _FIELDS for key in root)
    for record in records:
        for key in ("primary_di", *_METADATA):
            add(key, record.get(key))
        for identifier in entries(record, "identifiers", "identifier"):
            if (_scalar(identifier.get("device_id_type")) or "").casefold() == "primary":
                add("primary_di", identifier.get("device_id"))
        if root_evidence and record is not root:
            continue
        for key, value in record.items():
            canonical = _ALIASES.get(key, key)
            if canonical in _FIELDS:
                add(canonical, value)
        for product in entries(record, "fda_product_codes", "fda_product_code"):
            add("product_code", product.get("product_code"))
        for term in entries(record, "gmdn_terms", "gmdn"):
            for key in ("gmdn_code", "gmdn_term", "gmdn_pt_name"):
                add(_ALIASES.get(key, key), term.get(key))
    return found


def classification_digest(rules: GloveClassification) -> str:
    """Return a stable hash binding the snapshot to its exact reviewed configuration."""
    value = rules.model_dump(mode="json")
    value["rules"] = sorted(value["rules"], key=lambda rule: rule["rule_id"])
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def classify_record(payload: Mapping[str, Any] | str, rules: GloveClassification) -> dict[str, Any]:
    """Classify exact structured evidence, retaining pending matches for audit only.

    :param payload: A JSON row or decoded full/daily row; string fields are never parsed recursively.
    :param rules: The versioned reviewed category configuration.
    :return: Decision, evidence, identities, provenance and source distribution metadata.
    :raises ValueError: If the input is not a JSON object.
    """
    decoded = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(decoded, Mapping):
        raise ValueError("classification payload must be a JSON object")
    found = _extract(decoded)
    evidence = []
    for rule in sorted(rules.rules, key=lambda rule: rule.rule_id):
        value = _normalize(rule.field, rule.value)
        if value in found.get(rule.field, set()):
            evidence.append({
                "rule_id": rule.rule_id, "field": rule.field, "value": value, "decision": rule.decision,
                "evidence_url": rule.evidence_url, "verified": rule.verified,
                "status": "verified" if rule.verified else "pending", "reviewed_by": rule.reviewed_by,
            })
    decisions = {match["decision"] for match in evidence if match["verified"]}
    decision = next(iter(decisions)) if len(decisions) == 1 else "unknown"
    reason = (
        "conflict" if len(decisions) > 1 else "matched" if decisions else "pending" if evidence
        else "no_match" if any(found.get(field) for field in _FIELDS) else "missing"
    )
    result: dict[str, Any] = {
        "primary_di": None, **{key: None for key in _METADATA},
        "product_codes": sorted(found.get("product_code", [])),
        "gmdn_codes": sorted(found.get("gmdn_code", [])),
        "gmdn_terms": sorted(found.get("gmdn_term", [])),
        "decision": decision, "category": "glove" if decision == "included" else None,
        "reason": reason, "evidence": evidence,
        "classification_version": rules.version, "classification_hash": classification_digest(rules),
        "source_release": None, "source_load_id": _scalar(decoded.get("_load_id")),
        "validation_error": None,
    }
    ambiguous = []
    for key in ("primary_di", *_METADATA):
        values = sorted(found.get(key, []))
        if len(values) > 1:
            ambiguous.append(key)
        result[key] = values[0] if values else None
    if ambiguous:
        result["validation_error"] = "ambiguous fields: " + ", ".join(ambiguous)
    release = _scalar(decoded.get("_release_date"))
    try:
        result["source_release"] = date.fromisoformat(release).isoformat() if release else None
    except ValueError:
        result["validation_error"] = "invalid _release_date"
    return result


def _priority(record: Mapping[str, Any]) -> tuple[str, int, int]:
    version = str(record.get("public_version_number") or "")
    number = int(version) if version.isascii() and version.isdecimal() and len(version) <= 38 else -1
    return (str(record.get("source_release") or ""), number,
            int(record.get("source_kind") == "daily"))


def _canonical(record: Mapping[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def select_latest(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reference the distributed release/version/daily ordering without stale evidence fallback.

    :param records: Classified snapshots, each with a DI or public record key.
    :return: Deterministically ordered current rows, matching on either identity.
    :raises ValueError: For missing identities or contradictory equal-priority snapshots.
    """
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for record in records:
        identities = [(key, str(record[key])) for key in ("primary_di", "public_device_record_key") if record.get(key)]
        if not identities:
            raise ValueError("classification row requires primary_di or public_device_record_key")
        for identity in identities:
            groups.setdefault(identity, []).append(record)
    winners = []
    for record in records:
        peers = [groups[(key, str(record[key]))] for key in ("primary_di", "public_device_record_key") if record.get(key)]
        if not all(_priority(record) == max(map(_priority, group)) for group in peers):
            continue
        for group in peers:
            signatures = {_canonical({key: peer.get(key) for key in _SEMANTIC_COLUMNS})
                          for peer in group if _priority(peer) == _priority(record)}
            if len(signatures) > 1:
                raise ValueError("ambiguous equal-priority classification snapshots")
        winners.append(dict(record))
    unique = {_canonical(record): record for record in winners}
    ordered = sorted(unique.values(), key=_canonical)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in ordered:
        identities = {(key, str(record[key])) for key in ("primary_di", "public_device_record_key") if record.get(key)}
        if not identities & seen:
            selected.append(record)
            seen.update(identities)
    return selected


def _normalized(frame: DataFrame) -> DataFrame:
    return frame.toDF(*normalized_columns(frame.columns))


def _require(frame: DataFrame, required: Sequence[str], label: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise RuntimeError(f"Classification {label} missing required columns {missing}; ingest a complete GUDID release first")


def transform_dataframes(
    full_device: DataFrame | None,
    product_codes: DataFrame | None,
    gmdn_terms: DataFrame | None,
    daily_device: DataFrame | None,
    rules: GloveClassification,
    *,
    full_source: str = "gudid_full_release",
    daily_source: str = "gudid_daily_updates",
) -> DataFrame:
    """Build and validate a distributed current category snapshot without writing.

    :param full_device: Full device base, or None for daily-only bootstrap.
    :param product_codes: Full product-code entity table; required with the full base.
    :param gmdn_terms: Full GMDN entity table; required with the full base.
    :param daily_device: Daily nested XML rows, optionally absent.
    :param rules: Versioned classification rules with at least one verified rule.
    :param full_source: Full input source name for provenance.
    :param daily_source: Daily input source name for provenance.
    :return: One current row per DI and/or record key, with explicit audit columns.
    :raises RuntimeError: For incomplete inputs, identities, releases or ambiguous current rows.
    """
    _guard_rules(rules)
    if full_device is None and (product_codes is not None or gmdn_terms is not None):
        raise RuntimeError("Full evidence tables exist without device_raw; ingest a complete full release first")
    if full_device is None and daily_device is None:
        raise RuntimeError("No classification input tables exist; ingest a full release or daily_device_raw first")
    from pyspark.sql import Window, functions as functions
    from pyspark.sql.types import StringType

    def classify_json(payload: str) -> str:
        return json.dumps(classify_record(payload, rules))

    classifier = functions.udf(classify_json, StringType())

    def classify(frame: DataFrame, kind: str, source: str) -> DataFrame:
        return (frame.select(functions.from_json(classifier(functions.to_json(functions.struct("*"))),
                                                _RESULT_SCHEMA).alias("result"))
                .select("result.*").withColumn("source_kind", functions.lit(kind))
                .withColumn("source_name", functions.lit(source)))

    frames = []
    if full_device is not None:
        if product_codes is None or gmdn_terms is None:
            raise RuntimeError("device_raw requires product_codes_raw and gmdn_terms_raw; ingest a complete full release first")
        base, products, terms = map(_normalized, (full_device, product_codes, gmdn_terms))
        _require(base, ("primary_di", "_release_date"), "device_raw")
        _require(products, ("primary_di", "_release_date", "product_code"), "product_codes_raw")
        _require(terms, ("primary_di", "_release_date"), "gmdn_terms_raw")
        term_fields = [key for key in ("gmdn_code", "gmdn_pt_name", "gmdn_term") if key in terms.columns]
        if not term_fields:
            raise RuntimeError("Classification gmdn_terms_raw requires gmdn_code or gmdn_pt_name")
        keys = ["primary_di", "_release_date"]
        products = products.groupBy(*keys).agg(
            functions.sort_array(functions.collect_set("product_code")).alias("product_code"))
        terms = terms.groupBy(*keys).agg(*[
            functions.sort_array(functions.collect_set(key)).alias(key) for key in term_fields
        ])
        joined = (base.select(functions.struct("*").alias("device"), *keys)
                  .join(products, keys, "left").join(terms, keys, "left"))
        payload = joined.select("device", "product_code", *term_fields,
                                functions.col("device._release_date").alias("_release_date"),
                                (functions.col("device._load_id") if "_load_id" in base.columns
                                 else functions.lit(None).cast("string")).alias("_load_id"))
        frames.append(classify(payload, "full", full_source))
    if daily_device is not None:
        daily = _normalized(daily_device)
        _require(daily, ("_release_date",), "daily_device_raw")
        frames.append(classify(daily, "daily", daily_source))
    combined = frames[0] if len(frames) == 1 else frames[0].unionByName(frames[1])
    invalid = (functions.col("validation_error").isNotNull() | functions.col("source_release").isNull()
               | (functions.col("primary_di").isNull() & functions.col("public_device_record_key").isNull())
               | ((functions.col("source_kind") == "daily") & functions.col("public_device_record_key").isNull())
               | ((functions.col("source_kind") == "full") & functions.col("primary_di").isNull()))
    if combined.where(invalid).limit(1).count():
        raise RuntimeError("Invalid classification input: require valid _release_date, full primary_di, "
                           "daily record key and unambiguous fields")
    version = functions.col("public_version_number")
    numeric_version = functions.when(version.rlike("^[0-9]+$") & (functions.length(version) <= 38),
                                     version.cast("decimal(38,0)")).otherwise(functions.lit(-1))
    ranked = combined.withColumn("priority", functions.struct(
        "source_release", numeric_version.alias("version"),
        (functions.col("source_kind") == "daily").cast("int").alias("daily")))
    ranked = ranked.withColumn("signature", functions.to_json(functions.struct(*_SEMANTIC_COLUMNS)))
    ranked = ranked.withColumn("tie", functions.to_json(functions.struct(*sorted(combined.columns))))
    for identity in ("primary_di", "public_device_record_key"):
        partition = Window.partitionBy(functions.coalesce(functions.col(identity), functions.col("tie")))
        ranked = ranked.withColumn(f"latest_{identity}", functions.max("priority").over(partition))
        peers = Window.partitionBy(functions.coalesce(functions.col(identity), functions.col("tie")), "priority")
        ranked = ranked.withColumn(f"conflict_{identity}",
                                   functions.min("signature").over(peers) != functions.max("signature").over(peers))
    current = ranked.where((functions.col("priority") == functions.col("latest_primary_di"))
                           & (functions.col("priority") == functions.col("latest_public_device_record_key")))
    if current.where("conflict_primary_di OR conflict_public_device_record_key").limit(1).count():
        raise RuntimeError("Ambiguous equal-priority classification snapshots; resolve duplicate source records before refresh")
    for identity in ("primary_di", "public_device_record_key"):
        window = Window.partitionBy(functions.coalesce(functions.col(identity), functions.col("tie"))).orderBy("tie")
        current = current.withColumn("row_number", functions.row_number().over(window)).where("row_number = 1")
    return current.select(*combined.columns).drop("validation_error")


def _guard_rules(rules: GloveClassification | None) -> None:
    if rules is None:
        raise RuntimeError("Glove classification is disabled; configure classification with reviewed versioned rules")
    if not any(rule.verified for rule in rules.rules):
        raise RuntimeError("Glove classification requires at least one verified rule; review rules before refresh")


def refresh_classification(spark: SparkSession, cfg: IngestConfig, names: Names, ops_fqn: str) -> dict[str, Any]:
    """Atomically replace the ops Delta snapshot and publish its included-only view.

    :param spark: Runtime Spark session (PySpark is imported only when transforming).
    :param cfg: Ingestion configuration; omitted classification is an error.
    :param names: Logical-to-physical environment table resolver.
    :param ops_fqn: Physical catalog.schema for both derived objects.
    :return: Physical object names, rule version/hash and available input tables.
    :raises RuntimeError: If rules or source tables cannot safely classify the snapshot.
    """
    rules = cfg.classification
    _guard_rules(rules)
    if len(ops_fqn.split(".")) != 2:
        raise ValueError("ops_fqn must be catalog.schema")
    qualified(ops_fqn)
    inputs: dict[str, str] = {}

    def read(source_name: str, table_name: str, filename: str) -> DataFrame | None:
        source = cfg.source(source_name)
        matches = [table for table in source.tables
                   if table.table_name == table_name or fnmatchcase(filename, table.pattern or "")]
        if len(matches) != 1:
            raise RuntimeError(f"Classification source {source_name!r} must configure exactly one {table_name} target")
        target = names.table(matches[0].target)
        if not spark.catalog.tableExists(target):
            return None
        inputs[table_name] = target
        return spark.table(target)

    full = read(rules.full_source, "device_raw", "device.txt")
    products = read(rules.full_source, "product_codes_raw", "productCodes.txt")
    terms = read(rules.full_source, "gmdn_terms_raw", "gmdnTerms.txt")
    daily = read(rules.daily_source, "daily_device_raw", "device.xml")
    snapshot = transform_dataframes(full, products, terms, daily, rules,
                                    full_source=rules.full_source, daily_source=rules.daily_source)
    table, view = f"{ops_fqn}.{CLASSIFICATION_TABLE}", f"{ops_fqn}.{INCLUDED_VIEW}"
    snapshot.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    spark.sql(f"CREATE OR REPLACE VIEW {qualified(view)} AS SELECT * FROM {qualified(table)} WHERE decision = 'included'")
    return {"classification_table": table, "included_view": view, "classification_version": rules.version,
            "classification_hash": classification_digest(rules), "inputs": inputs}