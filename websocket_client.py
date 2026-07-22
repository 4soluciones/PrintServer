#!/usr/bin/env python3
"""
Cliente WebSocket - Sistema de Impresion para Restaurante
Versión multiplataforma: Windows, Linux, Raspberry Pi
"""

import asyncio
import websockets
import json
import warnings
import logging
import logging.handlers
import socket
import time
import uuid
import os
import sys
import base64
import configparser
from datetime import datetime
from typing import Optional, Dict, Any
from io import BytesIO
from escpos.printer import Network
from PIL import Image
import qrcode
import unicodedata
import random
import inspect
import ssl
import certifi
from urllib.parse import urlparse

# ========================================
# DIRECTORIO BASE — funciona en .py y .exe (PyInstaller)
# ========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========================================
# CARGA DE CONFIGURACIÓN DESDE config.ini
# ========================================
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

def _load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        cfg['server'] = {
            'url': 'ws://192.168.1.XXX:8000/ws/raspberry/',
            'api_key': 'TU_API_KEY_AQUI'
        }
        cfg['device'] = {
            'branch_id': '1',
            'device_id': 'DEVICE_ID_AQUI'
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            cfg.write(f)
        msg = (
            "CONFIGURACION REQUERIDA\n"
            f"Se creo el archivo: {CONFIG_FILE}\n\n"
            "Edita config.ini con tus datos y reinicia el programa.\n"
        )
        with open(os.path.join(BASE_DIR, 'CONFIGURA_AQUI.txt'), 'w', encoding='utf-8') as f:
            f.write(msg)
        print(msg)
        sys.exit(0)
    cfg.read(CONFIG_FILE, encoding='utf-8')
    return cfg

_cfg = _load_config()
SERVER_URL = _cfg.get('server', 'url').strip()
API_KEY    = _cfg.get('server', 'api_key').strip()
BRANCH_ID  = int(_cfg.get('device', 'branch_id').strip())
_DEVICE_ID = _cfg.get('device', 'device_id').strip()

# ========================================
# INSTANCIA ÚNICA — evita correr más de una vez
# ========================================
import socket as _sock
_instance_lock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
try:
    _instance_lock.bind(('127.0.0.1', 47291))
except OSError:
    print("Ya hay una instancia del sistema de impresion corriendo. Saliendo.")
    sys.exit(0)

# ========================================
# TIEMPOS DE CONEXIÓN
# ========================================
HEARTBEAT_BASE_INTERVAL = 25
CONNECTION_TIMEOUT = 120
RECONNECT_DELAY = 10 + random.randint(0, 15)
MAX_RECONNECT_ATTEMPTS = 0

# ========================================
# LOGGING — ruta multiplataforma
# ========================================
LOG_DIR  = os.path.join(BASE_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'printer.log')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        ),
        logging.StreamHandler()
    ],
    force=True
)
logger = logging.getLogger(__name__)
logging.getLogger('escpos').setLevel(logging.ERROR)
logging.getLogger('PIL').setLevel(logging.WARNING)

warnings.filterwarnings("ignore", message=".*media.width.pixel.*")

def _ws_handshake_origin(ws_url: str) -> str:
    """Origin HTTP(s) para el handshake WS."""
    p = urlparse(ws_url)
    scheme = "https" if p.scheme == "wss" else "http"
    return f"{scheme}://{p.netloc}"

logger.info("=" * 50)
logger.info("SISTEMA DE IMPRESION INICIADO")
logger.info(f"Config : {CONFIG_FILE}")
logger.info(f"Logs   : {LOG_FILE}")
logger.info(f"Server : {SERVER_URL}")
logger.info(f"Branch : {BRANCH_ID}")
logger.info("=" * 50)
# ========================================
# UTILIDADES PARA MANEJO DE TEXTO
# ========================================

def clean_text(text: str) -> str:
    """
    Limpia el texto para impresoras térmicas
    Remueve acentos y caracteres especiales problemáticos
    """
    if not text:
        return ""
    
    # Normalizar y remover acentos
    text = unicodedata.normalize('NFD', text)
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    
    # Reemplazar caracteres especiales
    replacements = {
        '€': 'EUR',
        '£': 'GBP',
        '¥': 'YEN',
        '°': 'o',
        'º': 'o',
        'ª': 'a',
        '™': 'TM',
        '®': '(R)',
        '©': '(C)',
        '½': '1/2',
        '¼': '1/4',
        '¾': '3/4',
        '×': 'x',
        '÷': '/',
        '±': '+/-',
        '≤': '<=',
        '≥': '>=',
        '≠': '!=',
        '∞': 'INF',
        '√': 'sqrt',
        'π': 'pi',
        'Ñ': 'N',
        'ñ': 'n',
    }
    
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Asegurar que solo quedan caracteres ASCII imprimibles
    text = ''.join(char if 32 <= ord(char) <= 126 else ' ' for char in text)
    
    return text

def center_text(text: str, width: int = 48) -> str:
    """Centra el texto en el ancho especificado"""
    text = clean_text(text)
    if len(text) >= width:
        return text[:width]
    padding = (width - len(text)) // 2
    return ' ' * padding + text

# ========================================
# CLIENTE DE IMPRESIÓN PRINCIPAL
# ========================================

