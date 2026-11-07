# GaiConsole

Konsol lokal (`127.0.0.1`) untuk otomasi browser multi-akun. Jalan di **macOS**
dan **Ubuntu/Linux**: Chrome asli kalau ada, Chromium sistem kalau tidak,
browser bawaan Playwright sebagai jaring terakhir.

| platform | status |
|---|---|
| Ubuntu / Linux | diuji: Python 3.12.3 aarch64, Chromium sistem + bundled, backend `file` dan `secret-tool` |
| macOS | jalur lama dipertahankan apa adanya (Keychain, `/Applications/Google Chrome.app`) |

## Postur keamanan

Dependensi langsung dijaga seminimal mungkin — **9 paket**, semuanya
repositori kanonik:

| paket | pemelihara |
|---|---|
| fastapi, uvicorn | Encode / `fastapi/fastapi` |
| python-multipart | `Kludex/python-multipart` (form impor) |
| jinja2 | Pallets |
| pydantic, pydantic-settings | `pydantic/*` |
| aiosqlite | omnilib |
| psutil | Giampaolo Rodola |
| playwright | Microsoft |

Yang sengaja **tidak** dipakai:

- `keyring` → pakai CLI bawaan OS. macOS: `security` (Keychain), Linux:
  `secret-tool` (libsecret → gnome-keyring/KWallet lewat D-Bus). Secret masuk
  lewat stdin, hanya atribut yang ada di argv, jadi nilainya tidak pernah
  muncul di `ps`. Kalau tidak ada keyring yang hidup (server headless,
  container, SSH tanpa sesi desktop), backend jatuh ke **file 0600** di dalam
  data dir 0700 — lebih lemah dari keyring, karena itu `run.sh --check`,
  `/healthz`, dan log boot selalu menyebut backend yang benar-benar dipakai.
- `pyotp` → TOTP RFC 6238 ditulis dengan `hmac`+`base64` stdlib, diuji
  terhadap vektor RFC 6238 Appendix B (`tests/selftest.py`).
- `email-validator` → regex sederhana, cukup untuk alamat Google.
- `uvicorn[standard]` → `uvicorn` polos, membuang httptools/uvloop/watchfiles/
  websockets/pyyaml.

Pengerasan lain:

- `requirements.lock` di-pin penuh **dengan sha256**, dipasang lewat
  `pip install --require-hashes`. Lock memuat hash untuk **linux
  (manylinux/musllinux x86_64+aarch64) dan darwin (arm64+x86_64)** sekaligus —
  satu lock, dua OS: pip memilih artifact yang cocok untuk host lalu memeriksa
  hashnya. Tiap paket diberi komentar `# files:` yang menyebut cakupannya.
  Regenerasi: `tools/make_lock.py` (dari venv), `--from-lock` (tanpa venv),
  `--verify` (cocokkan lock dengan PyPI).
- Console butuh token 43-char (`?t=` → cookie), dibandingkan constant-time.
  Tanpa token, daftar akun tidak bisa dibaca proses lokal lain.
- Data dir `0700`; `db.sqlite3`, `console.token`, dan `vault.json` `0600`.
- Password, seed TOTP, nomor kartu, dan CVC **hanya** di vault (Keychain /
  Secret Service / file 0600). Tabel `accounts` menyimpan handle
  (`secret_ref`, `totp_ref`), bukan nilai.
- `/api` tidak pernah mengembalikan nomor kartu utuh atau CVC.

## Yang dibutuhkan

- **Google Chrome** atau **Chromium**. Linux tanpa keduanya: `run.sh` memasang
  Chromium bawaan Playwright (≈120 MB, sekali; batalkan dengan
  `GAI_SKIP_BROWSER_INSTALL=1`).
- Egress **US** (VPN). Alur menunggu sampai 120 detik lalu berhenti kalau bukan US.
- Python 3.12+ (`python3.13` lalu `python3.12`).
- Stok akun (`email|password|seed TOTP`) dan kartu (`nomor|MM/YY|CVC`) — keduanya
  masuk lewat UI, nilainya disimpan di vault.

### Ubuntu

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv libsecret-tools git
# opsional — keyring sungguhan di sesi desktop:
#   sudo apt install -y gnome-keyring
# opsional — server tanpa layar yang ingin browser headed/screenshot:
#   sudo apt install -y xvfb
git clone https://github.com/danskiv/google-ai-pro-farming-2-years-copy
cd google-ai-pro-farming-2-years-copy
./run.sh --check   # python, data dir, backend secret, browser (venv dibuat dulu kalau belum ada)
./run.sh
```

Ubuntu 24.04 sudah membawa Python 3.12. Untuk 22.04 (bawaannya 3.10, di bawah
`requires-python`), ambil 3.12 dari deadsnakes:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y python3.12 python3.12-venv
```

Chrome asli (opsional, GaiConsole menemukannya sendiri):

```bash
wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
  | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
sudo apt install -y google-chrome-stable   # setelah repo Google ditambahkan
```

`libsecret-tools` hanya berguna kalau ada Secret Service yang hidup. Di server
headless tanpa sesi desktop, `secret-tool` ada tetapi setiap panggilan gagal
(`Cannot autolaunch D-Bus without X11 $DISPLAY`, atau
`Object does not exist at path /org/freedesktop/secrets/collection/login`).
GaiConsole mendeteksi itu lewat round-trip probe dan pindah ke backend file,
bukan gagal saat menyimpan password pertama.

## Jalankan

