#!/usr/bin/env bash
# Run on NEW VPS AFTER DNS A record points to this server (sudo)
set -euo pipefail

DOMAIN="ausstd.augustasearch.com"
EMAIL="${CERTBOT_EMAIL:-naajm@augustasearch.com}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/ragadmin/ragadmin/projects/Augusta-Australia}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo"
  exit 1
fi

echo "==> Ensure nginx site config is installed..."
NGINX_CONF="/etc/nginx/sites-available/ausstd.augustasearch.com.conf"
if [[ ! -f "${NGINX_CONF}" ]]; then
  cp "${PROJECT_ROOT}/deploy/nginx/ausstd.augustasearch.com.conf" "${NGINX_CONF}"
fi
ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/ausstd.augustasearch.com.conf
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t
systemctl reload nginx

echo "==> Checking DNS for ${DOMAIN}..."
RESOLVED=$(dig +short "${DOMAIN}" A | tail -1)
MY_IP=$(curl -4 -sf ifconfig.me || hostname -I | awk '{print $1}')
echo "    ${DOMAIN} resolves to: ${RESOLVED:-unknown}"
echo "    This server public IP: ${MY_IP}"

if [[ -n "${RESOLVED}" && "${RESOLVED}" != "${MY_IP}" ]]; then
  echo "WARN: DNS may not point here yet. Certbot may fail."
  read -r -p "Continue anyway? [y/N] " ans
  [[ "${ans}" =~ ^[yY] ]] || exit 1
fi

certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" --redirect \
  || certbot install --cert-name "${DOMAIN}" --nginx

nginx -t && systemctl reload nginx

echo "==> Verify HTTPS..."
curl -sf "https://${DOMAIN}/" | grep -o '<title>[^<]*</title>' || true
curl -sf "https://${DOMAIN}/health" 2>/dev/null || curl -sf "http://127.0.0.1:8082/health"

echo "==> SSL done. Test login, upload, Stripe in browser."