class SmartPrinterClient:
    """
    Cliente inteligente que maneja toda la comunicación
    entre el servidor Django y las impresoras locales
    """
    
    def __init__(self):
        self.websocket = None
        self.device_id = self._get_device_id()
        self.mac_address = self._get_mac_address()
        self.device_info = {
            'device_id': self.device_id,
            'mac_address': self.mac_address,
            'hostname': socket.gethostname(),
            'ip': self._get_local_ip(),
            'branch_id': BRANCH_ID
        }
        self.printers_cache = {}
        self.running = True
        self.authenticated = False
        self.connection_attempts = 0
        self.last_ping_time = None  
        self.printers_with_beep = {} 
        
        # AGREGAR ESTOS LOGS PARA DEBUG
        logger.info("="*50)
        logger.info("INICIANDO CLIENTE DE IMPRESIÓN")
        logger.info(f"Device ID: {self.device_id}")
        logger.info(f"Branch ID: {BRANCH_ID}")
        logger.info(f"Log file: {LOG_FILE}")
        logger.info("="*50)
        
    def _get_device_id(self) -> str:
        """Retorna el Device ID configurado en config.ini"""
        return _DEVICE_ID

    def _get_mac_address(self) -> str:
        """Obtiene la dirección MAC del dispositivo — multiplataforma"""
        if sys.platform.startswith('linux'):
            try:
                with open('/sys/class/net/eth0/address', 'r') as f:
                    return f.read().strip()
            except:
                pass
        return ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff)
                        for i in range(0, 48, 8)][::-1])
    
    def _get_local_ip(self) -> str:
        """Obtiene la IP local del Raspberry"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def is_websocket_open(self) -> bool:
        """Verifica si el websocket está abierto de forma segura"""
        try:
            if self.websocket is None:
                return False
            # Verificar si tiene close_code (si lo tiene, está cerrado)
            return self.websocket.close_code is None
        except:
            return False

    def init_printer(self, ip: str, port: int = 9100, max_retries: int = 25, retry_interval: float = 3.0) -> Network:
        """
        Inicializa una impresora térmica, reintentando a intervalo fijo.

        Algunos módulos de red económicos (WiFi/LAN) usados en impresoras
        térmicas tienen una ventana de aceptación de conexión muy angosta
        e irregular en su firmware: pueden rechazar decenas de intentos
        seguidos y luego aceptar uno sin motivo aparente (confirmado por
        pruebas directas, no es un problema de Windows/red del PC).
        Por eso se reintenta a intervalo corto y fijo durante un lapso
        largo (~75s) en vez de un backoff creciente, para maximizar las
        chances de coincidir con esa ventana. No afecta a impresoras que
        responden a la primera: esas devuelven la conexión de inmediato.
        """
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                printer = Network(ip, port=port, timeout=3)

                # SOLO ESTOS COMANDOS BÁSICOS
                # 1. Reset completo de la impresora
                printer._raw(b'\x1B\x40')  # ESC @ - Initialize printer

                # 2. Configurar modo de impresión estándar (opcional, pero útil)
                printer._raw(b'\x1B\x21\x00')  # ESC ! - Select print mode (normal)

                # 3. Configurar alineación izquierda por defecto
                printer._raw(b'\x1B\x61\x00')  # ESC a - Left align

                if attempt > 1:
                    logger.info(f"✅ Conectado a impresora {ip}:{port} en el intento {attempt}/{max_retries}")

                return printer

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"⚠️ Intento {attempt}/{max_retries} fallido conectando a {ip}:{port} - {e}. "
                        f"Reintentando en {retry_interval}s..."
                    )
                    time.sleep(retry_interval)

        logger.error(f"Error inicializando impresora {ip}:{port} tras {max_retries} intentos - {last_error}")
        raise last_error

    def safe_print_text(self, printer: Network, text: str, 
                       bold: bool = False, 
                       double_height: bool = False,
                       double_width: bool = False,
                       align: str = 'left'):
        """
        Imprime texto de forma segura manejando caracteres especiales
        """
        try:
            # Limpiar el texto
            text = clean_text(text)
            
            # Configurar alineación
            align_codes = {
                'left': b'\x1B\x61\x00',
                'center': b'\x1B\x61\x01',
                'right': b'\x1B\x61\x02'
            }
            printer._raw(align_codes.get(align, b'\x1B\x61\x00'))
            
            # Configurar estilo de texto
            mode = 0x00
            if bold:
                mode |= 0x08
            if double_height:
                mode |= 0x10
            if double_width:
                mode |= 0x20
            
            printer._raw(b'\x1B\x21' + bytes([mode]))
            
            # Imprimir texto
            # Convertir a bytes usando latin-1 que es más compatible
            try:
                text_bytes = text.encode('cp850', errors='replace')
            except:
                text_bytes = text.encode('latin-1', errors='replace')
            
            printer._raw(text_bytes)
            # AGREGAR SALTO DE LÍNEA AL FINAL
            printer._raw(b'\n')
            
        except Exception as e:
            logger.error(f"Error imprimiendo texto: {e}")
            # Fallback: intentar imprimir con método básico
            try:
                printer.text(text)
                printer._raw(b'\n')
            except:
                pass

    # ACTIVA BEEP SONIDO:
    def beep_printer(self, printer: Network, ip: str, times: int = 3, duration: int = 5) -> bool:
        """
        Activa el buzzer de la impresora usando ESC B (método compatible)
        
        Args:
            printer: Instancia de la impresora
            ip: IP de la impresora (para cachear si tiene buzzer)
            times: Número de pitidos (1-9)
            duration: Duración en unidades (1-9), donde 5 ≈ 500ms
        
        Returns:
            bool: True si el buzzer funcionó, False si no
        """
        try:
            # ✅ VERIFICAR CACHE: Si ya sabemos que esta impresora NO tiene buzzer
            if self.printers_with_beep.get(ip) == False:
                logger.debug(f"⚠️ Impresora {ip} no tiene buzzer (cacheado)")
                return False
            
            # Limitar valores para seguridad
            times = max(1, min(9, int(times)))
            duration = max(1, min(9, int(duration)))
            
            # ✅ COMANDO ESC B n t (más compatible que ESC ( A)
            # ESC B: 0x1B 0x42 n t
            # n = número de pitidos
            # t = duración (en unidades de tiempo, 5 ≈ 500ms)
            cmd = bytes([0x1B, 0x42, times, duration])
            
            # Enviar comando
            printer._raw(cmd)
            
            # ✅ CACHEAR que esta impresora SÍ tiene buzzer
            self.printers_with_beep[ip] = True
            
            logger.info(f"🔔 Buzzer OK en {ip}: {times} pitidos de ~{duration*100}ms")
            return True
            
        except Exception as e:
            logger.debug(f"⚠️ Buzzer falló en {ip}: {e}")
            # ✅ CACHEAR que esta impresora NO tiene buzzer
            self.printers_with_beep[ip] = False
            return False

    def open_cash_drawer(self, printer: Network, ip: str) -> bool:
            """Abre la gaveta de dinero conectada a la impresora (Advanced)."""
            try:
                import time
                logger.info(f"💰 Abriendo gaveta en {ip}")
                printer._raw(bytes([0x1B, 0x70, 0x00, 0x19, 0x7D]))
                time.sleep(0.1)
                printer._raw(bytes([0x10, 0x14, 0x01, 0x00, 0x08]))
                time.sleep(0.1)
                printer._raw(bytes([0x1B, 0x70, 0x00, 0x32, 0xFA]))
                logger.info("✅ Gaveta abierta")
                return True
            except Exception as e:
                logger.error(f"❌ Error abriendo gaveta: {e}")
                return False

    async def connect(self) -> bool:
        """Conecta al servidor Django vía WebSocket - MEJORADO"""
        try:
            self.connection_attempts += 1
            logger.info(f"🔌 Conectando a {SERVER_URL} (intento #{self.connection_attempts})")
            logger.info(f"📋 Device ID: {self.device_id}")
            logger.info(f"🏢 Branch ID: {BRANCH_ID}")
            
            # MEJORA: PARA HTTPS SEGURIDAD
            _origin = _ws_handshake_origin(SERVER_URL)
            _hdrs = [("Origin", _origin)]
            _ssl = ssl.create_default_context(cafile=certifi.where()) if SERVER_URL.startswith('wss://') else None
            _kw = dict(
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10,
            )
            if "additional_headers" in inspect.signature(websockets.connect).parameters:
                _kw["additional_headers"] = _hdrs
            else:
                _kw["extra_headers"] = _hdrs
            logger.info(f"🌐 WebSocket Origin: {_origin}")
            self.websocket = await asyncio.wait_for(
                websockets.connect(SERVER_URL, ssl=_ssl, **_kw),
                timeout=30,
            )
            
            logger.info("✅ Conexión WebSocket establecida")
            
            auth_message = {
                'type': 'auth',
                'device_id': self.device_id,
                'api_key': API_KEY,
                'branch_id': BRANCH_ID,
                'mac_address': self.mac_address,
                'hostname': socket.gethostname(),
                'ip': self._get_local_ip(),
                'timestamp': datetime.now().isoformat()
            }
            await self.websocket.send(json.dumps(auth_message))
            logger.info("📤 Mensaje de autenticación enviado")
            
            try:
                response = await asyncio.wait_for(self.websocket.recv(), timeout=10)
                data = json.loads(response)
                
                if data.get('type') == 'auth_success':
                    self.authenticated = True
                    self.connection_attempts = 0  # Reset contador
                    self.last_ping_time = datetime.now()
                    logger.info("✅ Autenticación exitosa")
                    logger.info(f"🔍 [DEBUG] authenticated={self.authenticated}, websocket abierto={self.is_websocket_open()}")
                    return True
                else:
                    logger.error(f"❌ Error de autenticación: {data.get('message', 'Desconocido')}")
                    return False
                    
            except asyncio.TimeoutError:
                logger.error("⏰ Timeout esperando autenticación")
                return False
                
        except asyncio.TimeoutError:
            logger.error("⏰ Timeout conectando al servidor")
            return False
        except Exception as e:
            logger.error(f"❌ Error conectando: {e}")
            return False

    async def print_category_ticket(self, ip: str, port: int, data: Dict, paper_width: int) -> bool:
        """
        Imprime ticket de categoría - VERSIÓN MEJORADA
        """
        try:
            # Inicializar impresora
            printer = self.init_printer(ip, port)
            
            # NUEVO: Verificar si es ADICIONAL o REIMPRESIÓN
            order_info = data.get('order', {})
            is_additional = order_info.get('is_additional', False)
            is_reprint = order_info.get('is_reprint', False)
            
            header_label = order_info.get('header_label')
            if header_label:
                printer._raw(b'\x1B\x21\x30')  # Doble altura y ancho
                printer._raw(b'\x1B\x61\x01')  # Center
                printer.text(f"*** {header_label} ***\n")
                printer._raw(b'\x1B\x21\x00')  # Normal
                printer._raw(b'\n')
            if is_additional:
                printer._raw(b'\x1B\x21\x30')  # Doble altura y ancho
                printer._raw(b'\x1B\x61\x01')  # Center
                printer.text("*** PEDIDO ADICIONAL ***\n")
                printer._raw(b'\x1B\x21\x00')  # Normal
                printer._raw(b'\n')
            elif is_reprint:
                printer._raw(b'\x1B\x21\x08')  # Bold
                printer._raw(b'\x1B\x61\x01')  # Center
                printer.text("--- REIMPRESION ---\n")
                printer._raw(b'\x1B\x21\x00')  # Normal
                printer._raw(b'\n')
            
            # ENCABEZADO - Categoría
            category_name = clean_text(data.get('category', {}).get('name', 'ORDEN'))
            self.safe_print_text(printer, f"*** {category_name} ***", 
                               bold=True, double_height=True, align='center')
            
            # Información de la orden
            order_num = clean_text(order_info.get('number', ''))
            order_date = clean_text(order_info.get('date', ''))
            order_time = clean_text(order_info.get('time', ''))
            
            self.safe_print_text(printer, f"{order_num}  {order_date} {order_time}", 
                               align='center')
            
            # LÍNEA SEPARADORA
            self.safe_print_text(printer, "="*40, align='center')
            
            # MESA Y MESERO
            table = clean_text(order_info.get('table', 'PARA LLEVAR'))
            # Piso (nombre del piso/planta según backend)
            floor = order_info.get('floor') or order_info.get('floor_name') or ''
            waiter = clean_text(order_info.get('waiter', ''))
            mesa_line = f"MESA: {table}"
            if floor:
                mesa_line += f" -> PISO: {clean_text(floor)}"
            self.safe_print_text(printer, mesa_line, bold=True, align='left')
           
            if waiter:
                self.safe_print_text(printer, f"MESERO: {waiter}", bold=True, align='left')     
            
            # Servicio si existe
            if order_info.get('service_type'):
                service = clean_text(order_info.get('service_type'))
                self.safe_print_text(printer, f"Servicio: {service}", align='left')
            
            # LÍNEA SEPARADORA
            self.safe_print_text(printer, "-"*40, align='center')
            
            # ITEMS DE LA CATEGORÍA
            items = data.get('items', [])
            item_number = 1
            
            for item in items:
                qty = item.get('quantity', 1)
                product = clean_text(item.get('product_name', ''))
                
                # Verificar si el item es reimpresión
                if item.get('is_reprint'):
                    self.safe_print_text(printer, "(REIMPRESION)", align='right')
                
                # PLATO
                self.safe_print_text(printer, f"PLATO {item_number}: {qty:.0f}X {product}", 
                                   bold=True, double_height=True, align='left')
                
                # NOTA si existe
                if item.get('notes'):
                    notes = clean_text(item['notes'])
                    notes = notes.upper()
                    self.safe_print_text(printer, f"NOTA   : {notes}", align='left')
                
                # Línea separadora entre platos
                if item_number < len(items):
                    self.safe_print_text(printer, "-"*40, align='center')
                
                item_number += 1
            
            # LÍNEA SEPARADORA DOBLE
            self.safe_print_text(printer, "="*40, align='center')
            
            # NOTAS GENERALES si existen
            if data.get('notes'):
                notes = clean_text(data['notes'])
                self.safe_print_text(printer, f"NOTAS: {notes}", bold=True, align='left')
                self.safe_print_text(printer, "-"*40, align='center')
            
            # PIE - Hora de impresión
            time_str = datetime.now().strftime('%H:%M:%S')
            self.safe_print_text(printer, f"IMPRESO: {time_str}", align='center')
            
            # Si es adicional, agregar nota al final
            if is_additional:
                self.safe_print_text(printer, "** ADICIONAL **", bold=True, align='center')
                        
            # Avanzar papel y cortar
            printer._raw(b'\x1B\x64\x05')  # ESC d 5 = Avanza 5 líneas
            import time
            time.sleep(0.20)  # Pausa mínima
            logger.info("⏳ Esperando procesamiento del buffer...")            
            # CAMBIAR EL COMANDO DE CORTE
            # Opción 1: Corte parcial (solo perfora, no corta totalmente)
            printer._raw(b'\x1D\x56\x01')  # GS V 1 = Corte parcial
            time.sleep(0.15)
            # O usar el método de la librería con modo parcial:
            # printer.cut(mode='PART')
            # Pitido DESPUÉS de cortar
            self.beep_printer(printer, ip, times=3, duration=5)
            logger.info("✅ Corte parcial completado")
            logger.info(f"✅ Ticket impreso exitosamente")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error imprimiendo ticket: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        finally:
            # ✅ SIEMPRE CERRAR LA CONEXIÓN, INCLUSO SI HAY ERROR
            if printer:
                try:
                    logger.info("🔧 Cerrando conexión en finally block...")
                    printer.close()
                    logger.info("✅ Conexión cerrada correctamente")
                except Exception as e:
                    logger.error(f"⚠️ Error cerrando impresora: {e}")
   
    async def print_category_cancelled_ticket(self, ip: str, port: int, data: Dict, paper_width: int) -> bool:
        """
        Imprime ticket de ITEMS CANCELADOS o OPERACIÓN ANULADA
        Similar a print_category_ticket pero con formato de CANCELACIÓN
        """
        try:
            # Inicializar impresora
            printer = self.init_printer(ip, port)
            
            # Extraer información
            order_info = data.get('order', {})
            is_full_cancellation = order_info.get('is_full_cancellation', False)
            header_label = data.get('header_label', '*** ITEMS ANULADOS ***')
            
            # ============================================
            # ENCABEZADO CON ETIQUETA DE CANCELACIÓN
            # ============================================
            printer._raw(b'\x1B\x21\x38')  # Bold + Doble altura + Doble ancho
            printer._raw(b'\x1B\x61\x01')  # Center
            printer._raw(header_label.encode('cp850', errors='replace'))
            printer._raw(b'\n')
            printer._raw(b'\x1B\x21\x00')  # Normal
            printer._raw(b'\n')
            
            # Categoría
            category_name = clean_text(data.get('category', {}).get('name', 'ORDEN'))
            self.safe_print_text(printer, f"CATEGORIA: {category_name}", 
                            bold=True, double_height=True, align='center')
            
            # Información de la orden
            order_num = clean_text(order_info.get('number', ''))
            order_date = clean_text(order_info.get('date', ''))
            order_time = clean_text(order_info.get('time', ''))
            
            self.safe_print_text(printer, f"{order_num}  {order_date} {order_time}", 
                            align='center')
            
            # LÍNEA SEPARADORA
            self.safe_print_text(printer, "="*40, align='center')
            
            # MESA Y MESERO
            table = clean_text(order_info.get('table', 'PARA LLEVAR'))
            waiter = clean_text(order_info.get('waiter', ''))
            
            mesa_line = f"MESA: {table}"
            if waiter:
                mesa_line += f"  MESERO: {waiter}"
            self.safe_print_text(printer, mesa_line, bold=True, align='left')
            
            # RAZÓN DE CANCELACIÓN (si existe)
            cancellation_reason = clean_text(order_info.get('cancellation_reason', ''))
            if cancellation_reason:
                self.safe_print_text(printer, "-"*40, align='center')
                self.safe_print_text(printer, "MOTIVO DE CANCELACION:", bold=True, align='left')
                self.safe_print_text(printer, cancellation_reason, align='left')
            
            # LÍNEA SEPARADORA
            self.safe_print_text(printer, "="*40, align='center')
            
            # ============================================
            # ITEMS CANCELADOS CON PREFIJO [X]
            # ============================================
            items = data.get('items', [])
            item_number = 1
            
            for item in items:
                qty = item.get('quantity', 1)
                product = clean_text(item.get('product_name', ''))
                
                # ITEM CANCELADO - Prefijo [X]
                self.safe_print_text(printer, f"[X] PLATO {item_number}: {qty:.0f}X {product}", 
                                bold=True, double_height=True, align='left')
                
                # NOTA si existe
                if item.get('notes'):
                    notes = clean_text(item['notes'])
                    self.safe_print_text(printer, f"NOTA   : {notes}", align='left')
                
                # Línea separadora entre platos
                if item_number < len(items):
                    self.safe_print_text(printer, "-"*40, align='center')
                
                item_number += 1
            
            # ============================================
            # PIE CON ADVERTENCIA DE NO PREPARAR
            # ============================================
            self.safe_print_text(printer, "="*40, align='center')
            
            # Mensaje de advertencia según tipo de cancelación
            if is_full_cancellation:
                printer._raw(b'\x1B\x21\x28')  # Bold + Doble altura + Doble ancho
                printer._raw(b'\x1B\x61\x01')  # Center
                printer._raw(b'NO PREPARAR - ORDEN ANULADA\n')
                printer._raw(b'\x1B\x21\x00')  # Normal
            else:
                printer._raw(b'\x1B\x21\x28')  # Bold + Doble altura + Doble ancho
                printer._raw(b'\x1B\x61\x01')  # Center
                printer._raw(b'NO PREPARAR - ITEMS ANULADOS\n')
                printer._raw(b'\x1B\x21\x00')  # Normal
            
            self.safe_print_text(printer, "="*40, align='center')
            
            # PIE - Hora de cancelación
            time_str = datetime.now().strftime('%H:%M:%S')
            self.safe_print_text(printer, f"CANCELADO: {time_str}", align='center')
            
            # Avanzar papel y cortar
            import time
            printer._raw(b'\x1B\x64\x05')  # ESC d 5 = Avanza 5 líneas (más espacio)
            time.sleep(0.20)

            # Cortar
            printer._raw(b'\x1D\x56\x01')  # Corte parcial
            time.sleep(0.15)

            # Pitido DESPUÉS de cortar
            self.beep_printer(printer, ip, times=3, duration=5)

            logger.info("✅ Ticket cancelación impreso y cortado")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error imprimiendo ticket de cancelación: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        finally:
            # ✅ SIEMPRE CERRAR LA CONEXIÓN
            if printer:
                try:
                    logger.info("🔧 Cerrando conexión en finally block...")
                    printer.close()
                    logger.info("✅ Conexión cerrada correctamente")
                except Exception as e:
                    logger.error(f"⚠️ Error cerrando impresora: {e}")

    async def print_document(self, ip: str, port: int, data: Dict, paper_width: int, open_drawer: bool = False) -> bool:
        """
        Imprime documentos (precuenta, cuenta, boleta, factura) - VERSIÓN MEJORADA
        Con logo reducido, precios alineados y logging completo
        """
        printer = None 
        try:
            # Inicializar impresora con configuración robusta
            printer = self.init_printer(ip, port)

            # RESET Y CONFIGURACIÓN INICIAL
            printer._raw(b'\x1B\x40')  # Reset completo
            printer._raw(b'\x1B\x21\x00')  # Modo normal
            
            # Logo si existe - REDUCIDO Y CENTRADO
            if data.get('logo_base64'):
                try:
                    await self.print_logo_reduced(printer, data['logo_base64'], paper_width)
                except:
                    logger.warning("No se pudo imprimir el logo")
            
            # INFORMACIÓN DE LA EMPRESA
            branch = data.get('branch', {})
            company = clean_text(branch.get('company', ''))
            branch_name = clean_text(branch.get('name', ''))
            
            self.safe_print_text(printer, company, bold=True, align='center')
            self.safe_print_text(printer, branch_name, bold=True, align='center')
            
            if branch.get('ruc'):
                ruc = clean_text(branch['ruc'])
                self.safe_print_text(printer, f"RUC: {ruc}", align='center')
            
            if branch.get('address'):
                address = clean_text(branch['address'])
                self.safe_print_text(printer, address, align='center')
            
            if branch.get('phone'):
                phone = clean_text(branch['phone'])
                self.safe_print_text(printer, f"Tel: {phone}", align='center')
            
            self.safe_print_text(printer, "=" * 48, align='center')
            
            # TIPO DE DOCUMENTO
            doc_type = clean_text(data.get('type', 'CUENTA'))
            doc_info = data.get('document', {})            
            doc_issued = clean_text(doc_info.get('invoice', ''))
            doc_number = clean_text(doc_info.get('number', ''))
            
            self.safe_print_text(printer, doc_type, 
                            bold=True, double_height=True, align='center')
            self.safe_print_text(printer, doc_issued, 
                            bold=True, align='center')
            self.safe_print_text(printer, doc_number, 
                            bold=True, align='center')
            
            doc_date = clean_text(doc_info.get('date', ''))
            doc_time = clean_text(doc_info.get('time', ''))
            self.safe_print_text(printer, f"{doc_date} {doc_time}", align='center')
            
            # INFORMACIÓN DEL CLIENTE
            customer = data.get('customer', {})
            if customer.get('name'):
                self.safe_print_text(printer, "-" * 48, align='left')
            
                cust_name = clean_text(customer['name'])
                self.safe_print_text(printer, f"Cliente: {cust_name}", align='left')
                
                if customer.get('document'):
                    cust_doc_type = clean_text(customer.get('document_type', 'Doc'))
                    cust_doc_number = clean_text(customer['document'])
                    self.safe_print_text(printer, f"{cust_doc_type}: {cust_doc_number}", align='left')
                
                if customer.get('address'):
                    cust_addr = clean_text(customer['address'])
                    self.safe_print_text(printer, f"Dirección: {cust_addr}", align='left')
            
            # Mesa y mesero
            if data.get('table'):
                table = clean_text(data['table'])
                self.safe_print_text(printer, f"Mesa: {table}", align='left')
            
            if data.get('waiter'):
                waiter = clean_text(data['waiter'])
                self.safe_print_text(printer, f"Atendido por: {waiter}", align='left')
            
            self.safe_print_text(printer, "=" * 48, align='left')
            
            # ENCABEZADO DE ITEMS MEJORADO
            # Ajustado para papel de 48 caracteres
            header = "CANT  DESCRIPCION                P.UNIT   TOTAL"
            self.safe_print_text(printer, header, bold=True, align='left')
            self.safe_print_text(printer, "-" * 48, align='left')
            
            # DETALLE DE ITEMS CON FORMATO MEJORADO
            items = data.get('items', [])
            for item in items:
                qty = item.get('quantity', 1)
                name = clean_text(item.get('product_name', ''))
                price = item.get('unit_price', 0)
                total = item.get('total', 0)
                
                # Formatear línea con alineación correcta
                # Formato: "9999  DESCRIPCION LARGA        99.99  999.99"
                qty_str = f"{qty:.0f}"[:4].ljust(4)
                
                # Truncar nombre si es necesario (máximo 24 caracteres)
                if len(name) > 24:
                    name_str = name[:24]
                else:
                    name_str = name.ljust(24)
                
                # Formatear precios alineados a la derecha
                price_str = f"{price:.2f}".rjust(7)
                total_str = f"{total:.2f}".rjust(8)
                
                # Construir línea completa
                line = f"{qty_str}  {name_str} {price_str} {total_str}"
                
                # Imprimir línea formateada
                printer._raw(line.encode('cp850', errors='replace'))
                printer._raw(b'\n')
                
                # Notas del item si existen (con indentación)
                if item.get('notes'):
                    notes = clean_text(item['notes'])
                    if len(notes) > 40:
                        notes = notes[:40]
                    self.safe_print_text(printer, f"      > {notes}", align='left')

                # Componentes de promoción (MENÚ EJECUTIVO, etc.)
                for comp in item.get('combo_components', []):
                    comp_name = clean_text(comp.get('name', ''))
                    comp_qty = comp.get('quantity', 1)
                    comp_line = f"      + {comp_qty:.0f}x {comp_name}"
                    if len(comp_line) > 48:
                        comp_line = comp_line[:48]
                    printer._raw(comp_line.encode('cp850', errors='replace'))
                    printer._raw(b'\n')

            self.safe_print_text(printer, "-" * 48, align='left')
            
            # TOTALES CON ALINEACIÓN A LA DERECHA MEJORADA
            amounts = data.get('amounts', {})
            
            # Función auxiliar para formatear líneas de totales
            def print_amount_line(label: str, amount: float, is_negative: bool = False):
                """Imprime línea de monto con alineación a la derecha"""
                label_str = label.rjust(30)
                if is_negative:
                    amount_str = f"S/ -{amount:.2f}".rjust(17)
                else:
                    amount_str = f"S/ {amount:.2f}".rjust(17)
                line = f"{label_str}:{amount_str}"
                printer._raw(line.encode('cp850', errors='replace'))
                printer._raw(b'\n')
            
            # 1) OP. Gravadas (base imponible) o Subtotal
            base = amounts.get('total_taxable') or amounts.get('subtotal')
            if base is not None and base > 0:
                print_amount_line("OP. Gravadas", float(base))
            
            # 2) IGV (default 10.5% para restaurantes)
            igv_percent = amounts.get('igv_percent', 10.5)
            if amounts.get('igv') is not None:
                print_amount_line("IGV (%.1f%%)" % igv_percent, float(amounts['igv']))
            
            # 3) Descuento (con % si viene discount_percent)
            discount = amounts.get('discount', 0) or 0
            if discount > 0:
                pct = amounts.get('discount_percent')
                if pct is not None and pct > 0:
                    print_amount_line("Descuento (%.0f%%)" % pct, float(discount), is_negative=True)
                else:
                    print_amount_line("Descuento", float(discount), is_negative=True)
            
            # Línea separadora antes del total
            self.safe_print_text(printer, "=" * 48, align='left')
            
            # TOTAL FINAL - Con formato especial
            total_amount = amounts.get('total', 0)
            
            # Configurar texto en negrita y doble altura
            printer._raw(b'\x1B\x21\x38')  # Bold + Double height + Double width
            printer._raw(b'\x1B\x61\x02')  # Right align
            
            # Imprimir total
            total_line = f"TOTAL: S/ {total_amount:.2f}"
            printer._raw(total_line.encode('cp850', errors='replace'))
            printer._raw(b'\n')
            
            # Volver a configuración normal
            printer._raw(b'\x1B\x21\x00')  # Normal
            printer._raw(b'\x1B\x61\x00')  # Left align
            
            # Espacio adicional
            printer._raw(b'\n')
            
            # QR para pagos (si aplica) - TAMAÑO REDUCIDO
            if data.get('qr_data'):
                try:
                    logger.info("="*60)
                    logger.info("🎯 DOCUMENTO TIENE QR_DATA, INTENTANDO IMPRIMIR...")
                    logger.info(f"   qr_data encontrado: {data.get('qr_data')}")
                    logger.info(f"   paper_width: {paper_width}")
                    logger.info("="*60)
                    await self.print_qr_reduced(printer, data['qr_data'], paper_width)  # ✅ AGREGAR paper_width
                    logger.info("✅ Retorno exitoso de print_qr_reduced")
                except Exception as e:
                    logger.warning(f"No se pudo imprimir el código QR: {e}")
            
            # MENSAJE FINAL
            printer._raw(b'\n')
            self.safe_print_text(printer, "Gracias por su preferencia!", align='center')
            
            time_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            self.safe_print_text(printer, f"Impreso: {time_str}", align='center')
            self.safe_print_text(printer, "https://sumapp.pe", align='center')
            
            # Avanzar papel y cortar
            # Avanzar papel (mínimo)
            import time
            printer._raw(b'\x1B\x64\x05')  # ESC d 5 = Avanza 5 líneas
            time.sleep(0.20)

            # Cortar
            printer._raw(b'\x1D\x56\x01')  # Corte parcial
            time.sleep(0.15)
            if open_drawer: # Abrir gaveta
                time.sleep(0.3)
                self.open_cash_drawer(printer, ip)
            logger.info(f"✅ Documento {doc_type} impreso exitosamente")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error imprimiendo documento: {e}", exc_info=True)
            return False
        finally:
            # ✅ SIEMPRE CERRAR LA CONEXIÓN
            if printer:
                try:
                    logger.info("🔧 Cerrando conexión (documento)...")
                    printer.close()
                    logger.info("✅ Conexión cerrada")
                except Exception as e:
                    logger.error(f"⚠️ Error cerrando: {e}")

    async def print_logo_reduced(self, printer: Network, logo_base64: str, paper_width: int):
        """Imprime logo desde base64 - VERSIÓN CENTRADA MANUAL"""
        try:
            # Decodificar imagen
            img_data = base64.b64decode(logo_base64)
            img = Image.open(BytesIO(img_data))
            
            # Convertir a blanco y negro (1-bit)
            img = img.convert('1')
            
            # TAMAÑOS SEGÚN TU CONFIGURACIÓN
            if paper_width == 80:
                max_width = 300
                max_height = 120
                # Ancho total del papel en píxeles (para 80mm)
                paper_width_px = 576
            else:
                max_width = 220
                max_height = 100
                # Ancho total del papel en píxeles (para 58mm)
                paper_width_px = 384
            
            # Calcular nuevo tamaño manteniendo proporción
            ratio = min(max_width / img.width, max_height / img.height)
            
            if ratio < 1:
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
            else:
                new_width = min(int(img.width * 1.2), max_width)
                new_height = min(int(img.height * 1.2), max_height)
            
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # ===== CENTRADO MANUAL =====
            # Calcular padding para centrar
            padding = (paper_width_px - new_width) // 2
            
            # Crear imagen nueva con el ancho del papel
            centered_img = Image.new('1', (paper_width_px, new_height), 'white')
            
            # Pegar el logo en el centro
            centered_img.paste(img, (padding, 0))
            
            # Imprimir la imagen centrada
            printer._raw(b'\n')
            printer.image(centered_img)
            printer._raw(b'\n')
            
            logger.info(f"✅ Logo centrado manualmente: {new_width}x{new_height}px con padding de {padding}px")
            
        except Exception as e:
            logger.error(f"Error imprimiendo logo: {e}")
    
    async def print_qr_reduced(self, printer: Network, qr_data: str, paper_width: int = 80):
        """
        Imprime código QR usando comandos NATIVOS de la impresora
        Esto es más rápido y confiable que enviar una imagen
        """
        try:
            logger.info("=" * 60)
            logger.info("🔍 INICIANDO IMPRESIÓN DE QR (MÉTODO NATIVO)")
            logger.info(f"   QR Data: {qr_data}")
            logger.info(f"   Paper Width: {paper_width}")
            logger.info("=" * 60)
            
            # Convertir datos a bytes
            data_bytes = qr_data.encode('utf-8')
            data_len = len(data_bytes) + 3
            
            # Calcular pL y pH para la longitud
            pL = data_len & 0xFF
            pH = (data_len >> 8) & 0xFF
            
            # ===== CENTRAR =====
            printer._raw(b'\x1B\x61\x01')  # ESC a 1 = Center
            
            # ===== PASO 1: Seleccionar modelo QR (Model 2) =====
            # GS ( k pL pH cn fn n1 n2
            # cn=49 (QR), fn=65 (model), n1=50 (Model 2), n2=0
            printer._raw(bytes([0x1D, 0x28, 0x6B, 0x04, 0x00, 0x31, 0x41, 0x32, 0x00]))
            logger.info("   ✓ Modelo QR configurado")
            
            # ===== PASO 2: Configurar tamaño del módulo =====
            # GS ( k pL pH cn fn n
            # cn=49 (QR), fn=67 (size), n=6 (tamaño 1-16)
            qr_module_size = 6 if paper_width == 80 else 4
            printer._raw(bytes([0x1D, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x43, qr_module_size]))
            logger.info(f"   ✓ Tamaño de módulo: {qr_module_size}")
            
            # ===== PASO 3: Configurar nivel de corrección de errores =====
            # GS ( k pL pH cn fn n
            # cn=49 (QR), fn=69 (error correction), n=48 (L), 49 (M), 50 (Q), 51 (H)
            printer._raw(bytes([0x1D, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x45, 0x31]))  # M level
            logger.info("   ✓ Nivel de corrección: M")
            
            # ===== PASO 4: Almacenar datos del QR =====
            # GS ( k pL pH cn fn m d1...dk
            # cn=49 (QR), fn=80 (store), m=48
            store_pL = (len(data_bytes) + 3) & 0xFF
            store_pH = ((len(data_bytes) + 3) >> 8) & 0xFF
            
            store_cmd = bytes([0x1D, 0x28, 0x6B, store_pL, store_pH, 0x31, 0x50, 0x30]) + data_bytes
            printer._raw(store_cmd)
            logger.info(f"   ✓ Datos almacenados: {len(data_bytes)} bytes")
            
            # ===== PASO 5: Imprimir el QR =====
            # GS ( k pL pH cn fn m
            # cn=49 (QR), fn=81 (print), m=48
            printer._raw(bytes([0x1D, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x51, 0x30]))
            logger.info("   ✓ Comando de impresión enviado")
            
            # ===== VOLVER A ALINEACIÓN IZQUIERDA =====
            printer._raw(b'\x1B\x61\x00')  # ESC a 0 = Left
            printer._raw(b'\n')
            
            logger.info("✅ QR Code impreso exitosamente (método nativo)")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"❌ ERROR EN QR NATIVO: {e}")
            import traceback
            logger.error(traceback.format_exc())
            logger.error("=" * 60)
            
            # Fallback: Intentar método alternativo simplificado
            try:
                logger.info("🔄 Intentando método alternativo...")
                await self._print_qr_fallback(printer, qr_data)
            except Exception as e2:
                logger.error(f"❌ Fallback también falló: {e2}")
                # Último recurso: solo texto
                try:
                    self.safe_print_text(printer, "Escanear QR en:", align='center')
                    # Dividir el QR data en líneas para que quepa
                    for i in range(0, len(qr_data), 40):
                        self.safe_print_text(printer, qr_data[i:i+40], align='center')
                except:
                    pass

    async def _print_qr_fallback(self, printer: Network, qr_data: str):
        """
        Método alternativo usando imagen pequeña con chunks
        """
        try:
            logger.info("🔄 Usando fallback con imagen pequeña...")
            
            # Crear QR muy pequeño
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=2,  # Muy pequeño
                border=1,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.convert('1')
            
            # Tamaño pequeño
            qr_size = 100
            qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.NEAREST)
            
            # Centrar
            printer._raw(b'\x1B\x61\x01')
            
            # Imprimir con el método de la librería pero en chunks pequeños
            width = qr_img.width
            height = qr_img.height
            
            # Usar GS * (define bit image) que es más simple
            # Dividir en líneas para no saturar el buffer
            pixels = list(qr_img.getdata())
            width_bytes = (width + 7) // 8
            
            for y in range(height):
                # Construir una línea
                line_data = []
                for x_byte in range(width_bytes):
                    byte_val = 0
                    for bit in range(8):
                        x = x_byte * 8 + bit
                        if x < width:
                            if pixels[y * width + x] == 0:  # Negro
                                byte_val |= (1 << (7 - bit))
                    line_data.append(byte_val)
                
                # Imprimir línea con ESC * (bit image)
                # ESC * m nL nH d1...dk
                m = 0  # 8-dot single density
                nL = width_bytes & 0xFF
                nH = (width_bytes >> 8) & 0xFF
                
                cmd = bytes([0x1B, 0x2A, m, nL, nH]) + bytes(line_data) + b'\n'
                printer._raw(cmd)
            
            printer._raw(b'\x1B\x61\x00')  # Left align
            printer._raw(b'\n')
            
            logger.info("✅ QR impreso con fallback")
            
        except Exception as e:
            logger.error(f"❌ Error en fallback: {e}")
            raise

    async def print_cash_closure(self, ip: str, port: int, data: Dict, paper_width: int) -> bool:
        """
        Imprime cierre de caja
        """
        try:
            printer = self.init_printer(ip, port)
            
            # ========== ENCABEZADO ==========
            if data.get('logo_base64'):
                try:
                    await self.print_logo_reduced(printer, data['logo_base64'], paper_width)
                except:
                    pass
            
            branch = data.get('branch', {})
            self.safe_print_text(printer, clean_text(branch.get('company', '')), bold=True, align='center')
            self.safe_print_text(printer, clean_text(branch.get('name', '')), align='center')
            
            if branch.get('ruc'):
                self.safe_print_text(printer, f"RUC: {clean_text(branch['ruc'])}", align='center')
            
            self.safe_print_text(printer, "=" * 48, align='center')
            
            # ========== TÍTULO ==========
            self.safe_print_text(printer, "CIERRE DE CAJA", 
                            bold=True, double_height=True, align='center')
            
            closure = data.get('closure', {})
            closure_num = closure.get('number', 0)
            self.safe_print_text(printer, f"CIERRE #{closure_num}", bold=True, align='center')
            
            self.safe_print_text(printer, "=" * 48, align='center')
            
            # ========== CAJERO ==========
            user = data.get('user', {})
            self.safe_print_text(printer, f"Cajero: {clean_text(user.get('name', ''))}", bold=True, align='left')
            self.safe_print_text(printer, f"Rol   : {clean_text(user.get('role', ''))}", align='left')
            
            cash_register = data.get('cash_register', {})
            self.safe_print_text(printer, f"Caja  : {clean_text(cash_register.get('name', ''))}", align='left')
            
            self.safe_print_text(printer, "-" * 48, align='left')
            
            closed_at = clean_text(closure.get('closed_at', ''))
            self.safe_print_text(printer, f"Fecha: {closed_at}", align='left')
            
            self.safe_print_text(printer, "=" * 48, align='left')
            
            # ========== DETALLE POR MÉTODO ==========
            self.safe_print_text(printer, "DETALLE POR METODO", bold=True, align='center')
            self.safe_print_text(printer, "-" * 48, align='left')
            
            payment_methods = data.get('payment_methods', [])
            
            for method_code, method_data in payment_methods:
                method_name = clean_text(method_data.get('name', method_code))
                income = method_data.get('income', 0)
                expense = method_data.get('expense', 0)
                net = method_data.get('net', 0)
                
                # ✅ EFECTIVO EN NEGRITA Y DOBLE ALTURA
                if method_code == 'CASH':
                    printer._raw(b'\n')
                    self.safe_print_text(printer, f">>> {method_name} <<<", 
                                    bold=True, double_height=True, align='center')
                    self.safe_print_text(printer, f"INGRESOS: S/ {income:>12.2f}", 
                                    bold=True, double_height=True, align='left')
                    self.safe_print_text(printer, f"EGRESOS : S/ {expense:>12.2f}", 
                                    bold=True, double_height=True, align='left')
                    self.safe_print_text(printer, f"NETO    : S/ {net:>12.2f}", 
                                    bold=True, double_height=True, align='left')
                    printer._raw(b'\n')
                else:
                    self.safe_print_text(printer, f"{method_name}:", bold=True, align='left')
                    self.safe_print_text(printer, f"  Ingresos: S/ {income:>10.2f}", align='left')
                    self.safe_print_text(printer, f"  Egresos : S/ {expense:>10.2f}", align='left')
                    self.safe_print_text(printer, f"  Neto    : S/ {net:>10.2f}", align='left')
                
                self.safe_print_text(printer, "-" * 48, align='left')
            
            # ========== TOTALES ==========
            totals = data.get('totals', {})
            total_income = totals.get('total_income', 0)
            total_expense = totals.get('total_expense', 0)
            net_total = totals.get('net_total', 0)
            
            self.safe_print_text(printer, "=" * 48, align='left')
            
            self.safe_print_text(printer, f"TOTAL INGRESOS: S/ {total_income:>10.2f}", bold=True, align='left')
            self.safe_print_text(printer, f"TOTAL EGRESOS : S/ {total_expense:>10.2f}", bold=True, align='left')
            
            printer._raw(b'\n')
            self.safe_print_text(printer, "TOTAL NETO:", 
                            bold=True, double_height=True, align='center')
            self.safe_print_text(printer, f"S/ {net_total:.2f}", 
                            bold=True, double_height=True, align='center')
            
            self.safe_print_text(printer, "=" * 48, align='left')
            
            # ========== PIE ==========
            time_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            self.safe_print_text(printer, f"Impreso: {time_str}", align='center')
            
            # Avanzar papel (mínimo)
            import time
            printer._raw(b'\x1B\x64\x05')  # ESC d 5 = Avanza 5 líneas
            time.sleep(0.20)

            # Cortar
            printer._raw(b'\x1D\x56\x01')  # Corte parcial
            time.sleep(0.15)

            logger.info("✅ Cierre de caja impreso y cortado")   
            logger.info(f"✅ Cierre #{closure_num} impreso")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)
            return False
        finally:
            if printer:
                try:
                    printer.close()
                except:
                    pass

    async def print_payment_ticket(self, ip: str, port: int, data: Dict, paper_width: int) -> bool:
        """Imprime ticket de movimiento de caja (ingreso, egreso, compra, venta)"""
        printer = None
        try:
            printer = self.init_printer(ip, port)
            payment = data.get('payment', {})
            label = payment.get('label', 'MOVIMIENTO')
            description = payment.get('description', '')
            amount = payment.get('amount', 0)
            payment_date = payment.get('payment_date', '')
            user = payment.get('user', '')
            method = payment.get('payment_method', '')
            reference = payment.get('reference', '')

            if data.get('logo_base64'):
                try:
                    await self.print_logo_reduced(printer, data['logo_base64'], paper_width)
                except:
                    pass

            branch = data.get('branch', {})
            self.safe_print_text(printer, clean_text(branch.get('company', '')), bold=True, align='center')
            self.safe_print_text(printer, clean_text(branch.get('name', '')), align='center')
            if branch.get('ruc'):
                self.safe_print_text(printer, "RUC: " + clean_text(branch['ruc']), align='center')
            self.safe_print_text(printer, "=" * 48, align='center')
            self.safe_print_text(printer, "*** " + label + " ***", bold=True, double_height=True, align='center')
            self.safe_print_text(printer, "=" * 48, align='center')
            self.safe_print_text(printer, (description[:48] if description else "Movimiento"), align='left')
            self.safe_print_text(printer, "Metodo: " + method, align='left')
            self.safe_print_text(printer, "Usuario: " + user, align='left')
            if reference:
                self.safe_print_text(printer, "Ref: " + reference[:40], align='left')
            self.safe_print_text(printer, "Fecha: " + payment_date, align='left')
            self.safe_print_text(printer, "-" * 48, align='left')
            self.safe_print_text(printer, "MONTO: S/ %.2f" % amount, bold=True, double_height=True, align='center')
            self.safe_print_text(printer, "=" * 48, align='center')
            time_str = datetime.now().strftime("%H:%M:%S")
            self.safe_print_text(printer, "Impreso: " + time_str, align='center')
            import time
            printer._raw(b'\x1B\x64\x05')
            time.sleep(0.20)
            printer._raw(b'\x1D\x56\x01')
            time.sleep(0.15)
            self.beep_printer(printer, ip, times=2, duration=3)
            logger.info("Ticket de pago impreso OK")
            return True
        except Exception as e:
            logger.error("Error imprimiendo payment: %s" % str(e))
            return False
        finally:
            if printer:
                try:
                    printer.close()
                except:
                    pass

    async def handle_test_print(self, data: Dict):
        """Imprime página de prueba con configuración robusta"""
        try:
            printer_ip = data.get('printer_ip', '192.168.1.100')
            printer_port = data.get('printer_port', 9100)
            
            # Inicializar con configuración robusta
            printer = self.init_printer(printer_ip, printer_port)
            
            # Imprimir prueba
            self.safe_print_text(printer, "PRUEBA DE IMPRESION\n", 
                            bold=True, double_height=True, align='center')
            self.safe_print_text(printer, "=" * 32 + "\n", align='center')
            
            self.safe_print_text(printer, f"Raspberry Pi ID: {self.device_id}\n", align='center')
            self.safe_print_text(printer, f"Sucursal: {BRANCH_ID}\n", align='center')
            self.safe_print_text(printer, f"IP Local: {self.device_info['ip']}\n", align='center')
            
            time_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            self.safe_print_text(printer, f"Fecha: {time_str}\n", align='center')
            
            self.safe_print_text(printer, "=" * 32 + "\n", align='center')
            
            # Prueba de caracteres especiales
            self.safe_print_text(printer, "Test de caracteres:\n", bold=True, align='left')
            self.safe_print_text(printer, "Acentos: a e i o u\n", align='left')
            self.safe_print_text(printer, "Mayusculas: A E I O U\n", align='left')
            self.safe_print_text(printer, "Especiales: n N ! ? @ # $ % & * ( )\n", align='left')
            self.safe_print_text(printer, "Numeros: 0123456789\n", align='left')
            
            self.safe_print_text(printer, "=" * 32 + "\n", align='center')
            self.safe_print_text(printer, "Impresora funcionando correctamente\n", align='center')
            
            # Avanzar y cortar
            printer._raw(b'\n\n\n')
            printer.cut()
            printer.close()
            
            logger.info("✅ Prueba de impresión exitosa")
            await self.send_print_status('test', True, printer_ip)
            
        except Exception as e:
            logger.error(f"❌ Error en prueba de impresión: {e}")
            await self.send_print_status('test', False, '', str(e))
    
    async def send_device_info(self):
        """Envía información del dispositivo al servidor"""
        if self.is_websocket_open():
            message = {
                'type': 'device_info',
                'device_info': self.device_info,
                'printers': [],
                'timestamp': datetime.now().isoformat()
            }
            await self.websocket.send(json.dumps(message))
            logger.debug("📤 Información del dispositivo enviada")
    
    async def send_heartbeat(self):
        """Envía heartbeat al servidor - VERSIÓN MEJORADA"""
        try:
            if not self.is_websocket_open():
                logger.error("❌ WebSocket cerrado en heartbeat")
                self.authenticated = False
                return False
            
            # Enviar heartbeat directamente (SIN ensure_open)
            message = {
                'type': 'heartbeat',
                'device_info': self.device_info,
                'timestamp': datetime.now().isoformat()
            }
            
            await self.websocket.send(json.dumps(message))
            self.last_ping_time = datetime.now()
            
            # LOG cada 5 heartbeats (no siempre)
            if not hasattr(self, 'heartbeat_count'):
                self.heartbeat_count = 0
            self.heartbeat_count += 1
            
            if self.heartbeat_count % 5 == 0:
                logger.info(f"💓 Heartbeat #{self.heartbeat_count} - OK")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error en heartbeat: {e}")
            self.authenticated = False
            return False       
    
    async def heartbeat_loop(self):
        """Loop mejorado con detección de conexión muerta"""
        consecutive_failures = 0
        
        while self.running and self.authenticated:
            try:
                # Esperar intervalo aleatorio
                interval = HEARTBEAT_BASE_INTERVAL + random.randint(0, 10)
                await asyncio.sleep(interval)

                # ✅ CAMBIO: Solo verificar timeout SI last_ping_time ya fue inicializado
                if self.last_ping_time is not None:
                    time_since_last_ping = (datetime.now() - self.last_ping_time).total_seconds()
                    if time_since_last_ping > CONNECTION_TIMEOUT:
                        logger.warning(f"⚠️ Conexión muerta detectada ({time_since_last_ping:.0f}s sin respuesta)")
                        self.authenticated = False
                        break
                
                # Enviar heartbeat
                if self.is_websocket_open():
                    success = await self.send_heartbeat()
                    if success:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        logger.warning(f"Heartbeat falló ({consecutive_failures}/3)")
                        
                        if consecutive_failures >= 3:
                            logger.error("❌ Múltiples fallos - FORZANDO RECONEXIÓN")
                            self.authenticated = False
                            if self.websocket:
                                await self.websocket.close()
                            break
                else:
                    logger.warning("WebSocket cerrado, reconectando...")
                    self.authenticated = False
                    break
                    
            except Exception as e:
                logger.error(f"Error en heartbeat: {e}")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    self.authenticated = False
                    break
    
    async def listen(self):
        """Escucha mensajes con detección mejorada de desconexión"""
        no_message_count = 0
        
        try:
            while self.authenticated and self.websocket:
                try:
                    # Esperar mensaje con timeout más corto
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=60  # Reducir a 60 segundos
                    )
                    
                    # Mensaje recibido OK
                    self.last_ping_time = datetime.now()
                    no_message_count = 0
                    await self.handle_message(message)
                    
                except asyncio.TimeoutError:
                    no_message_count += 1
                    logger.warning(f"⏰ Sin mensajes por {no_message_count} minuto(s)")
                    
                    # Si no hay mensajes por 2 minutos, verificar conexión
                    if no_message_count >= 2:
                        logger.warning("Verificando conexión...")
                        
                        # Intentar ping
                        try:
                            pong = await self.websocket.ping()
                            await asyncio.wait_for(pong, timeout=5)
                            logger.info("✅ Conexión OK (ping exitoso)")
                            no_message_count = 0
                        except:
                            logger.error("❌ CONEXIÓN MUERTA - Reconectando")
                            self.authenticated = False
                            break
                            
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"📡 Conexión cerrada: {e}")
            self.authenticated = False
        except Exception as e:
            logger.error(f"❌ Error en listen: {e}", exc_info=True)
            self.authenticated = False
        
    async def handle_message(self, message: str):
        """Procesa mensajes recibidos del servidor"""
        try:
            data = json.loads(message)
            action = data.get('action') or data.get('type')
            logger.info(f"Mensaje recibido: {action}")
            
            # Router de mensajes
            handlers = {
                'print': self.handle_print_job,
                'test_print': self.handle_test_print,
                'heartbeat_ack': self.handle_heartbeat_ack,
                'get_status': self.handle_status_request,
                'auth_success': self.handle_auth_success,
                'auth_error': self.handle_auth_error,
            }
            
            handler = handlers.get(action)
            if handler:
                await handler(data)
            else:
                logger.warning(f"Tipo de mensaje desconocido: {action}")
            
        except json.JSONDecodeError as e:
            logger.error("="*60)
            logger.error(f"❌ ERROR DE JSON: {e}")
            logger.error(f"📄 Mensaje que causó error: {message[:200]}")
            logger.error("="*60)
        except Exception as e:
            logger.error("="*60)
            logger.error(f"❌ ERROR PROCESANDO MENSAJE: {e}")
            logger.error(f"📄 Tipo de error: {type(e).__name__}")
            import traceback
            logger.error(traceback.format_exc())
            logger.error("="*60)
    
    async def handle_auth_success(self, data: Dict[str, Any]):
        """Maneja autenticación exitosa"""
        self.authenticated = True
        logger.info("✅ Autenticación exitosa confirmada")
        await self.send_device_info()
    
    async def handle_auth_error(self, data: Dict[str, Any]):
        """Maneja error de autenticación"""
        self.authenticated = False
        logger.error(f"❌ Error de autenticación: {data.get('message', 'Desconocido')}")
        await self.websocket.close()
    
    async def handle_print_job(self, data: Dict[str, Any]):
        """
        Maneja trabajos de impresión - VERSIÓN MEJORADA
        Detecta si es ADICIONAL o REIMPRESIÓN
        """
        try:
            # Extraer información de la impresora
            printer_info = data.get('printer', {})
            printer_ip = printer_info.get('ip')
            printer_port = printer_info.get('port', 9100)
            printer_name = printer_info.get('name', 'Desconocida')
            paper_width = printer_info.get('paper_width', 80)
            
            # Datos del ticket
            ticket_data = data.get('data', {})
            ticket_type = ticket_data.get('type', 'ORDEN')
            job_id = data.get('job_id', 'unknown')
            copy_number = data.get('copy_number', 1)
            open_drawer = data.get('open_drawer', False)
            
            # NUEVO: Detectar si es ADICIONAL o REIMPRESIÓN
            order_info = ticket_data.get('order', {})
            is_additional = order_info.get('is_additional', False)
            is_reprint = order_info.get('is_reprint', False)
            
            # LOGS DETALLADOS
            logger.info("="*40)
            logger.info(f"📥 TRABAJO DE IMPRESIÓN RECIBIDO")
            logger.info(f"   Tipo: {ticket_type}")
            logger.info(f"   Impresora: {printer_ip}:{printer_port}")
            logger.info(f"   Job ID: {job_id}")
            
            if is_additional:
                logger.info("   ⭐ PEDIDO ADICIONAL")
            elif is_reprint:
                logger.info("   🔄 REIMPRESIÓN")
            else:
                logger.info("   📝 PEDIDO NORMAL")
            
            # ✅ DETECTAR TIPO DE TICKET Y EJECUTAR FUNCIÓN CORRECTA
            if ticket_type == 'CATEGORY_CANCELLED':
                # ✅ NUEVO: Ticket de CANCELACIÓN
                logger.info("   🚫 TICKET DE CANCELACIÓN")
                success = await self.print_category_cancelled_ticket(
                    printer_ip, printer_port, ticket_data, paper_width
                )
            elif ticket_type == 'CATEGORY':
                success = await self.print_category_ticket(
                    printer_ip, printer_port, ticket_data, paper_width
                )
            elif ticket_type in ['PRECUENTA', 'CUENTA', 'BOLETA', 'FACTURA', 'NOTA DE VENTA']:
                success = await self.print_document(
                    printer_ip, printer_port, ticket_data, paper_width, open_drawer=open_drawer
                )
            elif ticket_type == 'CASH_CLOSURE':
                success = await self.print_cash_closure(
                    printer_info.get('ip'),
                    printer_info.get('port', 9100),
                    ticket_data,
                    printer_info.get('paper_width', 80)
                )
            elif ticket_type == 'PAYMENT':
                 logger.info("   💵 TICKET DE PAGO/MOVIMIENTO")
                 success = await self.print_payment_ticket(
                     printer_ip, printer_port, ticket_data, paper_width
                 )
            else:
                success = await self.print_generic_ticket(
                    printer_ip, printer_port, ticket_data, paper_width
                )
            
            # Reportar estado al servidor
            await self.send_print_status(job_id, success, printer_name)
            
            if success:
                logger.info(f"✅ IMPRESIÓN EXITOSA")
            else:
                logger.error(f"❌ IMPRESIÓN FALLIDA")
            
            logger.info("="*40)
            
        except Exception as e:
            logger.error(f"❌ Error en handle_print_job: {e}")
            await self.send_print_status(
                data.get('job_id', 'unknown'), 
                False, 
                data.get('printer', {}).get('name', 'Unknown'),
                str(e)
            )
    
    async def print_generic_ticket(self, ip: str, port: int, data: Dict, paper_width: int) -> bool:
        """Imprime un ticket genérico con configuración robusta"""
        try:
            # Inicializar con configuración robusta
            printer = self.init_printer(ip, port)
            
            # Título
            title = clean_text(data.get('title', 'TICKET'))
            self.safe_print_text(printer, f"{title}\n", 
                            bold=True, double_height=True, align='center')
            self.safe_print_text(printer, "-" * 32 + "\n", align='center')
            
            # Contenido
            for key, value in data.items():
                if key not in ['logo_base64', 'qr_data', 'title']:
                    key_clean = clean_text(str(key))
                    value_clean = clean_text(str(value))
                    self.safe_print_text(printer, f"{key_clean}: {value_clean}\n", align='left')
            
            # Avanzar y cortar
            printer._raw(b'\n\n\n')
            printer.cut()
            import time
            time.sleep(0.5)
            return True
            
        except Exception as e:
            logger.error(f"Error imprimiendo ticket genérico: {e}")
            return False
        finally:
            # ✅ SIEMPRE CERRAR
            if printer:
                try:
                    printer.close()
                except:
                    pass
    
    async def send_print_status(self, job_id: str, success: bool, printer_name: str, error: str = None):
        """Envía estado de impresión al servidor"""
        if self.is_websocket_open():
            status_msg = {
                'type': 'print_status',
                'job_id': job_id,
                'success': success,
                'printer': printer_name,
                'error': error,
                'timestamp': datetime.now().isoformat()
            }
            await self.websocket.send(json.dumps(status_msg))
            logger.debug(f"📤 Estado de impresión enviado: {success}")
    
    async def handle_heartbeat_ack(self, data: Dict):
        """Maneja confirmación de heartbeat"""
        self.last_ping_time = datetime.now()  # IMPORTANTE: actualizar tiempo
        logger.debug("💓 Heartbeat confirmado por el servidor")
    
    async def handle_status_request(self, data: Dict):
        """Maneja solicitud de estado"""
        await self.send_device_info()
    
    async def run(self):
        """Loop principal con reconexión automática MEJORADA"""
        logger.info("🚀 Iniciando cliente de impresión...")
        logger.info(f"📍 Configurado para sucursal ID: {BRANCH_ID}")
        
        while self.running:
            try:
                # Intentar conectar
                connected = await self.connect()
                
                if connected:
                    # Esperar un momento para asegurar autenticación
                    await asyncio.sleep(2)
                    
                    if self.authenticated:
                        logger.info("✅ Sistema listo y escuchando")
                        logger.info(f"🔍 [DEBUG] Antes de crear heartbeat_task - authenticated={self.authenticated}")  # ← AGREGAR
                        
                        # Crear tarea de heartbeat
                        heartbeat_task = asyncio.create_task(self.heartbeat_loop())
                        logger.info("🔍 [DEBUG] heartbeat_task creado")  # ← AGREGAR
                        try:
                            logger.info("🔍 [DEBUG] Entrando a listen()...")  # ← AGREGAR
                            # Escuchar mensajes
                            await self.listen()
                            logger.info("🔍 [DEBUG] Salió de listen()")  # ← AGREGAR
                        finally:
                            # Cancelar heartbeat si se desconecta
                            heartbeat_task.cancel()
                            try:
                                await heartbeat_task
                            except asyncio.CancelledError:
                                pass
                    else:
                        logger.error("❌ No se pudo autenticar con el servidor")
                
                if self.running:
                    # Backoff exponencial para reconexión
                    wait_time = min(RECONNECT_DELAY * (2 ** min(self.connection_attempts - 1, 5)), 60)
                    logger.info(f"🔄 Reconectando en {wait_time} segundos...")
                    await asyncio.sleep(wait_time)
                
            except KeyboardInterrupt:
                logger.info("⏹️ Deteniendo cliente...")
                self.running = False
            except Exception as e:
                logger.error(f"❌ Error en loop principal: {e}")
                if self.running:
                    await asyncio.sleep(RECONNECT_DELAY)
    
    async def shutdown(self):
        """Cierra la conexión limpiamente"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
        logger.info("👋 Cliente detenido")

