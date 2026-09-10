# -*- coding: utf-8 -*-
"""
Acceso a Google Drive, via rclone.

POR QUE EL SERVIDOR Y NO LA APP
-------------------------------
La app podria hablarle a Drive directamente, pero entonces cada telefono
tendria que llevar credenciales de escritura sobre las carpetas de todos los
Tectors de esa cuenta. Este proyecto ya se quemo una vez con credenciales de
Drive donde no correspondia: un rclone.conf commiteado hizo que Google
revocara el token en silencio y rompiera la sincronizacion sin aviso.

Con el servidor en el medio, las credenciales viven en un solo lugar
controlado, y la app solo tiene un token de sesion que se puede revocar.

POR QUE RCLONE Y NO LA API DE GOOGLE
------------------------------------
Ver la nota larga en config.py. Resumen: hace falta un token OAuth de
usuario (una cuenta de servicio no puede escribir en un Drive personal), el
proyecto ya usa rclone en los dispositivos, y el equipo ya sabe autorizarlo.
"""
import json
import re
import subprocess
import time
from threading import Lock

from . import config

_cache = {}
_cache_lock = Lock()

# Rufous_Hornero-92-2026-09-09-birdnet-09:52:26.mp3
# El separador acepta birdnet y tectornet: los archivos historicos que ya
# estan en Drive tienen el nombre viejo y se siguen leyendo bien.
PATRON_DETECCION = re.compile(
    r'^(?P<especie>.+?)-(?P<confianza>\d{1,3})-'
    r'(?P<fecha>\d{4}-\d{2}-\d{2})-(?:birdnet|tectornet)-'
    r'(?P<hora>\d{2}:\d{2}:\d{2})\.(?P<ext>mp3|wav|flac)$')


class ErrorDrive(RuntimeError):
    pass


def _correr(argumentos, entrada=None, binario=False):
    orden = [config.RCLONE_BIN, '--config', config.RCLONE_CONFIG] + argumentos
    try:
        proceso = subprocess.run(
            orden,
            input=entrada,
            capture_output=True,
            timeout=config.RCLONE_TIMEOUT_S)
    except FileNotFoundError:
        raise ErrorDrive(f'No se encontro el ejecutable de rclone '
                         f'({config.RCLONE_BIN}).')
    except subprocess.TimeoutExpired:
        raise ErrorDrive('Drive no respondio a tiempo.')

    if proceso.returncode != 0:
        detalle = proceso.stderr.decode('utf-8', 'replace').strip()
        # Ultima linea nada mas: rclone escupe varias lineas de contexto y la
        # util casi siempre es la ultima.
        detalle = detalle.splitlines()[-1] if detalle else 'error desconocido'
        raise ErrorDrive(detalle)

    return proceso.stdout if binario else proceso.stdout.decode('utf-8', 'replace')


def _remoto(ruta):
    return f'{config.RCLONE_REMOTE}:{ruta}'


def leer_texto(ruta):
    return _correr(['cat', _remoto(ruta)])


def leer_binario(ruta):
    return _correr(['cat', _remoto(ruta)], binario=True)


def escribir_texto(ruta, contenido):
    """rcat escribe desde stdin, sin archivo temporal en el servidor."""
    _correr(['rcat', _remoto(ruta)], entrada=contenido.encode('utf-8'))
    invalidar(ruta)


def listar(ruta, recursivo=False, solo_directorios=False, usar_cache=True):
    """lsjson de una carpeta. Devuelve [] si la carpeta no existe todavia --
    es el caso normal de un Tector que aun no subio nada, no un error."""
    clave = (ruta, recursivo, solo_directorios)
    if usar_cache:
        with _cache_lock:
            guardado = _cache.get(clave)
            if guardado and time.time() - guardado[0] < config.CACHE_SEGUNDOS:
                return guardado[1]

    argumentos = ['lsjson', _remoto(ruta)]
    if recursivo:
        argumentos.append('--recursive')
    if solo_directorios:
        argumentos.append('--dirs-only')

    try:
        datos = json.loads(_correr(argumentos) or '[]')
    except ErrorDrive as e:
        if 'directory not found' in str(e).lower():
            datos = []
        else:
            raise
    except ValueError:
        datos = []

    with _cache_lock:
        _cache[clave] = (time.time(), datos)
    return datos


def invalidar(prefijo=''):
    """Tira el cache de todo lo que cuelgue de un prefijo. Se llama despues
    de cada escritura para que la app no siga viendo el valor viejo."""
    with _cache_lock:
        for clave in [k for k in _cache if k[0].startswith(prefijo)]:
            del _cache[clave]


# ---------- lectura de alto nivel ----------

def estado(drive_path):
    """estado.json del dispositivo, o None si todavia no lo subio.

    Un Tector que viene de la 2.0 nunca escribio este archivo. La app tiene
    que seguir andando con esos equipos, en modo degradado, asi que un None
    aca no es un error.
    """
    try:
        return json.loads(leer_texto(f'{drive_path}/estado.json'))
    except (ErrorDrive, ValueError):
        return None


