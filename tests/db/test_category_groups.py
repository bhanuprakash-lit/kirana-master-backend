"""DB-backed tests for per-store category groups (G7, marked `db`).

The behaviour that matters here is the template/fork model: a store with no
groups of its own reads the per-vertical defaults, and the first edit forks a
private copy rather than mutating the shared template (which every other store
on that vertical is reading).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from kirana.repositories import category_groups as repo

pytestmark = pytest.mark.db

_DDL = [
    """CREATE TABLE IF NOT EXISTS kirana_oltp.store (
        store_id BIGSERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
        vertical_code TEXT, is_deleted BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.category (
        category_id BIGSERIAL PRIMARY KEY,
        parent_category_id BIGINT REFERENCES kirana_oltp.category(category_id),
        name VARCHAR(150) NOT NULL, vertical_code TEXT)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.product (
        product_id BIGSERIAL PRIMARY KEY,
        category_id BIGINT REFERENCES kirana_oltp.category(category_id),
        name VARCHAR(255))""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.inventory (
        inventory_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        product_id BIGINT REFERENCES kirana_oltp.product(product_id),
        variant_id BIGINT, quantity INT DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.category_group (
        group_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT REFERENCES kirana_oltp.store(store_id),
        vertical_code TEXT NOT NULL, name VARCHAR(120) NOT NULL,
        seed_key TEXT, sort_order INT NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.category_group_member (
        group_id BIGINT NOT NULL
            REFERENCES kirana_oltp.category_group(group_id) ON DELETE CASCADE,
        category_id BIGINT NOT NULL
            REFERENCES kirana_oltp.category(category_id) ON DELETE CASCADE,
        PRIMARY KEY (group_id, category_id))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_category_group_store_name
        ON kirana_oltp.category_group (store_id, lower(name))
        WHERE store_id IS NOT NULL""",
    # Other suites share this database and may have created these tables in an
    # older shape, in which case the CREATEs above were skipped. Add the columns
    # this suite depends on rather than assuming ownership of the tables.
    "ALTER TABLE kirana_oltp.store ADD COLUMN IF NOT EXISTS vertical_code TEXT",
    "ALTER TABLE kirana_oltp.category ADD COLUMN IF NOT EXISTS vertical_code TEXT",
    "ALTER TABLE kirana_oltp.category ADD COLUMN IF NOT EXISTS "
    "parent_category_id BIGINT",
    "ALTER TABLE kirana_oltp.product ADD COLUMN IF NOT EXISTS category_id BIGINT",
    "ALTER TABLE kirana_oltp.inventory ADD COLUMN IF NOT EXISTS variant_id BIGINT",
]


@pytest.fixture
def engine(db_engine):
    with db_engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
        conn.execute(text(
            "TRUNCATE TABLE kirana_oltp.category_group_member, "
            "kirana_oltp.category_group, kirana_oltp.inventory, "
            "kirana_oltp.product, kirana_oltp.category, kirana_oltp.store "
            "RESTART IDENTITY CASCADE"))
        conn.execute(text(
            "INSERT INTO kirana_oltp.store (store_id, name, vertical_code) "
            "VALUES (1,'Kirana A','grocery'), (2,'Kirana B','grocery')"))
        conn.execute(text("""
            INSERT INTO kirana_oltp.category (category_id, name, vertical_code)
            VALUES (10,'Chips & Crisps','grocery'), (11,'Biscuits','grocery'),
                   (12,'Detergent','grocery'), (13,'Milk','grocery')
        """))
        conn.execute(text("""
            INSERT INTO kirana_oltp.product (product_id, category_id, name)
            VALUES (100,10,'Lays'), (101,10,'Kurkure'), (102,11,'Parle-G'),
                   (103,12,'Surf'), (104,13,'Amul Milk')
        """))
        # Store 1 stocks chips + biscuits + milk; store 2 stocks only detergent.
        conn.execute(text("""
            INSERT INTO kirana_oltp.inventory (store_id, product_id, quantity)
            VALUES (1,100,5), (1,101,5), (1,102,5), (1,104,5), (2,103,5)
        """))
        # The per-vertical template both stores start on.
        conn.execute(text("""
            INSERT INTO kirana_oltp.category_group
                (group_id, store_id, vertical_code, name, seed_key, sort_order)
            VALUES (1,NULL,'grocery','Kids & treats','kids_treats',10),
                   (2,NULL,'grocery','Household needs','household_needs',20)
        """))
        conn.execute(text("""
            INSERT INTO kirana_oltp.category_group_member (group_id, category_id)
            VALUES (1,10), (1,11), (2,12)
        """))
        conn.execute(text(
            "SELECT setval(pg_get_serial_sequence("
            "'kirana_oltp.category_group','group_id'), 2)"))
    return db_engine


def test_store_without_groups_reads_the_vertical_template(engine):
    with engine.connect() as conn:
        assert repo.has_own_groups(conn, 1) is False
        groups = {g["name"]: g for g in repo.list_groups(conn, 1)}
    assert set(groups) == {"Kids & treats", "Household needs"}
    assert groups["Kids & treats"]["seed_key"] == "kids_treats"
    assert groups["Kids & treats"]["is_custom"] is False


def test_counts_are_scoped_to_what_the_store_stocks(engine):
    """A store must not see the whole global catalog's counts."""
    with engine.connect() as conn:
        s1 = {g["name"]: g for g in repo.list_groups(conn, 1)}
        s2 = {g["name"]: g for g in repo.list_groups(conn, 2)}
    # Store 1 stocks 2 chips + 1 biscuit, and no detergent.
    assert s1["Kids & treats"]["stocked_products"] == 3
    assert s1["Household needs"]["stocked_products"] == 0
    # Store 2 stocks only detergent, from the same shared categories.
    assert s2["Kids & treats"]["stocked_products"] == 0
    assert s2["Household needs"]["stocked_products"] == 1


def test_ungrouped_surfaces_stock_no_group_covers(engine):
    with engine.connect() as conn:
        ungrouped = repo.ungrouped_categories(conn, 1)
    # Milk is stocked by store 1 but in no group — it must not vanish.
    assert [(u["name"], u["stocked_products"]) for u in ungrouped] == [("Milk", 1)]


def test_rename_forks_and_leaves_other_stores_untouched(engine):
    with engine.begin() as conn:
        gid = [g for g in repo.list_groups(conn, 1)
               if g["seed_key"] == "kids_treats"][0]["group_id"]
        repo.rename_group(conn, 1, gid, "Children")

    with engine.connect() as conn:
        s1 = {g["name"]: g for g in repo.list_groups(conn, 1)}
        s2 = {g["name"]: g for g in repo.list_groups(conn, 2)}
        assert repo.has_own_groups(conn, 1) is True
        assert repo.has_own_groups(conn, 2) is False

    # Store 1 sees its own wording, and keeps the group it never touched.
    assert set(s1) == {"Children", "Household needs"}
    assert s1["Children"]["is_custom"] is True
    # seed_key cleared: the app must stop substituting a translation.
    assert s1["Children"]["seed_key"] is None
    # Store 2 still reads the untouched template.
    assert set(s2) == {"Kids & treats", "Household needs"}


def test_membership_replace_and_many_to_many(engine):
    with engine.begin() as conn:
        gid = [g for g in repo.list_groups(conn, 1)
               if g["seed_key"] == "kids_treats"][0]["group_id"]
        # Chips legitimately belong to two groups at once.
        other = repo.create_group(conn, 1, "Evening snacks", [10])
        repo.set_members(conn, 1, gid, [11, 13])

    with engine.connect() as conn:
        groups = {g["name"]: g for g in repo.list_groups(conn, 1)}
    assert {c["name"] for c in groups["Kids & treats"]["categories"]} == {
        "Biscuits", "Milk"}
    assert {c["name"] for c in groups["Evening snacks"]["categories"]} == {
        "Chips & Crisps"}
    assert other > 0


def test_a_store_cannot_touch_another_stores_group(engine):
    with engine.begin() as conn:
        gid = repo.create_group(conn, 1, "Private", [10])
    with engine.begin() as conn:
        assert repo.rename_group(conn, 2, gid, "Hijacked") is False
        assert repo.delete_group(conn, 2, gid) is False
        assert repo.set_members(conn, 2, gid, [13]) is False
    with engine.connect() as conn:
        assert "Private" in {g["name"] for g in repo.list_groups(conn, 1)}


def test_reset_returns_the_store_to_defaults(engine):
    with engine.begin() as conn:
        gid = [g for g in repo.list_groups(conn, 1)
               if g["seed_key"] == "kids_treats"][0]["group_id"]
        repo.rename_group(conn, 1, gid, "Children")
    with engine.begin() as conn:
        repo.reset_to_defaults(conn, 1)
    with engine.connect() as conn:
        assert repo.has_own_groups(conn, 1) is False
        assert {g["name"] for g in repo.list_groups(conn, 1)} == {
            "Kids & treats", "Household needs"}


def test_deleting_a_group_does_not_delete_categories(engine):
    with engine.begin() as conn:
        gid = [g for g in repo.list_groups(conn, 1)
               if g["seed_key"] == "kids_treats"][0]["group_id"]
        assert repo.delete_group(conn, 1, gid) is True
    with engine.connect() as conn:
        still_there = conn.execute(text(
            "SELECT COUNT(*) FROM kirana_oltp.category")).scalar()
        # The categories are shared master data — a store dropping its own
        # grouping must never remove them.
        assert still_there == 4
        names = {u["name"] for u in repo.ungrouped_categories(conn, 1)}
    assert {"Chips & Crisps", "Biscuits", "Milk"} <= names
