"""
Drop tables that no feature, KPI, ML model, or API route references.

Audit (2026-05-31): these three kirana_oltp tables had ZERO references across
the codebase outside of the schema builder and the OLTP whitelist — no KPI in
the registry reads them, no ML model uses them, no route writes them, and no
INSERT path exists. They were part of the original enterprise schema but were
never wired to anything.

  - ap_ar_aging      (AP/AR aging snapshots — never computed or read)
  - process_events   (manual-vs-automated process telemetry — never emitted)
  - crm_deals        (brand/CRM deal pipeline — never used)

This script is intentionally NOT run automatically by ensure_full_schema.py.
Run it deliberately against the target DB once you have confirmed the tables
hold no data you need:

    python db_generation/drop_unused_tables.py            # dry-run (lists row counts)
    python db_generation/drop_unused_tables.py --apply    # actually drops

Connection comes from the same env vars the other db_generation scripts use
(DATABASE_URL / PG* vars), so it works against local and Azure.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load the backend's .env (this script lives in db_generation/, one level down).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))

DEAD_TABLES = ["ap_ar_aging", "process_events", "crm_deals"]


def _engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in environment or backend .env")
    return create_engine(url)


def main(apply: bool) -> None:
    eng = _engine()
    with eng.begin() as conn:
        for t in DEAD_TABLES:
            exists = conn.execute(text(
                "SELECT to_regclass(:q) IS NOT NULL"
            ), {"q": f"kirana_oltp.{t}"}).scalar()
            if not exists:
                print(f"  {t}: not present — skipping")
                continue
            count = conn.execute(text(f"SELECT COUNT(*) FROM kirana_oltp.{t}")).scalar()
            if apply:
                conn.execute(text(f"DROP TABLE IF EXISTS kirana_oltp.{t} CASCADE"))
                print(f"  {t}: DROPPED (had {count} rows)")
            else:
                print(f"  {t}: would drop ({count} rows) — pass --apply to execute")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
