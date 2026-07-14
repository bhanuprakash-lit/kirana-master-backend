from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import MetaData, Table, and_, delete, exists, func, inspect, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError as SAIntegrityError


GLOBAL_READ_TABLES = {"calendar", "category", "product"}
ADMIN_ONLY_WRITE_TABLES = {"calendar", "store"}
DIRECT_STORE_TABLES = {
    "customer",
    "footfall",
    "inventory",
    "inventory_batch",
    "inventory_movements",
    "inventory_snapshots",
    "khata",
    "marketing_spend",
    "opex",
    "orders",
    "pricing",
    "promotion",
    "purchases",
    "return_to_vendor",
    "scheme_claim",
    "shelf_planogram",
    "store",
    "subscription",
    "supplier",
    "users",
}
INDIRECT_SCOPE_TABLES = {
    "order_item": "orders",
    "payments": "orders",
    "purchase_items": "purchases",
    "product_supplier": "supplier",
    "scheme": "supplier",
}


@dataclass(frozen=True)
class TableMeta:
    name: str
    primary_keys: tuple[str, ...]
    columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    required_create_columns: tuple[str, ...]
    foreign_keys: tuple[dict[str, str], ...]
    has_store_id: bool
    read_scope: str
    write_scope: str
    column_map: dict[str, str] = None


