# API Reference

Full API surface for the Kirana Master Backend ([main.py](main.py)).

## Base

- App root: `http://<host>:9000`
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

---

## Auth Rules

### Kirana AI + KPI + WhatsApp + OLTP routes

Pass **one** of:

| Header | Value | Role granted |
|--------|-------|--------------|
| `X-API-Key` | `<KIRANA_API_KEY>` | `admin` — full access, no store scope |
| `Authorization` | `Bearer <token>` | `store_owner` — auto-scoped to their `store_id` |

Admin (`X-API-Key`) can access all stores and all data.  
Store owners can only read/write their own store's data.

Token is obtained from `POST /kirana/auth/login` → `access_token`.

### POS routes (`/pos/*` except `/pos/token`)

```
Authorization: Bearer <pos_jwt>
```

JWT is obtained from `POST /pos/token`.

### WhatsApp webhook (Meta)

`GET /whatsapp/webhook` and `POST /whatsapp/webhook` are intentionally public (Meta cannot send auth headers). All other WhatsApp endpoints require the same auth as Kirana routes.

---

## Root Endpoints

### `GET /`

Returns:

```json
{
  "service": "Kirana Master Backend",
  "version": "1.0.0",
  "database": "lit_db (single)",
  "modules": {
    "kirana_ai": "/kirana",
    "pos": "/pos",
    "oltp": "/oltp",
    "whatsapp": "/whatsapp",
    "kpis": "/kirana/kpis"
  },
  "docs": "/docs",
  "dashboard": "/ui"
}
```

### `GET /health`

Returns:

```json
{
  "status": "ok",
  "kirana": {},
  "pos": "connected (kirana_oltp schema)",
  "whatsapp": {
    "phone_id_set": true,
    "send_enabled": true,
    "config_error": null,
    "mistral_set": true
  }
}
```

### `GET /ui`

Returns: HTML dashboard page.

---

## Kirana AI API

Base path: `/kirana`

### `GET /kirana/health`

No auth required.

Returns: Kirana service health object.

### `POST /kirana/auth/login`

No auth required.

Request:

```json
{
  "username": "string",
  "password": "string"
}
```

Response:

```json
{
  "access_token": "string",
  "token_type": "bearer",
  "user": {
    "user_id": 1,
    "username": "owner@example.com",
    "full_name": "Store Owner",
    "role": "store_owner",
    "store_id": 1
  }
}
```

### `POST /kirana/auth/register`

No auth required. Creates an app user **and** provisions `kirana_oltp.store` + `kirana_oltp.users` rows.

Request:

```json
{
  "username": "string",
  "password": "string",
  "full_name": "string",
  "store_name": "string",
  "store_type": "kirana",
  "footfall": 40
}
```

Response:

```json
{
  "access_token": "string",
  "token_type": "bearer",
  "user": {
    "user_id": 1,
    "username": "string",
    "full_name": "string",
    "role": "store_owner",
    "store_id": 1
  },
  "store": {}
}
```

### `GET /kirana/auth/me`

Auth required.

Response:

```json
{
  "user_id": 1,
  "username": "string",
  "full_name": "string",
  "role": "store_owner",
  "store_id": 1
}
```

### `PATCH /kirana/auth/me`

Auth required (Bearer only, not API key).

Request (any subset):

```json
{
  "full_name": "string",
  "password": "string"
}
```

Response: updated user profile.

### `GET /kirana/users`

**Admin only.**

Response:

```json
{
  "users": [
    {
      "user_id": 1,
      "username": "string",
      "full_name": "string",
      "role": "string",
      "store_id": 1,
      "is_active": true
    }
  ]
}
```

### `POST /kirana/users`

**Admin only.**

Request:

```json
{
  "username": "string",
  "password": "string",
  "full_name": "string",
  "role": "string",
  "store_id": 1
}
```

Response: created user object.

### `DELETE /kirana/users/{user_id}`

**Admin only.**

Response:

```json
{ "deleted": true }
```

### `GET /kirana/stores`

Auth required. Store owners see only their own store. Admins see all.

Response:

```json
{
  "stores": [
    {
      "store_id": 1,
      "store_name": "string",
      "store_type": "kirana",
      "footfall": 80,
      "budget": 100000,
      "daily_budget": 4000,
      "sku_count": 123
    }
  ]
}
```

