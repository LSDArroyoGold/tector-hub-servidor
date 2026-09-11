# -*- coding: utf-8 -*-
"""
API de Tector Hub.

Dos clientes, con reglas distintas:

  La APP  -> todo bajo /auth y /dispositivos, con token Bearer. Solo ve los
             Tectors vinculados a esa cuenta.
  El TECTOR -> un unico endpoint, POST /dispositivos/registrar, sin token.
             Un dispositivo recien flasheado no tiene ninguna credencial que
             presentar; lo que protege el sistema es que registrarse no le da
             acceso a nada. Un Tector registrado no le pertenece a nadie
             hasta que una cuenta lo reclama con su numero de serie.

Documentacion viva en /docs cuando el servidor esta corriendo.
"""
import io
import zipfile
from collections import Counter
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import auth, config, db, drive, especies

app = FastAPI(
    title='Tector Hub',
    description='Servidor de la app de administracion de estaciones LSD-Tector.',
    version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGENES,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'])

esquema_bearer = HTTPBearer(auto_error=False)


@app.on_event('startup')
def arrancar():
    for aviso in config.revisar():
        print(f'[tector-hub] AVISO: {aviso}')
    db.inicializar()


# ---------- modelos ----------

class Login(BaseModel):
    usuario: str = Field(min_length=1, max_length=64)
    clave: str = Field(min_length=1, max_length=256)


class RegistroDispositivo(BaseModel):
    serie: str = Field(pattern=r'^\d{4}$')
    id_hardware: str = Field(min_length=4, max_length=128)
    drive_path: str | None = Field(default=None, max_length=256)


class Vinculacion(BaseModel):
    serie: str = Field(pattern=r'^\d{4}$')
    apodo: str | None = Field(default=None, max_length=64)


class Apodo(BaseModel):
    apodo: str | None = Field(default=None, max_length=64)


class Horarios(BaseModel):
    """Lo que la app manda al guardar el panel de horarios.

    El usuario carga inicio y DURACION (la especificacion es explicita en que
    la hora de fin no se tipea); el fin se calcula aca y se escribe en el
    archivo, porque el dispositivo lee INICIO_* y FIN_*, no duraciones.
    """
    auto_sync: bool
    inicio_amanecer: str = Field(pattern=r'^\d{2}:\d{2}$')
    duracion_amanecer_h: float = Field(gt=0, le=12)
    inicio_atardecer: str = Field(pattern=r'^\d{2}:\d{2}$')
    duracion_atardecer_h: float = Field(gt=0, le=12)
    offset_amanecer_min: int = Field(default=0, ge=-180, le=180)
    offset_atardecer_min: int = Field(default=0, ge=-180, le=180)


# ---------- dependencias ----------

def usuario_actual(cred: HTTPAuthorizationCredentials = Depends(esquema_bearer)):
    if cred is None:
        raise HTTPException(401, 'Falta el token de sesion.')
    uid = auth.leer_token(cred.credentials)
    if uid is None:
        raise HTTPException(401, 'Sesion vencida. Volve a entrar.')
    usuario = db.usuario_por_id(uid)
    if usuario is None:
        raise HTTPException(401, 'La cuenta ya no esta activa.')
    return usuario


def dispositivo_propio(serie, usuario):
    """El dispositivo o un 404.

    404 y no 403 a proposito: para alguien que no es el dueño, un Tector
    ajeno no deberia ni existir. Un 403 confirmaria que ese numero de serie
    esta en uso.
    """
    disp = db.dispositivo_del_usuario(usuario['id'], serie)
    if disp is None:
        raise HTTPException(404, 'No tenes ningun Tector con ese numero.')
    if not disp.get('drive_path'):
        raise HTTPException(
            409, 'Este Tector todavia no informo su carpeta de Drive. '
                 'Va a pasar la proxima vez que abra una ventana.')
    return disp


# ---------- salud ----------

@app.get('/salud', tags=['sistema'])
def salud():
    return {'ok': True, 'hora': datetime.now(timezone.utc).isoformat()}


# ---------- autenticacion ----------

@app.post('/auth/login', tags=['cuenta'])
def login(datos: Login):
    usuario = auth.verificar_credenciales(datos.usuario, datos.clave)
    if usuario is None:
        # Un solo mensaje para los dos casos: no decimos si el usuario existe.
        raise HTTPException(401, 'Usuario o contraseña incorrectos.')
    return {
        'token': auth.emitir_token(usuario['id']),
        'usuario': {'usuario': usuario['usuario'], 'nombre': usuario['nombre']},
        # La app lo usa para decidir si manda al alta obligatoria o al dashboard.
        'tiene_dispositivos': bool(db.dispositivos_de(usuario['id'])),
    }


@app.get('/auth/yo', tags=['cuenta'])
def yo(usuario=Depends(usuario_actual)):
    return {'usuario': usuario['usuario'], 'nombre': usuario['nombre'],
            'dispositivos': len(db.dispositivos_de(usuario['id']))}


# ---------- registro (lo llama el Tector) ----------

@app.post('/dispositivos/registrar', tags=['dispositivo'])
def registrar(datos: RegistroDispositivo):
    """Lo llama el propio Tector, sin token, en su primer arranque con
    internet. Idempotente: el mismo hardware siempre recupera su numero."""
    if config.SOLO_SERIES_CONOCIDOS:
        with db.sesion() as con:
            conocido = con.execute('SELECT 1 FROM dispositivos WHERE serie = ?',
                                   (datos.serie,)).fetchone()
        if not conocido:
            raise HTTPException(403, 'Numero de serie no precargado.')

    estado, serie = db.registrar_dispositivo(datos.serie, datos.id_hardware)
    if datos.drive_path:
        db.fijar_drive_path(serie, datos.drive_path)
    return {'estado': estado, 'serie': serie}


# ---------- dispositivos ----------

@app.get('/dispositivos', tags=['dispositivo'])
def listar_dispositivos(usuario=Depends(usuario_actual)):
    """Los Tectors de la cuenta, con su estado. Es lo que alimenta el
    selector de la barra superior y los chips de estado."""
    salida = []
    for disp in db.dispositivos_de(usuario['id']):
        info = {'serie': disp['serie'], 'apodo': disp['apodo'],
                'drive_path': disp['drive_path'], 'estado': None,
                # Un Tector con software 1.1 no se registra solo ni publica
                # estado.json: lo cargo alguien a mano. La app lo muestra
                # distinto en vez de reportarlo como equipo mudo.
                'heredado': str(disp.get('id_hardware') or '')
                            .startswith('heredado:')}
        if disp['drive_path']:
            try:
                info['estado'] = drive.estado(disp['drive_path'])
                # Un 1.1 nunca va a tener estado.json. En vez de mostrarlo
                # como equipo mudo, se reconstruye lo que se pueda del log,
                # que ese si lo sube en cada ventana.
                if info['estado'] is None and info['heredado']:
                    info['estado'] = drive.estado_heredado(disp['drive_path'])
            except drive.ErrorDrive:
                info['estado'] = None
        salida.append(info)
    return {'dispositivos': salida}


@app.post('/dispositivos/vincular', tags=['dispositivo'])
def vincular(datos: Vinculacion, usuario=Depends(usuario_actual)):
    resultado, _ = db.vincular(usuario['id'], datos.serie, datos.apodo)
    if resultado == 'inexistente':
        raise HTTPException(
            404, 'Ese Tector todavia no se registro. Se registra solo la '
                 'primera vez que se conecta a una red WiFi.')
    if resultado == 'tomado':
        raise HTTPException(409, 'Ese Tector ya esta vinculado a otra cuenta.')
    return {'ok': True, 'serie': datos.serie}


@app.patch('/dispositivos/{serie}', tags=['dispositivo'])
def renombrar(datos: Apodo, serie: str = Path(pattern=r'^\d{4}$'),
              usuario=Depends(usuario_actual)):
    if not db.renombrar(usuario['id'], serie, datos.apodo):
        raise HTTPException(404, 'No tenes ningun Tector con ese numero.')
    return {'ok': True, 'apodo': datos.apodo}


@app.delete('/dispositivos/{serie}', tags=['dispositivo'])
def desvincular(serie: str = Path(pattern=r'^\d{4}$'),
                usuario=Depends(usuario_actual)):
    """Saca el Tector de esta cuenta. No borra nada de Drive ni toca el
    dispositivo: el equipo sigue grabando y subiendo igual, y puede volver a
    vincularse despues (o vincularse a otra cuenta)."""
    if not db.desvincular(usuario['id'], serie):
        raise HTTPException(404, 'No tenes ningun Tector con ese numero.')
    return {'ok': True}


@app.get('/dispositivos/{serie}/estado', tags=['dispositivo'])
def estado_dispositivo(serie: str = Path(pattern=r'^\d{4}$'),
                       usuario=Depends(usuario_actual)):
    disp = dispositivo_propio(serie, usuario)
    estado = drive.estado(disp['drive_path'])
    if estado is None:
        estado = drive.estado_heredado(disp['drive_path'])
    if estado is None:
        raise HTTPException(
            503, 'Este Tector todavia no publico su estado. Si viene de la '
                 'version 2.0, hace falta actualizarlo.')
    return estado


@app.get('/dispositivos/{serie}/log', tags=['dispositivo'])
def log(serie: str = Path(pattern=r'^\d{4}$'), usuario=Depends(usuario_actual)):
    disp = dispositivo_propio(serie, usuario)
    return {'log': drive.log_reciente(disp['drive_path'])}


# ---------- detecciones ----------

@app.get('/dispositivos/{serie}/fechas', tags=['detecciones'])
def fechas(serie: str = Path(pattern=r'^\d{4}$'), usuario=Depends(usuario_actual)):
    disp = dispositivo_propio(serie, usuario)
    return {'fechas': drive.fechas_con_detecciones(disp['drive_path'])}


@app.get('/dispositivos/{serie}/detecciones', tags=['detecciones'])
def detecciones(serie: str = Path(pattern=r'^\d{4}$'),
                fecha: str | None = Query(default=None,
                                          pattern=r'^\d{4}-\d{2}-\d{2}$'),
                especie: str | None = Query(default=None, max_length=96),
                limite: int = Query(default=200, ge=1, le=1000),
                usuario=Depends(usuario_actual)):
    disp = dispositivo_propio(serie, usuario)
    return {'detecciones': drive.detecciones(disp['drive_path'], fecha, especie,
                                             limite)}


@app.get('/dispositivos/{serie}/audio', tags=['detecciones'])
def audio(ruta: str = Query(max_length=512),
          serie: str = Path(pattern=r'^\d{4}$'),
          usuario=Depends(usuario_actual)):
    """Sirve el mp3 de una deteccion.

    La ruta viene del listado, pero igual se valida que caiga dentro de la
    carpeta de ESTE dispositivo: sin ese chequeo, un usuario podria pedir
    '<otro_drive_path>/...' y leer detecciones ajenas. Tambien se rechaza
    '..' para que no se pueda salir de la carpeta por el camino largo.
    """
    disp = dispositivo_propio(serie, usuario)
    prefijo = f'{disp["drive_path"]}/Detecciones/'
    if not ruta.startswith(prefijo) or '..' in ruta:
        raise HTTPException(400, 'Ruta invalida.')

    try:
        datos = drive.leer_binario(ruta)
    except drive.ErrorDrive as e:
        raise HTTPException(404, f'No se pudo leer el audio: {e}')

    return Response(
        content=datos,
        media_type='audio/mpeg',
        headers={'Cache-Control': 'private, max-age=86400',
                 'Content-Disposition': f'inline; filename="{ruta.split("/")[-1]}"'})


@app.get('/dispositivos/{serie}/estadisticas', tags=['detecciones'])
def estadisticas(serie: str = Path(pattern=r'^\d{4}$'),
                 dias: int = Query(default=30, ge=1, le=365),
                 usuario=Depends(usuario_actual)):
    """Las metricas del panel.

    Salen de dos fuentes que se complementan: los nombres de archivo del
    audio que esta en Drive, y --para los dias cuyo audio ya no esta-- los
    CSV de Resumenes/. Ver drive.detecciones_completas() para la regla de
    cual se usa cuando.

    Sin lo segundo, limpiar una carpeta vieja de Drive borraba tambien el
    historial: el nombre del mp3 era el unico registro que existia.
    """
    disp = dispositivo_propio(serie, usuario)
    todas, fechas_recuperadas = drive.detecciones_completas(
        disp['drive_path'], limite=20000)

    fechas_ordenadas = sorted({d['fecha'] for d in todas}, reverse=True)[:dias]
    recientes = [d for d in todas if d['fecha'] in fechas_ordenadas]

    por_hora = Counter(int(d['hora'][:2]) for d in recientes)
    por_especie = Counter(d['especie'] for d in recientes)
    por_fecha = Counter(d['fecha'] for d in recientes)

    vistas_antes = {d['especie'] for d in todas
                    if d['fecha'] not in fechas_ordenadas}
    nuevas = [
        {'especie': d['especie'], 'fecha': d['fecha'], 'hora': d['hora'],
         'confianza': d['confianza'], 'ruta': d['ruta'],
         # Un hallazgo recuperado de un resumen no tiene audio para escuchar.
         'desde_resumen': bool(d.get('desde_resumen'))}
        for d in sorted(recientes, key=lambda x: (x['fecha'], x['hora']))
        if d['especie'] not in vistas_antes
    ]
    # Solo la primera aparicion de cada especie nueva.
    destacados, ya = [], set()
    for n in reversed(nuevas):
        if n['especie'] not in ya:
            ya.add(n['especie'])
            destacados.append(n)

    dias_con_datos = len(por_fecha) or 1
    confianzas = [d['confianza'] for d in recientes]

    return {
        'dias': dias,
        'total': len(recientes),
        'promedio_por_dia': round(len(recientes) / dias_con_datos, 1),
        'especies_distintas': len(por_especie),
        'histograma_horas': [por_hora.get(h, 0) for h in range(24)],
        'top_especies': [{'especie': e, 'detecciones': n}
                         for e, n in por_especie.most_common(10)],
        'por_fecha': [{'fecha': f, 'detecciones': por_fecha[f]}
                      for f in sorted(por_fecha)],
        'confianza_media': round(sum(confianzas) / len(confianzas), 1)
        if confianzas else None,
        'hallazgos': destacados[:5],
        # Dias que entran en el calculo pero de los que ya no queda audio.
        # La app lo dice, para que un numero que no cierra con lo que se ve
        # en el explorador tenga una explicacion a la vista.
        'dias_sin_audio': [f for f in fechas_recuperadas
                           if f in fechas_ordenadas],
    }




# ---------- descarga de carpetas ----------

# Topes de una descarga en zip. Existen porque el zip se arma entero en
# memoria antes de mandarlo: sin limite, pedir una carpeta grande desde el
# telefono se lleva puesta la RAM del servidor (que puede ser un celular).
MAX_ARCHIVOS = 300
MAX_BYTES = 250 * 1024 * 1024


@app.get('/dispositivos/{serie}/descargar', tags=['detecciones'])
def descargar(serie: str = Path(pattern=r'^\d{4}$'),
              fecha: str = Query(pattern=r'^\d{4}-\d{2}-\d{2}$'),
              especie: str | None = Query(default=None, max_length=96),
              usuario=Depends(usuario_actual)):
    """Un zip con los audios de un día, o de una sola especie de ese día.

    Lo arma el servidor y no la app porque los archivos estan en Drive: la
    app tendria que bajar uno por uno y comprimirlos en el telefono.
    """
    disp = dispositivo_propio(serie, usuario)
    detecciones = drive.detecciones(disp['drive_path'], fecha, especie,
                                    limite=MAX_ARCHIVOS + 1)
    if not detecciones:
        raise HTTPException(404, 'No hay detecciones para descargar.')
    if len(detecciones) > MAX_ARCHIVOS:
        raise HTTPException(
            413, f'Son más de {MAX_ARCHIVOS} archivos. Descargá una especie '
                 'por vez.')

    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as z:
        # ZIP_STORED y no DEFLATE: los mp3 ya vienen comprimidos, deflate
        # gastaria CPU para no bajar casi nada de tamaño.
        for d in detecciones:
            try:
                datos = drive.leer_binario(d['ruta'])
            except drive.ErrorDrive:
                continue
            total += len(datos)
            if total > MAX_BYTES:
                raise HTTPException(
                    413, 'La carpeta pesa demasiado para descargarla de una. '
                         'Descargá una especie por vez.')
            z.writestr(f'{d["especie_carpeta"]}/{d["ruta"].split("/")[-1]}', datos)

    nombre = f'Tector{serie}_{fecha}' + (f'_{especie}' if especie else '') + '.zip'
    return Response(
        content=buf.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{nombre}"',
                 'Content-Length': str(buf.tell())})

