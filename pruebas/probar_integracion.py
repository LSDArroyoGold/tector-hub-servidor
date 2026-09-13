#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba de integración: el servidor de verdad, contra un Drive de mentira.

QUÉ CUBRE QUE probar_api.py NO
------------------------------
probar_api.py usa TestClient y nunca toca `drive.py`, así que todo lo que
depende de Drive --el parseo de los nombres de archivo, el armado del zip, la
escritura de horarios, la lectura de estado.json-- no estaba probado por
nadie. Y es justo la parte que se rompe en el despliegue.

Acá se levanta uvicorn de verdad, con `pruebas/rclone_falso.py` haciendo de
rclone sobre una carpeta local con la estructura real de un Tector. Se le
pega por HTTP, como lo haría la app.

    python3 pruebas/probar_integracion.py
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
fallos = []


def ck(nombre, cond, extra=''):
    print(f'  {"OK  " if cond else "FALLA"}  {nombre}' + (f'  [{extra}]' if extra else ''))
    if not cond:
        fallos.append(nombre)


def armar_drive(base):
    """Un Drive con la estructura real de un Tector 2.1."""
    raiz = base / 'Tector 2'
    hoy = date.today()
    especies = [('Rufous_Hornero', 92), ('Great_Kiskadee', 78),
                ('Rufous-collared_Sparrow', 61)]
    creados = 0
    for d in range(3):
        f = (hoy - timedelta(days=d)).isoformat()
        for esp, conf in especies:
            carpeta = raiz / 'Detecciones' / f / esp
            carpeta.mkdir(parents=True, exist_ok=True)
            # El nombre real lleva ":" en la hora. Windows no deja crear un
            # archivo así, y rclone_falso.py lo traduce a %3A en el disco: el
            # servidor sigue viendo el formato de verdad, que es lo que se
            # quiere probar.
            nombre = f'{esp}-{conf}-{f}-tectornet-09:5{d}:26.mp3'.replace(':', '%3A')
            # No es un mp3 real: al servidor le da igual, lo pasa tal cual.
            (carpeta / nombre).write_bytes(b'ID3' + bytes(400))
            creados += 1

    # Un dia cuyo audio ya no esta --se limpio la carpeta a mano-- pero que
    # dejo su resumen. Es el caso que justifica que Resumenes/ exista: sin
    # leerlo, estas detecciones desaparecen de las estadisticas como si nunca
    # hubieran ocurrido.
    resumenes = raiz / 'Resumenes'
    resumenes.mkdir(parents=True, exist_ok=True)
    cab = chr(0xFEFF) + 'fecha,hora,especie,confianza,serie,archivo,bytes' + chr(10)

    viejo = (hoy - timedelta(days=9)).isoformat()
    contenido = cab
    for h_, e_, c_ in [('06:12:00', 'Picui Ground Dove', 55),
                       ('07:03:00', 'Picui Ground Dove', 71),
                       ('19:44:00', 'Monk Parakeet', 83)]:
        contenido += viejo + ',' + h_ + ',' + e_ + ',' + str(c_) + ',4417,x.mp3,1234' + chr(10)
    (resumenes / (viejo + '.csv')).write_text(contenido, encoding='utf-8')

    # Y el resumen del dia de HOY, que si tiene audio. No se tiene que contar
    # dos veces: la regla es por dia entero, y si hay audio manda el audio.
    (resumenes / (hoy.isoformat() + '.csv')).write_text(
        cab + hoy.isoformat() + ',09:50:26,Rufous Hornero,92,4417,x.mp3,1234' + chr(10),
        encoding='utf-8')

    (raiz / 'estado.json').write_text(json.dumps({
        'version_formato': 1, 'serie': '4417',
        'generado': f'{hoy.isoformat()}T19:12:00',
        'estado': 'en_espera', 'ventana_activa': None,
        'proxima_ventana': {'cual': 'atardecer', 'hora': '18:09'},
        'cierre_forzado': False,
        'horarios': {'auto_sync': True,
                     'amanecer': {'inicio': '08:18', 'fin': '10:18'},
                     'atardecer': {'inicio': '18:09', 'fin': '20:09'},
                     'duracion_amanecer_h': '2', 'duracion_atardecer_h': '2',
                     'offset_amanecer_min': '0', 'offset_atardecer_min': '0'},
        'ubicacion': {'lat': '-34.6131', 'lon': '-58.3772'},
        'bateria': {'voltaje_v': 7.42, 'corriente_ma': 468, 'throttled': '0x0'},
        'detecciones_hoy': 3, 'version_software': '2c4f1a9',
        'drive_path': 'Tector 2',
    }, ensure_ascii=False), encoding='utf-8')

    (raiz / 'log_reciente.txt').write_text(
        f'[{hoy.isoformat()} 08:18] INICIO ventana amanecer | Batería: N/A\n'
        f'[{hoy.isoformat()} 10:18] FIN ventana amanecer | Batería: N/A | '
        'Detecciones subidas: 3 | Próxima ventana: 18:09\n', encoding='utf-8')
    return creados


