# Kirana Master Backend — QA API Reference

**Audience:** QA / Test Engineering
**Generated:** 2026-07-13 from the live FastAPI OpenAPI schema (`/openapi.json`) — this is the source of truth, every registered endpoint is included.
**Totals:** 312 paths · **356 operations** across 11 modules.
**Interactive docs (always current):** each environment also serves Swagger UI at `/docs` and ReDoc at `/redoc`.

---

## 1. Base URLs (Environments)

| Environment | Base URL |
|---|---|
| **DEV / PROD** | `https://ca-lohiya-outlet.purpleglacier-c71fadea.centralindia.azurecontainerapps.io` |
| **QA** | `https://ca-lohiya-outlet-qa.purpleglacier-c71fadea.centralindia.azurecontainerapps.io` |
| **UAT** | `https://ca-lohiya-outlet-uat.ambitiouspond-d8177a23.centralindia.azurecontainerapps.io` |

> All paths below are **relative to the base URL**. Example: `POST {BASE_URL}/kirana/auth/login`.
> QA testing should target the **QA** base URL unless a ticket says otherwise.

Module → path-prefix map:

| Module | Prefix |
|---|---|
| Kirana AI (app: auth, inventory, finance, modules…) | `/kirana` |
| POS | `/pos` |
| OLTP (raw transactional) | `/oltp` |
| KPIs | `/kirana/kpis` |
| AI (Gemini/chat) | `/kirana/ai` |
| Vision | `/kirana/vision` |
| Forecasting | `/kirana/forecast` |
| Call Center | `/kirana/callcenter` |
| Director Analytics | `/director` (token-gated) |
| WhatsApp (webhook) | `/whatsapp` |

---

## 2. Authentication

There are **three** auth mechanisms depending on the module:

### 2.1 App user token (most `/kirana/*` endpoints)
Protected endpoints accept **either** header:

| Header | Value | Who uses it |
|---|---|---|
| `Authorization: Bearer <token>` | token returned by `POST /kirana/auth/login` (or phone-OTP flow) | end-user app |
| `X-API-Key: <KIRANA_API_KEY>` | shared service/admin key (`KIRANA_API_KEY` env) | admin panel / service-to-service |

To get a user token: `POST /kirana/auth/login` with `{username, password}` → returns a token; send it as `Authorization: Bearer <token>` on subsequent calls.

### 2.2 POS JWT (`/pos/*`)
OAuth2 password flow. `POST /pos/token` (form-encoded `username`/`password`) → `{access_token, token_type}`. Send `Authorization: Bearer <access_token>`.
Phone-auth users (no password) call `POST /pos/token-from-kirana` exchanging their Kirana Bearer token for a POS JWT.
> ⚠️ The POS JWT has the `store_id` baked in — after switching stores the client must re-mint the token.

### 2.3 Director Analytics (`/director/*`)
Token-gated by the `DIRECTOR_TOKEN` env var (passed as a query/header token). Read-only analytics dashboard.

### 2.4 WhatsApp webhook (`/whatsapp/*`)
Verified by Meta webhook signature / verify-token; not called by QA directly except health.

---

## 3. Conventions

- **Content type:** `application/json` for request/response unless noted (`/pos/token` is form-encoded; Vision uploads are `multipart/form-data`).
- **Correlation ID:** you may send `X-Correlation-ID: <token>` (`[A-Za-z0-9._-]{1,64}`); it is echoed back on the response and appears in server logs — **useful for QA bug reports**. If omitted, the server generates one.
- **Success envelope:** many write endpoints return `{ "success": true, ... }`. Read endpoints typically return the resource/array directly. The exact success body per endpoint is documented in Section 5.
- **Store scoping:** most business endpoints are scoped to the caller's active store (derived from the token). Passing data for another store returns empty/forbidden.

---

## 4. Common Responses & Error Envelope (apply to ALL endpoints)

Errors are produced by **centralized handlers** (in `main.py`), so every endpoint shares this behavior. Section 5 documents each endpoint's *success* body and any *specific* codes; the table below is the shared error contract and is **not repeated per endpoint**.

| Status | When | Response body |
|---|---|---|
| `200` | Success | endpoint-specific (see Section 5) |
| `400` | Bad input / invalid value (`ValueError`) | `{ "success": false, "error": "Invalid request" }` |
| `400` | FK references a missing record (PG `23503`) | `{ "success": false, "error": "References a record that doesn't exist." }` |
| `400` | Required field missing (PG `23502`) | `{ "success": false, "error": "A required field is missing." }` |
| `401` | Missing/invalid token (auth-protected routes) | `{ "detail": "Not authenticated" }` (FastAPI security) |
| `403` | Not permitted (`PermissionError`) | `{ "success": false, "error": "<reason>" }` |
| `404` | Resource/route not found | `{ "detail": "Not Found" }` (or endpoint-specific message) |
| `409` | Duplicate / unique violation (PG `23505`) | `{ "success": false, "error": "That record already exists." }` |
| `409` | Other constraint conflict | `{ "success": false, "error": "The request conflicts with existing data." }` |
| `422` | Request validation failed (wrong types/missing body fields) | `{ "detail": [ { "loc": [...], "msg": "...", "type": "..." } ] }` |
| `499` | Client closed connection mid-request (mobile lifecycle) | _(no body — informational)_ |
| `500` | Unhandled server error | `{ "success": false, "error": "Internal server error" }` |

**QA tips**
- A `422` means the **payload shape** is wrong (missing/mistyped field) — check the field table for that endpoint.
- A `400` with `"error"` means the value passed validation but was rejected by business logic.
- Always capture the `X-Correlation-ID` from the response header when filing a bug — it lets us find the exact server log line.

---

## 5. Endpoints by Module

> Each endpoint lists: method + path, summary, path/query params, request body fields (type, required, notes), and success response body. Error responses follow Section 4. Nested object schemas are expanded in collapsible `↳` blocks.

## Kirana AI  (207 endpoints)

#### `GET /kirana/admin/all-subscriptions`
**List All Subscriptions**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/admin/approve-trial/{store_id}`
**Approve Trial**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/cancel-subscription/{store_id}`
**Admin Cancel Subscription**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/categories`
**Admin List Categories**

All product categories — used by the admin inventory editor.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/director/stores`
**Admin Director Stores**

List all stores with their director-dashboard inclusion flag, so the admin
can curate which stores' analytics the director sees (excludes dev/test stores).

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/admin/director/stores/{store_id}`
**Admin Set Director Store**

Toggle whether a store's data appears in the director analytics dashboard.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/extend-trial/{store_id}`
**Extend Trial**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Request body** (application/json, optional)

| Field | Type | Required | Notes |
|---|---|---|---|
| `days` | integer | no | default: `7` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/intelligence/all-logs`
**Admin Intelligence All Logs**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `limit` | integer | no | `50` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/intelligence/fire/{trigger_name}`
**Fire Trigger**

Manually fire an intelligence trigger immediately. Admin only.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `trigger_name` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/intelligence/triggers`
**Admin List Triggers**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/kpi-tiers`
**Admin Get Kpi Tiers**

Admin view: all KPIs with their current tier assignment.

**Responses**
- `200` Successful Response — `application/json`

---

#### `PUT /kirana/admin/kpi-tiers`
**Admin Save Kpi Tiers**

Admin: bulk-save tier assignments. Body: {configs: [{kpi_id, tier}]}

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/kpi-visibility`
**Admin Get Kpi Visibility**

Admin matrix: every KPI × the verticals it applies to, with its default
and effective (override-applied) visibility. Drives the admin toggle grid.

**Responses**
- `200` Successful Response — `application/json`

---

#### `PUT /kirana/admin/kpi-visibility`
**Admin Save Kpi Visibility**

Admin: bulk-save visibility. Body: {configs:[{kpi_id, vertical_code, is_visible}]}

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/logs`
**Admin Logs**

Return last N lines from the in-process log buffer. Admin only.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `lines` | integer | no | `200` |
| `level` | string | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/logs/stream`
**Stream Logs**

SSE live tail from the in-process log buffer. Admin only.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `tail` | integer | no | `100` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/loyalty/overview`
**Admin Loyalty Overview**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/admin/ml/retrain`
**Ml Retrain**

Kick off model retraining (ml_models/train_all.py) in the background.
Output is appended to logs/ml_retrain.log. Re-check /admin/ml/status?refresh=true after.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/ml/status`
**Ml Status**

Prediction-CSV freshness (per-file age + overall stale flag).
Pass ?refresh=true to reload the CSVs from disk first.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `refresh` | boolean | no | `False` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/notify`
**Admin Notify**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/admin/payment/mock-confirm`
**Admin Mock Payment**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/pending-trials`
**List Pending Trials**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/products`
**Admin List Products**

Paginated product list with search + filters. Admin only.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `q` | string | no |  |
| `category_id` | integer | no | `0` |
| `vertical` | string | no |  |
| `has_barcode` | string | no |  |
| `is_loose` | string | no |  |
| `limit` | integer | no | `50` |
| `offset` | integer | no | `0` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/admin/products/{product_id}`
**Admin Update Product**

Update editable fields of a product. Admin only.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/sessions`
**Admin List Sessions**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/settings`
**Get Admin Settings**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/admin/settings`
**Update Admin Settings**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/stats`
**Admin Stats**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/store-groups`
**List Store Groups**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/admin/store-groups`
**Create Store Group**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/stores`
**Admin List Stores**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/stores/{store_id}/deep-dive`
**Admin Store Deep Dive**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/stores/{store_id}/group`
**Assign Store Group**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/stores/{store_id}/serials`
**Admin List Serials**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/stores/{store_id}/serials/bulk`
**Admin Bulk Serials**

Register many serials at once for one product (warehouse intake).

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/stores/{store_id}/staff`
**Admin List Staff**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/admin/stores/{store_id}/staff/bulk`
**Admin Bulk Staff**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/user-activity`
**Admin User Activity**

Per-user app activity: last seen, opens today, time in app, last login, login method, sales.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/vision/analytics`
**Admin Vision Analytics**

Vision AI analytics for the admin panel. Fleet-wide by default (all stores);
pass ?store_id= to scope to one store. Returns the same analytics shape the
store-facing /kirana/vision/analytics endpoint does, plus a per-store breakdown
so the admin can see which stores use vision and how accurate it is for each.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |
| `store_id` | integer? | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/admin/vouchers`
**Admin List Vouchers**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/appointments`
**List Appointments**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/appointments`
**Create Appointment**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/appointments/utilisation`
**Appointment Utilisation**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/appointments/{appointment_id}`
**Update Appointment**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `appointment_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/associations`
**List Associations**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/associations`
**Add Association**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/associations/heatmap`
**Association Heatmap**

Per-apartment/area growth metrics (customers, orders, revenue, last order).

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/associations/{association_id}`
**Delete Association**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `association_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/associations/{association_id}`
**Update Association**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `association_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/attribute-defs`
**Attribute Defs**

The variant axes + attributes the caller's vertical exposes (F2).

