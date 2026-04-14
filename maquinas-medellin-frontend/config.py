import os
from datetime import timedelta

# ── Sesión ────────────────────────────────────────────────────────────────────
SECRET_KEY = 'maquinasmedellin_sk_v2_8h_timeout_2026'
SESSION_TIMEOUT = timedelta(hours=8)

# ── Base de datos ─────────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST",     "mysql")
DB_USER     = os.getenv("DB_USER",     "myuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mypassword")
DB_NAME     = os.getenv("DB_NAME",     "maquinasmedellin")
DB_PORT     = 3306

# ── Sentry ────────────────────────────────────────────────────────────────────
SENTRY_DSN = (
    "https://5fc281c2ace4860969f2f1f6fa10039d"
    "@o4510071013310464.ingest.us.sentry.io/4510071047454720"
)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE         = 'logs/maquinas.log'
LOG_MAX_BYTES    = 10 * 1024 * 1024   # 10 MB
LOG_BACKUP_COUNT = 10
LOGGER_NAME      = 'maquinas'         # nombre compartido por todos los módulos

# Rutas excluidas del access_log (polling, assets, health checks)
SKIP_ACCESS_LOG = (
    '/static',
    '/favicon',
    '/api/logs',
    '/api/esp32/check-commands',
    '/api/esp32/heartbeat',
    '/api/esp32/status',
    '/api/tft/',
)

# Endpoints que no requieren verificación de sesión
SESSION_SKIP = {'mostrar_login', 'procesar_login', 'static', None}
