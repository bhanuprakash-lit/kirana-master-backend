"""
migrate_to_azure.py — copy all local kirana_oltp / kirana_olap data to Azure DB.

- Reads Azure creds from .env (DATABASE_URL) in repo root
- Local DB: localhost / lit_db / postgres / 123456 (hardcoded defaults, override via LOCAL_* env vars)
- Uses PostgreSQL COPY for speed; TRUNCATE ... RESTART IDENTITY CASCADE clears Azure first
- Safe to re-run: always does a full replace

Usage:
    python db_generation/migrate_to_azure.py
"""
import io
import os
import sys
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_ENV)

# Local (source)
LOCAL_HOST = os.getenv("LOCAL_DB_HOST", "localhost")
LOCAL_PORT = os.getenv("LOCAL_DB_PORT", "5432")
LOCAL_USER = os.getenv("LOCAL_DB_USER", "postgres")
LOCAL_PASS = os.getenv("LOCAL_DB_PASSWORD", "123456")
LOCAL_NAME = os.getenv("LOCAL_DB_NAME", "lit_db")

# Azure (target) — parsed from DATABASE_URL in .env
_url = os.getenv("DATABASE_URL", "")
if not _url:
    sys.exit("ERROR: DATABASE_URL not set in .env")
_p = urlparse(_url.replace("postgresql+psycopg2://", "postgresql://"))
AZ_HOST = _p.hostname
AZ_PORT = str(_p.port or 5432)
AZ_USER = _p.username
AZ_PASS = _p.password or ""
AZ_NAME = (_p.path or "").lstrip("/")

# ── Table copy order (parents before children so FK constraints are satisfied) ─

OLTP_TABLES = [
    "store",
    "category",
    "customer",
    "users",
    "product",
    "supplier",
    "product_supplier",
    "pricing",
    "promotion",
    "inventory",
    "inventory_movements",
    "inventory_snapshots",
    "orders",
    "order_item",
    "payments",
    "purchases",
    "purchase_items",
    "user_sessions",
    "user_prefs",
    "user_fcm_tokens",
    "app_activity",
    "issue_report",
    "cashflow_requests",
    "basket",
    "basket_item",
    "ai_usage",
    "ai_credits",
    "kpi_tier_config",
    "subscription",
    "intelligence_log",
    "cart_session",
    "store_association",
    "khata",
    "khata_payments",
    "referral_campaigns",
    "referral_tokens",
    "referrals",
    "referral_vouchers",
    "footfall",
    "scheme",
    "scheme_claim",
    "calendar",
    "inventory_batch",
    "shelf_planogram",
    "opex",
    "return_to_vendor",
    "marketing_spend",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def local_conn():
    return psycopg2.connect(
        host=LOCAL_HOST, port=LOCAL_PORT,
        dbname=LOCAL_NAME, user=LOCAL_USER, password=LOCAL_PASS,
    )

def azure_conn():
    return psycopg2.connect(
        host=AZ_HOST, port=AZ_PORT,
        dbname=AZ_NAME, user=AZ_USER, password=AZ_PASS,
        sslmode="require",
    )

def row_count(cur, schema, table):
    cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
    return cur.fetchone()[0]

def table_exists(cur, schema, table):
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name=%s",
        (schema, table),
    )
    return cur.fetchone() is not None

def get_columns(cur, schema, table):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, table),
    )
    return [r[0] for r in cur.fetchall()]

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print(f"Source : {LOCAL_USER}@{LOCAL_HOST}/{LOCAL_NAME}")
    print(f"Target : {AZ_USER}@{AZ_HOST}/{AZ_NAME}")
    print()

    src = local_conn()
    dst = azure_conn()
    src.autocommit = True   # read-only, no transaction needed
    dst.autocommit = False

    src_cur = src.cursor()
    dst_cur = dst.cursor()

    # ── Step 1: count local rows ──────────────────────────────────────────────
    print("Scanning local tables …")
    to_copy = []   # (schema, table, count)
    for table in OLTP_TABLES:
        if not table_exists(src_cur, "kirana_oltp", table):
            print(f"  SKIP  kirana_oltp.{table} (not in local DB)")
            continue
        n = row_count(src_cur, "kirana_oltp", table)
        to_copy.append(("kirana_oltp", table, n))
        if n:
            print(f"  {n:>6}  kirana_oltp.{table}")

    print()

    # ── Step 2: truncate all Azure OLTP tables (CASCADE, restart seqs) ────────
    print("Truncating Azure tables …")
    # Truncate in reverse order to avoid FK conflicts (or use CASCADE)
    all_qualified = ", ".join(
        f"kirana_oltp.{t}" for t in reversed(OLTP_TABLES)
        if table_exists(dst_cur, "kirana_oltp", t)
    )
    if all_qualified:
        dst_cur.execute(f"TRUNCATE {all_qualified} RESTART IDENTITY CASCADE")
    dst.commit()
    print("  Done.\n")

    # ── Step 3: disable triggers that would block bulk copy ──────────────────
    print("Disabling triggers …")
    dst_cur.execute("ALTER TABLE kirana_oltp.order_item DISABLE TRIGGER USER")
    dst.commit()
    print("  Done.\n")

    # ── Step 4: copy table by table ───────────────────────────────────────────
    print("Copying data …")
    total_rows = 0
    for schema, table, n in to_copy:
        if n == 0:
            print(f"  SKIP  {schema}.{table} (empty)")
            continue

        if not table_exists(dst_cur, schema, table):
            print(f"  SKIP  {schema}.{table} (not in Azure DB)")
            continue

        # Use only columns that exist in BOTH source and target (handles patched extras)
        src_cols = get_columns(src_cur, schema, table)
        dst_cols = set(get_columns(dst_cur, schema, table))
        cols = [c for c in src_cols if c in dst_cols]
        col_list = ", ".join(f'"{c}"' for c in cols)

        buf = io.BytesIO()
        src_cur.copy_expert(
            f'COPY (SELECT {col_list} FROM {schema}.{table}) TO STDOUT (FORMAT TEXT)', buf
        )
        buf.seek(0)
        dst_cur.copy_expert(
            f'COPY {schema}.{table} ({col_list}) FROM STDIN (FORMAT TEXT)', buf
        )
        dst.commit()
        total_rows += n
        print(f"  OK    {schema}.{table:30s}  {n} rows")

    # ── Re-enable triggers ────────────────────────────────────────────────────
    dst_cur.execute("ALTER TABLE kirana_oltp.order_item ENABLE TRIGGER USER")
    dst.commit()

    # ── Step 5: reset sequences to max(id)+1 so new inserts don't collide ─────
    print("\nResetting sequences …")
    dst_cur.execute("""
        SELECT sequence_schema, sequence_name
        FROM information_schema.sequences
        WHERE sequence_schema IN ('kirana_oltp', 'kirana_olap')
    """)
    seqs = dst_cur.fetchall()
    for seq_schema, seq_name in seqs:
        # Find the table/column this sequence serves
        dst_cur.execute("""
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE column_default LIKE %s
              AND table_schema IN ('kirana_oltp','kirana_olap')
        """, (f"%{seq_name}%",))
        row = dst_cur.fetchone()
        if row:
            tschema, tname, col = row
            dst_cur.execute(
                f"SELECT setval('{seq_schema}.{seq_name}', "
                f"COALESCE((SELECT MAX({col}) FROM {tschema}.{tname}), 1))"
            )
    dst.commit()
    print("  Done.\n")

    src.close()
    dst.close()
    print(f"Migration complete — {total_rows} rows copied across {len([t for t in to_copy if t[2]])} tables.")

if __name__ == "__main__":
    run()
