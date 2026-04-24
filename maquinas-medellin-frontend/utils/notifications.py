import logging
import smtplib
import ssl
import threading
import urllib.parse
import urllib.request
from datetime import datetime

from config import (
    ALERT_PHONE, CALLMEBOT_APIKEY,
    ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD, ALERT_EMAIL_TO,
    LOGGER_NAME,
)

logger = logging.getLogger(LOGGER_NAME)


def _now_str() -> str:
    return datetime.now().strftime('%d/%m/%Y %I:%M %p')


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
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
                server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD)
                msg = (
                    f"From: Inversiones Arcade <{ALERT_EMAIL_FROM}>\r\n"
                    f"To: {ALERT_EMAIL_TO}\r\n"
                    f"Subject: {subject}\r\n"
                    f"Content-Type: text/plain; charset=utf-8\r\n"
                    f"\r\n"
                    f"{body}"
                )
                server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.encode("utf-8"))
        except Exception as e:
            logger.warning(f"[notify] Email falló: {e}")

    threading.Thread(target=_send, daemon=True).start()


def notify_falla(machine_name: str, local_nombre: str, notes: str = "") -> None:
    txt = (
        f"🔴 FALLA — {local_nombre}\n"
        f"Máquina: {machine_name}\n"
        f"Hora: {_now_str()}\n"
        f"Turno devuelto automáticamente."
        + (f"\nNota: {notes}" if notes else "")
    )
    send_whatsapp(txt)
    send_email(f"🔴 Falla en máquina — {machine_name}", txt)
    logger.info(f"[notify] Alerta de falla enviada: {machine_name}")


def notify_offline(machine_name: str, local_nombre: str, segundos: int) -> None:
    minutos = segundos // 60
    txt = (
        f"📴 OFFLINE — {local_nombre}\n"
        f"Máquina: {machine_name}\n"
        f"Sin reportar hace {minutos} min.\n"
        f"Verifica la conexión WiFi."
    )
    send_whatsapp(txt)
    send_email(f"📴 Máquina offline — {machine_name}", txt)
    logger.info(f"[notify] Alerta offline enviada: {machine_name} ({minutos} min)")


def notify_online(machine_name: str, local_nombre: str) -> None:
    txt = (
        f"✅ ONLINE — {local_nombre}\n"
        f"Máquina: {machine_name} volvió a conectarse.\n"
        f"Hora: {_now_str()}"
    )
    send_whatsapp(txt)
    send_email(f"✅ Máquina online — {machine_name}", txt)
    logger.info(f"[notify] Alerta online enviada: {machine_name}")
