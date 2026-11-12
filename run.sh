#!/usr/bin/env bash
# Start the local read-only diagnostics console on Linux or macOS.
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for candidate in python3.14 python3.13 python3.12; do
    if command -v "$candidate" >/dev/null 2>&1; then command -v "$candidate"; return 0; fi
  done
  return 1
}
PY=$(find_python) || { printf '%s\n' 'Python 3.12 or newer is required.' >&2; exit 1; }

if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, playwright, uvicorn' >/dev/null 2>&1; then
  .venv/bin/python -m pip install --require-hashes --no-input -r requirements.lock
fi

if [ "${1:-}" = "--check" ]; then
  .venv/bin/python -V
  .venv/bin/python -c 'from app.config import settings; print("Data directory:", settings.data_dir)'
  .venv/bin/python -c 'from app.runner.browser import describe; print("Browser:", describe())'
  .venv/bin/python -c 'from app.config import settings; print("Token file:", settings.data_dir / "console.token")'
  exit 0
fi

export GAI_BIND_PORT="${GAI_BIND_PORT:-8765}"
exec .venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$GAI_BIND_PORT" --log-level warning
