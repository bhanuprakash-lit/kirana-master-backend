# Call Center / Tele-calling Feature — Design Spec

**Status:** v1 IMPLEMENTED (2026-07-07) — approved with all four recommended defaults.
Not yet deployed/committed. See "Implementation notes" at the bottom.
**Scope:** Admin backend (`kirana-master-backend`) + admin panel (`admin-panel`).

Call executives phone kirana store owners to drive app adoption, remind them to use
features, and capture structured feedback. Every call becomes an attributed record:
answered or not, is the store using the app, what feedback, and what happens next.

---

## Recommended defaults (baked into this spec — change any before we build)

These are the answers to the four open decisions; the spec below assumes them.

1. **Executive login → per-executive accounts** with a `call_executive` role. Each
   exec gets a username + password; every call/feedback is attributed to a real
   person. A password-login path is added to the panel *alongside* the existing
   shared-key login. (This is the one real architectural change — see §6.)
2. **Telephony → manual logging in v1.** Exec dials on their own phone, logs the
   outcome. Schema is designed so click-to-call (Exotel/Knowlarity) slots in later
   via `call_log.recording_url` / `duration_sec` (already reserved).
3. **v1 scope → core only** (see §7 phasing). Dashboards, campaigns, telephony are
   v2/v3.
4. **Executive visibility → focused, no financials.** Owner name+phone, engagement
   signals, and call history — no revenue figures, no other execs' data.

---

## 1. Roles & responsibilities

| Role | Sees | Can do | Cannot do |
|------|------|--------|-----------|
| **Call Executive** (`call_executive`) | Only *assigned* stores | Work prioritized queue, log calls + feedback, schedule callbacks, flag escalations | See unassigned stores, financials, other execs' data, admin settings |
| **Team Lead / Manager** (`call_manager`) | All stores + all execs | Everything an exec does + CRUD execs, assign/rebalance stores, run campaigns, view performance, read all feedback, resolve escalations | Platform admin settings unless also admin |
| **Admin** (existing shared key) | Everything | Full access incl. all of the above | — |

Role gating in the panel: an authenticated executive gets a reduced sidebar
(My Queue / My Callbacks / My Stats). Managers/admins get the full **Call Center**
section plus the rest of the admin nav.

---

## 2. Executive workflow (the daily loop)

1. **Login** → lands on **My Queue**: their assigned stores ranked by a
   "needs-attention" score (§5), not a flat list.
2. **Open a store → Call Sheet**: owner name + phone, engagement snapshot
   (last app open, sales today/this week, subscription tier, trial days left),
   and full prior call history for that store.
3. **Call** (own phone in v1), then **log** via dropdowns:
   - **Answered?** → `answered` / `no_answer` / `busy` / `switched_off` /
     `wrong_number` / `invalid_number`
   - If answered → **app usage** → `using_active` / `using_rare` / `stopped` /
     `never_started` / `needs_training`
   - **Feedback** free text + **tags** (`bug` / `feature_request` / `pricing` /
     `training` / `happy` / `churn_risk`) + **sentiment**
     (`positive`/`neutral`/`negative`, auto-suggested from tags, editable)
   - **Rating** 1–5, **next action** → `callback` / `escalate` / `done` /
     `do_not_call`
4. **Callback** (if scheduled) resurfaces in the queue at `callback_at`; overdue
   ones highlight.

---

## 3. Data model (new `kirana_oltp` tables)

DDL sketch — final version boot-migrated via `base.py::_ensure_schema` +
`db_generation/ensure_full_schema.py`, matching house style (idempotent
`CREATE TABLE IF NOT EXISTS` + additive `ADD COLUMN IF NOT EXISTS`).

