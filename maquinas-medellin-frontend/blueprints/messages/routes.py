import logging
import re

import sentry_sdk
from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.messages import MessageService
from utils.responses import api_response, handle_api_errors
from utils.timezone import parse_db_datetime
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

messages_bp = Blueprint('messages', __name__)


@messages_bp.route('/api/mensajes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_mensajes():
    """Obtener todos los mensajes del sistema"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT id, message_code, message_type, message_text, language_code,
                   created_at, updated_at
            FROM system_messages
            ORDER BY message_code, language_code
        """)

        mensajes = cursor.fetchall()

        for mensaje in mensajes:
            if mensaje['created_at']:
                mensaje['created_at'] = parse_db_datetime(mensaje['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            if mensaje['updated_at']:
                mensaje['updated_at'] = parse_db_datetime(mensaje['updated_at']).strftime('%Y-%m-%d %H:%M:%S')

        return jsonify(mensajes)

    except Exception as e:
        logger.error(f"Error obteniendo mensajes: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@messages_bp.route('/api/mensajes', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['message_code', 'message_type', 'message_text'])
def crear_mensaje():
    """Crear un nuevo mensaje"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        message_code = data['message_code'].upper()
        message_type = data['message_type']
        message_text = data['message_text']
        language_code = data.get('language_code', 'es')

        if message_type not in ['error', 'success', 'warning', 'info']:
            return api_response('E005', http_status=400, data={'field': 'message_type'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT id FROM system_messages
            WHERE message_code = %s AND language_code = %s
        """, (message_code, language_code))

        if cursor.fetchone():
            return api_response(
                'E007',
                http_status=400,
                data={'message': f'El código {message_code} ya existe para el idioma {language_code}'}
            )

        cursor.execute("""
            INSERT INTO system_messages (message_code, message_type, message_text, language_code)
            VALUES (%s, %s, %s, %s)
        """, (message_code, message_type, message_text, language_code))

        connection.commit()

        MessageService.clear_cache()

        logger.info(f"Mensaje creado: {message_code} ({message_type})")

        return api_response('S002', status='success', data={'message_id': cursor.lastrowid})

    except Exception as e:
        logger.error(f"Error creando mensaje: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@messages_bp.route('/api/mensajes/<int:mensaje_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def actualizar_mensaje(mensaje_id):
    """Actualizar un mensaje existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()

        if not data:
            return api_response('E005', http_status=400)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT message_code FROM system_messages WHERE id = %s", (mensaje_id,))
        mensaje = cursor.fetchone()

        if not mensaje:
            return api_response('E002', http_status=404, data={'mensaje_id': mensaje_id})

        update_fields = []
        update_values = []

        if 'message_text' in data:
            update_fields.append("message_text = %s")
            update_values.append(data['message_text'])

        if 'message_type' in data:
            if data['message_type'] not in ['error', 'success', 'warning', 'info']:
                return api_response('E005', http_status=400, data={'field': 'message_type'})
            update_fields.append("message_type = %s")
            update_values.append(data['message_type'])

        if 'language_code' in data:
            update_fields.append("language_code = %s")
            update_values.append(data['language_code'])

        if not update_fields:
            return api_response('E005', http_status=400, data={'message': 'No hay campos para actualizar'})

        update_values.append(mensaje_id)
        cursor.execute(
            f"UPDATE system_messages SET {', '.join(update_fields)} WHERE id = %s",
            update_values
        )
        connection.commit()

        MessageService.clear_cache()

        logger.info(f"Mensaje actualizado: {mensaje['message_code']} (ID: {mensaje_id})")

        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando mensaje: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@messages_bp.route('/api/mensajes/<int:mensaje_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_mensaje(mensaje_id):
    """Eliminar un mensaje"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT message_code FROM system_messages WHERE id = %s", (mensaje_id,))
        mensaje = cursor.fetchone()

        if not mensaje:
            return api_response('E002', http_status=404, data={'mensaje_id': mensaje_id})

        codigos_esenciales = ['E001', 'E002', 'A001', 'S001']
        if mensaje['message_code'] in codigos_esenciales:
            return api_response(
                'E007',
                http_status=400,
                data={'message': 'No se pueden eliminar mensajes del sistema esenciales'}
            )

        cursor.execute("DELETE FROM system_messages WHERE id = %s", (mensaje_id,))
        connection.commit()

        MessageService.clear_cache()

        logger.info(f"Mensaje eliminado: {mensaje['message_code']} (ID: {mensaje_id})")

        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando mensaje: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@messages_bp.route('/api/mensajes/recargar-cache', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def recargar_cache_mensajes():
    """Forzar recarga del cache de mensajes"""
    try:
        MessageService.clear_cache()
        logger.info("Cache de mensajes recargado")
        return api_response('S003', status='success', data={'message': 'Cache recargado'})
    except Exception as e:
        logger.error(f"Error recargando cache: {e}")
        return api_response('E001', http_status=500)


@messages_bp.route('/api/mensajes/buscar', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def buscar_mensajes():
    """Buscar mensajes con filtros"""
    connection = None
    cursor = None
    try:
        query  = request.args.get('q', '').strip()
        tipo   = request.args.get('tipo', '')
        idioma = request.args.get('idioma', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        condiciones = []
        parametros  = []

        if query:
            condiciones.append("(message_code LIKE %s OR message_text LIKE %s)")
            parametros.extend([f"%{query}%", f"%{query}%"])

        if tipo and tipo != 'todos':
            condiciones.append("message_type = %s")
            parametros.append(tipo)

        if idioma and idioma != 'todos':
            condiciones.append("language_code = %s")
            parametros.append(idioma)

        where_clause = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""

        sql = f"""
            SELECT id, message_code, message_type, message_text, language_code,
                   created_at, updated_at
            FROM system_messages
            {where_clause}
            ORDER BY message_code, language_code
        """

        cursor.execute(sql, parametros)
        mensajes = cursor.fetchall()

        for mensaje in mensajes:
            if mensaje['created_at']:
                mensaje['created_at'] = parse_db_datetime(mensaje['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            if mensaje['updated_at']:
                mensaje['updated_at'] = parse_db_datetime(mensaje['updated_at']).strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({
            'resultados': mensajes,
            'total': len(mensajes),
            'parametros': {'query': query, 'tipo': tipo, 'idioma': idioma}
        })

    except Exception as e:
        logger.error(f"Error buscando mensajes: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@messages_bp.route('/api/mensajes/validar-codigo/<codigo>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def validar_codigo_mensaje(codigo):
    """Validar si un código de mensaje está disponible"""
    connection = None
    cursor = None
    try:
        if not re.match(r'^[A-Z][0-9]{3}$', codigo):
            return jsonify({
                'valido': False,
                'mensaje': 'Formato inválido. Debe ser letra mayúscula seguida de 3 números (ej: E001)'
            })

        connection = get_db_connection()
        if not connection:
            return jsonify({'valido': False, 'mensaje': 'Error de conexión a la base de datos'})

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT language_code, message_type, message_text
            FROM system_messages
            WHERE message_code = %s
        """, (codigo,))

        mensajes = cursor.fetchall()

        if not mensajes:
            return jsonify({
                'valido': True,
                'disponible': True,
                'mensaje': 'Código disponible para todos los idiomas'
            })

        idiomas_existentes  = [m['language_code'] for m in mensajes]
        idiomas_disponibles = ['es', 'en']
        idiomas_faltantes   = [i for i in idiomas_disponibles if i not in idiomas_existentes]

        if not idiomas_faltantes:
            return jsonify({
                'valido': True,
                'disponible': False,
                'mensaje': 'Código ya existe en todos los idiomas (es, en)',
                'detalles': mensajes
            })

        return jsonify({
            'valido': True,
            'disponible': True,
            'mensaje': f'Código disponible para idiomas: {", ".join(idiomas_faltantes)}',
            'idiomas_faltantes': idiomas_faltantes,
            'detalles': mensajes
        })

    except Exception as e:
        logger.error(f"Error validando código: {e}")
        return jsonify({'valido': False, 'mensaje': f'Error interno: {str(e)}'})
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()
