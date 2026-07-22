# ============================================================
# Sistema de Impresion - Desinstalador para Windows
# ============================================================
$InstallDir = "C:\PrintServer"
$DistroName = "PrintServerLinux"
$TaskName   = "PrintServer"
$UpdaterTaskName = "PrintServerUpdater"

Write-Host "============================================================"
Write-Host " SISTEMA DE IMPRESION - Desinstalador"
Write-Host "============================================================"
Write-Host ""

Write-Host "==> Deteniendo y eliminando las tareas programadas..." -ForegroundColor Cyan
foreach ($tn in @($TaskName, $UpdaterTaskName)) {
    $existingTask = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
    if ($existingTask) {
        Stop-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue
    }
}
Write-Host "    OK" -ForegroundColor Green

Write-Host "==> Eliminando la distribucion Linux interna..." -ForegroundColor Cyan
wsl --terminate $DistroName *> $null
wsl --unregister $DistroName *> $null
Write-Host "    OK" -ForegroundColor Green

Write-Host "==> Eliminando accesos directos..." -ForegroundColor Cyan
Remove-Item "$env:PUBLIC\Desktop\Ver Logs - Sistema Impresion.lnk" -ErrorAction SilentlyContinue
Write-Host "    OK" -ForegroundColor Green

$resp = Read-Host "Deseas borrar tambien la carpeta $InstallDir (config, logs y codigo)? (S/N)"
if ($resp -match '^[sS]') {
    Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    Carpeta eliminada" -ForegroundColor Green
} else {
    Write-Host "    Carpeta conservada en $InstallDir (puedes borrarla manualmente despues)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Desinstalacion completa." -ForegroundColor Green
