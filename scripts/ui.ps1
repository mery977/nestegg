<#
  Start the Nestegg chat interface on Windows:   .\scripts\ui.ps1
  With a real model:                             .\scripts\ui.ps1 -Provider anthropic   (or openai, azure, gemini, local)
  The key is asked once, hidden, and kept only in this terminal session. Close with Ctrl+C.
#>
param([ValidateSet("offline", "anthropic", "openai", "azure", "gemini", "local")] [string] $Provider = "offline")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:VIRTUAL_ENV) { . .venv\Scripts\Activate.ps1 }

function Read-Key([string] $name) {
    # Pasting into a hidden prompt fails in some terminals: copying the key and pressing Enter reads the clipboard.
    Write-Host "Copy your $name API key, then press Enter here (or paste it, then Enter)." -ForegroundColor Cyan
    $secure = Read-Host "Key" -AsSecureString
    $typed = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    $key = if ($typed -and $typed.Trim().Length -ge 20) { $typed } else { Get-Clipboard -Raw }
    $key = ("$key" -replace '[\x00-\x20]', '')
    if ($key.Length -lt 20) { throw "No valid key found: copy the whole key and run the script again." }
    Write-Host ("Key received: " + $key.Substring(0, 4) + "..." + $key.Substring($key.Length - 4)) -ForegroundColor Green
    return $key
}

$extra = "ui"
switch ($Provider) {
    "anthropic" {
        if (-not $env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY = Read-Key "Anthropic" }
        $extra = "ui,anthropic"
    }
    "openai" {
        if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY = Read-Key "OpenAI" }
        $extra = "ui,openai"
    }
    "gemini" {
        if (-not $env:GEMINI_API_KEY) { $env:GEMINI_API_KEY = Read-Key "Gemini" }
        $extra = "ui,gemini"
    }
    "azure" {
        if (-not $env:NESTEGG_AZURE_ENDPOINT) { $env:NESTEGG_AZURE_ENDPOINT = Read-Host "Azure OpenAI endpoint (https://<resource>.openai.azure.com)" }
        $extra = "ui,azure"
    }
    "local" {
        if (-not $env:NESTEGG_LOCAL_URL) { $env:NESTEGG_LOCAL_URL = "http://localhost:11434/v1" }
        $extra = "ui,local"
    }
}
$env:NESTEGG_PROVIDER = $Provider
pip install -q -e ".[$extra]"
streamlit run ui/app.py
