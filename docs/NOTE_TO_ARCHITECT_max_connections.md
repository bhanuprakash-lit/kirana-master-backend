# Note to Solution Architect — `max_connections` after the Postgres downgrade

**Date:** 2026-07-13
**From:** Engineering
**Re:** `psql-lohiya-kirana` (and UAT) after the GP → Burstable `B1ms` downgrade
**Priority:** Medium — no incident yet, but a latent risk on the 2 GiB tier.

---

Thanks for actioning the tier downgrade — the cost drop looks great (both servers now `B1ms`, ~₹40k → ~₹4k/mo expected).

One follow-up the downgrade left behind: **`max_connections` did not scale down with the tier.**

**What I found (verified via `az postgres flexible-server parameter show`):**

| Server | Tier / RAM | `max_connections` | App can open* |
| :--- | :--- | :--- | :--- |
| `psql-lohiya-kirana` (dev/prod) | `B1ms` / 2 GiB | **1718** | up to ~450 |
| `psql-lohiya-uat` | `B1ms` / 2 GiB | 50 (default) | up to ~90 |

\* App pool = `pool_size=15 + max_overflow=30` = 45 per replica (`main.py`), × up to 10 replicas (dev) / 2 (uat).

**Why it matters:** `1718` was computed for the old 8 GiB General Purpose tier. On a 2 GiB `B1ms`, each backend connection costs several MB of RAM — allowing 1718 means a connection spike would drive the server into **memory exhaustion / OOM** rather than cleanly rejecting new connections. Current live usage is only ~16–18 connections, so there's no problem today; this is about the failure mode under load.

**Suggested action (your call):**
1. **Dev/prod:** lower `max_connections` to **~150–200** — comfortably above real usage (~18) and the app ceiling for the current replica count, while keeping RAM safe.
2. **UAT:** `50` is a bit tight vs. the app's ~90 ceiling; consider **~100** so load tests don't hit *"too many connections"*.

Reference commands (needs a restart to take effect):
```bash
az postgres flexible-server parameter set \
  -g rg-lohiya-outlet-dev -s psql-lohiya-kirana \
  --name max_connections --value 200

az postgres flexible-server parameter set \
  -g rg-lohiya-outlet-UAT -s psql-lohiya-uat \
  --name max_connections --value 100
```

**Also on your radar (not blocking):** `B1ms` is 1 vCPU + limited burst credits + 2 GiB RAM. Fine at current load, but heavy KPI/analytics or Vision batch queries could exhaust burst credits or spill large sorts to disk. Worth watching the `cpu_credits_remaining` metric; if it trends to zero under real load, `B2ms` (2 vCore / 8 GiB) is the safe step up.

Happy to apply the `max_connections` change during a maintenance window once you confirm the target values — I haven't changed anything.

— Engineering