```sql
-- Executives / managers who use the call-center panel.
CREATE TABLE kirana_oltp.call_executive (
    executive_id  BIGSERIAL PRIMARY KEY,
    username      VARCHAR(100) UNIQUE NOT NULL,
    full_name     VARCHAR(255) NOT NULL,
    phone         VARCHAR(20),
    email         VARCHAR(255),
    role          VARCHAR(20) NOT NULL DEFAULT 'call_executive', -- | 'call_manager'
    password_salt VARCHAR(64),
    password_hash VARCHAR(128),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Which exec owns which store. status lets us unassign without losing history.
CREATE TABLE kirana_oltp.store_assignment (
    assignment_id BIGSERIAL PRIMARY KEY,
    store_id      BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    executive_id  BIGINT NOT NULL REFERENCES kirana_oltp.call_executive(executive_id) ON DELETE CASCADE,
    assigned_by   BIGINT,           -- executive_id of the manager (or NULL = admin key)
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status        VARCHAR(20) NOT NULL DEFAULT 'active',  -- active | unassigned
    priority      SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (store_id, executive_id, status)   -- one active assignment per store/exec
);
CREATE INDEX idx_store_assignment_exec ON kirana_oltp.store_assignment(executive_id, status);

-- The heart: one row per call attempt.
CREATE TABLE kirana_oltp.call_log (
    call_id          BIGSERIAL PRIMARY KEY,
    store_id         BIGINT NOT NULL REFERENCES kirana_oltp.store(store_id) ON DELETE CASCADE,
    executive_id     BIGINT NOT NULL REFERENCES kirana_oltp.call_executive(executive_id),
    called_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered         BOOLEAN,          -- NULL until logged
    disposition      VARCHAR(24) NOT NULL,   -- answered|no_answer|busy|switched_off|wrong_number|invalid_number
    app_usage_status VARCHAR(24),      -- using_active|using_rare|stopped|never_started|needs_training
    feedback_text    TEXT,
    sentiment        VARCHAR(12),      -- positive|neutral|negative
    rating           SMALLINT,         -- 1..5
    next_action      VARCHAR(16),      -- callback|escalate|done|do_not_call
    callback_at      TIMESTAMPTZ,      -- when next_action='callback'
    duration_sec     INT,              -- reserved for telephony (v3)
    recording_url    TEXT,             -- reserved for telephony (v3)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_call_log_store ON kirana_oltp.call_log(store_id, called_at DESC);
CREATE INDEX idx_call_log_exec  ON kirana_oltp.call_log(executive_id, called_at DESC);
CREATE INDEX idx_call_log_callback ON kirana_oltp.call_log(callback_at)
    WHERE next_action = 'callback';

-- Multi-tag a call for the product-feedback digest.
CREATE TABLE kirana_oltp.call_feedback_tag (
    id      BIGSERIAL PRIMARY KEY,
    call_id BIGINT NOT NULL REFERENCES kirana_oltp.call_log(call_id) ON DELETE CASCADE,
    tag     VARCHAR(24) NOT NULL   -- bug|feature_request|pricing|training|happy|churn_risk
);
CREATE INDEX idx_call_feedback_tag_call ON kirana_oltp.call_feedback_tag(call_id);

-- v2: calling campaigns (targeted drives with a script).
CREATE TABLE kirana_oltp.call_campaign (
    campaign_id BIGSERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    script      TEXT,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      VARCHAR(16) NOT NULL DEFAULT 'active'  -- active|closed
);
-- campaign membership + per-call campaign linkage added in v2.
```

**Store "do-not-call":** the latest `call_log.next_action = 'do_not_call'` per store
is treated as a DNC flag (surfaced in the queue + respected by campaigns). No extra
column needed for v1.

---

## 4. Backend API

Two auth surfaces on the same FastAPI app:

- **Manager/admin endpoints** (`X-API-Key` admin OR `call_manager` session token) —
  under `/kirana/admin/callcenter/*`.
- **Executive endpoints** (`call_executive` session token) — under
  `/kirana/callcenter/*`, always scoped to the logged-in exec's assignments.

