#!/bin/bash
# Jalankan konsol. Aman diulang: pakai venv kalau ada, kalau tidak buat dulu.
set -euo pipefail
cd "$(dirname "$0")"

PY=/opt/homebrew/bin/python3.13
[ -x "$PY" ] || PY=$(command -v python3)
[ -x .venv/bin/python ] || { "$PY" -m venv .venv && .venv/bin/python -m pip install --require-hashes -r requirements.lock; }

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning
