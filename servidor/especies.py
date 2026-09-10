# -*- coding: utf-8 -*-
"""
Fotos de especie, desde Wikimedia Commons.

DE DONDE SALEN
--------------
La especificacion original pedia "las mismas fotos que usa BirdWeather".
BirdWeather no publica ningun endpoint documentado de imagenes por especie, y
las que muestra vienen de terceros con licencias propias: reusarlas seria
tomar contenido ajeno sin permiso ni credito.

Wikimedia Commons resuelve las tres cosas de una: tiene foto de practicamente
cualquier ave, la licencia es explicita y consultable por API, y pide
atribucion --que este modulo trae junto con la imagen para que la app la
pueda mostrar. Es tambien de donde saca sus imagenes BirdNET-Pi.

OJO: "esta en Wikipedia" no quiere decir "es de uso libre". Casi todas las
fotos de aves son CC BY o CC BY-SA --libres para reusar, incluso
comercialmente, PERO con obligacion de citar al autor y la licencia-- y
Wikipedia en ingles aloja ademas imagenes de uso legitimo ("fair use") que no
se pueden reusar fuera de su articulo. Por eso hay lista blanca de licencias
(ver LICENCIAS_LIBRES) y por eso la atribucion viaja con la foto en vez de
ser opcional.

COMO SE BUSCA
-------------
Lo unico que el sistema conoce de una deteccion es su nombre comun en ingles,
porque es lo que va en el nombre del archivo que arma exportador.py
("Rufous_Hornero-92-...mp3"). Da la casualidad de que las aves en Wikipedia en
ingles viven justamente bajo su nombre comun, asi que la busqueda es directa.
De paso se trae el titulo en castellano, que es mejor para mostrar.

CACHE
-----
Una especie se resuelve una sola vez y queda en disco para siempre. Son unas
pocas decenas de especies por estacion y la foto de un ave no cambia. Ademas
evita golpear la API de Wikimedia en cada scroll del explorador.
"""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

API = 'https://en.wikipedia.org/w/api.php'
AGENTE = ('TectorHub/1.0 (Laboratorio de Sistemas Dinamicos, FCEyN-UBA; '
          'https://github.com/LSDArroyoGold)')
TIMEOUT_S = 12
ANCHO = 800

CACHE = config.RUTA_DB.parent / 'especies'

# Version del formato de las fichas cacheadas. Subirla invalida el cache
# entero sin tener que borrarlo a mano: una ficha vieja se ignora y se vuelve
# a resolver. Hace falta porque el cache es permanente y sobrevive a cambios
# de codigo --sin esto, agregar un campo nuevo no se veria nunca en las
# especies ya resueltas.
VERSION_FICHA = 3


def _pedir(parametros):
    url = f'{API}?{urllib.parse.urlencode(parametros)}'
    pedido = urllib.request.Request(url, headers={'User-Agent': AGENTE})
    with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode('utf-8'))


# Licencias que permiten reusar la foto en la app. Se comparan contra el
# LicenseShortName que devuelve Commons.
#
# Es una LISTA BLANCA, no una lista negra, y a proposito: si la licencia no
# se reconoce --o si Commons no informa ninguna-- la foto NO se usa. Wikipedia
# en ingles aloja tambien imagenes de uso legitimo ("fair use") que NO se
# pueden reusar fuera de su articulo; con una lista negra, cualquier etiqueta
# que no hubieramos previsto pasaria de largo.
LICENCIAS_LIBRES = re.compile(
    r'^(cc0|cc[ -]by([ -]sa)?[ -]\d|public domain|pdm|no restrictions)',
    re.I)

# Si alguna vez se quiere el criterio mas estricto posible --solo fotos sin
# ninguna obligacion-- alcanza con dejar:
#
#     LICENCIAS_LIBRES = re.compile(r'^(cc0|public domain|pdm)', re.I)
#
# Medido el 10/9/2026 sobre las 10 especies de referencia del AMBA: ninguna
# tiene su foto principal en CC0 ni en dominio publico. Con ese criterio la
# app se quedaria sin ninguna foto. Por eso se aceptan tambien CC BY y
# CC BY-SA, que permiten el uso --incluso comercial-- a cambio de citar autor
# y licencia, cosa que la app hace debajo de cada foto y en Cuenta >
# Créditos de las fotos.


def _es_libre(licencia):
    return bool(licencia and LICENCIAS_LIBRES.match(licencia.strip()))


def _limpiar_html(texto):
    """extmetadata devuelve el autor como HTML (suele ser un <a> al perfil).
    Para mostrarlo en la app alcanza con el texto."""
    if not texto:
        return None
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', texto)).strip() or None


