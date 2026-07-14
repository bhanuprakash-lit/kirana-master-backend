"""DB-backed tests for the counter repository (marked `db`).

Covers the price attachment (store's active pricing row), the day summary with
total value, and the scan history — the features that turn counter tallies into
money the owner can read back later.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from vision import counter_repository as repo

pytestmark = pytest.mark.db

_DDL = [
    """CREATE TABLE IF NOT EXISTS kirana_oltp.store (
        store_id BIGSERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
        location VARCHAR(255), is_deleted BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.product (
        product_id BIGSERIAL PRIMARY KEY, name VARCHAR(255))""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.pricing (
        pricing_id BIGSERIAL PRIMARY KEY, product_id BIGINT NOT NULL,
        store_id BIGINT NOT NULL, price NUMERIC NOT NULL,
        valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(), valid_to TIMESTAMPTZ)""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.counter_session (
        session_id BIGSERIAL PRIMARY KEY,
        store_id BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
        client_uid VARCHAR(64) NOT NULL,
        session_date DATE NOT NULL DEFAULT CURRENT_DATE,
        device_label VARCHAR(120), source VARCHAR(30) NOT NULL DEFAULT 'on_device',
        started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
        total_units INT NOT NULL DEFAULT 0, total_skus INT NOT NULL DEFAULT 0,
        unknown_count INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (store_id, client_uid))""",
    """CREATE TABLE IF NOT EXISTS kirana_oltp.counter_item (
        item_id BIGSERIAL PRIMARY KEY,
        session_id BIGINT NOT NULL
            REFERENCES kirana_oltp.counter_session(session_id) ON DELETE CASCADE,
        product_id BIGINT, class_name VARCHAR(255) NOT NULL,
        display_name VARCHAR(255), qty INT NOT NULL DEFAULT 1,
        match_score REAL NOT NULL DEFAULT 0, is_unknown BOOLEAN NOT NULL DEFAULT TRUE,
        avg_confidence REAL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
]


@pytest.fixture
def engine(db_engine):
    with db_engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
        conn.execute(text(
            "TRUNCATE TABLE kirana_oltp.counter_item, kirana_oltp.counter_session, "
            "kirana_oltp.pricing, kirana_oltp.product, kirana_oltp.store "
            "RESTART IDENTITY CASCADE"))
        conn.execute(text(
            "INSERT INTO kirana_oltp.store (store_id, name) VALUES (1,'S1'), (2,'S2')"))
        conn.execute(text(
            "INSERT INTO kirana_oltp.product (product_id, name) "
            "VALUES (101,'Red Label Tea'), (102,'Lux Soap')"))
        # Store 1 prices both; store 2 prices only the tea (and differently).
        # An expired price row must NOT win over the active one.
        conn.execute(text("""
            INSERT INTO kirana_oltp.pricing (product_id, store_id, price, valid_from, valid_to)
            VALUES (101, 1, 999, NOW() - interval '30 days', NOW() - interval '1 day'),
                   (101, 1, 55,  NOW() - interval '1 day',  NULL),
                   (102, 1, 30,  NOW(), NULL),
                   (101, 2, 60,  NOW(), NULL)
        """))
    return db_engine


def _sync(engine, store_id, uid, items, sdate=None):
    return repo.upsert_session(engine, store_id, uid, sdate, None, None, None, items)


def test_attach_prices_uses_active_store_price(engine):
    items = [
        {"product_id": 101, "qty": 2},
        {"product_id": 102, "qty": 1},
        {"product_id": None, "qty": 3},  # unknown class: no price
    ]
    repo.attach_prices(engine, 1, items)
    assert items[0]["price"] == 55 and items[0]["line_value"] == 110
    assert items[1]["price"] == 30 and items[1]["line_value"] == 30
    assert items[2]["price"] is None and items[2]["line_value"] is None

    # Store 2 sees its own price for tea and none for soap.
    items2 = [{"product_id": 101, "qty": 1}, {"product_id": 102, "qty": 1}]
    repo.attach_prices(engine, 2, items2)
    assert items2[0]["price"] == 60
    assert items2[1]["price"] is None


def test_summary_totals_value(engine):
    _sync(engine, 1, "uid-a", [
        {"class_name": "red_label_tea", "qty": 2, "product_id": 101,
         "display_name": "Red Label Tea", "match_score": 0.9, "is_unknown": False},
        {"class_name": "mystery_snack", "qty": 1, "product_id": None,
         "display_name": None, "match_score": 0.0, "is_unknown": True},
    ])
    s = repo.get_summary(engine, 1)
    assert s["total_units"] == 3 and s["total_skus"] == 1
    assert s["total_value"] == 110  # only the priced tea contributes
    tea = next(i for i in s["items"] if i["product_id"] == 101)
    assert tea["price"] == 55 and tea["line_value"] == 110
    unknown = next(i for i in s["items"] if i["product_id"] is None)
    assert unknown["display_name"] == "Mystery Snack"  # prettified class


def test_history_lists_sessions_with_priced_items(engine):
    _sync(engine, 1, "uid-1", [
        {"class_name": "red_label_tea", "qty": 1, "product_id": 101,
         "display_name": "Red Label Tea", "match_score": 0.9, "is_unknown": False},
    ])
    _sync(engine, 1, "uid-2", [
        {"class_name": "lux_soap", "qty": 4, "product_id": 102,
         "display_name": "Lux Soap", "match_score": 0.9, "is_unknown": False},
    ])
    _sync(engine, 2, "uid-3", [  # other store — must not leak in
        {"class_name": "red_label_tea", "qty": 9, "product_id": 101,
         "display_name": "Red Label Tea", "match_score": 0.9, "is_unknown": False},
    ])

    hist = repo.get_history(engine, 1, days=7)
    assert len(hist) == 2
    newest = hist[0]  # newest first
    assert newest["total_units"] == 4 and newest["total_value"] == 120
    assert newest["items"][0]["price"] == 30
    oldest = hist[1]
    assert oldest["total_value"] == 55

    # re-sync of the same client_uid replaces, never duplicates
    _sync(engine, 1, "uid-2", [
        {"class_name": "lux_soap", "qty": 2, "product_id": 102,
         "display_name": "Lux Soap", "match_score": 0.9, "is_unknown": False},
    ])
    hist = repo.get_history(engine, 1, days=7)
    assert len(hist) == 2
    assert sum(s["total_units"] for s in hist) == 3