def log_reciente(drive_path):
    for nombre in ('log_reciente.txt', 'log_sistema.txt'):
        try:
            return leer_texto(f'{drive_path}/{nombre}')
        except ErrorDrive:
            continue
    return ''


def horarios(drive_path):
    """config_horarios.txt parseado. Ojo: es lo que la app ESCRIBIO, que no
    es necesariamente lo que el dispositivo esta corriendo -- el Tector lo
    baja recien al abrir o cerrar su proxima ventana. Para lo que realmente
    esta vigente en el equipo, mirar estado.json."""
    try:
        crudo = leer_texto(f'{drive_path}/config_horarios.txt')
    except ErrorDrive:
        return {}
    datos = {}
    for linea in crudo.splitlines():
        linea = linea.strip()
        if linea.startswith('#') or '=' not in linea:
            continue
        clave, valor = linea.split('=', 1)
        datos[clave.strip()] = valor.strip()
    return datos


def _parsear_nombre(nombre):
    m = PATRON_DETECCION.match(nombre)
    if not m:
        return None
    return {
        'especie': m.group('especie').replace('_', ' '),
        'especie_carpeta': m.group('especie'),
        'confianza': int(m.group('confianza')),
        'fecha': m.group('fecha'),
        'hora': m.group('hora'),
    }


def detecciones(drive_path, fecha=None, especie=None, limite=200):
    """Detecciones leidas del arbol de Drive.

    No hay base de datos de detecciones en ningun lado del proyecto: el
    nombre del archivo ES el registro (lo arma exportador.py de TectorNet).
    Asi que esto lista Drive y parsea nombres, que es exactamente lo que ya
    hacen los scripts del dispositivo para contar detecciones.

    Estructura real: <drive_path>/Detecciones/<fecha>/<Especie>/<archivo>.mp3
    """
    raiz = f'{drive_path}/Detecciones'
    if fecha and especie:
        subruta, prof = f'{raiz}/{fecha}/{especie}', False
    elif fecha:
        subruta, prof = f'{raiz}/{fecha}', True
    else:
        subruta, prof = raiz, True

    salida = []
    for entrada in listar(subruta, recursivo=prof):
        if entrada.get('IsDir'):
            continue
        datos = _parsear_nombre(entrada['Name'])
        if not datos:
            continue
        datos['ruta'] = f'{subruta}/{entrada["Path"]}'
        datos['bytes'] = entrada.get('Size')
        salida.append(datos)

    salida.sort(key=lambda d: (d['fecha'], d['hora']), reverse=True)
    return salida[:limite]


def fechas_con_detecciones(drive_path):
    carpetas = listar(f'{drive_path}/Detecciones', solo_directorios=True)
    fechas = [c['Name'] for c in carpetas
              if re.fullmatch(r'\d{4}-\d{2}-\d{2}', c['Name'])]
    return sorted(fechas, reverse=True)


# ---------- escritura ----------

PLANTILLA_HORARIOS = """# Escrito por Tector Hub el {sello}.
#
# El dispositivo baja este archivo de Drive al abrir y al cerrar cada
# ventana (ver inicio_*.sh y cierre_*.sh de LSD-Tector2.1), asi que estos
# valores rigen a partir de la proxima vez que el equipo despierte, no de
# forma inmediata.

# Setear AUTO_SYNC en ON para calculo automatico de amanecer y atardecer
AUTO_SYNC={auto_sync}

# -------------------------

# Tocar solo si AUTO_SYNC esta en ON

## Offsets en minutos. Negativo para arrancar ventana antes del horario calculado
OFFSET_AMANECER_SYNC={offset_amanecer}
OFFSET_ATARDECER_SYNC={offset_atardecer}

## Duracion de las ventanas en horas. Acepta decimales
DURACION_AMANECER_SYNC={duracion_amanecer}
DURACION_ATARDECER_SYNC={duracion_atardecer}

# -------------------------

# Tocar solo si AUTO_SYNC esta en OFF. Horarios de inicio y fin de ventanas
INICIO_AMANECER={inicio_amanecer}
FIN_AMANECER={fin_amanecer}
INICIO_ATARDECER={inicio_atardecer}
FIN_ATARDECER={fin_atardecer}
"""


def escribir_horarios(drive_path, valores):
    """Escribe config_horarios.txt entero, con el formato exacto que esperan
    los awk del dispositivo.

    Aca SI se reescribe el archivo completo, a diferencia de
    config_general.txt: este no guarda ningun estado del equipo, solo la
    configuracion que la app controla. Reescribirlo entero evita tener que
    hacer sed remoto sobre Drive."""
    from datetime import datetime
    contenido = PLANTILLA_HORARIOS.format(
        sello=datetime.now().strftime('%d/%m/%Y %H:%M'), **valores)
    escribir_texto(f'{drive_path}/config_horarios.txt', contenido)
    return contenido
