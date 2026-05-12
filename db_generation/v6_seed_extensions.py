"""
v6 Synthetic Seed — fills every new v6 table/column with deterministic
data so all 46 KPIs return real numbers.

Idempotent where possible (uses ON CONFLICT or WHERE-NOT-EXISTS). Safe to
re-run; existing rows are not duplicated.

Run after `v6_schema_extensions.py`:
    python db_generation/v6_seed_extensions.py
"""
import psycopg2
import psycopg2.extras as _extras
import random
from datetime import datetime, date, timedelta

DB_NAME = "lit_db"
DB_USER = "postgres"
DB_PASSWORD = "123456"
DB_HOST = "localhost"
DB_PORT = "5432"

random.seed(42)
TODAY = date.today()


def _connect():
    return psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        host=DB_HOST, port=DB_PORT,
    )


# ── 1. is_private_label flag on product ─────────────────────────────────────
def seed_private_label(cur):
    cur.execute("SELECT product_id FROM kirana_oltp.product ORDER BY product_id")
    pids = [r[0] for r in cur.fetchall()]
    # ~15% of SKUs are private label
    sample = random.sample(pids, k=max(1, int(len(pids) * 0.15)))
    cur.execute(
        "UPDATE kirana_oltp.product SET is_private_label = (product_id = ANY(%s))",
        (sample,),
    )
    print(f"  private_label flagged on {len(sample)}/{len(pids)} products")


# ── 2. order_channel on existing orders ─────────────────────────────────────
def seed_order_channels(cur):
    # Distribute existing orders: 70% walk_in, 18% whatsapp, 12% delivery
    cur.execute("SELECT order_id FROM kirana_oltp.orders WHERE order_channel IS NULL OR order_channel = 'walk_in'")
    order_ids = [r[0] for r in cur.fetchall()]
    if not order_ids:
        print("  (order_channel already populated)")
        return

    rng = random.Random(7)
    updates = []
    for oid in order_ids:
        roll = rng.random()
        if roll < 0.18:
            ch = "whatsapp"
        elif roll < 0.30:
            ch = "delivery"
        else:
            ch = "walk_in"
        updates.append((ch, oid))

    _extras.execute_batch(
        cur,
        "UPDATE kirana_oltp.orders SET order_channel = %s WHERE order_id = %s",
        updates,
        page_size=500,
    )
    print(f"  order_channel set on {len(order_ids)} orders (~18%% whatsapp, ~12%% delivery)")


# ── 3. Customer household_size ──────────────────────────────────────────────
def seed_household_size(cur):
    cur.execute("SELECT customer_id FROM kirana_oltp.customer ORDER BY customer_id")
    cids = [r[0] for r in cur.fetchall()]
    rng = random.Random(11)
    rows = [(rng.choices([2, 3, 4, 5, 6], weights=[10, 25, 35, 20, 10])[0], cid)
            for cid in cids]
    _extras.execute_batch(
        cur,
        "UPDATE kirana_oltp.customer SET household_size = %s WHERE customer_id = %s",
        rows, page_size=500,
    )
    print(f"  household_size set on {len(rows)} customers")


# ── 4. Footfall — hourly per store, 60 days ─────────────────────────────────
def seed_footfall(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    rng = random.Random(13)
    rows = []
    for sid in stores:
        # Per-store baseline so different stores show different traffic
        baseline = 8 + (sid * 3) % 7
        for d in range(60):
            day = TODAY - timedelta(days=d)
            is_weekend = day.weekday() >= 5
            for hr in range(8, 22):  # 8am - 9pm
                # Morning rush (8-10) and evening rush (18-20) get a lift
                rush = 1.6 if hr in (8, 9, 18, 19) else 1.0
                wk = 1.3 if is_weekend else 1.0
                visitors = max(0, int(rng.gauss(baseline * rush * wk, 3)))
                rows.append((sid, datetime.combine(day, datetime.min.time()).replace(hour=hr),
                             hr, visitors))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.footfall (store_id, ts, hour, visitors)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (store_id, ts) DO NOTHING
        """,
        rows, page_size=2000,
    )
    print(f"  footfall: {len(rows)} hourly rows across {len(stores)} stores")


# ── 5. Festival calendar — one year either side of TODAY ────────────────────
def seed_calendar(cur):
    festivals = [
        # (month, day, name, weight)
        (1, 14,  "Pongal / Sankranti", 1.6),
        (1, 26,  "Republic Day",       1.2),
        (3, 8,   "Holi",                1.8),
        (4, 14,  "Baisakhi / Tamil NY", 1.4),
        (5, 1,   "May Day",             1.1),
        (8, 15,  "Independence Day",    1.3),
        (8, 26,  "Raksha Bandhan",      1.5),
        (9, 7,   "Janmashtami",         1.4),
        (9, 18,  "Ganesh Chaturthi",    1.7),
        (10, 2,  "Gandhi Jayanti",      1.1),
        (10, 24, "Dussehra",            1.6),
        (11, 12, "Diwali",              2.4),
        (12, 25, "Christmas",           1.3),
        (12, 31, "New Year Eve",        1.5),
        # Festival eves get a smaller boost
    ]
    rng = random.Random(17)

    rows = []
    # Walk +/- 365 days from today and stamp festivals + weekends
    for offset in range(-365, 366):
        d = TODAY + timedelta(days=offset)
        festival = None
        weight = 1.0
        for fm, fd, name, w in festivals:
            if d.month == fm and d.day == fd:
                festival = name
                weight = w
                break
            # Eve = day before
            if (d + timedelta(days=1)).month == fm and (d + timedelta(days=1)).day == fd:
                festival = f"{name} eve"
                weight = round(0.5 * w + 0.6, 2)
                break
        if festival is None and d.weekday() >= 5:
            weight = 1.15  # weekend boost
        rows.append((d, festival, weight))

    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.calendar (cal_date, festival, weight)
        VALUES (%s, %s, %s)
        ON CONFLICT (cal_date) DO UPDATE SET
            festival = EXCLUDED.festival,
            weight = EXCLUDED.weight
        """,
        rows, page_size=500,
    )
    print(f"  calendar: {len(rows)} day-rows seeded")


