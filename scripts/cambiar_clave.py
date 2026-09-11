#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cambia la contraseña de una cuenta.

    python3 -m scripts.cambiar_clave lsd "nueva clave"

Las sesiones ya abiertas en los telefonos siguen valiendo: el token de la app
no depende de la contraseña, solo se pide al entrar. Si lo que se quiere es
sacar a alguien, ademas hay que rotar TECTOR_CLAVE_JWT y reiniciar.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servidor import auth, db  # noqa: E402


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    usuario, clave = sys.argv[1], sys.argv[2]
    if len(clave) < 6:
        print('Muy corta: seis caracteres como minimo.', file=sys.stderr)
        return 2
    db.inicializar()
    with db.sesion() as con:
        cur = con.execute('UPDATE usuarios SET hash_clave = ? WHERE usuario = ?',
                          (auth.hashear(clave), usuario))
    if cur.rowcount == 0:
        print(f'No existe la cuenta {usuario}.', file=sys.stderr)
        return 1
    print(f'Contraseña de {usuario} cambiada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
