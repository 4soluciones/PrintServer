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
    rem Sincroniza todo el proyecto (codigo, scripts .bat/.sh, _scripts\*.ps1, etc.)
    rem EXCEPTO: .git/.idea (no pertenecen al destino), logs (no mezclar con los
    rem del destino) y config.ini (credenciales propias de cada instalacion;
    rem nunca se debe pisar con la copia de desarrollo).
    robocopy "%SOURCE_DIR%." "%INSTALL_DIR%" /E /XD .git .idea logs /XF config.ini /NFL /NDL /NJH /NJS
    if %errorLevel% GEQ 8 (
        echo.
        echo ERROR: fallo al copiar archivos ^(robocopy codigo %errorLevel%^).
        pause
        exit /b 1
    )
    echo Listo.
)

echo Reiniciando el sistema de impresion (aplica cambios del codigo/scripts; config.ini no se toca)...
rem IMPORTANTE: schtasks /End no mata el proceso Python real dentro de WSL2,
rem solo el lanzador de Windows. Hay que matarlo explicitamente o el
rem proceso viejo (con el codigo viejo en memoria) sigue corriendo y puede
rem quedar unos segundos conectado en paralelo al nuevo (misma sesion en el
rem servidor), imprimiendo con el formato antiguo. Por eso se fuerza con -9
rem y se espera activamente a que el proceso realmente muera antes de
rem lanzar el nuevo, en vez de un timeout fijo que no lo garantiza.
wsl.exe -d PrintServerLinux -u root -- pkill -9 -f websocket_client.py >nul 2>&1
schtasks /End /TN "PrintServer" >nul 2>&1

set "INTENTOS=0"
:esperar_muerte
wsl.exe -d PrintServerLinux -u root -- pgrep -f websocket_client.py >nul 2>&1
if %errorLevel% equ 0 (
    set /a INTENTOS+=1
    if %INTENTOS% GEQ 10 (
        echo ADVERTENCIA: el proceso anterior no murio tras varios intentos, continuando de todas formas...
        goto seguir_reinicio
    )
    timeout /t 1 /nobreak >nul
    goto esperar_muerte
)

:seguir_reinicio
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
