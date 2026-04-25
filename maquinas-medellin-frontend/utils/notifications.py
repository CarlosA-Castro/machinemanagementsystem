import logging
import smtplib
import ssl
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.parse
import urllib.request

from config import (
    ALERT_PHONE, CALLMEBOT_APIKEY,
    ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD, ALERT_EMAIL_TO,
    LOGGER_NAME,
)
from utils.timezone import get_colombia_time

logger = logging.getLogger(LOGGER_NAME)


def _now_str() -> str:
    return get_colombia_time().strftime('%d/%m/%Y %I:%M %p')


def send_whatsapp(message: str) -> None:
    if not ALERT_PHONE or not CALLMEBOT_APIKEY:
        return

    def _send():
        try:
            encoded = urllib.parse.quote(message)
            url = (
                f"https://api.callmebot.com/whatsapp.php"
                f"?phone={ALERT_PHONE}&text={encoded}&apikey={CALLMEBOT_APIKEY}"
            )
            urllib.request.urlopen(url, timeout=10)
        except Exception as e:
            logger.warning(f"[notify] WhatsApp falló: {e}")

    threading.Thread(target=_send, daemon=True).start()


def send_email(subject: str, body: str) -> None:
    if not ALERT_EMAIL_FROM or not ALERT_EMAIL_PASSWORD or not ALERT_EMAIL_TO:
        return

    def _send():
        try:
            msg = MIMEMultipart()
            msg['From']    = f"Inversiones Arcade <{ALERT_EMAIL_FROM}>"
            msg['To']      = ALERT_EMAIL_TO
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            context = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
                server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD)
                server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.as_string())
            logger.info(f"[notify] Email enviado: {subject}")
        except Exception as e:
            logger.error(f"[notify] Email falló: {e}")

    threading.Thread(target=_send, daemon=True).start()


def notify_falla(machine_name: str, local_nombre: str, notes: str = "") -> None:
    txt = (
        f"FALLA — {local_nombre}\n"
        f"Maquina: {machine_name}\n"
        f"Hora: {_now_str()}\n"
        f"Turno devuelto automaticamente."
        + (f"\nNota: {notes}" if notes else "")
    )
    wpp = (
        f"🔴 FALLA — {local_nombre}\n"
        f"Máquina: {machine_name}\n"
        f"Hora: {_now_str()}\n"
        f"Turno devuelto automáticamente."
        + (f"\nNota: {notes}" if notes else "")
    )
    send_whatsapp(wpp)
    send_email(f"[FALLA] {machine_name} — {local_nombre}", txt)
    logger.info(f"[notify] Alerta de falla enviada: {machine_name}")


def notify_offline(machine_name: str, local_nombre: str, segundos: int) -> None:
    minutos = segundos // 60
    txt = (
        f"OFFLINE — {local_nombre}\n"
        f"Maquina: {machine_name}\n"
        f"Sin reportar hace {minutos} min.\n"
        f"Verifica la conexion WiFi."
    )
    wpp = (
        f"📴 OFFLINE — {local_nombre}\n"
        f"Máquina: {machine_name}\n"
        f"Sin reportar hace {minutos} min.\n"
        f"Verifica la conexión WiFi."
    )
    send_whatsapp(wpp)
    send_email(f"[OFFLINE] {machine_name} — {local_nombre}", txt)
    logger.info(f"[notify] Alerta offline enviada: {machine_name} ({minutos} min)")


def notify_mantenimiento(machine_name: str, local_nombre: str, station_index: int, failure_count: int) -> None:
    txt = (
        f"MANTENIMIENTO — {local_nombre}\n"
        f"Maquina: {machine_name}\n"
        f"Estacion: {station_index}\n"
        f"Fallas consecutivas: {failure_count}\n"
        f"Hora: {_now_str()}\n"
        f"La estacion fue bloqueada automaticamente."
    )
    wpp = (
        f"🔧 MANTENIMIENTO — {local_nombre}\n"
        f"Máquina: {machine_name}\n"
        f"Estación: {station_index}\n"
        f"Fallas consecutivas: {failure_count}\n"
        f"Hora: {_now_str()}\n"
        f"Estación bloqueada automáticamente."
    )
    send_whatsapp(wpp)
    send_email(f"[MANTENIMIENTO] {machine_name} Est.{station_index} — {local_nombre}", txt)
    logger.info(f"[notify] Alerta de mantenimiento enviada: {machine_name} est.{station_index}")


def notify_online(machine_name: str, local_nombre: str) -> None:
    txt = (
        f"ONLINE — {local_nombre}\n"
        f"Maquina: {machine_name} volvio a conectarse.\n"
        f"Hora: {_now_str()}"
    )
    wpp = (
        f"✅ ONLINE — {local_nombre}\n"
        f"Máquina: {machine_name} volvió a conectarse.\n"
        f"Hora: {_now_str()}"
    )
    send_whatsapp(wpp)
    send_email(f"[ONLINE] {machine_name} — {local_nombre}", txt)
    logger.info(f"[notify] Alerta online enviada: {machine_name}")