### Auth
- `POST /kirana/callcenter/login` → username+password → session token
  (mirror the existing user-session pattern; token in a new `call_executive_session`
  table or reuse `user_sessions` shape). Returns role so the panel renders the right nav.
- `GET  /kirana/callcenter/me` → current exec profile + role.

### Manager (`/kirana/admin/callcenter/...`)
- `GET/POST/PATCH /executives` — CRUD, activate/deactivate, reset password.
- `GET  /executives/{id}/performance` — calls/day, connect rate, conversion, avg rating.
- `POST /assignments` — assign stores (single/bulk), `POST /assignments/auto` —
  round-robin or by region; `DELETE /assignments/{id}` — unassign.
- `GET  /assignments/load` — store-count per exec (workload balancing).
- `GET  /feedback` — all feedback, filter by tag/sentiment/usage/date (the digest).
- `GET  /performance` — leaderboard + funnel + coverage SLA (v2).
- Campaigns CRUD (v2).

### Executive (`/kirana/callcenter/...`)
- `GET  /queue?limit=` — assigned stores ranked by needs-attention score, each with
  the engagement snapshot + last-call summary.
- `GET  /stores/{id}` — call sheet (focused view; no financials) + call history.
- `POST /calls` — log a call (creates `call_log` + tags).
- `GET  /callbacks` — my due/overdue callbacks.
- `GET  /stats` — my personal numbers.

All executive endpoints enforce that `store_id` is in the caller's active assignments
(403 otherwise) — the same store-scoping discipline used across the codebase.

---

## 5. Needs-attention score (queue ranking)

Computed from data we **already** collect — no new tracking. Higher = call sooner.

- **Overdue callback** → top priority (explicitly promised).
- **Never onboarded / never_started** (no `app_activity`, no orders) → high.
- **Trial ending soon** (`subscription.trial_ends_at` within N days) → high.
- **Usage dropped** (had activity, now silent > N days via `app_activity.foreground`) → medium.
- **Never called / stale** (no `call_log` in > N days) → medium.
- **DNC** (`do_not_call`) → excluded.

Signals sourced from the same queries `/admin/user-activity` already runs
(`app_activity`, `user_sessions`, `orders`, `subscription`).

---

## 6. The one architectural change: per-executive identity

Today the panel authenticates with a **single shared admin API key** (`X-API-Key`),
role hard-coded `admin`; there is no per-person login. For attribution we add:

- A `call_executive` table (credentials) + a session-token login endpoint.
- The panel's **Login** gains a second mode: "Executive login" (username+password)
  that stores a session token instead of the shared key. `api.js` sends
  `Authorization: Bearer <token>` when present, else falls back to `X-API-Key`.
- `App.jsx` reads the role from `/callcenter/me` and renders exec vs manager/admin nav.
- Existing admin-key flow is **unchanged** — admins keep using the key; executives
  are the new session-based path.

This is isolated and additive; no change to how store-owner app auth works.

---

## 7. Phasing

**v1 — core (this build):**
executives + login + role gating, store assignment (single/bulk), prioritized queue,
call sheet (focused view), call logging (dispositions/usage/feedback/tags/sentiment),
callbacks, per-exec attribution, basic per-exec stats, StoreDetail "Call History" tab.

**v2 — advanced:**
smart score tuning, manager performance dashboards + SLA/coverage, feedback digest →
route bugs/features into the existing Support/Issues page, campaigns with scripts,
DNC handling UI, escalations, optional WhatsApp post-call follow-up (reuse `whatsapp/`).

**v3 — telephony + AI:**
click-to-call with number masking + recording via Exotel/Knowlarity
(`duration_sec`/`recording_url` already reserved), later AI call summaries/sentiment
from transcripts.

---

## 8. Integration touchpoints (existing code)

