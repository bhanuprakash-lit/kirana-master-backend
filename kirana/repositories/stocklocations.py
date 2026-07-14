from __future__ import annotations
import logging
import re
from sqlalchemy import text

logger = logging.getLogger("kirana.repository")


def rack_label_key(label: str) -> str:
    """Normalized rack identity: case-, space- and punctuation-insensitive, so
    "A1", "a 1", "A-1" and "shelf 1" / "SHELF1" name the same rack. Must stay
    in sync with the SQL normalization in the racks_first_class_v1 backfill
    (base.py)."""
    return re.sub(r"[\W_]+", "", label or "", flags=re.UNICODE).upper()


def rack_display_label(label: str) -> str:
    """Canonical display spelling: trimmed, single-spaced, uppercased."""
    return re.sub(r"\s+", " ", (label or "").strip()).upper()


class StockLocationsRepositoryMixin:
    """Module M3 — multi-location / multi-rack stock. Racks are first-class
    rows (pre-creatable, renamable, mergeable); each inventory_location row is
    the quantity of one SKU at one rack. The legacy `rack` string column is
    kept in sync with rack.label so older readers keep working."""

    # ── Racks ────────────────────────────────────────────────────────────

    def list_racks(self, store_id: int) -> list[dict]:
        """All racks of the store — including empty ones — with placement
        counts, for the rack-browsing view and the rack picker."""
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT r.rack_id, r.label,
                       COUNT(il.id)                 AS items,
                       COALESCE(SUM(il.quantity), 0) AS total_qty
                FROM kirana_oltp.rack r
                LEFT JOIN kirana_oltp.inventory_location il ON il.rack_id = r.rack_id
                WHERE r.store_id = :sid
                GROUP BY r.rack_id, r.label
                ORDER BY r.label
            """), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]

    def create_rack(self, store_id: int, label: str) -> dict | None:
        """Create a rack, or return the existing one when the normalized label
        already exists ("a 1" while "A1" exists). None = unusable label."""
        display = rack_display_label(label)
        key = rack_label_key(label)
        if not key:
            return None
        with self._conn() as conn:
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.rack (store_id, label, label_key)
                VALUES (:sid, :label, :key)
                ON CONFLICT (store_id, label_key) DO NOTHING
                RETURNING rack_id, label
            """), {"sid": store_id, "label": display, "key": key}).mappings().first()
            if row is not None:
                conn.commit()
                return {**dict(row), "created": True}
            existing = conn.execute(text(
                "SELECT rack_id, label FROM kirana_oltp.rack "
                "WHERE store_id = :sid AND label_key = :key"),
                {"sid": store_id, "key": key}).mappings().first()
        return {**dict(existing), "created": False} if existing else None

    def rename_rack(self, store_id: int, rack_id: int, label: str) -> dict | str | None:
        """Returns the updated rack, 'conflict' when another rack already owns
        the normalized label, or None when the rack doesn't exist. Placement
        rows keep their denormalized rack string in sync."""
        display = rack_display_label(label)
        key = rack_label_key(label)
        if not key:
            return "conflict"
        with self._conn() as conn:
            other = conn.execute(text(
                "SELECT rack_id FROM kirana_oltp.rack "
                "WHERE store_id = :sid AND label_key = :key AND rack_id <> :rid"),
                {"sid": store_id, "key": key, "rid": rack_id}).first()
            if other:
                return "conflict"
            row = conn.execute(text("""
                UPDATE kirana_oltp.rack SET label = :label, label_key = :key
                WHERE rack_id = :rid AND store_id = :sid
                RETURNING rack_id, label
            """), {"label": display, "key": key, "rid": rack_id,
                   "sid": store_id}).mappings().first()
            if row is None:
                return None
            conn.execute(text(
                "UPDATE kirana_oltp.inventory_location SET rack = :label "
                "WHERE rack_id = :rid AND store_id = :sid"),
                {"label": display, "rid": rack_id, "sid": store_id})
            conn.commit()
        return dict(row)

    def delete_rack(self, store_id: int, rack_id: int) -> str:
        """'deleted' | 'not_found' | 'not_empty' — only empty racks can go, so
        stock never disappears silently."""
        with self._conn() as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM kirana_oltp.rack WHERE rack_id = :rid AND store_id = :sid"),
                {"rid": rack_id, "sid": store_id}).first()
            if not exists:
                return "not_found"
            in_use = conn.execute(text(
                "SELECT 1 FROM kirana_oltp.inventory_location "
                "WHERE rack_id = :rid LIMIT 1"), {"rid": rack_id}).first()
            if in_use:
                return "not_empty"
            conn.execute(text(
                "DELETE FROM kirana_oltp.rack WHERE rack_id = :rid AND store_id = :sid"),
                {"rid": rack_id, "sid": store_id})
            conn.commit()
        return "deleted"

    def merge_racks(self, store_id: int, source_id: int, target_id: int) -> dict | None:
        """Move every placement from source into target (summing quantities
        where the same SKU sits in both), then delete the source rack. For
        cleaning up duplicates that predate normalization. None = bad ids."""
        if source_id == target_id:
            return None
        with self._conn() as conn:
            target = conn.execute(text(
                "SELECT rack_id, label FROM kirana_oltp.rack "
                "WHERE rack_id = :rid AND store_id = :sid"),
                {"rid": target_id, "sid": store_id}).mappings().first()
            source = conn.execute(text(
                "SELECT rack_id FROM kirana_oltp.rack "
                "WHERE rack_id = :rid AND store_id = :sid"),
                {"rid": source_id, "sid": store_id}).first()
            if target is None or source is None:
                return None
            # SKUs present in both racks: add source qty into the target row…
            conn.execute(text("""
                UPDATE kirana_oltp.inventory_location t
                SET quantity = t.quantity + s.quantity
                FROM kirana_oltp.inventory_location s
                WHERE t.rack_id = :tgt AND s.rack_id = :src
                  AND t.store_id = :sid AND s.store_id = :sid
                  AND t.product_id = s.product_id
                  AND COALESCE(t.variant_id, 0) = COALESCE(s.variant_id, 0)
            """), {"tgt": target_id, "src": source_id, "sid": store_id})
            # …and drop the now-absorbed source rows.
            conn.execute(text("""
                DELETE FROM kirana_oltp.inventory_location s
                USING kirana_oltp.inventory_location t
                WHERE s.rack_id = :src AND t.rack_id = :tgt
                  AND s.store_id = :sid AND t.store_id = :sid
                  AND t.product_id = s.product_id
                  AND COALESCE(t.variant_id, 0) = COALESCE(s.variant_id, 0)
            """), {"tgt": target_id, "src": source_id, "sid": store_id})
            # SKUs only in the source simply move over.
            conn.execute(text("""
                UPDATE kirana_oltp.inventory_location
                SET rack_id = :tgt, rack = :label
                WHERE rack_id = :src AND store_id = :sid
            """), {"tgt": target_id, "label": target["label"],
                   "src": source_id, "sid": store_id})
            conn.execute(text(
                "DELETE FROM kirana_oltp.rack WHERE rack_id = :rid AND store_id = :sid"),
                {"rid": source_id, "sid": store_id})
            conn.commit()
        return {"rack_id": target["rack_id"], "label": target["label"]}

    def _resolve_rack(self, conn, store_id: int, rack_id: int | None,
                      label: str | None) -> dict | None:
        """Rack for a placement: by id (must belong to the store), or by label
        with get-or-create-by-normalized-key semantics."""
        if rack_id is not None:
            row = conn.execute(text(
                "SELECT rack_id, label FROM kirana_oltp.rack "
                "WHERE rack_id = :rid AND store_id = :sid"),
                {"rid": rack_id, "sid": store_id}).mappings().first()
            return dict(row) if row else None
        key = rack_label_key(label or "")
        if not key:
            return None
        row = conn.execute(text(
            "SELECT rack_id, label FROM kirana_oltp.rack "
            "WHERE store_id = :sid AND label_key = :key"),
            {"sid": store_id, "key": key}).mappings().first()
        if row:
            return dict(row)
        row = conn.execute(text("""
            INSERT INTO kirana_oltp.rack (store_id, label, label_key)
            VALUES (:sid, :label, :key)
            ON CONFLICT (store_id, label_key) DO NOTHING
            RETURNING rack_id, label
        """), {"sid": store_id, "label": rack_display_label(label or ""),
               "key": key}).mappings().first()
        if row is None:  # lost a concurrent race — the other writer's rack wins
            row = conn.execute(text(
                "SELECT rack_id, label FROM kirana_oltp.rack "
                "WHERE store_id = :sid AND label_key = :key"),
                {"sid": store_id, "key": key}).mappings().first()
        return dict(row) if row else None

    # ── Placements ───────────────────────────────────────────────────────

    def list_locations(self, store_id: int, product_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT id, product_id, variant_id, rack, rack_id, quantity
                FROM kirana_oltp.inventory_location
                WHERE store_id = :sid AND product_id = :pid
                ORDER BY rack
            """), {"sid": store_id, "pid": product_id}).mappings().all()
        return [dict(r) for r in rows]

    def product_exists(self, product_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute(text(
                "SELECT 1 FROM kirana_oltp.product WHERE product_id = :pid"),
                {"pid": product_id}).first() is not None

    def upsert_location(self, store_id: int, product_id: int, rack: str | None,
                        quantity: float, variant_id: int | None = None,
                        rack_id: int | None = None) -> dict | None:
        """Place a product in a rack (by rack_id, or by label which is
        normalized and auto-creates the rack). Same SKU + same rack updates
        the quantity instead of duplicating. None = rack not resolvable."""
        with self._conn() as conn:
            r = self._resolve_rack(conn, store_id, rack_id, rack)
            if r is None:
                return None
            row = conn.execute(text("""
                INSERT INTO kirana_oltp.inventory_location
                    (store_id, product_id, variant_id, rack, rack_id, quantity)
                VALUES (:sid, :pid, :vid, :rack, :rid, :qty)
                ON CONFLICT (store_id, product_id, (COALESCE(variant_id, 0)), rack_id)
                DO UPDATE SET quantity = EXCLUDED.quantity, rack = EXCLUDED.rack
                RETURNING id, product_id, variant_id, rack, rack_id, quantity
            """), {"sid": store_id, "pid": product_id, "vid": variant_id,
                   "rack": r["label"], "rid": r["rack_id"],
                   "qty": quantity}).mappings().first()
            conn.commit()
        return dict(row)

    def delete_location(self, location_id: int, store_id: int) -> bool:
        with self._conn() as conn:
            n = conn.execute(text(
                "DELETE FROM kirana_oltp.inventory_location WHERE id = :id AND store_id = :sid"),
                {"id": location_id, "sid": store_id}).rowcount
            conn.commit()
        return n > 0

    def find_by_rack(self, store_id: int, rack: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT il.id, il.product_id, p.name AS product_name, il.variant_id,
                       il.rack, il.rack_id, il.quantity
                FROM kirana_oltp.inventory_location il
                JOIN kirana_oltp.product p ON p.product_id = il.product_id
                WHERE il.store_id = :sid AND il.rack ILIKE :rack
                ORDER BY il.rack, p.name
            """), {"sid": store_id, "rack": f"%{rack}%"}).mappings().all()
        return [dict(r) for r in rows]

    def list_all_locations(self, store_id: int) -> list[dict]:
        """Every placement in the store (id + product name), for the rack-browsing
        view. The app groups these by rack to show 'what's in each rack'."""
        with self._conn() as conn:
            rows = conn.execute(text("""
                SELECT il.id, il.product_id, p.name AS product_name, il.variant_id,
                       il.rack, il.rack_id, il.quantity
                FROM kirana_oltp.inventory_location il
                JOIN kirana_oltp.product p ON p.product_id = il.product_id
                WHERE il.store_id = :sid
                ORDER BY il.rack, p.name
            """), {"sid": store_id}).mappings().all()
        return [dict(r) for r in rows]
