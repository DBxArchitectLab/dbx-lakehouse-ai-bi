"""QuoteAI's SQL files, rendered for one environment.

The files name schemas by their logical key in braces ({product}.dbxlab_sku, {gudid_silver}.device); a job passes the
physical names it got from the bundle (${resources.schemas.<key>.name}), so no code hard-codes a schema name and a
developer's dev copy (dev_<user>_quote_ai_product) works unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

_TOKEN = re.compile(r"\{([a-z_]+)\}")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

# logical key in the SQL files -> bundle schema resource key
SCHEMA_KEYS = {
    "product": "quote_ai_product", "sales": "quote_ai_sales", "sales_bronze": "quote_ai_sales_bronze",
    "gudid_silver": "quote_ai_gudid_silver", "gudid_gold": "quote_ai_gudid_gold", "ops": "quote_ai_ops",
    "ai": "quote_ai_ai", "monitoring": "quote_ai_monitoring", "ml": "quote_ai_ml", "eval": "quote_ai_eval",
}


def render(text: str, **names: str) -> str:
    """Replace each {key} with catalog.schema (or {catalog} with the catalog). An unknown key, or a value that isn't
    a plain identifier, raises: nothing unresolved reaches the warehouse."""
    for v in names.values():
        if not _IDENT.match(v):
            raise ValueError(f"not a plain identifier: {v!r}")

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in names:
            raise KeyError(f"no value for {{{key}}}")
        return names[key]
    return _TOKEN.sub(sub, text)


def names_for(catalog: str, schemas: dict[str, str]) -> dict[str, str]:
    """{product: cat.quote_ai_product, ...} from the catalog and logical=physical schema names (bundle-resolved)."""
    out = {"catalog": catalog}
    for key, logical in SCHEMA_KEYS.items():
        out[key] = f"{catalog}.{schemas.get(logical, logical)}"
    return out


def names_from_anchor(catalog: str, logical: str, physical: str) -> dict[str, str]:
    """All names from one deployed schema name. A personal dev copy prefixes every schema the same way
    (dev_<user>_quote_ai_sales), dev-shared and prod not at all; so the prefix of one gives the others. Used by the app,
    which gets one full name from its resources (a volume in quote_ai_sales)."""
    if not physical.endswith(logical):
        raise ValueError(f"{physical!r} is not a deployed name of {logical!r}")
    prefix = physical[: len(physical) - len(logical)]
    return names_for(catalog, {k: prefix + k for k in SCHEMA_KEYS.values()})


def statements(text: str) -> list[str]:
    """Split a rendered file on the ';' that ends a line; drop comment-only chunks."""
    out = []
    for chunk in re.split(r";\s*(?:\n|$)", text):
        body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            out.append(chunk.strip())
    return out


def load(path: str | Path, **names: str) -> list[str]:
    return statements(render(Path(path).read_text(encoding="utf-8"), **names))
