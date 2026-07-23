# ============================================================
# Sistema de Impresion - Instalador para Windows (motor WSL2)
# ============================================================
# Se ejecuta con permisos de administrador (invocado por instalar.bat)
# Es seguro volver a ejecutarlo: cada paso verifica si ya esta hecho.

$ErrorActionPreference = "Stop"
$InstallDir   = "C:\PrintServer"
$DistroName   = "PrintServerLinux"
$TaskName     = "PrintServer"
$RootfsUrl    = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current/ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz"
$SourceDir    = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg) {
    Write-Host "    OK: $msg" -ForegroundColor Green
}
function Write-Warn2($msg) {
    Write-Host "    AVISO: $msg" -ForegroundColor Yellow
}
function Fail($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    Write-Host "La instalacion no se pudo completar. Corrige el problema y vuelve a ejecutar instalar.bat" -ForegroundColor Red
    exit 1
}

Write-Host "============================================================"
Write-Host " SISTEMA DE IMPRESION - Instalador (Windows)"
Write-Host "============================================================"

# ------------------------------------------------------------
# 1) Verificar version de Windows (WSL2 requiere build 18362+)
# ------------------------------------------------------------
Write-Step "Verificando compatibilidad de Windows con WSL2..."
$build = [System.Environment]::OSVersion.Version.Build
if ($build -lt 18362) {
    Fail "Tu version de Windows (build $build) es muy antigua para WSL2. Se requiere Windows 10 build 18362 o superior (Windows 10 2004+, LTSC 2021, o Windows 11). Si tienes Windows 10 LTSC 2019, no es compatible sin actualizar."
}
Write-Ok "Windows build $build es compatible"

# ------------------------------------------------------------
# 2) Verificar / habilitar caracteristicas de Windows para WSL2
# ------------------------------------------------------------
Write-Step "Verificando funciones de Windows necesarias (WSL2)..."
$needsReboot = $false
$feats = @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")
foreach ($f in $feats) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
    if ($state -ne "Enabled") {
        Write-Warn2 "Habilitando funcion de Windows: $f (puede requerir reinicio)"
        $res = Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart
        if ($res.RestartNeeded) { $needsReboot = $true }
    } else {
        Write-Ok "$f ya esta habilitado"
    }
}
if ($needsReboot) {
    Write-Host ""
    Write-Host "Se activaron funciones de Windows que requieren REINICIAR el equipo." -ForegroundColor Yellow
    Write-Host "Reinicia el PC y vuelve a ejecutar instalar.bat para continuar." -ForegroundColor Yellow
    exit 0
}

# Asegurar que WSL2 sea la version por defecto
wsl --set-default-version 2 *> $null

# ------------------------------------------------------------
# 3) Validar config.ini antes de seguir (evita instalar con datos de plantilla)
# ------------------------------------------------------------
Write-Step "Validando config.ini..."
$configSrc = Join-Path $SourceDir "config.ini"
if (-not (Test-Path $configSrc)) { Fail "No se encontro config.ini en $SourceDir" }
# Solo evaluar lineas de valores reales (ignorar comentarios ; o # y lineas vacias/encabezados)
$valueLines = Get-Content $configSrc | Where-Object {
    $_ -notmatch '^\s*[;#]' -and $_ -match '='
}
$valueText = $valueLines -join "`n"
if ($valueText -match "TU_API_KEY_AQUI" -or $valueText -match "DEVICE_ID_AQUI" -or $valueText -match "192\.168\.1\.XXX") {
    Fail "config.ini todavia tiene valores de plantilla sin completar (api_key, device_id o url). Editalo antes de instalar."
}
Write-Ok "config.ini luce configurado"

