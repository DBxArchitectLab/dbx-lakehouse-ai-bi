"""What the app reads about products (AI-07): the offerable SKUs, the price book, and a lookup by catalog number.

  SqlProductCatalog   the lakehouse, through the app's warehouse: quote_ai_product (dbxlab_product, dbxlab_sku,
                      sku_offerable), quote_ai_gudid_gold.glove_devices and quote_ai_sales.price_book
  DemoProductCatalog  the same answers from quote_ai.mock.catalog, for tests and a local run (synthetic; every
                      box-level mapping counted as verified, as --demo-verify does in the product job)

AI-15 adds what the eligibility engine and fulfilment read: products (A to F and the wider range G to R, from the view
quote_ai_product.catalog_skus) as domain Products, and the latest inventory snapshot.
AI-18 adds what the agent's tools read: a product's GUDID device record (the lakehouse copy, with its release date)
and each SKU's packaging, which price_quote converts units with.

Read-only: the app never writes the product model.
"""
from __future__ import annotations

from typing import Protocol

from quote_ai.domain.models import Product, Sku
from quote_ai.mock import catalog as mock
from quote_ai.mock import range as mock_range
from quote_ai.rules.fulfilment import Stock
from quote_ai.rules.pricing import Packaging, PriceBookRow
from quote_ai.rules.simple_rules import CatalogSku


class ProductCatalog(Protocol):
    def offerable_skus(self) -> list[CatalogSku]: ...
    def price_book(self) -> list[PriceBookRow]: ...
    def lookup(self, catalog_number: str) -> dict | None: ...
    def products(self, product_ids: list[str]) -> list[Product]: ...                  # AI-15
    def stock(self, sku_ids: list[str]) -> tuple[dict[str, Stock], str]: ...          # AI-15: rows, snapshot id
    def device(self, product_id: str) -> dict | None: ...                              # AI-18: the lakehouse copy
    def packaging(self, sku_ids: list[str]) -> dict[str, Packaging]: ...               # AI-18: for price_quote


def sku_packaging(unit: str, gloves_per_unit: int) -> Packaging:
    """A SKU's packaging from the product model: its selling unit, and single gloves (each)."""
    return Packaging(selling_unit=unit, gloves_per={"each": 1, unit: int(gloves_per_unit)})


def to_products(rows: list[dict]) -> list[Product]:
    """catalog_skus rows -> domain Products (one per product_id, SKUs in row order)."""
    by: dict[str, Product] = {}
    for r in rows:
        p = by.setdefault(r["product_id"], Product(product_id=r["product_id"], name=r.get("product_name") or "",
                                                   skus=[]))
        p.skus.append(Sku(sku_id=r["sku_id"], product_id=r["product_id"], size=r["size"], unit=r["unit"],
                          gloves_per_unit=int(r["gloves_per_unit"]), sellable=bool(r["sellable"]),
                          mapping_verified=bool(r["mapping_verified"])))
    return list(by.values())


