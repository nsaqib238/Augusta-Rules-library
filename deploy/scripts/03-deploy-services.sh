#!/usr/bin/env bash
# Run on NEW VPS — install backend, frontend, nginx, systemd (sudo)
set -euo pipefail

PROJECT_ROOT="/home/ragadmin/ragadmin/projects/Augusta-Australia"
RAGADMIN_USER="ragadmin"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/scripts/03-deploy-services.sh"
  exit 1
fi

echo "==> Backend venv + dependencies..."
cd "${PROJECT_ROOT}/backend"
sudo -u "${RAGADMIN_USER}" python3 -m venv venv
sudo -u "${RAGADMIN_USER}" bash -c "source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

echo "==> systemd services..."
chmod +x "${PROJECT_ROOT}/deploy/scripts/start-api.sh"
cp "${PROJECT_ROOT}/deploy/systemd/aus-augusta-backend.service" /etc/systemd/system/
cp "${PROJECT_ROOT}/deploy/systemd/aus-augusta-pdf-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable aus-augusta-backend
systemctl restart aus-augusta-backend

if grep -qE '^EXTERNAL_PDF_WORKER=(1|true|yes|on)' "${PROJECT_ROOT}/backend/.env" 2>/dev/null; then
  echo "==> EXTERNAL_PDF_WORKER enabled — starting PDF worker..."
  systemctl enable aus-augusta-pdf-worker
  systemctl restart aus-augusta-pdf-worker
else
  echo "==> PDF worker unit installed but not enabled (set EXTERNAL_PDF_WORKER=true in backend/.env to enable)."
fi

sleep 3
curl -sf http://127.0.0.1:8082/health || { journalctl -u aus-augusta-backend -n 40 --no-pager; exit 1; }
echo "Backend health OK"

echo "==> Frontend build..."
cd "${PROJECT_ROOT}/frontend"
sudo -u "${RAGADMIN_USER}" npm install
sudo -u "${RAGADMIN_USER}" npm run build

echo "==> Deploy static files..."
rm -rf /var/www/aus-augusta-frontend/*
cp -r "${PROJECT_ROOT}/frontend/build/"* /var/www/aus-augusta-frontend/
chown -R www-data:www-data /var/www/aus-augusta-frontend

echo "==> Nginx..."
cp "${PROJECT_ROOT}/deploy/nginx/ausstd.augustasearch.com.conf" /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/ausstd.augustasearch.com.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t
systemctl reload nginx

echo "==> Deploy complete."
echo "    Next: bash deploy/scripts/04-preflight-test.sh (before DNS cutover)"
