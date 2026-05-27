# Kirana Master Backend — Docker Deployment Guide

This guide builds a production image of the FastAPI backend (the `kirana-ml`
conda env in dev = Python 3.11) and runs it on any Linux server.

---

## 1. Server prerequisites

Install on the **target server** (Ubuntu 22.04 / Debian 12 example):

```bash
# Docker Engine + Compose plugin (official one-liner)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER       # log out + back in
docker --version
docker compose version
```

That is it. No Python, no conda, no Postgres on the host — all three live in
containers.

On your **dev machine** you also need:

- Docker Desktop (Windows / macOS) — to test the build locally, OR
- `git` to push the repo to the server and build there.

---

## 2. Files in this repo

| File | Purpose |
|---|---|
| `Dockerfile` | Two-stage build (wheel cache → slim runtime), Python 3.11 |
| `.dockerignore` | Keeps `.env`, `serviceAccountKey.json`, logs, node_modules etc. out of the image |
| `docker-compose.yml` | Brings up Postgres 16 + the backend with one command |
| `.env` | **Not committed.** Holds all secrets (DB URL, Mistral/Gemini/WhatsApp keys, Razorpay) |
| `serviceAccountKey.json` | **Not committed.** Firebase admin SDK key, mounted read-only at `/app/serviceAccountKey.json` |

---

## 3. Local build & smoke test

From this directory on your dev machine:

```bash
docker build -t kirana-backend:latest .
```

Quick standalone run against an existing Postgres (skip if you use compose):

```bash
docker run --rm -p 9000:9000 \
  --env-file .env \
  -v "$PWD/serviceAccountKey.json:/app/serviceAccountKey.json:ro" \
  kirana-backend:latest
```

Visit `http://localhost:9000/health` — should return `{"status":"ok",...}`.

---

## 4. Full stack with docker compose

The compose file ships Postgres + backend together.

1. Create `.env` from `.env.example` and fill in real values. **Important**: do
   NOT set `DATABASE_URL` here — compose overrides it to point at the `db`
   service. You may add:

   ```env
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=<strong-password>
   POSTGRES_DB=lit_db
   WORKERS=2
   ```

2. Put `serviceAccountKey.json` next to `docker-compose.yml`.

3. Bring it up:

   ```bash
   docker compose up -d --build
   docker compose logs -f backend
   ```

4. The lifespan in `main.py` will auto-create the `kirana_oltp` schema on first
   boot (via `KiranaRepository(engine)`). No manual migration step.

5. (Optional) Seed the catalog and demo data:

   ```bash
   docker compose exec backend python db_generation/seed_blinkit_catalog.py
   ```

---

## 5. Deploying to the server

```bash
# On the server
git clone <your-repo> kirana-master-backend
cd kirana-master-backend

# Copy secrets from your dev machine (NEVER commit these)
scp .env user@server:/path/to/kirana-master-backend/.env
scp serviceAccountKey.json user@server:/path/to/kirana-master-backend/

# Build + start
docker compose up -d --build
```

Front the container with Nginx / Caddy if you need TLS + a real hostname.
Backend listens on `9000` and already sets `--proxy-headers
--forwarded-allow-ips='*'`, so it sees the real client IP behind a reverse
proxy.

Minimal Caddyfile:

```caddy
api.yourdomain.com {
    reverse_proxy 127.0.0.1:9000
}
```

---

## 6. Common operations

```bash
# Tail logs
docker compose logs -f backend

# Restart after pulling new code
git pull && docker compose up -d --build

# Open a shell inside the container
docker compose exec backend bash

# Run pytest inside the image
docker compose exec backend pytest -q

# Postgres CLI
docker compose exec db psql -U postgres -d lit_db

# Backup the database
docker compose exec db pg_dump -U postgres lit_db > backup_$(date +%F).sql

# Stop everything (data volume survives)
docker compose down

# Stop AND wipe the database volume
docker compose down -v
```

---

## 7. Things to verify before going live

- [ ] `.env` has real (not placeholder) values for `KIRANA_API_KEY`,
  `POS_SECRET_KEY`, `MISTRAL_API_KEY`, `WHATSAPP_*`, `GEMINI_API_KEY`,
  `RAZORPAY_*`.
- [ ] `serviceAccountKey.json` is the production Firebase key.
- [ ] `POSTGRES_PASSWORD` is strong (NOT `postgres` or `123456`).
- [ ] Postgres port `5432` is bound to `127.0.0.1` only (already the case in
  the compose file). Don't open it publicly.
- [ ] TLS termination is handled by Nginx/Caddy in front of port `9000`.
- [ ] Backups: schedule a daily `pg_dump` cron on the host.
- [ ] Firewall (`ufw allow 80,443/tcp` + deny everything else).
