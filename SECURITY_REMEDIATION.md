# Security Remediation — Pentest + SAST (2026-07-16)

Response to `pentesting.md` (Burp API authorization assessment) and
`Backend_Sast_OutletAI_26_07_09.docx` (Bandit SAST triage). Fixes below;
items marked **false positive** were verified in code and needed no change.

## Pentest (IDOR / access control)

| Finding | Verdict | Fix |
|---|---|---|
| **F1 — KPI endpoints IDOR (29)** | **Confirmed** | Router-level `_enforce_store_scope` on the KPI router: non-admins must pass their **own** `store_id` (read from the raw query string, so it can't be dodged by omitting the param and falling through to the `store_id=1` default). Admins unrestricted. `/registry` (catalogue metadata, no store data) exempt. |
| **F2 — Forecast endpoints IDOR (4)** | **Confirmed** | Same router-level guard on the forecasting router. |
| F3–F6, F9 — admin store endpoints | **False positive** | `admin.py` / `subscriptions.py` / `staff.py` / `warranty.py` / `multistore.py` already gate every handler with `if user.get("role") != "admin": 403`. Report enumerated routes without reading handler bodies. |
| **F7 — `campaigns/recommended`** | **Confirmed** | Non-admins pinned to their own store; a passed `store_id` is ignored for them. |
| **F8 — `referral/token`, `referral/vouchers`** | **Confirmed** | Ownership check (`role==admin or store_id==owned`) added to both. |
| F10 — call center store access | **False positive** | `call_sheet` / `log_call` already call `_require_store_access` → `is_assigned`, raising 403 for unassigned stores. |
| F11 — token-scoped `store_id=None` for API key | Low / by design | The API key is the admin identity; those endpoints returning cross-store data under the admin key is expected. Store-bearer users always carry a concrete `store_id`. |
| F12 — CORS `*`, username enumeration, POS token errors | Low / accepted | Auth is header-based (Bearer), not cookie, so the CORS wildcard is not credential-exploitable; browsers reject `*`+credentials anyway. Username/token oracle endpoints are low-severity; rate-limiting tracked as follow-up. |

## SAST (Bandit triage)

| Finding | Verdict | Fix |
|---|---|---|
| **F01 — Hardcoded DB credentials (Critical)** | **Confirmed + expanded** | Removed the hardcoded weak local password from `ml_models/config.py`, `ml_models/kpi_models/train_kpi_models.py`, root `config.py`, and `db_generation/ensure_full_schema.py` (env-only, `PG*` fallbacks, no literal password). **Also found and removed a live Azure dev DB password hardcoded in 6 ops/demo scripts** — those now require `AZURE_DB_URL`/`DATABASE_URL`. **⚠ Both exposed credentials must be ROTATED** (assumed compromised — present in git history). Actual values are intentionally not reproduced in this document. |
| **F02 — Unparameterized dates in shrinkage query** | **Confirmed** | Bound `opening_date`/`closing_date` as `%(name)s` params via `_q(..., params=...)`. |
| **F03 — String-built store_id IN-lists (×10)** | **Confirmed** | `data_loader._in()` now `int()`-coerces every element before it reaches SQL text (covers all 10 sites; raises on non-numeric). |
| F04 — Dynamic product table name | **False positive** | `_product_tbl()` returns one of two hardcoded literal table names via a set-membership check; `store_id` is never interpolated into an identifier. |
| **F05 — WhatsApp session SET-clause from kwargs** | **Confirmed** | `WaSessionStore.update()` now allowlists updatable column names and raises on anything else — a webhook-derived key can no longer become a SQL identifier. |
| **F06 — Dynamic DELETE target in demo rollback** | **Confirmed** | `fancy_demo_rollback.delete_ids()` validates `(table, id_col)` against a closed allowlist before formatting the DELETE. |
| F07 — bare `except: pass` | Low / deferred | Observability nit, not a security control; left as opportunistic cleanup. |

## Required operator follow-ups (not code)

1. **Rotate** the PostgreSQL passwords that were in source (the weak local default and the Azure dev password) in every environment — assume exposed via git history. The `.claude/settings.local.json` permission log on dev machines also contains the Azure password in example commands; scrub it there too.
2. Add a git-history secret scanner (gitleaks / trufflehog) to CI to prevent re-introduction.
3. Consider purging the leaked secrets from git history (BFG / `git filter-repo`).