# ---------- horarios ----------

def _sumar(hhmm, horas):
    h, m = (int(x) for x in hhmm.split(':'))
    total = (h * 60 + m + round(horas * 60)) % (24 * 60)
    return f'{total // 60:02d}:{total % 60:02d}'


@app.get('/dispositivos/{serie}/horarios', tags=['horarios'])
def leer_horarios(serie: str = Path(pattern=r'^\d{4}$'),
                  usuario=Depends(usuario_actual)):
    disp = dispositivo_propio(serie, usuario)
    return {
        # Lo que la app escribio en Drive.
        'en_drive': drive.horarios(disp['drive_path']),
        # Lo que el dispositivo esta corriendo de verdad. Pueden diferir
        # mientras haya un cambio pendiente de aplicarse.
        'en_dispositivo': (drive.estado(disp['drive_path']) or {}).get('horarios'),
    }


@app.put('/dispositivos/{serie}/horarios', tags=['horarios'])
def guardar_horarios(datos: Horarios, serie: str = Path(pattern=r'^\d{4}$'),
                     usuario=Depends(usuario_actual)):
    """Escribe config_horarios.txt en Drive.

    NO aplica el cambio: el dispositivo baja el archivo al abrir o cerrar su
    proxima ventana. La respuesta incluye cuando se espera que eso pase, para
    que la app pueda decirlo en el dialogo de confirmacion en vez de fingir
    que fue inmediato.
    """
    disp = dispositivo_propio(serie, usuario)

    fin_amanecer = _sumar(datos.inicio_amanecer, datos.duracion_amanecer_h)
    fin_atardecer = _sumar(datos.inicio_atardecer, datos.duracion_atardecer_h)

    drive.escribir_horarios(disp['drive_path'], {
        'auto_sync': 'ON' if datos.auto_sync else 'OFF',
        'offset_amanecer': datos.offset_amanecer_min,
        'offset_atardecer': datos.offset_atardecer_min,
        'duracion_amanecer': datos.duracion_amanecer_h,
        'duracion_atardecer': datos.duracion_atardecer_h,
        'inicio_amanecer': datos.inicio_amanecer,
        'fin_amanecer': fin_amanecer,
        'inicio_atardecer': datos.inicio_atardecer,
        'fin_atardecer': fin_atardecer,
    })

    estado = drive.estado(disp['drive_path']) or {}
    proxima = (estado.get('proxima_ventana') or {}).get('hora')

    return {
        'ok': True,
        'fin_amanecer': fin_amanecer,
        'fin_atardecer': fin_atardecer,
        'aplicado': False,
        'se_aplica_en': proxima,
        'aviso': (f'El Tector toma el cambio cuando despierte, '
                  f'a las {proxima}.' if proxima else
                  'El Tector toma el cambio en su proxima ventana.'),
        # La hora de inicio ya se uso para programar el despertador de la
        # ventana en curso, asi que un cambio de inicio nunca puede aplicar
        # hacia atras. La app muestra esto tal cual.
        'nota_inicio': 'Los cambios de hora de inicio rigen desde la ventana '
                       'siguiente, no desde la que este en curso.',
    }




