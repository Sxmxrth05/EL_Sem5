#!/usr/bin/env sh
# Same as `make all`: tests, default experiment, then start the service.
#   sh scripts/run_all.sh [--skip-serve]
set -eu
cd "$(dirname "$0")/.."
PY="${PY:-python}"
CONFIG="${CONFIG:-configs/default.yaml}"
PORT="${PORT:-8000}"

echo "== lint";  ruff check .
echo "== tests"; "$PY" -m pytest -rs
echo "== experiment"
if [ -f eval/run_experiment.py ]; then
  "$PY" -m eval.run_experiment --config "$CONFIG"
else
  echo "eval/run_experiment.py not merged yet (Person 3). Running parity summary instead."
  "$PY" -m tools.parity
fi
if [ "${1:-}" != "--skip-serve" ]; then
  echo "== service on http://127.0.0.1:$PORT/"
  exec "$PY" -m uvicorn service.api:app --port "$PORT"
fi
