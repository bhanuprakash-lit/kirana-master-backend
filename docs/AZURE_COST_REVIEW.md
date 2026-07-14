# Azure Cost Review — Backend (`rg-lohiya-outlet-dev`)

**Date:** 2026-07-13
**Prepared by:** Engineering (for Solution Architect review)
**Subscription:** `subscription-LohiyaAI-Azure` (`870f8134-85c4-4dd8-b598-9882b99bf6e8`)
**Resource Group:** `rg-lohiya-outlet-dev`
**Region:** Central India
**Status:** 🟢 **Partially actioned by the Solution Architect (2026-07-13)** — both PostgreSQL servers downgraded to Burstable. See **Section 1a — Changes Applied**. Remaining items (incl. one follow-up the downgrade introduced) still awaiting decision.

---

## 1. Executive Summary

The monthly backend bill is **~₹60,000+**, of which **~₹40,000 is attributed to Azure Database for PostgreSQL** (per the Cost Analysis view shared by the team).

**Root cause:** the PostgreSQL Flexible Server is provisioned on the **General Purpose** tier (`Standard_D2ds_v5`, 2 dedicated vCores, always-on) but is running at **~2–5% CPU utilisation** — i.e. it is heavily over-provisioned. General Purpose is Azure's premium, dedicated-vCore tier and costs roughly **3× the equivalent Burstable tier**. A PostgreSQL Flexible Server bills for **every hour it is running** (it cannot scale to zero), so an idle over-sized server is paid for 24×7.

**Primary recommendation:** downgrade the compute tier from **General Purpose → Burstable** (`Standard_B2ms`, same 2 vCore / 8 GiB, burstable billing). Expected to cut PostgreSQL compute cost by **~60–65%** with no capacity loss for the current workload.

> **Important caveat:** despite the `-dev` naming, this server is the **live production database** (the production container app `ca-lohiya-outlet` connects to it). Any change causes a short restart and must be treated as a production maintenance action.

---

## 1a. Changes Applied (Solution Architect — 2026-07-13)

The architect actioned the primary recommendation on **both** PostgreSQL servers. Verified via `az` after the change:

| Resource | Before | After | Notes |
| :--- | :--- | :--- | :--- |
| **psql-lohiya-kirana** (dev/prod) | General Purpose `D2ds_v5` (2 vCore / 8 GiB) | **Burstable `B1ms` (1 vCore / 2 GiB)** | More aggressive than the `B2ms` proposed — defensible: DB holds **~81 MB** data, sits at **~2.5% CPU** |
| **psql-lohiya-uat** (`rg-lohiya-outlet-UAT`) | General Purpose `D2ds_v5` (2 vCore / 8 GiB) | **Burstable `B1ms` (1 vCore / 2 GiB)** | Same downgrade |
| Storage | 128 GB (dev) / 32 GB (uat) | unchanged | Expected — Flexible Server storage cannot be shrunk in place |
| `rg-lohiya-outlet-QA` | — | **created (empty)** | Placeholder QA env; nothing deployed yet (QA app still runs as `ca-lohiya-outlet-qa` in the dev RG) |
| Log Analytics (UAT) | — | new `workspace-rglohiyaoutletex2v` | Minor |

**Estimated effect:** each GP `D2ds_v5` ≈ ₹18–20k/mo compute → `B1ms` ≈ ₹1.5–2k/mo. Across both servers the PostgreSQL line should fall from **~₹40k → ~₹4k/mo** — the majority of the ₹60k bill. *(Confirm on the next invoice / Cost Analysis.)*

### ⚠️ Follow-up introduced by the downgrade — needs architect action

1. **`max_connections` is stale on the dev server.** It reads **`1718`** (a value computed for the old 8 GiB GP tier); it did **not** auto-reduce on the downgrade. On a 2 GiB `B1ms`, permitting 1718 connections risks **RAM exhaustion / OOM** under a connection spike instead of a clean rejection. The app opens up to 45 connections per replica (`pool_size=15 + max_overflow=30`) × up to 10 replicas ≈ 450 possible. **Recommend lowering `max_connections` to ~150–200.**
2. **UAT is at `max_connections = 50`** (tier default) but its app can open ~90 (2 replicas × 45) — could hit *"too many connections"* under load. Worth aligning.
3. **B1ms = 1 vCPU + limited burst credits, 2 GiB RAM.** Fine at today's load, but heavy KPI/analytics queries or Vision batches may exhaust burst credits (throttle to baseline) or spill large sorts to disk. **Watch `cpu_credits_remaining`.** If it bites, `B2ms` (2 vCore / 8 GiB) is the safe middle ground.

