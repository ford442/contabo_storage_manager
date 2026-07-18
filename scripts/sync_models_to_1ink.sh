#!/usr/bin/env bash
# Transfer /data/files/models/ to storage.1ink.us via lftp (SFTP).
# Skips .cache dirs (HuggingFace metadata, ~200MB, not needed for serving).
# Uploads .htaccess for CORS + gzip/brotli headers.
# Safe to re-run — lftp mirror --only-newer skips unchanged files.
#
# Usage:
#   export SFTP_PASS='storage442'
#   bash scripts/sync_models_to_1ink.sh

set -euo pipefail

# DreamHost SSH listens on 1ink.us, not storage.1ink.us (port 22 refused there).
REMOTE_USER="${REMOTE_USER:-storage_manager}"
REMOTE_HOST="${REMOTE_HOST:-1ink.us}"
REMOTE_PORT="${REMOTE_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-storage.1ink.us/models}"
LOCAL_DIR="${LOCAL_DIR:-/data/files/models}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HTACCESS_SRC="$SCRIPT_DIR/../deploy/models_htaccess"

if [[ -z "${SFTP_PASS:-}" ]]; then
  echo "ERROR: Set SFTP_PASS env var before running:"
  echo "  export SFTP_PASS='storage442'"
  echo "  bash scripts/sync_models_to_1ink.sh"
  exit 1
fi

if ! command -v lftp &>/dev/null; then
  echo "ERROR: lftp not found. Install with: apt-get install lftp"
  exit 1
fi

echo "=== Syncing models to storage.1ink.us/models/ ==="
echo "Source : $LOCAL_DIR"
echo "Dest   : sftp://$REMOTE_HOST/$REMOTE_DIR"
echo ""

lftp -u "$REMOTE_USER,$SFTP_PASS" "sftp://$REMOTE_HOST:$REMOTE_PORT" <<LFTP
set sftp:auto-confirm yes
set net:timeout 30
set net:max-retries 5
set net:reconnect-interval-base 10

# Create destination directory if it doesn't exist
mkdir -p $REMOTE_DIR

# Mirror local → remote, skipping .cache dirs and .metadata files
mirror --reverse --only-newer --verbose --exclude-glob '.cache/' --exclude-glob '*.metadata' "$LOCAL_DIR/" "$REMOTE_DIR/"

# Upload .htaccess
put "$HTACCESS_SRC" -o "$REMOTE_DIR/.htaccess"

quit
LFTP

echo ""
echo "=== Done ==="
echo "Verify:"
echo "  curl -I https://storage.1ink.us/models/Hermes-3-Llama-3.1-8B-q4f16_1-MLC/mlc-chat-config.json"
echo "  curl -I https://storage.1ink.us/models/wllama-wasm/single-thread.wasm"
