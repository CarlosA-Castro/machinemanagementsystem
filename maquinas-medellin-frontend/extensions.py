from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Instancia compartida del rate limiter.
# Se inicializa con init_app(app) en factory.py.
# default_limits aplica a todos los endpoints salvo los eximidos explícitamente.
limiter = Limiter(key_func=get_remote_address, default_limits=["300 per minute"])
