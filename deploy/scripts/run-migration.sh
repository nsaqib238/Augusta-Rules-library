#!/usr/bin/env bash
# Master migration runner — NEW VPS
# Usage:
#   Step 1 (root):  sudo bash deploy/scripts/run-migration.sh prepare
#   Step 2 (ragadmin): bash deploy/scripts/run-migration.sh clone-env
#   Step 3 (sudo):    sudo bash deploy/scripts/run-migration.sh deploy
#   Step 4:           bash deploy/scripts/run-migration.sh preflight
#   Step 5 (after DNS): sudo bash deploy/scripts/run-migration.sh ssl
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cmd="${1:-help}"

case "${cmd}" in
  prepare)
    exec bash "${SCRIPT_DIR}/01-prepare-server.sh"
    ;;
  clone-env)
    cd "${PROJECT_ROOT}"
    exec bash "${SCRIPT_DIR}/02-clone-and-env.sh"
    ;;
  deploy)
    cd "${PROJECT_ROOT}"
    exec bash "${SCRIPT_DIR}/03-deploy-services.sh"
    ;;
  preflight)
    cd "${PROJECT_ROOT}"
    exec bash "${SCRIPT_DIR}/04-preflight-test.sh"
    ;;
  ssl)
    cd "${PROJECT_ROOT}"
    exec bash "${SCRIPT_DIR}/05-certbot-ssl.sh"
    ;;
  retire-old)
    exec bash "${SCRIPT_DIR}/06-retire-old-augusta.sh"
    ;;
  help|*)
    cat <<EOF
Augusta Search — new VPS migration

  prepare     Run as root on NEW VPS (packages, user, ufw)
  clone-env   Run as ragadmin — git clone + scp .env from old VPS
  deploy      Run with sudo — backend, frontend, nginx
  preflight   Test before DNS cutover
  ssl         Run with sudo AFTER DNS points to new IP
  retire-old  Run on OLD VPS after 24-48h stable

Full guide: deploy/NEW_VPS_MIGRATION.md
EOF
    ;;
esac
