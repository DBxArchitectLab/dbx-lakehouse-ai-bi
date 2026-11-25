"""Validate audit-view SQL construction without requiring a Spark runtime."""

import ast
from pathlib import Path


def test_ingestion_audit_expands_loads_before_left_joins() -> None:
    """Keep outer load expansion in a subquery so subsequent joins are valid."""
    source_path = Path(__file__).resolve().parents[2] / "src/quote_ai/ingest/log.py"
    module = ast.parse(source_path.read_text())
    ddl_assignment = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DDL" for target in node.targets)
    )
    ddl = ast.literal_eval(ddl_assignment.value)
    query = " ".join(ddl["v_ingestion_audit"].format(ops="`deepwater_tst`.`dev_quote_ai_ops`").split())

    assert "LATERAL VIEW" not in query.upper()
    assert (
        "FROM ( SELECT logs.*, explode_outer(logs.load_ids) AS load_id "
        "FROM `deepwater_tst`.`dev_quote_ai_ops`.ingestion_log logs ) l LEFT JOIN"
    ) in query
    assert "f.load_id = l.load_id AND f.source = l.source" in query
    assert "ON c.source = l.source AND c.config_hash = l.config_hash" in query
    assert "l.load_id, f.path" in query