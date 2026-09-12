# Update / deploy — library.augustasearch.com

**Product:** Augusta Rules Library (NCC/SIR shared library)  
**Repo:** `nsaqib238/Augusta-Rules-library`  
**Folder on VPS:** `/home/ragadmin/ragadmin/projects/Augusta-Rules-library`  
**Full isolation table:** [APP_SEPARATION.md](APP_SEPARATION.md)

This is **not** the upload app. Do not use `ausstd`, port `8082`, or `aus-augusta-*` units when deploying this product.

---

## Recommendations (read before first deploy)

1. **Sibling folder only** — Clone/move this repo next to Australia, never nested inside `Augusta-Australia/`.
2. **Confirm remote every time** — `git remote -v` must show `Augusta-Rules-library.git`.
3. **Own Supabase** — Backend + frontend must use the library project (`ejwov…` locally), never the upload project (`rqxzow…`).
4. **Own domain** — `library.augustasearch.com` (DNS A/AAAA → VPS). Do not rsync into `/var/www/aus-augusta-frontend/`.
5. **Own port** — API listens on **8083**. Australia keeps **8082**.
6. **Own systemd** — `aus-rules-backend` / `aus-rules-pdf-worker` (never install the deprecated `aus-augusta-*` files from this repo).
7. **Own Redis DB** — `REDIS_URL=redis://127.0.0.1:6379/1` (Australia uses `/0`) so ask/answer caches do not collide.
8. **Own Stripe** — Separate webhook URL, secrets, prices, publishable key, customer portal. Do not reuse ausstd Stripe settings.
9. **Stripe webhook** — Point Stripe at  
   `https://library.augustasearch.com/api/v1/webhooks/stripe`  
   (not the ausstd webhook).
10. **Certbot** — Issue TLS for `library.augustasearch.com` after nginx site is enabled.
11. **`chmod +x deploy/scripts/start-api.sh`** after every pull (avoids systemd `203/EXEC`).
12. **SQL** — Run `supabase/combined_setup.sql` on the **library** Supabase project only.

---

## One-time setup (VPS)

```bash
# 1) Clone as sibling (example)
cd /home/ragadmin/ragadmin/projects
git clone git@github.com:nsaqib238/Augusta-Rules-library.git
cd Augusta-Rules-library
git remote -v

# 2) Backend env (copy from example, then edit)
cp backend/.env.example backend/.env
# Set at least:
#   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY  (library project)
#   ALLOWED_ORIGINS=https://library.augustasearch.com,https://www.augustasearch.com
#   FRONTEND_URL=https://library.augustasearch.com
#   UVICORN_PORT=8083
#   REDIS_URL=redis://127.0.0.1:6379/1
#   STRIPE_*  (library-only)

cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
deactivate
cd ..

# 3) systemd
chmod +x deploy/scripts/start-api.sh
sudo cp deploy/systemd/aus-rules-backend.service /etc/systemd/system/
sudo cp deploy/systemd/aus-rules-pdf-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aus-rules-backend

# 4) nginx + www root
sudo mkdir -p /var/www/aus-rules-frontend
sudo cp deploy/nginx/conf.d/aus-rules-rate-limits.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/library.augustasearch.com.conf /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/library.augustasearch.com.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# then: sudo certbot --nginx -d library.augustasearch.com

# 5) frontend
cd frontend
# .env: library Supabase anon URL/key; leave REACT_APP_API_URL unset for production
npm install && npm run build
sudo rsync -a --delete build/ /var/www/aus-rules-frontend/
sudo chown -R www-data:www-data /var/www/aus-rules-frontend
sudo nginx -t && sudo systemctl reload nginx

curl -s http://127.0.0.1:8083/health
```

---

## Routine update (after code changes)

```bash
cd /home/ragadmin/ragadmin/projects/Augusta-Rules-library
git remote -v
git pull origin main

chmod +x deploy/scripts/start-api.sh
cd backend
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart aus-rules-backend
sudo systemctl status aus-rules-backend --no-pager

cd ../frontend
npm install
npm run build
sudo rsync -a --delete build/ /var/www/aus-rules-frontend/
sudo chown -R www-data:www-data /var/www/aus-rules-frontend
sudo nginx -t && sudo systemctl reload nginx

curl -s http://127.0.0.1:8083/health
```

Also: [update vps.md](update%20vps.md)

---

## Pre-flight checklist

| Check | Command / action | Expect |
|-------|------------------|--------|
| Remote | `git remote -v` | `Augusta-Rules-library.git` |
| Path | `pwd` | `.../Augusta-Rules-library` |
| Supabase | `grep SUPABASE_URL backend/.env` | library project host |
| Port | `grep UVICORN_PORT backend/.env` | `8083` |
| Frontend origin | `grep FRONTEND_URL backend/.env` | `library.augustasearch.com` |
| Redis | `grep REDIS_URL backend/.env` | ends with `/1` |
| Health | `curl -s http://127.0.0.1:8083/health` | JSON healthy |
| Australia untouched | `curl -s http://127.0.0.1:8082/health` | still healthy |
| Wrong www | — | never write to `aus-augusta-frontend` |
| Stripe webhook | Stripe Dashboard | library host, not ausstd |

---

## Do not

- `git remote set-url` this folder to `Augusta-Australia` (or the reverse).
- Deploy this build to `ausstd.augustasearch.com`.
- Copy Australia `backend/.env` here without changing Supabase / domain / port / Redis / Stripe.
- Install deprecated `deploy/systemd/aus-augusta-*.service` or `deploy/nginx/ausstd.*.conf` from this repo.
- Share Stripe webhook between the two apps.

---

## Logs

```bash
sudo journalctl -u aus-rules-backend -f --no-pager -l
sudo journalctl -u aus-rules-backend -n 100 --no-pager -l
```
