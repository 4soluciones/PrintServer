@echo off
chcp 65001 >nul
title Sistema de Impresion - Reiniciar

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Se necesitan permisos de administrador. Solicitando...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "INSTALL_DIR=C:\PrintServer"
set "SOURCE_DIR=%~dp0"

if /I not "%SOURCE_DIR%"=="%INSTALL_DIR%\" (
    echo Copiando cambios desde "%SOURCE_DIR%" hacia "%INSTALL_DIR%"...
    if not exist "%INSTALL_DIR%" (
        echo.
        echo ERROR: no se encontro %INSTALL_DIR%. Ejecuta instalar.bat primero.
        echo.
        pause
        exit /b 1
    )
    copy /Y "%SOURCE_DIR%config.ini" "%INSTALL_DIR%\config.ini" >nul
    copy /Y "%SOURCE_DIR%websocket_client.py" "%INSTALL_DIR%\websocket_client.py" >nul
    copy /Y "%SOURCE_DIR%requirements.txt" "%INSTALL_DIR%\requirements.txt" >nul
    echo Listo.
)

echo Reiniciando el sistema de impresion (aplica cambios de config.ini o del codigo)...
rem IMPORTANTE: schtasks /End no mata el proceso Python real dentro de WSL2,
rem solo el lanzador de Windows. Hay que matarlo explicitamente o el
rem proceso viejo (con el codigo viejo en memoria) sigue corriendo.
wsl.exe -d PrintServerLinux -u root -- pkill -f websocket_client.py >nul 2>&1
schtasks /End /TN "PrintServer" >nul 2>&1
timeout /t 2 /nobreak >nul
schtasks /Run /TN "PrintServer"
if %errorLevel% equ 0 (
    echo.
    echo Listo. El sistema se reinicio correctamente.
) else (
    echo.
    echo No se encontro la tarea "PrintServer". Ejecuta instalar.bat primero.
)
echo.
pause
