#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Un rclone de mentira, que trabaja contra una carpeta local.

PARA QUÉ
--------
Todo lo que el servidor sabe de un Tector lo saca de Drive, y hasta ahora eso
no estaba cubierto por ninguna prueba: probar_api.py usa TestClient y nunca
toca `drive.py`. O sea que el parseo de nombres de archivo, el armado del zip,
la escritura de horarios y la lectura de `estado.json` sólo se habían
ejercitado a mano.

Esto reemplaza al binario de rclone por algo que hace lo mismo sobre una
carpeta del disco. Con eso se puede levantar el servidor de verdad, con
uvicorn, y recorrerlo entero sin depender de una cuenta de Google ni de tener
internet.

No pretende ser rclone: implementa sólo los subcomandos que usa el proyecto,
con el formato de salida que el proyecto espera.

    TECTOR_RCLONE=<ruta a rclone.bat>   (que llama a este script)
    TECTOR_DRIVE_FALSO=<carpeta que hace de Drive>
"""
import json
import os
import shutil
import sys
from pathlib import Path

RAIZ = Path(os.environ.get('TECTOR_DRIVE_FALSO', '')).resolve()

# Los nombres de archivo reales llevan la hora con dos puntos
# (Rufous_Hornero-92-2026-09-09-tectornet-09:52:26.mp3). En Linux y en Drive
# eso es válido; en Windows no se puede crear un archivo con ":" en el
# nombre. Para poder correr esta prueba en Windows SIN cambiar el formato que
# se prueba --que es justo lo que interesa verificar-- en el disco se guarda
# como %3A y se traduce en la frontera.
EN_DISCO, DE_VERDAD = '%3A', ':'


def a_disco(ruta):
    return ruta.replace(DE_VERDAD, EN_DISCO)


def a_verdad(ruta):
    return ruta.replace(EN_DISCO, DE_VERDAD)


def es_remoto(x):
    """¿Es 'gdrive:carpeta/x' o una ruta local?

    En Windows una ruta absoluta también tiene dos puntos (la unidad), así
    que no alcanza con buscar ':'. Un remoto de rclone tiene el nombre antes
    de los dos puntos, y ese nombre nunca es una sola letra ni trae barras.
    """
    if ':' not in x:
        return False
    nombre = x.split(':', 1)[0]
    return len(nombre) > 1 and os.sep not in nombre and '/' not in nombre


def local(remoto):
    """'gdrive:Tector 1/x.mp3' -> <RAIZ>/Tector 1/x.mp3

    Ojo: el propio remoto lleva un ":" que separa el nombre del remoto de la
    ruta, así que primero se corta por ahí y recién después se traduce.
    """
    ruta = remoto.split(':', 1)[1] if ':' in remoto else remoto
    return RAIZ / a_disco(ruta)


def lsjson(args):
    recursivo = '--recursive' in args
    solo_dirs = '--dirs-only' in args
    destino = local([a for a in args if not a.startswith('--')][0])
    if not destino.exists():
        print('directory not found', file=sys.stderr)
        return 3

    entradas = []
    it = destino.rglob('*') if recursivo else destino.iterdir()
    for p in sorted(it):
        if solo_dirs and not p.is_dir():
            continue
        if recursivo and p.is_dir():
            continue
        # a_verdad: en el disco los nombres llevan %3A donde el nombre real
        # tiene ":". El servidor tiene que ver el nombre real, que es lo que
        # su expresión regular espera parsear.
        entradas.append({
            'Path': a_verdad(str(p.relative_to(destino)).replace(os.sep, '/')),
            'Name': a_verdad(p.name),
            'Size': p.stat().st_size if p.is_file() else -1,
            'IsDir': p.is_dir(),
        })
    print(json.dumps(entradas))
    return 0


def main():
    args = [a for a in sys.argv[1:]]
    # El servidor siempre pasa --config; acá no significa nada.
    if '--config' in args:
        i = args.index('--config')
        del args[i:i + 2]
    if not args:
        return 1

    cmd, resto = args[0], args[1:]

    if cmd == 'cat':
        p = local(resto[0])
        if not p.exists():
            print('file not found', file=sys.stderr)
            return 3
        sys.stdout.buffer.write(p.read_bytes())
        return 0

    if cmd == 'rcat':
        p = local(resto[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(sys.stdin.buffer.read())
        return 0

    if cmd == 'lsjson':
        return lsjson(resto)

    if cmd == 'lsf':
        d = local(resto[0])
        if d.exists():
            for p in sorted(d.iterdir()):
                print(a_verdad(p.name) + ('/' if p.is_dir() else ''))
        return 0

    if cmd in ('copy', 'copyto'):
        origen, destino = resto[0], resto[1]
        o = local(origen) if es_remoto(origen) else Path(origen)
        d = local(destino) if es_remoto(destino) else Path(destino)
        if cmd == 'copy':
            d.mkdir(parents=True, exist_ok=True)
            d = d / o.name
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
        if not o.exists():
            print('source not found', file=sys.stderr)
            return 3
        shutil.copy2(o, d)
        return 0

    if cmd == 'deletefile':
        p = local(resto[0])
        if p.exists():
            p.unlink()
        return 0

    print(f'rclone_falso: subcomando no implementado: {cmd}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
