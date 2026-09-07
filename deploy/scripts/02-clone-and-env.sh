#!/usr/bin/env bash
# Run on NEW VPS as ragadmin (after 01-prepare-server.sh)
set -euo pipefail

OLD_VPS="${OLD_VPS:-213.199.49.234}"
OLD_USER="${OLD_USER:-ragadmin}"
PROJECT_ROOT="/home/ragadmin/ragadmin/projects/Augusta-Australia"
REPO_URL="${REPO_URL:-git@github.com:nsaqib238/Augusta-Australia.git}"

echo "==> Cloning repository..."
mkdir -p "$(dirname "${PROJECT_ROOT}")"
if [[ -d "${PROJECT_ROOT}/.git" ]]; then
  cd "${PROJECT_ROOT}"
  git fetch origin
  git checkout main
  git pull --rebase origin main
else
  git clone "${REPO_URL}" "${PROJECT_ROOT}"
  cd "${PROJECT_ROOT}"
  git checkout main
fi

echo "==> Copying .env files from old VPS (${OLD_USER}@${OLD_VPS})..."
echo "    You will be prompted for the OLD server password (twice)."

mkdir -p "${PROJECT_ROOT}/backend" "${PROJECT_ROOT}/frontend"
scp "${OLD_USER}@${OLD_VPS}:/home/ragadmin/ragadmin/projects/Augusta-Australia/backend/.env" \
  "${PROJECT_ROOT}/backend/.env"
scp "${OLD_USER}@${OLD_VPS}:/home/ragadmin/ragadmin/projects/Augusta-Australia/frontend/.env" \
  "${PROJECT_ROOT}/frontend/.env"

chmod 600 "${PROJECT_ROOT}/backend/.env" "${PROJECT_ROOT}/frontend/.env"

echo "==> Ensuring VPS-safe backend settings..."
BACKEND_ENV="${PROJECT_ROOT}/backend/.env"
touch "${BACKEND_ENV}"
grep -q '^DISABLE_CHUNK_EMBEDDINGS=' "${BACKEND_ENV}" || \
  echo 'DISABLE_CHUNK_EMBEDDINGS=true' >> "${BACKEND_ENV}"

if grep -q '^ALLOWED_ORIGINS=' "${BACKEND_ENV}"; then
  echo "    ALLOWED_ORIGINS already set — verify it includes https://ausstd.augustasearch.com"
else
  echo 'ALLOWED_ORIGINS=https://ausstd.augustasearch.com,https://augustasearch.com' >> "${BACKEND_ENV}"
fi

echo "==> Frontend: ensure REACT_APP_API_URL is empty for same-origin /api/"
FRONTEND_ENV="${PROJECT_ROOT}/frontend/.env"
if grep -q '^REACT_APP_API_URL=' "${FRONTEND_ENV}"; then
  sed -i 's/^REACT_APP_API_URL=.*/REACT_APP_API_URL=/' "${FRONTEND_ENV}" || true
fi

echo "==> Clone + env copy done. Next: sudo bash deploy/scripts/03-deploy-services.sh"
