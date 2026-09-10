#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alta de una cuenta de Tector Hub.

Las cuentas las carga el laboratorio a mano, con este script: la app no tiene
registro publico (asi lo pide la especificacion, y ademas evita tener que
resolver verificacion de mail y recuperacion de clave para un sistema de una
decena de usuarios).

Uso:
    python3 -m scripts.crear_usuario d.arroyo "Diego Arroyo"
    python3 -m scripts.crear_usuario d.arroyo "Diego Arroyo" --clave xxxxx

Sin --clave, se genera una al azar y se imprime una sola vez. Es la forma
recomendada: una clave generada no se parece a ninguna otra que la persona
use en otro lado.
"""
import argparse
import secrets
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import auth, config, db  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Crea una cuenta de Tector Hub.')
    parser.add_argument('usuario', help='Nombre de usuario para entrar (ej: d.arroyo)')
    parser.add_argument('nombre', help='Nombre completo, el que muestra la app')
    parser.add_argument('--clave', help='Contraseña. Si se omite, se genera una.')
    args = parser.parse_args()

    db.inicializar()

    clave = args.clave or secrets.token_urlsafe(12)

    try:
        db.crear_usuario(args.usuario, args.nombre, auth.hashear(clave))
    except sqlite3.IntegrityError:
        print(f'Ya existe una cuenta con el usuario "{args.usuario}".',
              file=sys.stderr)
        return 1

    print(f'Cuenta creada en {config.RUTA_DB}')
    print(f'  usuario:    {args.usuario}')
    print(f'  nombre:     {args.nombre}')
    if not args.clave:
        print(f'  contraseña: {clave}')
        print()
        print('  Esta contraseña no se vuelve a mostrar: en la base queda solo')
        print('  su hash. Pasasela a la persona por un canal privado.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
