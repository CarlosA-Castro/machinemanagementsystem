from functools import wraps

from flask import request

from utils.responses import api_response


def validate_required_fields(required_fields: list):
    """
    Decorador que valida que los campos requeridos estén presentes en
    el body JSON o en el form data del request.
    Retorna 400 con la lista de campos faltantes si alguno está ausente.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True) or request.form

            missing = [
                field for field in required_fields
                if field not in data or data[field] in (None, '', [])
            ]

            if missing:
                return api_response('E005', http_status=400, data={'missing_fields': missing})

            return func(*args, **kwargs)
        return wrapper
    return decorator
