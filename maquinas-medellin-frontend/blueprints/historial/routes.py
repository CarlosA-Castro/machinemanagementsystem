import logging

import sentry_sdk
from flask import Blueprint, jsonify, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.timezone import parse_db_datetime

logger = logging.getLogger(LOGGER_NAME)

historial_bp = Blueprint('historial', __name__)


@historial_bp.route('/api/historial-completo', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_completo():
    """Obtener historial completo de QR escaneados."""
    connection = None
    cursor = None
    try:
        user_id = session.get('user_id')
        local = session.get('user_local', 'El Mekatiadero')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        if session.get('user_role') == 'admin':
            cursor.execute(
                """
                SELECT
                    h.id,
                    h.qr_code,
                    h.user_name,
                    h.qr_name,
                    h.fecha_hora,
                    qr.turnPackageId,
                    tp.name as package_name,
                    tp.price as precio_paquete,
                    ut.turns_remaining
                FROM qrhistory h
                LEFT JOIN qrcode qr ON qr.code = h.qr_code
                LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
                LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
                WHERE h.local = %s
                  AND h.es_venta_real = TRUE
                ORDER BY h.fecha_hora DESC
                LIMIT 100
                """,
                (local,),
            )
        else:
            cursor.execute(
                """
                SELECT
                    h.id,
                    h.qr_code,
                    h.user_name,
                    h.qr_name,
                    h.fecha_hora,
                    qr.turnPackageId,
                    tp.name as package_name,
                    tp.price as precio_paquete,
                    ut.turns_remaining
                FROM qrhistory h
                LEFT JOIN qrcode qr ON qr.code = h.qr_code
                LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
                LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
                WHERE (h.user_id = %s OR h.local = %s)
                  AND h.es_venta_real = TRUE
                ORDER BY h.fecha_hora DESC
                LIMIT 50
                """,
                (user_id, local),
            )

        historial = cursor.fetchall()

        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    logger.warning(f"Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])

            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1

        logger.info(f"Historial obtenido: {len(historial)} registros")
        return jsonify(historial)

    except Exception as e:
        logger.error(f"Error obteniendo historial completo: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@historial_bp.route('/api/historial-qr/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_qr(qr_code):
    """Obtener historial específico de un código QR."""
    connection = None
    cursor = None
    try:
        logger.info(f"Obteniendo historial para QR: {qr_code}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT
                h.id,
                h.qr_code,
                h.user_name,
                h.qr_name,
                h.fecha_hora,
                qr.turnPackageId,
                tp.name as package_name,
                tp.price as precio_paquete,
                ut.turns_remaining
            FROM qrhistory h
            LEFT JOIN qrcode qr ON qr.code = h.qr_code
            LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
            LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
            WHERE h.qr_code = %s
            ORDER BY h.fecha_hora DESC
            LIMIT 20
            """,
            (qr_code,),
        )

        historial = cursor.fetchall()

        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    logger.warning(f"Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])

            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1

        logger.info(f"Historial obtenido para {qr_code}: {len(historial)} registros")

        if not historial:
            return api_response(
                'I001',
                status='info',
                data={'message': 'No hay historial para este QR', 'qr_code': qr_code},
            )

        return jsonify(historial)

    except Exception as e:
        logger.error(f"Error obteniendo historial del QR: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
