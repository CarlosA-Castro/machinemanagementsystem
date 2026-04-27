import logging
import os
from datetime import timedelta
from logging.handlers import RotatingFileHandler

import sentry_sdk
from flask import Flask
from flask_cors import CORS
from sentry_sdk.integrations.flask import FlaskIntegration

from config import (
    SECRET_KEY, SESSION_TIMEOUT,
    SENTRY_DSN,
    LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, LOGGER_NAME,
)
from middleware.session import check_session_timeout
from middleware.logging_mw import before_request_log, after_request_log


def _configure_logging(app: Flask) -> None:
    """Configura file handler + console handler sobre el logger 'maquinas'."""
    os.makedirs('logs', exist_ok=True)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)-8s] %(message)s  (%(filename)s:%(lineno)d)'
    ))
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)-8s] %(message)s'
    ))

    # Silenciar loggers ruidosos
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('sentry_sdk').setLevel(logging.WARNING)

    # Logger compartido por todos los mÃ³dulos del proyecto
    maquinas_logger = logging.getLogger(LOGGER_NAME)
    if not any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, 'baseFilename', '').endswith('maquinas.log')
        for handler in maquinas_logger.handlers
    ):
        maquinas_logger.addHandler(file_handler)
    if not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler)
        for handler in maquinas_logger.handlers
    ):
        maquinas_logger.addHandler(console_handler)
    maquinas_logger.setLevel(logging.INFO)

    # Propagar al app.logger de Flask para que /api/logs/consola-completa siga funcionando
    app.logger.handlers = maquinas_logger.handlers
    app.logger.setLevel(logging.INFO)


def _start_heartbeat_monitor() -> None:
    """Thread de fondo: detecta máquinas offline y notifica al admin."""
    import time
    import threading
    from datetime import datetime, date
    from blueprints.esp32.state import check_offline_machines, pop_newly_online
    from utils.notifications import notify_offline, notify_online
    from database import get_db_connection, get_db_cursor

    def _machine_info(machine_id: int) -> tuple:
        """Retorna (name, local_nombre, location_id) de la máquina."""
        try:
            conn = get_db_connection()
            if not conn:
                return f'Máquina {machine_id}', 'Sin local', None
            cur = get_db_cursor(conn)
            cur.execute(
                "SELECT m.name, COALESCE(l.name, 'Sin local') AS local_nombre, m.location_id "
                "FROM machine m LEFT JOIN location l ON m.location_id = l.id "
                "WHERE m.id = %s", (machine_id,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                return row['name'], row['local_nombre'], row['location_id']
        except Exception:
            pass
        return f'Máquina {machine_id}', 'Sin local', None

    def _log_connectivity(machine_id: int, machine_name: str, location_id, event_type: str) -> None:
        """Persiste un evento online/offline en machine_connectivity_log."""
        try:
            conn = get_db_connection()
            if not conn:
                return
            cur = get_db_cursor(conn)
            cur.execute(
                "INSERT INTO machine_connectivity_log "
                "  (machine_id, machine_name, location_id, event_type, event_at) "
                "VALUES (%s, %s, %s, %s, NOW())",
                (machine_id, machine_name, location_id, event_type),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass

    def _cleanup_connectivity_log() -> None:
        """Borra registros de conectividad con más de 60 días (depuración bimestral)."""
        try:
            conn = get_db_connection()
            if not conn:
                return
            cur = get_db_cursor(conn)
            cur.execute(
                "DELETE FROM machine_connectivity_log WHERE event_at < DATE_SUB(NOW(), INTERVAL 60 DAY)"
            )
            deleted = cur.rowcount
            conn.commit()
            cur.close()
            conn.close()
            if deleted:
                import logging
                logging.getLogger('maquinas').info(
                    f"[connectivity] Cleanup: {deleted} registros eliminados (>60 días)"
                )
        except Exception:
            pass

    def _monitor():
        time.sleep(15)  # esperar a que la app arranque completamente
        last_cleanup_day = None
        while True:
            try:
                for mid, segundos in check_offline_machines():
                    name, local, loc_id = _machine_info(mid)
                    notify_offline(name, local, segundos)
                    _log_connectivity(mid, name, loc_id, 'offline')
                for mid in pop_newly_online():
                    name, local, loc_id = _machine_info(mid)
                    notify_online(name, local)
                    _log_connectivity(mid, name, loc_id, 'online')

                # Cleanup bimestral: una vez por día en el primer ciclo del día
                today = date.today()
                if last_cleanup_day != today:
                    last_cleanup_day = today
                    _cleanup_connectivity_log()
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=_monitor, daemon=True, name='heartbeat-monitor').start()


def create_app() -> Flask:
    """
    Application factory.
    Crea y configura la app Flask con todos sus blueprints y middleware.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))

    app = Flask(
        __name__,
        static_folder=os.path.join(base_dir, 'static'),
        template_folder=os.path.join(base_dir, 'templates'),
    )
    app.secret_key = SECRET_KEY
    app.config['PERMANENT_SESSION_LIFETIME'] = SESSION_TIMEOUT

    CORS(app)

    # â”€â”€ Sentry â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        environment="production",
    )

    _configure_logging(app)

    app.before_request(check_session_timeout)
    app.before_request(before_request_log)
    app.after_request(after_request_log)

    # Blueprints activos en el backend actual.
    from blueprints.auth.routes        import auth_bp
    from blueprints.admin.routes       import admin_bp
    from blueprints.users.routes       import users_bp
    from blueprints.counters.routes    import counters_bp
    from blueprints.machines.routes    import machines_bp
    from blueprints.esp32.routes       import esp32_bp
    from blueprints.qr.routes          import qr_bp
    from blueprints.packages.routes    import packages_bp
    from blueprints.locations.routes   import locations_bp
    from blueprints.messages.routes    import messages_bp
    from blueprints.dashboard.routes   import dashboard_bp
    from blueprints.owners.routes      import owners_bp
    from blueprints.roles.routes       import roles_bp
    from blueprints.socios.routes      import socios_bp
    from blueprints.inversiones.routes import inversiones_bp
    from blueprints.pagos.routes       import pagos_bp
    from blueprints.liquidaciones.routes import liquidaciones_bp
    from blueprints.historial.routes   import historial_bp
    from blueprints.logs.routes        import logs_bp
    from blueprints.devoluciones.routes import devoluciones_bp
    from blueprints.firmware.routes    import firmware_bp

    for blueprint in (
        auth_bp,
        admin_bp,
        users_bp,
        counters_bp,
        machines_bp,
        esp32_bp,
        qr_bp,
        packages_bp,
        locations_bp,
        messages_bp,
        dashboard_bp,
        owners_bp,
        roles_bp,
        socios_bp,
        inversiones_bp,
        pagos_bp,
        liquidaciones_bp,
        historial_bp,
        logs_bp,
        devoluciones_bp,
        firmware_bp,
    ):
        app.register_blueprint(blueprint)

    _start_heartbeat_monitor()

    return app

