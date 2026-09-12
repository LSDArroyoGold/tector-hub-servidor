# -*- coding: utf-8 -*-
"""
El catalogo de especies del motor, para traducir codigos a nombres.

La app manda en los reportes el codigo eBird de la especie que la persona
dice que era (por ejemplo `rufhor2`). Sin esto, el servidor guardaba ese
codigo en la base y nada mas: el audio quedaba archivado en Drive bajo la
especie EQUIVOCADA --la que dijo el motor-- y la correccion, que es lo unico
que vale para reentrenar, no aparecia en ningun lado fuera de la base.

El archivo es el mismo contenido que el selector de la app (ver
tector-hub-app/catalogo.js): una linea por especie, `codigo|cientifico|comun`.
"""
from pathlib import Path

_ARCHIVO = Path(__file__).with_name('catalogo.txt')
_por_codigo = None


def _cargar():
    global _por_codigo
    if _por_codigo is not None:
        return
    tabla = {}
    try:
        for linea in _ARCHIVO.read_text(encoding='utf-8').splitlines():
            if not linea or linea.startswith('#'):
                continue
            partes = linea.split('|')
            if len(partes) >= 3:
                tabla[partes[0].strip()] = (partes[2].strip(), partes[1].strip())
    except OSError:
        pass
    _por_codigo = tabla


def nombre(codigo):
    """(nombre comun, nombre cientifico) de un codigo eBird, o (None, None)."""
    if not codigo:
        return None, None
    _cargar()
    return _por_codigo.get(codigo.strip(), (None, None))


def carpeta(codigo):
    """El nombre comun como lo usa el motor en las carpetas de Drive:
    espacios a guion bajo. None si el codigo no esta en el catalogo."""
    comun, _ = nombre(codigo)
    return comun.replace(' ', '_') if comun else None
