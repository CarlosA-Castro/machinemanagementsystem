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


# ── Email a dirección arbitraria ──────────────────────────────────────────────

def send_email_to(to_email: str, subject: str, body_text: str, body_html: str = None) -> None:
    """Envía un email a una dirección específica (no solo al admin).
    Se ejecuta en hilo daemon para no bloquear el request."""
    if not ALERT_EMAIL_FROM or not ALERT_EMAIL_PASSWORD or not to_email:
        return

    def _send():
        try:
            msg = MIMEMultipart('alternative')
            msg['From']    = f"Inversiones Arcade <{ALERT_EMAIL_FROM}>"
            msg['To']      = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            if body_html:
                msg.attach(MIMEText(body_html, 'html', 'utf-8'))

            context = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
                server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD)
                server.sendmail(ALERT_EMAIL_FROM, to_email, msg.as_string())
            logger.info(f"[notify] Email enviado a {to_email}: {subject}")
        except Exception as e:
            logger.error(f"[notify] Email a {to_email} falló: {e}")

    threading.Thread(target=_send, daemon=True).start()


def send_bienvenida_inversor(
    nombre: str,
    email: str,
    codigo_socio: str,
    username: str,
    password_temp: str,
    login_url: str = "https://inversionesarcade.com/login",
) -> None:
    """Email de bienvenida con credenciales para un nuevo inversor registrado."""
    primer_nombre = nombre.split()[0].capitalize() if nombre else "Inversor"
    subject = f"Bienvenido a Inversiones Arcade — tus credenciales de acceso"

    # Plain text
    body_text = (
        f"Hola {primer_nombre},\n\n"
        f"Tu cuenta de inversor en Inversiones Arcade ha sido creada exitosamente.\n\n"
        f"CREDENCIALES DE ACCESO\n"
        f"{'─' * 30}\n"
        f"Usuario:            {username}\n"
        f"Contraseña temporal:{password_temp}\n"
        f"Código de socio:    {codigo_socio}\n"
        f"{'─' * 30}\n\n"
        f"Ingresa en: {login_url}\n\n"
        f"⚠️ Por seguridad, cambia tu contraseña la primera vez que ingreses.\n\n"
        f"¿Tienes preguntas? Escríbenos por WhatsApp.\n"
        f"— Inversiones Arcade"
    )

    # HTML con diseño on-brand
    body_html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bienvenido a Inversiones Arcade</title></head>
<body style="margin:0;padding:0;background:#030712;font-family:Inter,-apple-system,Arial,sans-serif">
<div style="max-width:560px;margin:0 auto;padding:40px 24px">

  <!-- Header -->
  <div style="text-align:center;margin-bottom:36px">
    <div style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#8b5cf6);
                border-radius:12px;padding:10px 22px;margin-bottom:20px">
      <span style="color:#fff;font-size:16px;font-weight:800;letter-spacing:-0.3px">
        🎮&nbsp; Inversiones Arcade
      </span>
    </div>
    <h1 style="color:#f8fafc;font-size:26px;font-weight:800;margin:0 0 10px;letter-spacing:-0.8px">
      Bienvenido, {primer_nombre}
    </h1>
    <p style="color:#94a3b8;font-size:15px;margin:0;line-height:1.6">
      Tu cuenta de inversor ha sido creada exitosamente.<br>
      Ya puedes acceder al portal y ver tus activos en tiempo real.
    </p>
  </div>

  <!-- Credenciales -->
  <div style="background:#0c1222;border:1px solid #1e293b;border-radius:16px;
              padding:28px;margin-bottom:20px">
    <p style="color:#64748b;font-size:10px;text-transform:uppercase;
              letter-spacing:1.2px;margin:0 0 20px;font-weight:700">
      Tus credenciales de acceso
    </p>
    <div style="margin-bottom:20px">
      <p style="color:#64748b;font-size:11px;margin:0 0 6px;text-transform:uppercase;
                letter-spacing:.6px">Usuario</p>
      <p style="color:#f8fafc;font-size:22px;font-weight:800;margin:0;
                font-family:monospace;letter-spacing:2px">{username}</p>
    </div>
    <div style="border-top:1px solid #1e293b;padding-top:20px;margin-bottom:20px">
      <p style="color:#64748b;font-size:11px;margin:0 0 6px;text-transform:uppercase;
                letter-spacing:.6px">Contraseña temporal</p>
      <p style="color:#3b82f6;font-size:22px;font-weight:800;margin:0;
                font-family:monospace;letter-spacing:3px">{password_temp}</p>
    </div>
    <div style="border-top:1px solid #1e293b;padding-top:20px">
      <p style="color:#64748b;font-size:11px;margin:0 0 6px;text-transform:uppercase;
                letter-spacing:.6px">Código de socio</p>
      <p style="color:#8b5cf6;font-size:18px;font-weight:800;margin:0;
                font-family:monospace;letter-spacing:2px">{codigo_socio}</p>
    </div>
  </div>

  <!-- CTA -->
  <div style="text-align:center;margin-bottom:24px">
    <a href="{login_url}"
       style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#1d4ed8);
              color:#fff;text-decoration:none;padding:15px 36px;border-radius:10px;
              font-size:15px;font-weight:700;letter-spacing:-0.3px;
              box-shadow:0 4px 20px rgba(59,130,246,.4)">
      Acceder a mi portal &rarr;
    </a>
  </div>

  <!-- Aviso -->
  <div style="background:#111827;border-left:3px solid #f59e0b;
              border-radius:8px;padding:14px 18px;margin-bottom:32px">
    <p style="color:#f59e0b;font-size:12px;font-weight:700;margin:0 0 5px">
      ⚠️ Importante
    </p>
    <p style="color:#94a3b8;font-size:13px;margin:0;line-height:1.6">
      Por seguridad, cambia tu contraseña la primera vez que ingreses al portal.
      Guarda estas credenciales en un lugar seguro.
    </p>
  </div>

  <!-- Footer -->
  <p style="color:#475569;font-size:12px;text-align:center;margin:0;line-height:1.8">
    ¿Tienes preguntas? Escríbenos por WhatsApp.<br>
    <span style="color:#334155">Inversiones Arcade &mdash; Portal del Inversor</span>
  </p>

</div>
</body>
</html>"""

    send_email_to(email, subject, body_text, body_html)