class SqlProductCatalog:
    def __init__(self, sql, names: dict[str, str]) -> None:
        """sql: a quote_ai.storage.delta_repository SqlExecutor; names: quote_ai.storage.sql_files.names_for(...)."""
        self.sql, self.n = sql, names

    def offerable_skus(self) -> list[CatalogSku]:
        rows = self.sql.query(
            f"SELECT s.sku_id, s.product_id, p.name AS product_name, s.catalog_number, s.size, s.unit, "
            f"s.gloves_per_unit, s.sellable, TRUE AS mapping_verified, s.material, s.intended_use, p.powder_free, "
            f"p.latex_free FROM {self.n['product']}.sku_offerable s "
            f"JOIN {self.n['product']}.dbxlab_product p ON p.product_id = s.product_id")
        return [CatalogSku.model_validate(r) for r in rows]

    def price_book(self) -> list[PriceBookRow]:
        rows = self.sql.query(
            f"SELECT price_book_id AS id, sku_id, tier, min_units, CAST(list_price AS DOUBLE) AS list_price, "
            f"CAST(unit_price AS DOUBLE) AS unit_price, CAST(cost AS DOUBLE) AS cost, "
            f"CAST(min_margin AS DOUBLE) AS min_margin, CAST(floor_price AS DOUBLE) AS floor_price "
            f"FROM {self.n['sales']}.price_book "
            f"WHERE _release_date = (SELECT max(_release_date) FROM {self.n['sales']}.price_book)")
        return [PriceBookRow.model_validate(r) for r in rows]

    def lookup(self, catalog_number: str) -> dict | None:
        """A DBxLab catalog number (DBX-NE-M-100) or a supplier's: the SKU, its unit and verified status, and the
        GUDID record of its supplier device."""
        skus = self.sql.query(
            f"SELECT s.*, m.verification_status, m.package_di, m.unit_level FROM {self.n['product']}.dbxlab_sku s "
            f"LEFT JOIN {self.n['product']}.product_device_mapping m ON m.sku_id = s.sku_id "
            f"AND m.verification_status = 'verified' "
            f"WHERE upper(s.catalog_number) = upper(:c) OR upper(s.supplier_catalog_number) = upper(:c)",
            {"c": catalog_number})
        dis = {s["supplier_di"] for s in skus if s.get("supplier_di")}
        devices = self.sql.query(
            f"SELECT primary_di, brand_name, company_name, catalog_number, version_model_number, device_description, "
            f"labeled_no_nrl, labeled_contains_nrl, device_sterile, device_record_status, category "
            f"FROM {self.n['gudid_gold']}.glove_devices WHERE upper(catalog_number) = upper(:c)"
            + (" OR primary_di IN (" + ", ".join(f":d{i}" for i in range(len(dis))) + ")" if dis else ""),
            {"c": catalog_number} | {f"d{i}": d for i, d in enumerate(sorted(dis))})
        if not skus and not devices:
            return None
        return {"catalog_number": catalog_number, "skus": skus, "gudid": devices}


    def products(self, product_ids: list[str]) -> list[Product]:
        if not product_ids:
            return []
        marks = ", ".join(f":p{i}" for i in range(len(product_ids)))
        rows = self.sql.query(f"SELECT * FROM {self.n['product']}.catalog_skus WHERE product_id IN ({marks}) "
                              f"ORDER BY product_id, sku_id", {f"p{i}": p for i, p in enumerate(product_ids)})
        return to_products(rows)

    def stock(self, sku_ids: list[str]) -> tuple[dict[str, Stock], str]:
        """The latest inventory snapshot (AI-02's synthetic stock): one row per SKU, and its snapshot ID."""
        rows = self.sql.query(
            f"SELECT snapshot_id, sku_id, available, reserved, lead_time_days, replenishment_qty, replenishment_day "
            f"FROM {self.n['sales']}.inventory_snapshot "
            f"WHERE _release_date = (SELECT max(_release_date) FROM {self.n['sales']}.inventory_snapshot)")
        wanted = set(sku_ids)
        out = {r["sku_id"]: Stock.model_validate({k: v for k, v in r.items() if v is not None and k != "snapshot_id"})
               for r in rows if r["sku_id"] in wanted}
        return out, (rows[0]["snapshot_id"] if rows else "inventory@none")

    def device(self, product_id: str) -> dict | None:
        """The GUDID record of the product's supplier device, as of the last daily release (the lakehouse copy).
        Verify before recording: glove_devices carries _release_date (AI-03's gold keeps AI-02's lineage columns)."""
        rows = self.sql.query(
            f"SELECT s.product_id, g.primary_di, g.brand_name, g.company_name, g.device_description, "
            f"g.labeled_no_nrl, g.labeled_contains_nrl, g.device_sterile, g.device_record_status, "
            f"CAST(g._release_date AS STRING) AS release_date FROM {self.n['product']}.dbxlab_sku s "
            f"JOIN {self.n['gudid_gold']}.glove_devices g ON g.primary_di = s.supplier_di "
            f"WHERE s.product_id = :p ORDER BY s.sku_id LIMIT 1", {"p": product_id})
        return rows[0] if rows else None

    def packaging(self, sku_ids: list[str]) -> dict[str, Packaging]:
        if not sku_ids:
            return {}
        marks = ", ".join(f":s{i}" for i in range(len(sku_ids)))
        rows = self.sql.query(f"SELECT sku_id, unit, gloves_per_unit FROM {self.n['product']}.catalog_skus "
                              f"WHERE sku_id IN ({marks})", {f"s{i}": s for i, s in enumerate(sku_ids)})
        return {r["sku_id"]: sku_packaging(r["unit"], r["gloves_per_unit"]) for r in rows}


