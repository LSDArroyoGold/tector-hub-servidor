#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Resuelve por adelantado la foto de cada especie del catálogo.

POR QUÉ HACE FALTA
------------------
servidor/especies.py ya resuelve cualquier especie sola, la primera vez que
alguien la pide, y la cachea para siempre. O sea que la app nunca se queda
sin fotos: no hay una lista de especies "soportadas".

Lo que sí pasa sin esto es que la PRIMERA vez que aparece una especie nueva,
esa pantalla tarda uno o dos segundos más mientras se consulta Wikimedia. En
una salida de campo, con señal mala, se nota. Este script se come esa espera
de antemano.

CUÁNTO TARDA
------------
El catálogo de BirdSet tiene ~6300 especies con nombre. Cada una son dos
consultas a la API de Wikimedia más la descarga de la imagen, con una pausa
entre medio para no golpear el servicio. Calculá varias horas, y unos cuantos
GB de caché en disco.

No hace falta correrlo entero. Lo razonable es empezar por las especies que
la región realmente tiene:

    python3 -m herramientas.precalentar_fotos --archivo especies_amba.txt
    python3 -m herramientas.precalentar_fotos --limite 200
    python3 -m herramientas.precalentar_fotos            # todo el catálogo

Se puede cortar con Ctrl-C y volver a arrancar cuando sea: lo ya resuelto
queda en el caché y se saltea. Es reanudable por diseño, porque una corrida
de varias horas se corta.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import especies  # noqa: E402

BASE = 'https://raw.githubusercontent.com/LSDArroyoGold/TectorNet/master/modelo'

# Pausa entre especies. Wikimedia no publica un límite duro para lecturas,
# pero pide no paralelizar y mandar un User-Agent que identifique quién
# consulta (especies.py lo manda). Medio segundo es de sobra amable y aun así
# permite terminar el catálogo en una noche.
PAUSA_S = 0.5


def catalogo():
    """Los nombres comunes en inglés del vocabulario de BirdSet.

    Mismo criterio que herramientas/generar_catalogo.py de la app: BirdSet es
    el filtro final del pipeline, así que su vocabulario es el límite de lo
    que el sistema puede llegar a decir.
    """
    def bajar(u):
        with urllib.request.urlopen(u, timeout=60) as r:
            return json.loads(r.read().decode('utf-8'))

    bs = bajar(f'{BASE}/birdset_efficientnetb1_config.json')
    tax = bajar(f'{BASE}/eBird_taxonomy_codes_2024E.json')
    nombres = []
    for codigo in bs['id2label'].values():
        v = tax.get(codigo)
        if v and '_' in v:
            # especies.py busca en Wikipedia por nombre común, con guiones
            # bajos, igual que viene en el nombre de archivo de una detección.
            nombres.append(v.split('_', 1)[1].replace(' ', '_'))
    return sorted(set(nombres))


def main():
    ap = argparse.ArgumentParser(
        description='Precarga el caché de fotos de especie.')
    ap.add_argument('--limite', type=int, help='Cuántas especies procesar')
    ap.add_argument('--archivo', help='Un nombre común en inglés por línea, '
                                      'en vez del catálogo entero')
    ap.add_argument('--pausa', type=float, default=PAUSA_S,
                    help=f'Segundos entre especies (por defecto {PAUSA_S})')
    args = ap.parse_args()

    if args.archivo:
        nombres = [l.strip().replace(' ', '_')
                   for l in Path(args.archivo).read_text(encoding='utf-8').splitlines()
                   if l.strip() and not l.startswith('#')]
    else:
        print('Bajando el catálogo de BirdSet…')
        nombres = catalogo()

    if args.limite:
        nombres = nombres[:args.limite]

    print(f'{len(nombres)} especies. Caché en {especies.CACHE}')
    print('Se puede cortar con Ctrl-C: al volver, sigue donde quedó.\n')

    con_foto = sin_foto = ya_estaban = fallaron = 0
    t0 = time.time()
    try:
        for i, nombre in enumerate(nombres, 1):
            ficha = especies.CACHE / f'{especies._clave(nombre)}.json'
            if ficha.exists():
                ya_estaban += 1
                continue

            datos = especies.resolver(nombre)
            if datos is None:
                fallaron += 1          # fallo de red: no se cachea, se reintenta
            elif datos.get('imagen'):
                especies.foto(nombre)  # baja y guarda el archivo
                con_foto += 1
            else:
                sin_foto += 1

            if i % 25 == 0 or i == len(nombres):
                falta = (len(nombres) - i) * (time.time() - t0) / max(i, 1)
                print(f'  {i}/{len(nombres)}  con foto {con_foto}  '
                      f'sin foto {sin_foto}  ya estaban {ya_estaban}  '
                      f'fallaron {fallaron}  '
                      f'(faltan ~{falta / 60:.0f} min)')
            time.sleep(args.pausa)
    except KeyboardInterrupt:
        print('\nCortado. Lo resuelto ya quedó en el caché.')

    print(f'\ncon foto: {con_foto}   sin foto: {sin_foto}   '
          f'ya estaban: {ya_estaban}   fallaron: {fallaron}')
    if sin_foto:
        print('Las "sin foto" son especies sin imagen en Wikipedia, o cuya '
              'licencia no está en la lista blanca. Se cachean como negativo '
              'y no se vuelven a consultar.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
