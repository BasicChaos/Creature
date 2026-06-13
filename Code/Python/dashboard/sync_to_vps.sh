#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/public/"

# Deployment target is read from an untracked config file.
# Copy sync_to_vps.env.example to sync_to_vps.env and fill in your values.
CONFIG_FILE="$SCRIPT_DIR/sync_to_vps.env"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "Missing $CONFIG_FILE. Copy sync_to_vps.env.example to sync_to_vps.env and set VPS_USER, VPS_HOST, VPS_TARGET."
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG_FILE"

cd "$PYTHON_DIR"
python3 dashboard/export_static.py

rsync -avz \
  "$PUBLIC_DIR" \
  "$VPS_USER@$VPS_HOST:$VPS_TARGET"

echo "Dashboard synced."
