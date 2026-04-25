import csv
import io
import json
import logging
import os
import tempfile
import zipfile
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, send_file, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.logs import log_app_event
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

logs_bp = Blueprint('logs', __name__)


@logs_bp.route('/api/logs/transaccional-consolidado', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_logs_transaccional_consolidado():
    connection = None
    cursor = None
    try:
        hoy = get_colombia_time().strftime('%Y-%m-%d')
        fecha_inicio = request.args.get('fecha_inicio', hoy)
        fecha_fin = request.args.get('fecha_fin', hoy)
        limit_feed = int(request.args.get('limit', 100))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT qh.qr_code) AS paquetes_vendidos,
                COALESCE(SUM(tp.price), 0) AS ingresos_ventas
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            """,
            (fecha_inicio, fecha_fin),
        )
        kpi_ventas = cursor.fetchone()

        cursor.execute(
            """
            SELECT COUNT(*) AS turnos_jugados
            FROM turnusage
            WHERE DATE(usedAt) BETWEEN %s AND %s
            """,
            (fecha_inicio, fecha_fin),
        )
        kpi_turnos = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                COUNT(*) AS fallas_total,
                COALESCE(SUM(turnos_devueltos), 0) AS turnos_devueltos
            FROM machinefailures
            WHERE DATE(reported_at) BETWEEN %s AND %s
            """,
            (fecha_inicio, fecha_fin),
        )
        kpi_fallas = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                qh.fecha_hora,
                qh.qr_code,
                COALESCE(qr.qr_name, qh.qr_code) AS qr_name,
                tp.name AS paquete,
                tp.price AS precio,
                tp.turns AS turnos_paquete,
                qh.user_name AS cajero,
                COALESCE(NULLIF(qh.payment_method, ''), 'sin_registrar') AS payment_method
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            ORDER BY qh.fecha_hora DESC
            LIMIT 200
            """,
            (fecha_inicio, fecha_fin),
        )
        _metodo_labels = {
            'efectivo': 'Efectivo',
            'transferencia': 'Transferencia',
            'tarjeta': 'Tarjeta',
            'cheque': 'Cheque',
            'sin_registrar': 'Sin registrar',
        }
        ventas = []
        for venta in cursor.fetchall():
            row = dict(venta)
            row['precio'] = float(row['precio']) if row['precio'] else 0
            if row.get('fecha_hora') and hasattr(row['fecha_hora'], 'isoformat'):
                row['fecha_hora'] = row['fecha_hora'].isoformat()
            pm = row.get('payment_method') or 'sin_registrar'
            row['payment_method_label'] = _metodo_labels.get(pm, pm)
            ventas.append(row)

        cursor.execute(
            """
            SELECT
                tp.name AS paquete,
                COUNT(DISTINCT qh.qr_code) AS cantidad,
                COALESCE(SUM(tp.price), 0) AS total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            GROUP BY tp.id, tp.name
            ORDER BY cantidad DESC
            LIMIT 5
            """,
            (fecha_inicio, fecha_fin),
        )
        top_paquetes = []
        for paquete in cursor.fetchall():
            row = dict(paquete)
            row['total'] = float(row['total']) if row['total'] else 0
            top_paquetes.append(row)

        cursor.execute(
            """
            SELECT
                mf.id,
                mf.reported_at,
                mf.machine_id,
                COALESCE(mf.machine_name, 'Desconocida') AS machine_name,
                mf.station_index,
                COALESCE(qr.code, '') AS qr_code,
                COALESCE(qr.qr_name, '') AS qr_name,
                mf.turnos_devueltos,
                COALESCE(mf.notes, '') AS notes,
                COALESCE(mf.is_forced, 0) AS is_forced,
                COALESCE(mf.forced_by, '') AS forced_by
            FROM machinefailures mf
            LEFT JOIN qrcode qr ON mf.qr_code_id = qr.id
            WHERE DATE(mf.reported_at) BETWEEN %s AND %s
            ORDER BY mf.reported_at DESC
            LIMIT 300
            """,
            (fecha_inicio, fecha_fin),
        )
        fallas_esp32 = []
        for falla in cursor.fetchall():
            row = dict(falla)
            if row.get('reported_at') and hasattr(row['reported_at'], 'isoformat'):
                row['reported_at'] = row['reported_at'].isoformat()
            fallas_esp32.append(row)

        cursor.execute(
            """
            SELECT
                m.id,
                m.name AS nombre,
                m.status AS estado,
                COALESCE(tu_agg.turnos_periodo, 0)          AS turnos_periodo,
                COALESCE(mf_agg.fallas_periodo, 0)          AS fallas_periodo,
                COALESCE(mf_agg.turnos_devueltos_periodo, 0) AS turnos_devueltos_periodo,
                tu_agg.ultimo_uso
            FROM machine m
            LEFT JOIN (
                SELECT machineId,
                       COUNT(*)    AS turnos_periodo,
                       MAX(usedAt) AS ultimo_uso
                FROM turnusage
                WHERE DATE(usedAt) BETWEEN %s AND %s
                GROUP BY machineId
            ) tu_agg ON tu_agg.machineId = m.id
            LEFT JOIN (
                SELECT machine_id,
                       COUNT(*)               AS fallas_periodo,
                       SUM(turnos_devueltos)  AS turnos_devueltos_periodo
                FROM machinefailures
                WHERE DATE(reported_at) BETWEEN %s AND %s
                GROUP BY machine_id
            ) mf_agg ON mf_agg.machine_id = m.id
            ORDER BY turnos_periodo DESC
            """,
            (fecha_inicio, fecha_fin, fecha_inicio, fecha_fin),
        )
        por_maquina = []
        for maquina in cursor.fetchall():
            row = dict(maquina)
            row['turnos_devueltos_periodo'] = (
                float(row['turnos_devueltos_periodo']) if row['turnos_devueltos_periodo'] else 0
            )
            if row.get('ultimo_uso') and hasattr(row['ultimo_uso'], 'isoformat'):
                row['ultimo_uso'] = row['ultimo_uso'].isoformat()
            por_maquina.append(row)

        es_mismo_dia = fecha_inicio == fecha_fin
        if es_mismo_dia:
            cursor.execute(
                """
                SELECT
                    HOUR(qh.fecha_hora) AS periodo,
                    COUNT(DISTINCT qh.qr_code) AS ventas_count,
                    COALESCE(SUM(tp.price), 0) AS ventas_monto
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) = %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY HOUR(qh.fecha_hora)
                ORDER BY periodo
                """,
                (fecha_inicio,),
            )
            grafica_ventas = cursor.fetchall()

            cursor.execute(
                """
                SELECT HOUR(usedAt) AS periodo, COUNT(*) AS turnos
                FROM turnusage
                WHERE DATE(usedAt) = %s
                GROUP BY HOUR(usedAt)
                ORDER BY periodo
                """,
                (fecha_inicio,),
            )
            grafica_turnos = cursor.fetchall()
            tipo_grafica = 'horas'
        else:
            cursor.execute(
                """
                SELECT
                    DATE(qh.fecha_hora) AS periodo,
                    COUNT(DISTINCT qh.qr_code) AS ventas_count,
                    COALESCE(SUM(tp.price), 0) AS ventas_monto
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY periodo
                """,
                (fecha_inicio, fecha_fin),
            )
            grafica_ventas = cursor.fetchall()

            cursor.execute(
                """
                SELECT DATE(usedAt) AS periodo, COUNT(*) AS turnos
                FROM turnusage
                WHERE DATE(usedAt) BETWEEN %s AND %s
                GROUP BY DATE(usedAt)
                ORDER BY periodo
                """,
                (fecha_inicio, fecha_fin),
            )
            grafica_turnos = cursor.fetchall()
            tipo_grafica = 'dias'

        grafica_ventas_fmt = []
        for punto in grafica_ventas:
            row = dict(punto)
            row['ventas_monto'] = float(row['ventas_monto']) if row['ventas_monto'] else 0
            if hasattr(row.get('periodo'), 'isoformat'):
                row['periodo'] = row['periodo'].isoformat()
            grafica_ventas_fmt.append(row)

        grafica_turnos_fmt = []
        for punto in grafica_turnos:
            row = dict(punto)
            if hasattr(row.get('periodo'), 'isoformat'):
                row['periodo'] = row['periodo'].isoformat()
            grafica_turnos_fmt.append(row)

        cursor.execute(
            """
            SELECT
                tl.id, tl.tipo, tl.categoria, tl.descripcion,
                tl.usuario, tl.maquina_nombre, tl.maquina_id,
                tl.entidad, tl.entidad_id, tl.monto,
                tl.datos_extra, tl.ip_address, tl.estado, tl.created_at
            FROM transaction_logs tl
            WHERE DATE(tl.created_at) BETWEEN %s AND %s
            ORDER BY tl.created_at DESC
            LIMIT %s
            """,
            (fecha_inicio, fecha_fin, limit_feed),
        )
        feed = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get('monto') is not None:
                item['monto'] = float(item['monto'])
            if item.get('created_at') and hasattr(item['created_at'], 'isoformat'):
                item['created_at'] = item['created_at'].isoformat()
            if isinstance(item.get('datos_extra'), str):
                try:
                    item['datos_extra'] = json.loads(item['datos_extra'])
                except Exception:
                    item['datos_extra'] = {}
            feed.append(item)

        return jsonify(
            {
                'periodo': {'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin, 'tipo': tipo_grafica},
                'kpis': {
                    'ingresos_ventas': float(kpi_ventas['ingresos_ventas'] or 0),
                    'paquetes_vendidos': int(kpi_ventas['paquetes_vendidos'] or 0),
                    'turnos_jugados': int(kpi_turnos['turnos_jugados'] or 0),
                    'fallas_total': int(kpi_fallas['fallas_total'] or 0),
                    'turnos_devueltos': int(kpi_fallas['turnos_devueltos'] or 0),
                },
                'ventas': ventas,
                'top_paquetes': top_paquetes,
                'fallas_esp32': fallas_esp32,
                'por_maquina': por_maquina,
                'grafica': {'tipo': tipo_grafica, 'ventas': grafica_ventas_fmt, 'turnos': grafica_turnos_fmt},
                'feed': feed,
                'timestamp': get_colombia_time().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Error en transaccional-consolidado: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if connection:
            try:
                connection.close()
            except Exception:
                pass


@logs_bp.route('/api/logs/consola-completa', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_logs_consola():
    connection = None
    cursor = None
    try:
        limit = int(request.args.get('limit', 200))
        nivel = request.args.get('nivel', 'todos')
        buscar = request.args.get('buscar', '').strip()
        fuente = request.args.get('fuente', 'todos')
        orden = request.args.get('orden', 'desc')
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        tail = request.args.get('tail', 'false').lower() == 'true'

        logs_data = []
        all_logs = []

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        if fuente in ['todos', 'app']:
            try:
                app_query = """
                    SELECT
                        'app' as fuente,
                        level as nivel,
                        message as mensaje,
                        module as modulo,
                        ip_address,
                        user_id,
                        created_at,
                        NULL as metodo,
                        NULL as path,
                        NULL as status_code,
                        NULL as response_time_ms
                    FROM app_logs
                    WHERE 1=1
                """
                params = []
                if nivel != 'todos':
                    app_query += " AND level = %s"
                    params.append(nivel)
                if buscar:
                    app_query += " AND (message LIKE %s OR module LIKE %s)"
                    params.extend([f'%{buscar}%', f'%{buscar}%'])
                if fecha_inicio:
                    app_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    app_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)
                app_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)
                cursor.execute(app_query, params)
                all_logs.extend(cursor.fetchall())
            except Exception as e:
                logger.error(f"Error ejecutando consulta app logs: {e}")

        if fuente in ['todos', 'access']:
            try:
                access_query = """
                    SELECT
                        'access' as fuente,
                        CASE
                            WHEN status_code >= 500 THEN 'ERROR'
                            WHEN status_code >= 400 THEN 'WARNING'
                            ELSE 'INFO'
                        END as nivel,
                        CONCAT(method, ' ', path, ' -> ', status_code) as mensaje,
                        'http' as modulo,
                        ip_address,
                        user_id,
                        created_at,
                        method,
                        path,
                        status_code,
                        response_time_ms
                    FROM access_logs
                    WHERE 1=1
                """
                params = []
                if nivel != 'todos':
                    if nivel == 'ERROR':
                        access_query += " AND status_code >= 500"
                    elif nivel == 'WARNING':
                        access_query += " AND status_code BETWEEN 400 AND 499"
                    elif nivel == 'INFO':
                        access_query += " AND status_code < 400"
                if buscar:
                    access_query += " AND (path LIKE %s OR method LIKE %s OR ip_address LIKE %s)"
                    params.extend([f'%{buscar}%', f'%{buscar}%', f'%{buscar}%'])
                if fecha_inicio:
                    access_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    access_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)
                access_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)
                cursor.execute(access_query, params)
                all_logs.extend(cursor.fetchall())
            except Exception as e:
                logger.error(f"Error ejecutando consulta access logs: {e}")

        if fuente in ['todos', 'session']:
            try:
                session_query = """
                    SELECT
                        'session' as fuente,
                        'INFO' as nivel,
                        CONCAT('Sesión usuario: ', COALESCE(u.name, 'Desconocido'),
                               ' - Login: ', DATE_FORMAT(s.loginTime, '%%H:%%i:%%s')) as mensaje,
                        'session' as modulo,
                        NULL as ip_address,
                        s.userId as user_id,
                        s.loginTime as created_at,
                        NULL as metodo,
                        NULL as path,
                        NULL as status_code,
                        NULL as response_time_ms
                    FROM sessionlog s
                    LEFT JOIN users u ON s.userId = u.id
                    WHERE 1=1
                """
                params = []
                if buscar:
                    session_query += " AND u.name LIKE %s"
                    params.append(f'%{buscar}%')
                if fecha_inicio:
                    session_query += " AND DATE(s.loginTime) >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    session_query += " AND DATE(s.loginTime) <= %s"
                    params.append(fecha_fin)
                session_query += f" ORDER BY s.loginTime {orden.upper()} LIMIT %s"
                params.append(limit)
                cursor.execute(session_query, params)
                all_logs.extend(cursor.fetchall())
            except Exception as e:
                logger.error(f"Error ejecutando consulta session logs: {e}")

        if fuente in ['todos', 'error']:
            try:
                error_query = """
                    SELECT
                        'error' as fuente,
                        level as nivel,
                        SUBSTRING(message, 1, 300) as mensaje,
                        module as modulo,
                        ip_address,
                        user_id,
                        created_at,
                        NULL as metodo,
                        endpoint as path,
                        NULL as status_code,
                        NULL as response_time_ms
                    FROM error_logs
                    WHERE 1=1
                """
                params = []
                if buscar:
                    error_query += " AND (message LIKE %s OR module LIKE %s)"
                    params.extend([f'%{buscar}%', f'%{buscar}%'])
                if fecha_inicio:
                    error_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    error_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)
                error_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)
                cursor.execute(error_query, params)
                all_logs.extend(cursor.fetchall())
            except Exception as e:
                logger.error(f"Error ejecutando consulta error logs: {e}")

        if fuente in ['todos', 'transacciones']:
            try:
                txn_query = """
                    SELECT
                        'transaccion' as fuente,
                        CASE
                            WHEN estado = 'error' THEN 'ERROR'
                            WHEN estado = 'advertencia' THEN 'WARNING'
                            ELSE 'INFO'
                        END as nivel,
                        CONCAT('[', UPPER(tipo), '] ', descripcion) as mensaje,
                        tipo as modulo,
                        ip_address,
                        usuario_id as user_id,
                        created_at,
                        NULL as metodo,
                        NULL as path,
                        NULL as status_code,
                        NULL as response_time_ms,
                        tipo,
                        categoria,
                        usuario,
                        maquina_id,
                        maquina_nombre,
                        entidad,
                        entidad_id,
                        monto,
                        moneda,
                        datos_extra,
                        estado
                    FROM transaction_logs
                    WHERE 1=1
                """
                params = []
                if buscar:
                    txn_query += " AND (descripcion LIKE %s OR tipo LIKE %s OR usuario LIKE %s OR maquina_nombre LIKE %s)"
                    params.extend([f'%{buscar}%'] * 4)
                if fecha_inicio:
                    txn_query += " AND DATE(created_at) >= %s"
                    params.append(fecha_inicio)
                if fecha_fin:
                    txn_query += " AND DATE(created_at) <= %s"
                    params.append(fecha_fin)
                txn_query += f" ORDER BY created_at {orden.upper()} LIMIT %s"
                params.append(limit)
                cursor.execute(txn_query, params)
                all_logs.extend(cursor.fetchall())
            except Exception as e:
                logger.error(f"Error ejecutando consulta transaction logs: {e}")

        try:
            all_logs.sort(
                key=lambda item: item['created_at'] if item['created_at'] else datetime.min,
                reverse=(orden.lower() == 'desc'),
            )
        except Exception as e:
            logger.warning(f"Error ordenando logs: {e}")

        all_logs = all_logs[:limit]
        for log in all_logs:
            try:
                log_entry = {
                    'fuente': log.get('fuente', 'unknown'),
                    'nivel': log.get('nivel', 'INFO'),
                    'mensaje': log.get('mensaje', '') or '',
                    'modulo': log.get('modulo', '') or '',
                    'timestamp': log.get('created_at').isoformat() if log.get('created_at') else '',
                    'ip': log.get('ip_address', '') or '',
                    'user_id': log.get('user_id'),
                }
                if log.get('fuente') == 'access':
                    log_entry.update(
                        {
                            'metodo': log.get('metodo', ''),
                            'path': log.get('path', ''),
                            'status_code': log.get('status_code'),
                            'response_time': log.get('response_time_ms'),
                        }
                    )
                elif log.get('fuente') == 'transaccion':
                    datos_extra = log.get('datos_extra')
                    if isinstance(datos_extra, str):
                        try:
                            datos_extra = json.loads(datos_extra)
                        except Exception:
                            datos_extra = {}
                    log_entry.update(
                        {
                            'tipo': log.get('tipo', ''),
                            'categoria': log.get('categoria', ''),
                            'usuario': log.get('usuario', ''),
                            'maquina_id': log.get('maquina_id'),
                            'maquina_nombre': log.get('maquina_nombre', ''),
                            'entidad': log.get('entidad', ''),
                            'entidad_id': log.get('entidad_id'),
                            'monto': float(log['monto']) if log.get('monto') is not None else None,
                            'moneda': log.get('moneda', 'COP'),
                            'datos_extra': datos_extra,
                            'estado': log.get('estado', 'ok'),
                        }
                    )
                logs_data.append(log_entry)
            except Exception as e:
                logger.error(f"Error formateando log: {e}")

        return jsonify(
            {
                'logs': logs_data,
                'total': len(logs_data),
                'filtros': {
                    'limit': limit,
                    'nivel': nivel,
                    'buscar': buscar,
                    'fuente': fuente,
                    'orden': orden,
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'tail': tail,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo logs consola: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_estadisticas_logs():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        hoy = get_colombia_time().date()

        cursor.execute("SELECT COUNT(*) as total_logs_hoy FROM app_logs WHERE DATE(created_at) = %s", (hoy,))
        total_logs = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as errores_hoy FROM error_logs WHERE DATE(created_at) = %s", (hoy,))
        errores = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as accesos_hoy FROM access_logs WHERE DATE(created_at) = %s", (hoy,))
        accesos = cursor.fetchone()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT user_id) as usuarios_activos_hoy
            FROM access_logs
            WHERE DATE(created_at) = %s
              AND user_id IS NOT NULL
            """,
            (hoy,),
        )
        usuarios_activos = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                CONCAT(method, ' ', path) as endpoint,
                COUNT(*) as total,
                AVG(response_time_ms) as avg_time,
                COUNT(DISTINCT ip_address) as ips_unicas
            FROM access_logs
            WHERE DATE(created_at) = %s
            GROUP BY method, path
            ORDER BY total DESC
            LIMIT 5
            """,
            (hoy,),
        )
        top_endpoints = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                error_type,
                COUNT(*) as total,
                GROUP_CONCAT(DISTINCT module) as modulos
            FROM error_logs
            WHERE DATE(created_at) = %s
            GROUP BY error_type
            ORDER BY total DESC
            LIMIT 5
            """,
            (hoy,),
        )
        errores_por_tipo = cursor.fetchall()

        return jsonify(
            {
                'total_logs_hoy': total_logs['total_logs_hoy'] or 0,
                'errores_hoy': errores['errores_hoy'] or 0,
                'accesos_hoy': accesos['accesos_hoy'] or 0,
                'usuarios_activos_hoy': usuarios_activos['usuarios_activos_hoy'] or 0,
                'top_endpoints': top_endpoints,
                'errores_por_tipo': errores_por_tipo,
                'fecha': hoy.isoformat(),
                'timestamp': get_colombia_time().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/config', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_config_logs():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM log_config ORDER BY config_key")
        config = cursor.fetchall()

        cursor.execute("SELECT * FROM log_alerts WHERE is_active = TRUE ORDER BY severity")
        alertas = cursor.fetchall()

        return jsonify({'config': config, 'alertas': alertas, 'timestamp': get_colombia_time().isoformat()})
    except Exception as e:
        logger.error(f"Error obteniendo configuración: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/config', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['config_key', 'config_value'])
def actualizar_config_logs():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            INSERT INTO log_config (config_key, config_value, config_type, description, updated_by)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                config_value = VALUES(config_value),
                config_type = VALUES(config_type),
                description = VALUES(description),
                updated_by = VALUES(updated_by),
                updated_at = NOW()
            """,
            (
                data['config_key'],
                data['config_value'],
                data.get('config_type', 'string'),
                data.get('description', ''),
                session.get('user_id'),
            ),
        )
        connection.commit()

        log_app_event('INFO', f'Configuración actualizada: {data["config_key"]}', 'logs', data, session.get('user_id'))
        return api_response('S003', status='success')
    except Exception as e:
        logger.error(f"Error actualizando configuración: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/alertas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['alert_type', 'alert_message', 'condition'])
def crear_alerta_logs():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            INSERT INTO log_alerts
                (alert_type, alert_message, severity, condition, notification_method)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                data['alert_type'],
                data['alert_message'],
                data.get('severity', 'medium'),
                data['condition'],
                data.get('notification_method', 'console'),
            ),
        )
        alerta_id = cursor.lastrowid
        connection.commit()

        log_app_event('INFO', f'Alerta creada: {data["alert_type"]}', 'logs', data, session.get('user_id'))
        return api_response('S002', status='success', data={'alerta_id': alerta_id})
    except Exception as e:
        logger.error(f"Error creando alerta: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/alertas/<int:alerta_id>/toggle', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def toggle_alerta_logs(alerta_id):
    connection = None
    cursor = None
    try:
        data = request.get_json()
        activa = data.get('activa', True)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            UPDATE log_alerts
            SET is_active = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (activa, alerta_id),
        )
        connection.commit()

        estado = 'activada' if activa else 'desactivada'
        log_app_event(
            'INFO',
            f'Alerta {alerta_id} {estado}',
            'logs',
            {'alerta_id': alerta_id, 'estado': estado},
            session.get('user_id'),
        )
        return api_response('S003', status='success')
    except Exception as e:
        logger.error(f"Error toggle alerta: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/limpiar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def limpiar_logs_sistema():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        dias = int(data.get('dias', 30))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        fecha_limite = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

        tablas = ['app_logs', 'access_logs', 'error_logs', 'sessionlog']
        total_eliminados = 0
        for tabla in tablas:
            cursor.execute(
                f"""
                DELETE FROM {tabla}
                WHERE DATE(created_at) < %s
                """,
                (fecha_limite,),
            )
            total_eliminados += cursor.rowcount

        backup_suffix = datetime.now().strftime('%Y%m%d')
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS log_statistics_backup_{backup_suffix}
            SELECT * FROM log_statistics
            WHERE date < %s
            """,
            (fecha_limite,),
        )
        cursor.execute("DELETE FROM log_statistics WHERE date < %s", (fecha_limite,))
        connection.commit()

        log_app_event(
            'INFO',
            f'Logs limpiados: {total_eliminados} registros eliminados (>{dias} días)',
            'logs',
            {'dias': dias, 'eliminados': total_eliminados},
            session.get('user_id'),
        )
        return api_response(
            'S001',
            status='success',
            data={'eliminados': total_eliminados, 'dias': dias, 'fecha_limite': fecha_limite},
        )
    except Exception as e:
        logger.error(f"Error limpiando logs: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/exportar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def exportar_logs_sistema():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin') or datetime.now().strftime('%Y-%m-%d')
        formatos = data.get('formatos', ['json', 'csv'])

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            INSERT INTO log_exports
                (export_name, start_date, end_date, filters, exported_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                fecha_inicio,
                fecha_fin,
                json.dumps(data),
                session.get('user_id'),
            ),
        )
        export_id = cursor.lastrowid

        cursor.execute(
            "SELECT * FROM app_logs WHERE DATE(created_at) BETWEEN %s AND %s ORDER BY created_at",
            (fecha_inicio, fecha_fin),
        )
        app_logs = cursor.fetchall()

        cursor.execute(
            "SELECT * FROM access_logs WHERE DATE(created_at) BETWEEN %s AND %s ORDER BY created_at",
            (fecha_inicio, fecha_fin),
        )
        access_logs = cursor.fetchall()

        cursor.execute(
            "SELECT * FROM error_logs WHERE DATE(created_at) BETWEEN %s AND %s ORDER BY created_at",
            (fecha_inicio, fecha_fin),
        )
        error_logs = cursor.fetchall()

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_path = temp_file.name
        temp_file.close()

        with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if 'json' in formatos:
                export_json = {
                    'app_logs': app_logs,
                    'access_logs': access_logs,
                    'error_logs': error_logs,
                    'metadata': {
                        'fecha_inicio': fecha_inicio,
                        'fecha_fin': fecha_fin,
                        'exportado_el': datetime.now().isoformat(),
                        'exportado_por': session.get('user_name'),
                    },
                }
                zipf.writestr('logs.json', json.dumps(export_json, default=str, indent=2))

            if 'csv' in formatos:
                if app_logs:
                    csv_str = io.StringIO()
                    csv_writer = csv.DictWriter(csv_str, fieldnames=app_logs[0].keys())
                    csv_writer.writeheader()
                    csv_writer.writerows(app_logs)
                    zipf.writestr('app_logs.csv', csv_str.getvalue())

                if access_logs:
                    csv_str = io.StringIO()
                    csv_writer = csv.DictWriter(csv_str, fieldnames=access_logs[0].keys())
                    csv_writer.writeheader()
                    csv_writer.writerows(access_logs)
                    zipf.writestr('access_logs.csv', csv_str.getvalue())

                if error_logs:
                    csv_str = io.StringIO()
                    csv_writer = csv.DictWriter(csv_str, fieldnames=error_logs[0].keys())
                    csv_writer.writeheader()
                    csv_writer.writerows(error_logs)
                    zipf.writestr('error_logs.csv', csv_str.getvalue())

        file_size = os.path.getsize(temp_path)
        connection.commit()
        cursor.close()
        connection.close()
        cursor = None
        connection = None

        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            cursor.execute(
                """
                UPDATE log_exports
                SET file_path = %s,
                    file_size = %s,
                    status = 'completed',
                    completed_at = NOW()
                WHERE id = %s
                """,
                (temp_path, file_size, export_id),
            )
            connection.commit()

        log_app_event(
            'INFO',
            f'Exportación de logs completada: {export_id}',
            'logs',
            {'export_id': export_id, 'tamano': file_size},
            session.get('user_id'),
        )
        return send_file(
            temp_path,
            as_attachment=True,
            download_name=f'logs_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip',
            mimetype='application/zip',
        )
    except Exception as e:
        logger.error(f"Error exportando logs: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/errores/<int:error_id>/resolver', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def resolver_error_log(error_id):
    connection = None
    cursor = None
    try:
        data = request.get_json()
        comentarios = data.get('comentarios', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            UPDATE error_logs
            SET resolved = TRUE,
                resolved_at = NOW(),
                resolved_by = %s
            WHERE id = %s
            """,
            (session.get('user_id'), error_id),
        )
        connection.commit()

        log_app_event(
            'INFO',
            f'Error {error_id} marcado como resuelto',
            'logs',
            {'error_id': error_id, 'comentarios': comentarios},
            session.get('user_id'),
        )
        return api_response('S003', status='success')
    except Exception as e:
        logger.error(f"Error resolviendo error: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/api/logs/dashboard', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_dashboard_logs():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        hoy = datetime.now().date()

        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM app_logs WHERE DATE(created_at) = %s) as total_logs_hoy,
                (SELECT COUNT(*) FROM app_logs WHERE DATE(created_at) = %s AND level = 'ERROR') as errores_hoy,
                (SELECT COUNT(*) FROM access_logs WHERE DATE(created_at) = %s) as accesos_hoy,
                (SELECT COUNT(*) FROM error_logs WHERE DATE(created_at) = %s AND resolved = FALSE) as errores_pendientes
            """,
            (hoy, hoy, hoy, hoy),
        )
        resumen = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                date,
                total_logs,
                error_logs,
                access_logs
            FROM log_statistics
            WHERE date >= DATE_SUB(%s, INTERVAL 7 DAY)
            ORDER BY date
            """,
            (hoy,),
        )
        evolucion = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                error_type,
                COUNT(*) as total,
                MIN(created_at) as primer_error,
                MAX(created_at) as ultimo_error
            FROM error_logs
            WHERE resolved = FALSE
            GROUP BY error_type
            ORDER BY total DESC
            LIMIT 5
            """
        )
        top_errores = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                HOUR(created_at) as hora,
                COUNT(*) as total
            FROM access_logs
            WHERE DATE(created_at) = %s
            GROUP BY HOUR(created_at)
            ORDER BY hora
            """,
            (hoy,),
        )
        actividad_hora = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY response_time_ms) as p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY response_time_ms) as p95,
                AVG(response_time_ms) as promedio,
                MAX(response_time_ms) as maximo
            FROM access_logs
            WHERE DATE(created_at) = %s
            """,
            (hoy,),
        )
        rendimiento = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                alert_type,
                alert_message,
                severity,
                last_triggered
            FROM log_alerts
            WHERE last_triggered >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            ORDER BY last_triggered DESC
            LIMIT 5
            """
        )
        alertas_recientes = cursor.fetchall()

        return jsonify(
            {
                'resumen': resumen,
                'evolucion': evolucion,
                'top_errores': top_errores,
                'actividad_hora': actividad_hora,
                'rendimiento': rendimiento,
                'alertas_recientes': alertas_recientes,
                'timestamp': get_colombia_time().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo dashboard: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@logs_bp.route('/admin/logs/backup-manual', methods=['POST'])
@require_login(['admin'])
def backup_logs_manual():
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_path = temp_file.name
        temp_file.close()

        with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            log_file = 'logs/maquinas.log'
            if os.path.exists(log_file):
                zipf.write(log_file, 'maquinas.log')

            for i in range(1, 11):
                rotated_file = f'logs/maquinas.log.{i}'
                if os.path.exists(rotated_file):
                    zipf.write(rotated_file, f'maquinas.log.{i}')

        filename = f'logs_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
        return send_file(temp_path, as_attachment=True, download_name=filename, mimetype='application/zip')
    except Exception as e:
        logger.error(f"Error en backup manual: {e}")
        return api_response('E001', http_status=500)