# ---------- especies ----------

@app.get('/especies/{nombre}', tags=['especies'])
def especie(nombre: str = Path(max_length=96), usuario=Depends(usuario_actual)):
    """Nombre cientifico, foto y atribucion de una especie.

    Se pide con el nombre tal cual viene en el archivo de la deteccion
    ('Rufous_Hornero'), que es lo unico que el sistema conoce.
    """
    datos = especies.resolver(nombre)
    if datos is None:
        raise HTTPException(503, 'No se pudo consultar Wikimedia. Reintentá.')
    return datos


@app.get('/especies/{nombre}/foto', tags=['especies'])
def foto_especie(nombre: str = Path(max_length=96)):
    """La foto en si.

    Sin token a proposito: es lo unico de toda la API que no es dato de
    nadie --son fotos publicas de Wikimedia Commons, cacheadas. Pedir
    autenticacion aca obligaria a que cada <img> del dashboard cargara con
    JavaScript en vez de dejarselo al navegador, que ya sabe cachear.
    """
    contenido, mime = especies.foto(nombre)
    if contenido is None:
        raise HTTPException(404, 'Sin foto para esa especie.')
    return Response(
        content=contenido, media_type=mime,
        headers={'Cache-Control': 'public, max-age=31536000, immutable'})


# ---------- BirdWeather ----------