### `GET /kirana/stores/{store_id}/recommendations`

Auth required. Store owner can only access their own `store_id`.

Response:

```json
{
  "summary": {
    "store_id": 1,
    "total_skus": 0,
    "reorder_candidates": 0,
    "high_risk_skus": 0,
    "fast_moving_skus": 0,
    "profit_opportunities": 0,
    "dead_stock_skus": 0
  },
  "recommendations": [
    {
      "store_id": 1,
      "sku_id": 1,
      "product_name": "string",
      "category_name": "string",
      "recommendation_type": "string",
      "priority": "medium",
      "stockout_probability": 0.2,
      "prob_stockout_3d": 0.1,
      "prob_stockout_7d": 0.2,
      "prob_stockout_30d": 0.4,
      "reorder_qty": 10,
      "forecast_demand": 7.5,
      "current_stock": 12,
      "days_to_stockout": 2.5,
      "current_price": 30,
      "optimal_price": 32,
      "price_change_pct": 6.7,
      "expected_profit_impact": 400,
      "effective_margin": 18,
      "reorder_point": 15,
      "message": "string"
    }
  ]
}
```

### `GET /kirana/stores/{store_id}/snapshot`

Auth required. Store-scoped.

Response:

```json
{
  "store_id": 1,
  "snapshot_count": 10,
  "snapshot_date": "2026-05-06",
  "items": [
    {
      "sku_id": 1,
      "snapshot_date": "2026-05-06",
      "units_sold": 10,
      "stock": 20,
      "lost_sales": null,
      "revenue": 1000,
      "profit": 200,
      "price": 50,
      "promo_flag": 1,
      "category": "Staples",
      "product_name": "Rice"
    }
  ]
}
```

### `GET /kirana/stores/{store_id}/reorder`

Auth required. Store-scoped. Returns recommendations filtered for reorder items.

### `GET /kirana/stores/{store_id}/risks`

Auth required. Store-scoped. Returns recommendations filtered for stockout risk.

### `GET /kirana/stores/{store_id}/opportunities`

Auth required. Store-scoped. Returns recommendations filtered for profit opportunities.

### `POST /kirana/stores/{store_id}/snapshot`

Auth required. Store-scoped.

Request:

```json
{
  "snapshot_date": "2026-05-06",
  "items": [
    {
      "sku_id": 1,
      "units_sold": 10,
      "stock": 20,
      "revenue": 1000,
      "profit": 200,
      "price": 50,
      "promo_flag": true
    }
  ]
}
```

Response:

```json
{
  "store_id": 1,
  "snapshot_date": "2026-05-06",
  "upserted_count": 1
}
```

### `PATCH /kirana/stores/{store_id}`

Auth required. Store-scoped.

Request (any subset):

```json
{
  "store_name": "string",
  "store_type": "string",
  "footfall": 80,
  "budget": 100000,
  "daily_budget": 4000
}
```

Response: updated store object.

### `GET /kirana/recommendations`

Auth required. Store owners are auto-scoped to their store.

Query params:

| Param | Type | Description |
|-------|------|-------------|
| `store_id` | int | Required for admin; auto-filled for owners |
| `sku_ids` | string | Comma-separated: `1,2,3` |
| `top_n` | int | Limit results |
| `only_reorder` | bool | |
| `only_high_priority` | bool | |
| `recommendation_type` | string | |
| `sort_by` | string | Default: `expected_profit` |

Response:

```json
{
  "count": 10,
  "results": [
    {
      "store_id": 1,
      "sku_id": 1,
      "product_name": "string",
      "category_name": "string",
      "recommendation_type": "string",
      "priority": "medium",
      "message": "string"
    }
  ]
}
```

### `POST /kirana/recommendations/query`

Auth required. Store owners auto-scoped.

Request:

```json
{
  "store_id": 1,
  "sku_ids": [1, 2],
  "top_n": 10,
  "only_reorder": false,
  "only_high_priority": false,
  "recommendation_type": "string",
  "sort_by": "expected_profit"
}
```

Response: same shape as `GET /kirana/recommendations`.

### `POST /kirana/agent/explain`

Auth required. Store owners auto-scoped.

Request:

```json
{
  "store_id": 1,
  "sku_ids": [1, 2],
  "recommendation_type": "optional",
  "top_n": 5
}
```

Response:

