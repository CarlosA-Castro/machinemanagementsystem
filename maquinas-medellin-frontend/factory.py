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

    # Logger compartido por todos los módulos del proyecto
    maquinas_logger = logging.getLogger(LOGGER_NAME)
    maquinas_logger.addHandler(file_handler)
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

    # ── Sentry ────────────────────────────────────────────────────────────────
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        environment="production",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    _configure_logging(app)

    # ── Middleware (before / after request) ───────────────────────────────────
    app.before_request(check_session_timeout)
    app.before_request(before_request_log)
    app.after_request(after_request_log)

    # ── Blueprints ────────────────────────────────────────────────────────────
    # Se registran en las fases 2-3. Descomentar a medida que se completan.
    #
    # from blueprints.auth.routes    import auth_bp
    # from blueprints.admin.routes   import admin_bp
    # from blueprints.users.routes   import users_bp
    # from blueprints.machines.routes import machines_bp
    # from blueprints.esp32.routes   import esp32_bp
    # from blueprints.qr.routes      import qr_bp
    # from blueprints.sales.routes   import sales_bp
    # from blueprints.partners.routes import partners_bp
    # from blueprints.logs.routes    import logs_bp
    # from blueprints.counters.routes import counters_bp
    #
    # app.register_blueprint(auth_bp)
    # app.register_blueprint(admin_bp)
    # app.register_blueprint(users_bp)
    # app.register_blueprint(machines_bp)
    # app.register_blueprint(esp32_bp)
    # app.register_blueprint(qr_bp)
    # app.register_blueprint(sales_bp)
    # app.register_blueprint(partners_bp)
    # app.register_blueprint(logs_bp)
    # app.register_blueprint(counters_bp)

    return app
