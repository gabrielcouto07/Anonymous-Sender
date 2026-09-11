<#
.SYNOPSIS
    One-shot setup for the Canal de Etica & Compliance on a Windows VM.

.DESCRIPTION
    1. Creates the Python virtual environment (.\venv)
    2. Installs the dependencies from requirements.txt
    3. Creates .env from .env.example when it does not exist yet
    4. Optionally opens the backend TCP port in Windows Firewall (-Firewall).
       Do not use this option in the recommended IIS/HTTPS deployment.

    Run from an elevated PowerShell (Run as Administrator) when using -Firewall:
        powershell -ExecutionPolicy Bypass -File deploy\windows\setup.ps1 -Firewall

    This file is intentionally ASCII-only so Windows PowerShell 5.1 reads it
    correctly regardless of code page.
#>
[CmdletBinding()]
param(
    [switch]$Firewall,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

Write-Host "== Canal de Etica & Compliance :: setup ==" -ForegroundColor Cyan
Write-Host "Pasta da aplicacao: $Root"

# --- 1. Locate Python --------------------------------------------------------
if (-not $PythonExe) {
    $candidates = @("py -3", "python", "python3")
    foreach ($c in $candidates) {
        try {
            $v = & cmd /c "$c --version 2>&1"
            if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(9|1[0-9])") { $PythonExe = $c; break }
        } catch { }
    }
}
if (-not $PythonExe) {
    throw "Python 3.9+ nao encontrado. Instale a partir de https://www.python.org/downloads/windows/ (marque 'Add python.exe to PATH')."
}
Write-Host "Python encontrado: $PythonExe"

# --- 2. Virtual environment --------------------------------------------------
$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "Criando ambiente virtual em .\venv ..."
    & cmd /c "$PythonExe -m venv `"$Root\venv`""
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o venv." }
} else {
    Write-Host "Ambiente virtual ja existe (.\venv)."
}

# --- 3. Dependencies ---------------------------------------------------------
Write-Host "Instalando dependencias (flask, waitress, tzdata) ..."
& $VenvPython -m pip install --upgrade pip setuptools --quiet --disable-pip-version-check
& $VenvPython -m pip install -r (Join-Path $Root "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependencias (a VM tem acesso ao PyPI ou a um proxy?)." }

# --- 4. .env -----------------------------------------------------------------
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host ""
    Write-Host "ATENCAO: .env criado a partir de .env.example." -ForegroundColor Yellow
    Write-Host "         Abra o arquivo e preencha SMTP_PASSWORD antes de iniciar o servico." -ForegroundColor Yellow
} else {
    Write-Host ".env ja existe - mantido."
}

# --- 5. Logs folder ----------------------------------------------------------
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

# --- 6. Firewall (optional) --------------------------------------------------
function Get-ConfiguredPort {
    $port = 8980
    if (Test-Path $EnvFile) {
        $line = Get-Content $EnvFile | Where-Object { $_ -match '^\s*PORT\s*=' } | Select-Object -Last 1
        if ($line) {
            $raw = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
            if ($raw -match '^\d+$') { $port = [int]$raw }
        }
    }
    return $port
}

if ($Firewall) {
    $port = Get-ConfiguredPort
    $ruleName = "Canal Compliance (TCP $port)"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Regra de firewall '$ruleName' ja existe."
    } else {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Domain,Private | Out-Null
        Write-Host "Regra de firewall criada: $ruleName (entrada, perfis Dominio/Privado)."
    }
}

Write-Host ""
Write-Host "Setup concluido." -ForegroundColor Green
Write-Host "Proximos passos:"
Write-Host "  1) Edite .env e informe SMTP_PASSWORD"
Write-Host "  2) Teste o SMTP:      venv\Scripts\python.exe app.py --test-email"
Write-Host "  3) Rode manualmente:  deploy\windows\start.bat"
Write-Host "  4) Configure o proxy HTTPS (README, secao 4)"
Write-Host "  5) Instale o servico: powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1"
