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
        committed_at  TIMESTAMPTZ,
        finished_at   TIMESTAMPTZ,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE kirana_oltp.vision_session ADD COLUMN IF NOT EXISTS committed_at TIMESTAMPTZ",
    "ALTER TABLE kirana_oltp.vision_session ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ",
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
        image_index          SMALLINT NOT NULL DEFAULT 0,
        detector_source      VARCHAR(16) NOT NULL DEFAULT 'gemini',
        corrected_product_id BIGINT,
        corrected_at         TIMESTAMPTZ,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE kirana_oltp.vision_item ADD COLUMN IF NOT EXISTS image_index SMALLINT NOT NULL DEFAULT 0",
    "ALTER TABLE kirana_oltp.vision_item "
    "ADD COLUMN IF NOT EXISTS detector_source VARCHAR(16) NOT NULL DEFAULT 'gemini'",
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


def _det(name, product_id, count, *, unknown=False, score=0.9, source="gemini"):
    d = DetectedProduct(raw_name=name, count=count, x1=0, y1=0, x2=1, y2=1,
                        visible_text=name.upper(), source=source)
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
    assert s["finished_at"] is not None  # failures still stamp latency


def test_finalize_stamps_finished_at(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    assert repo.get_session(engine, store_id, sid)["finished_at"] is None
    repo.finalize_session(engine, sid, 1, 1, 0)
    assert repo.get_session(engine, store_id, sid)["finished_at"] is not None


def test_detector_source_is_persisted(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, sid, [_det("Tata Salt 1kg", 2, 5, source="yolo"),
                                  _det("Maggi Noodles", 3, 4)])  # default gemini
    by_name = {i["gemini_name"]: i for i in repo.get_items(engine, store_id, sid)}
    assert by_name["Tata Salt 1kg"]["detector_source"] == "yolo"
    assert by_name["Maggi Noodles"]["detector_source"] == "gemini"


def test_analytics_aggregates_sessions_and_items(vision_db):
    engine, store_id = vision_db
    # One done morning scan: 2 matched (1 yolo, 1 gemini) + 2 unknowns.
    m = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, m, [
        _det("Tata Salt 1kg", 2, 5, source="yolo"),
        _det("Maggi Noodles", 3, 4),
        _det("Mystery item", None, 1, unknown=True, score=0.0),
        _det("Other mystery", None, 2, unknown=True, score=0.0),
    ])
    repo.finalize_session(engine, m, 2, 12, 2)
    # Correct one unknown → it leaves the unknown backlog and counts as corrected.
    fixed_id = next(i["item_id"] for i in repo.get_items(engine, store_id, m)
                    if i["gemini_name"] == "Mystery item")
    repo.correct_item(engine, store_id, fixed_id, 42)
    # And one failed evening scan.
    e = repo.create_session(engine, store_id, "evening", "[]")
    repo.fail_session(engine, e, "boom")

    a = repo.get_analytics(engine, store_id, days=7)
    assert a["sessions"]["total"] == 2
    assert a["sessions"]["done"] == 1 and a["sessions"]["failed"] == 1
    assert a["sessions"]["morning"] == 1 and a["sessions"]["evening"] == 1
    assert a["sessions"]["avg_processing_seconds"] is not None
    assert a["detections"]["items"] == 4 and a["detections"]["units"] == 12
    assert a["detections"]["unknown_items"] == 1   # the uncorrected one
    assert a["detections"]["corrected_items"] == 1
    assert a["detections"]["unknown_rate"] == pytest.approx(1 / 4, abs=1e-3)
    assert a["detections"]["correction_rate"] == pytest.approx(1 / 4, abs=1e-3)
    split = {d["detector_source"]: d for d in a["detectors"]}
    assert split["yolo"]["items"] == 1 and split["yolo"]["units"] == 5
    assert split["gemini"]["items"] == 3
    assert len(a["daily"]) == 1 and a["daily"][0]["sessions"] == 2
    # Only the still-unresolved unknown surfaces as a label to train next.
    assert [u["raw_name"] for u in a["top_unknowns"]] == ["Other mystery"]


def test_analytics_is_store_scoped_and_empty_safe(vision_db):
    engine, store_id = vision_db
    sid = repo.create_session(engine, store_id, "morning", "[]")
    repo.save_items(engine, sid, [_det("Tata Salt 1kg", 2, 5)])
    repo.finalize_session(engine, sid, 1, 5, 0)

    other = repo.get_analytics(engine, store_id + 999, days=30)
    assert other["sessions"]["total"] == 0
    assert other["detections"]["items"] == 0
    assert other["detections"]["unknown_rate"] == 0.0  # no division by zero
    assert other["detectors"] == [] and other["daily"] == []


def test_analytics_fleet_wide_spans_all_stores(vision_db):
    engine, store_a = vision_db
    with engine.begin() as conn:
        store_b = int(conn.execute(text(
            "INSERT INTO kirana_oltp.store (name) VALUES ('Second Store') "
            "RETURNING store_id"
        )).scalar())

    a = repo.create_session(engine, store_a, "morning", "[]")
    repo.save_items(engine, a, [_det("Tata Salt 1kg", 2, 5, source="yolo")])
    repo.finalize_session(engine, a, 1, 5, 0)
    b = repo.create_session(engine, store_b, "onboarding", "[]")
    repo.save_items(engine, b, [_det("Maggi Noodles", 3, 4)])
    repo.finalize_session(engine, b, 1, 4, 0)

    # store_id=None ⇒ fleet-wide: both stores' sessions counted.
    fleet = repo.get_analytics(engine, None, days=7)
    assert fleet["sessions"]["total"] == 2
    assert fleet["sessions"]["morning"] == 1 and fleet["sessions"]["onboarding"] == 1
    assert fleet["detections"]["items"] == 2 and fleet["detections"]["units"] == 9
    # Scoping to one store still narrows it.
    assert repo.get_analytics(engine, store_a, days=7)["detections"]["units"] == 5


def test_store_breakdown_one_row_per_active_store(vision_db):
    engine, store_a = vision_db
    with engine.begin() as conn:
        store_b = int(conn.execute(text(
            "INSERT INTO kirana_oltp.store (name) VALUES ('Quiet Store') "
            "RETURNING store_id"
        )).scalar())

    # store_a: 2 sessions, some unknowns + a yolo detection; store_b: none.
    s1 = repo.create_session(engine, store_a, "morning", "[]")
    repo.save_items(engine, s1, [_det("Tata Salt 1kg", 2, 5, source="yolo"),
                                 _det("Mystery", None, 1, unknown=True, score=0.0)])
    repo.finalize_session(engine, s1, 1, 6, 1)
    s2 = repo.create_session(engine, store_a, "evening", "[]")
    repo.save_items(engine, s2, [_det("Maggi Noodles", 3, 2)])
    repo.finalize_session(engine, s2, 1, 2, 0)

    rows = repo.get_store_breakdown(engine, days=30)
    assert len(rows) == 1  # only the active store appears
    r = rows[0]
    assert r["store_id"] == store_a and r["store_name"] == "Vision Test Store"
    assert r["sessions"] == 2 and r["items"] == 3 and r["units"] == 8
    assert r["unknown_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert r["yolo_share"] == pytest.approx(1 / 3, abs=1e-3)
    assert r["last_scan"] is not None
    assert store_b not in {row["store_id"] for row in rows}
