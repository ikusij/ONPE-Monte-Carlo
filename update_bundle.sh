#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Running fetch_all.py..."
venv/bin/python fetch_all.py "$@"

echo "Committing and pushing bundle.json..."
git add bundle.json
git commit -m "update bundle $(date '+%Y-%m-%d %H:%M')"
git push

echo "Done."
