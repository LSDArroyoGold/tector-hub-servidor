#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carga un numero de serie a mano.

DOS USOS
--------
1. Precargar un serie esperado, para el modo TECTOR_SOLO_SERIES_CONOCIDOS.
2. Con --heredado, dar de alta un Tector con software VIEJO (LSD-Tector1.1),
   que no sabe registrarse solo porque esa version no tiene numero de serie
   ni conoce al servidor.

   Un 1.1 igual funciona bastante bien en la app sin tocarle nada al equipo:
   sube sus detecciones a "<carpeta>/Detecciones" con el mismo formato de
   nombre, y baja config_horarios.txt de Drive al cerrar ventana, asi que se
   pueden ver los cantos, las estadisticas, y hasta cambiarle los horarios.
   Lo unico que no hay es estado.json --no existe en esa version-- asi que la
   app no puede decir si esta grabando ahora mismo, y lo muestra distinto.

       python3 -m scripts.precargar_serie 0001 --heredado --drive-path "Tector 1"

SOBRE EL PRIMER USO
-------------------
Con TECTOR_SOLO_SERIES_CONOCIDOS activo, el servidor solo acepta el registro
de dispositivos cuyo numero ya este en la base. El flujo es: se corre
install.sh en el Tector, que imprime su numero; se precarga ese numero aca;
recien entonces el dispositivo puede registrarse.

    python3 -m scripts.precargar_serie 4417
    python3 -m scripts.precargar_serie 4417 --drive-path "Tector 2"

El id_hardware queda como un marcador "reservado:" hasta que el equipo se
registre de verdad; ahi el registro real lo reemplaza. Con --heredado el
marcador es "heredado:" y NO se reemplaza nunca: no hay ningun equipo que se
vaya a registrar.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import config, db  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description='Precarga un numero de serie esperado.')
    parser.add_argument('serie', help='Numero de 4 digitos que imprimio install.sh')
    parser.add_argument('--drive-path', help='Carpeta del equipo en Drive '
                                             '(ej: "Tector 1")')
    parser.add_argument('--heredado', action='store_true',
                        help='Para un Tector con software viejo (1.1), que no '
                             'se registra solo. Queda cargado de forma '
                             'definitiva y ningún equipo nuevo puede tomarlo.')
    args = parser.parse_args()

    serie = args.serie.strip()
    if not (serie.isdigit() and len(serie) == 4):
        print('El numero de serie tiene que ser de 4 digitos.', file=sys.stderr)
        return 2

    db.inicializar()
    try:
        with db.sesion() as con:
            con.execute(
                'INSERT INTO dispositivos (serie, id_hardware, primer_registro, '
                'ultimo_visto, drive_path) VALUES (?, ?, ?, ?, ?)',
                (serie, f'{"heredado" if args.heredado else "reservado"}:{serie}',
                 db.ahora(), db.ahora(), args.drive_path))
    except sqlite3.IntegrityError:
        print(f'El numero {serie} ya estaba cargado.', file=sys.stderr)
        return 1

    print(f'Serie {serie} cargado en {config.RUTA_DB}')
    if args.heredado:
        print('Marcado como heredado: no espera registro del dispositivo.')
        print('Vinculalo desde la app con ese número de serie.')
    else:
        print('Ya puede registrarse desde el dispositivo.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
