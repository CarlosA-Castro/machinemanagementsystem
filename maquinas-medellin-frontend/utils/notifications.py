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
  <div style="background:#111827;border-left:3px solid #3b82f6;
              border-radius:8px;padding:14px 18px;margin-bottom:32px">
    <p style="color:#3b82f6;font-size:12px;font-weight:700;margin:0 0 5px">
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


def send_info_inversor(
    nombre: str,
    email: str,
    roi_promedio: float = None,
) -> None:
    """
    Email de marketing para prospecto del formulario landing.
    Diseño tipo 'shader hero': fondo oscuro dramático, headline con gradiente
    cálido, badge de confianza, stat de ROI real y dos CTAs.
    """
    primer_nombre = nombre.split()[0].capitalize() if nombre else "Inversor"
    subject = f"{primer_nombre}, esto es lo que tu dinero puede hacer en Inversiones Arcade"

    # WhatsApp CTA — número sin el +
    wpp_number = (ALERT_PHONE or "").lstrip('+').replace(' ', '')
    wpp_url    = f"https://wa.me/{wpp_number}" if wpp_number else "https://wa.me/"

    # ROI stat
    roi_str  = f"{roi_promedio:.1f}%" if roi_promedio is not None else "real y verificable"
    roi_desc = "ROI promedio de socios activos" if roi_promedio is not None else "Retorno sobre inversión"

    body_text = (
        f"Hola {primer_nombre},\n\n"
        f"Gracias por tu interés en Inversiones Arcade.\n\n"
        f"Mientras uno de nuestros socios te contacta por WhatsApp, aquí te contamos qué hace "
        f"que esta inversión sea diferente:\n\n"
        f"🎮 MÁQUINAS ARCADE EN LOCALES ACTIVOS\n"
        f"Tu dinero trabaja en máquinas físicas instaladas en restaurantes y centros de entretenimiento "
        f"de Medellín, generando ingresos todos los días de la semana.\n\n"
        f"📊 RETORNO REAL: {roi_str}\n"
        f"No son proyecciones teóricas. Es el retorno que están recibiendo nuestros socios actuales, "
        f"liquidado mes a mes y verificable en la plataforma.\n\n"
        f"🔍 TRANSPARENCIA TOTAL\n"
        f"Cada turno jugado queda registrado. Ves en tiempo real cuánto genera tu máquina, "
        f"cuántos turnos van en el mes y cuándo llega tu liquidación.\n\n"
        f"¿Listo para ver tus proyecciones?\n"
        f"Simula tu inversión: https://inversionesarcade.com/#simula\n"
        f"Habla con nosotros: {wpp_url}\n\n"
        f"— El equipo de Inversiones Arcade"
    )

    body_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#07090f;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased">

<!-- ── HERO ────────────────────────────────────────────────── -->
<div style="background:linear-gradient(170deg,#020c1e 0%,#06091e 40%,#07090f 75%);
            padding:0;overflow:hidden">

  <!-- Warm radial glow overlay -->
  <div style="background:radial-gradient(ellipse 90% 55% at 50% -5%,rgba(59,130,246,.2) 0%,transparent 65%);
              padding:52px 28px 44px;text-align:center">

    <!-- Brand pill -->
    <div style="display:inline-block;margin-bottom:28px">
      <div style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.22);
                  border-radius:40px;padding:8px 20px;display:inline-flex;align-items:center;gap:8px">
        <span style="font-size:15px">🎮</span>
        <span style="color:#3b82f6;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase">
          Inversiones Arcade &nbsp;·&nbsp; Medellín
        </span>
      </div>
    </div>

    <!-- Trust badge -->
    <div style="margin-bottom:24px">
      <div style="display:inline-block;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);
                  border-radius:40px;padding:6px 16px">
        <span style="color:#cbd5e1;font-size:12px;font-weight:500">
          ✦ &nbsp;Socios activos recibiendo liquidaciones mensuales reales
        </span>
      </div>
    </div>

    <!-- Headline -->
    <h1 style="margin:0 0 20px;font-size:40px;font-weight:900;line-height:1.08;letter-spacing:-1.5px;
               max-width:520px;margin-left:auto;margin-right:auto">
      <span style="color:#f1f5f9;display:block;margin-bottom:4px">
        {primer_nombre}, tu dinero puede
      </span>
      <span style="background:linear-gradient(90deg,#3b82f6 0%,#6366f1 55%,#8b5cf6 100%);
                   -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                   background-clip:text;color:#3b82f6;display:block">
        trabajar mientras tú descansas.
      </span>
    </h1>

    <!-- Subtitle -->
    <p style="color:#94a3b8;font-size:16px;line-height:1.75;max-width:460px;
              margin:0 auto 36px;font-weight:400">
      Máquinas arcade en locales activos de Medellín. Ingresos reales,
      <strong style="color:#e2e8f0;font-weight:600">verificables en tiempo real</strong>,
      liquidados mes a mes.
    </p>

    <!-- ROI stat -->
    <div style="background:rgba(59,130,246,.07);border:1px solid rgba(59,130,246,.18);
                border-radius:16px;padding:20px 28px;display:inline-block;margin-bottom:36px;
                max-width:320px">
      <div style="color:#3b82f6;font-size:42px;font-weight:900;letter-spacing:-2px;
                  font-variant-numeric:tabular-nums;line-height:1">{roi_str}</div>
      <div style="color:#78716c;font-size:11px;font-weight:600;text-transform:uppercase;
                  letter-spacing:1px;margin-top:6px">{roi_desc}</div>
    </div>

    <!-- CTAs -->
    <div style="margin-bottom:8px">
      <!-- Primary CTA -->
      <div style="margin-bottom:12px">
        <a href="https://inversionesarcade.com/#simula"
           style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#8b5cf6);
                  color:#fff;text-decoration:none;padding:15px 32px;border-radius:40px;
                  font-size:15px;font-weight:800;letter-spacing:-.2px;
                  box-shadow:0 8px 32px rgba(59,130,246,.4)">
          Simula tu inversión &rarr;
        </a>
      </div>
      <!-- Secondary CTA -->
      <div>
        <a href="{wpp_url}"
           style="display:inline-block;background:rgba(255,255,255,.07);
                  border:1px solid rgba(255,255,255,.14);
                  color:#e2e8f0;text-decoration:none;padding:13px 28px;border-radius:40px;
                  font-size:14px;font-weight:600">
          💬 &nbsp;Hablar con un asesor
        </a>
      </div>
    </div>

  </div>
