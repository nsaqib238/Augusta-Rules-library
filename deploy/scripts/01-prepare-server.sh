#!/usr/bin/env bash
# Run on NEW VPS as root: bash deploy/scripts/01-prepare-server.sh
set -euo pipefail

RAGADMIN_USER="${RAGADMIN_USER:-ragadmin}"
RAGADMIN_PASSWORD="${RAGADMIN_PASSWORD:-}"

echo "==> Updating packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

echo "==> Installing runtime packages..."
apt-get install -y \
  git curl ca-certificates \
  nginx \
  python3 python3-venv python3-pip \
  certbot python3-certbot-nginx \
  ufw

# NodeSource nodejs bundles npm; Ubuntu's npm package conflicts with it.
if ! command -v node >/dev/null 2>&1; then
  echo "==> Node.js not found — install Node 20 LTS (nodesource) then re-run this script."
  echo "    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -"
  echo "    apt-get install -y nodejs"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "WARN: npm missing — install nodejs from NodeSource (includes npm)."
  exit 1
fi
echo "==> Node $(node -v), npm $(npm -v)"

echo "==> Creating user ${RAGADMIN_USER} (if missing)..."
if ! id "${RAGADMIN_USER}" &>/dev/null; then
  useradd -m -s /bin/bash "${RAGADMIN_USER}"
  usermod -aG sudo "${RAGADMIN_USER}"
  if [[ -n "${RAGADMIN_PASSWORD}" ]]; then
    echo "${RAGADMIN_USER}:${RAGADMIN_PASSWORD}" | chpasswd
  else
    echo "Set password for ${RAGADMIN_USER}: passwd ${RAGADMIN_USER}"
  fi
fi

echo "==> SSH: copy your public key to /home/${RAGADMIN_USER}/.ssh/authorized_keys if needed"

echo "==> Configuring UFW..."
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "==> Creating project directories..."
mkdir -p "/home/${RAGADMIN_USER}/ragadmin/projects"
mkdir -p /var/www/aus-augusta-frontend
chown -R "${RAGADMIN_USER}:${RAGADMIN_USER}" "/home/${RAGADMIN_USER}/ragadmin"

echo "==> Done. Next: su - ${RAGADMIN_USER} and run 02-clone-and-env.sh"
