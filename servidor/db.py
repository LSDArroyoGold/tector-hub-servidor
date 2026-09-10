# -*- coding: utf-8 -*-
"""
Esquema y acceso a la base. SQLite, con la conexion por request.

Tres tablas y nada mas:

  usuarios      quien puede entrar a la app
  dispositivos  que Tectors existen, y con que hardware esta atado cada serie
  vinculos      que usuario ve que dispositivo, y con que apodo

La separacion entre 'dispositivos' y 'vinculos' es a proposito: un Tector
existe (y puede registrarse) sin pertenecerle a nadie. Es el estado en que
queda un equipo recien instalado hasta que alguien lo reclama desde la app.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

ESQUEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS usuarios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nombre       TEXT NOT NULL,
    hash_clave   TEXT NOT NULL,
    activo       INTEGER NOT NULL DEFAULT 1,
    creado       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositivos (
    serie           TEXT PRIMARY KEY,
    -- UNIQUE es lo que hace posible resolver colisiones: si el mismo
    -- hardware vuelve a registrarse, se lo reconoce en vez de darle un
    -- numero nuevo cada vez.
    id_hardware     TEXT NOT NULL UNIQUE,
    primer_registro TEXT NOT NULL,
    ultimo_visto    TEXT NOT NULL,
    drive_path      TEXT
);

CREATE TABLE IF NOT EXISTS vinculos (
    -- Un dispositivo pertenece a una sola cuenta. Si mas adelante hace falta
    -- compartir un Tector entre varias personas, sacar este PRIMARY KEY y
    -- poner uno compuesto (serie, usuario_id) -- el resto del codigo ya
    -- consulta por las dos columnas.
    serie       TEXT PRIMARY KEY REFERENCES dispositivos(serie) ON DELETE CASCADE,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    apodo       TEXT,
    vinculado   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vinculos_usuario ON vinculos(usuario_id);
"""


def ahora():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def conectar():
    config.RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.RUTA_DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


@contextmanager
def sesion():
    con = conectar()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar():
    with sesion() as con:
        con.executescript(ESQUEMA)


# ---------- dispositivos ----------

def serie_libre(con, propuesto):
    """Devuelve un numero de serie libre.

    Arranca por el propuesto. Si esta tomado, sondea de forma DETERMINISTICA
    (+1 modulo 10000) en vez de al azar: si dos equipos colisionan y se
    registran casi al mismo tiempo, el orden de llegada decide, y el
    resultado es reproducible al depurar.

    Con la base llena (10000 dispositivos) esto no termina nunca, asi que se
    corta y se avisa -- pero llegado ese punto el problema real es que 4
    digitos quedaron chicos para la red.
    """
    numero = int(propuesto)
    for _ in range(10000):
        candidato = f'{numero % 10000:04d}'
        existe = con.execute(
            'SELECT 1 FROM dispositivos WHERE serie = ?', (candidato,)
        ).fetchone()
        if not existe:
            return candidato
        numero += 1
    raise RuntimeError('No quedan numeros de serie libres')


def registrar_dispositivo(serie_propuesto, id_hardware):
    """(estado, serie). estado es 'ok' o 'reasignado'."""
    with sesion() as con:
        ya = con.execute(
            'SELECT serie FROM dispositivos WHERE id_hardware = ?',
            (id_hardware,)
        ).fetchone()

        if ya:
            # Hardware conocido. Su numero es el que ya tenia, sin importar
            # que proponga ahora -- esto es lo que hace idempotente al
            # registro y lo que evita que un equipo cambie de identidad cada
            # vez que arranca.
            con.execute('UPDATE dispositivos SET ultimo_visto = ? WHERE serie = ?',
                        (ahora(), ya['serie']))
            estado = 'ok' if ya['serie'] == serie_propuesto else 'reasignado'
            return estado, ya['serie']

        # Una fila 'reservado:' es una precarga hecha a mano con
        # scripts/precargar_serie.py, esperando justamente a este equipo. No
        # es una colision: es su lugar guardado. Sin este caso, precargar un
        # numero provocaria que el dispositivo real se lo encuentre ocupado y
        # el servidor le asigne otro -- exactamente lo contrario de lo que la
        # precarga quiere lograr.
        reservada = con.execute(
            'SELECT id_hardware FROM dispositivos WHERE serie = ?',
            (serie_propuesto,)).fetchone()
        if reservada and reservada['id_hardware'] == f'reservado:{serie_propuesto}':
            con.execute(
                'UPDATE dispositivos SET id_hardware = ?, primer_registro = ?, '
                'ultimo_visto = ? WHERE serie = ?',
                (id_hardware, ahora(), ahora(), serie_propuesto))
            return 'ok', serie_propuesto

        serie = serie_libre(con, serie_propuesto)
        con.execute(
            'INSERT INTO dispositivos (serie, id_hardware, primer_registro, '
            'ultimo_visto) VALUES (?, ?, ?, ?)',
            (serie, id_hardware, ahora(), ahora()))
        return ('ok' if serie == serie_propuesto else 'reasignado'), serie


