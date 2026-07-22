#!/bin/bash
# ============================================================
# Sistema de Impresion - Chequeo de actualizaciones (Linux)
# ============================================================
# Se ejecuta periodicamente via cron. Si la sucursal tiene
# auto_update=no en config.ini, no hace nada.

INSTALL_DIR="/opt/printserver"
CONFIG_FILE="$INSTALL_DIR/config.ini"
TARGET_FILE="$INSTALL_DIR/websocket_client.py"
SERVICE_NAME="printserver"
LOG_FILE="$INSTALL_DIR/logs/updater.log"
MIN_VALID_BYTES=10240

mkdir -p "$INSTALL_DIR/logs"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [UPDATER] $1" >> "$LOG_FILE"; }

[ -f "$CONFIG_FILE" ] || exit 0

get_ini_value() {
    # $1 = seccion, $2 = clave
    awk -F'=' -v section="[$1]" -v key="$2" '
        /^\[.*\]$/ { insection = ($0 == section); next }
        insection && $1 ~ "^[ \t]*"key"[ \t]*$" { gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit }
    ' "$CONFIG_FILE"
}

AUTO_UPDATE=$(get_ini_value "updates" "auto_update")
if ! echo "$AUTO_UPDATE" | grep -qiE '^(si|s|yes|true|1)$'; then
    exit 0
fi

SOURCE_URL=$(get_ini_value "updates" "source_url")
if [ -z "$SOURCE_URL" ] || echo "$SOURCE_URL" | grep -q "TU_USUARIO"; then
    exit 0
fi

[ -f "$TARGET_FILE" ] || exit 0

TMP_FILE="/tmp/websocket_client_latest.py"
if ! curl -fsSL --max-time 30 -o "$TMP_FILE" "$SOURCE_URL"; then
    log "No se pudo descargar la actualizacion"
    rm -f "$TMP_FILE"
    exit 0
fi

SIZE=$(stat -c%s "$TMP_FILE" 2>/dev/null || stat -f%z "$TMP_FILE" 2>/dev/null)
if [ -z "$SIZE" ] || [ "$SIZE" -lt "$MIN_VALID_BYTES" ]; then
    log "Descarga sospechosamente pequena, se descarta"
    rm -f "$TMP_FILE"
    exit 0
fi

REMOTE_HASH=$(sha256sum "$TMP_FILE" | awk '{print $1}')
LOCAL_HASH=$(sha256sum "$TARGET_FILE" | awk '{print $1}')

if [ "$REMOTE_HASH" = "$LOCAL_HASH" ]; then
    rm -f "$TMP_FILE"
    exit 0
fi

log "Nueva version detectada. Actualizando websocket_client.py..."
mkdir -p "$INSTALL_DIR/backups"
cp "$TARGET_FILE" "$INSTALL_DIR/backups/websocket_client_$(date +%Y%m%d_%H%M%S).py"

cp "$TMP_FILE" "$TARGET_FILE"
rm -f "$TMP_FILE"
log "Archivo actualizado correctamente."

if command -v systemctl >/dev/null 2>&1; then
    systemctl restart "$SERVICE_NAME"
    log "Sistema de impresion reiniciado con la nueva version."
fi