class TokenBirdWeather(BaseModel):
    # Vacio = desconectar. birdweather.py de TectorNet interpreta
    # BIRDWEATHER_ID vacio como "estacion no conectada" y deja de postear.
    token: str = Field(default='', max_length=128)


PLANTILLA_BW = """# Escrito por Tector Hub el {sello}.
#
# Lo lee scripts/birdweather.py de TectorNet. El puente que lo baja de Drive
# y lo instala en la carpeta del motor es scripts/aplicar_config_remota.sh de
# LSD-Tector2.1, que corre al abrir cada ventana.
#
# LATITUDE y LONGITUDE las completa ese script con las coordenadas reales que
# el propio dispositivo detecto: NO viajan desde la app, para que no se pueda
# publicar una estacion en el lugar equivocado.
BIRDWEATHER_ID = {token}
LATITUDE =
LONGITUDE =
"""


@app.get('/dispositivos/{serie}/birdweather', tags=['birdweather'])
def leer_birdweather(serie: str = Path(pattern=r'^\d{4}$'),
                     usuario=Depends(usuario_actual)):
    disp = dispositivo_propio(serie, usuario)
    token = ''
    try:
        crudo = drive.leer_texto(f'{disp["drive_path"]}/config_birdweather.txt')
        for linea in crudo.splitlines():
            if linea.strip().startswith('BIRDWEATHER_ID'):
                token = linea.split('=', 1)[1].strip()
    except drive.ErrorDrive:
        pass

    estado_disp = drive.estado(disp['drive_path']) or {}
    ubicacion = estado_disp.get('ubicacion') or {}
    return {
        'conectado': bool(token),
        # Nunca se devuelve el token entero: alcanza para que el usuario
        # reconozca cual cargo, y no para reusarlo si le miran la pantalla.
        'token_parcial': f'{token[:4]}…{token[-4:]}' if len(token) > 8 else None,
        'mapa': f'https://app.birdweather.com/stations/{token}' if token else None,
        'ubicacion': ubicacion,
        'aplicado': (estado_disp.get('proxima_ventana') or {}).get('hora'),
    }


