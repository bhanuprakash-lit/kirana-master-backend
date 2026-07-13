"""DB-backed tests for the first-class racks repository (marked `db`).

Covers label normalization ("A1" == "a 1" == "A-1"), the upsert that must
UPDATE instead of duplicating for NULL-variant products (the old 4-column
UNIQUE never fired on NULLs), rack CRUD guards, merge semantics, and store
scoping. The one-time racks_first_class_v1 backfill lives in
KiranaRepository._ensure_schema and is exercised on deploy.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from kirana.repositories.stocklocations import (
    StockLocationsRepositoryMixin,
    rack_display_label,
    rack_label_key,
)

pytestmark = pytest.mark.db

# Minimal schema in its post-migration shape (self-contained, like the
# callcenter tests).
_DDL = [
    """CREATE TABLE IF NOT EXISTS kirana_oltp.store (
        store_id BIGSERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
        location VARCHAR(255), is_deleted BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.product (
        product_id BIGSERIAL PRIMARY KEY, name VARCHAR(255))""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.rack (
        rack_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        label VARCHAR(60) NOT NULL, label_key VARCHAR(60) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (store_id, label_key))""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.inventory_location (
        id BIGSERIAL PRIMARY KEY,
        store_id BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id),
        product_id BIGINT NOT NULL REFERENCES kirana_oltp.product(product_id),
        variant_id BIGINT, rack VARCHAR(60) NOT NULL,
        rack_id BIGINT REFERENCES kirana_oltp.rack(rack_id),
        quantity NUMERIC NOT NULL DEFAULT 0)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS inventory_location_placement_uniq
        ON kirana_oltp.inventory_location
            (store_id, product_id, COALESCE(variant_id, 0), rack_id)""",
]


class _Repo(StockLocationsRepositoryMixin):
    def __init__(self, engine):
        self._engine = engine

    def _conn(self):
        return self._engine.connect()


@pytest.fixture
def repo(db_engine):
    with db_engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
        conn.execute(text(
            "TRUNCATE TABLE kirana_oltp.inventory_location, kirana_oltp.rack, "
            "kirana_oltp.product, kirana_oltp.store RESTART IDENTITY CASCADE"))
        conn.execute(text(
            "INSERT INTO kirana_oltp.store (store_id, name) VALUES (1,'S1'), (2,'S2')"))
        conn.execute(text(
            "INSERT INTO kirana_oltp.product (product_id, name) "
            "VALUES (101,'Rice'), (102,'Dal')"))
        conn.execute(text(
            "SELECT setval(pg_get_serial_sequence('kirana_oltp.store','store_id'), 2)"))
        conn.execute(text(
            "SELECT setval(pg_get_serial_sequence('kirana_oltp.product','product_id'), 102)"))
    return _Repo(db_engine)


def test_label_normalization():
    assert rack_label_key("a 1") == rack_label_key("A-1") == rack_label_key("A1") == "A1"
    assert rack_label_key("shelf 1") == rack_label_key("SHELF1") == "SHELF1"
    assert rack_label_key(" -- ") == ""
    assert rack_display_label("  top   shelf ") == "TOP SHELF"


def test_create_rack_folds_variants(repo):
    first = repo.create_rack(1, "a 1")
    assert first["created"] is True and first["label"] == "A 1"
    again = repo.create_rack(1, "A-1")
    assert again["created"] is False and again["rack_id"] == first["rack_id"]
    assert repo.create_rack(1, " -- ") is None
    # same key in another store is a different rack
    other = repo.create_rack(2, "A1")
    assert other["created"] is True and other["rack_id"] != first["rack_id"]


def test_upsert_updates_instead_of_duplicating(repo):
    row = repo.upsert_location(1, 101, "B2", 5)
    # NULL-variant re-upsert must hit the same row (the old schema's bug)
    row2 = repo.upsert_location(1, 101, "b-2", 8)
    assert row2["id"] == row["id"] and float(row2["quantity"]) == 8
    assert len(repo.list_locations(1, 101)) == 1
    # by rack_id as well
    row3 = repo.upsert_location(1, 101, None, 9, rack_id=row["rack_id"])
    assert row3["id"] == row["id"] and float(row3["quantity"]) == 9
    # unknown rack_id refused
    assert repo.upsert_location(1, 101, None, 1, rack_id=99999) is None


def test_upsert_new_label_creates_canonical_rack(repo):
    row = repo.upsert_location(1, 102, "  top   shelf ", 3)
    assert row["rack"] == "TOP SHELF" and row["rack_id"] is not None
    racks = {r["label"]: r for r in repo.list_racks(1)}
    assert racks["TOP SHELF"]["items"] == 1


def test_list_racks_includes_empty(repo):
    repo.create_rack(1, "C9")
    racks = {r["label"]: r for r in repo.list_racks(1)}
    assert racks["C9"]["items"] == 0


def test_rename_guards_and_syncs(repo):
    a = repo.create_rack(1, "A1")
    b = repo.create_rack(1, "B1")
    repo.upsert_location(1, 101, None, 4, rack_id=b["rack_id"])
    assert repo.rename_rack(1, b["rack_id"], "a-1") == "conflict"
    assert repo.rename_rack(1, 99999, "X") is None
    renamed = repo.rename_rack(1, b["rack_id"], "b 2")
    assert renamed["label"] == "B 2"
    # denormalized rack string on placements follows the rename
    assert repo.list_locations(1, 101)[0]["rack"] == "B 2"
    assert a["rack_id"] != b["rack_id"]


def test_delete_only_when_empty(repo):
    r = repo.create_rack(1, "D1")
    repo.upsert_location(1, 101, None, 2, rack_id=r["rack_id"])
    assert repo.delete_rack(1, r["rack_id"]) == "not_empty"
    assert repo.delete_rack(1, 99999) == "not_found"
    loc_id = repo.list_locations(1, 101)[0]["id"]
    assert repo.delete_location(loc_id, 1) is True
    assert repo.delete_rack(1, r["rack_id"]) == "deleted"


def test_merge_sums_collisions_and_moves_rest(repo):
    src = repo.create_rack(1, "S")
    tgt = repo.create_rack(1, "T")
    repo.upsert_location(1, 101, None, 5, rack_id=src["rack_id"])  # collides
    repo.upsert_location(1, 101, None, 7, rack_id=tgt["rack_id"])
    repo.upsert_location(1, 102, None, 3, rack_id=src["rack_id"])  # moves
    res = repo.merge_racks(1, src["rack_id"], tgt["rack_id"])
    assert res == {"rack_id": tgt["rack_id"], "label": "T"}
    locs = {r["product_id"]: r for r in repo.list_all_locations(1)}
    assert float(locs[101]["quantity"]) == 12 and locs[101]["rack"] == "T"
    assert locs[102]["rack_id"] == tgt["rack_id"]
    assert "S" not in {r["label"] for r in repo.list_racks(1)}
    # guards
    assert repo.merge_racks(1, tgt["rack_id"], tgt["rack_id"]) is None
    assert repo.merge_racks(1, 99999, tgt["rack_id"]) is None


def test_store_scoping(repo):
    mine = repo.create_rack(1, "Z1")
    theirs = repo.create_rack(2, "Z1")
    repo.upsert_location(2, 101, "Z1", 6)
    # store 1 can't touch store 2's rack
    assert repo.upsert_location(1, 101, None, 1, rack_id=theirs["rack_id"]) is None
    assert repo.rename_rack(1, theirs["rack_id"], "Q") is None
    assert repo.delete_rack(1, theirs["rack_id"]) == "not_found"
    assert {r["label"] for r in repo.list_racks(1)} == {"Z1"}
    assert repo.list_racks(2)[0]["items"] == 1
    assert mine["rack_id"] != theirs["rack_id"]
