@echo off
chcp 65001 >nul
title Sistema de Impresion - Logs en tiempo real

if not exist "%~dp0logs" mkdir "%~dp0logs"
if not exist "%~dp0logs\printer.log" type nul > "%~dp0logs\printer.log"

echo ============================================================
echo  Mostrando logs en tiempo real de: %~dp0logs\printer.log
echo  (deja esta ventana abierta; se actualiza sola)
echo  Cierra esta ventana para salir.
echo ============================================================
echo.

powershell -NoProfile -NoExit -Command "Get-Content -Path '%~dp0logs\printer.log' -Wait -Tail 80 -Encoding UTF8"
