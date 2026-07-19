#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
elif [[ -d "venv" ]]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
else
  echo "No Python virtualenv found. Create one at .venv or venv first."
  exit 1
fi

if [[ ! -d "node_modules/@rollup/rollup-linux-x64-gnu" ]]; then
  echo "Installing/fixing WSL Node dependencies..."
  npm install
  echo
fi

rm -rf node_modules/.vite

cleanup() {
  echo
  echo "Stopping dev servers..."
  jobs -pr | xargs -r kill
  wait || true
}

trap cleanup INT TERM EXIT

echo "Starting Home Vision AI dev stack from: $APP_DIR"
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo

python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload &
sh run_celery_dev.sh &
npm run dev &

wait