```json
{
  "count": 2,
  "explanations": ["string", "string"]
}
```

### `POST /kirana/agent/query`

Auth required. Store owners auto-scoped.

Request:

```json
{
  "query": "show me stockout risk",
  "store_id": 1,
  "top_n": 5
}
```

Response:

```json
{
  "intent": "string",
  "filters": {},
  "results": [],
  "explanations": ["string"]
}
```

### `POST /kirana/pipeline/refresh`

**Admin only.** Triggers ML model re-training pipeline.

Response: ML refresh result object.

### `GET /kirana/me/prefs`

Auth required (Bearer only).

Response:

```json
{
  "forecast_horizon_days": 7,
  "alert_stockout_threshold": 0.5,
  "alert_min_velocity": 0.3,
  "alert_reorder_days": 3,
  "alert_dead_stock_days": 21,
  "notify_whatsapp": false,
  "notify_in_app": true,
  "quiet_hours_start": 22,
  "quiet_hours_end": 7
}
```

### `PATCH /kirana/me/prefs`

Auth required (Bearer only). Pass any subset of the prefs fields above.

Response: updated preferences object.

---

## POS API

Base path: `/pos`

All endpoints except `/pos/token` require `Authorization: Bearer <pos_jwt>`.  
Store owners are auto-scoped to their store.

### `POST /pos/token`

No auth. Form data login.

Form fields: `username`, `password`

Response:

```json
{
  "access_token": "string",
  "token_type": "bearer"
}
```

### `GET /pos/me`

Response: current POS user dict.

### `GET /pos/stores`

Store owners see only their own store. Admins see all.

Response:

```json
[
  {
    "store_id": 1,
    "name": "string",
    "location": null,
    "region": null
  }
]
```

### `GET /pos/stores/{store_id}`

Store owners can only access their own `store_id`.

Response: single store object.

### `GET /pos/categories`

Returns all product categories (global catalog).

Response:

```json
[
  {
    "category_id": 1,
    "name": "string",
    "parent_category_id": null
  }
]
```

### `GET /pos/products`

Query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `store_id` | int | caller's store | Scoped automatically for owners |
| `skip` | int | 0 | |
| `limit` | int | 100 | |

Response:

```json
[
  {
    "product_id": 1,
    "name": "string",
    "brand": null,
    "unit": null,
    "sku": null,
    "barcode": null,
    "is_perishable": false,
    "is_loose": false,
    "category_id": 1,
    "price": 10.0,
    "mrp": 12.0,
    "stock_quantity": 100
  }
]
```

### `GET /pos/products/barcode/{barcode}`

Query params: `store_id` (optional, auto-scoped)

Response: single product object or 404.

### `GET /pos/products/{product_id}`

Query params: `store_id` (optional, auto-scoped)

Response: single product object or 404.

### `POST /pos/orders`

Creates an order and **automatically deducts stock**.

Request:

```json
{
  "items": [
    {
      "product_id": 1,
      "quantity": 2
    }
  ],
  "customer_id": 123
}
```

Response (HTTP 201):

```json
{
  "order_id": 1,
  "store_id": 1,
  "user_id": 2,
  "order_status": "completed",
  "order_date": "2026-05-06T12:00:00Z",
  "total_amount": 200.0,
  "items": [
    {
      "order_item_id": 1,
      "product_id": 1,
      "quantity": 2,
      "unit_price": 100.0,
      "cost_price": 80.0
    }
  ]
}
```

### `GET /pos/orders`

Returns orders scoped to the caller's store.

Query params: `skip`, `limit`

Response: array of order objects.

### `GET /pos/orders/{order_id}`

Store owner can only access orders belonging to their store.

Response: single order object or 404.

### `POST /pos/payments`

Records a payment. Verifies the order belongs to the caller's store.

Request:

```json
{
  "order_id": 1,
  "amount": 200.0,
  "payment_method": "cash"
}
```

`payment_method` values: `cash | upi | card | credit`

Response (HTTP 201):

```json
{
  "payment_id": 1,
  "order_id": 1,
  "amount": 200.0,
  "payment_method": "upi",
  "status": "success",
  "created_at": "2026-05-06T12:00:00Z"
}
```

### `GET /pos/reports/daily-sales`

Query params:

| Param | Type | Description |
|-------|------|-------------|
| `date` | string | `YYYY-MM-DD` — defaults to today |
| `store_id` | int | Auto-scoped for owners |

