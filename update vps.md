============================================================
               quick vps 1

cd /home/ragadmin/ragadmin/projects/Augusta-Australia

git pull origin main

# Backend
cd backend
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart aus-augusta-backend
sudo systemctl status aus-augusta-backend --no-pager

# Frontend (must be from frontend/, not backend/)
cd ../frontend
npm install
npm run build
sudo rsync -a --delete build/ /var/www/aus-augusta-frontend/
sudo chown -R www-data:www-data /var/www/aus-augusta-frontend
sudo nginx -t && sudo systemctl reload nginx

# Quick checks
curl -s http://127.0.0.1:8082/health

============================================================
# Update VPS — Augusta Australia

| | VPS1 (edge) | VPS2 (PDF) |
|--|-------------|------------|
| **Role** | nginx + API + Redis | PDF worker only |
| **Path** | `/home/ragadmin/ragadmin/projects/Augusta-Australia` | same |
| **Site** | `https://ausstd.augustasearch.com` | no public site |
| **Services** | `aus-augusta-backend` | `aus-augusta-pdf-worker` |

SSH as `ragadmin`. Repo: `https://github.com/nsaqib238/Augusta-Australia.git`

---

## 0. Once — Supabase (browser)

In Supabase → SQL Editor, run:

`supabase/combined_setup.sql`

(idempotent; includes `subscription_details` fix)

---

## 1. VPS2 — first deploy (PDF worker)

### 1.1 Clone + Python

```bash
mkdir -p /home/ragadmin/ragadmin/projects
cd /home/ragadmin/ragadmin/projects
git clone https://github.com/nsaqib238/Augusta-Australia.git
cd Augusta-Australia/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

### 1.2 Copy full `.env` from VPS1, then change worker lines

**Do not** create a tiny `.env` with only the lines below.

1. Copy **entire** `backend/.env` from VPS1 → VPS2 (same Supabase, Modal, OpenAI/Gemini, Stripe, S3 keys, etc.).
2. On VPS2, **change only** these:

```env
WORKER_MODE=pdf
EXTERNAL_PDF_WORKER=true
PDF_QUEUE_BACKEND=postgres
PDF_JOB_MAX_CONCURRENT=4
PDF_JOB_MAX_PER_USER=2
PDF_WORKER_POLL_SECONDS=2
HEALTH_REQUIRE_REDIS=false
AUTO_PROCESS_PDF_ON_UPLOAD=true
```

**Required to keep from VPS1** (worker will fail without them):

| Vars | Why |
|------|-----|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Claim jobs + write documents/chunks |
| `MODAL_ENDPOINT`, `MODAL_API_SECRET` | PDF table extraction |
| `S3_*` / storage keys (if you use them) | Download uploaded PDFs |
| `OPENAI_API_KEY` / `GEMINI_*` | Only if pipeline AI helpers are enabled |

Optional on VPS2: leave `REDIS_URL` unset; Stripe/frontend vars unused by the PDF worker but fine to leave in the copied file.

```bash
# on VPS2
nano /home/ragadmin/ragadmin/projects/Augusta-Australia/backend/.env
```

### 1.3 Install + start PDF worker

```bash
cd /home/ragadmin/ragadmin/projects/Augusta-Australia
sudo cp deploy/systemd/aus-augusta-pdf-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable aus-augusta-pdf-worker
sudo systemctl start aus-augusta-pdf-worker
sudo systemctl status aus-augusta-pdf-worker --no-pager
```

Logs:

```bash
sudo journalctl -u aus-augusta-pdf-worker -f --no-pager -l
```

---

## 2b. VPS1 — update `backend/.env` (API box)

SSH to VPS1, then:

```bash
# backup first
cp /home/ragadmin/ragadmin/projects/Augusta-Australia/backend/.env \
   ~/env-backup-$(date +%F)-vps1.env

nano /home/ragadmin/ragadmin/projects/Augusta-Australia/backend/.env
```

### Must be set on VPS1 (API role)

```env
WORKER_MODE=api
EXTERNAL_PDF_WORKER=true
PDF_QUEUE_BACKEND=postgres
REDIS_URL=redis://127.0.0.1:6379/0
HEALTH_REQUIRE_REDIS=true
UVICORN_WORKERS=3
UVICORN_PORT=8082
UVICORN_LIMIT_CONCURRENCY=96

ALLOWED_ORIGINS=https://ausstd.augustasearch.com,https://augustasearch.com
FRONTEND_URL=https://ausstd.augustasearch.com

FREE_ASK_QUOTA_ENABLED=true
FREE_ASK_DAILY_GLOBAL_LIMIT=80
FREE_ASK_DAILY_PER_USER_LIMIT=5
FREE_ASK_TIMEZONE=Australia/Sydney

ANSWER_CACHE_ENABLED=true
ANSWER_CACHE_TTL_SECONDS=86400

