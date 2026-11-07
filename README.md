# GaiConsole

Konsol lokal (127.0.0.1) untuk otomasi browser multi-akun. Jalan di **macOS**
dan **Ubuntu/Linux**. Python 3.12+, Playwright: Chrome asli kalau ada,
Chromium sistem kalau tidak, browser bawaan Playwright sebagai jaring terakhir.

## Postur keamanan

Dependensi langsung dijaga seminimal mungkin — **8 paket**, semuanya
repositori kanonik:

| paket | pemelihara |
|---|---|
| fastapi, uvicorn | Encode / `fastapi/fastapi` |
| jinja2 | Pallets |
| pydantic, pydantic-settings | `pydantic/*` |
| aiosqlite | omnilib |
| psutil | Giampaolo Rodola |
| playwright | Microsoft |

Yang sengaja **tidak** dipakai:

- `keyring` → pakai CLI bawaan OS. macOS: `security` (Keychain), Linux:
  `secret-tool` (libsecret → gnome-keyring/KWallet via D-Bus). Secret masuk
  lewat stdin, jadi tidak pernah muncul di argv/`ps`. Kalau tidak ada keyring
  yang hidup (server headless, container, SSH tanpa sesi), backend jatuh ke
  **file 0600** di dalam data dir 0700 — lebih lemah dari keyring, karena itu
  `run.sh --check`, `/healthz`, dan log boot selalu menyebut backend yang hidup.
- `pyotp` → TOTP RFC 6238 ditulis dengan `hmac`+`base64` stdlib, diuji
  terhadap vektor RFC 6238 Appendix B (`tests/selftest.py`).
- `email-validator` → regex sederhana, cukup untuk alamat Google.
- `uvicorn[standard]` → `uvicorn` polos, membuang httptools/uvloop/watchfiles/
  websockets/pyyaml.

Pengerasan lain:

- `requirements.lock` di-pin penuh **dengan sha256**, dipasang lewat
  `pip install --require-hashes`. Lock memuat hash untuk **linux
  (manylinux/musllinux x86_64+aarch64) dan darwin (arm64+x86_64)** sekaligus —
  satu lock, dua OS, pip memilih artifact yang cocok lalu memeriksa hashnya.
  Regenerasi: `tools/make_lock.py` (tanpa venv: `--from-lock`).
- Console butuh token 43-char (`?t=` → cookie), dibandingkan constant-time.
  Tanpa token, daftar akun tidak bisa dibaca proses lokal lain.
- Data dir `0700`, `db.sqlite3`, `console.token`, dan `vault.json` `0600`.
- Password, seed TOTP, nomor kartu, dan CVC **hanya** di vault (Keychain /
  Secret Service / file 0600). Tabel `accounts` menyimpan handle
  (`secret_ref`, `totp_ref`), bukan nilai.
- `privacy: /api` tidak pernah mengembalikan nomor kartu utuh atau CVC.

## Yang dibutuhkan

- **Google Chrome** atau **Chromium**. Linux tanpa keduanya: `run.sh` memasang
  Chromium bawaan Playwright (≈120 MB, sekali).
- Egress **US** (VPN). Alur menunggu sampai 120 detik lalu berhenti kalau bukan US.
- Python 3.12+ (`python3.13`/`python3.12`).
- Stok akun (`email|password|seed TOTP`) dan kartu (`nomor|MM/YY|CVC`) — keduanya
  masuk lewat UI, nilainya disimpan di vault.

### Ubuntu 22.04/24.04

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv libsecret-tools git
# opsional, untuk keyring sungguhan di sesi desktop:
#   sudo apt install -y gnome-keyring
# opsional, untuk server tanpa layar yang tetap ingin browser headed:
#   sudo apt install -y xvfb
git clone https://github.com/danskiv/google-ai-pro-farming-2-years-copy && cd google-ai-pro-farming-2-years-copy
./run.sh --check   # periksa python, data dir, backend secret, browser
./run.sh
```

Ubuntu 24.04 sudah membawa Python 3.12. Untuk 22.04 (bawaannya 3.10, di bawah
`requires-python`), ambil 3.12 dari deadsnakes:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y python3.12 python3.12-venv
```

Kalau memakai Chrome asli:
`wget -qO- https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg`
lalu pasang `google-chrome-stable` seperti biasa — GaiConsole menemukannya sendiri.

`libsecret-tools` hanya berguna kalau ada Secret Service yang hidup. Di server
headless tanpa sesi desktop, `secret-tool` ada tetapi setiap panggilan gagal
("Cannot autolaunch D-Bus without X11 $DISPLAY"); GaiConsole mendeteksi itu dan
pindah ke backend file, bukan gagal saat menyimpan password pertama.

