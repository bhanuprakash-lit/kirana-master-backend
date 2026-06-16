"""DB-backed tests for vision.repository (marked `db` — run only with a real
Postgres via TEST_DATABASE_URL; CI provides one).

Exercises the full session lifecycle: create → save items → finalize → list →
sales delta → owner correction, all store-scoped.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from vision import repository as repo
from vision.analyzer import DetectedProduct

pytestmark = pytest.mark.db


_VISION_DDL = [
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.vision_session (
        session_id    BIGSERIAL PRIMARY KEY,
        store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
        session_type  VARCHAR(20) NOT NULL,
        session_date  DATE NOT NULL DEFAULT CURRENT_DATE,
        image_url     TEXT,
        status        VARCHAR(20) NOT NULL DEFAULT 'pending',
        total_skus    INT NOT NULL DEFAULT 0,
        total_units   INT NOT NULL DEFAULT 0,
        unknown_count INT NOT NULL DEFAULT 0,
        error         TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kirana_oltp.vision_item (
        item_id              BIGSERIAL PRIMARY KEY,
        session_id           BIGINT NOT NULL
                                 REFERENCES kirana_oltp.vision_session(session_id) ON DELETE CASCADE,
        sku_id               VARCHAR(64),
        product_id           BIGINT,
        display_name         VARCHAR(255),
        gemini_name          VARCHAR(255) NOT NULL,
        visible_text         TEXT,
        count                INT NOT NULL DEFAULT 1,
        match_score          REAL NOT NULL DEFAULT 0,
        is_unknown           BOOLEAN NOT NULL DEFAULT TRUE,
        bbox_json            TEXT,
        corrected_product_id BIGINT,
        corrected_at         TIMESTAMPTZ,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


@pytest.fixture
def vision_db(db_engine):
    with db_engine.begin() as conn:
        for ddl in _VISION_DDL:
            conn.execute(text(ddl))
        conn.execute(text(
            "TRUNCATE TABLE kirana_oltp.vision_item, kirana_oltp.vision_session, "
            "kirana_oltp.store RESTART IDENTITY CASCADE"
        ))
        store_id = conn.execute(text(
            "INSERT INTO kirana_oltp.store (name) VALUES ('Vision Test Store') "
            "RETURNING store_id"
        )).scalar()
    yield db_engine, int(store_id)
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE kirana_oltp.vision_item, kirana_oltp.vision_session, "
            "kirana_oltp.store RESTART IDENTITY CASCADE"
        ))


def _det(name, product_id, count, *, unknown=False, score=0.9):
    d = DetectedProduct(raw_name=name, count=count, x1=0, y1=0, x2=1, y2=1,
                        visible_text=name.upper())
    d.product_id = product_id
    d.display_name = name if product_id is not None else None
    d.is_unknown = unknown
    d.match_score = score
    return d


def test_create_save_finalize_and_list(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    assert isinstance(sid, int)

    dets = [_det("Tata Salt 1kg", 2, 5), _det("Maggi Noodles", 3, 4),
            _det("Mystery item", None, 1, unknown=True, score=0.1)]
    repo.save_items(engine, sid, dets)
    repo.finalize_session(engine, sid, total_skus=2, total_units=10, unknown_count=1)

    sessions = repo.get_sessions(engine, store_id)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["status"] == "done"
    assert s["total_units"] == 10 and s["total_skus"] == 2 and s["unknown_count"] == 1

    items = repo.get_items(engine, store_id, sid)
    assert len(items) == 3
    assert {i["gemini_name"] for i in items} == {"Tata Salt 1kg", "Maggi Noodles", "Mystery item"}


def test_get_items_is_store_scoped(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, sid, [_det("Tata Salt 1kg", 2, 1)])
    # Wrong store sees nothing.
    assert repo.get_items(engine, store_id + 999, sid) == []
    assert repo.get_session(engine, store_id + 999, sid) is None
    assert repo.get_session(engine, store_id, sid) is not None


def test_sales_delta_is_morning_minus_evening(vision_db):
    engine, store_id = vision_db
    m = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, m, [_det("Tata Salt 1kg", 2, 5), _det("Maggi Noodles", 3, 3)])
    repo.finalize_session(engine, m, 2, 8, 0)

    e = repo.create_session(engine, store_id, "evening", "[]")
    repo.save_items(engine, e, [_det("Tata Salt 1kg", 2, 2)])  # 3 sold; Maggi all sold
    repo.finalize_session(engine, e, 1, 2, 0)

    deltas = {d["product_id"]: d for d in repo.compute_sales_delta(engine, store_id)}
    assert deltas[2]["sold"] == 3   # 5 morning - 2 evening
    assert deltas[3]["sold"] == 3   # 3 morning - 0 evening (sold out)
    assert deltas[2]["morning_count"] == 5 and deltas[2]["evening_count"] == 2


def test_correction_updates_item_and_clears_unknown(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, sid, [_det("Mystery", None, 1, unknown=True, score=0.0)])
    item_id = repo.get_items(engine, store_id, sid)[0]["item_id"]

    assert repo.correct_item(engine, store_id, item_id, 42) is True
    fixed = repo.get_items(engine, store_id, sid)[0]
    assert fixed["corrected_product_id"] == 42
    assert fixed["is_unknown"] is False

    # Clearing the correction sets it back to NULL.
    assert repo.correct_item(engine, store_id, item_id, None) is True
    assert repo.get_items(engine, store_id, sid)[0]["corrected_product_id"] is None


def test_correction_is_store_scoped(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, sid, [_det("Tata Salt", 2, 1)])
    item_id = repo.get_items(engine, store_id, sid)[0]["item_id"]
    # A different store cannot correct this item.
    assert repo.correct_item(engine, store_id + 999, item_id, 5) is False


def test_failed_session_records_error(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "evening", "[]")
    repo.fail_session(engine, sid, "boom")
    s = repo.get_session(engine, store_id, sid)
    assert s["status"] == "failed"
    assert s["error"] == "boom"
