# ============================================================
#  Local verify launcher (does NOT affect production / Render)
#  Usage:  .\run-local.ps1
#  Then open:        http://localhost:8000
#  Test login at:    http://localhost:8000/dev-login
#  Stop with:        Ctrl + C
#
#  NOTE: keep this file ASCII-only. PowerShell 5.1 reads .ps1 as
#  the system codepage (cp932 on JP Windows), so non-ASCII text
#  can corrupt parsing. Japanese UI text lives in the app, not here.
# ============================================================

# Make console/Python use UTF-8 so app logs are not garbled
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

# Local-only data dirs (separate from production DB; git-ignored)
$env:DB_DIR     = Join-Path $PSScriptRoot "localdata"
$env:UPLOAD_DIR = Join-Path $PSScriptRoot "localdata\uploads"

# Enable the test login (NEVER set this in production)
$env:ALLOW_DEV_LOGIN = "1"

# Dummy local settings
$env:SESSION_SECRET = "local-dev-secret"
$env:BASE_URL       = "http://localhost:8000"
$env:DEBUG_ERRORS   = "1"

# Seed zoo data into the local DB on startup (idempotent: skips existing).
# Lets you test with the full zoo list locally. Not set in production.
$env:CSV_PATH       = Join-Path $PSScriptRoot "zoos_with_animals.csv"

New-Item -ItemType Directory -Force -Path $env:DB_DIR     | Out-Null
New-Item -ItemType Directory -Force -Path $env:UPLOAD_DIR | Out-Null

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " LOCAL server starting..." -ForegroundColor Cyan
Write-Host "   Top page   : http://localhost:8000" -ForegroundColor Yellow
Write-Host "   Test login : http://localhost:8000/dev-login" -ForegroundColor Yellow
Write-Host " Stop: Ctrl + C" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
