@echo off
chcp 65001 >nul
title Sistema de Impresion - Desinstalador

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Se necesitan permisos de administrador. Solicitando...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_scripts\desinstalar.ps1"

echo.
pause
