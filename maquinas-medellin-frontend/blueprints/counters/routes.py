import logging

import sentry_sdk
from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time

logger = logging.getLogger(LOGGER_NAME)

counters_bp = Blueprint('counters', __name__)


@counters_bp.route('/api/contador-global-vendidos', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_vendidos():
    """QR vendidos (con paquetes) y valor total del día."""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT COUNT(DISTINCT qh.qr_code) as total_vendidos
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) = %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
        """, (fecha,))
        resultado = cursor.fetchone()

        cursor.execute("""
            SELECT COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
        """, (fecha,))
        ventas = cursor.fetchone()

        logger.info(f"Contador global vendidos: {resultado['total_vendidos'] or 0} QR vendidos hoy")

        return jsonify({
            'total_vendidos': resultado['total_vendidos'] or 0,
            'valor_total':    float(ventas['valor_total'] or 0),
            'fecha':          fecha,
            'timestamp':      get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo contador global vendidos: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@counters_bp.route('/api/contador-global-escaneados', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_escaneados():
    """QR escaneados totales del día con desglose por tipo."""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT COUNT(*) as total_escaneados
            FROM qrhistory
            WHERE DATE(fecha_hora) = %s
        """, (fecha,))
        resultado = cursor.fetchone()

        cursor.execute("""
            SELECT
                COUNT(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1 THEN 1 END) as con_paquete,
                COUNT(CASE WHEN qr.turnPackageId IS NULL OR qr.turnPackageId = 1 THEN 1 END) as sin_paquete
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) = %s
        """, (fecha,))
        desglose = cursor.fetchone()

        logger.info(f"Contador global escaneados: {resultado['total_escaneados'] or 0} hoy")

        return jsonify({
            'total_escaneados': resultado['total_escaneados'] or 0,
            'con_paquete':      desglose['con_paquete'] or 0,
            'sin_paquete':      desglose['sin_paquete'] or 0,
            'fecha':            fecha,
            'timestamp':        get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo contador global escaneados: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@counters_bp.route('/api/contador-global-turnos', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_turnos():
    """Turnos utilizados del día con desglose por máquina."""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT COUNT(*) as turnos_utilizados
            FROM turnusage
            WHERE DATE(usedAt) = %s
        """, (fecha,))
        resultado = cursor.fetchone()

        cursor.execute("""
            SELECT m.name as maquina_nombre, COUNT(tu.id) as turnos
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE DATE(tu.usedAt) = %s
            GROUP BY m.id, m.name
            ORDER BY turnos DESC
        """, (fecha,))
        por_maquina = cursor.fetchall()

        logger.info(f"Contador global turnos: {resultado['turnos_utilizados'] or 0} hoy")

        return jsonify({
            'turnos_utilizados': resultado['turnos_utilizados'] or 0,
            'por_maquina':       por_maquina,
            'fecha':             fecha,
            'timestamp':         get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo contador global turnos: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@counters_bp.route('/api/contador-global-resumen', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_contador_global_resumen():
    """Resumen completo de todos los contadores globales del día."""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_vendidos,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
        """, (fecha,))
        ventas = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as total_escaneados FROM qrhistory WHERE DATE(fecha_hora) = %s", (fecha,))
        escaneados = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as turnos_utilizados FROM turnusage WHERE DATE(usedAt) = %s", (fecha,))
        turnos = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as fallas_reportadas FROM machinefailures WHERE DATE(reported_at) = %s", (fecha,))
        fallas = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as reportes_maquinas FROM errorreport WHERE DATE(reportedAt) = %s", (fecha,))
        reportes = cursor.fetchone()

        logger.info(f"Resumen global: {ventas['total_vendidos'] or 0} vendidos, {turnos['turnos_utilizados'] or 0} turnos")

        return jsonify({
            'fecha':     fecha,
            'ventas':    {'total_vendidos': ventas['total_vendidos'] or 0,    'valor_total': float(ventas['valor_total'] or 0)},
            'escaneados': {'total_escaneados': escaneados['total_escaneados'] or 0},
            'turnos':    {'turnos_utilizados': turnos['turnos_utilizados'] or 0},
            'fallas':    {'fallas_reportadas': fallas['fallas_reportadas'] or 0},
            'reportes':  {'reportes_maquinas': reportes['reportes_maquinas'] or 0},
            'timestamp': get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo resumen global: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