- **`admin-panel/src/components/Sidebar.jsx`** — new 📞 Call Center section (role-gated).
- **`admin-panel/src/App.jsx`** — routes + role-based nav; **`Login.jsx`** — exec login mode.
- **`admin-panel/src/api.js`** — Bearer-token support + callcenter methods.
- **`kirana/routers/`** — new `callcenter.py` router (+ manager routes; wire in `main.py`).
- **`kirana/repositories/`** — new `callcenter.py` repository (SQL), house-style.
- **`StoreDetail.jsx`** — "Call History & Feedback" tab.
- **Support/Issues** page — destination for bug/feature-tagged feedback (v2).
- **`whatsapp/`** — post-call follow-up (v2).

---

## 9. Open questions captured (answered by §Recommended defaults; reconfirm)

1. Exec login: **per-exec accounts** (recommended) vs extend `users` vs shared-key+name.
2. Telephony: **manual logging v1** (recommended) vs click-to-call day one.
3. v1 scope: **core only** (recommended) vs +dashboards vs +campaigns.
4. Exec visibility: **focused, no financials** (recommended) vs full store detail.

---

## Implementation notes (v1 as built, 2026-07-07)

**Backend** — self-contained `callcenter/` package (mirrors `vision/`):
- `callcenter/repository.py` (engine-based SQL), `routes.py`, `schemas.py`; wired in
  `main.py`. Tables added to both `base.py::_ensure_schema` (boot path) and
  `db_generation/ensure_full_schema.py`: `call_executive`, `call_executive_session`,
  `store_assignment` (unique partial index → one active assignment per store),
  `call_log`, `call_feedback_tag`.
- Password hashing = same `sha256(salt+password)` scheme as app users; bearer session
  tokens in `call_executive_session` (30-day validity, revocable on logout).
- Endpoints under `/kirana/callcenter/*` (17 routes). Two auth surfaces: admin
  `X-API-Key` (full manager access) and executive bearer token (role gates reach —
  `call_manager` = manager, `call_executive` = own assigned stores only, enforced by
  `_require_store_access`). Manager oversight endpoints also accept the admin key so
  the existing admin panel works without executive accounts.
- Needs-attention queue score computed in Python from SQL-computed integer deltas
  (avoids tz-aware/naive datetime mixing). Callback due = 10000 (always tops), then
  never-called / stale / trial-ending / no-sales-7d / inactive-login. DNC excluded.
- Signals reuse existing tables: `users` (owner+phone), `subscription` (trial),
  `user_sessions` (last login), `orders` (7-day sales). Focused sheet carries NO
  financial figures.

**Frontend** — `admin-panel`:
- `api.js` gained bearer-token support (`configureExecutive`) + `cc*` methods.
- `Login.jsx` has Admin / Call Executive tabs; `App.jsx` stores the session and
  renders mode/role-based routes; `Sidebar.jsx` role-gated nav.
- Pages under `src/pages/callcenter/`: manager `Executives`, `Assignments`,
  `Feedback`; executive `Queue`, `Callbacks`, `Stats`; shared `CallSheet` modal
  (focused context + call history + log form with dispositions/usage/feedback/
  tags/sentiment/rating/next-action/callback); `constants.js` (enum labels).
- `StoreDetail.jsx` gained a "Call History & Feedback" section.

**Tests/verification:** `tests/db/test_callcenter_repository.py` (10) +
`tests/routes/test_callcenter_routes.py` (4) → full suite 204 passed. HTTP e2e
(manager+exec auth, assignment, queue scoring, store-scoping 403s, logging,
callbacks supersede, stats, feedback filters, assignable-stores, logout) verified
against docker Postgres 16. `npm run build` clean (64 modules).

**To go live:** backend redeploy (tables migrate on boot) + admin-panel rebuild.
Bootstrap: an admin (API key) creates the first executive/manager via the
Executives page. **v2/v3** (dashboards/SLA, campaigns, telephony click-to-call) per
the phasing above remain future work; `call_log.duration_sec`/`recording_url` are
already reserved for telephony.
