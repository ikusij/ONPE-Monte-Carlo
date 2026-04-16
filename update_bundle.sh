#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Running fetch_all.py..."
venv/bin/python fetch_all.py "$@"

echo "Adding timeseries..."
venv/bin/python run_simulation.py --date "$(date '+%Y-%m-%d %H:%M')"

echo "Committing and pushing bundle.json..."
git add .
git commit -m "update bundle $(date '+%Y-%m-%d %H:%M')"
git push

echo "Done."
