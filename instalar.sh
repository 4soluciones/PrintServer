#!/bin/bash
# ============================================================
# Sistema de Impresion - Instalador para Linux
# ============================================================
# Uso: sudo bash instalar.sh
set -e

INSTALL_DIR="/opt/printserver"
SERVICE_NAME="printserver"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step()  { echo -e "\n==> $1"; }
ok()    { echo "    OK: $1"; }
fail()  { echo -e "\nERROR: $1"; exit 1; }

echo "============================================================"
echo " SISTEMA DE IMPRESION - Instalador (Linux)"
echo "============================================================"

if [ "$EUID" -ne 0 ]; then
    fail "Este instalador necesita permisos de administrador. Ejecuta: sudo bash instalar.sh"
fi

# ------------------------------------------------------------
# 1) Validar config.ini antes de seguir
# ------------------------------------------------------------
step "Validando config.ini..."
CONFIG_SRC="$SOURCE_DIR/config.ini"
[ -f "$CONFIG_SRC" ] || fail "No se encontro config.ini en $SOURCE_DIR"
# Solo evaluar lineas de valores reales (ignorar comentarios ; # y lineas sin '=')
if grep -v '^\s*[;#]' "$CONFIG_SRC" | grep '=' | grep -q "TU_API_KEY_AQUI\|DEVICE_ID_AQUI\|192\.168\.1\.XXX"; then
    fail "config.ini todavia tiene valores de plantilla sin completar (api_key, device_id o url). Editalo antes de instalar."
fi
ok "config.ini luce configurado"

# ------------------------------------------------------------
# 2) Python y dependencias del sistema
# ------------------------------------------------------------
step "Verificando Python3, pip y venv..."
# Siempre se ejecuta (no solo si falta python3): python3 puede estar presente
# pero pip/venv ser paquetes separados sin instalar. apt/dnf/yum son idempotentes,
# no reinstalan lo que ya esta.
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip
elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip
elif ! command -v python3 >/dev/null 2>&1; then
    fail "No se encontro un gestor de paquetes compatible (apt/dnf/yum) y python3 no esta instalado. Instalalo manualmente."
fi
command -v python3 >/dev/null 2>&1 || fail "No se pudo instalar Python3"
ok "Python3 disponible: $(python3 --version)"

# ------------------------------------------------------------
# 3) Copiar archivos a la carpeta de instalacion
# ------------------------------------------------------------
step "Copiando archivos a $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/logs"
cp "$SOURCE_DIR/websocket_client.py" "$INSTALL_DIR/"
cp "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/"
if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    cp "$CONFIG_SRC" "$INSTALL_DIR/"
else
    echo "    AVISO: ya existe config.ini en $INSTALL_DIR, se conserva el existente"
fi
cp "$SOURCE_DIR/reiniciar.sh" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SOURCE_DIR/desinstalar.sh" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SOURCE_DIR/ver_logs.sh" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SOURCE_DIR/check_update.sh" "$INSTALL_DIR/" 2>/dev/null || true
chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true
ok "Archivos copiados"

# ------------------------------------------------------------
# 4) Dependencias de Python (entorno virtual dedicado)
# ------------------------------------------------------------
step "Instalando librerias de Python (puede tardar un poco)..."
python3 -m venv "$INSTALL_DIR/venv" --clear
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
ok "Dependencias instaladas"

# ------------------------------------------------------------
# 5) Servicio systemd (arranque automatico + reinicio si falla)
# ------------------------------------------------------------
step "Configurando arranque automatico (systemd)..."
if command -v systemctl >/dev/null 2>&1; then
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Sistema de Impresion
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/websocket_client.py
WorkingDirectory=$INSTALL_DIR
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    systemctl restart "$SERVICE_NAME"
    ok "Servicio '$SERVICE_NAME' creado y arrancado (systemctl status $SERVICE_NAME)"
else
    fail "Este sistema no tiene systemd. Contacta a soporte para un metodo de arranque alternativo (cron @reboot)."
fi

# ------------------------------------------------------------
# 6) Chequeo automatico de actualizaciones (cron)
# ------------------------------------------------------------
step "Configurando chequeo de actualizaciones..."
CHECK_INTERVAL=$(grep -A5 '^\[updates\]' "$INSTALL_DIR/config.ini" | grep 'check_interval_minutes' | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
[ -z "$CHECK_INTERVAL" ] && CHECK_INTERVAL=30
if command -v crontab >/dev/null 2>&1; then
    ( crontab -l 2>/dev/null | grep -v "check_update.sh" ; echo "*/$CHECK_INTERVAL * * * * bash $INSTALL_DIR/check_update.sh" ) | crontab -
    ok "Cron configurado (revisa GitHub cada $CHECK_INTERVAL minutos, respeta auto_update=no en config.ini)"
else
    echo "    AVISO: no se encontro 'crontab', configura manualmente check_update.sh si quieres auto-actualizacion"
fi

echo ""
echo "============================================================"
echo " INSTALACION COMPLETADA"
echo "============================================================"
echo ""
echo "  Carpeta de instalacion : $INSTALL_DIR"
echo "  Config editable en     : $INSTALL_DIR/config.ini"
echo "  Ver logs en vivo       : bash $INSTALL_DIR/ver_logs.sh"
echo "  Si editas config.ini   : bash $INSTALL_DIR/reiniciar.sh despues"
echo "  Para desinstalar       : sudo bash $INSTALL_DIR/desinstalar.sh"
echo ""