@app.put('/dispositivos/{serie}/birdweather', tags=['birdweather'])
def guardar_birdweather(datos: TokenBirdWeather,
                        serie: str = Path(pattern=r'^\d{4}$'),
                        usuario=Depends(usuario_actual)):
    """Deja el token en Drive. Igual que los horarios, NO lo aplica: el
    dispositivo lo baja al abrir su proxima ventana."""
    from datetime import datetime
    disp = dispositivo_propio(serie, usuario)
    token = datos.token.strip()

    drive.escribir_texto(
        f'{disp["drive_path"]}/config_birdweather.txt',
        PLANTILLA_BW.format(sello=datetime.now().strftime('%d/%m/%Y %H:%M'),
                            token=token))

    proxima = ((drive.estado(disp['drive_path']) or {})
               .get('proxima_ventana') or {}).get('hora')
    return {
        'ok': True,
        'conectado': bool(token),
        'aplicado': False,
        'se_aplica_en': proxima,
        'aviso': (f'El Tector toma el cambio cuando despierte, a las {proxima}.'
                  if proxima else
                  'El Tector toma el cambio en su proxima ventana.'),
    }



# ---------- reportes de error ----------

# Los cuatro tipos que la app ofrece. Lista cerrada y validada del lado del
# servidor: si mañana la app manda uno nuevo sin que exista aca, se rechaza en
# vez de guardar basura que despues nadie sabe interpretar.
#
# NO hay un tipo "la especie estaba bien". Es deliberado: si existiera, lo que
# llegaria seria una mezcla de "escuche y estaba bien" con "toque sin
# escuchar", indistinguibles entre si. Asi, un reporte significa siempre lo
# mismo.
TIPOS_REPORTE = {
    'sin_ave': 'No hay ningún ave en este audio',
    'otra_desconocida': 'Hay un ave, pero no es esta especie (no sé cuál es)',
    'otra_conocida': 'Hay un ave, pero no es esta especie (sé cuál es)',
    'audio_cortado': 'El canto está cortado o partido en dos',
}


