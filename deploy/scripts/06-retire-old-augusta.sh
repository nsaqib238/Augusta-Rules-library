#!/usr/bin/env bash
# Run on OLD VPS (213.199.49.234) after new server is stable 24-48h
set -euo pipefail

echo "==> Stopping Augusta backend on OLD server..."
sudo systemctl stop aus-augusta-backend
sudo systemctl disable aus-augusta-backend

echo "==> Optional: disable nginx site only (keeps other apps on this VPS)..."
echo "    sudo rm /etc/nginx/sites-enabled/ausstd.augustasearch.com.conf"
echo "    sudo nginx -t && sudo systemctl reload nginx"

echo "==> Rollback: point DNS A record back to OLD IP and:"
echo "    sudo systemctl enable aus-augusta-backend && sudo systemctl start aus-augusta-backend"

echo "Done — old Augusta service stopped."
