# -*- coding: utf-8 -*-
"""
Configuracion del servidor. Todo por variables de entorno, con defaults
razonables para correrlo en el laboratorio.

Se leen de /etc/tector-hub/entorno cuando corre bajo systemd (ver
systemd/tector-hub.service). Para desarrollo alcanza con exportarlas a mano
o poner un archivo .env al lado de este repo.
"""
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _bool(nombre, default=False):
    valor = os.environ.get(nombre)
    if valor is None:
        return default
    return valor.strip().lower() in ('1', 'true', 'si', 'yes', 'on')


# --- Base de datos ---
# SQLite alcanza y sobra: la carga real de este servicio es un puñado de
# usuarios y unas decenas de dispositivos, con escrituras contadas por dia.
# Meter Postgres aca seria infraestructura para un problema que no existe.
RUTA_DB = Path(os.environ.get('TECTOR_DB', BASE / 'datos' / 'tector_hub.db'))

# --- Firma de tokens ---
# En produccion TIENE que venir del entorno. El default aleatorio existe para
# que el servidor arranque en desarrollo sin configurar nada, y tiene el
# efecto deseado de invalidar todas las sesiones en cada reinicio -- si eso
# pasa en produccion, es que falta configurar la variable.
CLAVE_JWT = os.environ.get('TECTOR_CLAVE_JWT') or secrets.token_urlsafe(48)
HORAS_TOKEN = int(os.environ.get('TECTOR_HORAS_TOKEN', '720'))  # 30 dias

# --- Google Drive, via rclone ---
# Se usa rclone y no la API de Google directamente por dos razones. Una: el
# proyecto ya lo usa en los dispositivos, el equipo sabe autorizarlo y hay
# procedimiento escrito. Dos, y mas importante: una cuenta de servicio NO
# PUEDE escribir en un Drive personal (Google la rechaza con "Service
# Accounts do not have storage quota"), asi que si o si hace falta un token
# OAuth de usuario -- que es exactamente lo que guarda rclone.conf.
#
# Se autoriza igual que en un dispositivo:
#   rclone authorize drive --drive-scope drive.file <client_id> <client_secret>
RCLONE_BIN = os.environ.get('TECTOR_RCLONE', 'rclone')
RCLONE_CONFIG = os.environ.get(
    'TECTOR_RCLONE_CONFIG', str(Path.home() / '.config' / 'rclone' / 'rclone.conf'))
RCLONE_REMOTE = os.environ.get('TECTOR_RCLONE_REMOTE', 'gdrive')
RCLONE_TIMEOUT_S = int(os.environ.get('TECTOR_RCLONE_TIMEOUT', '60'))

# --- Respaldo de la base ---
# A que remoto de rclone van los respaldos. Por defecto el mismo que los
# datos, pero conviene que sea OTRO: un respaldo guardado en la misma cuenta
# que los datos se pierde con la cuenta. El del proyecto es
# lsdarroyogold@gmail.com; ver el README para autorizarlo como un segundo
# remoto de rclone.
RESPALDO_REMOTE = os.environ.get('TECTOR_RESPALDO_REMOTE', '') or None
RESPALDO_CARPETA = os.environ.get('TECTOR_RESPALDO_CARPETA',
                                  'Tector Hub/respaldos')
RESPALDO_COPIAS = int(os.environ.get('TECTOR_RESPALDO_COPIAS', '14'))

# Cache en memoria de los listados de Drive. Sin esto, abrir el explorador de
# la app dispara un rclone lsjson por pantalla, que tarda segundos.
CACHE_SEGUNDOS = int(os.environ.get('TECTOR_CACHE_S', '120'))

# --- Registro de dispositivos ---
# El endpoint que usan los Tectors para registrarse no lleva autenticacion:
# un dispositivo recien flasheado no tiene ninguna credencial que presentar.
# Lo que protege el sistema es la VINCULACION -- un dispositivo registrado no
# le pertenece a nadie hasta que una cuenta lo reclama con su numero de
# serie, y una vez reclamado no lo puede reclamar otra.
#
# Si en algun momento el servidor queda expuesto a internet abierta, activar
# esto y precargar los seriales esperados con scripts/precargar_serie.py.
SOLO_SERIES_CONOCIDOS = _bool('TECTOR_SOLO_SERIES_CONOCIDOS', False)

CORS_ORIGENES = [
    o.strip() for o in os.environ.get('TECTOR_CORS', '*').split(',') if o.strip()
]


def revisar():
    """Chequeos de arranque. Devuelve la lista de avisos, para que main.py los
    imprima al levantar el servicio en vez de que pasen desapercibidos."""
    avisos = []

    if not os.environ.get('TECTOR_CLAVE_JWT'):
        avisos.append(
            'TECTOR_CLAVE_JWT no esta configurada: se genero una al azar. '
            'Todas las sesiones de la app se van a cortar en cada reinicio '
            'del servicio. Ver systemd/entorno.ejemplo.')
    elif len(CLAVE_JWT) < 32:
        # RFC 7518 3.2: la clave HMAC no deberia ser mas corta que la salida
        # del hash. Con menos de 32 bytes, firmar con HS256 deja de dar la
        # seguridad que se supone que da.
        avisos.append(
            f'TECTOR_CLAVE_JWT tiene {len(CLAVE_JWT)} caracteres; se '
            'recomiendan al menos 32. Generar con: '
            'python3 -c "import secrets; print(secrets.token_urlsafe(48))"')

    if not Path(RCLONE_CONFIG).exists():
        avisos.append(
            f'No existe {RCLONE_CONFIG}: sin eso el servidor no puede leer '
            'ni escribir nada en Drive. Ver el paso 2 del README.')

    return avisos
