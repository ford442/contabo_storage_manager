#!/usr/bin/env bash
# Apply on the Contabo *storage* VPS (storage.noahcohn.com / 173.249.14.134).
# Do NOT run this on the code-server box (85.239.*) unless that box is also
# the live deploy service — check:
#   curl -sS https://storage.noahcohn.com/api/deploy/health
#   curl -sS http://127.0.0.1:8000/api/deploy/health
#
# Prerequisites:
#   - contabo_storage_manager checkout already contains prod support
#     (deploy_base_dir_prod / target_site=prod). If not, sync that code first.
#   - systemd unit contabo-storage-python.service (or equivalent) uses this .env
#
# Usage:
#   sudo bash scripts/enable_deploy_prod.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
PROD_BASE="${DEPLOY_BASE_DIR_PROD_VALUE:-/home/ford442}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

if ! grep -qE '^DEPLOY_HOST=' "$ENV_FILE"; then
    echo "ERROR: $ENV_FILE has no DEPLOY_HOST — this does not look like the storage VPS env" >&2
    exit 1
fi

if grep -qE '^DEPLOY_BASE_DIR_PROD=' "$ENV_FILE"; then
    echo "Updating existing DEPLOY_BASE_DIR_PROD in $ENV_FILE"
    sed -i -E "s|^DEPLOY_BASE_DIR_PROD=.*|DEPLOY_BASE_DIR_PROD=${PROD_BASE}|" "$ENV_FILE"
else
    echo "Appending DEPLOY_BASE_DIR_PROD=${PROD_BASE} to $ENV_FILE"
    printf '\n# Production DreamHost parent for projectm.1ink.us/\nDEPLOY_BASE_DIR_PROD=%s\n' "$PROD_BASE" >> "$ENV_FILE"
fi

echo "Restarting contabo-storage-python.service..."
systemctl restart contabo-storage-python.service
sleep 1
systemctl --no-pager --full status contabo-storage-python.service | sed -n '1,12p'

echo
echo "Local health:"
curl -sS http://127.0.0.1:8000/api/deploy/health | python3 -m json.tool | sed -n '1,20p'

echo
echo "Public health (should show deploy_base_dir_prod=${PROD_BASE}):"
curl -sS https://storage.noahcohn.com/api/deploy/health | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("deploy_base_dir_prod =", d.get("deploy_base_dir_prod"))
print("base_dir             =", d.get("base_dir"))
print("deploy_base_dir_go   =", d.get("deploy_base_dir_go"))
if not d.get("deploy_base_dir_prod"):
    sys.exit("FAIL: deploy_base_dir_prod still missing — code not deployed or wrong host")
'

echo
echo "Done. From Project-M:"
echo "  export DEPLOY_TOKEN=..."
echo "  python deploy.py --target prod"
echo "  # or: python deploy.py --target test,go,prod"
