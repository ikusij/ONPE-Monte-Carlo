#!/bin/bash

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source venv/bin/activate

echo "Running fetch_all.py..."
python3 fetch_all.py "$@"

echo "Adding timeseries..."
python3 run_simulation.py --date "$(date '+%Y-%m-%d %H:%M')"

echo "Committing and pushing bundle.json..."
git add .
git commit -m "update bundle $(date '+%Y-%m-%d %H:%M')"
git push

echo "Done."
