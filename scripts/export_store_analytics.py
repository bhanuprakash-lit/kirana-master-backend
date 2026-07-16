#!/usr/bin/env python3
"""
Export store-wise analytics (orders/sales, inventory, customers, footfall,
CRM deals) from the Azure DB to an .xlsx workbook -- one summary sheet plus a
per-store detail row, used to sanity-check the crm_demo_seed.py run.

Usage:
    python scripts/export_store_analytics.py [output_path.xlsx]
"""

import os
import sys

import pandas as pd
import psycopg2

DB_URL = os.environ.get("AZURE_DB_URL") or os.environ.get("DATABASE_URL")
if not DB_URL:
    raise SystemExit("Set AZURE_DB_URL (or DATABASE_URL) — no hardcoded DB credentials.")

OUT_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "store_analytics.xlsx"
)

QUERY = """
SELECT
  s.store_id,
  s.name,
  s.location,
  s.region,
  s.store_type,
  s.created_at::date AS registered_on,
  (SELECT count(*) FROM kirana_oltp.orders o WHERE o.store_id = s.store_id) AS orders,
  (SELECT COALESCE(SUM(o.total_amount),0) FROM kirana_oltp.orders o WHERE o.store_id = s.store_id) AS sales_total,
  (SELECT MAX(o.order_date)::date FROM kirana_oltp.orders o WHERE o.store_id = s.store_id) AS last_order,
  (SELECT count(*) FROM kirana_oltp.inventory inv WHERE inv.store_id = s.store_id) AS inventory_rows,
  (SELECT COALESCE(SUM(inv.quantity),0) FROM kirana_oltp.inventory inv WHERE inv.store_id = s.store_id) AS inventory_qty,
  (SELECT count(*) FROM kirana_oltp.customer c WHERE c.store_id = s.store_id) AS customers,
  (SELECT count(*) FROM kirana_oltp.footfall f WHERE f.store_id = s.store_id) AS footfall_days,
  (SELECT COALESCE(SUM(f.visitors),0) FROM kirana_oltp.footfall f WHERE f.store_id = s.store_id) AS footfall_visitors,
  (SELECT count(*) FROM kirana_oltp.crm_deals cd WHERE cd.store_id = s.store_id) AS crm_deals,
  (SELECT COALESCE(SUM(cd.deal_value),0) FROM kirana_oltp.crm_deals cd WHERE cd.store_id = s.store_id) AS crm_deal_value
FROM kirana_oltp.store s
ORDER BY s.store_id;
"""


def main():
    conn = psycopg2.connect(DB_URL)
    conn.set_client_encoding("UTF8")
    df = pd.read_sql(QUERY, conn)
    conn.close()

    manifest_path = os.path.join(os.path.dirname(__file__), "crm_demo_manifest.json")
    seeded_ids = set()
    if os.path.exists(manifest_path):
        import json

        with open(manifest_path) as f:
            seeded_ids = {int(sid) for sid in json.load(f)["target_stores"]}
    is_seeded = df["store_id"].isin(seeded_ids)

    date_cols = ["registered_on", "last_order"]

    with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="All stores", index=False)
        df[is_seeded].to_excel(writer, sheet_name="Demo Activity Stores", index=False)
        for sheet_df, sheet_name in [
            (df, "All stores"),
            (df[is_seeded], "Demo Activity Stores"),
        ]:
            ws = writer.sheets[sheet_name]
            for i, col in enumerate(sheet_df.columns, start=1):
                width = max(12, min(40, int(sheet_df[col].astype(str).str.len().max() or 10) + 2))
                letter = ws.cell(row=1, column=i).column_letter
                ws.column_dimensions[letter].width = width
                if col in date_cols:
                    for cell in ws[letter][1:]:
                        cell.number_format = "DD-MM-YYYY"

    print(f"Wrote {len(df)} stores ({len(seeded_ids)} seeded) to {OUT_PATH}")


if __name__ == "__main__":
    main()
