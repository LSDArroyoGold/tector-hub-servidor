#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas de la API. Sin pytest a proposito: se corre igual que cualquier otro
script del proyecto, con el interprete del venv y nada mas.

    .venv/bin/python pruebas/probar_api.py

Usa una base temporal, asi que no toca la base real. No prueba nada que
dependa de Drive: para eso hace falta un rclone autorizado y detecciones de
verdad, y esto tiene que poder correrse en cualquier lado.
"""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Antes de importar el servidor: la config se lee al importar.
_tmp = tempfile.mkdtemp(prefix='tector-pruebas-')
os.environ['TECTOR_DB'] = str(Path(_tmp) / 'prueba.db')
os.environ['TECTOR_CLAVE_JWT'] = 'clave-larga-solo-para-pruebas-no-produccion'

from fastapi.testclient import TestClient  # noqa: E402

from servidor import auth, db, main  # noqa: E402

fallos = []


def ck(nombre, condicion):
    marca = 'OK  ' if condicion else 'FALLA'
    print(f'  {marca}  {nombre}')
    if not condicion:
        fallos.append(nombre)


def main_pruebas():
    db.inicializar()
    db.crear_usuario('d.arroyo', 'Diego Arroyo', auth.hashear('clave-buena'))
    db.crear_usuario('t.gold', 'Tomas Gold', auth.hashear('otra-clave'))
    c = TestClient(main.app)

    print('\n-- contraseñas --')
    ck('el hash no contiene la clave', 'clave-buena' not in auth.hashear('clave-buena'))
    ck('dos hashes de la misma clave difieren (sal)',
       auth.hashear('x') != auth.hashear('x'))
    larga_a, larga_b = 'A' * 80 + 'UNO', 'A' * 80 + 'DOS'
    h = auth.hashear(larga_a)
    ck('claves de mas de 72 bytes con prefijo comun no se confunden',
       auth._verificar(larga_a, h) and not auth._verificar(larga_b, h))
    ck('un hash corrupto en la base es login fallido, no error 500',
       auth._verificar('x', 'esto-no-es-un-hash') is False)

    print('\n-- login --')
    ck('clave incorrecta -> 401',
       c.post('/auth/login', json={'usuario': 'd.arroyo', 'clave': 'mala'}
              ).status_code == 401)
    r = c.post('/auth/login', json={'usuario': 'no-existe', 'clave': 'x'})
    ck('usuario inexistente -> el mismo 401 y el mismo mensaje',
       r.status_code == 401 and 'incorrectos' in r.json()['detail'])
    r = c.post('/auth/login', json={'usuario': 'd.arroyo', 'clave': 'clave-buena'})
    ck('login valido devuelve token', r.status_code == 200 and r.json()['token'])
    ck('cuenta sin dispositivos -> tiene_dispositivos False',
       r.json()['tiene_dispositivos'] is False)
    tok = {'Authorization': f'Bearer {r.json()["token"]}'}
    r2 = c.post('/auth/login', json={'usuario': 't.gold', 'clave': 'otra-clave'})
    tok2 = {'Authorization': f'Bearer {r2.json()["token"]}'}
    ck('sin token -> 401', c.get('/dispositivos').status_code == 401)
    ck('token invalido -> 401',
       c.get('/dispositivos', headers={'Authorization': 'Bearer xx'}
             ).status_code == 401)

    print('\n-- registro y colision de numero de serie --')
    r = c.post('/dispositivos/registrar',
               json={'serie': '4417', 'id_hardware': 'hw-AAA',
                     'drive_path': 'Tector 1'})
    ck('primer registro -> ok, 4417', r.json() == {'estado': 'ok', 'serie': '4417'})
    r = c.post('/dispositivos/registrar',
               json={'serie': '4417', 'id_hardware': 'hw-AAA'})
    ck('el mismo hardware se re-registra sin cambiar de numero',
       r.json()['serie'] == '4417')
    r = c.post('/dispositivos/registrar',
               json={'serie': '4417', 'id_hardware': 'hw-BBB'})
    ck('otro hardware con el mismo numero -> reasignado a 4418',
       r.json() == {'estado': 'reasignado', 'serie': '4418'})
    r = c.post('/dispositivos/registrar',
               json={'serie': '4417', 'id_hardware': 'hw-BBB'})
    ck('el reasignado conserva su numero nuevo', r.json()['serie'] == '4418')
    ck('numero con formato invalido -> 422',
       c.post('/dispositivos/registrar',
              json={'serie': '99', 'id_hardware': 'hw-CCC'}).status_code == 422)

    print('\n-- precarga (modo SOLO_SERIES_CONOCIDOS) --')
    with db.sesion() as con:
        con.execute(
            'INSERT INTO dispositivos (serie, id_hardware, primer_registro, '
            'ultimo_visto) VALUES (?, ?, ?, ?)',
            ('7777', 'reservado:7777', db.ahora(), db.ahora()))
    r = c.post('/dispositivos/registrar',
               json={'serie': '7777', 'id_hardware': 'hw-DDD'})
    ck('un equipo reclama su numero precargado sin que se lea como colision',
       r.json() == {'estado': 'ok', 'serie': '7777'})

    print('\n-- vinculacion --')
    ck('vincular un numero inexistente -> 404',
       c.post('/dispositivos/vincular', json={'serie': '0000'}, headers=tok
              ).status_code == 404)
    ck('vincular 4417 -> ok',
       c.post('/dispositivos/vincular',
              json={'serie': '4417', 'apodo': 'Reserva Costanera'}, headers=tok
              ).status_code == 200)
    ck('otra cuenta no puede reclamar el mismo -> 409',
       c.post('/dispositivos/vincular', json={'serie': '4417'}, headers=tok2
              ).status_code == 409)
    r = c.get('/dispositivos', headers=tok)
    ck('el dueño ve 1 dispositivo', len(r.json()['dispositivos']) == 1)
    ck('y ve su apodo', r.json()['dispositivos'][0]['apodo'] == 'Reserva Costanera')
    ck('la otra cuenta ve 0',
       len(c.get('/dispositivos', headers=tok2).json()['dispositivos']) == 0)
    ck('login ahora informa que la cuenta ya tiene dispositivos',
       c.post('/auth/login', json={'usuario': 'd.arroyo', 'clave': 'clave-buena'}
              ).json()['tiene_dispositivos'] is True)

    print('\n-- aislamiento entre cuentas --')
    ck('un Tector ajeno responde 404, no 403',
       c.get('/dispositivos/4417/estado', headers=tok2).status_code == 404)
    ck('el audio de un Tector ajeno -> 404',
       c.get('/dispositivos/4417/audio',
             params={'ruta': 'Tector 1/Detecciones/x.mp3'}, headers=tok2
             ).status_code == 404)
    ck('ruta con .. -> 400',
       c.get('/dispositivos/4417/audio',
             params={'ruta': 'Tector 1/Detecciones/../../otro/x.mp3'}, headers=tok
             ).status_code == 400)
    ck('ruta fuera de la carpeta del equipo -> 400',
       c.get('/dispositivos/4417/audio',
             params={'ruta': 'Tector 9/Detecciones/x.mp3'}, headers=tok
             ).status_code == 400)
    ck('renombrar un Tector ajeno -> 404',
       c.patch('/dispositivos/4417', json={'apodo': 'mio'}, headers=tok2
               ).status_code == 404)

    print('\n-- apodo y desvinculacion --')
    ck('renombrar el propio',
       c.patch('/dispositivos/4417', json={'apodo': 'Costanera Sur'}, headers=tok
               ).status_code == 200)
    ck('desvincular', c.delete('/dispositivos/4417', headers=tok).status_code == 200)
    ck('tras desvincular, otra cuenta ya lo puede reclamar',
       c.post('/dispositivos/vincular', json={'serie': '4417'}, headers=tok2
              ).status_code == 200)

    print('\n-- calculo de fin de ventana --')
    from servidor.main import _sumar
    ck('08:18 + 2 h = 10:18', _sumar('08:18', 2) == '10:18')
    ck('18:09 + 2,5 h = 20:39', _sumar('18:09', 2.5) == '20:39')
    ck('07:30 + 3,5 h = 11:00', _sumar('07:30', 3.5) == '11:00')
    ck('23:30 + 1 h cruza medianoche = 00:30', _sumar('23:30', 1) == '00:30')

    print('\n-- reportes de error --')
    # Hace falta un dispositivo propio con drive_path para poder reportar.
    c.post('/dispositivos/registrar',
           json={'serie': '5555', 'id_hardware': 'hw-REP', 'drive_path': 'Tector R'})
    c.post('/dispositivos/vincular', json={'serie': '5555'}, headers=tok)
    ruta = ('Tector R/Detecciones/2026-09-10/Rufous_Hornero/'
            'Rufous_Hornero-92-2026-09-10-tectornet-09:52:26.mp3')

    r = c.get('/reportes/tipos', headers=tok)
    ck('hay cuatro tipos de reporte', len(r.json()['tipos']) == 4)
    ck('ninguno confirma que la especie estaba bien',
       not any('bien' in t['texto'].lower() or 'correct' in t['texto'].lower()
               for t in r.json()['tipos']))

    ck('se puede reportar "no hay ave"',
       c.post('/dispositivos/5555/reportes',
              json={'ruta': ruta, 'tipo': 'sin_ave'}, headers=tok
              ).status_code == 200)
    ck('un tipo inventado se rechaza',
       c.post('/dispositivos/5555/reportes',
              json={'ruta': ruta, 'tipo': 'la_pegaste'}, headers=tok
              ).status_code == 400)
    ck('"se cual es" sin especie se rechaza',
       c.post('/dispositivos/5555/reportes',
              json={'ruta': ruta, 'tipo': 'otra_conocida'}, headers=tok
              ).status_code == 400)
    ck('con especie si se acepta',
       c.post('/dispositivos/5555/reportes',
              json={'ruta': ruta, 'tipo': 'otra_conocida',
                    'especie_sugerida': 'rufhor2'}, headers=tok
              ).status_code == 200)
    ck('una ruta de otro dispositivo se rechaza',
       c.post('/dispositivos/5555/reportes',
              json={'ruta': 'Tector 9/Detecciones/x.mp3', 'tipo': 'sin_ave'},
              headers=tok).status_code == 400)
    ck('no se puede reportar en un Tector ajeno',
       c.post('/dispositivos/5555/reportes',
              json={'ruta': ruta, 'tipo': 'sin_ave'}, headers=tok2
              ).status_code == 404)

    rs = c.get('/reportes', headers=tok).json()['reportes']
    ck('los reportes quedan guardados', len(rs) == 2)  # los rechazados no se guardan
    uno = [x for x in rs if x['tipo'] == 'otra_conocida'][0]
    ck('el reporte copia la especie del nombre de archivo',
       uno['especie_detectada'] == 'Rufous Hornero')
    ck('y la confianza', uno['confianza'] == 92)
    ck('y la fecha y hora', uno['fecha_deteccion'] == '2026-09-10 09:52:26')
    ck('la otra cuenta no ve estos reportes',
       len(c.get('/reportes', headers=tok2).json()['reportes']) == 0)

    print('\n-- nombres de archivo de detecciones --')
    from servidor.drive import _parsear_nombre
    viejo = _parsear_nombre('Rufous_Hornero-92-2026-09-09-birdnet-09:52:26.mp3')
    nuevo = _parsear_nombre('Rufous_Hornero-92-2026-09-09-tectornet-09:52:26.mp3')
    ck('se parsea el nombre viejo (birdnet)', viejo is not None)
    ck('se parsea el nombre nuevo (tectornet)', nuevo is not None)
    ck('los dos dan lo mismo', viejo == nuevo)
    ck('especie con guion bajo -> espacio', viejo['especie'] == 'Rufous Hornero')
    ck('confianza como entero', viejo['confianza'] == 92)
    ck('un nombre que no sigue el patron se descarta',
       _parsear_nombre('cualquier_cosa.mp3') is None)

    print()
    if fallos:
        print(f'{len(fallos)} prueba(s) fallaron:')
        for f in fallos:
            print(f'  - {f}')
        return 1
    print('Todas las pruebas pasaron.')
    return 0


if __name__ == '__main__':
    sys.exit(main_pruebas())
