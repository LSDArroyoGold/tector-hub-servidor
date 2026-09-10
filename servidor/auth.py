# -*- coding: utf-8 -*-
"""
Autenticacion: hash de contraseñas y tokens.

La especificacion pide una lista de usuarios en un servidor propio, sin
autenticacion de terceros. Eso es lo que hay aca, con tres cuidados que no
son opcionales cuando uno guarda contraseñas ajenas:

1. Nunca se guarda la contraseña, solo un hash bcrypt con sal. Si la base se
   filtra, no se filtran las claves.
2. La contraseña pasa antes por sha256+base64. bcrypt TRUNCA en silencio a
   los 72 bytes: sin este paso, dos contraseñas largas que compartan los
   primeros 72 caracteres serian la misma para el sistema. Es el mismo
   esquema que passlib llama bcrypt_sha256.
3. Un login con usuario inexistente tambien corre el hash, contra uno falso.
   Sin eso responde mucho mas rapido que uno con usuario valido y clave mala,
   y esa diferencia de tiempo alcanza para enumerar quien tiene cuenta.

Se usa bcrypt directo y no passlib a proposito: passlib esta sin mantener
desde 2020 y su deteccion de version rompe con bcrypt 4.x (tira
"error reading bcrypt version" en cada arranque). Lo unico que se usaba de
passlib era hash y verify, que son dos lineas.
"""
import base64
import hashlib
import time

import bcrypt
import jwt

from . import config, db

ALGORITMO = 'HS256'

# Costo del bcrypt. 12 son ~250 ms en hardware modesto: suficiente para que
# probar claves a lo bruto no sirva, sin que el login se sienta lento.
RONDAS = 12


def _preparar(clave):
    """sha256 + base64, para no depender del limite de 72 bytes de bcrypt.

    Se usa base64 y no el digest crudo porque bcrypt corta en el primer byte
    nulo, y un sha256 binario puede tener uno en el medio.
    """
    return base64.b64encode(hashlib.sha256(clave.encode('utf-8')).digest())


def hashear(clave):
    return bcrypt.hashpw(_preparar(clave), bcrypt.gensalt(RONDAS)).decode('ascii')


def _verificar(clave, hash_guardado):
    try:
        return bcrypt.checkpw(_preparar(clave), hash_guardado.encode('ascii'))
    except (ValueError, TypeError):
        # Hash con formato invalido en la base. No es motivo para tirar un
        # 500: es un login fallido como cualquier otro.
        return False


# Hash de descarte, para gastar el mismo tiempo cuando el usuario no existe.
# Se calcula una sola vez, al importar.
_HASH_FALSO = hashear('no-existe-este-usuario')


def verificar_credenciales(usuario, clave):
    """El usuario si las credenciales son validas, None si no.

    Devuelve None por las dos razones posibles (no existe / clave mala) sin
    distinguirlas: quien pregunta desde afuera no tiene por que saber cual de
    las dos fue.
    """
    registro = db.usuario_por_nombre(usuario)
    if registro is None:
        _verificar(clave, _HASH_FALSO)
        return None
    if not _verificar(clave, registro['hash_clave']):
        return None
    return registro


def emitir_token(usuario_id):
    ahora = int(time.time())
    return jwt.encode(
        {
            'sub': str(usuario_id),
            'iat': ahora,
            'exp': ahora + config.HORAS_TOKEN * 3600,
        },
        config.CLAVE_JWT, algorithm=ALGORITMO)


def leer_token(token):
    """El id de usuario, o None si el token no sirve (invalido o vencido)."""
    try:
        datos = jwt.decode(token, config.CLAVE_JWT, algorithms=[ALGORITMO])
    except jwt.PyJWTError:
        return None
    try:
        return int(datos['sub'])
    except (KeyError, TypeError, ValueError):
        return None
