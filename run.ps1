# Start the local read-only diagnostics console on Windows.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$scriptArgs = @($args)
Push-Location $PSScriptRoot
try {
    function Fail($message) { Write-Error $message; exit 1 }
    $python = $null
    foreach ($candidate in @(@("py", "-3.14"), @("py", "-3.13"), @("py", "-3.12"), @("python", ""))) {
        $command = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $candidateArgs = @($candidate[1]) | Where-Object { $_ }
        try {
            $supported = & $command.Source @candidateArgs -c "import sys; print(sys.version_info >= (3, 12))" 2>$null
            if ($supported -eq "True") { $python = @($command.Source) + $candidateArgs; break }
        } catch { continue }
    }
    if (-not $python) { Fail "Python 3.12 or newer is required." }
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $pythonArgs = @($python | Select-Object -Skip 1)
        & $python[0] @pythonArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail "Unable to create the virtual environment." }
    }
    & $venvPython -c "import fastapi, playwright, uvicorn" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m pip install --require-hashes --no-input -r requirements.lock
        if ($LASTEXITCODE -ne 0) { Fail "Dependency installation failed." }
    }
    if ($scriptArgs.Count -gt 0 -and $scriptArgs[0] -eq "--check") {
        Write-Host "Python: $(& $venvPython -V 2>&1)"
        Write-Host "Data directory: $(& $venvPython -c 'from app.config import settings; print(settings.data_dir)')"
        Write-Host "Browser: $(& $venvPython -c 'from app.runner.browser import describe; print(describe())')"
        Write-Host "Token file: $(& $venvPython -c 'from app.config import settings; print(settings.data_dir / ''console.token'')')"
        exit 0
    }
    $port = if ($env:GAI_BIND_PORT) { $env:GAI_BIND_PORT } else { "8765" }
    $env:GAI_BIND_PORT = $port
    & $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $port --log-level warning
    exit $LASTEXITCODE
}
finally { Pop-Location }
