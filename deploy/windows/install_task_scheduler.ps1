<#
.SYNOPSIS
    Alternative to NSSM: keeps the Canal de Etica & Compliance running in the
    background via Windows Task Scheduler, with NO third-party binary.

.DESCRIPTION
    Creates a scheduled task that:
      * runs as LocalService (no user needs to be logged in),
      * starts at boot and is started immediately after registration,
      * restarts the process if it exits with an error,
      * has no execution time limit (runs forever).

    Trade-off vs NSSM: the Task Scheduler cannot capture stdout, so rely on
    logs\app.log (written by serve.py). Also 'Restart-Service' does not apply;
    use Stop-ScheduledTask / Start-ScheduledTask instead.

    Run from an ELEVATED PowerShell:
        powershell -ExecutionPolicy Bypass -File deploy\windows\install_task_scheduler.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\install_task_scheduler.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$TaskName = "CanalCompliance",
    [switch]$Uninstall,
    [switch]$Firewall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Execute este script em um PowerShell elevado (Executar como administrador)."
}

if ($Uninstall) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "Tarefa '$TaskName' nao existe. Nada a fazer."; exit 0 }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # The python process may survive Stop-ScheduledTask; kill it explicitly.
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*$Root\serve.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarefa '$TaskName' removida." -ForegroundColor Green
    exit 0
}

$Python = Join-Path $Root "venv\Scripts\python.exe"
$Entry = Join-Path $Root "serve.py"
if (-not (Test-Path $Python)) { throw "venv nao encontrado. Execute deploy\windows\setup.ps1 primeiro." }
if (-not (Test-Path (Join-Path $Root ".env"))) { throw ".env nao encontrado. Execute setup.ps1 e configure o arquivo primeiro." }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

# Least privilege: Local Service reads the app and writes only to ./logs.
& icacls.exe $Root /grant:r '*S-1-5-19:(OI)(CI)(RX)' | Out-Null
& icacls.exe (Join-Path $Root "logs") /grant:r '*S-1-5-19:(OI)(CI)(M)' | Out-Null
& icacls.exe (Join-Path $Root ".env") /inheritance:r /grant:r '*S-1-5-32-544:(F)' '*S-1-5-18:(F)' '*S-1-5-19:(R)' | Out-Null

$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Entry`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "S-1-5-19" -LogonType ServiceAccount -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal `
    -Description "Canal de Etica e Compliance (OEA) - servidor web interno. Inicia no boot e reinicia em caso de falha." -Force | Out-Null

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

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$info = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "Tarefa '$TaskName' registrada. Estado: $($info.State)" -ForegroundColor Green
Write-Host "Log da aplicacao: $Root\logs\app.log"
Write-Host "Parar / iniciar:  Stop-ScheduledTask -TaskName $TaskName  |  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Teste local do backend: http://127.0.0.1:8980/health"
Write-Host "Portal para usuarios:   https://srvapp01/"