# ========================================
# FUNCIÓN PRINCIPAL
# ========================================

async def main():
    """Función principal que ejecuta el cliente"""
    client = SmartPrinterClient()
    
    try:
        await client.run()
    except KeyboardInterrupt:
        logger.info("Interrupción recibida, cerrando...")
    finally:
        await client.shutdown()

# ========================================
# PUNTO DE ENTRADA
# ========================================
if __name__ == "__main__":
    try:
        # TEST DE LOGGING
        logger.info("="*50)
        logger.info("LOGS DEL SISTEMA DE IMPRESION")
        logger.info(f"Archivo de log: {LOG_FILE}")
        logger.info("="*50)
        
        print("""
        ╔════════════════════════════════════════╗
        ║   Sistema de Impresión - Raspberry Pi  ║
        ║     Cliente WebSocket v3.3 OPTIMIZADO  ║
        ║         Para 100+ Sucursales           ║
        ╚════════════════════════════════════════╝
        """)
        print("✅ Heartbeat cada 30 segundos")
        print("✅ Logs con rotación automática")
        print("✅ Detección de ADICIONALES y REIMPRESIÓN")
        print(f"📁 Logs en: {LOG_FILE}\n")
        
        # Ejecutar
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✋ Programa terminado por el usuario")
    except Exception as e:
        logger.critical(f"Error crítico: {e}")
        print(f"Error crítico: {e}")
