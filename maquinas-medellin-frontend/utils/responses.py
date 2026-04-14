import logging
from functools import wraps

from config import LOGGER_NAME
from utils.messages import MessageService

logger = logging.getLogger(LOGGER_NAME)


def api_response(
    message_code: str,
    status: str = 'error',
    http_status: int = 200,
    data: dict = None,
    **kwargs
):
    """Helper centralizado para respuestas API estandarizadas."""
    return MessageService.get_json_response(message_code, status, data, http_status, **kwargs)


def handle_api_errors(func):
    """
    Decorador que envuelve un endpoint y captura excepciones no manejadas.
    Retorna la respuesta de error estandarizada según el tipo de excepción.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error en {func.__name__}: {e}", exc_info=True)

            if isinstance(e, (ValueError, KeyError)):
                return api_response('E005', http_status=400)
            if isinstance(e, PermissionError):
                return api_response('E004', http_status=403)
            if isinstance(e, FileNotFoundError) or "no encontrado" in str(e).lower():
                return api_response('E002', http_status=404)
            if "no autorizado" in str(e).lower():
                return api_response('E003', http_status=401)
            return api_response('E001', http_status=500)

    return wrapper
