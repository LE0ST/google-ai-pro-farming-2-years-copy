# Jalankan konsol di Windows. Padanan run.sh: aman diulang -- pakai venv kalau
# ada, kalau tidak buat dulu. `.\run.ps1 --check` hanya memeriksa lingkungan.
# Browser jalan headed bawaan (desktop Windows selalu punya layar, tidak
# perlu Xvfb) dan secret jatuh ke backend file di %LOCALAPPDATA%\GaiConsole
# (tidak ada CLI keyring bawaan Windows; proteksinya ACL profil user).
#
# File ini sengaja ASCII murni: Windows PowerShell 5.1 membaca .ps1 tanpa BOM
# sebagai ANSI (cp1252), dan karakter seperti em-dash di dalam string bisa
# mengacaukan parser sebelum baris pertama jalan.
$ErrorActionPreference = "Stop"

# Paksa UTF-8 untuk proses Python anak: tanpa ini, stdout di Windows memakai
# codepage lokal (cp1252) dan log konsol/alur yang penuh karakter non-ASCII
# bisa berhenti dengan UnicodeEncodeError.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Push-Location $PSScriptRoot
try {
    # Disimpan untuk dipulihkan di finally: GAI_BIND_PORT sesi pemanggil
    # tidak boleh berubah permanen gara-gara menjalankan skrip ini.
    $PrevBindPort = $env:GAI_BIND_PORT
    function Say($msg) { Write-Host $msg }
    function Die($msg) { Write-Host "gagal: $msg"; exit 1 }

    # -- Python 3.12+ -------------------------------------------------------
    # pyproject menuntut >=3.12. Launcher `py` adalah jalur standar instalasi
    # python.org; `python` di PATH jadi cadangan.
    function Find-Python {
        foreach ($cand in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3"), @("python", ""))) {
            $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
            if (-not $exe) { continue }
            $extra = @($cand[1]) | Where-Object { $_ }
            try {
                $ver = & $exe.Source @extra -c "import sys; print(sys.version_info[:2] >= (3, 12))" 2>$null
            } catch { continue }
            if ($ver -eq "True") { return @($exe.Source) + $extra }
        }
        return $null
    }

    # @(...) menahan hasil 1 elemen tetap sebagai array; tanpa itu PowerShell
    # meng-unroll-nya jadi string dan $Py[0] mengambil karakter pertamanya.
    $Py = @(Find-Python)
    if ($Py.Count -eq 0) { Die "Python 3.12+ tidak ada. Pasang dulu dari https://www.python.org/downloads/ (centang 'py launcher')" }
    $PyExe = $Py[0]
    $PyArgs = @($Py | Select-Object -Skip 1)

    $VenvPy = ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) {
        Say "membuat .venv dengan $(& $PyExe @PyArgs -V 2>&1)"
        & $PyExe @PyArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { Die "venv gagal" }
    }

    $depsOk = $false
    try {
        & $VenvPy -c "import fastapi, playwright, tzdata" 2>$null
        $depsOk = ($LASTEXITCODE -eq 0)
    } catch { $depsOk = $false }
    if (-not $depsOk) {
        Say "memasang dependensi hash-pinned dari requirements.lock"
        & $VenvPy -m pip install --quiet --upgrade pip
        & $VenvPy -m pip install --require-hashes --no-input -r requirements.lock
        if ($LASTEXITCODE -ne 0) { Die "pip install gagal" }
    }

    # -- browser ------------------------------------------------------------
    function Test-BrowserPresent {
        $paths = @(
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
            "$env:LOCALAPPDATA\Chromium\Application\chrome.exe",
            "$env:ProgramFiles\Chromium\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Chromium\Application\chrome.exe"
        )
        foreach ($p in $paths) { if (Test-Path $p) { return $true } }
        return [bool](Get-Command chrome -ErrorAction SilentlyContinue)
    }

    if (-not (Test-BrowserPresent)) {
        if ($env:GAI_SKIP_BROWSER_INSTALL -eq "1") {
            Say "peringatan: tidak ada Chrome/Chromium, dan GAI_SKIP_BROWSER_INSTALL=1 -- launch akan gagal"
        } else {
            Say "tidak ada Chrome/Chromium sistem -- memasang chromium bawaan Playwright"
            & $VenvPy -m playwright install chromium
            if ($LASTEXITCODE -ne 0) { Say "peringatan: unduhan browser gagal; jalankan manual: .venv\Scripts\python -m playwright install chromium" }
        }
    }

    # -- lingkungan ---------------------------------------------------------
    # PORT adalah nama pendeknya; GAI_BIND_PORT menang kalau keduanya diisi.
    # Dihitung ke variabel lokal supaya env sesi PowerShell pemanggil bersih.
    $BindPort = if ($env:GAI_BIND_PORT) { $env:GAI_BIND_PORT } elseif ($env:PORT) { $env:PORT } else { "8765" }
    $BindHost = if ($env:GAI_BIND_HOST) { $env:GAI_BIND_HOST } else { "127.0.0.1" }

    if ($args.Count -gt 0 -and $args[0] -eq "--check") {
        Say "python   : $(& $VenvPy -V 2>&1)"
        Say "data dir : $(& $VenvPy -c 'from app.config import settings; print(settings.data_dir)')"
        Say "secret   : $(& $VenvPy -c 'from app.secrets import describe; print(describe())')"
        Say "browser  : $(& $VenvPy -c 'from app.runner.browser import describe; print(describe())')"
        Say "token    : $(& $VenvPy -c 'from app.config import settings; print(settings.data_dir / ''console.token'')')"
        exit 0
    }

    Say "catatan: tidak ada keyring CLI di Windows -- secret disimpan di vault.json di data dir (ACL profil user)"

    # Banner boot app membaca port dari env GAI_BIND_PORT; proses anak harus
    # melihat nilai yang sama dengan --port di bawah, tanpa mengotori sesi
    # pemanggil (dipulihkan di finally).
    $env:GAI_BIND_PORT = $BindPort
    & $VenvPy -m uvicorn app.main:app --host $BindHost --port $BindPort --log-level warning
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $PrevBindPort) {
        Remove-Item Env:GAI_BIND_PORT -ErrorAction SilentlyContinue
    } else {
        $env:GAI_BIND_PORT = $PrevBindPort
    }
    Pop-Location
}