def puerto_libre():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def pedir(url, token=None, metodo='GET', cuerpo=None, crudo=False):
    req = urllib.request.Request(url, method=metodo)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    if cuerpo is not None:
        req.add_header('Content-Type', 'application/json')
        req.data = json.dumps(cuerpo).encode()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (r.read() if crudo else json.loads(r.read()))
    except urllib.error.HTTPError as e:
        return e.code, None


def ruta_ascii(ruta):
    """Devuelve una ruta escribible en un .bat: ASCII sí o sí.

    Si ya es ASCII se devuelve igual. Si no, se le pide a Windows el nombre
    corto 8.3, que por definición no tiene acentos. Si el volumen tiene los
    nombres cortos desactivados --se puede, por disco-- no queda nada que
    hacer, y conviene decirlo acá y no fallar después con un error que no se
    entiende.
    """
    try:
        ruta.encode('ascii')
        return ruta
    except UnicodeEncodeError:
        pass
    if os.name != 'nt':
        return ruta
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    corta = ''
    if ctypes.windll.kernel32.GetShortPathNameW(ruta, buf, 1024):
        corta = buf.value
        try:
            corta.encode('ascii')
        except UnicodeEncodeError:
            corta = ''
    if not corta:
        raise SystemExit(
            'No hay ruta ASCII para ' + ruta + ': el disco tiene los nombres '
            'cortos 8.3 desactivados y la ruta tiene acentos. Mover el repo o '
            'el venv a una ruta sin acentos.')
    return corta