class Reporte(BaseModel):
    ruta: str = Field(min_length=1, max_length=512)
    tipo: str
    # Codigo eBird de la especie que la persona dice que era. Solo tiene
    # sentido con tipo 'otra_conocida'.
    especie_sugerida: str | None = Field(default=None, max_length=64)
    comentario: str | None = Field(default=None, max_length=500)


@app.get('/reportes/tipos', tags=['reportes'])
def tipos_de_reporte():
    """Los tipos validos, para que la app arme el menu sin tenerlos duplicados
    en su propio codigo."""
    return {'tipos': [{'id': k, 'texto': v} for k, v in TIPOS_REPORTE.items()]}


@app.post('/dispositivos/{serie}/reportes', tags=['reportes'])
def reportar(datos: Reporte, serie: str = Path(pattern=r'^\d{4}$'),
             usuario=Depends(usuario_actual)):
    """Alguien escuchó una detección y dice que el motor se equivocó."""
    disp = dispositivo_propio(serie, usuario)

    if datos.tipo not in TIPOS_REPORTE:
        raise HTTPException(400, f'Tipo de reporte desconocido: {datos.tipo}')
    if datos.tipo == 'otra_conocida' and not datos.especie_sugerida:
        raise HTTPException(400, 'Falta la especie: elegila de la lista.')

    # Misma validacion que para servir el audio: la ruta tiene que caer dentro
    # de la carpeta de ESTE dispositivo. Sin esto, un reporte podria dejar
    # anotada una ruta a la carpeta de otro.
    prefijo = f'{disp["drive_path"]}/Detecciones/'
    if not datos.ruta.startswith(prefijo) or '..' in datos.ruta:
        raise HTTPException(400, 'Ruta inválida.')

    # El nombre del archivo ya trae especie, confianza y fecha. Se copian al
    # reporte porque los audios viejos se borran por retencion y el reporte
    # tiene que seguir siendo legible cuando el archivo ya no este.
    nombre = datos.ruta.split('/')[-1]
    m = drive.PATRON_DETECCION.match(nombre)

    # El audio se preserva ANTES de guardar el reporte. Si se hiciera al
    # reves y la copia fallara, quedaria un reporte apuntando a un audio que
    # se va a borrar solo, y nadie se enteraria hasta querer reentrenar.
    #
    # El nombre del destino lleva el tipo de reporte adelante: la carpeta se
    # vuelve navegable por categoria de error sin abrir la base.
    conservado = drive.preservar(
        datos.ruta, f'{datos.tipo}/{serie}_{nombre}')

    ident = db.guardar_reporte(usuario['id'], serie, {
        'ruta': datos.ruta,
        'especie_detectada': m.group('especie').replace('_', ' ') if m else None,
        'confianza': int(m.group('confianza')) if m else None,
        'fecha_deteccion': f'{m.group("fecha")} {m.group("hora")}' if m else None,
        'tipo': datos.tipo,
        'especie_sugerida': datos.especie_sugerida,
        'comentario': (datos.comentario or '').strip() or None,
    })
    return {'ok': True, 'id': ident, 'audio_conservado': conservado}


