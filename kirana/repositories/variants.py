from __future__ import annotations
import json
import logging
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


class VariantsRepositoryMixin:
    """Foundation 2 — product variants + dynamic attributes.

    Grocery products keep exactly one *implicit* variant, created lazily, so all
    legacy single-product queries keep working. Non-grocery verticals add real
    variants (size×colour, model/storage, …) off the per-vertical attribute defs.
    """

    # ── Attribute definitions ────────────────────────────────────────────────
    def list_attribute_defs(self, vertical_code: str) -> list[dict]:
        """The variant axes / attributes a vertical exposes, ordered for the UI."""
        sql = """
        SELECT attr_code, label, type, options, is_variant_axis, sort
        FROM kirana_oltp.product_attribute_def
        WHERE vertical_code = :vc
        ORDER BY sort, attr_code
        """
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"vc": vertical_code}).mappings().all()
        return [dict(r) for r in rows]

    # ── Implicit variant ─────────────────────────────────────────────────────
    def ensure_implicit_variant(self, product_id: int) -> int:
        """Return the product's implicit variant id, creating it if missing.

        Idempotent: a product never gets a second implicit variant. New products
        (created via the oltp path) get theirs on first variant access.
        """
        with self._conn() as conn:
            existing = conn.execute(
                text(
                    "SELECT variant_id FROM kirana_oltp.product_variant "
                    "WHERE product_id = :pid AND is_implicit = TRUE LIMIT 1"
                ),
                {"pid": product_id},
            ).scalar()
            if existing:
                return int(existing)
            vid = conn.execute(
                text("""
                INSERT INTO kirana_oltp.product_variant
                    (product_id, sku, barcode, is_implicit, is_active)
                SELECT p.product_id, p.sku, p.barcode, TRUE, TRUE
                FROM kirana_oltp.product p
                WHERE p.product_id = :pid
                RETURNING variant_id
                """),
                {"pid": product_id},
            ).scalar()
            conn.commit()
            return int(vid)

    # ── CRUD ─────────────────────────────────────────────────────────────────
    def list_variants(self, product_id: int, include_inactive: bool = False) -> list[dict]:
        """All variants for a product (ensures the implicit one exists first)."""
        self.ensure_implicit_variant(product_id)
        sql = """
        SELECT variant_id, product_id, sku, barcode, attributes,
               price, mrp, cost, stock, is_implicit, is_active, created_at
        FROM kirana_oltp.product_variant
        WHERE product_id = :pid
        """
        if not include_inactive:
            sql += " AND is_active = TRUE"
        sql += " ORDER BY is_implicit DESC, variant_id"
        with self._conn() as conn:
            rows = conn.execute(text(sql), {"pid": product_id}).mappings().all()
        return [dict(r) for r in rows]

    def create_variant(
        self,
        product_id: int,
        attributes: dict | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        price: float | None = None,
        mrp: float | None = None,
        cost: float | None = None,
        stock: float = 0,
    ) -> dict:
        sql = """
        INSERT INTO kirana_oltp.product_variant
            (product_id, sku, barcode, attributes, price, mrp, cost, stock, is_implicit, is_active)
        VALUES
            (:pid, :sku, :barcode, CAST(:attrs AS JSONB), :price, :mrp, :cost, :stock, FALSE, TRUE)
        RETURNING variant_id, product_id, sku, barcode, attributes,
                  price, mrp, cost, stock, is_implicit, is_active, created_at
        """
        with self._conn() as conn:
            row = conn.execute(
                text(sql),
                {
                    "pid": product_id,
                    "sku": sku,
                    "barcode": barcode,
                    "attrs": json.dumps(attributes or {}),
                    "price": price,
                    "mrp": mrp,
                    "cost": cost,
                    "stock": stock or 0,
                },
            ).mappings().first()
            conn.commit()
        self._sync_variant_inventory(product_id, int(row["variant_id"]), stock or 0)
        return dict(row)

    def _sync_variant_inventory(self, product_id: int, variant_id: int, stock: float) -> None:
        """F2 — mirror a real variant's stock into a store-scoped inventory row so
        reorder/dashboards (which read inventory) see per-variant stock. Derives
        the store from the product's existing inventory; no-op if the product has
        no inventory yet (e.g. mid add-product)."""
        with self._conn() as conn:
            conn.execute(text("""
                INSERT INTO kirana_oltp.inventory (store_id, product_id, variant_id, quantity)
                SELECT DISTINCT store_id, :pid, :vid, :qty
                FROM kirana_oltp.inventory WHERE product_id = :pid
                ON CONFLICT (store_id, product_id, COALESCE(variant_id, 0))
                DO UPDATE SET quantity = EXCLUDED.quantity
            """), {"pid": product_id, "vid": variant_id, "qty": int(stock or 0)})
            conn.commit()

    def update_variant(self, variant_id: int, **fields) -> dict | None:
        """Patch a variant. Accepts attributes/sku/barcode/price/mrp/cost/is_active."""
        allowed = {"attributes", "sku", "barcode", "price", "mrp", "cost", "stock", "is_active"}
        sets, params = [], {"vid": variant_id}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "attributes":
                sets.append("attributes = CAST(:attributes AS JSONB)")
                params["attributes"] = json.dumps(v)
            else:
                sets.append(f"{k} = :{k}")
                params[k] = v
        if not sets:
            return self.get_variant(variant_id)
        sql = (
            "UPDATE kirana_oltp.product_variant SET " + ", ".join(sets) +
            " WHERE variant_id = :vid RETURNING variant_id, product_id, sku, barcode, "
            "attributes, price, mrp, cost, stock, is_implicit, is_active, created_at"
        )
        with self._conn() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            conn.commit()
        if row and not row["is_implicit"] and "stock" in params:
            self._sync_variant_inventory(
                int(row["product_id"]), variant_id, float(params["stock"]))
        return dict(row) if row else None

    def get_variant(self, variant_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                text("""
                SELECT variant_id, product_id, sku, barcode, attributes,
                       price, mrp, cost, stock, is_implicit, is_active, created_at
                FROM kirana_oltp.product_variant WHERE variant_id = :vid
                """),
                {"vid": variant_id},
            ).mappings().first()
        return dict(row) if row else None

    def deactivate_variant(self, variant_id: int) -> bool:
        """Soft-delete. The implicit variant can never be deactivated."""
        with self._conn() as conn:
            updated = conn.execute(
                text(
                    "UPDATE kirana_oltp.product_variant SET is_active = FALSE "
                    "WHERE variant_id = :vid AND is_implicit = FALSE"
                ),
                {"vid": variant_id},
            ).rowcount
            conn.commit()
        return updated > 0