> A short note covering item 1 was sent to the architect — see `docs/NOTE_TO_ARCHITECT_max_connections.md`.

---

## 2. Current Architecture — `rg-lohiya-outlet-dev`

| Resource | Type | Configuration | Cost significance |
| :--- | :--- | :--- | :--- |
| **psql-lohiya-kirana** | PostgreSQL Flexible Server | **General Purpose `Standard_D2ds_v5`** (2 vCore / 8 GiB), **128 GB** storage, IOPS 500, PG v18, HA **off**, geo-backup **off**, 7-day backup, auto-grow **off** | 🔴 **~₹40k / month** |
| ca-lohiya-outlet | Container App (prod) | 0.5 vCPU / 1 GiB, **min 1** replica, max 10, single-revision, Consumption plan | 🟡 Low |
| ca-lohiya-outlet-qa | Container App (QA) | 0.5 vCPU / 1 GiB, **min 0** replicas (scales to zero) | 🟢 ~0 when idle |
| cae-lohiya-outlet | Container Apps Managed Env | Consumption workload profile | 🟢 Negligible |
| crlohiyakirana | Container Registry (ACR) | **Standard** SKU, admin user enabled | 🟡 ~$20/mo |
| stlohiyaml | Storage Account | StorageV2, **Standard_LRS**, Hot tier | 🟢 Low |
| laws-outlet-dev | Log Analytics Workspace | `pergb2018`, 30-day retention, **no daily ingestion cap** | 🟡 Variable |
| kv-lohiya-outlet | Key Vault | — | 🟢 Negligible |
| kv-lohiya-qa | Key Vault | — | 🟢 Negligible |
| kv-lohiya-dev | Key Vault | — | 🟢 Negligible |

**Deployment mechanism:** `deploy.ps1` builds the Docker image, pushes to ACR (`crlohiyakirana`), and updates the container app `ca-lohiya-outlet`. No infrastructure-as-code is in use; all resources are managed manually.

---

## 3. Evidence — the database is over-provisioned

Live utilisation metrics pulled from Azure Monitor (`az monitor metrics list`) for `psql-lohiya-kirana`:

| Metric | Observed | Provisioned capacity | Headroom used |
| :--- | :--- | :--- | :--- |
| **CPU** | **2.5% avg, 5.3% max** | 2 dedicated vCores | ~5% |
| **Memory** | **~42%** (~3.4 GiB) | 8 GiB | ~42% |
| **Active connections** | **16–18** | thousands supported | trivial |

**Interpretation:** the server almost never uses CPU. A dedicated (General Purpose) tier only pays off under sustained CPU load; a **Burstable** tier is designed for exactly this profile — low baseline with occasional spikes — and is far cheaper. Memory usage (~3.4 GiB) rules out the smallest Burstable SKUs but is comfortably within an 8 GiB Burstable SKU.

---

## 4. Why the cost is what it is (reasons)

1. **Wrong compute tier.** General Purpose = dedicated vCores billed 24×7. For a ~5%-CPU workload this is the single largest source of waste. Burstable would deliver the same experience at ~⅓ the compute price.
2. **No scale-to-zero for databases.** Unlike Container Apps, a PostgreSQL Flexible Server bills continuously while running. An over-sized always-on server therefore compounds every hour.
3. **Over-provisioned storage.** 128 GB is provisioned (and billed) regardless of actual usage. *Note:* Flexible Server storage can only be **increased**, never decreased in place — reducing it requires a rebuild/migration.
4. **Uncapped log ingestion.** Log Analytics has no daily quota; a verbose logging period can inflate cost unexpectedly.
5. **No cost guardrails.** No budget alerts, no IaC to enforce tiers, and `-dev`-named resources are actually serving production — making it easy for an expensive tier to persist unnoticed.

> **Data-access limitation for this review:** the account used to gather this data is **RBAC-denied on the Azure Cost Management API**, so the exact per-resource rupee breakdown could not be exported programmatically. The ₹40k figure is as reported by the team's Cost Analysis view; the tier/utilisation diagnosis above is from live resource config + Azure Monitor metrics. **The architect should confirm the exact split in Portal → Cost Analysis, scoped to this resource.**