Optional ?category=<name> narrows to that category's axes plus the
vertical-wide ones (tester #1 — e.g. electronics Storage vs mAh).

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/auth/change-password`
**Change Password**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `old_password` | string? | no |  |
| `new_password` | string | yes |  |
| `confirm_password` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/auth/check-username/{username}`
**Check Username**

Returns {available: bool} — call before registration to validate uniqueness.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `username` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/auth/fcm-token`
**Update Fcm Token**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `fcm_token` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/auth/login`
**Login**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `username` | string | yes |  |
| `password` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/auth/me`
**Me**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/auth/password-status`
**Password Status**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/auth/phone-login`
**Phone Login**

Log in using a Firebase-verified phone number. Returns 401 if no account exists.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone_number` | string | yes |  |
| `firebase_uid` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/auth/register`
**Register**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `username` | string | yes |  |
| `password` | string | no | default: `` |
| `full_name` | string | yes |  |
| `store_name` | string | yes |  |
| `store_type` | string | no | default: `kirana` |
| `vertical_code` | string? | no |  |
| `footfall` | integer | no | default: `40` |
| `budget` | number? | no |  |
| `location` | string? | no |  |
| `region` | string? | no |  |
| `city` | string? | no |  |
| `email` | string? | no |  |
| `phone_number` | string? | no |  |
| `firebase_uid` | string? | no |  |
| `latitude` | number? | no |  |
| `longitude` | number? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/basket-tier-config`
**Get Basket Tier Config**

**Responses**
- `200` Successful Response — `application/json`

---

#### `PUT /kirana/basket-tier-config`
**Put Basket Tier Config**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/baskets`
**List Baskets**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `include_archived` | boolean | no | `False` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/baskets`
**Create Basket**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/baskets/retier`
**Retier Baskets**

Recompute tier/price for all existing baskets under the current config.

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/baskets/{basket_id}`
**Delete Basket**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `basket_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PUT /kirana/baskets/{basket_id}`
**Update Basket**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `basket_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/baskets/{basket_id}/alert`
**Alert Basket Customers**

Send WhatsApp message to all store customers about this basket deal.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `basket_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/baskets/{basket_id}/archive`
**Archive Basket**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `basket_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/baskets/{basket_id}/restore`
**Restore Basket**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `basket_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/campaigns/recommended`
**Get Recommended Campaigns**

Returns top campaigns: general time-based + area-specific from associations.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `0` |
| `limit` | integer | no | `3` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/cashflow/request`
**Create Cashflow Request**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `amount_requested` | number | yes |  |
| `selected_bank` | string? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/cashflow/status`
**Get Cashflow Status**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/catalog/search`
**Catalog Search**

Search global product catalog by name (ILIKE) or barcode (exact).

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `q` | string | no |  |
| `barcode` | string | no |  |
| `limit` | integer | no | `20` |
| `offset` | integer | no | `0` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/coupons`
**List Coupons**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/coupons`
**Create Coupon**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/coupons/validate`
**Validate Coupon**

**Responses**
- `200` Successful Response — `application/json`

---

#### `PATCH /kirana/coupons/{coupon_id}`
**Toggle Coupon**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `coupon_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/customers`
**List Customers Segments**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/customers/{customer_id}/loyalty`
**Customer Loyalty**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/customers/{customer_id}/price`
**Set Customer Price**

Pin (or, with price=null, remove) a customer-specific price for a product.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `price` | number? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/customers/{customer_id}/price-memory`
**Customer Price Memory**

Per-customer price memory — products where this customer's last-paid price
differs from the current catalog price (powers POS customer-specific pricing).

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/customers/{customer_id}/profile`
**Get Profile**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/customers/{customer_id}/profile`
**Update Profile**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/customers/{customer_id}/purchases`
**Customer Purchases**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/customers/{customer_id}/wishlist`
**List Wishlist**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/customers/{customer_id}/wishlist`
**Add Wishlist**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `customer_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/estimates`
**List Estimates**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/estimates`
**Create Estimate**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/estimates/{estimate_id}`
**Get Estimate**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `estimate_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/estimates/{estimate_id}`
**Set Estimate Status**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `estimate_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/explain`
**Explain**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `sku_ids` | array<integer>? | no |  |
| `recommendation_type` | string? | no |  |
| `top_n` | integer | no | default: `5` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/finance/customers/sync`
**Sync Customers**

**Request body** (application/json, required)

_array<CustomerSyncItem> | CustomerSyncRequest_

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/finance/overview`
**Get Finance Overview**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/finance/udhaar`
**Get Udhaar List**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `include_recovered` | boolean | no | `False` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/finance/udhaar/add`
**Add Udhaar**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `customer_name` | string | yes |  |
| `phone` | string | yes |  |
| `amount` | number | yes |  |
| `due_date` | string? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/finance/udhaar/consent`
**Upload Udhaar Consent**

Receive a customer's voice-consent clip for an udhaar order. The clip
persists to Azure Blob (durable legal record) and a 'pending' row is created;
the in-house voice model later fills the analysis + speaker-match score.
The mobile app uploads this from a persistent background queue, so the owner
is never blocked — there is no synchronous AI work here.

**Request body** (multipart/form-data, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `audio` | string | yes |  |
| `order_id` | integer? | no |  |
| `khata_id` | integer? | no |  |
| `customer_id` | integer? | no |  |
| `agreed_total` | number? | no |  |
| `agreed_udhaar` | number? | no |  |
| `promised_date` | string? | no |  |
| `language` | string? | no |  |
| `duration_sec` | number? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/finance/udhaar/consent/audio/{blob}`
**Get Udhaar Consent Audio**

Authed proxy that streams a consent clip from the private blob container.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `blob` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/finance/udhaar/consent/{order_id}`
**Get Udhaar Consent**

Consent record + analysis for an order (order-details screen).

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `order_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/finance/udhaar/recovery`
**Record Recovery**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `khata_id` | integer | yes |  |
| `amount` | number | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/finance/udhaar/remind`
**Remind Udhaar**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `khata_id` | integer | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/finance/udhaar/smart`
**Smart Udhaar**

Open udhaar ranked by recovery risk, with a suggested action per entry.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/finance/udhaar/{khata_id}/history`
**Get Udhaar History**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `khata_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/health`
**Health**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/intelligence/cart-ping`
**Cart Ping**

Flutter calls this every time the cart changes (debounced).

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `item_count` | integer | no | default: `0` |
| `items` | array<object> | no | default: `[]` |
| `converted` | boolean | no | default: `False` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/intelligence/logs`
**Intelligence Logs**

Returns recent intelligence notifications for this store (or all stores for admin).

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `limit` | integer | no | `50` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/intelligence/notification-opened`
**Notification Opened**

Flutter calls this when the user taps a push notification.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `log_id` | integer | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/inventory/batch/{batch_id}/markdown`
**Set Batch Markdown**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `batch_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `markdown_pct` | number | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/inventory/batch/{batch_id}/waste`
**Record Batch Waste**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `batch_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `units` | integer | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/inventory/cost`
**Set Product Cost**

Capture a product's real purchase cost (product_supplier.cost_price).

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `cost_price` | number | yes |  |
| `supplier_id` | integer? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/inventory/flags`
**Inventory Flags**

Per-product ML flags (fast_moving / reorder_now / dead_stock / stockout_risk
/ profit_opportunity) for the store — used to tag items in inventory/POS.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/inventory/missing-prices`
**Missing Prices**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/inventory/near-expiry`
**Near Expiry Batches**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `7` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/inventory/price`
**Set Product Price**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `price` | number | yes |  |
| `mrp` | number? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/inventory/reorder-suggestions`
**Reorder Suggestions**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `cover_days` | integer | no | `14` |
| `lookback_days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/job-cards`
**List Job Cards**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/job-cards`
**Create Job Card**

**Responses**
- `200` Successful Response — `application/json`

---

#### `PATCH /kirana/job-cards/{job_id}`
**Update Job Card**

Update a job card. Accepts a status change and/or editable fields
(item_desc/details/charge/promised_date/customer_*).

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `job_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/tiers`
**Get Kpi Tiers**

Returns {kpi_id: 'basic'|'pro'} for every KPI in the registry.
DB config wins; missing entries fall back to the default rule:
'Core Insight' category → pro, first 3 per other category → basic, rest → pro.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/kpis/visible`
**Get Visible Kpis**

App: the KPI set this store should show — applicable to its vertical and
visible after admin overrides. Reflects admin changes live (no app update).

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/locations/{location_id}`
**Delete Location**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `location_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/loyalty/config`
**Get Loyalty Config**

**Responses**
- `200` Successful Response — `application/json`

---

#### `PUT /kirana/loyalty/config`
**Save Loyalty Config**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/loyalty/offers-due`
**Offers Due**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `7` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/loyalty/redeem`
**Redeem Points**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/memberships`
**List Memberships**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/memberships`
**Create Membership**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/memberships/{membership_id}/use`
**Use Membership**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `membership_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/my-stores`
**My Stores**

All stores the logged-in owner can switch between (active one flagged).

**Responses**
- `200` Successful Response — `application/json`

---

#### `PATCH /kirana/orders/{order_id}/delivery`
**Set Delivery**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `order_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/payment/create-order`
**Create Payment Order**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `tier` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/payment/mock-confirm`
**Mock Confirm Payment**

Directly upgrades subscription — only for test/dev mode. Blocked in production.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `tier` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/payment/verify`
**Verify Payment**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `tier` | string | yes |  |
| `razorpay_order_id` | string | yes |  |
| `razorpay_payment_id` | string | yes |  |
| `razorpay_signature` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/payment/verify-iap`
**Verify Iap Payment**

Verify a Google Play IAP purchase and activate the subscription.

Optional server-side verification with Google Play Developer API when
GOOGLE_PLAY_CREDENTIALS_JSON is set in .env. Without credentials, the
purchase token is trusted (acceptable for testing; add credentials before
going live).

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/preferences`
**Get Prefs**

**Responses**
- `200` Successful Response — `application/json`

---

#### `PATCH /kirana/preferences`
**Update Prefs**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `forecast_horizon_days` | integer? | no |  |
| `alert_stockout_threshold` | number? | no |  |
| `alert_min_velocity` | number? | no |  |
| `alert_reorder_days` | integer? | no |  |
| `alert_dead_stock_days` | integer? | no |  |
| `notify_whatsapp` | boolean? | no |  |
| `notify_in_app` | boolean? | no |  |
| `quiet_hours_start` | integer? | no |  |
| `quiet_hours_end` | integer? | no |  |
| `allow_social_marketing` | boolean? | no |  |
| `alert_expiry_days` | integer? | no |  |
| `subscribed_kpis` | string? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/products/{product_id}/locations`
**List Locations**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/products/{product_id}/locations`
**Upsert Location**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/products/{product_id}/tax`
**Set Product Tax**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/products/{product_id}/variants`
**List Variants**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/products/{product_id}/variants`
**Create Variant**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/products/{product_id}/warranty`
**Set Product Warranty**

Set a product's warranty length in months (tester #11). 0/None clears it.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/query`
**Agent Query**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | string | yes |  |
| `store_id` | integer? | no |  |
| `top_n` | integer | no | default: `5` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/racks`
**Find By Rack**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `q` | string | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/racks`
**Create Rack**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/racks/all`
**List All Racks**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/racks/list`
**List Racks**

First-class racks (including empty ones) with placement counts.

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/racks/{rack_id}`
**Delete Rack**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `rack_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/racks/{rack_id}`
**Rename Rack**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `rack_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/racks/{rack_id}/merge`
**Merge Racks**

Merge this rack's placements into target_rack_id and delete this rack.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `rack_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/recommendations`
**Query Recommendations**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `sku_ids` | string? | no |  |
| `top_n` | integer | no | `5` |
| `only_reorder` | boolean | no | `False` |
| `only_high_priority` | boolean | no | `False` |
| `recommendation_type` | string? | no |  |
| `sort_by` | string | no | `expected_profit` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/referral/campaigns`
**List Campaigns**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/referral/campaigns`
**Create Campaign**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `name` | string | yes |  |
| `referral_discount_pct` | number | no | default: `10.0` |
| `milestone_every_n` | integer | no | default: `10` |
| `milestone_reward_pct` | number | no | default: `5.0` |
| `max_referrals_per_referrer` | integer | no | default: `50` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/referral/campaigns/{campaign_id}/toggle`
**Toggle Campaign**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `campaign_id` | integer | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `is_active` | boolean | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/referral/scan`
**Process Referral**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `token_hash` | string | yes |  |
| `new_customer_phone` | string | yes |  |
| `new_customer_name` | string | no | default: `` |
| `order_id` | integer? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/referral/token`
**Get Referral Token**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `customer_id` | integer | yes |  |
| `campaign_id` | integer | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/referral/token-info`
**Token Info**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `token` | string | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/referral/vouchers`
**Get Vouchers**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `customer_id` | integer | yes |  |
| `store_id` | integer | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/referral/vouchers/use`
**Use Voucher**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `voucher_id` | integer | yes |  |
| `order_id` | integer? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/returns`
**Record Return**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `order_id` | integer? | no |  |
| `items` | array<ReturnItemInput> | no | default: `[]` |
| `reason` | string? | no |  |
| `refund_amount` | number | no | default: `0` |
| `is_exchange` | boolean | no | default: `False` |
| `customer_id` | integer? | no |  |

<details><summary>↳ <code>ReturnItemInput</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `qty` | integer | yes |  |
| `resaleable` | boolean | no | default: `True` |

</details>


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/sales-returns`
**List Returns**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `90` |
| `order_id` | integer? | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/sales-returns`
**Create Return**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/serials`
**List Serials**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/serials`
**Add Serial**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/serials/sold`
**Mark Sold**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/services`
**List Services**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/services`
**Create Service**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/services/revenue`
**Service Revenue**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/services/{service_id}`
**Update Service**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `service_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/staff`
**List Staff**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/staff`
**Create Staff**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/staff/attendance`
**List Attendance**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `date` | string | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/staff/attendance`
**Mark Attendance**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/staff/performance`
**Staff Performance**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/staff/sales`
**Staff Sales**

Sales + commission per staff member (from orders.staff_id).

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/staff/tasks`
**List Tasks**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/staff/tasks`
**Create Task**

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/staff/tasks/{task_id}`
**Delete Task**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `task_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/staff/tasks/{task_id}`
**Set Task**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `task_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/staff/{staff_id}`
**Update Staff**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `staff_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/staff/{staff_id}/attendance/history`
**Attendance History**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `staff_id` | integer | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/stores`
**List Stores**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/stores/add`
**Add Store**

Create an additional store for the current owner and (by default) switch
to it. Body: store_name, store_type, vertical_code, city, location, region,
footfall, budget, make_active.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/stores/rollup`
**Store Rollup**

Per-store + per-city/region comparison across the caller's store group.
Single-store owners get a one-row rollup (is_multi_store = false).

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/stores/switch`
**Switch Store**

Switch the owner's active store. Body: {store_id}. Membership-checked.

Also mints the new POS JWT (it bakes in store_id) in this same call —
the client used to make a second round trip to /pos/token-from-kirana
right after this one, which doubled the perceived switch latency for no
reason since we already know the new store_id here.

**Responses**
- `200` Successful Response — `application/json`

---

#### `PATCH /kirana/stores/{store_id}`
**Update Store**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `store_name` | string? | no |  |
| `store_type` | string? | no |  |
| `footfall` | integer? | no |  |
| `budget` | number? | no |  |
| `daily_budget` | number? | no |  |
| `location` | string? | no |  |
| `region` | string? | no |  |
| `city` | string? | no |  |
| `vertical_code` | string? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/stores/{store_id}/recommendations`
**Store Recommendations**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/stores/{store_id}/snapshot`
**Get Latest Snapshot**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/stores/{store_id}/snapshot`
**Ingest Snapshot**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `snapshot_date` | string | yes |  |
| `items` | array<InventorySnapshotWriteItem> | yes |  |

<details><summary>↳ <code>InventorySnapshotWriteItem</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `sku_id` | integer | yes |  |
| `units_sold` | number? | no |  |
| `stock` | number? | no |  |
| `revenue` | number? | no |  |
| `profit` | number? | no |  |
| `price` | number? | no |  |
| `promo_flag` | boolean? | no |  |

</details>


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/subscription`
**Get Subscription**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/subscription/cancel`
**Cancel Subscription**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/subscription/request-trial`
**Request Trial**

**Request body** (application/json, optional)

| Field | Type | Required | Notes |
|---|---|---|---|
| `tier` | string | no | default: `basic` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/subscription/send-reminder`
**Send Subscription Reminder**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/subscription/upgrade`
**Upgrade Subscription**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `tier` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/support/report`
**Report Issue**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `category` | string | yes |  |
| `title` | string | yes |  |
| `description` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/tax-rules`
**List Tax Rules**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/tax-rules`
**Create Tax Rule**

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/tax-rules/{rule_id}`
**Delete Tax Rule**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `rule_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/tax/gst-summary`
**Gst Summary**

GSTR-style GST summary for a period (per-rate slab breakup + totals).

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `date_from` | string | yes |  |
| `date_to` | string | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/tracking/app-event`
**Track App Event**

Called by the Flutter app on foreground/background lifecycle transitions.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/users`
**List Users**

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /kirana/users/{user_id}`
**Delete User**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `user_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `DELETE /kirana/variants/{variant_id}`
**Deactivate Variant**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `variant_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/variants/{variant_id}`
**Update Variant**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `variant_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vertical-config`
**Vertical Config**

Foundation 1: the calling store's merged vertical config (feature flags,
units, KPI/ML/tax profiles, copy). Drives config-gated UI in the app.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/warranty-claims`
**List Claims**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/warranty-claims`
**Create Claim**

**Responses**
- `200` Successful Response — `application/json`

---

#### `PATCH /kirana/warranty-claims/{claim_id}`
**Set Claim**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `claim_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `DELETE /kirana/wishlist/{item_id}`
**Remove Wishlist**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `item_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## POS  (14 endpoints)

#### `GET /pos/categories`
**List all product categories**

**Responses**
- `200` Successful Response — body:
Array of `CategoryOut`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `category_id` | integer | yes |  |
| `name` | string | yes |  |
| `parent_category_id` | integer? | no |  |


---

#### `GET /pos/me`
**Current POS user info**

**Responses**
- `200` Successful Response — body:
_object_

---

#### `GET /pos/orders`
**List orders for the current user's store**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `skip` | integer | no | `0` |
| `limit` | integer | no | `50` |
| `status` | string? | no |  |
| `payment_method` | string? | no |  |
| `customer_id` | integer? | no |  |
| `start_date` | string? | no |  |
| `end_date` | string? | no |  |
| `min_amount` | number? | no |  |
| `max_amount` | number? | no |  |

**Responses**
- `200` Successful Response — body:
Array of `OrderOut`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `order_id` | integer | yes |  |
| `store_id` | integer | yes |  |
| `user_id` | integer | yes |  |
| `order_status` | string | yes |  |
| `order_date` | string | yes | format: date-time |
| `total_amount` | number | yes |  |
| `items` | array<OrderItemOut> | no | default: `[]` |
| `payment_method` | string? | no |  |
| `customer_id` | integer? | no |  |
| `udhaar_amount` | number? | no |  |
| `cash_paid` | number? | no |  |
| `basket_id` | integer? | no |  |
| `basket_name` | string? | no |  |
| `basket_gross` | number? | no |  |
| `basket_savings` | number? | no |  |
| `tax_amount` | number? | no |  |
| `taxable_amount` | number? | no |  |

<details><summary>↳ <code>OrderItemOut</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `order_item_id` | integer | yes |  |
| `product_id` | integer | yes |  |
| `product_name` | string? | no |  |
| `quantity` | number | yes |  |
| `unit_price` | number | yes |  |
| `selling_price` | number? | no |  |
| `cost_price` | number? | no |  |
| `variant_id` | integer? | no |  |
| `gst_rate` | number? | no |  |
| `tax_amount` | number? | no |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /pos/orders`
**Create a new POS order (deducts stock automatically)**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | array<OrderItemCreate> | yes |  |
| `customer_id` | integer? | no |  |
| `total_amount` | number? | no |  |
| `payment_method` | string | no | default: `cash` |
| `staff_id` | integer? | no |  |
| `udhaar_amount` | number? | no |  |
| `cash_paid` | number? | no |  |
| `due_date` | string? | no |  |
| `basket_id` | integer? | no |  |
| `basket_name` | string? | no |  |
| `basket_gross` | number? | no |  |
| `basket_savings` | number? | no |  |
| `coupon_id` | integer? | no |  |
| `coupon_discount` | number? | no |  |
| `redeem_points` | number? | no |  |
| `serials` | array<string>? | no |  |
| `serial_items` | array<SerialItemCreate>? | no |  |
| `membership_id` | integer? | no |  |
| `appointment_id` | integer? | no |  |
| `job_card_id` | integer? | no |  |

<details><summary>↳ <code>OrderItemCreate</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `variant_id` | integer? | no |  |
| `quantity` | number | yes |  |
| `unit_price` | number? | no |  |
| `selling_price` | number? | no |  |

</details>

<details><summary>↳ <code>SerialItemCreate</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `serial_no` | string | yes |  |
| `product_id` | integer | yes |  |
| `variant_id` | integer? | no |  |

</details>


**Responses**
- `201` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `order_id` | integer | yes |  |
| `store_id` | integer | yes |  |
| `user_id` | integer | yes |  |
| `order_status` | string | yes |  |
| `order_date` | string | yes | format: date-time |
| `total_amount` | number | yes |  |
| `items` | array<OrderItemOut> | no | default: `[]` |
| `payment_method` | string? | no |  |
| `customer_id` | integer? | no |  |
| `udhaar_amount` | number? | no |  |
| `cash_paid` | number? | no |  |
| `basket_id` | integer? | no |  |
| `basket_name` | string? | no |  |
| `basket_gross` | number? | no |  |
| `basket_savings` | number? | no |  |
| `tax_amount` | number? | no |  |
| `taxable_amount` | number? | no |  |

<details><summary>↳ <code>OrderItemOut</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `order_item_id` | integer | yes |  |
| `product_id` | integer | yes |  |
| `product_name` | string? | no |  |
| `quantity` | number | yes |  |
| `unit_price` | number | yes |  |
| `selling_price` | number? | no |  |
| `cost_price` | number? | no |  |
| `variant_id` | integer? | no |  |
| `gst_rate` | number? | no |  |
| `tax_amount` | number? | no |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /pos/orders/{order_id}`
**Get a single order**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `order_id` | integer | yes |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `order_id` | integer | yes |  |
| `store_id` | integer | yes |  |
| `user_id` | integer | yes |  |
| `order_status` | string | yes |  |
| `order_date` | string | yes | format: date-time |
| `total_amount` | number | yes |  |
| `items` | array<OrderItemOut> | no | default: `[]` |
| `payment_method` | string? | no |  |
| `customer_id` | integer? | no |  |
| `udhaar_amount` | number? | no |  |
| `cash_paid` | number? | no |  |
| `basket_id` | integer? | no |  |
| `basket_name` | string? | no |  |
| `basket_gross` | number? | no |  |
| `basket_savings` | number? | no |  |
| `tax_amount` | number? | no |  |
| `taxable_amount` | number? | no |  |

<details><summary>↳ <code>OrderItemOut</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `order_item_id` | integer | yes |  |
| `product_id` | integer | yes |  |
| `product_name` | string? | no |  |
| `quantity` | number | yes |  |
| `unit_price` | number | yes |  |
| `selling_price` | number? | no |  |
| `cost_price` | number? | no |  |
| `variant_id` | integer? | no |  |
| `gst_rate` | number? | no |  |
| `tax_amount` | number? | no |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /pos/payments`
**Record a payment for an order**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `order_id` | integer | yes |  |
| `amount` | number | yes |  |
| `payment_method` | string | yes |  |


**Responses**
- `201` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `payment_id` | integer | yes |  |
| `order_id` | integer | yes |  |
| `amount` | number | yes |  |
| `payment_method` | string | yes |  |
| `status` | string | yes |  |
| `created_at` | string | yes | format: date-time |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /pos/products`
**List products with current price and stock for a store**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `skip` | integer | no | `0` |
| `limit` | integer | no | `100` |

**Responses**
- `200` Successful Response — body:
Array of `ProductOut`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `name` | string | yes |  |
| `brand` | string? | no |  |
| `unit` | string? | no |  |
| `weight` | number? | no |  |
| `sku` | string? | no |  |
| `barcode` | string? | no |  |
| `is_perishable` | boolean | no | default: `False` |
| `is_loose` | boolean | no | default: `False` |
| `category_id` | integer | yes |  |
| `image_url` | string? | no |  |
| `hsn_code` | string? | no |  |
| `gst_rate` | number? | no |  |
| `warranty_months` | integer? | no |  |
| `price` | number? | no |  |
| `mrp` | number? | no |  |
| `stock_quantity` | integer? | no |  |
| `expiry_date` | string? | no |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /pos/products/barcode/{barcode}`
**Look up a product by barcode**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `barcode` | string | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `name` | string | yes |  |
| `brand` | string? | no |  |
| `unit` | string? | no |  |
| `weight` | number? | no |  |
| `sku` | string? | no |  |
| `barcode` | string? | no |  |
| `is_perishable` | boolean | no | default: `False` |
| `is_loose` | boolean | no | default: `False` |
| `category_id` | integer | yes |  |
| `image_url` | string? | no |  |
| `hsn_code` | string? | no |  |
| `gst_rate` | number? | no |  |
| `warranty_months` | integer? | no |  |
| `price` | number? | no |  |
| `mrp` | number? | no |  |
| `stock_quantity` | integer? | no |  |
| `expiry_date` | string? | no |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /pos/products/{product_id}`
**Get a single product with price and stock**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `product_id` | integer | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `name` | string | yes |  |
| `brand` | string? | no |  |
| `unit` | string? | no |  |
| `weight` | number? | no |  |
| `sku` | string? | no |  |
| `barcode` | string? | no |  |
| `is_perishable` | boolean | no | default: `False` |
| `is_loose` | boolean | no | default: `False` |
| `category_id` | integer | yes |  |
| `image_url` | string? | no |  |
| `hsn_code` | string? | no |  |
| `gst_rate` | number? | no |  |
| `warranty_months` | integer? | no |  |
| `price` | number? | no |  |
| `mrp` | number? | no |  |
| `stock_quantity` | integer? | no |  |
| `expiry_date` | string? | no |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /pos/reports/daily-sales`
**Daily revenue summary for the store (defaults to today IST)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `date` | string? | no |  |
| `store_id` | integer? | no |  |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `date` | string | yes | format: date-time |
| `store_id` | integer? | no |  |
| `total_sales` | number | yes |  |
| `total_orders` | integer | yes |  |
| `avg_order_value` | number | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /pos/stores`
**List all kirana stores**

**Responses**
- `200` Successful Response — body:
Array of `StoreOut`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `name` | string | yes |  |
| `location` | string? | no |  |
| `region` | string? | no |  |


---

#### `GET /pos/stores/{store_id}`
**Get a specific store**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `name` | string | yes |  |
| `location` | string? | no |  |
| `region` | string? | no |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /pos/token`
**POS login — returns JWT**

**Request body** (application/x-www-form-urlencoded, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `grant_type` | string? | no |  |
| `username` | string | yes |  |
| `password` | string | yes | format: password |
| `scope` | string | no | default: `` |
| `client_id` | string? | no |  |
| `client_secret` | string? | no | format: password |


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `access_token` | string | yes |  |
| `token_type` | string | no | default: `bearer` |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /pos/token-from-kirana`
**Exchange Kirana Bearer token for POS JWT**

Phone-auth users have no password — they exchange their Kirana Bearer token for a POS JWT.

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `access_token` | string | yes |  |
| `token_type` | string | no | default: `bearer` |


---

## OLTP  (9 endpoints)

#### `GET /oltp/schema`
**List schema metadata for all kirana_oltp tables**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /oltp/schema/{table_name}`
**Get schema metadata for one kirana_oltp table**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `DELETE /oltp/{table_name}`
**Delete a row by query parameter keys**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /oltp/{table_name}`
**List rows from a kirana_oltp table**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `limit` | integer | no | `100` |
| `offset` | integer | no | `0` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /oltp/{table_name}`
**Update a row by query parameter keys**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Request body** (application/json, required)

_object_

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /oltp/{table_name}`
**Create a row in a kirana_oltp table**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Request body** (application/json, required)

_object_

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `DELETE /oltp/{table_name}/record`
**Delete a row in a kirana_oltp table**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /oltp/{table_name}/record`
**Get a single row by primary key**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /oltp/{table_name}/record`
**Update a row using a structured keys/data body**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `table_name` | string | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `keys` | object | no |  |
| `data` | object | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## KPIs  (64 endpoints)

#### `GET /kirana/kpis/ai-roi`
**AI ROI Multiplier (C_11)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/appointment-utilisation`
**Appointment Utilisation (V_SV_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/arpu`
**ARPU Growth (C_3)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/attach-rate`
**Accessory Attach-rate (V_EL_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/avg-basket-value`
**Average Basket Value — order value distribution**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/brand-conversion`
**Brand Co-invest Conversion (C_6)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/by-id/{kpi_id}`
**Compute a single KPI by its registry kpi_id (e.g. K_TL_1, C_7)**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `kpi_id` | string | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/by-slug/{slug}`
**Compute a single KPI by its endpoint slug (e.g. walkin-purchase)**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `slug` | string | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/cac-payback`
**CAC Payback Period (C_8)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/cash-leakage`
**Cash Leakage / Billing Misses — orders with missing or mismatched payments**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/cashflow-runway`
**Cashflow Runway — net cash position and days of runway**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/category-mix`
**Category Mix Optimization — BCG quadrant analysis**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/cross-category-basket`
**Cross-Category Basket % — multi-category purchase behaviour**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/customer-credit-risk`
**Customer Credit Risk (C_12)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/customer-ltv`
**Customer / Outlet LTV (C_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/daily-revenue`
**Annual Revenue — GMV and daily trend**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/data-quality-score`
**Data Quality Score — fill rate across critical fields (C13)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/dead-stock`
**Dead Stock — SKUs with zero sales**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/digital-payment-adoption`
**Digital Payment Adoption — UPI/card vs cash split and trend**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/distributor-terms`
**Distributor Terms Leverage — price variance and reliability per supplier**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `90` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/expiry-wastage`
**Expiry & Wastage Loss (K_BL_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/festive-uplift`
**Festive / Seasonal Uplift (K_TL_12)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/gmroi`
**GMROI (V_AP_4)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/gross-profit-margin`
**Gross Profit Margin — overall and by category**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/high-margin-sales`
**High-Margin Item Sales % — revenue share from top-margin SKUs (K-TL5 / C7)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |
| `margin_pctile` | number | no | `0.75` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/home-delivery`
**Home Delivery Revenue % (K_TL_9)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/household-wallet-share`
**Household Wallet Share (K_TL_15)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/inventory-holding-cost`
**Inventory Holding Cost — capital tied up vs optimal levels**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/inventory-turnover`
**Inventory Turnover — annualised ratio and days on hand**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/lead-time-accuracy`
**Reorder Lead-Time Accuracy — actual vs expected supplier delivery days**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `90` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/markdown`
**Markdown % (V_AP_3)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/markdown-recovery`
**Near-Expiry Markdown Recovery (K_BL_16)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/morning-stock-readiness`
**Morning Stock Readiness — fast-movers stocked before rush hour**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/new-product-trial`
**New Product Trial Success — 30-day velocity of recently launched SKUs**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `trial_days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/nrr`
**Net Revenue Retention (C_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/ops-cost-per-outlet`
**Ops Cost per Outlet (C_10)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/outfit-uptake`
**Outfit / Bundle Uptake (V_AP_5)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/overhead-ratio`
**Electricity / Rent % of Rev (K_BL_10)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/perishable-waste`
**Perishable Freshness Waste — stagnant perishable stock at risk**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `14` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/private-label`
**Private Label / Store Brand % (K_TL_13)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/process-automation`
**Process Automation Rate (C_14)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/procurement-cost-savings`
**Procurement Cost Savings — actual vs standard rate per supplier**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `90` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/registry`
**46-KPI Master Registry — vertical, theme, status and endpoint per KPI**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `vertical` | string? | no |  |
| `status` | string? | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/repeat-customer-frequency`
**Repeat Customer Frequency — loyalty and churn signals**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/return-rate`
**Return Rate — order cancellations and returns**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/rtv-recovery`
**Return-to-Vendor Recovery (K_BL_13)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/rx-renewal`
**Prescription Renewal Due (V_OP_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/scheme-capture`
**Scheme Benefit Capture (K_TL_6)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/sell-through`
**Sell-through % (V_AP_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/service-revenue`
**Service-wise Revenue (V_SV_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/shelf-productivity`
**Shelf Space Productivity (K_BL_7)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/shrinkage`
**Pilferage / Shrinkage Loss — stock reconciliation with anomaly detection**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/size-curve`
**Size-curve / Size-mix (V_AP_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/staff-performance`
**Staff Performance (V_CM_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/stockout-lost-sales`
**Stockout Lost Sales — revenue lost to OOS days (K-BL5)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/stockout-rate`
**Stockout Rate — % of SKUs out of stock**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/summary`
**All-KPIs summary — one card per KPI in the master registry (46)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `vertical` | string? | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/supplier-fill-rate`
**Supplier Fill Rate (K_BL_11)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/udhar-recovery`
**Udhar (Credit) Recovery (K_BL_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/walkin-purchase`
**Walk-in to Purchase % (K_TL_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/warranty-claim-rate`
**Warranty-claim Rate (V_EL_2)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/whatsapp-conversion`
**WhatsApp Order Conversion — chatbot engagement funnel**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/working-capital-cycle`
**Working Capital Cycle (C_9)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/kpis/zone-comparison`
**Zone / City Comparison (V_CM_1)**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | no | `1` |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## AI  (5 endpoints)

#### `POST /kirana/ai/credits/add`
**Ai Credits Add**

Add purchased credits for a feature. Returns updated status.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `feature` | string | yes |  |
| `count` | integer | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/ai/handwrite`
**Ai Handwrite**

Read a handwritten grocery note (PNG canvas screenshot).
Returns: {transcript: str, items: [{name, quantity}]}

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `image_b64` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/ai/invoice`
**Ai Invoice**

Extract structured data from a supplier invoice (image or PDF).
Returns full InvoiceExtraction JSON.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `data_b64` | string | yes |  |
| `mime_type` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/ai/status`
**Ai Status**

Return remaining daily uses + credit balance for all AI features.

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/ai/voice`
**Ai Voice**

Transcribe audio and extract grocery items.
Accepts base64-encoded AAC audio (16 kHz mono, max 15 s).
Returns: {transcript: str, items: [{name, quantity}]}

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `audio_b64` | string | yes |  |
| `mime_type` | string | no | default: `audio/aac` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## Vision  (15 endpoints)

#### `GET /kirana/vision/analytics`
**Analytics**

Vision usage + accuracy analytics for the store: session volume and processing
latency, unknown/correction rates (accuracy proxies), own-YOLO vs Gemini detector
split, per-day trend series, and the most-seen unknown products (next labels to
train). Derived entirely from vision_session / vision_item.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `days` | integer | yes |  |
| `sessions` | AnalyticsSessions | yes |  |
| `detections` | AnalyticsDetections | yes |  |
| `detectors` | array<AnalyticsDetectorSplit> | yes |  |
| `daily` | array<AnalyticsDaily> | yes |  |
| `top_unknowns` | array<AnalyticsUnknown> | yes |  |

<details><summary>↳ <code>AnalyticsSessions</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `total` | integer | yes |  |
| `done` | integer | yes |  |
| `failed` | integer | yes |  |
| `pending` | integer | yes |  |
| `morning` | integer | yes |  |
| `evening` | integer | yes |  |
| `onboarding` | integer | yes |  |
| `committed` | integer | yes |  |
| `avg_processing_seconds` | number? | no |  |

</details>

<details><summary>↳ <code>AnalyticsDetections</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | integer | yes |  |
| `units` | integer | yes |  |
| `unknown_items` | integer | yes |  |
| `corrected_items` | integer | yes |  |
| `unknown_rate` | number | yes |  |
| `correction_rate` | number | yes |  |
| `avg_match_score` | number? | no |  |

</details>

<details><summary>↳ <code>AnalyticsDetectorSplit</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `detector_source` | string | yes |  |
| `items` | integer | yes |  |
| `units` | integer | yes |  |
| `matched_items` | integer | yes |  |

</details>

<details><summary>↳ <code>AnalyticsDaily</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `date` | string | yes |  |
| `sessions` | integer | yes |  |
| `items` | integer | yes |  |
| `units` | integer | yes |  |
| `unknown_items` | integer | yes |  |
| `corrected_items` | integer | yes |  |

</details>

<details><summary>↳ <code>AnalyticsUnknown</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `raw_name` | string | yes |  |
| `times_seen` | integer | yes |  |
| `units` | integer | yes |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/vision/correct/{item_id}`
**Correct**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `item_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `corrected_product_id` | integer? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/vision/counter/resolve`
**Counter Resolve**

Resolve on-device model class labels → catalog products + the store's selling
price. The app calls this once per counter launch (and caches the map), so the
LIVE tally can show prices/value while counting — even before any sync.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `class_names` | array<string> | no | default: `[]` |


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `items` | array<CounterResolveItem> | yes |  |

<details><summary>↳ <code>CounterResolveItem</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `class_name` | string | yes |  |
| `product_id` | integer? | no |  |
| `display_name` | string | yes |  |
| `price` | number? | no |  |
| `is_unknown` | boolean | yes |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/counter/sessions`
**Counter Sessions**

Counter scan history: recent sessions (newest first) with their per-product
tallies and prices, so the owner can look back at any counting run.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `14` |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `sessions` | array<CounterHistorySession> | yes |  |

<details><summary>↳ <code>CounterHistorySession</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | integer | yes |  |
| `session_date` | string | yes |  |
| `started_at` | string? | no |  |
| `ended_at` | string? | no |  |
| `created_at` | string? | no |  |
| `total_units` | integer | yes |  |
| `total_skus` | integer | yes |  |
| `unknown_count` | integer | yes |  |
| `total_value` | number | no | default: `0.0` |
| `items` | array<CounterSummaryItem> | no | default: `[]` |

<details><summary>↳ <code>CounterSummaryItem</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer? | no |  |
| `class_name` | string | yes |  |
| `display_name` | string | yes |  |
| `qty` | integer | yes |  |
| `is_unknown` | boolean | yes |  |
| `price` | number? | no |  |
| `line_value` | number? | no |  |

</details>

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/counter/summary`
**Counter Summary**

Aggregated per-product tally for the day across all counter sessions.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `date` | string? | no |  |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `session_date` | string | yes |  |
| `items` | array<CounterSummaryItem> | yes |  |
| `total_units` | integer | yes |  |
| `total_skus` | integer | yes |  |
| `total_value` | number | no | default: `0.0` |

<details><summary>↳ <code>CounterSummaryItem</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer? | no |  |
| `class_name` | string | yes |  |
| `display_name` | string | yes |  |
| `qty` | integer | yes |  |
| `is_unknown` | boolean | yes |  |
| `price` | number? | no |  |
| `line_value` | number? | no |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/vision/counter/sync`
**Counter Sync**

Sync a finalized on-device counter session. Detection + line-crossing counting
happened on the phone; here we resolve each class_name → product_id via the shared
catalog matcher and persist the tally. Idempotent by (store_id, client_uid).

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `client_uid` | string | yes |  |
| `session_date` | string? | no |  |
| `device_label` | string? | no |  |
| `started_at` | string? | no |  |
| `ended_at` | string? | no |  |
| `items` | array<CounterItemIn> | no | default: `[]` |

<details><summary>↳ <code>CounterItemIn</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `class_name` | string | yes |  |
| `qty` | integer | no | default: `1` |
| `avg_confidence` | number? | no |  |

</details>


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | integer | yes |  |
| `session_date` | string | yes |  |
| `total_units` | integer | yes |  |
| `total_skus` | integer | yes |  |
| `unknown_count` | integer | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/image/{path}`
**Get Image**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `path` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/item/{item_id}/crop`
**Item Crop**

Return a cropped JPEG of one detected item (its bbox out of its source photo),
so the owner can visually recognise what each row is when reviewing/correcting.
404 if the item/image is unavailable — the app just falls back to no thumbnail.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `item_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/vision/onboarding/analyze`
**Onboarding Analyze**

Bulk stock-in: upload shelf photos captured in-app → durable Azure Blob →
async detection. Returns 202 with a pending onboarding session; an FCM fires when
detection finishes so the app can open the review screen.

**Request body** (multipart/form-data, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `files` | array<string> | yes | 3–10 in-app shelf photos |


**Responses**
- `202` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | integer | yes |  |
| `store_id` | integer | yes |  |
| `session_type` | string | yes |  |
| `status` | string | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/vision/onboarding/commit/{session_id}`
**Onboarding Commit**

Write the owner-reviewed quantities into store inventory and mark the session
committed. Idempotent: quantities are SET, so a re-commit is safe.

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `session_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | array<OnboardingCommitItem> | no | default: `[]` |
| `add_to_existing` | boolean | no | default: `False` |

<details><summary>↳ <code>OnboardingCommitItem</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `quantity` | integer | yes |  |

</details>


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | integer | yes |  |
| `products_added` | integer | yes |  |
| `total_quantity` | integer | yes |  |
| `skipped` | integer | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/sales`
**Sales**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `date` | string? | no |  |

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `session_date` | string | yes |  |
| `items` | array<SalesDeltaItem> | yes |  |
| `total_sold` | integer | yes |  |

<details><summary>↳ <code>SalesDeltaItem</code></summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `product_id` | integer | yes |  |
| `display_name` | string | yes |  |
| `morning_count` | integer | yes |  |
| `evening_count` | integer | yes |  |
| `sold` | integer | yes |  |

</details>

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/session/{session_id}/items`
**Session Items**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `session_id` | integer | yes |

**Responses**
- `200` Successful Response — body:
Array of `VisionItemOut`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `item_id` | integer | yes |  |
| `sku_id` | string? | no |  |
| `product_id` | integer? | no |  |
| `display_name` | string? | no |  |
| `gemini_name` | string | yes |  |
| `visible_text` | string? | no |  |
| `count` | integer | yes |  |
| `match_score` | number | yes |  |
| `is_unknown` | boolean | yes |  |
| `bbox_json` | string? | no |  |
| `image_index` | integer | no | default: `0` |
| `corrected_product_id` | integer? | no |  |
| `detector_source` | string | no | default: `gemini` |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/session/{session_id}/photo/{index}`
**Session Photo**

Serve one of the photos the owner uploaded for a scan, so the history view
can show exactly what was photographed. Store-scoped; 404 when the photo is
gone (e.g. pre-Blob sessions on a restarted container).

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `session_id` | integer | yes |
| `index` | integer | yes |

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `thumb` | integer | no | `0` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/vision/sessions`
**List Sessions**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `date` | string? | no |  |
| `days` | integer? | no |  |

**Responses**
- `200` Successful Response — body:
Array of `SessionSummary`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | integer | yes |  |
| `session_type` | string | yes |  |
| `session_date` | string | yes |  |
| `status` | string | yes |  |
| `total_skus` | integer | yes |  |
| `total_units` | integer | yes |  |
| `unknown_count` | integer | yes |  |
| `created_at` | string? | no |  |
| `photo_count` | integer | no | default: `0` |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/vision/shelf/analyze`
**Shelf Analyze**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `session_type` | string | yes |  |

**Request body** (multipart/form-data, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `files` | array<string> | yes | 3–10 shelf photos covering the store |


**Responses**
- `202` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | integer | yes |  |
| `store_id` | integer | yes |  |
| `session_type` | string | yes |  |
| `status` | string | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## Forecasting  (4 endpoints)

#### `GET /kirana/forecast/items`
**Per-SKU demand + revenue forecast for a single horizon**

Returns per-SKU predicted units and revenue for the chosen horizon.
Items are sorted by predicted revenue descending — top revenue drivers first.

Includes:
- `predicted_units` ± CI (95%)
- `predicted_revenue` ± CI
- `will_oos_in_window` flag
- `stockout_risk_pct`
- `days_of_supply`

Use this to build the "What will we sell?" inventory planning table.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `horizon_days` | integer | no | `7` |
| `top_n` | integer | no | `100` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/forecast/revenue`
**Store revenue forecast across all horizons (1/3/5/7/14/30 days)**

Returns total predicted revenue for the store across all 6 forecast horizons.
Each horizon includes a `low`/`high` confidence band (95% Poisson CI).

Use this for the revenue forecast trend chart.

Example response item:
```json
{"horizon_days": 7, "horizon_label": "Next 7 days",
 "predicted": 87500, "low": 72000, "high": 103000, "predicted_units": 1750}
```

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/forecast/risks`
**Items at OOS risk during the forecast window — with lost revenue estimate**

Returns items that are likely to go out-of-stock within the forecast window,
ranked by estimated lost revenue impact.

Urgency levels:
- CRITICAL: < 1 day of supply remaining
- HIGH:     1–3 days
- MEDIUM:   3–7 days
- LOW:      > 7 days (still risky given the horizon)

`predicted_lost_revenue` = avg_daily × OOS_duration × stockout_prob × avg_price.
Use this to drive the "Act now" reorder alert panel.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |
| `horizon_days` | integer | no | `7` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/forecast/summary`
**Demand + Revenue forecast — all horizons (1/3/5/7/14/30 days)**

Returns demand + revenue forecast for every horizon in a single response.
Use this for the main dashboard forecast card — one round-trip shows all windows.

Revenue CI: ±1.96σ√N (Poisson, 95%). Adjust for OOS probability per window.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer | yes |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## Call Center  (17 endpoints)

#### `GET /kirana/callcenter/assignable-stores`
**Assignable Stores**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `q` | string? | no |  |
| `unassigned_only` | boolean | no | `False` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/callcenter/assignments`
**Assign**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `executive_id` | integer | yes |  |
| `store_ids` | array<integer> | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `DELETE /kirana/callcenter/assignments/{store_id}`
**Unassign**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/callcenter/callbacks`
**Callbacks**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/callcenter/executives`
**List Executives**

**Responses**
- `200` Successful Response — body:
Array of `ExecutiveOut`:
| Field | Type | Required | Notes |
|---|---|---|---|
| `executive_id` | integer | yes |  |
| `username` | string | yes |  |
| `full_name` | string | yes |  |
| `phone` | string? | no |  |
| `email` | string? | no |  |
| `role` | string | yes |  |
| `is_active` | boolean | yes |  |
| `created_at` | string? | no |  |
| `assigned_count` | integer | no | default: `0` |
| `calls_today` | integer | no | default: `0` |


---

#### `POST /kirana/callcenter/executives`
**Create Executive**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `username` | string | yes |  |
| `full_name` | string | yes |  |
| `password` | string | yes |  |
| `phone` | string? | no |  |
| `email` | string? | no |  |
| `role` | string | no | default: `call_executive` |


**Responses**
- `201` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `executive_id` | integer | yes |  |
| `username` | string | yes |  |
| `full_name` | string | yes |  |
| `phone` | string? | no |  |
| `email` | string? | no |  |
| `role` | string | yes |  |
| `is_active` | boolean | yes |  |
| `created_at` | string? | no |  |
| `assigned_count` | integer | no | default: `0` |
| `calls_today` | integer | no | default: `0` |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `PATCH /kirana/callcenter/executives/{executive_id}`
**Update Executive**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `executive_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `is_active` | boolean? | no |  |
| `password` | string? | no |  |


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `executive_id` | integer | yes |  |
| `username` | string | yes |  |
| `full_name` | string | yes |  |
| `phone` | string? | no |  |
| `email` | string? | no |  |
| `role` | string | yes |  |
| `is_active` | boolean | yes |  |
| `created_at` | string? | no |  |
| `assigned_count` | integer | no | default: `0` |
| `calls_today` | integer | no | default: `0` |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/callcenter/feedback`
**Feedback**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `days` | integer | no | `30` |
| `tag` | string? | no |  |
| `sentiment` | string? | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/callcenter/load`
**Load**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /kirana/callcenter/login`
**Login**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `username` | string | yes |  |
| `password` | string | yes |  |


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string | yes |  |
| `executive_id` | integer | yes |  |
| `full_name` | string | yes |  |
| `role` | string | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/callcenter/logout`
**Logout**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/callcenter/me`
**Me**

**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `executive_id` | integer | yes |  |
| `username` | string | yes |  |
| `full_name` | string | yes |  |
| `role` | string | yes |  |


---

#### `GET /kirana/callcenter/queue`
**Queue**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `limit` | integer | no | `100` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/callcenter/stats`
**Stats**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/callcenter/stores/{store_id}`
**Call Sheet**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /kirana/callcenter/stores/{store_id}/calls`
**Log Call**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `disposition` | string | yes |  |
| `app_usage_status` | string? | no |  |
| `feedback_text` | string? | no |  |
| `sentiment` | string? | no |  |
| `rating` | integer? | no |  |
| `next_action` | string? | no |  |
| `callback_at` | string? | no |  |
| `tags` | array<string> | no | default: `[]` |


**Responses**
- `200` Successful Response — body:
| Field | Type | Required | Notes |
|---|---|---|---|
| `call_id` | integer | yes |  |
| `called_at` | string | yes |  |

- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/callcenter/stores/{store_id}/history`
**Store History**

Call history for a store — powers the StoreDetail 'Call History' tab (admin key).

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `store_id` | integer | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## Director Analytics  (11 endpoints)

#### `GET /director/api/ai`
**Api Ai**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/baskets`
**Api Baskets**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/customers`
**Api Customers**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/engagement`
**Api Engagement**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/footfall`
**Api Footfall**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/overview`
**Api Overview**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/referrals`
**Api Referrals**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/sales`
**Api Sales**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/stores`
**Api Stores**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /director/api/subscriptions`
**Api Subscriptions**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /director/api/vision`
**Api Vision**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `store_id` | integer? | no |  |
| `days` | integer | no | `30` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## WhatsApp  (9 endpoints)

#### `GET /whatsapp/health`
**WhatsApp service health**

**Responses**
- `200` Successful Response — `application/json`

---

#### `POST /whatsapp/send/media`
**Send a media (image/document/video) WhatsApp message**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone_number` | string | yes |  |
| `media_type` | string | yes |  |
| `media_url` | string | yes |  |
| `caption` | string? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /whatsapp/send/template`
**Send a WhatsApp template message**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone_number` | string | yes |  |
| `template_name` | string | yes |  |
| `template_language` | string | no | default: `en_US` |
| `parameters` | array<string> | no | default: `[]` |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /whatsapp/send/text`
**Send a plain text WhatsApp message**

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone_number` | string | yes |  |
| `message` | string | yes |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /whatsapp/session/link-store`
**Link a WhatsApp number to a store**

Associate a phone number with a store so analytics data is store-scoped.

**Request body** (application/json, required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone_number` | string | yes |  |
| `store_id` | integer | yes |  |
| `owner_name` | string? | no |  |
| `store_name` | string? | no |  |


**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `DELETE /whatsapp/session/{phone}`
**Reset conversation session for a phone number**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `phone` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /whatsapp/session/{phone}`
**Get session state for a phone number**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `phone` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /whatsapp/webhook`
**WhatsApp webhook verification**

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `hub.mode` | string | no |  |
| `hub.challenge` | string | no |  |
| `hub.verify_token` | string | no |  |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `POST /whatsapp/webhook`
**Receive WhatsApp messages and status updates**

Meta sends all incoming messages here.
We parse, route through ConversationHandler, and return 200 immediately.

**Request body** (application/json, required)

_object_

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

## Root  (1 endpoints)

#### `GET /health`
**Health**

**Responses**
- `200` Successful Response — `application/json`

---

## Convenience & Utility Endpoints

> These are operational/helper endpoints (health checks, service status, session reset, root metadata) — not part of a business flow. They are also listed above under their module tag; collected here for the tester's convenience.

#### `GET /health`
**Health**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/admin/ml/status`
**Ml Status**

Prediction-CSV freshness (per-file age + overall stale flag).
Pass ?refresh=true to reload the CSVs from disk first.

**Query parameters**
| Name | Type | Required | Default |
|---|---|---|---|
| `refresh` | boolean | no | `False` |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

#### `GET /kirana/ai/status`
**Ai Status**

Return remaining daily uses + credit balance for all AI features.

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /kirana/health`
**Health**

**Responses**
- `200` Successful Response — `application/json`

---

#### `GET /whatsapp/health`
**WhatsApp service health**

**Responses**
- `200` Successful Response — `application/json`

---

#### `DELETE /whatsapp/session/{phone}`
**Reset conversation session for a phone number**

**Path parameters**
| Name | Type | Required |
|---|---|---|
| `phone` | string | yes |

**Responses**
- `200` Successful Response — `application/json`
- `422` Validation error — standard FastAPI `{ "detail": [ {loc,msg,type} ] }` (see Common Errors).

---

---

## 6. Database Structure

> Source of truth: `db_generation/ensure_full_schema.py` (idempotent). Schemas: `kirana_oltp` (transactional) and `kirana_olap` (analytics). Reproduced verbatim below for QA reference (data-validation, FK/constraint checks).

```text
================================================================================
KIRANA-MASTER-BACKEND — DATABASE SCHEMA
================================================================================
Source of truth: db_generation/ensure_full_schema.py (idempotent schema script)
Engine: PostgreSQL
Schemas: kirana_oltp (transactional), kirana_olap (analytics)

Generated: 2026-06-15

Legend:  PK = primary key   FK = foreign key   UQ = unique   NN = not null

================================================================================
SCHEMA: kirana_oltp  (transactional / OLTP)
================================================================================

--------------------------------------------------------------------------------
store
--------------------------------------------------------------------------------
  store_id      BIGSERIAL      PK
  name          VARCHAR(150)   NN
  location      VARCHAR(255)
  region        VARCHAR(100)
  store_type    VARCHAR(100)   DEFAULT 'kirana'
  footfall      INT
  budget        NUMERIC
  daily_budget  NUMERIC
  latitude      NUMERIC(10,7)
  longitude     NUMERIC(10,7)
  created_at    TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
  is_deleted    BOOLEAN        DEFAULT FALSE

--------------------------------------------------------------------------------
category
--------------------------------------------------------------------------------
  category_id         BIGSERIAL    PK
  parent_category_id  BIGINT       FK -> category(category_id)
  name                VARCHAR(150) NN

--------------------------------------------------------------------------------
customer
--------------------------------------------------------------------------------
  customer_id     BIGSERIAL    PK
  store_id        BIGINT       FK -> store(store_id)
  name            VARCHAR(150)
  phone           VARCHAR(20)
  email           VARCHAR(150)
  household_size  INT          DEFAULT 4
  referral_count  INT          NN DEFAULT 0
  association_id  INTEGER      FK -> store_association(association_id) ON DELETE SET NULL
  created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP

--------------------------------------------------------------------------------
users
--------------------------------------------------------------------------------
  user_id              BIGSERIAL     PK
  username             VARCHAR(100)  UQ NN
  email                VARCHAR(150)
  role                 VARCHAR(50)
  store_id             BIGINT        FK -> store(store_id)
  full_name            VARCHAR(255)  NN DEFAULT ''
  password_salt        VARCHAR(64)
  password_hash        VARCHAR(128)
  password_changed_at  TIMESTAMPTZ
  is_active            BOOLEAN       NN DEFAULT TRUE
  fcm_token            VARCHAR(255)
  phone_number         VARCHAR(20)
  firebase_uid         VARCHAR(128)
  created_at           TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
  is_deleted           BOOLEAN       DEFAULT FALSE

--------------------------------------------------------------------------------
product
--------------------------------------------------------------------------------
  product_id        BIGSERIAL     PK
  category_id       BIGINT        NN FK -> category(category_id)
  name              VARCHAR(200)  NN
  brand             VARCHAR(100)
  unit              VARCHAR(20)
  weight            NUMERIC(10,2)
  is_loose          BOOLEAN       DEFAULT FALSE
  is_perishable     BOOLEAN       DEFAULT FALSE
  is_private_label  BOOLEAN       DEFAULT FALSE
  sku               VARCHAR(100)  UQ
  barcode           VARCHAR(100)  UQ
  image_url         VARCHAR(500)
  created_at        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP

--------------------------------------------------------------------------------
supplier
--------------------------------------------------------------------------------
  supplier_id  BIGSERIAL    PK
  name         VARCHAR(150)
  contact      VARCHAR(150)
  phone        VARCHAR(20)
  category     VARCHAR(100)
  store_id     BIGINT       FK -> store(store_id)

--------------------------------------------------------------------------------
product_supplier
--------------------------------------------------------------------------------
  id              BIGSERIAL      PK
  product_id      BIGINT         FK -> product(product_id)
  supplier_id     BIGINT         FK -> supplier(supplier_id)
  cost_price      NUMERIC(10,2)  CHECK >= 0
  lead_time_days  INT            CHECK >= 0

--------------------------------------------------------------------------------
pricing
--------------------------------------------------------------------------------
  pricing_id  BIGSERIAL      PK
  product_id  BIGINT         FK -> product(product_id)
  store_id    BIGINT         FK -> store(store_id)
  price       NUMERIC(10,2)  CHECK >= 0
  mrp         NUMERIC(10,2)  CHECK >= 0
  valid_from  TIMESTAMP      NN
  valid_to    TIMESTAMP

--------------------------------------------------------------------------------
promotion
--------------------------------------------------------------------------------
  promotion_id      BIGSERIAL     PK
  product_id        BIGINT        FK -> product(product_id)
  store_id          BIGINT        FK -> store(store_id)
  discount_percent  NUMERIC(5,2)  CHECK >= 0
  start_date        TIMESTAMP
  end_date          TIMESTAMP

--------------------------------------------------------------------------------
inventory
--------------------------------------------------------------------------------
  inventory_id  BIGSERIAL  PK
  store_id      BIGINT     FK -> store(store_id)
  product_id    BIGINT     FK -> product(product_id)
  quantity      INT        DEFAULT 0  CHECK >= 0
  UQ (store_id, product_id)

--------------------------------------------------------------------------------
inventory_movements
--------------------------------------------------------------------------------
  movement_id      BIGSERIAL    PK
  store_id         BIGINT       FK -> store(store_id)
  product_id       BIGINT       FK -> product(product_id)
  change_quantity  INT
  reason           VARCHAR(50)
  reference_id     BIGINT
  created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP

--------------------------------------------------------------------------------
inventory_snapshots
--------------------------------------------------------------------------------
  snapshot_date  DATE          PK (composite)
  store_id       BIGINT        PK (composite)  FK -> store(store_id)
  product_id     BIGINT        PK (composite)  FK -> product(product_id)
  stock_on_hand  INT           CHECK >= 0
  upserted_at    TIMESTAMPTZ   NN DEFAULT NOW()
  PK (snapshot_date, store_id, product_id)

--------------------------------------------------------------------------------
orders
--------------------------------------------------------------------------------
  order_id        BIGSERIAL     PK
  store_id        BIGINT        NN FK -> store(store_id)
  user_id         BIGINT        FK -> users(user_id)
  customer_id     BIGINT        FK -> customer(customer_id)
  order_status    VARCHAR(50)   DEFAULT 'completed'
  order_date      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
  total_amount    NUMERIC(12,2) CHECK >= 0
  udhaar_amount   NUMERIC(12,2)
  cash_paid       NUMERIC(12,2)
  order_channel   VARCHAR(20)   DEFAULT 'walk_in'
  basket_id       BIGINT
  basket_name     VARCHAR(255)
  basket_gross    NUMERIC(12,2)
  basket_savings  NUMERIC(12,2)

--------------------------------------------------------------------------------
order_item
--------------------------------------------------------------------------------
  order_item_id  BIGSERIAL      PK
  order_id       BIGINT         NN FK -> orders(order_id) ON DELETE CASCADE
  product_id     BIGINT         NN FK -> product(product_id)
  quantity       NUMERIC
  unit_price     NUMERIC(10,2)  CHECK >= 0
  cost_price     NUMERIC(10,2)  CHECK >= 0

--------------------------------------------------------------------------------
payments
--------------------------------------------------------------------------------
  payment_id      BIGSERIAL      PK
  order_id        BIGINT         FK -> orders(order_id)
  amount          NUMERIC(10,2)
  payment_method  VARCHAR(50)
  status          VARCHAR(50)    DEFAULT 'paid'
  created_at      TIMESTAMP      DEFAULT CURRENT_TIMESTAMP

--------------------------------------------------------------------------------
purchases
--------------------------------------------------------------------------------
  purchase_id     BIGSERIAL      PK
  supplier_id     BIGINT         FK -> supplier(supplier_id)
  store_id        BIGINT         FK -> store(store_id)
  order_date      TIMESTAMP
  arrival_date    TIMESTAMP
  status          VARCHAR(50)
  total_amount    NUMERIC(12,2)
  due_date        DATE
  payment_status  VARCHAR(20)    DEFAULT 'unpaid'
  notes           VARCHAR(255)

--------------------------------------------------------------------------------
purchase_items
--------------------------------------------------------------------------------
  purchase_item_id  BIGSERIAL      PK
  purchase_id       BIGINT         FK -> purchases(purchase_id) ON DELETE CASCADE
  product_id        BIGINT         FK -> product(product_id)
  quantity          INT            CHECK > 0
  cost_price        NUMERIC(10,2)
  requested_qty     INT

================================================================================
AUTH / APP TABLES
================================================================================

--------------------------------------------------------------------------------
user_sessions
--------------------------------------------------------------------------------
  session_id    BIGSERIAL     PK
  user_id       BIGINT        NN FK -> users(user_id) ON DELETE CASCADE
  access_token  VARCHAR(128)  UQ NN
  login_method  VARCHAR(20)   DEFAULT 'password'
  created_at    TIMESTAMPTZ   NN DEFAULT NOW()
  revoked_at    TIMESTAMPTZ

--------------------------------------------------------------------------------
user_prefs
--------------------------------------------------------------------------------
  user_id                   BIGINT       PK  FK -> users(user_id) ON DELETE CASCADE
  forecast_horizon_days     INT          NN DEFAULT 7
  alert_stockout_threshold  REAL         NN DEFAULT 0.5
  alert_min_velocity        REAL         NN DEFAULT 0.3
  alert_reorder_days        INT          NN DEFAULT 3
  alert_dead_stock_days     INT          NN DEFAULT 21
  alert_expiry_days         INT          NN DEFAULT 7
  notify_whatsapp           BOOLEAN      NN DEFAULT FALSE
  notify_in_app             BOOLEAN      NN DEFAULT TRUE
  quiet_hours_start         INT          NN DEFAULT 22
  quiet_hours_end           INT          NN DEFAULT 7
  subscribed_kpis           TEXT
  allow_social_marketing    BOOLEAN      NN DEFAULT FALSE
  updated_at                TIMESTAMPTZ  NN DEFAULT NOW()

--------------------------------------------------------------------------------
user_fcm_tokens
--------------------------------------------------------------------------------
  token_id    BIGSERIAL     PK
  user_id     BIGINT        NN FK -> users(user_id) ON DELETE CASCADE
  fcm_token   VARCHAR(255)  NN  UQ (uq_user_fcm_tokens_token)
  created_at  TIMESTAMPTZ   NN DEFAULT NOW()
  last_seen   TIMESTAMPTZ   NN DEFAULT NOW()

--------------------------------------------------------------------------------
app_activity
--------------------------------------------------------------------------------
  id            BIGSERIAL    PK
  user_id       BIGINT       NN FK -> users(user_id) ON DELETE CASCADE
  event         VARCHAR(20)  NN
  duration_sec  INT
  created_at    TIMESTAMPTZ  NN DEFAULT NOW()

--------------------------------------------------------------------------------
issue_report
--------------------------------------------------------------------------------
  report_id    BIGSERIAL     PK
  user_id      BIGINT        NN FK -> users(user_id)
  store_id     BIGINT        NN FK -> store(store_id)
  category     VARCHAR(50)   NN
  title        VARCHAR(255)  NN
  description  TEXT          NN
  status       VARCHAR(20)   DEFAULT 'open'
  created_at   TIMESTAMPTZ   DEFAULT NOW()

--------------------------------------------------------------------------------
cashflow_requests
--------------------------------------------------------------------------------
  request_id        BIGSERIAL      PK
  store_id          BIGINT         NN FK -> store(store_id)
  user_id           BIGINT         NN FK -> users(user_id)
  amount_requested  NUMERIC(12,2)  NN
  selected_bank     VARCHAR(100)
  status            VARCHAR(20)    NN DEFAULT 'pending'
  store_name        VARCHAR(200)
  location          VARCHAR(500)
  avg_footfall      INT
  created_at        TIMESTAMPTZ    NN DEFAULT NOW()

--------------------------------------------------------------------------------
basket
--------------------------------------------------------------------------------
  basket_id    BIGSERIAL     PK
  store_id     BIGINT        NN FK -> store(store_id) ON DELETE CASCADE
  name         VARCHAR(200)  NN
  description  TEXT
  price        NUMERIC
  valid_from   DATE
  valid_to     DATE
  is_active    BOOLEAN       NN DEFAULT TRUE
  created_at   TIMESTAMPTZ   NN DEFAULT NOW()

--------------------------------------------------------------------------------
basket_item
--------------------------------------------------------------------------------
  id            BIGSERIAL     PK
  basket_id     BIGINT        NN FK -> basket(basket_id) ON DELETE CASCADE
  product_id    BIGINT        NN
  product_name  VARCHAR(255)
  qty           NUMERIC       NN DEFAULT 1

--------------------------------------------------------------------------------
vision_session
--------------------------------------------------------------------------------
  session_id     BIGSERIAL    PK
  store_id       BIGINT       NN FK -> store(store_id) ON DELETE CASCADE
  session_type   VARCHAR(20)  NN
  session_date   DATE         NN DEFAULT CURRENT_DATE
  image_url      TEXT
  status         VARCHAR(20)  NN DEFAULT 'pending'
  total_skus     INT          NN DEFAULT 0
  total_units    INT          NN DEFAULT 0
  unknown_count  INT          NN DEFAULT 0
  error          TEXT
  created_at     TIMESTAMPTZ  NN DEFAULT NOW()

--------------------------------------------------------------------------------
vision_item
--------------------------------------------------------------------------------
  item_id               BIGSERIAL     PK
  session_id            BIGINT        NN FK -> vision_session(session_id) ON DELETE CASCADE
  sku_id                VARCHAR(64)
  product_id            BIGINT
  display_name          VARCHAR(255)
  gemini_name           VARCHAR(255)  NN
  visible_text          TEXT
  count                 INT           NN DEFAULT 1
  match_score           REAL          NN DEFAULT 0
  is_unknown            BOOLEAN       NN DEFAULT TRUE
  bbox_json             TEXT
  corrected_product_id  BIGINT
  corrected_at          TIMESTAMPTZ
  created_at            TIMESTAMPTZ   NN DEFAULT NOW()

--------------------------------------------------------------------------------
ai_usage
--------------------------------------------------------------------------------
  id          BIGSERIAL    PK
  user_id     BIGINT       NN FK -> users(user_id) ON DELETE CASCADE
  feature     VARCHAR(20)  NN
  usage_date  DATE         NN DEFAULT CURRENT_DATE
  count       INT          NN DEFAULT 0
  UQ (user_id, feature, usage_date)

--------------------------------------------------------------------------------
ai_credits
--------------------------------------------------------------------------------
  id       BIGSERIAL    PK
  user_id  BIGINT       NN FK -> users(user_id) ON DELETE CASCADE
  feature  VARCHAR(20)  NN
  balance  INT          NN DEFAULT 0  CHECK >= 0
  UQ (user_id, feature)

--------------------------------------------------------------------------------
kpi_tier_config
--------------------------------------------------------------------------------
  kpi_id         TEXT       PK
  required_tier  TEXT       NN DEFAULT 'basic'  CHECK IN ('basic','pro')
  updated_at     TIMESTAMP  NN DEFAULT NOW()

================================================================================
SUBSCRIPTION
================================================================================

--------------------------------------------------------------------------------
subscription
--------------------------------------------------------------------------------
  subscription_id  BIGSERIAL      PK
  store_id         BIGINT         NN FK -> store(store_id)  UQ
  tier             VARCHAR(40)    NN
  monthly_price    NUMERIC(10,2)  NN DEFAULT 0
  started_at       TIMESTAMP      NN DEFAULT NOW()
  ended_at         TIMESTAMP
  renewal_count    INT            NN DEFAULT 0
  savings_to_date  NUMERIC(12,2)  NN DEFAULT 0
  is_trial         BOOLEAN        NN DEFAULT FALSE
  trial_ends_at    TIMESTAMP
  trial_tier       VARCHAR(40)
  requested_tier   VARCHAR(40)
  UQ (store_id)

================================================================================
INTELLIGENCE / CART
================================================================================

--------------------------------------------------------------------------------
intelligence_log
--------------------------------------------------------------------------------
  id            BIGSERIAL    PK
  store_id      INTEGER      NN
  user_id       INTEGER
  trigger_type  VARCHAR(50)  NN
  title         TEXT         NN
  body          TEXT         NN
  payload       JSONB        NN DEFAULT '{}'
  sent_at       TIMESTAMPTZ  NN DEFAULT NOW()
  opened_at     TIMESTAMPTZ
  status        VARCHAR(20)  NN DEFAULT 'sent'  CHECK IN ('sent','failed','opened','skipped','internal')

--------------------------------------------------------------------------------
cart_session
--------------------------------------------------------------------------------
  store_id      INTEGER      PK
  item_count    INTEGER      NN DEFAULT 0
  cart_data     JSONB        NN DEFAULT '[]'
  updated_at    TIMESTAMPTZ  NN DEFAULT NOW()
  notified_at   TIMESTAMPTZ
  converted_at  TIMESTAMPTZ

================================================================================
STORE ASSOCIATIONS
================================================================================

--------------------------------------------------------------------------------
store_association
--------------------------------------------------------------------------------
  association_id        SERIAL    PK
  store_id              INTEGER   NN FK -> store(store_id) ON DELETE CASCADE
  name                  TEXT      NN
  area_type             TEXT      NN CHECK IN ('apartment','hostel','school','office','colony')
  estimated_households  INTEGER
  notes                 TEXT
  is_active             BOOLEAN   NN DEFAULT TRUE
  created_at            TIMESTAMP NN DEFAULT NOW()

================================================================================
KHATA (udhaar / credit)
================================================================================

--------------------------------------------------------------------------------
khata
--------------------------------------------------------------------------------
  khata_id     BIGSERIAL      PK
  customer_id  BIGINT         NN FK -> customer(customer_id)
  store_id     BIGINT         NN FK -> store(store_id)
  order_id     BIGINT         FK -> orders(order_id)
  amount       NUMERIC(12,2)  NN CHECK >= 0
  amount_paid  NUMERIC(12,2)  NN DEFAULT 0
  issue_date   DATE           NN
  due_date     DATE           NN
  status       VARCHAR(20)    NN DEFAULT 'open'
  notes        TEXT

--------------------------------------------------------------------------------
khata_payments
--------------------------------------------------------------------------------
  payment_id  BIGSERIAL    PK
  khata_id    BIGINT       NN FK -> khata(khata_id) ON DELETE CASCADE
  store_id    BIGINT       NN
  amount      NUMERIC      NN
  paid_at     TIMESTAMPTZ  NN DEFAULT NOW()
  notes       TEXT

================================================================================
REFERRAL SYSTEM
================================================================================

--------------------------------------------------------------------------------
referral_campaigns
--------------------------------------------------------------------------------
  campaign_id                 BIGSERIAL     PK
  store_id                    BIGINT        NN FK -> store(store_id)
  name                        VARCHAR(100)  NN
  referral_discount_pct       NUMERIC(5,2)  NN DEFAULT 10
  milestone_every_n           INT           NN DEFAULT 10
  milestone_reward_pct        NUMERIC(5,2)  NN DEFAULT 5
  max_referrals_per_referrer  INT           NN DEFAULT 50
  is_active                   BOOLEAN       NN DEFAULT TRUE
  created_at                  TIMESTAMPTZ   NN DEFAULT NOW()

--------------------------------------------------------------------------------
referral_tokens
--------------------------------------------------------------------------------
  token_id              BIGSERIAL     PK
  store_id              BIGINT        NN FK -> store(store_id)
  referrer_customer_id  BIGINT        NN FK -> customer(customer_id)
  campaign_id           BIGINT        NN FK -> referral_campaigns(campaign_id)
  token_hash            VARCHAR(64)   UQ NN
  created_at            TIMESTAMPTZ   NN DEFAULT NOW()
  UQ (referrer_customer_id, campaign_id)

--------------------------------------------------------------------------------
referrals
--------------------------------------------------------------------------------
  referral_id       BIGSERIAL     PK
  token_id          BIGINT        NN FK -> referral_tokens(token_id)
  new_customer_id   BIGINT        FK -> customer(customer_id)
  order_id          BIGINT        FK -> orders(order_id)
  discount_applied  NUMERIC(5,2)
  status            VARCHAR(20)   NN DEFAULT 'rewarded'
  created_at        TIMESTAMPTZ   NN DEFAULT NOW()

--------------------------------------------------------------------------------
referral_vouchers
--------------------------------------------------------------------------------
  voucher_id        BIGSERIAL     PK
  customer_id       BIGINT        NN FK -> customer(customer_id)
  store_id          BIGINT        NN FK -> store(store_id)
  campaign_id       BIGINT        NN FK -> referral_campaigns(campaign_id)
  discount_pct      NUMERIC(5,2)  NN
  status            VARCHAR(20)   NN DEFAULT 'pending'
  earned_at         TIMESTAMPTZ   NN DEFAULT NOW()
  used_at           TIMESTAMPTZ
  used_on_order_id  BIGINT        FK -> orders(order_id)

================================================================================
KPI EXTENSION TABLES
================================================================================

--------------------------------------------------------------------------------
footfall
--------------------------------------------------------------------------------
  footfall_id  BIGSERIAL  PK
  store_id     BIGINT     NN FK -> store(store_id) ON DELETE CASCADE
  ts           TIMESTAMP  NN
  hour         INT        NN CHECK BETWEEN 0 AND 23
  visitors     INT        NN CHECK >= 0
  UQ (store_id, ts)

--------------------------------------------------------------------------------
scheme
--------------------------------------------------------------------------------
  scheme_id    BIGSERIAL      PK
  supplier_id  BIGINT         FK -> supplier(supplier_id)
  product_id   BIGINT         FK -> product(product_id)
  name         VARCHAR(150)   NN
  scheme_type  VARCHAR(40)    NN
  value        NUMERIC(12,2)  NN DEFAULT 0
  min_qty      INT            NN DEFAULT 1
  start_date   DATE           NN
  end_date     DATE           NN

--------------------------------------------------------------------------------
scheme_claim
--------------------------------------------------------------------------------
  claim_id      BIGSERIAL      PK
  scheme_id     BIGINT         NN FK -> scheme(scheme_id) ON DELETE CASCADE
  store_id      BIGINT         NN FK -> store(store_id)
  purchase_id   BIGINT         FK -> purchases(purchase_id)
  claim_date    DATE           NN
  amount_saved  NUMERIC(12,2)  NN DEFAULT 0
  status        VARCHAR(20)    NN DEFAULT 'claimed'

--------------------------------------------------------------------------------
calendar
--------------------------------------------------------------------------------
  cal_date  DATE          PK
  festival  VARCHAR(100)
  weight    NUMERIC(4,2)  NN DEFAULT 1.0

--------------------------------------------------------------------------------
inventory_batch
--------------------------------------------------------------------------------
  batch_id           BIGSERIAL     PK
  store_id           BIGINT        NN FK -> store(store_id)
  product_id         BIGINT        NN FK -> product(product_id)
  batch_no           VARCHAR(60)
  manufactured_date  DATE
  expiry_date        DATE          NN
  qty_in_stock       INT           NN DEFAULT 0  CHECK >= 0
  markdown_pct       NUMERIC(5,2)  DEFAULT 0
  recovered_units    INT           DEFAULT 0
  wasted_units       INT           DEFAULT 0
  UQ (store_id, product_id, batch_no)

--------------------------------------------------------------------------------
shelf_planogram
--------------------------------------------------------------------------------
  plano_id    BIGSERIAL     PK
  store_id    BIGINT        NN FK -> store(store_id)
  product_id  BIGINT        NN FK -> product(product_id)
  shelf_id    VARCHAR(40)   NN
  sq_ft       NUMERIC(6,2)  NN CHECK > 0
  eye_level   BOOLEAN       NN DEFAULT FALSE
  UQ (store_id, product_id)

--------------------------------------------------------------------------------
opex
--------------------------------------------------------------------------------
  opex_id      BIGSERIAL      PK
  store_id     BIGINT         NN FK -> store(store_id)
  month_start  DATE           NN
  electricity  NUMERIC(12,2)  DEFAULT 0
  rent         NUMERIC(12,2)  DEFAULT 0
  staff        NUMERIC(12,2)  DEFAULT 0
  other        NUMERIC(12,2)  DEFAULT 0
  UQ (store_id, month_start)

--------------------------------------------------------------------------------
return_to_vendor
--------------------------------------------------------------------------------
  rtv_id            BIGSERIAL      PK
  store_id          BIGINT         NN FK -> store(store_id)
  supplier_id       BIGINT         FK -> supplier(supplier_id)
  product_id        BIGINT         NN FK -> product(product_id)
  return_date       DATE           NN
  qty_returned      INT            NN CHECK > 0
  unit_cost         NUMERIC(10,2)  NN DEFAULT 0
  recovery_pct      NUMERIC(5,2)   NN DEFAULT 0
  amount_recovered  NUMERIC(12,2)  NN DEFAULT 0
  reason            VARCHAR(60)

--------------------------------------------------------------------------------
marketing_spend
--------------------------------------------------------------------------------
  spend_id              BIGSERIAL      PK
  store_id              BIGINT         FK -> store(store_id)
  spend_date            DATE           NN
  channel               VARCHAR(40)    NN
  amount                NUMERIC(12,2)  NN
  attributed_customers  INT            NN DEFAULT 0

================================================================================
SCHEMA: kirana_olap  (analytics / OLAP)
================================================================================

--------------------------------------------------------------------------------
daily_store_sku_metrics  (PARTITIONED BY RANGE (date))
--------------------------------------------------------------------------------
  date               DATE           NN  PK (composite)
  store_id           BIGINT             PK (composite)
  product_id         BIGINT             PK (composite)
  units_sold         INT
  revenue            NUMERIC(12,2)
  profit             NUMERIC(12,2)
  stock_on_hand      INT
  lost_sales         INT
  price              NUMERIC(10,2)
  discount           NUMERIC(5,2)
  promo_flag         BOOLEAN
  avg_selling_price  NUMERIC(10,2)
  margin             NUMERIC(5,2)
  weather_temp       NUMERIC(5,2)
  rain_flag          BOOLEAN
  PK (date, store_id, product_id)
  Partitions: daily_metrics_default (DEFAULT) + monthly partitions
              created on demand via ensure_daily_metrics_partition()

--------------------------------------------------------------------------------
mv_store_daily_summary  (MATERIALIZED VIEW)
--------------------------------------------------------------------------------
  SELECT date, store_id,
         SUM(revenue)    AS total_revenue,
         SUM(profit)     AS total_profit,
         SUM(units_sold) AS total_units
  FROM kirana_olap.daily_store_sku_metrics
  GROUP BY date, store_id

================================================================================
VIEWS  (kirana_oltp)
================================================================================

product_catalog
  SELECT * FROM kirana_oltp.product
  WHERE (barcode IS NOT NULL OR is_loose = TRUE)

================================================================================
FUNCTIONS & TRIGGERS
================================================================================

kirana_olap.ensure_daily_metrics_partition(target_date DATE) -> VOID
    Creates the monthly partition of daily_store_sku_metrics for the given date.

kirana_olap.populate_daily_metrics(target_date DATE) -> VOID
    Aggregates orders/order_item/inventory into daily_store_sku_metrics
    (upsert on conflict).

kirana_oltp.update_inventory_on_sale() -> TRIGGER
    BEFORE INSERT trigger fn: decrements inventory.quantity, validates stock,
    and logs an inventory_movements 'sale' row.

TRIGGER trg_inventory_on_sale
    BEFORE INSERT ON kirana_oltp.order_item
    FOR EACH ROW EXECUTE kirana_oltp.update_inventory_on_sale()

================================================================================
INDEXES
================================================================================

  uidx_users_phone               users(phone_number) WHERE phone_number IS NOT NULL  [UNIQUE]
  idx_product_category           product(category_id)
  idx_product_brand              product(brand)
  idx_product_loose              product(is_loose)
  idx_product_barcode            product(barcode) WHERE barcode IS NOT NULL
  idx_orders_store_date          orders(store_id, order_date DESC)
  idx_inventory_store_product    inventory_snapshots(store_id, product_id)
  idx_customer_store_id          customer(store_id)
  idx_customer_store_phone       customer(store_id, phone)
  idx_user_fcm_tokens_user_id    user_fcm_tokens(user_id)
  idx_app_activity_user_id       app_activity(user_id, created_at)
  idx_khata_payments_khata_id    khata_payments(khata_id)
  idx_store_association_store    store_association(store_id)
  idx_intel_log_store            intelligence_log(store_id, sent_at DESC)
  idx_intel_log_trigger          intelligence_log(trigger_type, sent_at DESC)
  idx_footfall_store_ts          footfall(store_id, ts)
  idx_scheme_dates               scheme(start_date, end_date)
  idx_scheme_claim_store         scheme_claim(store_id, claim_date)
  idx_khata_store_status         khata(store_id, status)
  idx_khata_due                  khata(due_date)
  idx_batch_store_expiry         inventory_batch(store_id, expiry_date)
  idx_rtv_store_date             return_to_vendor(store_id, return_date)
  idx_subscription_store_active  subscription(store_id) WHERE ended_at IS NULL
  idx_mv_store_date              mv_store_daily_summary(store_id, date)

================================================================================
END OF SCHEMA
================================================================================
```
