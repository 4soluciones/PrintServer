# ============================================================
# Sistema de Impresion - Chequeo de actualizaciones (Windows)
# ============================================================
# Se ejecuta periodicamente via tarea programada "PrintServerUpdater".
# Si la sucursal tiene auto_update=no en config.ini, no hace nada.

$InstallDir  = "C:\PrintServer"
$ConfigFile  = "$InstallDir\config.ini"
$TargetFile  = "$InstallDir\websocket_client.py"
$TaskName    = "PrintServer"
$LogDir      = "$InstallDir\logs"
$LogFile     = "$LogDir\updater.log"
$MinValidBytes = 10KB

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [UPDATER] $msg"
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

if (-not (Test-Path $ConfigFile)) { exit 0 }

# --- Parser simple de INI (solo lo que necesitamos) ---
$ini = @{}
$section = ""
foreach ($rawLine in Get-Content $ConfigFile) {
    $l = $rawLine.Trim()
    if ($l -match '^\[(.+)\]$') { $section = $matches[1]; continue }
    if ($l -match '^\s*[;#]' -or $l -eq '') { continue }
    if ($l -match '^([^=]+)=(.*)$') {
        $key = $matches[1].Trim()
        $val = $matches[2].Trim()
        $ini["$section.$key"] = $val
    }
}

$autoUpdate = $ini["updates.auto_update"]
if ($autoUpdate -notmatch '^(si|s|yes|true|1)$') {
    exit 0  # Sucursal con codigo personalizado: auto_update desactivado.
}

$sourceUrl = $ini["updates.source_url"]
if ([string]::IsNullOrWhiteSpace($sourceUrl) -or $sourceUrl -match "TU_USUARIO") {
    exit 0  # source_url no configurada todavia.
}

if (-not (Test-Path $TargetFile)) { exit 0 }

$tempFile = "$env:TEMP\websocket_client_latest.py"
try {
    Invoke-WebRequest -Uri $sourceUrl -OutFile $tempFile -UseBasicParsing -TimeoutSec 30
} catch {
    Log "No se pudo descargar la actualizacion: $_"
    exit 0
}

if ((Get-Item $tempFile).Length -lt $MinValidBytes) {
    Log "Descarga sospechosamente pequena, se descarta (posible error de GitHub/red)"
    Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    exit 0
}

$remoteHash = (Get-FileHash -Path $tempFile -Algorithm SHA256).Hash
$localHash  = (Get-FileHash -Path $TargetFile -Algorithm SHA256).Hash

if ($remoteHash -eq $localHash) {
    Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    exit 0  # Sin cambios
}

Log "Nueva version detectada. Actualizando websocket_client.py..."

$backupDir = "$InstallDir\backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item $TargetFile "$backupDir\websocket_client_$(Get-Date -Format 'yyyyMMdd_HHmmss').py" -Force

Copy-Item $tempFile $TargetFile -Force
Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
Log "Archivo actualizado correctamente."

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
    Log "Sistema de impresion reiniciado con la nueva version."
}