---

## 5. Recommended Actions (for architect approval)

Ranked by savings-to-risk. **None executed — awaiting sign-off.**

### 🎯 Action 1 — Downgrade PostgreSQL: General Purpose → Burstable *(primary fix)*
- **Target SKU:** `Standard_B2ms` — **identical 2 vCore / 8 GiB**, burstable billing. Drop-in; no capacity reduction.
- **Expected saving:** ~60–65% of PostgreSQL compute.
- **Impact:** in-place tier change → **~2–5 min restart** (brief production downtime). Schedule in a low-traffic window.
- **Command (for the architect to run/approve):**
  ```bash
  az postgres flexible-server update \
    -g rg-lohiya-outlet-dev -n psql-lohiya-kirana \
    --tier Burstable --sku-name Standard_B2ms
  ```
- **Rollback:** the change is reversible — re-running with `--tier GeneralPurpose --sku-name Standard_D2ds_v5` restores the original tier (another short restart).
- **Alternative (more aggressive):** `Standard_B2s` (2 vCore / **4 GiB**) is cheaper still, but the ~3.4 GiB working set leaves little headroom — **not recommended** without a load test.

### Action 2 — Reduce provisioned storage (larger effort, optional)
- Current 128 GB cannot be shrunk in place. If actual DB size is small (to be confirmed), a rebuild on a fresh Burstable server with ~32 GB would save the storage delta. **Only pursue if maximum savings are required** — involves a migration and downtime.

### Action 3 — ACR Standard → Basic
- If total repository storage < 10 GB, `Basic` (~$5/mo) replaces `Standard` (~$20/mo).
  ```bash
  az acr update -n crlohiyakirana --sku Basic
  ```

### Action 4 — Cap Log Analytics ingestion
- Set a daily quota to prevent runaway ingestion cost.
  ```bash
  az monitor log-analytics workspace update \
    -g rg-lohiya-outlet-dev -n laws-outlet-dev --quota <GB_per_day>
  ```

### Action 5 — Container Apps
- Already efficient (QA scales to zero; prod min-1 at 0.5 vCPU is a few dollars/month). **No change recommended.**

---

## 6. Prevention — guardrails to stop recurrence

1. **Set a subscription/RG budget + alerts.** Configure Cost Management budget alerts at, e.g., ₹30k / ₹45k / ₹60k thresholds so overruns are caught early.
   ```bash
   az consumption budget create --budget-name rg-lohiya-monthly \
     --amount 45000 --time-grain Monthly --category Cost \
     --resource-group rg-lohiya-outlet-dev
   ```
2. **Right-size to Burstable by default** for all non-production and low-load databases; reserve General Purpose for workloads with proven sustained CPU load.
3. **Fix environment naming.** A production database living in a `-dev` resource group hides its true criticality and invites both over- and under-provisioning. Separate PROD into its own resource group (aligns with the existing `AZURE_ENV_STRATEGY.md` proposal).
4. **Adopt Infrastructure-as-Code (Bicep/Terraform).** Pin SKUs/tiers in code so an expensive tier can't be introduced or persist silently; also enables reviewable change history.
5. **Monthly cost + utilisation review.** Check CPU/memory metrics vs. tier quarterly; downgrade anything consistently under ~20% CPU.
6. **Grant read-only Cost Management access** to the engineering account so future reviews can pull exact figures without escalation.
7. **Evaluate Reserved Capacity** for PostgreSQL **only after** right-sizing — a 1-year reservation on the correct (Burstable/GP) SKU yields further savings, but reserving an over-sized SKU locks in the waste.

---

## 7. Open Questions for the Architect

1. Confirm the exact ₹ breakdown in Cost Analysis scoped to `psql-lohiya-kirana` (compute vs. storage vs. backup).
2. Approve target SKU: `Standard_B2ms` (safe, same specs) vs. `Standard_B2s` (cheaper, needs load test)?
3. Approve a maintenance window for the ~2–5 min restart.
4. Approve secondary actions (ACR → Basic, Log Analytics cap, budget alerts)?
5. Decision on separating PROD into its own resource group / adopting IaC.

---

**Reviewer Sign-off**
Solution Architect: ________________________  Date: __________
Manager: ________________________  Date: __________
