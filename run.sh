#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "Installing dependencies…"
pip install -q -r requirements.txt

echo ""
echo "Starting server at http://localhost:8000  (Ctrl+C to stop)"
echo ""
uvicorn backend.app:app --reload --port 8000
