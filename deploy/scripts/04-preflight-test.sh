#!/usr/bin/env bash
# Run on NEW VPS before DNS cutover
set -euo pipefail

DOMAIN="ausstd.augustasearch.com"
NEW_IP="${NEW_IP:-37.60.238.78}"

echo "==> Backend health (localhost)..."
curl -sf http://127.0.0.1:8082/health | head -c 200
echo

echo "==> Nginx frontend (Host header)..."
curl -sf -H "Host: ${DOMAIN}" "http://127.0.0.1/" | grep -o '<title>[^<]*</title>' || echo "WARN: no title in index.html"

echo "==> Nginx /health proxy..."
curl -sf -H "Host: ${DOMAIN}" "http://127.0.0.1/health"
echo

echo "==> systemd status..."
systemctl is-active aus-augusta-backend
systemctl is-active nginx

echo "==> Stripe price loaded (no secrets printed)..."
journalctl -u aus-augusta-backend -n 80 --no-pager | grep -E 'STRIPE_PRICE|Initialized Stripe' || true

echo ""
echo "==> From YOUR PC (before DNS change), add to hosts file temporarily:"
echo "    ${NEW_IP}  ${DOMAIN}"
echo "    Then open https://${DOMAIN} after step 05-certbot (or http for initial test)"
echo ""
echo "==> Or test via curl from PC:"
echo "    curl -s --resolve ${DOMAIN}:80:${NEW_IP} http://${DOMAIN}/ | head"
echo "    curl -s --resolve ${DOMAIN}:80:${NEW_IP} http://${DOMAIN}/api/v1/subscriptions/usage-stats"
