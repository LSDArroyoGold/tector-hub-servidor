#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Copia la base a Google Drive.

QUÉ SE PIERDE SI NO ESTÁ
------------------------
La base tiene las cuentas (con sus contraseñas hasheadas), qué Tector es de
quién, y todos los reportes de error. Nada de eso está en ningún otro lado:
las detecciones y los logs viven en Drive y se pueden volver a leer, pero
esto no. El "servidor" es un teléfono con una tarjeta microSD, y las microSD
se corrompen.

POR QUÉ NO ES UN `cp`
---------------------
SQLite corre en modo WAL (ver db.py): las escrituras recientes están en un
archivo `-wal` aparte y todavía no en el `.db`. Copiar el `.db` con cp
mientras el servidor atiende un pedido da un archivo o incompleto o
directamente corrupto, y encima el error no aparece hasta el día que hace
falta restaurarlo.

Se usa la API de respaldo en caliente de SQLite (`Connection.backup`), que es
justamente para esto: saca una copia consistente sin frenar al servidor y sin
importar qué haya en el WAL.

USO
---
    python3 -m scripts.respaldar

Pensado para el cron de la Debian del teléfono, una vez por día:

    17 3 * * *  cd /opt/tector-hub-servidor && .venv/bin/python -m scripts.respaldar

Se guardan las últimas TECTOR_RESPALDO_COPIAS (14) en Drive, con la fecha en
el nombre.
Rotar importa: una corrupción silenciosa que se respalda todos los días pisa
la única copia buena si hay una sola.

RESTAURAR
---------
Bajar el archivo de Drive, parar el servicio, y ponerlo en su lugar:

    systemctl stop tector-hub
    rclone copy "<remoto>:Tector Hub/respaldos/tector_hub-2026-09-11.db" datos/
    mv datos/tector_hub-2026-09-11.db datos/tector_hub.db
    systemctl start tector-hub
"""
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import config  # noqa: E402

def remoto():
    """El remoto al que van los respaldos.

    Por defecto el mismo que los datos, pero lo esperable es que sea otro:
    un respaldo guardado en la misma cuenta que los datos se pierde junto con
    la cuenta. Se configura con TECTOR_RESPALDO_REMOTE.
    """
    return config.RESPALDO_REMOTE or config.RCLONE_REMOTE


def rclone(*args):
    orden = [config.RCLONE_BIN, '--config', config.RCLONE_CONFIG, *args]
    try:
        return subprocess.run(orden, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        # Que falte rclone es un problema de instalacion, no un error del
        # respaldo: hay que decirlo con todas las letras y no con un rastro
        # de pila de 20 lineas.
        print(f'No se encontró rclone en {config.RCLONE_BIN!r}. '
              'Instalalo o configurá TECTOR_RCLONE.', file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print('rclone no respondió en 5 minutos.', file=sys.stderr)
        sys.exit(1)


def main():
    if not config.RUTA_DB.exists():
        print(f'No existe {config.RUTA_DB}. ¿Arrancó alguna vez el servidor?',
              file=sys.stderr)
        return 1

    nombre = f'tector_hub-{date.today().isoformat()}.db'
    tmp = Path(tempfile.mkdtemp(prefix='respaldo-')) / nombre

    # Copia consistente en caliente. sqlite3.connect sobre el destino y
    # origen.backup(destino) hace el trabajo: bloquea lo mínimo, respeta el
    # WAL, y si el servidor escribe en el medio, reintenta esa página.
    origen = sqlite3.connect(f'file:{config.RUTA_DB}?mode=ro', uri=True)
    destino = sqlite3.connect(tmp)
    try:
        origen.backup(destino)
    finally:
        destino.close()
        origen.close()

    # Verificar antes de subir: un respaldo corrupto que se sube igual es
    # peor que no tener respaldo, porque uno cree que está cubierto.
    chequeo = sqlite3.connect(tmp)
    try:
        estado = chequeo.execute('PRAGMA integrity_check').fetchone()[0]
        cuentas = chequeo.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0]
        reportes = chequeo.execute('SELECT COUNT(*) FROM reportes').fetchone()[0]
    finally:
        chequeo.close()

    if estado != 'ok':
        print(f'La copia no pasó integrity_check: {estado}', file=sys.stderr)
        return 1

    r = rclone('copy', str(tmp), f'{remoto()}:{config.RESPALDO_CARPETA}/')
    if r.returncode != 0:
        print(f'No se pudo subir a Drive: {r.stderr.strip()}', file=sys.stderr)
        return 1

    print(f'{nombre} -> {remoto()}:{config.RESPALDO_CARPETA}/  '
          f'({tmp.stat().st_size // 1024} KB, {cuentas} cuentas, '
          f'{reportes} reportes)')

    # Rotación. Se listan los respaldos y se borran los más viejos: el nombre
    # lleva la fecha ISO, así que ordenar por nombre es ordenar por fecha.
    listado = rclone('lsf', f'{remoto()}:{config.RESPALDO_CARPETA}/')
    if listado.returncode == 0:
        copias = sorted(l for l in listado.stdout.split('\n')
                        if l.startswith('tector_hub-') and l.endswith('.db'))
        for vieja in copias[:-config.RESPALDO_COPIAS]:
            rclone('deletefile',
                   f'{remoto()}:{config.RESPALDO_CARPETA}/{vieja}')
            print(f'  borrada la copia vieja {vieja}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
