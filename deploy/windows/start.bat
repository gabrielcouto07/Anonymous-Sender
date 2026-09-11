@echo off
REM Starts the Canal de Etica & Compliance in the foreground (for tests / troubleshooting).
REM For unattended operation use install_service_nssm.ps1 or install_task_scheduler.ps1.

cd /d "%~dp0..\.."

if not exist "venv\Scripts\python.exe" (
    echo [ERRO] Ambiente virtual nao encontrado. Execute primeiro:
    echo        powershell -ExecutionPolicy Bypass -File deploy\windows\setup.ps1
    exit /b 1
)

if not exist ".env" (
    echo [ERRO] Arquivo .env nao encontrado. Execute setup.ps1 e configure o SMTP.
    exit /b 1
)

echo Iniciando servidor... pressione Ctrl+C para encerrar.
"venv\Scripts\python.exe" serve.py
