#!/usr/bin/env bash
# Jalankan konsol di macOS atau Ubuntu/Linux. Aman diulang: pakai venv kalau
# ada, kalau tidak buat dulu. `./run.sh --check` hanya memeriksa lingkungan.
set -euo pipefail
cd "$(dirname "$0")"

OS=$(uname -s)
say() { printf '%s\n' "$*" >&2; }
die() { printf 'gagal: %s\n' "$*" >&2; exit 1; }

# ── Python 3.12+ ────────────────────────────────────────────────────────────
# pyproject menuntut >=3.12. 3.13 lebih dulu karena itu yang dipakai di macOS,
# lalu 3.12 yang tersedia bawaan di Ubuntu 24.04.
find_python() {
  for cand in python3.13 python3.12; do
    if command -v "$cand" >/dev/null 2>&1; then command -v "$cand"; return 0; fi
  done
  if [ -x /opt/homebrew/bin/python3.13 ]; then echo /opt/homebrew/bin/python3.13; return 0; fi
  return 1
}
PY=$(find_python) || true
if [ -z "${PY:-}" ]; then
  case "$OS" in
    Darwin) die "python3.13 tidak ada. Pasang dulu: brew install python@3.13" ;;
    *)      die "python3.13/3.12 tidak ada. Ubuntu 24.04: sudo apt install -y python3.12 python3.12-venv"
            ;;
  esac
fi

if [ ! -x .venv/bin/python ]; then
  say "membuat .venv dengan $("$PY" -V 2>&1)"
  "$PY" -m venv .venv || die "venv gagal — paket python3-venv belum terpasang? (sudo apt install python3.12-venv)"
fi

if ! .venv/bin/python -c 'import fastapi, playwright, tzdata' >/dev/null 2>&1; then
  say "memasang dependensi hash-pinned dari requirements.lock"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --require-hashes --no-input -r requirements.lock
fi

# ── browser ─────────────────────────────────────────────────────────────────
browser_present() {
  [ "$OS" = "Darwin" ] && [ -d "/Applications/Google Chrome.app" ] && return 0
  for name in google-chrome google-chrome-stable chromium chromium-browser; do
    command -v "$name" >/dev/null 2>&1 && return 0
  done
  for path in /snap/bin/chromium /opt/google/chrome/chrome /usr/lib/chromium/chromium; do
    [ -x "$path" ] && return 0
  done
  return 1
}

if ! browser_present; then
  if [ "${GAI_SKIP_BROWSER_INSTALL:-0}" = "1" ]; then
    say "peringatan: tidak ada Chrome/Chromium, dan GAI_SKIP_BROWSER_INSTALL=1 — launch akan gagal"
  else
    say "tidak ada Chrome/Chromium sistem — memasang chromium bawaan Playwright"
    .venv/bin/python -m playwright install chromium \
      || say "peringatan: unduhan browser gagal; jalankan manual: .venv/bin/python -m playwright install --with-deps chromium"
  fi
fi

# ── lingkungan ──────────────────────────────────────────────────────────────
# PORT adalah nama pendeknya; GAI_BIND_PORT menang kalau keduanya diisi.
export GAI_BIND_PORT="${GAI_BIND_PORT:-${PORT:-8765}}"

if [ "$OS" = "Darwin" ]; then
  [ -x /usr/bin/security ] || say "peringatan: CLI security tidak ada — secret jatuh ke backend file"
else
  command -v secret-tool >/dev/null 2>&1 \
    || say "catatan: libsecret-tools tidak ada — secret disimpan di vault.json 0600 (bukan keyring)"
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    say "catatan: tidak ada DISPLAY — browser headless. Untuk headed: xvfb-run -a ./run.sh"
  fi
fi

if [ "${1:-}" = "--check" ]; then
  say "python   : $(.venv/bin/python -V 2>&1)"
  say "data dir : $(.venv/bin/python -c 'from app.config import settings; print(settings.data_dir)')"
  say "secret   : $(.venv/bin/python -c 'from app.secrets import describe; print(describe())')"
  say "browser  : $(.venv/bin/python -c 'from app.runner.browser import describe; print(describe())')"
  say "token    : $(.venv/bin/python -c 'from app.config import settings; print(settings.data_dir / "console.token")')"
  exit 0
fi

exec .venv/bin/python -m uvicorn app.main:app \
  --host "${GAI_BIND_HOST:-127.0.0.1}" --port "$GAI_BIND_PORT" --log-level warning
