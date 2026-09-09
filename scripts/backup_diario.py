#!/usr/bin/env python3
"""
Backup diario de Salas Llenas (Boletería CNA).

Lee las tablas de Supabase, arma un Excel con una hoja por tabla — la misma
estructura del backup manual de la app, para que los dos archivos sean
intercambiables al restaurar — y lo envía por correo. Además deja una copia
en el repositorio y borra las más viejas de 30 días.

Corre desde GitHub Actions; no depende de que nadie tenga la app abierta.
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
from pathlib import Path

from openpyxl import Workbook

# Mismo orden y nombres de hoja que usa el backup manual de la app
TABLAS = [
    ("salas", "boleteria_salas"),
    ("categorias", "boleteria_categorias"),
    ("agrupadores", "boleteria_agrupadores"),
    ("obras", "boleteria_obras"),
    ("funciones", "boleteria_funciones"),
    ("acciones", "boleteria_acciones"),
    ("historial", "boleteria_historial"),
    ("usuarios", "boleteria_usuarios"),
]

# Columnas que guardan arreglos JSON: se serializan como texto, igual que en la app
COLUMNAS_JSON = ["ventas_por_dia", "registros_por_dia"]

DIAS_A_CONSERVAR = 30
CARPETA_BACKUPS = Path("backups")

# Colombia es UTC-5 todo el año
HORA_COLOMBIA = timezone(timedelta(hours=-5))


def variable(nombre):
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        sys.exit("Falta la variable de entorno %s" % nombre)
    return valor


def leer_tabla(base_url, api_key, tabla):
    """Trae todas las filas de una tabla paginando de a 1000."""
    filas = []
    desde = 0
    while True:
        params = urllib.parse.urlencode({"select": "*", "limit": 1000, "offset": desde})
        req = urllib.request.Request(
            "%s/rest/v1/%s?%s" % (base_url, tabla, params),
            headers={"apikey": api_key, "Authorization": "Bearer " + api_key},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            lote = json.loads(r.read())
        filas.extend(lote)
        if len(lote) < 1000:
            return filas
        desde += 1000


def celda(valor, columna):
    """Deja el valor listo para Excel: los JSON como texto, el resto plano."""
    if columna in COLUMNAS_JSON:
        return json.dumps(valor if valor is not None else [], ensure_ascii=False)
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    if valor is None:
        return None
    return valor


def construir_excel(datos, ruta):
    libro = Workbook()
    libro.remove(libro.active)
    for hoja, _ in TABLAS:
        ws = libro.create_sheet(hoja)
        filas = datos[hoja]
        if not filas:
            continue
        # Se toma el juego completo de columnas, no solo las de la primera fila
        columnas = []
        for fila in filas:
            for c in fila:
                if c not in columnas:
                    columnas.append(c)
        ws.append(columnas)
        for fila in filas:
            ws.append([celda(fila.get(c), c) for c in columnas])
    libro.save(ruta)


def enviar_correo(ruta, resumen, fecha):
    mensaje = EmailMessage()
    mensaje["Subject"] = "Backup Salas Llenas — %s" % fecha
    mensaje["From"] = variable("GMAIL_USER")
    mensaje["To"] = variable("BACKUP_EMAIL_TO")
    mensaje.set_content(
        "Copia de seguridad automática de Salas Llenas (Boletería CNA).\n\n"
        + resumen
        + "\n\nEl archivo adjunto se puede restaurar desde Administración > "
        "Copia de seguridad > Restaurar desde Excel.\n"
        "También queda una copia en el repositorio, en la carpeta backups.\n"
    )
    with open(ruta, "rb") as f:
        mensaje.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=ruta.name,
        )

    contexto = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=contexto, timeout=60) as smtp:
        smtp.login(variable("GMAIL_USER"), variable("GMAIL_APP_PASSWORD"))
        smtp.send_message(mensaje)


def limpiar_viejos():
    """Conserva solo los backups de los últimos 30 días."""
    if not CARPETA_BACKUPS.exists():
        return []
    corte = datetime.now(HORA_COLOMBIA).date() - timedelta(days=DIAS_A_CONSERVAR)
    borrados = []
    for archivo in CARPETA_BACKUPS.glob("backup-salas-llenas-*.xlsx"):
        try:
            fecha = datetime.strptime(archivo.stem[-10:], "%Y-%m-%d").date()
        except ValueError:
            continue
        if fecha < corte:
            archivo.unlink()
            borrados.append(archivo.name)
    return borrados


def main():
    base_url = variable("SUPABASE_URL").rstrip("/")
    api_key = variable("SUPABASE_KEY")

    fecha = datetime.now(HORA_COLOMBIA).strftime("%Y-%m-%d")

    datos = {}
    conteos = []
    for hoja, tabla in TABLAS:
        filas = leer_tabla(base_url, api_key, tabla)
        datos[hoja] = filas
        conteos.append("%s: %d" % (hoja, len(filas)))

    # Un backup sin obras casi seguro significa que algo falló al leer:
    # es preferible fallar ruidosamente a mandar un archivo vacío
    if len(datos["obras"]) == 0:
        sys.exit("El backup salió sin obras. Se cancela para no sobrescribir con datos vacíos.")

    CARPETA_BACKUPS.mkdir(exist_ok=True)
    ruta = CARPETA_BACKUPS / ("backup-salas-llenas-%s.xlsx" % fecha)
    construir_excel(datos, ruta)

    resumen = "Registros por tabla — " + " · ".join(conteos)
    print(resumen)
    print("Archivo: %s (%d KB)" % (ruta, ruta.stat().st_size // 1024))

    enviar_correo(ruta, resumen, fecha)
    print("Correo enviado a %s" % os.environ.get("BACKUP_EMAIL_TO"))

    borrados = limpiar_viejos()
    if borrados:
        print("Backups eliminados por antigüedad: %s" % ", ".join(borrados))


if __name__ == "__main__":
    main()
