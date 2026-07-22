#!/bin/bash
# Sistema de Impresion - Ver logs en tiempo real (Linux)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$DIR/logs"
touch "$DIR/logs/printer.log"
echo "============================================================"
echo " Mostrando logs en tiempo real de: $DIR/logs/printer.log"
echo " Presiona Ctrl+C para salir."
echo "============================================================"
echo ""
tail -n 80 -f "$DIR/logs/printer.log"
