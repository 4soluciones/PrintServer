#!/bin/bash
# Sistema de Impresion - Reiniciar (Linux)
if [ "$EUID" -ne 0 ]; then
    echo "Ejecuta con: sudo bash reiniciar.sh"
    exit 1
fi
echo "Reiniciando el sistema de impresion (aplica cambios de config.ini o del codigo)..."
systemctl restart printserver
echo "Listo."
systemctl status printserver --no-pager -l | head -10