# ------------------------------------------------------------
# 4) Importar la distro Linux dedicada (si no existe ya)
# ------------------------------------------------------------
Write-Step "Verificando distribucion Linux interna ($DistroName)..."
$existing = (wsl -l -q 2>$null) -replace "`0", ""
if ($existing -notcontains $DistroName) {
    New-Item -ItemType Directory -Force -Path "$InstallDir\wsl-distro" | Out-Null
    $tarPath = "$env:TEMP\ubuntu-printserver-rootfs.tar.gz"
    $minValidSizeBytes = 300MB
    if ((Test-Path $tarPath) -and (Get-Item $tarPath).Length -lt $minValidSizeBytes) {
        Write-Warn2 "Se encontro una descarga previa incompleta/corrupta, se descarta y se vuelve a descargar"
        Remove-Item $tarPath -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path $tarPath)) {
        Write-Step "Descargando base de Linux (Ubuntu 24.04, ~357MB, solo la primera vez)..."
        try {
            Invoke-WebRequest -Uri $RootfsUrl -OutFile $tarPath -UseBasicParsing
        } catch {
            Remove-Item $tarPath -Force -ErrorAction SilentlyContinue
            Fail "No se pudo descargar la base de Linux. Verifica tu conexion a internet. Detalle: $_"
        }
        if ((Get-Item $tarPath).Length -lt $minValidSizeBytes) {
            Remove-Item $tarPath -Force -ErrorAction SilentlyContinue
            Fail "La descarga de la base de Linux quedo incompleta. Verifica tu conexion e intenta de nuevo."
        }
    }
    Write-Step "Importando distribucion Linux dedicada..."
    wsl --import $DistroName "$InstallDir\wsl-distro" $tarPath --version 2
    if ($LASTEXITCODE -ne 0) { Fail "wsl --import fallo (codigo $LASTEXITCODE)" }
    Write-Ok "Distribucion '$DistroName' creada"
} else {
    Write-Ok "Distribucion '$DistroName' ya existe, se reutiliza"
}

# Configurar usuario root por defecto (evita configuracion interactiva)
$wslConf = "[user]`ndefault=root`n[boot]`nsystemd=false`n"
wsl -d $DistroName -- sh -c "printf '$wslConf' > /etc/wsl.conf"
wsl --terminate $DistroName *> $null

# ------------------------------------------------------------
# 5) Instalar Python y dependencias dentro de la distro
# ------------------------------------------------------------
Write-Step "Instalando Python y dependencias dentro de Linux (puede tardar unos minutos)..."
$checkPip = wsl -d $DistroName -- sh -c "command -v pip3" 2>$null
if (-not $checkPip) {
    wsl -d $DistroName -- sh -c "apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv > /dev/null"
    if ($LASTEXITCODE -ne 0) { Fail "No se pudo instalar Python/pip dentro de Linux" }
}
Write-Ok "Python3 y pip3 disponibles en la distribucion"

# ------------------------------------------------------------
# 6) Copiar archivos del sistema a la carpeta de instalacion
# ------------------------------------------------------------
Write-Step "Copiando archivos a $InstallDir..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null
Copy-Item -Path (Join-Path $SourceDir "websocket_client.py") -Destination $InstallDir -Force
Copy-Item -Path (Join-Path $SourceDir "requirements.txt") -Destination $InstallDir -Force
$configDst = Join-Path $InstallDir "config.ini"
if (-not (Test-Path $configDst)) {
    Copy-Item -Path $configSrc -Destination $configDst -Force
} else {
    Write-Warn2 "Ya existe config.ini en $InstallDir, se conserva el existente (no se sobreescribe)"
}
Copy-Item -Path (Join-Path $SourceDir "reiniciar.bat")    -Destination $InstallDir -Force -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $SourceDir "desinstalar.bat")  -Destination $InstallDir -Force -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $SourceDir "ver_logs.bat")     -Destination $InstallDir -Force -ErrorAction SilentlyContinue
Copy-Item -Path $PSScriptRoot -Destination $InstallDir -Recurse -Force
Write-Ok "Archivos copiados"

# ------------------------------------------------------------
# 7) Instalar dependencias de Python (pip) — via mount /mnt/c
# ------------------------------------------------------------
Write-Step "Instalando librerias de Python (websockets, python-escpos, etc.)..."
wsl -d $DistroName -- sh -c "cd /mnt/c/PrintServer && pip3 install --break-system-packages -q -r requirements.txt 2>/dev/null || pip3 install -q -r requirements.txt"
if ($LASTEXITCODE -ne 0) { Fail "No se pudieron instalar las dependencias de Python" }
Write-Ok "Dependencias instaladas"