class OltpRepository:
    _CACHE: dict[str, tuple[MetaData, dict[str, Table], dict[str, TableMeta]]] = {}

    # Frontend key -> DB column mappings for specific tables
    COLUMN_MAPPINGS = {
        "inventory_batch": {
            "quantity": "qty_in_stock",
        },
        "pricing": {
            "selling_price": "price",
        }
    }

    def __init__(self, engine: Engine):
        self._engine = engine
        cache_key = str(engine.url)
        cached = self._CACHE.get(cache_key)
        if cached is None:
            cached = _load_oltp_metadata(engine)
            self._CACHE[cache_key] = cached
        self._meta, self._tables, self._table_meta = cached

    def schema_overview(self) -> list[dict[str, Any]]:
        return [self._table_meta[name].__dict__ for name in sorted(self._table_meta)]

    def schema_for(self, table_name: str) -> dict[str, Any]:
        return self._meta_for(table_name).__dict__

    def list_rows(
        self,
        table_name: str,
        user: dict,
        filters: dict[str, Any],
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        table = self._table(table_name)
        
        if table_name == "purchase_items":
            # Special case: Join with product to get names
            product_table = self._table("product")
            stmt = select(
                table,
                product_table.c.name.label("product_name")
            ).select_from(
                table.outerjoin(product_table, table.c.product_id == product_table.c.product_id)
            )
        else:
            stmt = select(table)
            
        stmt = self._apply_scope(stmt, table_name, user)
        stmt = self._apply_filters(stmt, table, filters)
        stmt = stmt.limit(limit).offset(offset)
        with self._engine.begin() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt).all()]
        return {
            "table": table_name,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "rows": rows,
        }

    def get_row(self, table_name: str, user: dict, keys: dict[str, Any]) -> dict[str, Any]:
        table = self._table(table_name)
        stmt = select(table).where(self._row_filter(table_name, table, keys))
        stmt = self._apply_scope(stmt, table_name, user)
        with self._engine.begin() as conn:
            row = conn.execute(stmt).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail=f"{table_name} record not found")
        return {"table": table_name, "row": dict(row)}

    # Tables where (store_id, product_id) is the natural unique key and a
    # duplicate insert should update instead of raising.
    _UPSERT_ON_CONFLICT = {"inventory"}

    def create_row(self, table_name: str, user: dict, payload: dict[str, Any]) -> dict[str, Any]:
        table = self._table(table_name)
        self._check_write_permission(table_name, user, action="create")
        clean = self._sanitize_payload(table_name, payload, allow_primary_keys=True)
        with self._engine.begin() as conn:
            clean = self._enforce_write_scope(conn, table_name, user, clean)
            self._validate_required_create_fields(table_name, clean)
            if table_name in self._UPSERT_ON_CONFLICT:
                # pg_insert supports on_conflict_do_update (PostgreSQL-specific).
                # F2 — inventory is unique per (store, product, variant); target
                # the functional index uq_inventory_store_product_variant so a
                # grocery row (variant_id NULL) and each real variant upsert
                # independently. COALESCE(variant_id, 0) matches the index expr.
                stmt = pg_insert(table).values(**clean)
                update_cols = {
                    k: v for k, v in clean.items()
                    if k not in ("store_id", "product_id", "variant_id",
                                 "inventory_id", "batch_id")
                }
                conflict_target = [
                    table.c.store_id,
                    table.c.product_id,
                    func.coalesce(table.c.variant_id, 0),
                ]
                if update_cols:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=conflict_target,
                        set_=update_cols,
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing(index_elements=conflict_target)
            else:
                stmt = insert(table).values(**clean)
            result = conn.execute(stmt)
            keys = self._normalize_pk_values(table_name, result.inserted_primary_key, clean)
            row = self._fetch_created_row(conn, table_name, user, keys)
        return {"table": table_name, "row": row}

    def update_row(
        self,
        table_name: str,
        user: dict,
        keys: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        table = self._table(table_name)
        self._check_write_permission(table_name, user, action="update")
        clean = self._sanitize_payload(table_name, payload, allow_primary_keys=False)
        if not clean:
            raise HTTPException(status_code=400, detail="No updatable fields supplied")
        with self._engine.begin() as conn:
            # Identity check before update
            existing = self._fetch_row(conn, table_name, user, keys)
            clean = self._enforce_write_scope(conn, table_name, user, clean, existing_row=existing)

            row_filter = self._row_filter(table_name, table, keys)

            # Guard against ambiguous filters: if the caller's keys don't
            # uniquely identify a single row (e.g. PATCH /oltp/inventory with
            # store_id+product_id but no variant_id, which matches every
            # variant row for that product), refuse to update rather than
            # silently clobbering whichever row the DB happens to match.
            # Tables keyed by their full primary key always match 0 or 1 rows
            # here, so this only fires for genuinely ambiguous cases.
            match_count = conn.execute(
                select(func.count()).select_from(table).where(row_filter)
            ).scalar()
            if match_count > 1:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Ambiguous update for {table_name}: filter keys {sorted(keys)} "
                        f"match {match_count} rows. Provide additional keys "
                        f"(e.g. variant_id) to uniquely identify a single row."
                    ),
                )

            # Perform update using the same filter logic
            stmt = update(table).where(row_filter).values(**clean)
            result = conn.execute(stmt)
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"{table_name} record not found")
            
            # Return updated row (using PKs from existing row for precision)
            pk_keys = {pk: existing[pk] for pk in self._meta_for(table_name).primary_keys}
            row = self._fetch_row(conn, table_name, user, pk_keys)
        return {"table": table_name, "row": row}

    def delete_row(self, table_name: str, user: dict, keys: dict[str, Any]) -> dict[str, Any]:
        table = self._table(table_name)
        self._check_write_permission(table_name, user, action="delete")
        with self._engine.begin() as conn:
            # Identity check before delete
            existing = self._fetch_row(conn, table_name, user, keys)
            if table_name == "customer":
                if "is_deleted" not in table.c or "deleted_at" not in table.c:
                    raise HTTPException(
                        status_code=500,
                        detail="Customer soft-delete columns are not available",
                    )
                stmt = (
                    update(table)
                    .where(self._row_filter(table_name, table, keys))
                    .values(is_deleted=True, deleted_at=func.now())
                )
                result = conn.execute(stmt)
                if result.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"{table_name} record not found")
                return {"table": table_name, "deleted": True, "soft_deleted": True, "keys": keys}
            stmt = delete(table).where(self._row_filter(table_name, table, keys))
            try:
                result = conn.execute(stmt)
            except SAIntegrityError as exc:
                orig = str(getattr(exc, "orig", exc))
                if "ForeignKeyViolation" in orig or "foreign key" in orig.lower():
                    raise HTTPException(
                        status_code=409,
                        detail=f"Cannot delete this {table_name}: it is still referenced by other records (e.g. orders or transactions). Remove those first.",
                    )
                raise
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"{table_name} record not found")
        return {"table": table_name, "deleted": True, "keys": keys}

    def _table(self, table_name: str) -> Table:
        table = self._tables.get(table_name)
        if table is None:
            raise HTTPException(status_code=404, detail=f"Unknown OLTP table: {table_name}")
        return table

    def _meta_for(self, table_name: str) -> TableMeta:
        meta = self._table_meta.get(table_name)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Unknown OLTP table: {table_name}")
        return meta

    @staticmethod
    def _filter_condition(column, value):
        # "__null__" lets query-string filters (which are always strings)
        # express IS NULL — needed to target e.g. the base pricing row
        # (variant_id IS NULL) of a product that also has variant rows.
        if isinstance(value, str) and value == "__null__":
            return column.is_(None)
        return column == value

    def _apply_filters(self, stmt, table: Table, filters: dict[str, Any]):
        for key, value in filters.items():
            column = table.columns.get(key)
            if column is None:
                raise HTTPException(status_code=400, detail=f"Unknown filter column: {key}")
            stmt = stmt.where(self._filter_condition(column, value))
        return stmt

    def _row_filter(self, table_name: str, table: Table, keys: dict[str, Any]):
        filter_cols = [k for k in keys if k in table.columns]
        if not filter_cols:
            raise HTTPException(
                status_code=400,
                detail=f"No valid lookup keys provided for {table_name}",
            )
        return and_(*[self._filter_condition(table.c[k], keys[k]) for k in filter_cols])

    def _check_write_permission(self, table_name: str, user: dict, action: str) -> None:
        if user.get("role") == "admin":
            return
        if table_name in ADMIN_ONLY_WRITE_TABLES:
            raise HTTPException(
                status_code=403,
                detail=f"{action.title()} is admin-only for table {table_name}",
            )

    def _sanitize_payload(
        self,
        table_name: str,
        payload: dict[str, Any],
        *,
        allow_primary_keys: bool,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be a JSON object")
        
        meta = self._meta_for(table_name)
        
        # Translate keys using column_map if available
        mapped_payload = {}
        for k, v in payload.items():
            mapped_key = meta.column_map.get(k, k) if meta.column_map else k
            mapped_payload[mapped_key] = v

        allowed = set(meta.columns)
        if not allow_primary_keys:
            allowed -= set(meta.primary_keys)
        
        clean = {k: v for k, v in mapped_payload.items() if k in allowed}
        unknown = sorted(set(mapped_payload) - set(meta.columns))
        
        if unknown:
            # Check if any original keys were unknown (for better error message)
            orig_unknown = sorted(set(payload) - (set(meta.columns) | set(meta.column_map.keys() if meta.column_map else [])))
            raise HTTPException(
                status_code=400,
                detail=f"Unknown columns for {table_name}: {', '.join(orig_unknown or unknown)}",
            )
        return clean

    def _validate_required_create_fields(self, table_name: str, payload: dict[str, Any]) -> None:
        meta = self._meta_for(table_name)
        missing = [col for col in meta.required_create_columns if col not in payload]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields for {table_name}: {', '.join(missing)}",
            )

    def _enforce_write_scope(
        self,
        conn,
        table_name: str,
        user: dict,
        payload: dict[str, Any],
        *,
        existing_row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if user.get("role") == "admin":
            return payload

        scoped_store_id = user.get("store_id")
        if scoped_store_id is None:
            raise HTTPException(status_code=403, detail="No store assigned to this user")

        clean = dict(payload)
        if table_name in DIRECT_STORE_TABLES:
            current = existing_row.get("store_id") if existing_row else None
            incoming = clean.get("store_id", current)
            if incoming is not None and incoming != scoped_store_id:
                raise HTTPException(status_code=403, detail=f"Access denied to table {table_name}")
            clean["store_id"] = scoped_store_id
            return clean

        if table_name == "customer":
            return clean

        parent_table = INDIRECT_SCOPE_TABLES.get(table_name)
        if parent_table:
            self._verify_parent_scope(conn, table_name, clean, existing_row, scoped_store_id)
            return clean

        return clean

    def _verify_parent_scope(
        self,
        conn,
        table_name: str,
        payload: dict[str, Any],
        existing_row: dict[str, Any] | None,
        scoped_store_id: int,
    ) -> None:
        key_column = {
            "order_item": "order_id",
            "payments": "order_id",
            "purchase_items": "purchase_id",
            "product_supplier": "supplier_id",
            "scheme": "supplier_id",
        }[table_name]
        parent_table_name = INDIRECT_SCOPE_TABLES[table_name]
        parent_key = payload.get(key_column, existing_row.get(key_column) if existing_row else None)
        if parent_key is None:
            raise HTTPException(status_code=400, detail=f"{key_column} is required for {table_name}")
        parent = self._tables[parent_table_name]
        parent_pk_column = {
            "orders": "order_id",
            "purchases": "purchase_id",
            "supplier": "supplier_id",
        }[parent_table_name]
        stmt = select(parent.c.store_id).where(parent.c[parent_pk_column] == parent_key)
        parent_store_id = conn.execute(stmt).scalar()
        if parent_store_id != scoped_store_id:
            raise HTTPException(status_code=403, detail=f"Access denied to table {table_name}")

    def _apply_scope(self, stmt, table_name: str, user: dict):
        stmt = self._exclude_soft_deleted(stmt, table_name)
        if user.get("role") == "admin":
            return stmt

        scoped_store_id = user.get("store_id")
        if table_name in GLOBAL_READ_TABLES:
            return stmt
        if table_name in DIRECT_STORE_TABLES:
            table = self._table(table_name)
            return stmt.where(table.c.store_id == scoped_store_id)

        if table_name == "order_item":
            child = self._table(table_name)
            parent = self._table("orders")
            visible = exists(select(literal(1)).select_from(parent).where(
                and_(parent.c.order_id == child.c.order_id, parent.c.store_id == scoped_store_id)
            ))
            return stmt.where(visible)

        if table_name == "payments":
            child = self._table(table_name)
            parent = self._table("orders")
            visible = exists(select(literal(1)).select_from(parent).where(
                and_(parent.c.order_id == child.c.order_id, parent.c.store_id == scoped_store_id)
            ))
            return stmt.where(visible)

        if table_name == "purchase_items":
            child = self._table(table_name)
            parent = self._table("purchases")
            visible = exists(select(literal(1)).select_from(parent).where(
                and_(parent.c.purchase_id == child.c.purchase_id, parent.c.store_id == scoped_store_id)
            ))
            return stmt.where(visible)

        if table_name in {"product_supplier", "scheme"}:
            child = self._table(table_name)
            parent = self._table("supplier")
            fk_column = "supplier_id"
            visible = exists(select(literal(1)).select_from(parent).where(
                and_(parent.c.supplier_id == child.c[fk_column], parent.c.store_id == scoped_store_id)
            ))
            return stmt.where(visible)

        return stmt

    def _exclude_soft_deleted(self, stmt, table_name: str):
        if table_name != "customer":
            return stmt
        table = self._table(table_name)
        if "is_deleted" not in table.c:
            return stmt
        return stmt.where(table.c.is_deleted.is_(False))

    def _fetch_row(self, conn, table_name: str, user: dict, keys: dict[str, Any]) -> dict[str, Any]:
        table = self._table(table_name)
        stmt = select(table).where(self._row_filter(table_name, table, keys))
        stmt = self._apply_scope(stmt, table_name, user)
        row = conn.execute(stmt).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail=f"{table_name} record not found")
        return dict(row)

    def _fetch_created_row(self, conn, table_name: str, user: dict, keys: dict[str, Any]) -> dict[str, Any]:
        if table_name == "customer" and user.get("role") != "admin":
            table = self._table(table_name)
            row = conn.execute(
                select(table).where(self._row_filter(table_name, table, keys))
            ).mappings().first()
            if not row:
                raise HTTPException(status_code=404, detail=f"{table_name} record not found")
            return dict(row)
        return self._fetch_row(conn, table_name, user, keys)

    def _normalize_pk_values(
        self,
        table_name: str,
        inserted_primary_key: tuple[Any, ...],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        meta = self._meta_for(table_name)
        keys: dict[str, Any] = {}
        for index, pk in enumerate(meta.primary_keys):
            inserted = inserted_primary_key[index] if index < len(inserted_primary_key) else None
            keys[pk] = inserted if inserted is not None else payload.get(pk)
        return keys


def _load_oltp_metadata(engine: Engine):
    metadata = MetaData(schema="kirana_oltp")
    metadata.reflect(bind=engine)
    tables = {table.name: table for table in metadata.tables.values()}
    inspector = inspect(engine)
    metas: dict[str, TableMeta] = {}
    for name, table in tables.items():
        columns = tuple(col.name for col in table.columns)
        pk = tuple(col.name for col in table.primary_key.columns)
        fks = []
        for fk in inspector.get_foreign_keys(name, schema="kirana_oltp"):
            for idx, constrained in enumerate(fk.get("constrained_columns", [])):
                fks.append({
                    "column": constrained,
                    "foreign_table": fk.get("referred_table"),
                    "foreign_column": (fk.get("referred_columns") or [None])[idx],
                })
        required = tuple(
            col.name for col in table.columns
            if (not col.nullable and col.default is None and col.server_default is None and not col.primary_key)
        )
        required_create = tuple(
            col.name for col in table.columns
            if (
                not col.nullable
                and col.default is None
                and col.server_default is None
                and not (
                    col.primary_key
                    and getattr(col, "autoincrement", None) in (True, "auto")
                    and col.name.endswith("_id")
                )
            )
        )
        read_scope = "global" if name in GLOBAL_READ_TABLES else "store"
        write_scope = "admin_only" if name in ADMIN_ONLY_WRITE_TABLES else ("store" if name != "customer" else "shared")
        metas[name] = TableMeta(
            name=name,
            primary_keys=pk,
            columns=columns,
            required_columns=required,
            required_create_columns=required_create,
            foreign_keys=tuple(fks),
            has_store_id="store_id" in columns,
            read_scope=read_scope,
            write_scope=write_scope,
            column_map=OltpRepository.COLUMN_MAPPINGS.get(name, {}),
        )
    return metadata, tables, metas