```bash
./run.sh
```

`run.sh` membuat venv, memasang dependensi hash-pinned bila belum ada, lalu
menyajikan konsol di `127.0.0.1:8765` (`PORT=9000 ./run.sh` untuk port lain).
Buka:

```
http://127.0.0.1:8765/?t=<isi token>
```

| OS | lokasi token |
|---|---|
| macOS | `~/Library/Application Support/GaiConsole/console.token` |
| Linux | `~/.local/share/GaiConsole/console.token` (ikut `$XDG_DATA_HOME`) |

Token juga dicetak di log saat boot. Tanpa token, daftar akun tidak bisa dibaca.

Sebagai service (Ubuntu, tanpa login):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/gai-console.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gai-console
loginctl enable-linger "$USER"          # tetap hidup setelah logout
journalctl --user -u gai-console -f
```

Unit itu memakai `WorkingDirectory=%h/google-ai-pro-farming-2-years-copy` —
sesuaikan kalau repo di-clone ke nama lain.

## Variabel lingkungan

Semua variabel ber-prefix `GAI_` dibaca `app/config.py`; `PORT` dan
`GAI_SKIP_BROWSER_INSTALL` dibaca `run.sh`.

| variabel | arti |
|---|---|
| `PORT` | port konsol (default 8765) |
| `GAI_BIND_HOST` | alamat bind (default `127.0.0.1` — jangan dibuka ke LAN) |
| `GAI_BIND_PORT` | sama dengan `PORT`, menang kalau keduanya diisi |
| `GAI_DATA_DIR` | pindahkan data dir (default: path per-OS di atas) |
| `GAI_SECRET_BACKEND` | `auto` (default) / `security` / `secret-tool` / `file` |
| `GAI_BROWSER_CHANNEL` | `auto` (default) / `chrome` / `chromium` / `bundled` |
| `GAI_BROWSER_EXECUTABLE` | path biner browser tertentu, mis. snap Chromium |
| `GAI_HEADLESS` | `auto` (default) / `1` / `0` |
| `GAI_NO_SANDBOX` | `1` kalau Chromium tidak bisa memakai sandbox (container) |
| `GAI_MAX_CONCURRENT` | jumlah run paralel (default 1 — jangan dinaikkan tanpa ukur) |
| `GAI_RUN_TIMEOUT` | batas detik satu run (default 900) |
| `GAI_SKIP_BROWSER_INSTALL` | `1` untuk melarang `run.sh` mengunduh Chromium |

`auto` untuk browser berarti: Chrome di `/Applications` (macOS) atau
`google-chrome`/`google-chrome-stable` di PATH → `channel="chrome"`; Chromium
sistem/snap/`/opt` → `executable_path`; tidak ada keduanya → Chromium bawaan
Playwright (dengan fallback otomatis kalau channel yang diminta tidak ketemu).

`auto` untuk headless berarti: ada `DISPLAY`/`WAYLAND_DISPLAY` → headed, tidak
ada → headless. Permintaan headed di mesin tanpa layar **dialihkan** ke headless
dengan peringatan di log, bukan crash. Untuk memaksa headed: `GAI_HEADLESS=0`
(dan sediakan `xvfb-run`).

Backend secret yang dipilih eksplisit tetapi tidak bisa dipakai akan **gagal
keras** saat menulis (`RuntimeError: secret-tool write failed…`), bukan menurun
diam-diam ke file — supaya tidak ada kesan proteksi yang keliru.

## Verifikasi

```bash
.venv/bin/python tests/selftest.py     # 11 test: TOTP RFC, DB, vault, browser, lock
python3 tools/make_lock.py --verify    # hash lock vs PyPI, tanpa venv
./run.sh --check                       # python, data dir, backend secret, browser
curl -s localhost:8765/healthz         # {"ok":true,"secret_backend":…,"browser":…}
```

`/healthz` sengaja menyebut backend dan browser yang hidup: itulah cara
memastikan secret benar-benar di keyring dan bukan di file.

## Troubleshooting

| gejala | sebab & tindakan |
|---|---|
| `THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE` | lock dibuat di platform lain. Regenerasi: `python3 tools/make_lock.py --from-lock` |
| `secret-tool: Cannot autolaunch D-Bus without X11 $DISPLAY` | tidak ada Secret Service. Biarkan `auto` (jatuh ke file 0600) atau jalankan di sesi desktop |
| `Object does not exist at path /org/freedesktop/secrets/collection/login` | koleksi login belum dibuat; `gnome-keyring` butuh prompter ber-display. Pakai sesi desktop atau `xvfb-run` |
| `Missing X server or $DISPLAY` | `GAI_HEADLESS=1`, atau `xvfb-run -a ./run.sh` |
| `Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome` | GaiConsole otomatis fallback ke Chromium bawaan; kalau bundled belum ada: `.venv/bin/python -m playwright install chromium` |
| `No usable sandbox!` / `clone3` | container tanpa user namespace: `GAI_NO_SANDBOX=1 ./run.sh` |
| browser mati saat buka banyak tab | `/dev/shm` kecil — sudah ditangani `--disable-dev-shm-usage` di Linux |
| `token required` (401) | ambil `?t=` dari `console.token` (lokasi di tabel atas) atau dari log boot |

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

Di server tanpa layar, dua titik itu tetap berjalan (prompt-nya di HP). Kalau
butuh melihat layar browser: `xvfb-run -a ./run.sh`, log dan screenshot tetap
tersimpan di `artifacts/`.

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
