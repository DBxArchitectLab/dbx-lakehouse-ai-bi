"""Validate the Databricks catalog inventory notebook structure and scope."""

import ast
import json
from pathlib import Path

import yaml


NOTEBOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "notebooks/quote_ai/02_inventory_dev_quote_ai_objects.ipynb"
)
SOURCES_PATH = Path(__file__).resolve().parents[2] / "config/quote_ai/ingest/sources.yml"


def test_catalog_inventory_notebook_has_valid_cells_and_language_metadata() -> None:
    """Keep every notebook cell identified and Python cells syntactically valid."""
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    cell_ids = [cell["metadata"]["id"] for cell in notebook["cells"]]

    assert len(cell_ids) == len(set(cell_ids))
    for cell in notebook["cells"]:
        expected_language = "markdown" if cell["cell_type"] == "markdown" else "python"
        assert cell["metadata"]["language"] == expected_language
        assert isinstance(cell["source"], list)
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]), filename=str(NOTEBOOK_PATH))


def test_catalog_inventory_covers_scoped_unity_catalog_objects() -> None:
    """Include schemas, tables/views, volumes, routines, summary, and safe volume export."""
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    code = "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )

    assert "DEFAULT_CATALOG = \"deepwater_tst\"" in code
    assert "DEFAULT_SCHEMA_PREFIX = \"dev_quote_ai\"" in code
    assert "information_schema.schemata" in code
    assert "information_schema.tables" in code
    assert "information_schema.volumes" in code
    assert "information_schema.routines" in code
    assert "schema_prefix" in code
    assert "groupBy(\"schema_name\", \"object_type\")" in code
    assert "mode(\"errorifexists\")" in code
    assert "current_user()" in code


def test_catalog_inventory_documents_ingestion_method_for_configured_objects() -> None:
    """Every table and volume in sources.yml is mapped to its retrieval method."""
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    code = "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    config = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))
    targets = [
        table["target"].split(".", 1)[1]
        for source in config["sources"]
        for table in source["tables"]
    ]

    assert targets
    for target in targets:
        assert f'"{target}"' in code
    assert "api/v3/devices/history.json" in code
    assert 'withColumn("ingestion_method"' in code
