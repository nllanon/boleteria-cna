#!/usr/bin/env python3
"""
Envía por correo las consultas que la gente manda desde la sección Ayuda.

La aplicación no puede enviar correos por sí sola (no tiene servidor), así que
guarda cada consulta en la tabla boleteria_soporte con enviado = false. Este
script corre periódicamente desde GitHub Actions, envía las pendientes y las
marca como enviadas.

El destinatario sale de la tabla boleteria_config, para que se pueda cambiar
desde Administración sin tocar el código.
"""

import json
import os
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

HORA_COLOMBIA = timezone(timedelta(hours=-5))


def variable(nombre):
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        sys.exit("Falta la variable de entorno %s" % nombre)
    return valor


def rpc(base_url, api_key, funcion, cuerpo):
    """Llama una función de Postgres. La tabla de soporte está cerrada a la llave
    pública; solo estas funciones, protegidas con un token, pueden leerla."""
    req = urllib.request.Request(
        base_url + "/rest/v1/rpc/" + funcion,
        method="POST",
        data=json.dumps(cuerpo).encode(),
        headers={
            "apikey": api_key,
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        contenido = r.read()
        return json.loads(contenido) if contenido else None


def marcar_enviada(base_url, api_key, token, consulta_id):
    rpc(base_url, api_key, "soporte_marcar_enviado",
        {"token": token, "consulta_id": consulta_id})


def destinatario(base_url, api_key, token):
    valor = rpc(base_url, api_key, "soporte_destinatario", {"token": token})
    if valor:
        return valor
    # Si nadie configuró el correo, se usa el mismo de los backups
    return os.environ.get("BACKUP_EMAIL_TO", "").strip()


def enviar(destino, consulta):
    creado = consulta.get("creado_en", "")
    try:
        fecha = datetime.fromisoformat(creado.replace("Z", "+00:00")).astimezone(HORA_COLOMBIA)
        creado = fecha.strftime("%d/%m/%Y %H:%M")
    except Exception:
        pass

    mensaje = EmailMessage()
    mensaje["Subject"] = "Salas Llenas — %s de %s" % (
        consulta.get("tipo") or "Consulta",
        consulta.get("usuario_nombre") or consulta.get("usuario_email") or "un usuario",
    )
    mensaje["From"] = variable("GMAIL_USER")
    mensaje["To"] = destino
    if consulta.get("usuario_email"):
        # Así se puede responder directamente a quien escribió
        mensaje["Reply-To"] = consulta["usuario_email"]

    mensaje.set_content(
        "Mensaje enviado desde la sección Ayuda de Salas Llenas.\n\n"
        "De: %s (%s)\n"
        "Perfil: %s\n"
        "Tipo: %s\n"
        "Fecha: %s\n\n"
        "-----------------------------------------\n\n"
        "%s\n\n"
        "-----------------------------------------\n\n"
        "Puedes responder directamente a este correo y le llega a quien escribió.\n"
        % (
            consulta.get("usuario_nombre") or "sin nombre",
            consulta.get("usuario_email") or "sin correo",
            consulta.get("usuario_rol") or "sin perfil",
            consulta.get("tipo") or "sin tipo",
            creado,
            consulta.get("mensaje") or "",
        )
    )

    contexto = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=contexto, timeout=60) as smtp:
        smtp.login(variable("GMAIL_USER"), variable("GMAIL_APP_PASSWORD"))
        smtp.send_message(mensaje)


def main():
    base_url = variable("SUPABASE_URL").rstrip("/")
    api_key = variable("SUPABASE_KEY")
    token = variable("SOPORTE_TOKEN")

    pendientes = rpc(base_url, api_key, "soporte_pendientes", {"token": token}) or []

    if not pendientes:
        print("Sin consultas pendientes.")
        return

    destino = destinatario(base_url, api_key, token)
    if not destino:
        sys.exit("No hay correo de soporte configurado.")

    print("Consultas pendientes: %d · destino: %s" % (len(pendientes), destino))

    enviadas = 0
    for consulta in pendientes:
        try:
            enviar(destino, consulta)
            marcar_enviada(base_url, api_key, token, consulta["id"])
            enviadas += 1
        except Exception as err:
            # Si una falla, se deja pendiente y se reintenta en la próxima corrida
            print("Error enviando %s: %s" % (consulta.get("id"), err))

    print("Enviadas: %d de %d" % (enviadas, len(pendientes)))


if __name__ == "__main__":
    main()
