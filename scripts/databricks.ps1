<#
  Deploy and run the Nestegg lakehouse on Databricks (Free Edition or Azure Databricks), from Windows:
      .\scripts\databricks.ps1 -Workspace https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
  Optional: -Customers 2000 (default) -Catalog workspace (default)
  First run: installs the Databricks CLI (winget) and opens the browser to log in (OAuth, no token to copy).
#>
param(
    [Parameter(Mandatory = $true)] [string] $Workspace,
    [int] $Customers = 2000,
    [string] $Catalog = "workspace"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$Workspace = $Workspace.TrimEnd("/")

if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) {
    Write-Host "Installing the Databricks CLI..." -ForegroundColor Cyan
    winget install --id Databricks.DatabricksCLI --silent --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) {
        throw "CLI installed: close and reopen the terminal, then run this script again."
    }
}
$env:DATABRICKS_HOST = $Workspace
# cmd /c keeps the "not logged in" message from stopping the script in Windows PowerShell 5.1
cmd /c "databricks current-user me >nul 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Opening the browser to log in to $Workspace ..." -ForegroundColor Cyan
    databricks auth login --host $Workspace
}

Write-Host "`n== 1/3 Validating the bundle ==" -ForegroundColor Cyan
databricks bundle validate --var "catalog=$Catalog" --var "customers=$Customers"
if ($LASTEXITCODE -ne 0) { throw "Bundle validation failed: send the message above." }
Write-Host "`n== 2/3 Deploying (code + job) ==" -ForegroundColor Cyan
databricks bundle deploy --var "catalog=$Catalog" --var "customers=$Customers"
if ($LASTEXITCODE -ne 0) { throw "Deployment failed: send the message above." }
Write-Host "`n== 3/3 Running the pipeline (a few minutes) ==" -ForegroundColor Cyan
databricks bundle run nestegg_lakehouse --var "catalog=$Catalog" --var "customers=$Customers"
if ($LASTEXITCODE -ne 0) { throw "A task failed: open the run link above and send the error." }
Write-Host "`nDone. In Databricks: Catalog > $Catalog > nestegg, then try databricks\sql\demo_queries.sql in the SQL editor." -ForegroundColor Green
