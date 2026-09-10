#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Precarga un numero de serie esperado, para el modo TECTOR_SOLO_SERIES_CONOCIDOS.

Con ese modo activo, el servidor solo acepta el registro de dispositivos cuyo
numero ya este en la base. Sirve cuando el servidor esta expuesto a internet
abierta y no alcanza con que registrarse no de acceso a nada.

El flujo es: se corre install.sh en el Tector, que imprime su numero; se
precarga ese numero aca; recien entonces el dispositivo puede registrarse.

Uso:
    python3 -m scripts.precargar_serie 4417
    python3 -m scripts.precargar_serie 4417 --drive-path "Tector 1"

El id_hardware queda como un marcador reservado hasta que el equipo se
registre de verdad; ahi el registro real lo reemplaza.
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
                (serie, f'reservado:{serie}', db.ahora(), db.ahora(),
                 args.drive_path))
    except sqlite3.IntegrityError:
        print(f'El numero {serie} ya estaba cargado.', file=sys.stderr)
        return 1

    print(f'Serie {serie} precargado en {config.RUTA_DB}')
    print('Ya puede registrarse desde el dispositivo.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