## Jalankan

```bash
./run.sh
```

`run.sh` membuat venv dan memasang dependensi hash-pinned bila belum ada, lalu
menyajikan konsol di `127.0.0.1:8765` (ubah dengan `PORT=9000 ./run.sh`). Buka:

```
http://127.0.0.1:8765/?t=<isi token>
```

| OS | lokasi token |
|---|---|
| macOS | `~/Library/Application Support/GaiConsole/console.token` |
| Linux | `~/.local/share/GaiConsole/console.token` (ikut `$XDG_DATA_HOME`) |

Token dicetak di log saat boot. Tanpa token, daftar akun tidak bisa dibaca.

Sebagai service (Ubuntu, tanpa login):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/gai-console.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gai-console
loginctl enable-linger "$USER"   # tetap hidup setelah logout
journalctl --user -u gai-console -f
```

## Variabel lingkungan

| variabel | arti |
|---|---|
| `PORT` | port konsol (default 8765) |
| `GAI_DATA_DIR` | pindahkan data dir (default: path per-OS di atas) |
| `GAI_SECRET_BACKEND` | `auto` (default) / `security` / `secret-tool` / `file` |
| `GAI_BROWSER_CHANNEL` | `auto` (default) / `chrome` / `chromium` / `bundled` |
| `GAI_BROWSER_EXECUTABLE` | path biner browser tertentu, mis. snap Chromium |
| `GAI_HEADLESS` | `auto` (default) / `1` / `0` |
| `GAI_NO_SANDBOX` | `1` kalau Chromium tidak bisa memakai sandbox (container) |
| `GAI_SKIP_BROWSER_INSTALL` | `1` untuk melarang `run.sh` mengunduh Chromium |

`auto` untuk browser berarti: Chrome di `/Applications` (macOS) atau
`google-chrome` di PATH → `channel="chrome"`; Chromium sistem → `executable_path`;
tidak ada keduanya → Chromium bawaan Playwright. `auto` untuk headless berarti:
ada `DISPLAY`/`WAYLAND_DISPLAY` → headed, tidak ada → headless (dan itu
dicatat di log, bukan gagal senyap).

## Cara pakai

1. Impor kartu: `nomor|MM/YY|CVC` (satu per baris atau satu per satu).
2. Impor akun: `email|password|seed_totp` (format sama).
3. Tekan **jalankan** pada akun, atau **run-all** untuk antrean berurutan.

Alur per akun: login → discovery term berjalan → layer 1–4 → guard tanggal →
cancel → lepas kartu dari akun (kartu tetap di vault untuk akun berikutnya).

Discovery memilih titik mulai sendiri: 0 bulan → layer 1, 12 → layer 2, 18 → layer 3,
24 → hanya mencoba layer 4 lalu cancel. Jadi run yang terputus aman diulang.

Dua titik tetap butuh tangan manusia di HP, dan keduanya ditunggu (5 menit login,
300 detik hapus kartu): device prompt saat login, dan persetujuan verifikasi saat
menghapus kartu. Kalau tidak disetujui, langganan tetap aktif 24 bulan dan cancel
tetap berjalan — hanya kartu tertinggal di akun.

Di server tanpa layar, dua titik itu tetap berjalan (prompt-nya di HP), tetapi
kalau butuh melihat layar browser: `xvfb-run -a ./run.sh` lalu ambil screenshot
dari `artifacts/`.

## Struktur

```
app/
  main.py          rute halaman + antrean (+ /healthz: backend & browser)
  config.py        settings, data dir per-OS, token console
  db.py            skema SQLite + migrasi idempoten
  vault.py         akun & kartu (nilai di vault)
  secrets.py       backend secret: security / secret-tool / file 0600
  totp.py          TOTP stdlib
  egress.py        cek negara egress
  runner/          browser (resolusi channel/headless), navigasi, antrean
  sites/google_one/
    login.py       login + TOTP + tunggu device prompt
    checkout.py    hook, arm, baca dialog
    discover.py    peta term → layer
    tokens.py      token per layer
    hook.js        penulisan ulang request
tasks/
  subscribe_flow.py  alur utama (login → layer → cancel → lepas kartu)
  cancel.py          cancel manual
  probe_pay.py       pemeriksa halaman pembayaran
deploy/
  gai-console.service  unit systemd --user untuk Ubuntu
tests/selftest.py
tools/make_lock.py     lock multi-platform (linux+darwin) dari PyPI JSON
```
