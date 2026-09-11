<#
.SYNOPSIS
    Installs (or removes) the Canal de Etica & Compliance as a real Windows
    Service using NSSM (Non-Sucking Service Manager).

.DESCRIPTION
    Why NSSM: Python scripts cannot register themselves as Windows services.
    NSSM wraps any executable as a service with automatic restart on failure,
    start at boot (before anyone logs in) and stdout/stderr capture to a file.
    It is the standard way to run Python/Node apps as services on Windows.

    Requirements:
      * Run from an ELEVATED PowerShell (Run as Administrator).
      * nssm.exe available in one of:
          - deploy\windows\tools\nssm.exe   (recommended: download from https://nssm.cc/download,
                                             use win64\nssm.exe from the zip)
          - anywhere in PATH
          - the path given with -NssmPath
      * setup.ps1 already executed (venv exists) and .env filled in.

    Examples:
        powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1 -Uninstall
        powershell -ExecutionPolicy Bypass -File deploy\windows\install_service_nssm.ps1 -NssmPath C:\Tools\nssm.exe

    Manage afterwards with the usual tools:
        sc.exe query CanalCompliance | Restart-Service CanalCompliance | services.msc
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "CanalCompliance",
    [string]$NssmPath = "",
    [switch]$Uninstall,
    [switch]$Firewall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

# --- Elevation check ----------------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Execute este script em um PowerShell elevado (Executar como administrador)."
}

# --- Locate nssm.exe ----------------------------------------------------------
function Find-Nssm {
    param([string]$Hint)
    if ($Hint -and (Test-Path $Hint)) { return (Resolve-Path $Hint).Path }
    $local = Join-Path $PSScriptRoot "tools\nssm.exe"
    if (Test-Path $local) { return $local }
    $cmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$Nssm = Find-Nssm -Hint $NssmPath
if (-not $Nssm) {
    throw "nssm.exe nao encontrado. Baixe em https://nssm.cc/download e copie win64\nssm.exe para $PSScriptRoot\tools\nssm.exe"
}
Write-Host "NSSM: $Nssm"

# --- Uninstall ----------------------------------------------------------------
if ($Uninstall) {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Host "Servico '$ServiceName' nao existe. Nada a fazer."; exit 0 }
    if ($svc.Status -ne "Stopped") { & $Nssm stop $ServiceName | Out-Null }
    & $Nssm remove $ServiceName confirm | Out-Null
    Write-Host "Servico '$ServiceName' removido." -ForegroundColor Green
    exit 0
}

# --- Pre-flight ----------------------------------------------------------------
$Python = Join-Path $Root "venv\Scripts\python.exe"
$Entry = Join-Path $Root "serve.py"
if (-not (Test-Path $Python)) { throw "venv nao encontrado. Execute deploy\windows\setup.ps1 primeiro." }
if (-not (Test-Path (Join-Path $Root ".env"))) { throw ".env nao encontrado em $Root. Execute setup.ps1 e configure o arquivo antes de instalar." }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
$ServiceLog = Join-Path $Root "logs\service.log"

# Least privilege: the service can read the application and only write logs.
& icacls.exe $Root /grant:r '*S-1-5-19:(OI)(CI)(RX)' | Out-Null
& icacls.exe (Join-Path $Root "logs") /grant:r '*S-1-5-19:(OI)(CI)(M)' | Out-Null
& icacls.exe (Join-Path $Root ".env") /inheritance:r /grant:r '*S-1-5-32-544:(F)' '*S-1-5-18:(F)' '*S-1-5-19:(R)' | Out-Null

# --- Install / update ------------------------------------------------------------
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Servico '$ServiceName' ja existe - atualizando configuracao."
    if ($existing.Status -ne "Stopped") { & $Nssm stop $ServiceName | Out-Null }
} else {
    & $Nssm install $ServiceName $Python "`"$Entry`""
    if ($LASTEXITCODE -ne 0) { throw "nssm install falhou (codigo $LASTEXITCODE)." }
}

& $Nssm set $ServiceName Application      $Python                | Out-Null
& $Nssm set $ServiceName AppParameters    "`"$Entry`""           | Out-Null
& $Nssm set $ServiceName AppDirectory     $Root                  | Out-Null
& $Nssm set $ServiceName DisplayName      "Canal de Etica e Compliance (OEA)" | Out-Null
& $Nssm set $ServiceName Description      "Portal interno de relatos eticos / denuncias do Programa OEA. Encaminha relatos por e-mail ao Compliance sem registrar dados do relator." | Out-Null
& $Nssm set $ServiceName Start            SERVICE_AUTO_START     | Out-Null
& $Nssm set $ServiceName ObjectName       "NT AUTHORITY\LocalService" | Out-Null
# Restart automatically if the process dies; wait 5 s between attempts.
& $Nssm set $ServiceName AppExit          Default Restart        | Out-Null
& $Nssm set $ServiceName AppRestartDelay  5000                   | Out-Null
& $Nssm set $ServiceName AppThrottle      5000                   | Out-Null
# Capture stdout/stderr (operational log only - never contains report data).
& $Nssm set $ServiceName AppStdout        $ServiceLog            | Out-Null
& $Nssm set $ServiceName AppStderr        $ServiceLog            | Out-Null
& $Nssm set $ServiceName AppRotateFiles   1                      | Out-Null
& $Nssm set $ServiceName AppRotateOnline  1                      | Out-Null
& $Nssm set $ServiceName AppRotateBytes   1048576                | Out-Null
# Make Python flush prints immediately so the log is readable in real time.
& $Nssm set $ServiceName AppEnvironmentExtra "PYTHONUNBUFFERED=1" "PYTHONUTF8=1" "PYTHONDONTWRITEBYTECODE=1" | Out-Null

# --- Firewall ---------------------------------------------------------------------
if ($Firewall) {
    $port = 8980
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*PORT\s*=' } | Select-Object -Last 1
        if ($line) {
            $raw = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
            if ($raw -match '^\d+$') { $port = [int]$raw }
        }
    }
    $ruleName = "Canal Compliance (TCP $port)"
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Domain,Private | Out-Null
        Write-Host "Regra de firewall criada: $ruleName"
    }
}

# --- Start ------------------------------------------------------------------------
& $Nssm start $ServiceName | Out-Null
Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName
Write-Host ""
Write-Host "Servico '$ServiceName' -> $($svc.Status)" -ForegroundColor Green
Write-Host "Log do servico: $ServiceLog"
Write-Host "Log da aplicacao: $Root\logs\app.log"
Write-Host "Teste local do backend: http://127.0.0.1:8980/health"
Write-Host "Portal para usuarios:   https://srvapp01/"
