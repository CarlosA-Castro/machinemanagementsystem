from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Instancia compartida del rate limiter.
# Se inicializa con init_app(app) en factory.py.
limiter = Limiter(key_func=get_remote_address, default_limits=[])
