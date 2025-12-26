# response_utils.py
from functools import wraps
from flask import jsonify, request, session
import logging
from message_service import MessageService

logger = logging.getLogger(__name__)

def api_response(message_code: str, status: str = 'error', 
                http_status: int = 200, data: dict = None, **kwargs):
    """
    Helper para respuestas API estandarizadas
    """
    return MessageService.get_json_response(
        message_code, status, data, http_status, **kwargs
    )

def handle_api_errors(func):
    """
    Decorador para manejar errores en endpoints API
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error en {func.__name__}: {str(e)}", exc_info=True)
            
            # Determinar tipo de error
            if isinstance(e, ValueError):
                return api_response('E005', http_status=400)
            elif isinstance(e, KeyError):
                return api_response('E005', http_status=400)
            elif isinstance(e, PermissionError):
                return api_response('E004', http_status=403)
            elif "no encontrado" in str(e).lower() or isinstance(e, FileNotFoundError):
                return api_response('E002', http_status=404)
            elif "no autorizado" in str(e).lower():
                return api_response('E003', http_status=401)
            else:
                return api_response('E001', http_status=500)
    return wrapper

def require_login(roles=None):
    """
    Decorador para requerir autenticación y roles específicos
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                return api_response('A004', http_status=401)
            
            if roles and session.get('user_role') not in roles:
                return api_response('E004', http_status=403)
            
            return func(*args, **kwargs)
        return wrapper
    return decorator

def validate_required_fields(required_fields):
    """
    Decorador para validar campos requeridos
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.get_json() if request.is_json else request.form
            
            missing_fields = []
            for field in required_fields:
                if field not in data or not data[field]:
                    missing_fields.append(field)
            
            if missing_fields:
                return api_response(
                    'E005', 
                    http_status=400,
                    data={'missing_fields': missing_fields}
                )
            
            return func(*args, **kwargs)
        return wrapper
    return decorator