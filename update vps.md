============================================================
               quick vps — Augusta-Rules-library ONLY

# NEVER use ausstd / aus-augusta-* / port 8082 here (those are Augusta-Australia).
# See APP_SEPARATION.md

cd /home/ragadmin/ragadmin/projects/Augusta-Rules-library
git remote -v
# expect: nsaqib238/Augusta-Rules-library.git

git pull origin main

# Backend
cd backend
source venv/bin/activate
pip install -r requirements.txt
deactivate
chmod +x ../deploy/scripts/start-api.sh
sudo systemctl restart aus-rules-backend
sudo systemctl status aus-rules-backend --no-pager

# Frontend
cd ../frontend
npm install
npm run build
sudo rsync -a --delete build/ /var/www/aus-rules-frontend/
sudo chown -R www-data:www-data /var/www/aus-rules-frontend
sudo nginx -t && sudo systemctl reload nginx

curl -s http://127.0.0.1:8083/health

============================================================
# Update VPS — Augusta Rules Library (NCC/SIR)

| | VPS1 (edge) | VPS2 (PDF) |
|--|-------------|------------|
| **Role** | nginx + API + Redis | PDF worker only |
| **Path** | `/home/ragadmin/ragadmin/projects/Augusta-Rules-library` | same |
| **Site** | `https://library.augustasearch.com` | no public site |
| **API port** | `8083` | — |
| **Services** | `aus-rules-backend` | `aus-rules-pdf-worker` |

SSH as `ragadmin`. Repo: `https://github.com/nsaqib238/Augusta-Rules-library.git`

**Not this product:** `ausstd.augustasearch.com` / `Augusta-Australia` / `aus-augusta-backend` / port `8082`.

---

## 0. Once — Supabase (browser)

Use the **library** Supabase project (not the upload app). SQL Editor → `supabase/combined_setup.sql`.

---

## Backend `.env` production (must differ from Australia)

```env
SUPABASE_URL=https://<library-project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
ALLOWED_ORIGINS=https://library.augustasearch.com,https://www.augustasearch.com
FRONTEND_URL=https://library.augustasearch.com
UVICORN_PORT=8083
REDIS_URL=redis://127.0.0.1:6379/1
# Stripe: separate webhook endpoint → https://library.augustasearch.com/api/v1/webhooks/stripe
```

---

## Install systemd (once)

```bash
cd /home/ragadmin/ragadmin/projects/Augusta-Rules-library
sudo cp deploy/systemd/aus-rules-backend.service /etc/systemd/system/
sudo cp deploy/systemd/aus-rules-pdf-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable aus-rules-backend
sudo systemctl start aus-rules-backend
```

## Install nginx (once)

```bash
sudo cp deploy/nginx/conf.d/aus-rules-rate-limits.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/library.augustasearch.com.conf /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/library.augustasearch.com.conf /etc/nginx/sites-enabled/
sudo mkdir -p /var/www/aus-rules-frontend
sudo nginx -t && sudo systemctl reload nginx
# then certbot for library.augustasearch.com
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Q&A says shared compliance / wrong product code | `git remote -v` must be **Rules-library**, not Australia |
| 502 / API dead | `systemctl status aus-rules-backend`; port **8083** |
| Accidentally updated Australia site | You rsynced to wrong www path — use `/var/www/aus-rules-frontend/` |
| Redis cache bleed from other app | Use `REDIS_URL=.../1` (Australia uses `/0`) |

Full separation table: [APP_SEPARATION.md](APP_SEPARATION.md)