# ------------------------------------------------------------
# 8) Crear tarea programada (arranque automatico + reinicio si falla)
# ------------------------------------------------------------
Write-Step "Configurando arranque automatico..."
# IMPORTANTE: WSL2 no funciona de forma confiable invocado desde la cuenta
# SYSTEM (no tiene sesion de usuario real). Se usa S4U con el usuario actual:
# corre sin sesion iniciada y sin necesitar guardar la contraseña.
$currentUser = "$env:COMPUTERNAME\$env:USERNAME"
# Register-ScheduledTask -Force reemplaza la tarea si ya existia, no hace falta borrarla antes.
# Se envuelve en powershell -WindowStyle Hidden para que nunca aparezca
# ninguna ventana de consola visible en la PC (debe correr en silencio).
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -Command `"wsl.exe -d $DistroName -u root -- python3 /mnt/c/PrintServer/websocket_client.py`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Days 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Ok "Tarea programada '$TaskName' creada (usuario: $currentUser, arranca con Windows, se auto-reinicia si falla)"

# ------------------------------------------------------------
# 8b) Tarea de auto-actualizacion (revisa GitHub periodicamente)
# ------------------------------------------------------------
Write-Step "Configurando chequeo de actualizaciones..."
$updaterTaskName = "PrintServerUpdater"
$checkInterval = 30
$configForInterval = Get-Content $configDst -Raw
if ($configForInterval -match 'check_interval_minutes\s*=\s*(\d+)') {
    $checkInterval = [int]$matches[1]
}
$updaterAction  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\_scripts\check_update.ps1`""
$updaterTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $checkInterval) -RepetitionDuration (New-TimeSpan -Days 3650)
$updaterSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$updaterPrincipal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName $updaterTaskName -Action $updaterAction -Trigger $updaterTrigger -Settings $updaterSettings -Principal $updaterPrincipal -Force | Out-Null
Write-Ok "Tarea '$updaterTaskName' creada (revisa GitHub cada $checkInterval minutos, respeta auto_update=no en config.ini)"

# ------------------------------------------------------------
# 9) Acceso directo en el escritorio para ver logs
# ------------------------------------------------------------
Write-Step "Creando acceso directo para ver logs en tiempo real..."
$WshShell = New-Object -ComObject WScript.Shell
$shortcut = $WshShell.CreateShortcut("$env:PUBLIC\Desktop\Ver Logs - Sistema Impresion.lnk")
$shortcut.TargetPath = "$InstallDir\ver_logs.bat"
$shortcut.WorkingDirectory = $InstallDir
$shortcut.IconLocation = "imageres.dll,109"
$shortcut.Description = "Ver logs del sistema de impresion en tiempo real"
$shortcut.Save()
Write-Ok "Acceso directo creado en el escritorio: 'Ver Logs - Sistema Impresion'"

# ------------------------------------------------------------
# 10) Iniciar el servicio ahora mismo (sin esperar reinicio)
# ------------------------------------------------------------
Write-Step "Iniciando el sistema de impresion..."
# Si esto es una reinstalacion/actualizacion, mata cualquier proceso viejo
# dentro de WSL antes de arrancar (schtasks/Stop-ScheduledTask no lo hace).
wsl.exe -d $DistroName -u root -- pkill -f websocket_client.py 2>$null
Start-Sleep -Seconds 1
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
Write-Ok "Sistema iniciado"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " INSTALACION COMPLETADA" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Carpeta de instalacion : $InstallDir"
Write-Host "  Config editable en     : $InstallDir\config.ini"
Write-Host "  Ver logs en vivo       : doble clic en 'Ver Logs - Sistema Impresion' (escritorio)"
Write-Host "                           o ejecuta $InstallDir\ver_logs.bat"
Write-Host "  Si editas config.ini   : ejecuta $InstallDir\reiniciar.bat despues"
Write-Host "  Para desinstalar       : ejecuta $InstallDir\desinstalar.bat"
Write-Host ""
