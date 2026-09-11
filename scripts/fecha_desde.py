#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fija el piso de fecha de un Tector: nada anterior se muestra ni se cuenta.

POR QUE EXISTE
--------------
Un equipo puede tener detecciones que no valen: el modelo a medio instalar,
el microfono mal puesto, el Tector todavia en el banco de trabajo. Contarlas
ensucia las estadisticas y no hay forma de distinguirlas mirando los archivos.

El caso que lo motivo: el Tector 1 quedo bien instalado el 8/9/2026 y lo
anterior no es confiable.

POR QUE ESTO Y NO BORRAR LAS CARPETAS DE DRIVE
----------------------------------------------
Borrar a mano tambien "funciona", pero es irreversible y se lleva el audio.
Si manana se decide que esos dias servian para algo --calibrar, comparar,
material de reentrenamiento-- ya no estan. Asi el dato crudo queda donde
esta y solo se lo deja afuera del analisis, que es reversible con este mismo
comando.

Es POR DISPOSITIVO a proposito: que un equipo tenga historia dudosa no dice
nada de los demas.

Uso:
    python3 -m scripts.fecha_desde 0001 2026-09-08   # fijar
    python3 -m scripts.fecha_desde 0001 --quitar     # volver a mostrar todo
    python3 -m scripts.fecha_desde --listar
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import db  # noqa: E402


def listar():
    with db.sesion() as con:
        filas = con.execute(
            'SELECT serie, drive_path, fecha_desde FROM dispositivos '
            'ORDER BY serie').fetchall()
    if not filas:
        print('No hay dispositivos cargados.')
        return 0
    for f in filas:
        piso = f['fecha_desde'] or '(sin piso: se muestra todo)'
        print(f'  {f["serie"]}  {f["drive_path"] or "-":<20}  {piso}')
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if args[0] == '--listar':
        db.inicializar()
        return listar()

    serie = args[0]
    if not re.fullmatch(r'\d{4}', serie):
        print(f'Serie invalida: {serie} (son 4 digitos)', file=sys.stderr)
        return 2
    if len(args) < 2:
        print('Falta la fecha, o --quitar.', file=sys.stderr)
        return 2

    if args[1] == '--quitar':
        fecha = None
    else:
        fecha = args[1]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fecha):
            print(f'Fecha invalida: {fecha} (formato AAAA-MM-DD)',
                  file=sys.stderr)
            return 2

    db.inicializar()
    if not db.set_fecha_desde(serie, fecha):
        print(f'No existe el dispositivo {serie}.', file=sys.stderr)
        return 1

    if fecha:
        print(f'Tector {serie}: desde ahora solo se muestra y se cuenta lo '
              f'del {fecha} en adelante.')
        print('No se borro nada de Drive. Para deshacer:')
        print(f'    python3 -m scripts.fecha_desde {serie} --quitar')
    else:
        print(f'Tector {serie}: piso quitado, vuelve a mostrarse toda la '
              f'historia.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
