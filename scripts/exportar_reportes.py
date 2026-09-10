#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vuelca los reportes de error a un CSV.

Es la contraparte del botón de reportar de la app: la gente marca desde el
teléfono que una detección estaba mal, y esto los saca todos juntos para
poder mirarlos.

Por qué un CSV y no una pantalla en la app: revisar reportes es trabajo de
laboratorio, no de campo. Se cruza con los audios, se escuchan de a decenas,
y termina en una planilla o en un notebook. Una pantalla en el teléfono sería
peor para eso, y nadie la pidió.

    python3 -m scripts.exportar_reportes                 # a la pantalla
    python3 -m scripts.exportar_reportes reportes.csv    # a un archivo

La columna 'ruta' es la del audio en Drive, así que se puede volver a
escuchar cada caso --mientras la retención no lo haya borrado, por eso la
especie, la confianza y la fecha se guardan aparte en cada reporte.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import db  # noqa: E402

COLUMNAS = ['id', 'creado', 'usuario', 'serie', 'tipo', 'especie_detectada',
            'confianza', 'especie_sugerida', 'fecha_deteccion', 'comentario',
            'ruta']


def main():
    db.inicializar()
    filas = db.todos_los_reportes()
    if not filas:
        print('Todavía no hay ningún reporte.', file=sys.stderr)
        return 1

    destino = sys.argv[1] if len(sys.argv) > 1 else None
    salida = open(destino, 'w', newline='', encoding='utf-8-sig') if destino \
        else sys.stdout
    # utf-8-sig para el archivo: sin el BOM, Excel en Windows abre los
    # nombres con acentos rotos, y estas planillas se abren en Excel.

    try:
        w = csv.DictWriter(salida, fieldnames=COLUMNAS, extrasaction='ignore')
        w.writeheader()
        w.writerows(filas)
    finally:
        if destino:
            salida.close()

    if destino:
        print(f'{len(filas)} reportes en {destino}')
        # Un resumen rapido, que es lo primero que uno quiere saber.
        por_tipo = {}
        for f in filas:
            por_tipo[f['tipo']] = por_tipo.get(f['tipo'], 0) + 1
        for tipo, n in sorted(por_tipo.items(), key=lambda x: -x[1]):
            print(f'  {n:>4}  {tipo}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
