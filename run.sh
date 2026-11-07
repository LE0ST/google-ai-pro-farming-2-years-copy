#!/bin/bash
# Jalankan konsol. Aman diulang: pakai venv kalau ada, kalau tidak buat dulu.
set -euo pipefail
cd "$(dirname "$0")"

# Python 3.13 di mana pun ia berada; Homebrew jadi cadangan, bukan syarat mutlak.
PY=$(command -v python3.13 || true)
if [ -z "$PY" ] && [ -x /opt/homebrew/bin/python3.13 ]; then PY=/opt/homebrew/bin/python3.13; fi
if [ -z "$PY" ]; then
  echo "python3.13 tidak ditemukan. Pasang dulu: brew install python@3.13" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --require-hashes -r requirements.lock
fi

[ -d "/Applications/Google Chrome.app" ] || echo "peringatan: Google Chrome tidak ada di /Applications — login akan gagal" >&2
[ -x /usr/bin/security ] || echo "peringatan: CLI security tidak ada — secret tidak bisa disimpan" >&2

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8765}" --log-level warning