Response:

```json
{
  "date": "2026-05-06T00:00:00Z",
  "store_id": 1,
  "total_sales": 10000.0,
  "total_orders": 25,
  "avg_order_value": 400.0
}
```

---

## OLTP API

Base path: `/oltp`

Auth: X-API-Key (admin) or Bearer token (store owner).

This is a **generic CRUD layer** over all 31 tables in the `kirana_oltp` schema. Store owners are automatically scoped to their own store; admins access everything.

### Access Scoping per Table

| Scope type | Tables |
|------------|--------|
| **Global read** (anyone can read, admin-only write) | `calendar`, `category`, `product` |
| **Admin-only write** | `calendar`, `store` |
| **Store-scoped** (auto-filtered by `store_id`) | `ap_ar_aging`, `crm_deals`, `footfall`, `inventory`, `inventory_batch`, `inventory_movements`, `inventory_snapshots`, `khata`, `marketing_spend`, `opex`, `orders`, `pricing`, `process_events`, `promotion`, `purchases`, `return_to_vendor`, `scheme_claim`, `shelf_planogram`, `store`, `subscription`, `supplier`, `users` |
| **Indirect store scope** (scoped via parent) | `order_item` → `orders`, `payments` → `orders`, `purchase_items` → `purchases`, `product_supplier` → `supplier`, `scheme` → `supplier` |
| **Shared** (customers linked to store via orders or khata) | `customer` |

### `GET /oltp/schema`

Returns schema metadata for all 31 `kirana_oltp` tables: columns, PKs, FKs, required fields, scoping.

Response:

```json
{
  "schema": "kirana_oltp",
  "tables": [
    {
      "name": "khata",
      "primary_keys": ["khata_id"],
      "columns": ["khata_id", "store_id", "customer_id", "balance", "credit_limit", "last_transaction_date", "notes"],
      "required_columns": [],
      "required_create_columns": [],
      "foreign_keys": [
        { "column": "store_id", "foreign_table": "store", "foreign_column": "store_id" },
        { "column": "customer_id", "foreign_table": "customer", "foreign_column": "customer_id" }
      ],
      "has_store_id": true,
      "read_scope": "store",
      "write_scope": "store"
    }
  ]
}
```

### `GET /oltp/schema/{table_name}`

Returns schema for one table. Same shape as a single item in the array above.

### `GET /oltp/{table_name}`

List rows. Store owners see only their store's rows.

Query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 100 | Max 500 |
| `offset` | int | 0 | Pagination |
| any column name | string | — | Exact-match filter |

Response:

```json
{
  "table": "khata",
  "count": 5,
  "limit": 100,
  "offset": 0,
  "rows": [ { "khata_id": 1, "store_id": 1, "customer_id": 10, "balance": 500.0, ... } ]
}
```

**Example — get all khata (udhar) records:**
```
GET /oltp/khata
Authorization: Bearer <token>
```

**Example — filter by customer:**
```
GET /oltp/khata?customer_id=42
```

### `GET /oltp/{table_name}/record`

Get one row by primary key. Pass PK columns as query params.

**Example:**
```
GET /oltp/khata/record?khata_id=1
```

Response:

```json
{
  "table": "khata",
  "row": { "khata_id": 1, "store_id": 1, "customer_id": 10, "balance": 500.0, ... }
}
```

### `POST /oltp/{table_name}`

Create a row. Store owners' `store_id` is enforced automatically for store-scoped tables.

Request body: JSON object with column values.

**Example — add a khata entry:**
```json
POST /oltp/khata
{
  "customer_id": 42,
  "balance": 200.0,
  "credit_limit": 1000.0
}
```

Response:

```json
{
  "table": "khata",
  "row": { "khata_id": 101, "store_id": 1, "customer_id": 42, "balance": 200.0, ... }
}
```

**Example — create a category:**
```json
POST /oltp/category
{
  "name": "Dairy",
  "parent_category_id": null
}
```

**Example — create a product:**
```json
POST /oltp/product
{
  "name": "Amul Butter 100g",
  "category_id": 5,
  "brand": "Amul",
  "unit": "pcs",
  "barcode": "8901030890111",
  "is_perishable": true
}
```

### `PATCH /oltp/{table_name}/record`

Update a row. Pass primary keys + updated fields.