</div>

<!-- ── POR QUÉ ARCADE ──────────────────────────────────────── -->
<div style="background:#07090f;padding:40px 28px">
  <div style="max-width:520px;margin:0 auto">

    <p style="color:#475569;font-size:10px;font-weight:700;text-transform:uppercase;
              letter-spacing:1.2px;text-align:center;margin:0 0 28px">
      Por qué funciona
    </p>

    <!-- Feature 1 -->
    <div style="display:flex;gap:16px;margin-bottom:24px;align-items:flex-start">
      <div style="background:rgba(59,130,246,.1);border-radius:12px;
                  padding:12px;flex-shrink:0;width:44px;height:44px;
                  display:flex;align-items:center;justify-content:center;
                  font-size:20px;box-sizing:border-box">🕹️</div>
      <div>
        <p style="color:#f1f5f9;font-size:15px;font-weight:700;margin:0 0 5px">
          Las máquinas trabajan 7 días a la semana
        </p>
        <p style="color:#64748b;font-size:13px;margin:0;line-height:1.6">
          No depende de empleados ni horarios. Cada turno jugado genera ingreso
          automáticamente, a cualquier hora.
        </p>
      </div>
    </div>

    <!-- Feature 2 -->
    <div style="display:flex;gap:16px;margin-bottom:24px;align-items:flex-start">
      <div style="background:rgba(139,92,246,.1);border-radius:12px;
                  padding:12px;flex-shrink:0;width:44px;height:44px;
                  display:flex;align-items:center;justify-content:center;
                  font-size:20px;box-sizing:border-box">📡</div>
      <div>
        <p style="color:#f1f5f9;font-size:15px;font-weight:700;margin:0 0 5px">
          Transparencia total en tiempo real
        </p>
        <p style="color:#64748b;font-size:13px;margin:0;line-height:1.6">
          Ves cada turno jugado en tu portal personal. Ingresos, estado de la
          máquina y tu participación, sin intermediarios.
        </p>
      </div>
    </div>

    <!-- Feature 3 -->
    <div style="display:flex;gap:16px;margin-bottom:36px;align-items:flex-start">
      <div style="background:rgba(34,197,94,.1);border-radius:12px;
                  padding:12px;flex-shrink:0;width:44px;height:44px;
                  display:flex;align-items:center;justify-content:center;
                  font-size:20px;box-sizing:border-box">💰</div>
      <div>
        <p style="color:#f1f5f9;font-size:15px;font-weight:700;margin:0 0 5px">
          Tú defines tu porcentaje de propiedad
        </p>
        <p style="color:#64748b;font-size:13px;margin:0;line-height:1.6">
          Desde el 10% hasta el 100% de una máquina. Escala a tu ritmo
          y diversifica en múltiples locales.
        </p>
      </div>
    </div>

    <!-- Divider -->
    <div style="height:1px;background:rgba(255,255,255,.06);margin-bottom:28px"></div>

    <!-- CTA repetido -->
    <div style="text-align:center;margin-bottom:8px">
      <p style="color:#64748b;font-size:13px;margin:0 0 16px">
        Uno de nuestros socios te contactará por WhatsApp en las próximas horas.<br>
        Si prefieres no esperar:
      </p>
      <a href="{wpp_url}"
         style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#6366f1);
                color:#fff;text-decoration:none;padding:13px 28px;border-radius:40px;
                font-size:14px;font-weight:800">
        💬 &nbsp;Escribir ahora por WhatsApp
      </a>
    </div>

  </div>
</div>

<!-- ── FOOTER ──────────────────────────────────────────────── -->
<div style="background:#050709;border-top:1px solid rgba(255,255,255,.05);
            padding:24px 28px;text-align:center">
  <div style="background:linear-gradient(135deg,#3b82f6,#8b5cf6);border-radius:8px;
              padding:6px 14px;display:inline-block;margin-bottom:14px">
    <span style="color:#fff;font-size:12px;font-weight:800">🎮 Inversiones Arcade</span>
  </div>
  <p style="color:#334155;font-size:11px;margin:0;line-height:1.7">
    Medellín, Colombia &nbsp;·&nbsp; inversionesarcade.com<br>
    Recibiste este correo porque dejaste tus datos en nuestro formulario de contacto.
  </p>
</div>

</body>
</html>"""

    send_email_to(email, subject, body_text, body_html)
