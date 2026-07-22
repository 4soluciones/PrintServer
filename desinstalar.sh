#!/bin/bash
# Sistema de Impresion - Desinstalador (Linux)
if [ "$EUID" -ne 0 ]; then
    echo "Ejecuta con: sudo bash desinstalar.sh"
    exit 1
fi

INSTALL_DIR="/opt/printserver"

echo "==> Deteniendo y quitando el servicio..."
systemctl stop printserver 2>/dev/null || true
systemctl disable printserver 2>/dev/null || true
rm -f /etc/systemd/system/printserver.service
systemctl daemon-reload
echo "    OK"

echo "==> Quitando el chequeo automatico de actualizaciones (cron)..."
if command -v crontab >/dev/null 2>&1; then
    ( crontab -l 2>/dev/null | grep -v "check_update.sh" ) | crontab -
fi
echo "    OK"

read -p "Deseas borrar tambien la carpeta $INSTALL_DIR (config, logs y codigo)? (s/N) " resp
if [[ "$resp" =~ ^[sS]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo "    Carpeta eliminada"
else
    echo "    Carpeta conservada en $INSTALL_DIR"
fi

echo ""
echo "Desinstalacion completa."