Request:

```json
{
  "keys": { "khata_id": 101 },
  "data": { "balance": 350.0, "notes": "paid partial" }
}
```

Response:

```json
{
  "table": "khata",
  "row": { "khata_id": 101, "store_id": 1, "balance": 350.0, ... }
}
```

### `DELETE /oltp/{table_name}/record`

Delete a row by primary key. Pass PK columns as query params.

**Example:**
```
DELETE /oltp/khata/record?khata_id=101
```

Response:

```json
{
  "table": "khata",
  "deleted": true,
  "keys": { "khata_id": 101 }
}
```

---

## OLTP Table Reference

All 31 tables in `kirana_oltp`. Use these names as `{table_name}` in OLTP endpoints.

### Catalog / Global Tables

| Table | Description | Write access |
|-------|-------------|--------------|
| `category` | Product categories (hierarchy via `parent_category_id`) | All authenticated users |
| `product` | Product master — name, brand, unit, barcode, perishable flag | All authenticated users |
| `calendar` | Date dimension table | Admin only |

### Store-Scoped Tables (CRUD available to store owner)

| Table | Description | Key columns |
|-------|-------------|-------------|
| `store` | Store profile — name, address, gstin | Admin write only |
| `users` | Store staff accounts | `store_id`, `name`, `role`, `phone` |
| `inventory` | Current stock per SKU | `store_id`, `product_id`, `quantity`, `reorder_level` |
| `inventory_batch` | Batch tracking for perishables | `store_id`, `product_id`, `batch_no`, `expiry_date`, `quantity` |
| `inventory_movements` | Stock in/out log | `store_id`, `product_id`, `movement_type`, `quantity`, `moved_at` |
| `inventory_snapshots` | Daily EOD stock snapshots | `store_id`, `product_id`, `snapshot_date`, `closing_stock` |
| `pricing` | Store-specific selling price and MRP | `store_id`, `product_id`, `selling_price`, `mrp`, `cost_price` |
| `promotion` | Active discount/promo schemes | `store_id`, `product_id`, `discount_pct`, `start_date`, `end_date` |
| `orders` | Customer sales orders | `store_id`, `customer_id`, `order_date`, `total_amount`, `order_status` |
| `khata` | Udhar (credit) ledger per customer | `store_id`, `customer_id`, `balance`, `credit_limit` |
| `purchases` | Procurement orders from suppliers | `store_id`, `supplier_id`, `purchase_date`, `total_amount`, `status` |
| `supplier` | Supplier master | `store_id`, `name`, `phone`, `gstin`, `payment_terms` |
| `subscription` | Store service subscription status | `store_id`, `plan`, `start_date`, `end_date`, `is_active` |
| `footfall` | Daily visitor count log | `store_id`, `date`, `count` |
| `marketing_spend` | Ad/promo spend tracking | `store_id`, `channel`, `amount`, `date` |
| `opex` | Operating expenses log | `store_id`, `expense_type`, `amount`, `date` |
| `ap_ar_aging` | Accounts payable/receivable aging | `store_id`, `party_id`, `party_type`, `amount`, `due_date` |
| `crm_deals` | CRM pipeline entries | `store_id`, `customer_id`, `deal_value`, `stage` |
| `process_events` | Workflow/automation event log | `store_id`, `event_type`, `status`, `created_at` |
| `return_to_vendor` | RTV (return to supplier) records | `store_id`, `supplier_id`, `product_id`, `quantity`, `reason` |
| `scheme_claim` | Promotional scheme claim records | `store_id`, `scheme_id`, `claimed_at`, `amount` |
| `shelf_planogram` | Shelf layout / planogram data | `store_id`, `product_id`, `shelf_position`, `face_count` |

### Indirect-Scoped Tables (scoped via parent)

| Table | Description | Scoped via |
|-------|-------------|------------|
| `order_item` | Line items in an order | `orders` |
| `payments` | Payments against orders | `orders` |
| `purchase_items` | Line items in a purchase order | `purchases` |
| `product_supplier` | Product-to-supplier mapping | `supplier` |
| `scheme` | Supplier scheme/deal definitions | `supplier` |

### Shared Table

| Table | Description | Notes |
|-------|-------------|-------|
| `customer` | Customer master | Owners see only customers linked to their store via `orders` or `khata` |

---

## WhatsApp API