def dispositivos_de(usuario_id):
    with sesion() as con:
        filas = con.execute(
            'SELECT d.serie, d.drive_path, d.ultimo_visto, d.primer_registro, '
            '       v.apodo, v.vinculado '
            'FROM vinculos v JOIN dispositivos d ON d.serie = v.serie '
            'WHERE v.usuario_id = ? ORDER BY v.vinculado',
            (usuario_id,)).fetchall()
        return [dict(f) for f in filas]


def dispositivo_del_usuario(usuario_id, serie):
    """El dispositivo, solo si le pertenece a ese usuario. None si no.

    Todas las rutas que tocan datos de un Tector pasan por aca: es el unico
    punto donde se decide si alguien puede ver algo.
    """
    with sesion() as con:
        fila = con.execute(
            'SELECT d.serie, d.drive_path, d.ultimo_visto, v.apodo '
            'FROM vinculos v JOIN dispositivos d ON d.serie = v.serie '
            'WHERE v.usuario_id = ? AND v.serie = ?',
            (usuario_id, serie)).fetchone()
        return dict(fila) if fila else None


def vincular(usuario_id, serie, apodo=None):
    """('ok'|'inexistente'|'tomado', dispositivo|None)."""
    with sesion() as con:
        existe = con.execute('SELECT 1 FROM dispositivos WHERE serie = ?',
                             (serie,)).fetchone()
        if not existe:
            return 'inexistente', None

        duenio = con.execute('SELECT usuario_id FROM vinculos WHERE serie = ?',
                             (serie,)).fetchone()
        if duenio and duenio['usuario_id'] != usuario_id:
            return 'tomado', None
        if duenio:
            return 'ok', {'serie': serie, 'apodo': apodo}

        con.execute(
            'INSERT INTO vinculos (serie, usuario_id, apodo, vinculado) '
            'VALUES (?, ?, ?, ?)', (serie, usuario_id, apodo, ahora()))
        return 'ok', {'serie': serie, 'apodo': apodo}


def desvincular(usuario_id, serie):
    with sesion() as con:
        cur = con.execute('DELETE FROM vinculos WHERE serie = ? AND usuario_id = ?',
                          (serie, usuario_id))
        return cur.rowcount > 0


def renombrar(usuario_id, serie, apodo):
    with sesion() as con:
        cur = con.execute(
            'UPDATE vinculos SET apodo = ? WHERE serie = ? AND usuario_id = ?',
            (apodo, serie, usuario_id))
        return cur.rowcount > 0


def fijar_drive_path(serie, drive_path):
    with sesion() as con:
        con.execute('UPDATE dispositivos SET drive_path = ? WHERE serie = ?',
                    (drive_path, serie))


# ---------- usuarios ----------

def usuario_por_nombre(usuario):
    with sesion() as con:
        fila = con.execute('SELECT * FROM usuarios WHERE usuario = ? AND activo = 1',
                           (usuario,)).fetchone()
        return dict(fila) if fila else None


def usuario_por_id(uid):
    with sesion() as con:
        fila = con.execute('SELECT * FROM usuarios WHERE id = ? AND activo = 1',
                           (uid,)).fetchone()
        return dict(fila) if fila else None


def crear_usuario(usuario, nombre, hash_clave):
    with sesion() as con:
        cur = con.execute(
            'INSERT INTO usuarios (usuario, nombre, hash_clave, creado) '
            'VALUES (?, ?, ?, ?)', (usuario, nombre, hash_clave, ahora()))
        return cur.lastrowid
