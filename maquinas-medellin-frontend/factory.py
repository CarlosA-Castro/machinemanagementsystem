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
    from blueprints.auth.routes import auth_bp
    from blueprints.admin.routes import admin_bp
    from blueprints.users.routes import users_bp
    from blueprints.counters.routes import counters_bp
    from blueprints.socios.routes import socios_bp
    from blueprints.inversiones.routes import inversiones_bp
    from blueprints.pagos.routes import pagos_bp
    from blueprints.liquidaciones.routes import liquidaciones_bp
    from blueprints.historial.routes import historial_bp
    from blueprints.logs.routes import logs_bp
    from blueprints.devoluciones.routes import devoluciones_bp

    for blueprint in (
        auth_bp,
        admin_bp,
        users_bp,
        counters_bp,
        socios_bp,
        inversiones_bp,
        pagos_bp,
        liquidaciones_bp,
        historial_bp,
        logs_bp,
        devoluciones_bp,
    ):
        app.register_blueprint(blueprint)

    return app

