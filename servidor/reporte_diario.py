# -*- coding: utf-8 -*-
"""
El reporte diario de un Tector, en texto plano.

POR QUE EN TEXTO
----------------
Es lo que se puede mandar por mail, pegar en un cuaderno de campo, guardar
en Drive o leer en la terminal del laboratorio sin abrir nada. Pesa unos
pocos KB por dia, asi que guardarlo para siempre no cuesta nada, y sirve de
registro aunque la app o el servidor cambien.

Sin fotos a proposito: es un registro, no una vitrina.

QUE LLEVA
---------
Lo del dia (detecciones, especies, tasa por hora grabada, histograma
horario, ranking, especies que aparecen por primera vez en esa estacion) y
un cierre con los ultimos 30 dias para dar contexto. Y el estado del equipo
al momento de generarlo.
"""
from collections import Counter
from datetime import date, datetime

from . import drive


def _hist_texto(por_hora, ancho=24):
    """Histograma horario en texto, una fila por hora con detecciones."""
    if not por_hora:
        return '  (sin detecciones)'
    tope = max(por_hora.values()) or 1
    filas = []
    for h in range(24):
        n = por_hora.get(h, 0)
        if not n:
            continue
        barra = '#' * max(1, round(n / tope * ancho))
        filas.append(f'  {h:02d}h  {barra} {n}')
    return '\n'.join(filas)


def _linea_estado(est):
    if not est:
        return 'Estado: sin reporte del equipo'
    partes = []
    e = est.get('estado')
    if e == 'grabando':
        partes.append('grabando')
    elif e == 'en_espera':
        partes.append('en espera')
    elif e:
        partes.append(e)
    b = est.get('bateria') or {}
    if b.get('porcentaje') is not None:
        partes.append(f'bateria {b["porcentaje"]} %')
    elif b.get('voltaje_v') is not None:
        partes.append(f'bateria {b["voltaje_v"]} V')
    p = (est.get('proxima_ventana') or {}).get('hora')
    if p:
        partes.append(f'proxima ventana {p}')
    return 'Estado: ' + ' · '.join(partes) if partes else 'Estado: -'


def generar(disp, fecha, horas_dia=None):
    """Texto del reporte de `fecha` (ISO) para el dispositivo `disp`
    (fila de la base, con serie, apodo, drive_path, fecha_desde)."""
    ruta = disp['drive_path']
    desde = disp.get('fecha_desde')
    apodo = disp.get('apodo') or f'Tector {disp["serie"]}'

    todas, _ = drive.detecciones_completas(ruta, limite=20000, desde=desde)
    del_dia = [d for d in todas if d['fecha'] == fecha]
    anteriores = [d for d in todas if d['fecha'] < fecha]

    por_hora = Counter(int(d['hora'][:2]) for d in del_dia)
    por_especie = Counter(d['especie'] for d in del_dia)
    vistas_antes = {d['especie'] for d in anteriores}
    nuevas = [e for e in por_especie if e not in vistas_antes]
    confianzas = [d['confianza'] for d in del_dia]

    try:
        est = drive.estado(ruta) or drive.estado_heredado(ruta)
    except drive.ErrorDrive:
        est = None

    # Ultimos 30 dias hasta la fecha, para contexto.
    fechas_30 = sorted({d['fecha'] for d in todas if d['fecha'] <= fecha},
                       reverse=True)[:30]
    ult = [d for d in todas if d['fecha'] in fechas_30]
    esp_30 = Counter(d['especie'] for d in ult)

    f = datetime.strptime(fecha, '%Y-%m-%d')
    lineas = [
        'TECTOR HUB — Reporte diario',
        f'{apodo} (serie {disp["serie"]}) · {f.strftime("%d/%m/%Y")}',
        '',
        _linea_estado(est),
    ]
    if horas_dia:
        lineas.append(f'Ventanas de grabacion: {horas_dia:g} h por dia')
    lineas += ['', 'DETECCIONES DEL DIA']
    if not del_dia:
        lineas.append('  Sin detecciones registradas este dia.')
    else:
        tasa = (f' · {len(del_dia) / horas_dia:.1f} por hora grabada'
                if horas_dia else '')
        lineas.append(f'  Total: {len(del_dia)} · Especies distintas: '
                      f'{len(por_especie)}{tasa}')
        lineas.append(f'  Confianza media: {sum(confianzas) / len(confianzas):.0f} %')
        lineas += ['', 'POR HORA', _hist_texto(por_hora)]
        lineas += ['', 'ESPECIES (por cantidad de detecciones)']
        for e, n in por_especie.most_common():
            lineas.append(f'  {n:>4}  {e}')
        if nuevas:
            lineas += ['', 'PRIMERA VEZ EN ESTA ESTACION']
            for e in nuevas:
                lineas.append(f'  · {e}')
    lineas += ['', 'ULTIMOS 30 DIAS (hasta esta fecha)']
    if ult:
        tasa30 = (f' · {len(ult) / (len(fechas_30) * horas_dia):.1f} por hora grabada'
                  if horas_dia else '')
        lineas.append(f'  {len(ult)} detecciones en {len(fechas_30)} dias con datos · '
                      f'{len(esp_30)} especies{tasa30}')
        lineas.append('  Mas frecuentes: ' + ', '.join(
            f'{e} ({n})' for e, n in esp_30.most_common(5)))
    else:
        lineas.append('  Sin datos.')
    lineas += ['',
               'Nota: el modelo tiene ~89 % de precision y es mas sensible a unas',
               'especies que a otras. Que una especie aparezca mucho no significa',
               'que sea mas abundante en el sitio.',
               '',
               f'Generado {datetime.now().strftime("%d/%m/%Y %H:%M")} por Tector Hub.']
    return '\n'.join(lineas) + '\n'