# ── 6. Khata / Udhar (credit ledger) ────────────────────────────────────────
def seed_khata(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    cur.execute("""
        SELECT customer_id FROM kirana_oltp.customer ORDER BY customer_id LIMIT 100
    """)
    cust_ids = [r[0] for r in cur.fetchall()]

    rng = random.Random(19)
    rows = []
    for sid in stores:
        # ~25 customers have outstanding khata per store
        sample = rng.sample(cust_ids, k=min(25, len(cust_ids)))
        for cid in sample:
            issue = TODAY - timedelta(days=rng.randint(5, 90))
            due = issue + timedelta(days=rng.choice([15, 30, 45]))
            amount = round(rng.uniform(150, 4500), 2)
            paid_pct = rng.choice([0, 0, 0, 0.3, 0.5, 1.0])  # most are unpaid
            paid = round(amount * paid_pct, 2)
            if paid >= amount:
                status = "settled"
            elif due < TODAY:
                status = "overdue"
            else:
                status = "open"
            rows.append((cid, sid, None, amount, paid, issue, due, status))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.khata
            (customer_id, store_id, order_id, amount, amount_paid, issue_date, due_date, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        rows, page_size=500,
    )
    print(f"  khata: {len(rows)} entries across {len(stores)} stores")


# ── 7. Inventory batches (perishables) ──────────────────────────────────────
def seed_inventory_batches(cur):
    cur.execute("""
        SELECT i.store_id, i.product_id, i.quantity, p.is_perishable
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p ON p.product_id = i.product_id
    """)
    invs = cur.fetchall()
    rng = random.Random(23)

    rows = []
    for sid, pid, qty, perishable in invs:
        # Two batches per SKU (older + newer); perishables get tighter expiries
        batches = 2
        remaining = max(int(qty), 1)
        for b in range(batches):
            qb = max(1, remaining // (batches - b))
            remaining -= qb
            mfd = TODAY - timedelta(days=rng.randint(5, 30))
            if perishable:
                exp = TODAY + timedelta(days=rng.randint(-2, 10))
            else:
                exp = TODAY + timedelta(days=rng.randint(60, 300))
            wasted = 0
            recovered = 0
            mark = 0.0
            # If expired (or near-expired), simulate some markdown / waste history
            if exp < TODAY:
                wasted = rng.randint(0, qb)
                qb_remaining = qb - wasted
                recovered = rng.randint(0, qb_remaining)
                mark = rng.choice([20, 30, 40, 50])
            elif (exp - TODAY).days <= 5:
                mark = rng.choice([0, 10, 15, 20])
                recovered = rng.randint(0, max(qb // 4, 1))
            batch_no = f"B{sid}{pid}{b}"
            rows.append((sid, pid, batch_no, mfd, exp, qb, mark, recovered, wasted))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.inventory_batch
            (store_id, product_id, batch_no, manufactured_date, expiry_date,
             qty_in_stock, markdown_pct, recovered_units, wasted_units)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (store_id, product_id, batch_no) DO NOTHING
        """,
        rows, page_size=2000,
    )
    print(f"  inventory_batch: {len(rows)} rows")


# ── 8. Shelf planogram ──────────────────────────────────────────────────────
def seed_shelf_planogram(cur):
    cur.execute("""
        SELECT i.store_id, i.product_id, p.category_id
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p ON p.product_id = i.product_id
    """)
    rows_in = cur.fetchall()
    rng = random.Random(29)
    rows = []
    for sid, pid, cat in rows_in:
        shelf_id = f"S{sid}-{cat}-{(pid % 6)+1}"
        sq_ft = round(rng.uniform(0.4, 2.0), 2)
        eye = rng.random() < 0.20  # ~20% of SKUs at eye level
        rows.append((sid, pid, shelf_id, sq_ft, eye))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.shelf_planogram (store_id, product_id, shelf_id, sq_ft, eye_level)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (store_id, product_id) DO NOTHING
        """,
        rows, page_size=2000,
    )
    print(f"  shelf_planogram: {len(rows)} rows")


# ── 9. Opex (rent / electricity / staff) — last 6 months ────────────────────
def seed_opex(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    rng = random.Random(31)
    rows = []
    for sid in stores:
        for m in range(6):
            month_start = (TODAY.replace(day=1) - timedelta(days=30 * m)).replace(day=1)
            elec = round(rng.uniform(8000, 14000), 2)
            rent = round(rng.uniform(20000, 45000), 2)
            staff = round(rng.uniform(15000, 35000), 2)
            other = round(rng.uniform(2000, 6000), 2)
            rows.append((sid, month_start, elec, rent, staff, other))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.opex (store_id, month_start, electricity, rent, staff, other)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (store_id, month_start) DO NOTHING
        """,
        rows, page_size=200,
    )
    print(f"  opex: {len(rows)} rows ({len(stores)} stores x 6 months)")


# ── 10. requested_qty backfill on purchase_items ────────────────────────────
def seed_requested_qty(cur):
    cur.execute("""
        SELECT purchase_item_id, quantity
        FROM kirana_oltp.purchase_items
        WHERE requested_qty IS NULL
    """)
    rows_in = cur.fetchall()
    rng = random.Random(37)
    updates = []
    for pid, qty in rows_in:
        # ~80% fully fulfilled, ~15% short by 5-15%, ~5% short by 20-40%
        roll = rng.random()
        if roll < 0.80:
            req = qty
        elif roll < 0.95:
            req = int(round(qty / rng.uniform(0.85, 0.95)))
        else:
            req = int(round(qty / rng.uniform(0.60, 0.80)))
        updates.append((max(req, qty), pid))
    _extras.execute_batch(
        cur,
        "UPDATE kirana_oltp.purchase_items SET requested_qty = %s WHERE purchase_item_id = %s",
        updates, page_size=2000,
    )
    print(f"  requested_qty backfilled on {len(updates)} purchase_items")


# ── 11. Return-to-Vendor ────────────────────────────────────────────────────
def seed_return_to_vendor(cur):
    cur.execute("""
        SELECT i.store_id, i.product_id, ps.supplier_id, ps.cost_price, p.is_perishable
        FROM kirana_oltp.inventory i
        JOIN kirana_oltp.product p ON p.product_id = i.product_id
        JOIN kirana_oltp.product_supplier ps ON ps.product_id = i.product_id
    """)
    rng = random.Random(41)
    rows = []
    for sid, pid, supp, cost, perishable in rng.sample(cur.fetchall(), 80):
        return_date = TODAY - timedelta(days=rng.randint(1, 60))
        qty = rng.randint(1, 8)
        rec_pct = round(rng.uniform(40, 90), 2)
        amt = round(float(cost or 0) * qty * (rec_pct / 100), 2)
        reason = rng.choice(["damaged", "expired", "unsold"]) if perishable else rng.choice(["damaged", "unsold"])
        rows.append((sid, supp, pid, return_date, qty, float(cost or 0), rec_pct, amt, reason))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.return_to_vendor
            (store_id, supplier_id, product_id, return_date, qty_returned,
             unit_cost, recovery_pct, amount_recovered, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        rows, page_size=200,
    )
    print(f"  return_to_vendor: {len(rows)} rows")


# ── 12. Subscription tiers ──────────────────────────────────────────────────
def seed_subscription(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    tiers = [("basic", 599), ("pro", 1499), ("enterprise", 3499)]
    rng = random.Random(43)
    rows = []
    for sid in stores:
        # Some stores upgraded over time → multiple subscription rows
        started = TODAY - timedelta(days=rng.randint(180, 540))
        first_tier, first_price = rng.choice(tiers[:2])
        upgraded_at = started + timedelta(days=rng.randint(60, 200))
        if upgraded_at < TODAY and rng.random() < 0.6:
            # Past tier, then current
            rows.append((sid, first_tier, first_price,
                         datetime.combine(started, datetime.min.time()),
                         datetime.combine(upgraded_at, datetime.min.time()),
                         (upgraded_at - started).days // 30,
                         round(rng.uniform(2000, 8000), 2)))
            cur_tier, cur_price = rng.choice([t for t in tiers if t[0] != first_tier])
            rows.append((sid, cur_tier, cur_price,
                         datetime.combine(upgraded_at, datetime.min.time()),
                         None,
                         (TODAY - upgraded_at).days // 30,
                         round(rng.uniform(8000, 30000), 2)))
        else:
            rows.append((sid, first_tier, first_price,
                         datetime.combine(started, datetime.min.time()),
                         None,
                         (TODAY - started).days // 30,
                         round(rng.uniform(5000, 25000), 2)))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.subscription
            (store_id, tier, monthly_price, started_at, ended_at, renewal_count, savings_to_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        rows, page_size=200,
    )
    print(f"  subscription: {len(rows)} rows ({len(stores)} stores)")


# ── 13. CRM brand deals ─────────────────────────────────────────────────────
def seed_crm_deals(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    brands = ["HUL", "P&G", "Nestle", "ITC", "Britannia", "Parle", "Amul", "Dabur", "Marico", "Tata Consumer"]
    rng = random.Random(47)
    rows = []
    for sid in stores:
        for _ in range(rng.randint(4, 7)):
            opened = TODAY - timedelta(days=rng.randint(10, 240))
            stage = rng.choices(["lead", "proposal", "won", "lost"], weights=[3, 3, 5, 2])[0]
            closed = (opened + timedelta(days=rng.randint(15, 90))) if stage in ("won", "lost") else None
            value = round(rng.uniform(8000, 60000), 2)
            rows.append((sid, rng.choice(brands), rng.choice(["co_invest", "trade_promo", "sampling"]),
                         value, stage, opened, closed))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.crm_deals
            (store_id, brand_name, deal_type, deal_value, stage, opened_at, closed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        rows, page_size=200,
    )
    print(f"  crm_deals: {len(rows)} rows")


# ── 14. Marketing spend ─────────────────────────────────────────────────────
def seed_marketing_spend(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    channels = [
        ("whatsapp", 0.20, 25),
        ("flyer",    0.50, 15),
        ("hoarding", 0.80, 8),
        ("digital",  0.30, 18),
    ]
    rng = random.Random(53)
    rows = []
    for sid in stores:
        for d in range(0, 90, 7):  # weekly buckets, last 90 days
            spend_date = TODAY - timedelta(days=d)
            for ch, _ratio, base_attrib in channels:
                amt = round(rng.uniform(500, 5000), 2)
                cust = max(0, int(rng.gauss(base_attrib, 5)))
                rows.append((sid, spend_date, ch, amt, cust))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.marketing_spend
            (store_id, spend_date, channel, amount, attributed_customers)
        VALUES (%s, %s, %s, %s, %s)
        """,
        rows, page_size=500,
    )
    print(f"  marketing_spend: {len(rows)} rows")


# ── 15. AP/AR aging snapshots ───────────────────────────────────────────────
def seed_ap_ar_aging(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    rng = random.Random(59)
    rows = []
    for sid in stores:
        for d_back in (0, 7, 14, 30, 60, 90):
            snap = TODAY - timedelta(days=d_back)
            ap0 = round(rng.uniform(15000, 60000), 2)
            ap1 = round(rng.uniform(5000, 20000), 2)
            ap2 = round(rng.uniform(0, 8000), 2)
            ar0 = round(rng.uniform(8000, 25000), 2)
            ar1 = round(rng.uniform(2000, 12000), 2)
            ar2 = round(rng.uniform(0, 6000), 2)
            inv = round(rng.uniform(80000, 220000), 2)
            rows.append((sid, snap, ap0, ap1, ap2, ar0, ar1, ar2, inv))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.ap_ar_aging
            (store_id, snapshot_date, ap_0_30, ap_31_60, ap_61_plus,
             ar_0_30, ar_31_60, ar_61_plus, avg_inventory_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (store_id, snapshot_date) DO NOTHING
        """,
        rows, page_size=200,
    )
    print(f"  ap_ar_aging: {len(rows)} rows")


# ── 16. Process events (manual vs automated) ────────────────────────────────
def seed_process_events(cur):
    cur.execute("SELECT store_id FROM kirana_oltp.store WHERE COALESCE(is_deleted, FALSE) = FALSE")
    stores = [r[0] for r in cur.fetchall()]
    processes = [
        ("reorder",     0.55),  # fraction automated
        ("bill",        0.85),
        ("scheme_claim", 0.30),
        ("reconcile",   0.40),
    ]
    rng = random.Random(61)
    rows = []
    for sid in stores:
        for d in range(60):
            ts_date = TODAY - timedelta(days=d)
            for proc, auto_ratio in processes:
                count = rng.randint(2, 8)
                for _ in range(count):
                    mode = "automated" if rng.random() < auto_ratio else "manual"
                    success = rng.random() > (0.05 if mode == "automated" else 0.10)
                    latency = rng.randint(120, 800) if mode == "automated" else rng.randint(2000, 18000)
                    ts = datetime.combine(ts_date, datetime.min.time()) + timedelta(
                        hours=rng.randint(8, 21), minutes=rng.randint(0, 59))
                    rows.append((sid, ts, proc, mode, latency, success))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.process_events
            (store_id, ts, process, mode, latency_ms, success)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        rows, page_size=2000,
    )
    print(f"  process_events: {len(rows)} rows")


# ── 17. Schemes + claims ────────────────────────────────────────────────────
def seed_schemes(cur):
    cur.execute("SELECT supplier_id, store_id FROM kirana_oltp.supplier")
    suppliers = cur.fetchall()
    cur.execute("SELECT product_id FROM kirana_oltp.product ORDER BY product_id")
    pids = [r[0] for r in cur.fetchall()]
    rng = random.Random(67)

    scheme_rows = []
    for sup_id, _store in suppliers:
        for _ in range(rng.randint(1, 3)):
            pid = rng.choice(pids)
            stype = rng.choice(["bulk_discount", "free_qty", "cashback"])
            value = rng.choice([3, 5, 8, 10, 12])
            min_qty = rng.choice([5, 10, 20, 50])
            sd = TODAY - timedelta(days=rng.randint(0, 60))
            ed = sd + timedelta(days=rng.randint(20, 90))
            scheme_rows.append((sup_id, pid, f"{stype.replace('_', ' ').title()} {value}{'%' if stype != 'cashback' else '₹'}",
                               stype, value, min_qty, sd, ed))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.scheme
            (supplier_id, product_id, name, scheme_type, value, min_qty, start_date, end_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        scheme_rows, page_size=200,
    )
    print(f"  scheme: {len(scheme_rows)} rows")

    # Generate claims: ~70% of eligible schemes claimed, the rest "missed"
    cur.execute("""
        SELECT s.scheme_id, s.supplier_id, s.value, s.start_date, s.end_date,
               sup.store_id
        FROM kirana_oltp.scheme s
        JOIN kirana_oltp.supplier sup ON sup.supplier_id = s.supplier_id
    """)
    schemes = cur.fetchall()
    claim_rows = []
    for scheme_id, _sup, value, sd, ed, store_id in schemes:
        roll = rng.random()
        # Spread 0..3 claims per scheme
        for _ in range(rng.randint(0, 3)):
            cdate = sd + timedelta(days=rng.randint(0, max((ed - sd).days, 1)))
            if cdate > TODAY:
                continue
            saved = round(rng.uniform(50, 800), 2)
            status = "claimed" if roll < 0.7 else "missed"
            claim_rows.append((scheme_id, store_id, None, cdate, saved, status))
    _extras.execute_batch(
        cur,
        """
        INSERT INTO kirana_oltp.scheme_claim
            (scheme_id, store_id, purchase_id, claim_date, amount_saved, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        claim_rows, page_size=500,
    )
    print(f"  scheme_claim: {len(claim_rows)} rows")


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    conn = _connect()
    cur = conn.cursor()
    print("Seeding v6 extension tables (deterministic, idempotent where possible)...\n")
    seed_private_label(cur);          conn.commit()
    seed_order_channels(cur);         conn.commit()
    seed_household_size(cur);         conn.commit()
    seed_footfall(cur);               conn.commit()
    seed_calendar(cur);               conn.commit()
    seed_khata(cur);                  conn.commit()
    seed_inventory_batches(cur);      conn.commit()
    seed_shelf_planogram(cur);        conn.commit()
    seed_opex(cur);                   conn.commit()
    seed_requested_qty(cur);          conn.commit()
    seed_return_to_vendor(cur);       conn.commit()
    seed_subscription(cur);           conn.commit()
    seed_crm_deals(cur);              conn.commit()
    seed_marketing_spend(cur);        conn.commit()
    seed_ap_ar_aging(cur);            conn.commit()
    seed_process_events(cur);         conn.commit()
    seed_schemes(cur);                conn.commit()
    cur.close()
    conn.close()
    print("\nv6 seed complete.")


if __name__ == "__main__":
    main()