Base path: `/whatsapp`

Webhook endpoints (`GET /whatsapp/webhook`, `POST /whatsapp/webhook`) are public. All other endpoints require X-API-Key or Bearer auth.

### `GET /whatsapp/webhook`

Meta webhook verification. Query params: `hub.mode`, `hub.challenge`, `hub.verify_token`.

Returns: integer challenge value.

### `POST /whatsapp/webhook`

Receives messages from Meta. No auth.

Returns:

```json
{
  "status": "ok",
  "processed": 1
}
```

### `POST /whatsapp/send/text`

Auth required.

Request:

```json
{
  "phone_number": "+919876543210",
  "message": "Hello"
}
```

Response:

```json
{
  "success": true,
  "message_id": "wamid..."
}
```

### `POST /whatsapp/send/template`

Auth required.

Request:

```json
{
  "phone_number": "+919876543210",
  "template_name": "onboarding_template",
  "template_language": "en_US",
  "parameters": ["42"]
}
```

Response:

```json
{
  "success": true,
  "message_id": "wamid..."
}
```

### `POST /whatsapp/send/media`

Auth required.

Request:

```json
{
  "phone_number": "+919876543210",
  "media_type": "image",
  "media_url": "https://...",
  "caption": "optional"
}
```

`media_type` values: `image | document | video | audio`

Response:

```json
{
  "success": true,
  "message_id": "wamid..."
}
```

### `POST /whatsapp/session/link-store`

Auth required. Store owners can only link their own `store_id`.

Request:

```json
{
  "phone_number": "+919876543210",
  "store_id": 1,
  "owner_name": "string",
  "store_name": "string"
}
```

Response:

```json
{
  "success": true,
  "phone": "+919876543210",
  "store_id": 1
}
```

### `GET /whatsapp/session/{phone}`

Auth required. Store owners see only sessions linked to their store.

Response:

```json
{
  "phone": "919876543210",
  "state": "new",
  "language": "en",
  "store_id": 1,
  "owner_name": "string",
  "store_name": "string",
  "last_message_at": "2026-05-06T12:00:00Z",
  "updated_at": "2026-05-06T12:00:00Z"
}
```

### `DELETE /whatsapp/session/{phone}`

Auth required. Store owners can only reset sessions linked to their store.

Response:

```json
{
  "success": true,
  "phone": "919876543210",
  "state": "new"
}
```

### `GET /whatsapp/health`

Auth required.

Response:

```json
{
  "status": "ok",
  "phone_number_id": "string",
  "verify_token_set": true,
  "access_token_set": true,
  "send_enabled": true,
  "config_error": null,
  "mistral_enabled": true
}
```

---

## KPI API

Base path: `/kirana/kpis`

Auth required (X-API-Key or Bearer) on all endpoints.

Common query params for all KPI endpoints:

| Param | Type | Default | Range |
|-------|------|---------|-------|
| `store_id` | int | 1 | — |
| `days` | int | 30 | 7–365 |

Common response envelope included in every KPI response:

```json
{
  "kpi_id": "string",
  "kpi_name": "string",
  "store_id": 1,
  "store_name": "string",
  "period_days": 30,
  "period_from": "2026-04-06",
  "period_to": "2026-05-06",
  "target": {
    "raw": "+10%",
    "low_pct": 10.0,
    "high_pct": 18.0,
    "description": "string"
  },
  "trend": {},
  "last_updated": "2026-05-06T12:00:00Z"
}
```

### Helper Endpoints

#### `GET /kirana/kpis/registry`

Query params: `vertical` (optional), `status` (`ok | data_unavailable`)

Response:

```json
{
  "verticals": ["Kirana Owner", "Common (All Verticals)"],
  "counts": { "total": 46, "ok": 46, "data_unavailable": 0 },
  "kpis": [
    {
      "kpi_id": "K_TL_1",
      "name": "Annual Revenue",
      "vertical": "Kirana Owner",
      "pl_category": "Top Line",
      "target": "+10% to +18%",
      "status": "ok",
      "endpoint": "/kirana/kpis/daily-revenue",
      "primary_field": "total_revenue"
    }
  ],
  "last_updated": "2026-05-06T12:00:00Z"
}
```

#### `GET /kirana/kpis/by-slug/{slug}`

Run any KPI by its URL slug.

Response: full KPI envelope + all computed fields for that KPI.

