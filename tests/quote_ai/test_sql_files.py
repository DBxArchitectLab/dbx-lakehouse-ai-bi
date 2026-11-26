"""QuoteAI's SQL files: schema names come from the bundle, and every table and view carries a comment (AI context)."""
import re
from pathlib import Path

import pytest

from quote_ai.storage import sql_files

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "src" / "quote_ai").rglob("*.sql"))
NAMES = sql_files.names_for("cat", {"quote_ai_product": "dev_me_quote_ai_product"})


def test_there_are_sql_files():
    assert {f.name for f in FILES} >= {"sku_model.sql", "glove_subset.sql"}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_schema_name_is_hard_coded(path):
    code = [l for l in path.read_text(encoding="utf-8").splitlines() if not l.strip().startswith("--")]
    assert not [l for l in code if re.search(r"\bquote_ai_\w+\.", l)], "use {key}.table, filled in from the bundle"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_table_and_view_has_a_comment(path):
    text = path.read_text(encoding="utf-8")
    if text.startswith("-- dialect: postgres"):     # Lakebase (AI-20): Postgres has COMMENT ON TABLE, not inline
        tables = re.findall(r"(?i)CREATE TABLE IF NOT EXISTS (\w+)", text)
        assert tables
        for t in tables:
            assert re.search(rf"(?i)COMMENT ON TABLE {t} IS '", text), f"{path.name}: {t} has no COMMENT ON TABLE"
        return
    for stmt in sql_files.load(path, **NAMES):
        if re.match(r"(?is)\s*(--[^\n]*\n\s*)*CREATE ", stmt):
            assert re.search(r"\)\s*COMMENT '|\bCOMMENT '", stmt), stmt[:80]


def test_render_uses_the_deployed_schema_names_and_refuses_unknown_keys():
    out = sql_files.render("SELECT * FROM {product}.dbxlab_sku", **NAMES)
    assert out == "SELECT * FROM cat.dev_me_quote_ai_product.dbxlab_sku"
    with pytest.raises(KeyError):
        sql_files.render("{nope}.t", **NAMES)
    with pytest.raises(ValueError):
        sql_files.render("{product}.t", product="x; DROP TABLE y")


def test_statements_split_on_line_ending_semicolons():
    assert sql_files.statements("-- c\nA;\nB 'x;y';\n") == ["-- c\nA", "B 'x;y'"]
