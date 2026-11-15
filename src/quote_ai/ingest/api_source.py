"""AccessGUDID v3 as a Spark source: spark.read.format("quote_ai_gudid_api").options(...).load().

A PySpark custom data source (the Python Data Source API, pyspark.sql.datasource): it reads like any other Spark
source, splits the identifiers across partitions (workers), and keeps retries and rate limits in one place
(http.Http). The partitions share the rate limit, so the service sees at most max_rps in total.

Options (all strings): base_url, endpoint, param (di or record_key), ids (comma-separated), limits (ApiLimits JSON).
Register once per session: register(spark).
"""
from __future__ import annotations

import json

from quote_ai.ingest.gudid_client import HISTORY_SCHEMA, GudidClient
from quote_ai.ingest.http import Http

try:    # on Databricks; locally the tests use split_ids and the client without Spark
    from pyspark.sql.datasource import DataSource, DataSourceReader, InputPartition
except ImportError:     # pragma: no cover
    DataSource = DataSourceReader = object

    class InputPartition:          # type: ignore[no-redef]
        def __init__(self, value):
            self.value = value


def split_ids(ids: list[str], partitions: int) -> list[list[str]]:
    """Round-robin the identifiers into at most `partitions` non-empty chunks."""
    n = max(1, min(partitions, len(ids)))
    return [c for c in (ids[i::n] for i in range(n)) if c]


class IdsPartition(InputPartition):
    def __init__(self, ids: list[str]) -> None:
        super().__init__(ids)
        self.ids = ids


class GudidApiDataSource(DataSource):
    @classmethod
    def name(cls) -> str:
        return "quote_ai_gudid_api"

    def schema(self) -> str:
        return HISTORY_SCHEMA

    def reader(self, schema):
        return GudidApiReader(self.options)


class GudidApiReader(DataSourceReader):
    def __init__(self, options) -> None:
        self.base_url = options["base_url"]
        self.endpoint = options["endpoint"]
        self.param = options.get("param", "di")
        self.ids = [i for i in options.get("ids", "").split(",") if i]
        self.limits = json.loads(options.get("limits", "{}"))

    def partitions(self):
        return [IdsPartition(c) for c in split_ids(self.ids, int(self.limits.get("partitions", 2)))] or \
            [IdsPartition([])]

    def read(self, partition):
        n = max(1, len(self.partitions()))
        http = Http(max_rps=float(self.limits.get("max_rps", 2)) / n, retries=int(self.limits.get("retries", 5)),
                    backoff_s=float(self.limits.get("backoff_s", 2)),
                    timeout_s=float(self.limits.get("timeout_s", 30)))
        client = GudidClient(self.base_url, self.endpoint, http)
        for value in partition.ids:
            yield from client.history(self.param, value)


def register(spark) -> None:
    spark.dataSource.register(GudidApiDataSource)
