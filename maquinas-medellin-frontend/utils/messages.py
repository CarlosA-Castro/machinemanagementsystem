import logging
from datetime import datetime
from functools import lru_cache

from flask import json

from config import LOGGER_NAME
from database import get_db_connection

logger = logging.getLogger(LOGGER_NAME)


class MessageService:
    """
    Servicio para obtener mensajes i18n desde la tabla system_messages.
    Usa doble capa de caché: dict en memoria + lru_cache.
    """
    _cache: dict = {}

    # ── Obtención de mensajes ─────────────────────────────────────────────────

    @classmethod
    @lru_cache(maxsize=128)
    def get_message(cls, message_code: str, language_code: str = 'es', **kwargs) -> dict:
        """Obtiene un mensaje de la BD y aplica variables de formato."""
        try:
            cache_key = f"{message_code}_{language_code}"

            if cache_key in cls._cache:
                message_data = cls._cache[cache_key]
            else:
                connection = get_db_connection()
                if not connection:
                    return cls._get_default_message(message_code)

                cursor = connection.cursor(dictionary=True)
                cursor.execute(
                    """SELECT message_code, message_type, message_text, language_code
                       FROM system_messages
                       WHERE message_code = %s AND language_code = %s""",
                    (message_code, language_code)
                )
                message = cursor.fetchone()

                # Fallback a español si el idioma solicitado no existe
                if not message and language_code != 'es':
                    cursor.execute(
                        """SELECT message_code, message_type, message_text, language_code
                           FROM system_messages
                           WHERE message_code = %s AND language_code = 'es'""",
                        (message_code,)
                    )
                    message = cursor.fetchone()

                cursor.close()
                connection.close()

                if not message:
                    return cls._get_default_message(message_code)

                message_data = {
                    'code':     message['message_code'],
                    'type':     message['message_type'],
                    'text':     message['message_text'],
                    'language': message['language_code'],
                }
                cls._cache[cache_key] = message_data

            # Aplicar variables de formato
            formatted_text = message_data['text']
            if kwargs:
                try:
                    formatted_text = formatted_text.format(**kwargs)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Error formateando mensaje {message_code}: {e}")
                    formatted_text = f"{formatted_text} [Error de formato: {e}]"

            return {**message_data, 'formatted': formatted_text}

        except Exception as e:
            logger.error(f"Error obteniendo mensaje {message_code}: {e}")
            return cls._get_default_message(message_code)

    @classmethod
    def get_error_message(cls, error_code: str, **kwargs) -> str:
        """Retorna solo el texto formateado de un mensaje de error."""
        return cls.get_message(error_code, **kwargs).get('formatted', f"Error: {error_code}")

    @classmethod
    def get_json_response(
        cls,
        message_code: str,
        status: str = 'error',
        data: dict = None,
        http_status: int = 200,
        **kwargs
    ) -> tuple:
        """Construye una respuesta JSON estandarizada lista para retornar desde un endpoint."""
        message = cls.get_message(message_code, **kwargs)
        response = {
            'status':       status,
            'code':         message_code,
            'message':      message['formatted'],
            'message_type': message['type'],
            'timestamp':    datetime.now().isoformat(),
        }
        if data:
            response['data'] = data
        return response, http_status

    # ── Caché ──────────────────────────────────────────────────────────────────

    @classmethod
    def clear_cache(cls):
        """Limpia el caché en memoria y el lru_cache."""
        cls._cache.clear()
        cls.get_message.cache_clear()
        logger.info("Caché de mensajes limpiado")

    # ── Mensajes por defecto (sin BD) ─────────────────────────────────────────

    @classmethod
    def _get_default_message(cls, message_code: str) -> dict:
        defaults = {
            'E001': {'type': 'error',   'text': 'Error interno del servidor'},
            'E002': {'type': 'error',   'text': 'Recurso no encontrado'},
            'E003': {'type': 'error',   'text': 'No autorizado'},
            'E004': {'type': 'error',   'text': 'Acceso prohibido'},
            'E005': {'type': 'error',   'text': 'Parámetros inválidos'},
            'E006': {'type': 'error',   'text': 'Error de conexión a la base de datos'},
            'A001': {'type': 'error',   'text': 'Credenciales inválidas'},
            'S001': {'type': 'success', 'text': 'Operación exitosa'},
        }
        entry = defaults.get(message_code, {
            'type': 'error',
            'text': f'Mensaje no configurado: {message_code}',
        })
        return {
            'code':      message_code,
            'type':      entry['type'],
            'text':      entry['text'],
            'language':  'es',
            'formatted': entry['text'],
        }
