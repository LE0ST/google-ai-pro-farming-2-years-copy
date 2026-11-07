# AGENTS.md — runbook untuk agent/engine yang mengoperasikan GaiConsole

Dokumen ini ditulis untuk agent AI (Muse, Hermes, atau engine lain) yang
diminta memasang atau menjalankan repo ini atas nama pemiliknya. Ikuti apa
adanya; jangan mengimprovisasi jalur pintas di sekitar guard.

## Misi

Per akun Google di vault: berlangganan Google AI Pro lewat penumpukan masa
gratis (layer 1 → 12 bulan, layer 2 → 18, layer 3 → 24), lalu **cancel
auto-renew** dan **lepas kartu** dari akun. Hasil akhir yang sah hanya satu:
langganan aktif sampai tanggal berakhir ±24 bulan, total yang dibayar **$0**,
status cancel terverifikasi, kartu tidak tertinggal di akun.

## Aturan keras

1. **Jangan pernah commit kalau total bukan $0 / bukan free trial.** Guard di
   `tasks/subscribe_flow.py` sudah menolak nominal bukan nol dan tanggal yang
   meleset; kalau guard berhenti, **berhenti** — jangan paksa, jangan edit
   guard supaya lolos.
2. **Jangan pernah mencetak rahasia.** Nomor kartu, CVC, password, seed TOTP,
   dan token konsol hidup di vault dan file data dir. Jangan echo ke log,
   chat, commit, atau file di repo. Sebut kartu hanya sebagai `Brand-last4`.
3. **Jangan commit artefak jalan** (log run, screenshot, profil browser, data
   dir) ke repo.
4. Dua titik memang butuh manusia dan **tidak boleh diakali**: device prompt
   saat login, dan persetujuan “Verify that it’s you” di HP saat melepas
   kartu. Minta pemilik menyetujui, lalu tunggu.
5. Satu run untuk satu akun dalam satu waktu (`GAI_MAX_CONCURRENT=1`).

## Prasyarat mesin

- Python 3.11+, venv dibuat `run.sh`.
- Egress internet dengan **negara US** (alur memverifikasi sendiri lewat
  ipwho.is dan berhenti kalau bukan US).
- Mesin tanpa layar: pasang Xvfb. Google menolak login headless
  (`signin/rejected`); jalankan browser **headed** di display virtual.
- Egress terbatas (semua trafik dipaksa lewat proxy)? Baca bagian
  **“Egress terbatas”** di README dan set kombinasi env-nya sebelum menyerah:
  relay lokal + `GAI_BROWSER_PROXY` + `GAI_IGNORE_CERT_ERRORS=1` +
  `GAI_MIRROR_CLIENTS2=1`.

## Pasang & jalankan konsol

```bash
./run.sh --check                        # python, data dir, backend secret, browser
.venv/bin/python tests/selftest.py      # harus 11/11
./run.sh                                # konsol di 127.0.0.1:8765
curl -s localhost:8765/healthz          # {"ok":true,...}
```

Token konsol ada di file `console.token` di data dir
(Linux: `~/.local/share/GaiConsole/`, macOS: `~/Library/Application
Support/GaiConsole/`). Semua panggilan API memakai header
`X-Console-Token: <token>` atau query `?t=<token>`. Jangan tampilkan isinya.

## Isi data (lewat UI atau API)

- Kartu: `POST /cards/import` — format `nomor|MM/YY|CVC` per baris.
- Akun: `POST /accounts/import` — format `email|password|seed_totp` per baris.
  Akun otomatis dipasangkan ke kartu yang bebas.

## Jalankan satu akun

```bash
curl -s -X POST localhost:8765/accounts/<account_id>/run -H "X-Console-Token: $TOKEN"
curl -s localhost:8765/api/state -H "X-Console-Token: $TOKEN"   # status + step per akun
```

Pantau log run terbaru di `<data dir>/artifacts/run_<account_id>_*.log` dan
screenshot `flow_L<layer>_*.png` / `flow_final_*.png` di folder yang sama.
Tahapan sehat: `EGRESS … US` → `LOGIN siap` → `DISC … layer …` → per layer
`GUARD … lolos` → `terkirim` → `CANCEL terverifikasi` → `SELESAI`.
Status akhir akun di `/api/state` harus `subscribed`.

Run yang terputus aman diulang dari konsol: discovery membaca term yang
sudah berjalan dan melanjutkan dari layer yang tepat.

## Playbook kegagalan (dari pengalaman nyata)

| Gejala di log / layar | Penyebab | Tindakan |
|---|---|---|
| `nav gagal … ERR_SSL_PROTOCOL_ERROR` di cek egress | proxy egress menolak tunnel Chromium | relay lokal + `GAI_BROWSER_PROXY=127.0.0.1:<port>` |
| semua situs error sertifikat | proxy menandatangani ulang TLS | `GAI_IGNORE_CERT_ERRORS=1` |
| `LOGIN GAGAL`, Google menjawab “Wrong password” | password di vault salah | minta password yang benar ke pemilik, tulis ulang ke vault, reset status akun ke `pending`, ulangi |
| dialog checkout “Loading…” > 60 detik | `clients2.google.com` diblokir (`policy_denied`) | `GAI_MIRROR_CLIENTS2=1`, restart konsol, ulangi |
| `dialog tidak terbuka` di layer 1 | halaman paket memakai tombol “Get discount” | sudah ditangani alur (klik tombol paling kanan = kartu AI Pro); kalau layout berubah lagi, periksa teks tombol saat ini sebelum mengubah kode |
| kartu: `dialog tidak kembali setelah menyimpan` | reCAPTCHA muncul saat Save card, atau kartu memang belum tersimpan | cek `payments.google.com` → metode pembayaran. Kalau kosong: ulangi run (profil sama sering lolos), atau jalankan terawasi dan selesaikan tantangan di jendela browser |
| persen persetujuan tak kunjung selesai saat lepas kartu | menunggu tap pemilik di HP (maks 300 detik) | ingatkan pemilik; langganan + cancel tetap sah walau langkah ini gagal |
| `guard … GAGAL` / nominal bukan nol terbaca | penawaran berubah atau token layer tidak berlaku | **berhenti total**, laporkan teks dialog ke pemilik; jangan commit |

## Selesai & laporkan

Laporkan hanya: akun (email), plan aktif, tanggal berakhir, status cancel,
status kartu (terlepas / tertinggal), dan total dibayar ($0). Tanpa rahasia
apa pun. Bila ada patch lokal yang terbukti perlu, buat branch baru
`fix/<ringkasan>` dan commit hanya file kode — konfigurasi spesifik mesin
(env, alamat proxy, unit service) tidak masuk repo.
