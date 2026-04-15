import logging

from flask import Blueprint, request, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.transactions import log_transaction
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

inversiones_bp = Blueprint('inversiones', __name__)


@inversiones_bp.route('/api/inversiones', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['socio_id', 'maquina_id', 'porcentaje_inversion', 'fecha_inicio', 'monto_inicial'])
def crear_inversion():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM socios WHERE id = %s", (data['socio_id'],))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'socio_id': data['socio_id']})

        cursor.execute("SELECT id FROM machine WHERE id = %s", (data['maquina_id'],))
        if not cursor.fetchone():
            return api_response('M001', http_status=404, data={'machine_id': data['maquina_id']})

        cursor.execute(
            """
            SELECT COALESCE(SUM(porcentaje_inversion), 0) as porcentaje_ocupado
            FROM inversiones
            WHERE maquina_id = %s AND estado = 'activa'
            """,
            (data['maquina_id'],),
        )
        porcentaje_ocupado = cursor.fetchone()['porcentaje_ocupado'] or 0
        porcentaje_disponible = 100 - porcentaje_ocupado
        if float(data['porcentaje_inversion']) > porcentaje_disponible:
            return api_response(
                'E005',
                http_status=400,
                data={
                    'message': f'Porcentaje no disponible. Solo queda {porcentaje_disponible}%',
                    'porcentaje_disponible': porcentaje_disponible,
                },
            )

        cursor.execute(
            """
            INSERT INTO inversiones (
                socio_id, maquina_id, porcentaje_inversion, fecha_inicio,
                fecha_fin, monto_inicial, estado
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data['socio_id'],
                data['maquina_id'],
                data['porcentaje_inversion'],
                data['fecha_inicio'],
                data.get('fecha_fin'),
                data['monto_inicial'],
                data.get('estado', 'activa'),
            ),
        )
        inversion_id = cursor.lastrowid
        connection.commit()

        logger.info(
            f"Inversión creada | ID: {inversion_id} | Socio: {data['socio_id']} | "
            f"Máquina: {data['maquina_id']} | %: {data['porcentaje_inversion']} | "
            f"Monto: ${float(data['monto_inicial']):,.0f}"
        )
        log_transaction(
            tipo='inversion',
            categoria='financiero',
            descripcion=f"Nueva inversión {data['porcentaje_inversion']}% en máquina {data['maquina_id']} | socio {data['socio_id']}",
            usuario=session.get('user_name'),
            usuario_id=session.get('user_id'),
            maquina_id=data['maquina_id'],
            entidad='socio',
            entidad_id=data['socio_id'],
            monto=float(data['monto_inicial']),
            datos_extra={
                'inversion_id': inversion_id,
                'porcentaje_inversion': float(data['porcentaje_inversion']),
                'fecha_inicio': str(data['fecha_inicio']),
                'estado': data.get('estado', 'activa'),
            },
        )
        return api_response('S002', status='success', data={'inversion_id': inversion_id})
    except Exception as e:
        logger.error(f"Error creando inversión: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@inversiones_bp.route('/api/inversiones/<int:inversion_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def actualizar_inversion(inversion_id):
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM inversiones WHERE id = %s", (inversion_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'inversion_id': inversion_id})

        update_fields = []
        update_values = []
        for field in ['porcentaje_inversion', 'fecha_fin', 'estado', 'monto_inicial']:
            if field in data:
                update_fields.append(f"{field} = %s")
                update_values.append(data[field])

        if not update_fields:
            return api_response('E005', http_status=400, data={'message': 'No hay campos para actualizar'})

        update_fields.append("updated_at = NOW()")
        update_values.append(inversion_id)
        cursor.execute(f"UPDATE inversiones SET {', '.join(update_fields)} WHERE id = %s", update_values)
        connection.commit()
        logger.info(f"Inversión actualizada: ID {inversion_id}")
        return api_response('S003', status='success')
    except Exception as e:
        logger.error(f"Error actualizando inversión: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