class DemoProductCatalog:
    """Synthetic: Gloves A to F, every box-level mapping verified, list $8.60 for A to E (AI-02's price book has no
    Glove F, so F is never priced). gudid_release: the day of the lakehouse copy of GUDID (AI-18 onwards)."""

    def __init__(self, gudid_release: str = "2026-11-12") -> None:
        self.gudid_release = gudid_release

    def device(self, product_id: str) -> dict | None:
        from quote_ai.storage.evidence_store import DEMO_DEVICES
        devices = DEMO_DEVICES | mock_range.devices()
        if product_id not in devices:
            return None
        desc, fields = devices[product_id]
        all_skus = mock.skus() + mock_range.skus()
        i = next(i for i, s in enumerate(all_skus) if s["product_id"] == product_id)
        names = {p["product_id"]: p["name"] for p in mock.products() + mock_range.products()}
        return {"product_id": product_id, "primary_di": f"0081234500{i:04d}", "brand_name": names[product_id],
                "company_name": "Demo Supplier (synthetic)", "device_description": desc,
                "labeled_no_nrl": fields.get("labeled_no_nrl") == "true",
                "labeled_contains_nrl": fields.get("labeled_contains_nrl") == "true",
                "device_sterile": fields.get("device_sterile") == "true",
                "device_record_status": "Published",
                "in_commercial_distribution": fields.get("in_commercial_distribution") == "true",
                "release_date": self.gudid_release}

    def packaging(self, sku_ids: list[str]) -> dict[str, Packaging]:
        wanted = set(sku_ids)
        return {s["sku_id"]: sku_packaging(s["unit"], s["gloves_per_unit"])
                for s in mock.skus() + mock_range.skus() if s["sku_id"] in wanted}

    def offerable_skus(self) -> list[CatalogSku]:
        products = {p["product_id"]: p for p in mock.products()}
        out = []
        for s in mock.skus():
            p = products[s["product_id"]]
            out.append(CatalogSku(sku_id=s["sku_id"], product_id=s["product_id"], product_name=p["name"],
                                  catalog_number=s["catalog_number"], size=s["size"], unit=s["unit"],
                                  gloves_per_unit=s["gloves_per_unit"], sellable=s["sellable"], mapping_verified=True,
                                  material=s["material"], intended_use=s["intended_use"],
                                  powder_free=p["powder_free"], latex_free=p["latex_free"]))
        return out

    def price_book(self) -> list[PriceBookRow]:
        return [PriceBookRow(id=f"PB-DEMO-{s['sku_id']}", sku_id=s["sku_id"], tier="standard", min_units=1,
                             list_price=8.60, unit_price=8.60, cost=6.50, min_margin=0.12)
                for s in mock.skus() if s["product_id"] != "GLOVE-F"]

    def products(self, product_ids: list[str]) -> list[Product]:
        names = {p["product_id"]: p["name"] for p in mock.products() + mock_range.products()}
        rows = [s | {"product_name": names[s["product_id"]], "mapping_verified": True}
                for s in mock.skus() + mock_range.skus() if s["product_id"] in set(product_ids)]
        order = {p: i for i, p in enumerate(product_ids)}
        return sorted(to_products(rows), key=lambda p: order[p.product_id])

    def stock(self, sku_ids: list[str]) -> tuple[dict[str, Stock], str]:
        """The same numbers as AI-02's dbxlab_synthetic files: Gloves A to E in S to XL, 1000 each, 7 days; Glove C
        M and L short (65 now, 500 on day 14). F and the range have no stock row."""
        out = {}
        for letter in "ABCDE":
            for size in mock.SIZES:
                sku = f"GLOVE-{letter}-{size}"
                short = letter == "C" and size in ("M", "L")
                out[sku] = Stock(sku_id=sku, available=65 if short else 1000, lead_time_days=7,
                                 replenishment_qty=500 if short else 0, replenishment_day=14 if short else None)
        return {k: v for k, v in out.items() if k in set(sku_ids)}, "INV-demo"

    def lookup(self, catalog_number: str) -> dict | None:
        skus = [s for s in mock.skus() if s["catalog_number"].upper() == catalog_number.upper()]
        if not skus:
            return None
        return {"catalog_number": catalog_number,
                "skus": [s | {"verification_status": "verified", "unit_level": "box"} for s in skus],
                "gudid": []}