# Keep async ask off until verified
ASK_ASYNC_ENABLED=false
ASK_ASYNC_DEFAULT=false
ASK_JOB_WORKER_ENABLED=false
```

### Keep your existing secrets (do not wipe)

Leave these as they already are on VPS1 (copy from local only if you are rotating keys):

- `SUPABASE_*`, `OPENAI_API_KEY`, `GEMINI_*`
- `STRIPE_*`, `MODAL_*`, `S3_*`, `SMTP_*`, `ADMIN_EMAIL_ALLOWLIST`

### After saving `.env`

```bash
sudo systemctl restart aus-augusta-backend
sudo systemctl status aus-augusta-backend --no-pager
curl -s http://127.0.0.1:8082/health
curl -s http://127.0.0.1:8082/health/ready
```

If PDF worker already moved to VPS2:

```bash
sudo systemctl stop aus-augusta-pdf-worker
sudo systemctl disable aus-augusta-pdf-worker
```

---

## 2. VPS1 — after VPS2 is running

Keep API enqueueing jobs; stop the local PDF worker.

### 2.1 Confirm `backend/.env` on VPS1

```env
WORKER_MODE=api
EXTERNAL_PDF_WORKER=true
PDF_QUEUE_BACKEND=postgres
REDIS_URL=redis://127.0.0.1:6379/0
HEALTH_REQUIRE_REDIS=true
```

### 2.2 Stop PDF worker on VPS1

```bash
sudo systemctl stop aus-augusta-pdf-worker
sudo systemctl disable aus-augusta-pdf-worker
sudo systemctl restart aus-augusta-backend
sudo systemctl status aus-augusta-backend --no-pager
curl -s http://127.0.0.1:8082/health
```

---

## 3. Verify (both)

1. Open the site → upload a PDF  
2. **VPS2** logs show claim → process → finish:

```bash
sudo journalctl -u aus-augusta-pdf-worker -f --no-pager -l
```

3. Document finishes in the UI  
4. Ask still works on the site  

---

## 4. Routine update — VPS1 (code + frontend)

```bash
cd /home/ragadmin/ragadmin/projects/Augusta-Australia

git restore frontend/build frontend/package-lock.json 2>/dev/null || true
git clean -fd frontend/build/
git pull origin main

cd backend
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart aus-augusta-backend

cd ../frontend
npm install
npm run build
sudo cp -r build/* /var/www/aus-augusta-frontend/
sudo chown -R www-data:www-data /var/www/aus-augusta-frontend
sudo nginx -t && sudo systemctl reload nginx

curl -s http://127.0.0.1:8082/health
sudo systemctl status aus-augusta-backend --no-pager
```

Optional — nginx rate limits (first time only):

```bash
cd /home/ragadmin/ragadmin/projects/Augusta-Australia
sudo cp deploy/nginx/conf.d/aus-augusta-rate-limits.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/ausstd.augustasearch.com.conf /etc/nginx/sites-available/ausstd.augustasearch.com.conf
# If Certbot already rewrote the HTTPS block, merge rate-limit lines by hand — do not overwrite SSL blindly.
sudo nginx -t && sudo systemctl reload nginx
```

---

## 5. Routine update — VPS2 (PDF worker only)

```bash
cd /home/ragadmin/ragadmin/projects/Augusta-Australia

git pull origin main

cd backend
source venv/bin/activate
pip install -r requirements.txt
deactivate

sudo cp ../deploy/systemd/aus-augusta-pdf-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart aus-augusta-pdf-worker
sudo systemctl status aus-augusta-pdf-worker --no-pager
```

No frontend / nginx on VPS2.

---

## 6. Quick checks

**VPS1**

```bash
redis-cli ping
curl -s http://127.0.0.1:8082/health
curl -s http://127.0.0.1:8082/health/ready
sudo systemctl status aus-augusta-backend --no-pager
sudo journalctl -u aus-augusta-backend -n 50 --no-pager
```

**VPS2**

```bash
sudo systemctl status aus-augusta-pdf-worker --no-pager
sudo journalctl -u aus-augusta-pdf-worker -n 50 --no-pager
```

---

## Do not delete

| Path | Why |
|------|-----|
| `backend/.env` / `frontend/.env` | Secrets — not in git |
| `/var/www/aus-augusta-frontend/` | Live site (overwrite with `cp -r build/*`) |
| `/etc/nginx/` | Keep unless you mean to change SSL/site |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `git pull` blocked | `git restore frontend/build frontend/package-lock.json && git clean -fd frontend/build/` |
| Old UI | Rebuild + copy to `/var/www/aus-augusta-frontend/`, hard refresh |
| PDF stuck | VPS2 worker running? VPS1 worker disabled? Check VPS2 journal |
| Backend crash | `sudo journalctl -u aus-augusta-backend -n 100 --no-pager` |