#### `GET /kirana/kpis/by-id/{kpi_id}`

Run any KPI by its registry ID (e.g. `K_TL_1`, `C_7`).

Response:

```json
{
  "kpi_id": "K_TL_1",
  "name": "Annual Revenue",
  "status": "ok",
  "value": 12345.6,
  "data": {},
  "trend": {},
  "store_id": 1,
  "days": 30
}
```

#### `GET /kirana/kpis/summary`

All 46 KPIs in one call. One card per KPI.

Query params: `store_id`, `vertical` (optional filter)

Response:

```json
{
  "store_id": 1,
  "store_name": "string",
  "as_of": "2026-05-06",
  "vertical": null,
  "counts": { "total": 46, "ok": 46, "data_unavailable": 0, "errors": 0 },
  "kpis": [
    {
      "kpi_id": "K_TL_1",
      "kpi_key": "daily_revenue",
      "name": "Annual Revenue",
      "status": "ok",
      "value": 12345.6,
      "trend_direction": "up",
      "trend_pct_change": 5.4
    }
  ],
  "errors": {},
  "last_updated": "2026-05-06T12:00:00Z"
}
```

### Explicit KPI Endpoints

Each returns the common envelope plus metric-specific fields.

| Endpoint | Extra response fields |
|----------|-----------------------|
| `GET /kirana/kpis/daily-revenue` | `total_revenue`, `avg_daily_revenue`, `order_count`, `daily_breakdown[]` |
| `GET /kirana/kpis/gross-profit-margin` | `total_revenue`, `total_cogs`, `gross_profit`, `gpm_pct`, `by_category[]` |
| `GET /kirana/kpis/avg-basket-value` | `avg_basket_value`, `median_basket_value`, `max_basket_value`, `order_count`, `brackets[]` |
| `GET /kirana/kpis/inventory-turnover` | `turnover_ratio`, `days_of_inventory`, `cogs`, `avg_inventory_value`, `by_category[]` |
| `GET /kirana/kpis/stockout-rate` | `total_skus`, `oos_sku_count`, `low_stock_count`, `oos_rate_pct`, `oos_items[]` |
| `GET /kirana/kpis/dead-stock` | `dead_sku_count`, `dead_stock_value`, `total_inventory_value`, `dead_stock_pct`, `items[]` |
| `GET /kirana/kpis/return-rate` | `total_orders`, `returned_orders`, `return_rate_pct`, `returned_value`, `by_status{}` |
| `GET /kirana/kpis/cashflow-runway` | `period_revenue`, `period_cost`, `net_cashflow`, `daily_net`, `runway_days`, `cashflow_status`, `weekly_cashflow[]` |
| `GET /kirana/kpis/repeat-customer-frequency` | `total_customers`, `repeat_rate_pct`, `at_risk_count`, `segments[]`, `ml_insights{}` |
| `GET /kirana/kpis/category-mix` | `total_revenue`, `overall_margin_pct`, `categories[]` (BCG quadrant), `ml_insights{}` |
| `GET /kirana/kpis/digital-payment-adoption` | `digital_pct`, `cash_pct`, `by_method{}`, `weekly_trend[]` |
| `GET /kirana/kpis/new-product-trial` | `new_products_count`, `success_rate_pct`, `products[]`, `ml_insights{}` — extra param: `trial_days` |
| `GET /kirana/kpis/cross-category-basket` | `multi_category_pct`, `avg_categories_per_order`, `top_pairs[]` |
| `GET /kirana/kpis/whatsapp-conversion` | `total_sessions`, `state_breakdown{}`, `conversion_proxy_pct` |
| `GET /kirana/kpis/morning-stock-readiness` | `readiness_score`, `ready_count`, `critical_count`, `skus[]`, `ml_insights{}` — no `days` param |
| `GET /kirana/kpis/procurement-cost-savings` | `net_savings`, `savings_pct`, `by_supplier[]` |
| `GET /kirana/kpis/inventory-holding-cost` | `total_stock_value`, `holding_cost_pct_of_revenue`, `by_category[]` — no `days` param |
| `GET /kirana/kpis/distributor-terms` | `total_suppliers`, `total_overpay_opportunity`, `by_supplier[]`, `ml_insights{}` |
| `GET /kirana/kpis/perishable-waste` | `total_perishable_skus`, `high_risk_count`, `waste_rate_pct`, `items[]` |
| `GET /kirana/kpis/shrinkage` | `total_shrinkage_value`, `shrinkage_rate_pct`, `flagged_skus_count`, `items[]`, `ml_insights{}` |
| `GET /kirana/kpis/lead-time-accuracy` | `on_time_rate_pct`, `avg_actual_days`, `by_supplier[]` |
| `GET /kirana/kpis/cash-leakage` | `total_leakage_value`, `leakage_rate_pct`, `flagged_orders[]` |
| `GET /kirana/kpis/high-margin-sales` | `high_margin_pct`, `high_margin_revenue` — extra param: `margin_pctile` (0.5–0.95, default 0.75) |
| `GET /kirana/kpis/stockout-lost-sales` | `estimated_lost_revenue`, `zero_stock_days`, `skus_impacted` |
| `GET /kirana/kpis/data-quality-score` | `score`, `field_count`, `breakdown[]` — no `days` param |

