# -*- coding: utf-8 -*-
"""
Envio de correo, para el reporte diario.

Va por SMTP con una cuenta comun --Gmail con contrasena de aplicacion, o
cualquier otra--. Sin las variables de entorno configuradas no manda nada y
lo dice: el reporte se genera y se guarda igual, solo no viaja por mail.

Variables (en /etc/tector-hub/entorno):
    TECTOR_SMTP_HOST      smtp.gmail.com
    TECTOR_SMTP_PUERTO    587
    TECTOR_SMTP_USUARIO   lsdarroyogold@gmail.com
    TECTOR_SMTP_CLAVE     la contrasena de APLICACION (no la de la cuenta)
    TECTOR_SMTP_DE        "Tector Hub <lsdarroyogold@gmail.com>"  (opcional)
"""
import smtplib
import sys
from email.message import EmailMessage

from . import config


def configurado():
    return bool(config.SMTP_HOST and config.SMTP_USUARIO and config.SMTP_CLAVE)


def enviar(destinatarios, asunto, texto, adjunto=None):
    """Manda un mail de texto plano, con un adjunto .txt opcional.

    Devuelve True si salio. No lanza: que falle el mail no puede tumbar la
    generacion del reporte. Lo que falle queda en el log del servidor.
    """
    destinatarios = [d for d in (destinatarios or []) if d]
    if not destinatarios:
        return False
    if not configurado():
        print('[correo] SMTP sin configurar: no se manda el reporte a '
              + ', '.join(destinatarios), file=sys.stderr)
        return False

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = config.SMTP_DE or config.SMTP_USUARIO
    msg['To'] = ', '.join(destinatarios)
    msg.set_content(texto)
    if adjunto:
        nombre, contenido = adjunto
        msg.add_attachment(contenido.encode('utf-8'), maintype='text',
                           subtype='plain', filename=nombre)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PUERTO, timeout=60) as s:
            s.starttls()
            s.login(config.SMTP_USUARIO, config.SMTP_CLAVE)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f'[correo] no se pudo mandar a {destinatarios}: {e}',
              file=sys.stderr)
        return False