@app.get('/reportes', tags=['reportes'])
def mis_reportes(usuario=Depends(usuario_actual)):
    """Los reportes que hizo esta cuenta. Para que se pueda ver qué se mandó,
    no para revisarlos: eso se hace con scripts/exportar_reportes.py."""
    return {'reportes': db.reportes_de(usuario['id'])}

# ---------- vista combinada ----------

@app.get('/resumen', tags=['dispositivo'])
def resumen(usuario=Depends(usuario_actual)):
    """Los datos del boton 'Todos' de la barra superior."""
    dispositivos = db.dispositivos_de(usuario['id'])
    total, especies, reportando, ultimas = 0, set(), 0, []

    for disp in dispositivos:
        if not disp['drive_path']:
            continue
        try:
            recientes = drive.detecciones(disp['drive_path'], limite=500)
        except drive.ErrorDrive:
            continue
        if recientes:
            reportando += 1
            ultimas.append({**recientes[0], 'serie': disp['serie'],
                            'apodo': disp['apodo']})
        total += len(recientes)
        especies.update(d['especie'] for d in recientes)

    ultimas.sort(key=lambda d: (d['fecha'], d['hora']), reverse=True)
    return {
        'dispositivos': len(dispositivos),
        'reportando': reportando,
        'detecciones': total,
        'especies': len(especies),
        'ultima_deteccion': ultimas[0] if ultimas else None,
    }
