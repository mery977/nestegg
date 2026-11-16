<#
  First setup on Windows, in one command, from the project folder:   .\scripts\setup.ps1
  Creates .venv (only if missing), installs the project, runs the tests, loads the fictional demo customers
  and prints a first report. Safe to run again after an update: it reuses the existing .venv.
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Host "Creating the virtual environment (.venv)..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
}
. .venv\Scripts\Activate.ps1
python -m pip install -q --upgrade pip
pip install -q -e ".[dev]"

Write-Host "`n== Tests ==" -ForegroundColor Cyan
pytest -q
if ($LASTEXITCODE -ne 0) { throw "Some tests failed: send the output above." }

Write-Host "`n== Demo customers (fictional data) ==" -ForegroundColor Cyan
nestegg demo
Write-Host "`n== First report ==" -ForegroundColor Cyan
nestegg report --customer C001
Write-Host "`nReady. The terminal stays in the project's environment (.venv)." -ForegroundColor Green
