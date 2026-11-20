# SkyAssist demo launcher for Windows. Same job as run_demo.sh, without uv or Python 3.14:
# it needs Python 3.12 or newer, and Node 22 or newer only when web\out is missing.
#
#   .\run_demo.ps1            start the API (builds the front end and seeds the database first if needed)
#   .\run_demo.ps1 reset      rebuild the demo database (stop the API first)
#   .\run_demo.ps1 build      rebuild the front end after changing web\
#
# Set SKYASSIST_VENV to keep the virtual environment somewhere with a short path. Windows' 260 character path
# limit breaks pip inside deeply nested folders.
param([ValidateSet("up", "reset", "build")][string]$Command = "up")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Venv = if ($env:SKYASSIST_VENV) { $env:SKYASSIST_VENV } else { Join-Path $Root ".venv" }
$Py = Join-Path $Venv "Scripts\python.exe"
$DemoDb = Join-Path $Root "data\skyassist-demo.sqlite3"
$WebOut = Join-Path $Root "web\out\index.html"
$Port = 8000

function Ensure-Python {
    if (-not (Test-Path $Py)) {
        $system = Get-Command python -ErrorAction SilentlyContinue
        if (-not $system) { throw "Python 3.12 or newer is not on PATH." }
        Write-Host "creating $Venv"
        & $system.Source -m venv $Venv
    }
    & $Py -c "import fastapi, pydantic, segno, uvicorn, websockets" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "installing Python dependencies"
        $deps = & $Py -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))"
        & $Py -m pip install --quiet $deps
        if ($LASTEXITCODE -ne 0) { throw "pip install failed. If the path to this folder is long, set SKYASSIST_VENV to a short folder." }
    }
}

function Ensure-Database([bool]$force) {
    if ($force -or -not (Test-Path $DemoDb)) {
        $env:SKYASSIST_MODE = "demo"
        Write-Host "seeding $DemoDb"
        & $Py -c "from skyassist.seed import build_demo_db; print(build_demo_db())"
        if ($LASTEXITCODE -ne 0) { throw "seeding the demo database failed" }
    }
}

function Build-Web([bool]$force) {
    if ($force -or -not (Test-Path $WebOut)) {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "web\out is missing and npm is not on PATH. Install Node 22, then run: npm --prefix web ci; npm --prefix web run build" }
        Write-Host "building the front end (needs the npm registry and Google Fonts)"
        npm --prefix web ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        npm --prefix web run build
        if ($LASTEXITCODE -ne 0) { throw "front end build failed" }
    }
}

switch ($Command) {
    "reset" {
        if (Test-NetConnection 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue) { throw "The API is running on port $Port. Stop it first." }
        Ensure-Python
        Remove-Item "$DemoDb*" -Force -ErrorAction SilentlyContinue
        Ensure-Database $true
    }
    "build" { Build-Web $true }
    "up" {
        Ensure-Python
        Ensure-Database $false
        Build-Web $false
        $env:SKYASSIST_MODE = "demo"
        $env:SKYASSIST_SERVICE_URL = "http://127.0.0.1:$Port"
        # Frozen demo clock: the C01 numbers (21-29 min, 6 min to spare) do not drift during a rehearsal.
        if (-not $env:SKYASSIST_CLOCK_START) { $env:SKYASSIST_CLOCK_START = "C01" }
        if (-not $env:SKYASSIST_CLOCK_SPEED) { $env:SKYASSIST_CLOCK_SPEED = "0" }
        Write-Host ""
        Write-Host "SkyAssist demo"
        Write-Host "  Passenger kiosk   http://127.0.0.1:$Port/"
        Write-Host "  Coordinator       http://127.0.0.1:$Port/staff/#token=demo-coordinator"
        Write-Host "  Voice             " -NoNewline
        $voiceKey = $env:DEEPGRAM_API_KEY -or ((Test-Path ".env") -and (Select-String -Path ".env" -Pattern "^\s*DEEPGRAM_API_KEY=\S" -Quiet))
        Write-Host $(if ($voiceKey) { "key found" } else { "no key: add DEEPGRAM_API_KEY to .env to turn it on" })
        Write-Host "  Stop              Ctrl-C"
        Write-Host ""
        & $Py -m uvicorn skyassist.service_read:app_from_env --factory --host 127.0.0.1 --port $Port --timeout-graceful-shutdown 5
    }
}