def main():
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    tmp = Path(tempfile.mkdtemp(prefix='tector-integracion-'))
    drive = tmp / 'drive'
    drive.mkdir()
    n = armar_drive(drive)
    print(f'Drive de mentira en {drive}  ({n} detecciones)\n')

    # rclone.bat: subprocess en Windows no ejecuta un .py directo.
    #
    # TODO lo que entra al .bat tiene que ser ASCII. Los .bat los
    # interpreta Windows con la codepage OEM, no UTF-8, y la ruta del repo
    # tiene un acento ("Física"): escrita tal cual, el intérprete la lee
    # mal y apunta a un archivo inexistente. El error aparecía como "no se
    # pudo leer estado.json", que no ayuda en nada.
    #
    # Son dos rutas y cada una se arregla distinto:
    #   - la del script, copiándolo a la carpeta temporal, que es ASCII;
    #   - la del intérprete, que desde que hay un .venv adentro del repo
    #     también arrastra el acento, pidiendo el nombre corto 8.3.
    falso = tmp / 'rclone_falso.py'
    shutil.copy2(RAIZ / 'pruebas' / 'rclone_falso.py', falso)
    # En Linux el servidor ejecuta este .py DIRECTAMENTE, por su shebang, sin
    # el .bat que hace de intermediario en Windows. Tiene que tener permiso de
    # ejecucion, y git no lo trae. Sin esto el arranque falla con un 500 al
    # primer pedido que toque Drive, y el sintoma --"'NoneType' object is not
    # subscriptable" tres pruebas mas abajo-- no dice nada del permiso.
    if os.name != 'nt':
        falso.chmod(0o755)
    bat = tmp / 'rclone.bat'
    bat.write_text('@echo off' + chr(10) +
                   '"' + ruta_ascii(sys.executable) + '" "' +
                   str(falso) + '" %*' + chr(10), encoding='ascii')

    entorno = {
        **os.environ,
        'TECTOR_DB': str(tmp / 'prueba.db'),
        'TECTOR_CLAVE_JWT': 'clave-larga-solo-para-la-prueba-de-integracion',
        'TECTOR_RCLONE': str(bat) if os.name == 'nt' else str(falso),
        'TECTOR_RCLONE_CONFIG': str(tmp / 'rclone.conf'),
        'TECTOR_DRIVE_FALSO': str(drive),
        'TECTOR_CACHE_S': '0',      # sin cache: cada pedido va al "Drive"
        # El hilo de fondo (cache + reportes diarios) cada 3 s en vez de 10
        # min: el dispositivo se registra DESPUES del arranque y la prueba
        # necesita que el hilo lo vea en tiempo util.
        'TECTOR_CALENTAR_S': '3',
        'PYTHONPATH': str(RAIZ),
    }
    (tmp / 'rclone.conf').write_text('', encoding='utf-8')

    # La cuenta se crea antes de levantar el servicio, contra la misma base.
    subprocess.run([sys.executable, '-m', 'scripts.crear_usuario',
                    'd.arroyo', 'Diego Arroyo', '--clave', 'clave-de-prueba'],
                   cwd=RAIZ, env=entorno, capture_output=True, check=True)

    puerto = puerto_libre()
    # La salida va a un archivo y no a un pipe: leer un pipe a medias puede
    # bloquear, y lo que se necesita es poder mirarla DESPUES de un fallo.
    registro = tmp / 'servidor.log'
    fsal = open(registro, 'w', encoding='utf-8', errors='replace')
    servidor = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'servidor.main:app',
         '--host', '127.0.0.1', '--port', str(puerto), '--log-level', 'warning'],
        cwd=RAIZ, env=entorno, stdout=fsal, stderr=subprocess.STDOUT, text=True)

    base = f'http://127.0.0.1:{puerto}'
    try:
        # Esperar a que levante.
        for _ in range(60):
            try:
                if pedir(base + '/salud')[0] == 200:
                    break
            except Exception:
                pass
            if servidor.poll() is not None:
                print('El servidor murió al arrancar:\n' + servidor.stdout.read())
                return 1
            time.sleep(0.5)
        else:
            print('El servidor no respondió en 30 s.')
            return 1

        print('-- el servidor levanta y responde --')
        ck('GET /salud', pedir(base + '/salud')[0] == 200)
        ck('/docs se sirve', pedir(base + '/openapi.json')[0] == 200)

        print('\n-- login por HTTP de verdad --')
        cod, r = pedir(base + '/auth/login', metodo='POST',
                       cuerpo={'usuario': 'd.arroyo', 'clave': 'clave-de-prueba'})
        ck('login correcto', cod == 200 and bool(r and r.get('token')))
        tk = r['token']
        ck('clave mala rechazada',
           pedir(base + '/auth/login', metodo='POST',
                 cuerpo={'usuario': 'd.arroyo', 'clave': 'no'})[0] == 401)

        print('\n-- registro del dispositivo y vinculacion --')
        cod, r = pedir(base + '/dispositivos/registrar', metodo='POST',
                       cuerpo={'serie': '4417', 'id_hardware': 'hw-INT',
                               'drive_path': 'Tector 2'})
        ck('el Tector se registra', cod == 200 and r['serie'] == '4417')
        ck('la cuenta lo vincula',
           pedir(base + '/dispositivos/vincular', tk, 'POST',
                 {'serie': '4417', 'apodo': 'Reserva'})[0] == 200)

        print('\n-- lectura de Drive (esto es lo que no cubria probar_api) --')
        cod, r = pedir(base + '/dispositivos', tk)
        d0 = r['dispositivos'][0]
        ck('estado.json se lee y se parsea',
           bool(d0['estado']) and d0['estado']['estado'] == 'en_espera')
        ck('trae la proxima ventana',
           d0['estado']['proxima_ventana']['hora'] == '18:09')

        cod, r = pedir(base + '/dispositivos/4417/detecciones', tk)
        ck('lista las detecciones de Drive', len(r['detecciones']) == 9,
           str(len(r['detecciones'])))
        det = r['detecciones'][0]
        ck('parsea especie del nombre de archivo',
           det['especie'] in ('Rufous Hornero', 'Great Kiskadee',
                              'Rufous-collared Sparrow'), det['especie'])
        ck('parsea la confianza', isinstance(det['confianza'], int))
        ck('vienen de la mas reciente a la mas vieja',
           r['detecciones'][0]['fecha'] >= r['detecciones'][-1]['fecha'])

        cod, r = pedir(base + '/dispositivos/4417/fechas', tk)
        ck('lista los dias con detecciones', len(r['fechas']) == 3)

        cod, r = pedir(base + '/dispositivos/4417/estadisticas', tk)
        # 9 detecciones con audio + 3 recuperadas del resumen de un dia cuyo
        # audio ya no esta.
        ck('calcula estadisticas sobre datos reales', r['total'] == 12, str(r['total']))
        ck('el histograma tiene 24 horas', len(r['histograma_horas']) == 24)
        # Ventanas del drive falso: 2 h + 2 h = 4 h/dia. 3 dias con audio (9)
        # + 1 dia solo de resumen (3) = 4 dias con datos, 12 detecciones.
        ck('informa las horas de grabacion por dia', r.get('horas_grabacion_por_dia') == 4.0,
           str(r.get('horas_grabacion_por_dia')))
        ck('promedio por hora = total / (dias * horas)',
           r.get('promedio_por_hora') == round(12 / (4 * 4.0), 1),
           str(r.get('promedio_por_hora')))
        ck('cuenta 5 especies (3 con audio + 2 solo en resumen)',
           r['especies_distintas'] == 5, str(r['especies_distintas']))

        print()
        print('-- dias que ya no tienen audio --')
        viejo = (date.today() - timedelta(days=9)).isoformat()
        ck('el dia sin audio se declara', r.get('dias_sin_audio') == [viejo],
           str(r.get('dias_sin_audio')))
        top = {e['especie']: e['detecciones'] for e in r['top_especies']}
        ck('sus especies entran en el ranking',
           'Picui Ground Dove' in top and 'Monk Parakeet' in top)
        ck('con la cuenta correcta', top.get('Picui Ground Dove') == 2)
        ck('el histograma toma sus horas',
           r['histograma_horas'][6] >= 1 and r['histograma_horas'][19] >= 1)
        por_fecha = {x['fecha']: x['detecciones'] for x in r['por_fecha']}
        ck('el dia recuperado suma sus 3 detecciones',
           por_fecha.get(viejo) == 3, str(por_fecha.get(viejo)))
        ck('un dia con audio Y resumen no se cuenta dos veces',
           por_fecha.get(date.today().isoformat()) == 3,
           str(por_fecha.get(date.today().isoformat())))

        cod, dets = pedir(base + '/dispositivos/4417/detecciones', tk)
        ck('el explorador sigue mostrando solo lo que se puede escuchar',
           all(d['fecha'] != viejo for d in dets['detecciones']))
        cod, fs = pedir(base + '/dispositivos/4417/fechas', tk)
        ck('y las fechas navegables tampoco lo incluyen', viejo not in fs['fechas'])
        ck('toda deteccion que se muestra tiene ruta para reproducir',
           all(d.get('ruta') for d in dets['detecciones']))

        print('\n-- audio y descargas --')
        import urllib.parse
        q = urllib.parse.quote(det['ruta'])
        cod, datos = pedir(f'{base}/dispositivos/4417/audio?ruta={q}', tk, crudo=True)
        ck('sirve el audio', cod == 200 and datos.startswith(b'ID3'),
           f'{len(datos)} bytes')
        cod, zip_ = pedir(
            f'{base}/dispositivos/4417/descargar?fecha={det["fecha"]}', tk, crudo=True)
        ck('arma el zip de un dia', cod == 200 and zip_[:2] == b'PK',
           f'{len(zip_)} bytes')

        print('\n-- escritura: horarios --')
        cod, r = pedir(base + '/dispositivos/4417/horarios', tk, 'PUT', {
            'auto_sync': False, 'inicio_amanecer': '07:30',
            'duracion_amanecer_h': 1.5, 'inicio_atardecer': '18:00',
            'duracion_atardecer_h': 2})
        ck('guarda los horarios', cod == 200 and r['ok'])
        ck('calcula el fin del amanecer', r['fin_amanecer'] == '09:00',
           r.get('fin_amanecer'))
        ck('avisa que NO se aplico todavia', r['aplicado'] is False)
        escrito = (drive / 'Tector 2' / 'config_horarios.txt').read_text(encoding='utf-8')
        ck('el archivo quedo escrito en Drive',
           'INICIO_AMANECER=07:30' in escrito and 'FIN_AMANECER=09:00' in escrito)
        ck('y con el formato que espera el dispositivo',
           'AUTO_SYNC=OFF' in escrito and 'DURACION_AMANECER_SYNC=1.5' in escrito)

        # Coordenadas por el mismo archivo, y la lectura las devuelve.
        cod, r = pedir(base + '/dispositivos/4417/horarios', tk, 'PUT', {
            'auto_sync': True, 'inicio_amanecer': '07:30', 'duracion_amanecer_h': 2,
            'inicio_atardecer': '18:00', 'duracion_atardecer_h': 2,
            'lat': -34.6131, 'lon': -58.3772})
        escrito = (drive / 'Tector 2' / 'config_horarios.txt').read_text(encoding='utf-8')
        ck('las coordenadas se escriben en config_horarios.txt',
           'LAT=-34.6131' in escrito and 'LON=-58.3772' in escrito)
        cod, r = pedir(base + '/dispositivos/4417/horarios', tk)
        ck('y se leen de vuelta', r.get('coordenadas') == {'lat': -34.6131, 'lon': -58.3772},
           str(r.get('coordenadas')))
        ck('un 2.1 se declara como 2.1', r.get('firmware') == '2.1')

        # UN 1.1 LEE OTRO DIALECTO. Con la plantilla de la 2.1 el equipo no
        # sabia a que hora cerrar la ventana: sus awk buscan "fin_amanecer"
        # en minuscula. Se da de alta un heredado y se comprueba lo escrito.
        subprocess.run([sys.executable, '-m', 'scripts.precargar_serie', '0001',
                        '--heredado', '--drive-path', 'Tector 1'],
                       cwd=RAIZ, env=entorno, check=True, capture_output=True)
        (drive / 'Tector 1' / 'Detecciones').mkdir(parents=True, exist_ok=True)
        cod, r = pedir(base + '/dispositivos/vincular', tk, 'POST', {'serie': '0001'})
        ck('se vincula el 1.1 heredado', cod == 200, 'http ' + str(cod))
        cod, r = pedir(base + '/dispositivos/0001/horarios', tk, 'PUT', {
            'auto_sync': True, 'inicio_amanecer': '07:14', 'duracion_amanecer_h': 2,
            'inicio_atardecer': '19:03', 'duracion_atardecer_h': 2,
            'offset_amanecer_min': 20, 'offset_atardecer_min': 20,
            'lat': -34.6, 'lon': -58.4})
        ck('guardar horarios de un 1.1 responde bien', cod == 200, 'http ' + str(cod))
        e11 = (drive / 'Tector 1' / 'config_horarios.txt').read_text(encoding='utf-8')
        ck('para un 1.1 se escribe en SU dialecto (minusculas con espacios)',
           'inicio_amanecer = 07:14' in e11 and 'fin_amanecer = 09:14' in e11, e11[:160])
        ck('con las duraciones como las lee calcular_horarios.py',
           'duracion_amanecer_sync=2' in e11 and 'offset_atardecer_sync=20' in e11)
        ck('y AUTO_SYNC como lo lee auto_sync_horarios.sh', 'AUTO_SYNC=ON' in e11)
        ck('y NADA en mayusculas de la 2.1 que lo confunda', 'INICIO_AMANECER' not in e11)
        ck('las coordenadas tambien, sin espacios', 'LAT=-34.6' in e11 and 'LON=-58.4' in e11)
        cod, r = pedir(base + '/dispositivos/0001/horarios', tk)
        ck('la lectura de un 1.1 sale de su config_horarios.txt',
           r['en_drive'].get('INICIO_AMANECER') == '07:14' and r['en_dispositivo'] is None)
        ck('y se declara como 1.1', r.get('firmware') == '1.1')

        print('\n-- escritura: BirdWeather --')
        cod, r = pedir(base + '/dispositivos/4417/birdweather', tk, 'PUT',
                       {'token': 'a3f9c0de-1234'})
        ck('guarda el token', cod == 200 and r['conectado'])
        bw = (drive / 'Tector 2' / 'config_birdweather.txt').read_text(encoding='utf-8')
        ck('escribe BIRDWEATHER_ID', 'BIRDWEATHER_ID = a3f9c0de-1234' in bw)
        ck('y NO manda coordenadas desde la app',
           'LATITUDE =\n' in bw or 'LATITUDE =' in bw.split('\n')[-3])
        cod, r = pedir(base + '/dispositivos/4417/birdweather', tk)
        ck('nunca devuelve el token entero',
           r['token_parcial'] and 'a3f9c0de-1234' not in str(r['token_parcial']),
           str(r['token_parcial']))

        print('\n-- reportes --')
        cod, r = pedir(base + '/dispositivos/4417/reportes', tk, 'POST',
                       {'ruta': det['ruta'], 'tipo': 'sin_ave'})
        ck('se puede reportar contra una ruta real', cod == 200)
        cod, r = pedir(base + '/reportes', tk)
        ck('el reporte guarda la especie del archivo',
           r['reportes'][0]['especie_detectada'] == det['especie'])
        cod, r = pedir(base + '/dispositivos/4417/reportes', tk, 'POST',
                       {'ruta': det['ruta'], 'tipo': 'otra_conocida',
                        'especie_sugerida': 'rufhor2'})
        ck('responde de inmediato, sin esperar a Drive',
           cod == 200 and bool(r.get('destino_drive')), str(r))
        ck('traduce el codigo a nombre',
           r.get('especie_sugerida_nombre') == 'Rufous Hornero',
           str(r.get('especie_sugerida_nombre')))
        # La copia va en segundo plano: se le da unos segundos.
        carpeta = drive / 'Tector Hub' / 'reportados'
        guardados = []
        for _ in range(40):
            guardados = list(carpeta.rglob('*.mp3')) if carpeta.exists() else []
            if len(guardados) >= 2:
                break
            time.sleep(0.25)
        ck('la copia quedo en la carpeta del proyecto', len(guardados) >= 2,
           str(len(guardados)))
        ck('ordenada por tipo de error',
           any('otra_conocida' in str(g.parent) for g in guardados))
        # Lo que importa para reentrenar: la carpeta es la etiqueta CORRECTA,
        # la que dijo la persona, no la que creyo el motor.
        ck('y bajo la especie CORRECTA, no la que dijo el motor',
           any(g.parent.name == 'Rufous_Hornero' for g in guardados),
           str([str(g.relative_to(carpeta)) for g in guardados]))
        ck('el nombre del archivo conserva lo que creyo el motor',
           any(det['especie'].replace(' ', '_') in g.name for g in guardados))
        ck('y sobrevive si el equipo borra el original',
           bool(guardados) and guardados[0].read_bytes().startswith(b'ID3'))
        cod, r = pedir(base + '/dispositivos/4417/reportes', tk, 'POST',
                       {'ruta': det['ruta'], 'tipo': 'otra_conocida',
                        'especie_sugerida': 'noexiste9'})
        ck('un codigo de especie desconocido se rechaza', cod == 400,
           'http ' + str(cod))
        cod, r = pedir(base + '/reportes', tk)
        ck('la base guarda el nombre de la sugerida, no solo el codigo',
           any(x.get('especie_sugerida_nombre') == 'Rufous Hornero'
               for x in r['reportes']))

        # Deshacer un reporte: se va de la base y la copia se va de Drive.
        equivocado = [x for x in r['reportes']
                      if x.get('especie_sugerida_nombre') == 'Rufous Hornero'][0]
        cod, _ = pedir(f"{base}/reportes/{equivocado['id']}", tk, 'DELETE')
        ck('un reporte propio se puede deshacer', cod == 200, 'http ' + str(cod))
        cod, r = pedir(base + '/reportes', tk)
        ck('y desaparece de la lista',
           all(x['id'] != equivocado['id'] for x in r['reportes']))
        for _ in range(40):
            if not (carpeta / equivocado['destino_drive']).exists():
                break
            time.sleep(0.25)
        ck('y su copia se va de Drive',
           not (carpeta / equivocado['destino_drive']).exists())
        cod, _ = pedir(f"{base}/reportes/{equivocado['id']}", tk, 'DELETE')
        ck('deshacerlo dos veces da 404', cod == 404, 'http ' + str(cod))
        cod, _ = pedir(base + '/dispositivos/4417/estadisticas?dias=3650', tk)
        ck('acepta el periodo "Todo" (3650 dias)', cod == 200, 'http ' + str(cod))

        print()
        print('-- los limites que pide la app de verdad --')
        # La pantalla de cantos pide limite=2000. El tope del endpoint estaba
        # en 1000, asi que devolvia 422 SIEMPRE, desde el primer dia, y no lo
        # agarro nadie: las pruebas nunca habian pedido mas de 1000. Se
        # comprueba con el numero exacto que usa la app, no con uno comodo.
        cod, _ = pedir(base + '/dispositivos/4417/detecciones?limite=2000', tk)
        ck('acepta el limite=2000 que pide la vista de cantos', cod == 200,
           'http ' + str(cod))
        cod, _ = pedir(base + '/dispositivos/4417/detecciones?limite=999999', tk)
        ck('pero sigue rechazando un limite absurdo', cod == 422,
           'http ' + str(cod))

        print()
        print('-- reporte diario en texto --')
        cod, txt = pedir(base + '/dispositivos/4417/reporte', tk, crudo=True)
        txt = txt.decode('utf-8') if isinstance(txt, bytes) else str(txt)
        ck('el reporte del dia se genera', cod == 200, 'http ' + str(cod))
        ck('es texto plano con el encabezado', txt.startswith('TECTOR HUB'))
        ck('lleva el total del dia', 'Total: 3' in txt, txt[:200])
        ck('lleva el ranking de especies', 'Rufous Hornero' in txt)
        ck('lleva la tasa por hora grabada', 'por hora grabada' in txt)
        ck('y el contexto de 30 dias', 'ULTIMOS 30 DIAS' in txt)
        ck('sin fotos ni HTML', '<img' not in txt and 'http' not in txt.split('Generado')[0])
        viejo9 = (date.today() - timedelta(days=9)).isoformat()
        cod, txt2 = pedir(base + f'/dispositivos/4417/reporte?fecha={viejo9}', tk, crudo=True)
        txt2 = txt2.decode('utf-8')
        ck('un dia que solo existe como resumen tambien tiene reporte',
           cod == 200 and 'Picui Ground Dove' in txt2)

        # La carpeta de guardados: el hilo genera los dias pasados solo. Se
        # le da tiempo, y despues se pide la carpeta y el zip.
        for _ in range(60):
            cod, carp = pedir(base + '/dispositivos/4417/reportes-diarios', tk)
            if cod == 200 and len(carp.get('fechas', [])) >= 2:
                break
            time.sleep(0.5)
        ck('los dias pasados se guardan solos', cod == 200 and len(carp['fechas']) >= 2,
           str(carp.get('fechas')))
        ck('el ultimo viene entero para el panel',
           bool(carp.get('ultimo')) and carp['ultimo']['texto'].startswith('TECTOR HUB'))
        ck('el de hoy NO se guarda antes de la hora', date.today().isoformat() not in carp['fechas'])
        cod, z = pedir(base + '/dispositivos/4417/reportes-diarios/todos', tk, crudo=True)
        ck('descargar todos da un zip', cod == 200 and z and z[:2] == b'PK',
           'http ' + str(cod))
        z = z or b''
        import zipfile as _zf, io as _io
        nombres = _zf.ZipFile(_io.BytesIO(z)).namelist() if z else []
        ck('con un .txt por dia', all(n.endswith('.txt') for n in nombres) and len(nombres) == len(carp['fechas']),
           str(nombres))

        cod, r = pedir(base + '/cuenta/reporte-diario', tk)
        ck('la preferencia de mail arranca vacia', cod == 200 and r['email'] == '')
        ck('y dice si el servidor puede mandar correo', 'correo_configurado' in r)
        cod, r = pedir(base + '/cuenta/reporte-diario', tk, 'PUT', {'email': 'diego@ejemplo.org'})
        ck('se guarda un mail', cod == 200 and r['email'] == 'diego@ejemplo.org')
        ck('y avisa si no hay SMTP configurado', bool(r.get('aviso')))
        cod, r = pedir(base + '/cuenta/reporte-diario', tk, 'PUT', {'email': 'no es un mail'})
        ck('un mail invalido se rechaza', cod == 400, 'http ' + str(cod))
        cod, r = pedir(base + '/cuenta/reporte-diario', tk, 'PUT', {'email': ''})
        ck('vacio lo apaga', cod == 200 and r['email'] == '')

        print()
        print('-- piso de fecha por dispositivo --')
        # El caso real: un Tector con detecciones anteriores a estar bien
        # instalado. Se dejan afuera sin borrar nada de Drive.
        corte = (date.today() - timedelta(days=1)).isoformat()
        subprocess.run([sys.executable, '-m', 'scripts.fecha_desde',
                        '4417', corte],
                       cwd=RAIZ, env=entorno, check=True,
                       capture_output=True)

        cod, fs = pedir(base + '/dispositivos/4417/fechas', tk)
        ck('las fechas anteriores al piso desaparecen',
           all(f >= corte for f in fs['fechas']), str(fs['fechas']))
        ck('y las posteriores siguen', corte in fs['fechas'])

        cod, ds = pedir(base + '/dispositivos/4417/detecciones', tk)
        ck('las detecciones viejas tampoco se listan',
           all(d['fecha'] >= corte for d in ds['detecciones']))

        cod, e2 = pedir(base + '/dispositivos/4417/estadisticas', tk)
        ck('las estadisticas cuentan solo desde el piso',
           all(x['fecha'] >= corte for x in e2['por_fecha']),
           str([x['fecha'] for x in e2['por_fecha']]))
        ck('el dia que solo tenia resumen tambien queda afuera',
           e2.get('dias_sin_audio') == [], str(e2.get('dias_sin_audio')))
        ck('y el total baja', e2['total'] < 12, str(e2['total']))

        cod, disp2 = pedir(base + '/dispositivos', tk)
        ck('la app se entera del piso para poder decirlo',
           disp2['dispositivos'][0].get('fecha_desde') == corte)

        # Reversible: es la diferencia con borrar las carpetas a mano.
        subprocess.run([sys.executable, '-m', 'scripts.fecha_desde',
                        '4417', '--quitar'],
                       cwd=RAIZ, env=entorno, check=True, capture_output=True)
        cod, e3 = pedir(base + '/dispositivos/4417/estadisticas', tk)
        ck('quitar el piso devuelve toda la historia', e3['total'] == 12,
           str(e3['total']))

        print('\n-- respaldo de la base --')
        rr = subprocess.run([sys.executable, '-m', 'scripts.respaldar'],
                            cwd=RAIZ, env={**entorno,
                                           'TECTOR_RESPALDO_REMOTE': 'gdrive',
                                           'TECTOR_RESPALDO_CARPETA': 'Respaldos'},
                            capture_output=True, text=True, encoding='utf-8')
        ck('el respaldo corre sin error', rr.returncode == 0,
           (rr.stdout + rr.stderr).strip().split('\n')[-1][:70])
        copias = list((drive / 'Respaldos').glob('*.db')) \
            if (drive / 'Respaldos').exists() else []
        ck('la copia quedo en el Drive de respaldo', len(copias) == 1)
        if copias:
            import sqlite3
            con = sqlite3.connect(copias[0])
            ck('la copia es un SQLite integro',
               con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
            ck('y trae las cuentas',
               con.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0] == 1)
            con.close()

    finally:
        servidor.terminate()
        try:
            servidor.wait(timeout=10)
        except subprocess.TimeoutExpired:
            servidor.kill()
        fsal.close()
        if fallos:
            texto = registro.read_text(encoding='utf-8', errors='replace').strip()
            if texto:
                print(os.linesep + '-- salida del servidor --')
                print(chr(10).join(texto.splitlines()[-25:]))

    print()
    if fallos:
        print(f'{len(fallos)} prueba(s) fallaron:')
        for f in fallos:
            print('  - ' + f)
        return 1
    print('Todas las pruebas de integración pasaron.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