def _clave(nombre):
    """Nombre de archivo seguro para el cache."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', nombre)[:80]


def _buscar_en_wikipedia(nombre_comun):
    """(url_imagen, titulo_archivo, nombre_es) o (None, None, None)."""
    titulo = nombre_comun.replace('_', ' ')
    datos = _pedir({
        'action': 'query', 'format': 'json', 'redirects': 1,
        'titles': titulo,
        'prop': 'pageimages|langlinks',
        'piprop': 'thumbnail|name', 'pithumbsize': ANCHO,
        'lllang': 'es', 'lllimit': 1,
    })
    paginas = (datos.get('query') or {}).get('pages') or {}
    for _, pagina in paginas.items():
        if 'missing' in pagina:
            continue
        thumb = (pagina.get('thumbnail') or {}).get('source')
        archivo = pagina.get('pageimage')
        enlaces = pagina.get('langlinks') or []
        nombre_es = enlaces[0].get('*') if enlaces else None
        if thumb:
            return thumb, archivo, nombre_es
    return None, None, None


def _licencia(titulo_archivo):
    """(autor, licencia, url_descripcion, url_licencia) del archivo."""
    if not titulo_archivo:
        return None, None, None, None
    try:
        datos = _pedir({
            'action': 'query', 'format': 'json',
            'titles': f'File:{titulo_archivo}',
            'prop': 'imageinfo', 'iiprop': 'extmetadata|url',
        })
    except Exception:
        return None, None, None, None

    paginas = (datos.get('query') or {}).get('pages') or {}
    for _, pagina in paginas.items():
        info = (pagina.get('imageinfo') or [{}])[0]
        meta = info.get('extmetadata') or {}
        return (
            _limpiar_html((meta.get('Artist') or {}).get('value')),
            _limpiar_html((meta.get('LicenseShortName') or {}).get('value')),
            info.get('descriptionurl'),
            _limpiar_html((meta.get('LicenseUrl') or {}).get('value')),
        )
    return None, None, None, None


def resolver(nombre_comun):
    """Metadatos de la foto de una especie, o None si Wikipedia no la tiene.

    El resultado se cachea SIEMPRE, incluido el negativo: una especie que
    Wikipedia no conoce no la va a conocer en el proximo scroll tampoco, y
    reintentarla en cada pantalla seria pegarle a la API para nada.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    ficha = CACHE / f'{_clave(nombre_comun)}.json'

    if ficha.exists():
        try:
            guardada = json.loads(ficha.read_text(encoding='utf-8'))
            if guardada.get('v') == VERSION_FICHA:
                return guardada
        except ValueError:
            pass

    try:
        url, archivo, nombre_es = _buscar_en_wikipedia(nombre_comun)
    except Exception:
        # Fallo de red: NO se cachea. La proxima vez se reintenta.
        return None

    if not url:
        datos = {'v': VERSION_FICHA,
                 'nombre_comun': nombre_comun.replace('_', ' '), 'imagen': None}
        ficha.write_text(json.dumps(datos, ensure_ascii=False), encoding='utf-8')
        return datos

    autor, licencia, descripcion, url_licencia = _licencia(archivo)

    # Sin licencia reconocida no se usa la foto. Mejor una ficha sin imagen
    # que publicar algo que no se puede reusar.
    if not _es_libre(licencia):
        datos = {'v': VERSION_FICHA,
                 'nombre_comun': nombre_comun.replace('_', ' '), 'imagen': None,
                 'motivo_sin_foto': f'licencia no reutilizable: {licencia or "sin datos"}'}
        ficha.write_text(json.dumps(datos, ensure_ascii=False), encoding='utf-8')
        return datos

    # Wikipedia en castellano titula a casi todas estas aves por su nombre
    # cientifico ("Furnarius rufus"), no por el vulgar. Sale gratis y es
    # justo el dato que la app muestra en italica debajo del nombre comun,
    # asi que se separa cuando tiene forma de binomio.
    cientifico = nombre_es if (
        nombre_es and re.fullmatch(r'[A-Z][a-z]+ [a-z-]+', nombre_es)) else None

    datos = {
        'v': VERSION_FICHA,
        'nombre_comun': nombre_comun.replace('_', ' '),
        'nombre_cientifico': cientifico,
        'nombre_es': None if cientifico else nombre_es,
        'imagen': f'/especies/{urllib.parse.quote(nombre_comun)}/foto',
        'origen': url,
        'autor': autor,
        'licencia': licencia,
        'licencia_url': url_licencia,
        # Pagina del archivo en Commons: ahi estan el autor, la licencia
        # completa y el original. Es a donde apunta el credito en la app.
        'descripcion_url': descripcion,
    }
    ficha.write_text(json.dumps(datos, ensure_ascii=False), encoding='utf-8')
    return datos


def foto(nombre_comun):
    """(bytes, mime) de la foto, o (None, None).

    El archivo se guarda en disco la primera vez. Despues sale del disco y no
    vuelve a tocar la red --importa: el dashboard pide una foto por cada
    deteccion visible.
    """
    datos = resolver(nombre_comun)
    if not datos or not datos.get('origen'):
        return None, None

    url = datos['origen']
    extension = Path(urllib.parse.urlparse(url).path).suffix.lower() or '.jpg'
    if extension not in ('.jpg', '.jpeg', '.png', '.webp'):
        extension = '.jpg'
    archivo = CACHE / f'{_clave(nombre_comun)}{extension}'

    mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.webp': 'image/webp'}[extension]

    if archivo.exists():
        return archivo.read_bytes(), mime

    try:
        pedido = urllib.request.Request(url, headers={'User-Agent': AGENTE})
        with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as resp:
            contenido = resp.read()
    except Exception:
        return None, None

    archivo.write_bytes(contenido)
    return contenido, mime
