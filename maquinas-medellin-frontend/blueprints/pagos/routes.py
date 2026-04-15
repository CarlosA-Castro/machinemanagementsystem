import logging

from flask import Blueprint, request, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.transactions import log_transaction
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

pagos_bp = Blueprint('pagos', __name__)


@pagos_bp.route('/api/pagoscuotas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['socio_id', 'anio', 'monto'])
def crear_pago_cuota():
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

        cursor.execute(
            """
            SELECT id FROM pagoscuotas
            WHERE socio_id = %s AND anio = %s
            """,
            (data['socio_id'], data['anio']),
        )
        if cursor.fetchone():
            return api_response(
                'E007',
                http_status=400,
                data={'message': f'Ya existe un pago para el año {data["anio"]}'},
            )

        cursor.execute(
            """
            INSERT INTO pagoscuotas (
                socio_id, anio, monto, fecha_pago, metodo_pago,
                comprobante, estado, notas
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data['socio_id'],
                data['anio'],
                data['monto'],
                data.get('fecha_pago'),
                data.get('metodo_pago', 'efectivo'),
                data.get('comprobante', ''),
                data.get('estado', 'pendiente'),
                data.get('notas', ''),
            ),
        )
        pago_id = cursor.lastrowid
        connection.commit()

        logger.info(
            f"Pago de cuota creado | ID: {pago_id} | Socio: {data['socio_id']} | "
            f"Año: {data['anio']} | Monto: ${float(data['monto']):,.0f} | "
            f"Método: {data.get('metodo_pago', 'efectivo')}"
        )
        log_transaction(
            tipo='pago_cuota',
            categoria='financiero',
            descripcion=f"Pago cuota anual {data['anio']} | socio {data['socio_id']} vía {data.get('metodo_pago', 'efectivo')}",
            usuario=session.get('user_name'),
            usuario_id=session.get('user_id'),
            entidad='socio',
            entidad_id=data['socio_id'],
            monto=float(data['monto']),
            datos_extra={
                'pago_id': pago_id,
                'anio': data['anio'],
                'metodo_pago': data.get('metodo_pago', 'efectivo'),
                'estado': data.get('estado', 'pendiente'),
                'comprobante': data.get('comprobante', ''),
            },
        )
        return api_response('S002', status='success', data={'pago_id': pago_id})
    except Exception as e:
        logger.error(f"Error creando pago de cuota: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@pagos_bp.route('/api/pagoscuotas/<int:pago_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def actualizar_pago_cuota(pago_id):
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM pagoscuotas WHERE id = %s", (pago_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'pago_id': pago_id})

        update_fields = []
        update_values = []
        for field in ['fecha_pago', 'metodo_pago', 'comprobante', 'estado', 'notas']:
            if field in data:
                update_fields.append(f"{field} = %s")
                update_values.append(data[field])

        if not update_fields:
            return api_response('E005', http_status=400, data={'message': 'No hay campos para actualizar'})

        update_fields.append("updated_at = NOW()")
        update_values.append(pago_id)
        cursor.execute(f"UPDATE pagoscuotas SET {', '.join(update_fields)} WHERE id = %s", update_values)
        connection.commit()
        logger.info(f"Pago de cuota actualizado: ID {pago_id}")
        return api_response('S003', status='success')
    except Exception as e:
        logger.error(f"Error actualizando pago de cuota: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@pagos_bp.route('/api/pagoscuotas/<int:pago_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_pago_cuota(pago_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM pagoscuotas WHERE id = %s", (pago_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'pago_id': pago_id})

        cursor.execute("DELETE FROM pagoscuotas WHERE id = %s", (pago_id,))
        connection.commit()
        logger.info(f"Pago de cuota eliminado: ID {pago_id}")
        return api_response('S004', status='success')
    except Exception as e:
        logger.error(f"Error eliminando pago de cuota: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