### Dynamic Registry KPI Endpoints

These are auto-generated from the 46-KPI registry. Same response style as `GET /kirana/kpis/by-slug/{slug}`.

- `GET /kirana/kpis/walkin-purchase`
- `GET /kirana/kpis/scheme-capture`
- `GET /kirana/kpis/home-delivery`
- `GET /kirana/kpis/festive-uplift`
- `GET /kirana/kpis/private-label`
- `GET /kirana/kpis/household-wallet-share`
- `GET /kirana/kpis/udhar-recovery`
- `GET /kirana/kpis/expiry-wastage`
- `GET /kirana/kpis/shelf-productivity`
- `GET /kirana/kpis/overhead-ratio`
- `GET /kirana/kpis/supplier-fill-rate`
- `GET /kirana/kpis/rtv-recovery`
- `GET /kirana/kpis/markdown-recovery`
- `GET /kirana/kpis/customer-ltv`
- `GET /kirana/kpis/nrr`
- `GET /kirana/kpis/arpu`
- `GET /kirana/kpis/brand-conversion`
- `GET /kirana/kpis/cac-payback`
- `GET /kirana/kpis/working-capital-cycle`
- `GET /kirana/kpis/ops-cost-per-outlet`
- `GET /kirana/kpis/ai-roi`
- `GET /kirana/kpis/customer-credit-risk`
- `GET /kirana/kpis/process-automation`

---

## Error Responses

| HTTP Code | Meaning |
|-----------|---------|
| 400 | Bad request — missing field, invalid param, or unknown column |
| 401 | Missing or invalid auth header |
| 403 | Authenticated but not permitted (wrong store, admin-only route) |
| 404 | Record or table not found |
| 503 | WhatsApp not configured |
| 500 | Unhandled server error |

Error body:

```json
{
  "success": false,
  "error": "description"
}
```

---

## Notes for Frontend Developers

1. **Login flow**: Call `POST /kirana/auth/login` → store the `access_token` → pass as `Authorization: Bearer <token>` on all subsequent requests.

2. **Store ID**: After login the `user.store_id` field tells you which store the owner belongs to. The backend enforces this server-side too — you do not need to filter client-side.

3. **Product catalog**: `GET /pos/categories` and `GET /pos/products` read from the same global catalog. To add items call `POST /oltp/category` or `POST /oltp/product`.

4. **Khata / Udhar**: Full CRUD at `GET|POST|PATCH|DELETE /oltp/khata`. Filter by customer with `?customer_id=<id>`.

5. **Inventory**: Current stock is at `/oltp/inventory`. Movements log is at `/oltp/inventory_movements`. Batches (expiry) are at `/oltp/inventory_batch`.

6. **Purchases (procurement)**: Header at `/oltp/purchases`, line items at `/oltp/purchase_items` (needs `purchase_id`).

7. **Supplier management**: `/oltp/supplier` for supplier master, `/oltp/product_supplier` for which suppliers carry which products, `/oltp/scheme` for discount deals.

8. **Schema discovery**: Call `GET /oltp/schema` once on app start to get the full column list, required fields, and FK relationships for every table — useful for building dynamic forms.

9. **KPI dashboard**: Call `GET /kirana/kpis/summary?store_id=<id>` for a single-request snapshot of all 46 KPIs. Use individual endpoints when you need the detailed breakdown for a specific KPI card.
