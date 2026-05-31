# Azure CLI Commands — Kirana Backend

> Reference for the architect. No credentials included.  
> All commands target resource group `rg-lohiya-outlet-dev`, region `Central India`.

---

## Resources in Use

| Type | Name |
|---|---|
| Container Registry (ACR) | `crlohiyakirana` |
| Container App Environment | `cae-lohiya-outlet` |
| Container App | `ca-lohiya-outlet` |
| PostgreSQL Flexible Server | `psql-lohiya-kirana` |
| Database | `db-kirana-dev` |

---

## 1. Authentication

```bash
# Verify you are logged in and see the active subscription
az account show

# Interactive login (opens browser)
az login
```

---

## 2. Container Registry (ACR)

```bash
# Retrieve ACR admin credentials (used by docker login)
az acr credential show --name crlohiyakirana

# Log Docker into ACR so images can be pushed
docker login <acr-login-server> -u <username> -p <password>

# Build a Docker image tagged for ACR
docker build -t <acr-login-server>/kirana-backend:<tag> .

# Push the built image to ACR
docker push <acr-login-server>/kirana-backend:<tag>
```

---

## 3. Container App — Deploy

```bash
# Update the Container App to run a new image tag
# This triggers a rolling restart with zero downtime
az containerapp update \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --image <acr-login-server>/kirana-backend:<tag>
```

---

## 4. Container App — Configuration

```bash
# Read the full Container App configuration (JSON)
# Used to inspect current env vars, scale rules, ingress, etc.
az containerapp show \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev

# Get just the public FQDN (hostname)
az containerapp show \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --query properties.configuration.ingress.fqdn \
  --output tsv

# Update one or more simple (non-JSON) environment variables
# The container restarts automatically after this
az containerapp update \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --set-env-vars "KEY=value" "KEY2=value2"

# PATCH the Container App config via REST API
# Used when env var values contain JSON or special characters
# that the CLI argument parser cannot handle
az rest \
  --method patch \
  --url "https://management.azure.com/subscriptions/<sub-id>/resourceGroups/rg-lohiya-outlet-dev/providers/Microsoft.App/containerApps/ca-lohiya-outlet?api-version=2023-05-01" \
  --body @patch_body.json
```

---

## 5. Container App — Logs

```bash
# Print the last N log lines (snapshot)
az containerapp logs show \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --tail 50

# Stream logs live (Ctrl+C to stop)
az containerapp logs show \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --follow
```

---

## 6. PostgreSQL Flexible Server — Firewall

```bash
# Create a new firewall rule to allow a specific IP
az postgres flexible-server firewall-rule create \
  --name psql-lohiya-kirana \
  --resource-group rg-lohiya-outlet-dev \
  --rule-name AllowLocalDev \
  --start-ip-address <your-ip> \
  --end-ip-address <your-ip>

# Update an existing firewall rule with a new IP
# (used when the developer's public IP changes between sessions)
az postgres flexible-server firewall-rule update \
  --name psql-lohiya-kirana \
  --resource-group rg-lohiya-outlet-dev \
  --rule-name AllowLocalDev \
  --start-ip-address <new-ip> \
  --end-ip-address <new-ip>

# List all firewall rules
az postgres flexible-server firewall-rule list \
  --name psql-lohiya-kirana \
  --resource-group rg-lohiya-outlet-dev \
  --output table
```

---

## 7. One-Time Infrastructure Setup (already provisioned — reference only)

```bash
# Create resource group
az group create \
  --name rg-lohiya-outlet-dev \
  --location centralindia

# Create Azure Container Registry
az acr create \
  --resource-group rg-lohiya-outlet-dev \
  --name crlohiyakirana \
  --sku Basic

# Create Container App Environment (manages networking + scaling)
az containerapp env create \
  --name cae-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --location centralindia

# Create PostgreSQL Flexible Server
az postgres flexible-server create \
  --resource-group rg-lohiya-outlet-dev \
  --name psql-lohiya-kirana \
  --admin-user psqladmin \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --public-access 0.0.0.0

# Create the database inside the server
az postgres flexible-server db create \
  --resource-group rg-lohiya-outlet-dev \
  --server-name psql-lohiya-kirana \
  --database-name db-kirana-dev

# Create the Container App (first deploy only)
az containerapp create \
  --name ca-lohiya-outlet \
  --resource-group rg-lohiya-outlet-dev \
  --environment cae-lohiya-outlet \
  --image <acr-login-server>/kirana-backend:<tag> \
  --target-port 9000 \
  --ingress external \
  --registry-server <acr-login-server> \
  --min-replicas 1 \
  --max-replicas 3
```

---

## 8. ML Models — env vars, persistence & nightly training

The Intelligence Engine schedules **snapshot_refresh at 2:00 AM IST** and **ML
retrain at 3:00 AM IST** (runs `ml_models/train_all.py` as a subprocess). For
these to work on Azure:

**a) Env vars** — `ml_models/config.py` is env-driven (reads what the app sets):
```bash
az containerapp update --name ca-lohiya-outlet --resource-group rg-lohiya-outlet-dev \
  --set-env-vars \
    "DATABASE_URL=postgresql+psycopg2://USER:PASS@psql-lohiya-kirana.postgres.database.azure.com:5432/db-kirana-dev?sslmode=require" \
    "ML_RESULTS_DIR=/mnt/ml/results" \
    "ML_ARTIFACTS_DIR=/mnt/ml/artifacts"
```
The training subprocess inherits these, so it connects to the **same Azure DB**
(SSL handled automatically) and writes models where the app reads them. Without
`DATABASE_URL` it would fall back to `localhost` and the 3 AM job would fail.

**b) Persistent storage (Azure File Share)** — container storage is ephemeral, so
trained CSVs/`.pkl` are lost on restart unless `ML_RESULTS_DIR`/`ML_ARTIFACTS_DIR`
point at a mounted share:
```bash
# Storage account + share
az storage account create -g rg-lohiya-outlet-dev -n stlohiyaml --sku Standard_LRS
az storage share-rm create --storage-account stlohiyaml -g rg-lohiya-outlet-dev -n ml-models --quota 5
# Register the share with the Container App environment
az containerapp env storage set --name cae-lohiya-outlet -g rg-lohiya-outlet-dev \
  --storage-name mlshare --azure-file-account-name stlohiyaml \
  --azure-file-account-key <KEY> --azure-file-share-name ml-models --access-mode ReadWrite
# Mount it in the app (via YAML: volumes -> storageName: mlshare, mountPath: /mnt/ml)
```

**c) Multiple replicas** — the app runs up to 3 replicas, each with its own
scheduler. The 2 AM / 3 AM jobs now self-guard with Postgres **advisory locks**
(`_SNAPSHOT_LOCK_KEY` / `_ML_RETRAIN_LOCK_KEY` in `intelligence/engine.py`), so
only the replica that wins the lock runs them — no duplicate training.

**Verify:** `GET /kirana/admin/ml/status` (admin) shows per-file age + `stale`
flag; `POST /kirana/admin/ml/retrain` triggers a run on demand.

---

## Deployment Flow Summary

```
Code change
    │
    ▼
docker build  ──►  docker push  ──►  az containerapp update
    │
    ▼
az containerapp logs show --follow   (verify startup)
```

Schema change → run `db_generation/ensure_full_schema.py` against Azure DB **before** the image update.
