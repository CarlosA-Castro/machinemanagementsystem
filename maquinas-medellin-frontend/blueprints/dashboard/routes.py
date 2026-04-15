import logging
from datetime import datetime, timedelta

import sentry_sdk
from flask import Blueprint, request, jsonify, session, json

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from middleware.logging_mw import log_transaccion
from utils.auth import require_login, get_user_permissions
from utils.messages import MessageService
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time, format_datetime_for_db, parse_db_datetime
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

dashboard_bp = Blueprint('dashboard', __name__)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@dashboard_bp.route('/api/dashboard/estadisticas', methods=['GET'])
@handle_api_errors
def obtener_estadisticas_dashboard():
    """Obtener estadísticas principales para el dashboard"""
    if not session.get('logged_in'):
        return api_response('E003', http_status=401)
    permisos = get_user_permissions()
    if 'ver_dashboard' not in permisos and 'admin_panel' not in permisos:
        return api_response('E004', http_status=403)

    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin    = request.args.get('fecha_fin',    get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                COALESCE(SUM(tp.price), 0) as ingresos_totales,
                COUNT(DISTINCT qh.qr_code) as paquetes_vendidos
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
        """, (fecha_inicio, fecha_fin))
        ingresos = cursor.fetchone()

        cursor.execute("""
            SELECT
                COUNT(CASE WHEN status = 'activa' THEN 1 END) as maquinas_activas,
                COUNT(*) as maquinas_totales
            FROM machine
        """)
        maquinas = cursor.fetchone()

        cursor.execute("""
            SELECT
                CASE
                    WHEN COUNT(DISTINCT qh.qr_code) > 0
                    THEN COALESCE(SUM(tp.price), 0) / COUNT(DISTINCT qh.qr_code)
                    ELSE 0
                END as ticket_promedio
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
        """, (fecha_inicio, fecha_fin))
        ticket = cursor.fetchone()

        fecha_inicio_anterior = (datetime.strptime(fecha_inicio, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
        fecha_fin_anterior    = (datetime.strptime(fecha_fin,    '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')

        cursor.execute("""
            SELECT
                COALESCE(SUM(tp.price), 0) as ingresos_anterior,
                COUNT(DISTINCT qh.qr_code) as paquetes_anterior
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
        """, (fecha_inicio_anterior, fecha_fin_anterior))
        anterior = cursor.fetchone()

        ingresos_actual = float(ingresos['ingresos_totales'] or 0)
        ingresos_previo = float(anterior['ingresos_anterior'] or 0)
        paquetes_actual = ingresos['paquetes_vendidos'] or 0
        paquetes_previo = anterior['paquetes_anterior'] or 0

        tendencia_ingresos = ((ingresos_actual - ingresos_previo) / ingresos_previo * 100) if ingresos_previo > 0 else 0
        tendencia_paquetes = ((paquetes_actual - paquetes_previo) / paquetes_previo * 100) if paquetes_previo > 0 else 0

        logger.info(f"Dashboard stats: {ingresos_actual} ingresos, {paquetes_actual} paquetes")

        return jsonify({
            'ingresos_totales':  ingresos_actual,
            'paquetes_vendidos': paquetes_actual,
            'maquinas_activas':  maquinas['maquinas_activas'] or 0,
            'maquinas_totales':  maquinas['maquinas_totales'] or 0,
            'ticket_promedio':   float(ticket['ticket_promedio'] or 0),
            'tendencias': {
                'ingresos':  round(tendencia_ingresos, 1),
                'paquetes':  round(tendencia_paquetes, 1),
            },
            'rango_fechas': {'inicio': fecha_inicio, 'fin': fecha_fin},
            'timestamp': get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/dashboard/graficas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_graficas_dashboard():
    """Obtener datos para gráficas del dashboard"""
    connection = None
    cursor = None
    try:
        fecha_inicio    = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin       = request.args.get('fecha_fin',    get_colombia_time().strftime('%Y-%m-%d'))
        tipo_agrupacion = request.args.get('tipo', 'diario')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        if tipo_agrupacion == 'mensual':
            cursor.execute("""
                SELECT DATE_FORMAT(qh.fecha_hora, '%Y-%m') as fecha,
                       COUNT(DISTINCT qh.qr_code) as ventas,
                       COALESCE(SUM(tp.price), 0) as ingresos
                FROM qrhistory qh
                LEFT JOIN qrcode qr ON qr.code = qh.qr_code
                LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY DATE_FORMAT(qh.fecha_hora, '%Y-%m')
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))
        elif tipo_agrupacion == 'semanal':
            cursor.execute("""
                SELECT DATE_FORMAT(qh.fecha_hora, '%Y-S%u') as fecha,
                       COUNT(DISTINCT qh.qr_code) as ventas,
                       COALESCE(SUM(tp.price), 0) as ingresos
                FROM qrhistory qh
                LEFT JOIN qrcode qr ON qr.code = qh.qr_code
                LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY DATE_FORMAT(qh.fecha_hora, '%Y-%u')
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))
        else:
            cursor.execute("""
                SELECT DATE(qh.fecha_hora) as fecha,
                       COUNT(DISTINCT qh.qr_code) as ventas,
                       COALESCE(SUM(tp.price), 0) as ingresos
                FROM qrhistory qh
                LEFT JOIN qrcode qr ON qr.code = qh.qr_code
                LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY fecha
            """, (fecha_inicio, fecha_fin))

        evolucion_data  = cursor.fetchall()
        evolucion_ventas = {
            'labels': [str(i['fecha']) for i in evolucion_data],
            'data':   [float(i['ingresos']) for i in evolucion_data],
        }

        cursor.execute("""
            SELECT tp.name as paquete,
                   COUNT(DISTINCT qh.qr_code) as cantidad,
                   SUM(tp.price) as ingresos
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            GROUP BY tp.id, tp.name
            ORDER BY ingresos DESC LIMIT 10
        """, (fecha_inicio, fecha_fin))
        paquetes_data = cursor.fetchall()
        ventas_paquetes = {
            'labels': [i['paquete'] for i in paquetes_data],
            'data':   [i['cantidad'] for i in paquetes_data],
        }

        cursor.execute("""
            SELECT m.name as maquina,
                   COUNT(tu.id) as usos,
                   COALESCE(SUM(tp.price), 0) as ingresos
            FROM machine m
            LEFT JOIN turnusage tu ON tu.machineId = m.id
                AND DATE(tu.usedAt) BETWEEN %s AND %s
            LEFT JOIN qrcode qr ON tu.qrCodeId = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            GROUP BY m.id, m.name
            ORDER BY ingresos DESC, usos DESC LIMIT 10
        """, (fecha_inicio, fecha_fin))
        maquinas_data = cursor.fetchall()
        rendimiento_maquinas = {
            'labels': [i['maquina'] for i in maquinas_data],
            'data':   [float(i['ingresos']) for i in maquinas_data],
        }

        cursor.execute("""
            SELECT COUNT(CASE WHEN status='activa' THEN 1 END) as activas,
                   COUNT(CASE WHEN status='mantenimiento' THEN 1 END) as mantenimiento,
                   COUNT(CASE WHEN status='inactiva' THEN 1 END) as inactivas
            FROM machine
        """)
        estado_data   = cursor.fetchone()
        estado_maquinas = [
            estado_data['activas'] or 0,
            estado_data['mantenimiento'] or 0,
            estado_data['inactivas'] or 0,
        ]

        return jsonify({
            'evolucion_ventas':     evolucion_ventas,
            'ventas_paquetes':      ventas_paquetes,
            'rendimiento_maquinas': rendimiento_maquinas,
            'estado_maquinas':      estado_maquinas,
            'rango_fechas': {'inicio': fecha_inicio, 'fin': fecha_fin},
            'timestamp': get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo gráficas dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/maquinas/<int:maquina_id>/resolver-falla', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def resolver_falla_maquina(maquina_id):
    """Resolver falla de máquina: reactivar, limpiar reportes y enviar RESUME al ESP32"""
    connection = None
    cursor = None
    try:
        data          = request.get_json() or {}
        estacion_index = data.get('estacion_index', None)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id, name, status FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('E005', http_status=404, data={'message': 'Máquina no encontrada'})

        cursor.execute("""
            UPDATE machine SET errorNote = NULL, status = 'activa' WHERE id = %s
        """, (maquina_id,))

        if estacion_index is not None:
            cursor.execute("""
                UPDATE machinefailures
                SET resolved = 1, resolved_at = NOW()
                WHERE machine_id = %s AND station_index = %s AND resolved = 0
            """, (maquina_id, estacion_index))
        else:
            cursor.execute("""
                UPDATE machinefailures
                SET resolved = 1, resolved_at = NOW()
                WHERE machine_id = %s AND resolved = 0
            """, (maquina_id,))

        cursor.execute("""
            UPDATE errorreport
            SET isResolved = 1, resolved_at = NOW()
            WHERE machineId = %s AND isResolved = 0
        """, (maquina_id,))

        try:
            cursor.execute("""
                INSERT INTO esp32_commands
                (machine_id, command, parameters, triggered_by, status, triggered_at)
                VALUES (%s, 'RESUME', %s, %s, 'queued', NOW())
            """, (maquina_id, json.dumps({
                'machine_name':  maquina['name'],
                'estacion_index': estacion_index,
                'resolved_by':   session.get('user_name', 'admin'),
            }), session.get('user_name', 'admin')))
        except Exception as cmd_err:
            logger.error(f"No se pudo encolar RESUME: {cmd_err}")

        connection.commit()

        estacion_str = f" estación {estacion_index}" if estacion_index is not None else ""
        logger.info(
            f"Falla resuelta — Máquina: {maquina['name']} ({maquina_id})"
            f"{estacion_str} | Admin: {session.get('user_name', '-')}"
        )

        log_transaccion(
            tipo='resolver_falla',
            categoria='operacional',
            descripcion=f"Falla resuelta en {maquina['name']}" + estacion_str,
            usuario=session.get('user_name'),
            usuario_id=session.get('user_id'),
            maquina_id=maquina_id,
            maquina_nombre=maquina['name'],
            entidad='machine',
            entidad_id=maquina_id,
            datos_extra={'estacion_index': estacion_index},
        )

        return jsonify({'success': True, 'message': f'Falla resuelta en {maquina["name"]}'})

    except Exception as e:
        logger.error(f"Error resolviendo falla: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/dashboard/top-maquinas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_top_maquinas():
    """Obtener top 5 máquinas por usos"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin    = request.args.get('fecha_fin',    get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT m.name as nombre, COUNT(tu.id) as usos
            FROM machine m
            INNER JOIN turnusage tu ON tu.machineId = m.id
            WHERE DATE(tu.usedAt) BETWEEN %s AND %s
            GROUP BY m.id, m.name
            ORDER BY usos DESC LIMIT 5
        """, (fecha_inicio, fecha_fin))

        return jsonify([
            {'nombre': m['nombre'], 'usos': m['usos'] or 0, 'ventas': m['usos'] or 0, 'ingresos': 0}
            for m in cursor.fetchall()
        ])

    except Exception as e:
        logger.error(f"Error obteniendo top máquinas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/dashboard/ventas-recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_ventas_recientes():
    """Obtener las últimas 50 ventas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT qh.qr_code, qh.user_name, qh.fecha_hora,
                   tp.name as paquete, tp.price as precio
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            ORDER BY qh.fecha_hora DESC LIMIT 50
        """)

        ventas_formateadas = []
        for v in cursor.fetchall():
            try:
                fc = parse_db_datetime(v['fecha_hora'])
                hora_f  = fc.strftime('%H:%M')
                fecha_f = fc.strftime('%Y-%m-%d')
            except Exception:
                hora_f  = str(v['fecha_hora'])
                fecha_f = str(v['fecha_hora'])

            ventas_formateadas.append({
                'qr_code': v['qr_code'],
                'usuario': v['user_name'],
                'paquete': v['paquete'] or 'Sin paquete',
                'precio':  float(v['precio'] or 0),
                'hora':    hora_f,
                'fecha':   fecha_f,
            })

        return jsonify(ventas_formateadas)

    except Exception as e:
        logger.error(f"Error obteniendo ventas recientes: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/dashboard/resumen', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_resumen_dashboard():
    """Obtener resumen para dashboard/panel de control"""
    connection = None
    cursor = None
    try:
        fecha_hoy  = get_colombia_time().strftime('%Y-%m-%d')
        fecha_ayer = (get_colombia_time() - timedelta(days=1)).strftime('%Y-%m-%d')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN qh.qr_code END) as vendidos_hoy,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN tp.price END), 0) as valor_hoy
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
        """, (fecha_hoy,))
        hoy = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as turnos_hoy FROM turnusage WHERE DATE(usedAt) = %s", (fecha_hoy,))
        turnos_hoy = cursor.fetchone()

        cursor.execute("""
            SELECT
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN qh.qr_code END) as vendidos_ayer,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN tp.price END), 0) as valor_ayer
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
        """, (fecha_ayer,))
        ayer = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as turnos_ayer FROM turnusage WHERE DATE(usedAt) = %s", (fecha_ayer,))
        turnos_ayer = cursor.fetchone()

        cursor.execute("""
            SELECT
                COUNT(CASE WHEN status='activa'       THEN 1 END) as maquinas_activas,
                COUNT(CASE WHEN status='mantenimiento' THEN 1 END) as maquinas_mantenimiento,
                COUNT(CASE WHEN status='inactiva'     THEN 1 END) as maquinas_inactivas,
                COUNT(*) as total_maquinas
            FROM machine
        """)
        maquinas = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as reportes_pendientes FROM errorreport WHERE isResolved = FALSE")
        reportes = cursor.fetchone()

        cursor.execute("""
            SELECT qh.qr_code, qh.user_name, qh.fecha_hora,
                   tp.name as paquete_nombre, tp.price as precio
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
            ORDER BY qh.fecha_hora DESC LIMIT 5
        """)
        ultimas_ventas = cursor.fetchall()

        for v in ultimas_ventas:
            if v['fecha_hora']:
                try:
                    fc = parse_db_datetime(v['fecha_hora'])
                    v['fecha_hora']    = fc.strftime('%H:%M')
                    v['fecha_completa'] = fc.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    v['fecha_completa'] = str(v['fecha_hora'])

        logger.info(f"Dashboard: {hoy['vendidos_hoy'] or 0} vendidos hoy")

        return jsonify({
            'hoy':  {'fecha': fecha_hoy,  'vendidos': hoy['vendidos_hoy'] or 0,   'valor': float(hoy['valor_hoy'] or 0),   'turnos': turnos_hoy['turnos_hoy'] or 0},
            'ayer': {'fecha': fecha_ayer, 'vendidos': ayer['vendidos_ayer'] or 0, 'valor': float(ayer['valor_ayer'] or 0), 'turnos': turnos_ayer['turnos_ayer'] or 0},
            'maquinas': {
                'activas':      maquinas['maquinas_activas'] or 0,
                'mantenimiento': maquinas['maquinas_mantenimiento'] or 0,
                'inactivas':    maquinas['maquinas_inactivas'] or 0,
                'total':        maquinas['total_maquinas'] or 0,
            },
            'reportes':     {'pendientes': reportes['reportes_pendientes'] or 0},
            'ultimas_ventas': ultimas_ventas,
            'timestamp': get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo resumen dashboard: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/mis-permisos', methods=['GET'])
@handle_api_errors
def obtener_mis_permisos():
    """Obtener permisos del usuario actual"""
    if not session.get('logged_in'):
        return api_response('E003', http_status=401)
    permisos = get_user_permissions()
    return jsonify({
        'role':     session.get('user_role'),
        'permisos': permisos,
        'es_admin': 'admin_panel' in permisos,
    })


# ── Estadísticas históricas ───────────────────────────────────────────────────

@dashboard_bp.route('/api/estadisticas/rango-fechas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_rango_fechas():
    """Obtener estadísticas por rango de fechas"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin    = request.args.get('fecha_fin',    get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                DATE(qh.fecha_hora) as fecha,
                COUNT(DISTINCT qh.qr_code) as total_escaneados,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN qh.qr_code END) as vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN tp.price END), 0) as valor_ventas,
                COUNT(tu.id) as turnos_utilizados
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON DATE(tu.usedAt) = DATE(qh.fecha_hora)
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            GROUP BY DATE(qh.fecha_hora)
            ORDER BY fecha DESC
        """, (fecha_inicio, fecha_fin))
        estadisticas = cursor.fetchall()

        cursor.execute("""
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_escaneados,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN qh.qr_code END) as total_vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          THEN tp.price END), 0) as total_valor_ventas,
                COUNT(DISTINCT tu.id) as total_turnos_utilizados
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON DATE(tu.usedAt) = DATE(qh.fecha_hora)
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
        """, (fecha_inicio, fecha_fin))
        totales = cursor.fetchone()

        cursor.execute("""
            SELECT m.name as maquina_nombre, COUNT(tu.id) as turnos_utilizados
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE DATE(tu.usedAt) BETWEEN %s AND %s
            GROUP BY m.id, m.name
            ORDER BY turnos_utilizados DESC LIMIT 10
        """, (fecha_inicio, fecha_fin))
        maquinas_populares = cursor.fetchall()

        cursor.execute("""
            SELECT tp.name as paquete_nombre,
                   COUNT(DISTINCT qh.qr_code) as veces_vendido,
                   SUM(tp.price) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
            GROUP BY tp.id, tp.name
            ORDER BY veces_vendido DESC LIMIT 10
        """, (fecha_inicio, fecha_fin))
        paquetes_populares = cursor.fetchall()

        logger.info(f"Estadísticas rango {fecha_inicio} a {fecha_fin}: {totales['total_vendidos'] or 0} vendidos")

        return jsonify({
            'rango': {'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin},
            'estadisticas_por_dia': estadisticas,
            'totales': {
                'total_escaneados':      totales['total_escaneados'] or 0,
                'total_vendidos':        totales['total_vendidos'] or 0,
                'total_valor_ventas':    float(totales['total_valor_ventas'] or 0),
                'total_turnos_utilizados': totales['total_turnos_utilizados'] or 0,
            },
            'maquinas_populares': maquinas_populares,
            'paquetes_populares': paquetes_populares,
            'timestamp': get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas por rango: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


# ── Usuarios estado + estadísticas ────────────────────────────────────────────

@dashboard_bp.route('/api/usuarios/<int:usuario_id>/estado', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def cambiar_estado_usuario(usuario_id):
    """Cambiar estado activo/inactivo de un usuario"""
    connection = None
    cursor = None
    try:
        data      = request.get_json()
        is_active = data['isActive']

        if usuario_id == session.get('user_id'):
            return api_response('U005', http_status=400, data={'message': 'No puedes cambiar tu propio estado'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM users WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()
        if not usuario:
            return api_response('U001', http_status=404, data={'usuario_id': usuario_id})

        cursor.execute("""
            UPDATE users SET isActive = %s, updatedAt = NOW() WHERE id = %s
        """, (1 if is_active else 0, usuario_id))

        logger.info(f"Filas afectadas: {cursor.rowcount}, isActive: {1 if is_active else 0}, usuario_id: {usuario_id}")

        connection.commit()

        logger.info(f"Estado de usuario cambiado: {usuario['name']} (ID: {usuario_id}, Activo: {is_active})")

        return api_response('S003', status='success', data={
            'isActive': is_active,
            'message': f'Usuario {"activado" if is_active else "desactivado"} correctamente',
        })

    except Exception as e:
        logger.error(f"Error cambiando estado de usuario: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@dashboard_bp.route('/api/usuarios/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_estadisticas_usuarios():
    """Obtener estadísticas de usuarios"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN isActive = TRUE OR isActive IS NULL THEN 1 END) as activos,
                COUNT(CASE WHEN isActive = FALSE THEN 1 END) as inactivos,
                COUNT(CASE WHEN role = 'admin'            THEN 1 END) as admins,
                COUNT(CASE WHEN role = 'cajero'           THEN 1 END) as cajeros,
                COUNT(CASE WHEN role = 'admin_restaurante' THEN 1 END) as admin_restaurante,
                COUNT(CASE WHEN role = 'socio'            THEN 1 END) as socios
            FROM users
        """)
        estadisticas = cursor.fetchone()

        return jsonify({
            'total':             estadisticas['total'] or 0,
            'activos':           estadisticas['activos'] or 0,
            'inactivos':         estadisticas['inactivos'] or 0,
            'admins':            estadisticas['admins'] or 0,
            'cajeros':           estadisticas['cajeros'] or 0,
            'admin_restaurante': estadisticas['admin_restaurante'] or 0,
            'socios':            estadisticas['socios'] or 0,
            'timestamp':         get_colombia_time().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas de usuarios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


# ── Debug ─────────────────────────────────────────────────────────────────────

@dashboard_bp.route('/debug/session')
def debug_session():
    return jsonify(dict(session))


@dashboard_bp.route('/check-session')
def check_session():
    return jsonify({
        'session_working': True,
        'logged_in': session.get('logged_in', False),
        'user_name': session.get('user_name', 'No user'),
    })


@dashboard_bp.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'message': 'Server is running'})


@dashboard_bp.route('/test-sentry-activo')
def test_sentry_activo():
    try:
        resultado = 10 / 0
        return "Esto no debería mostrarse"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"✅ Error capturado y enviado a Sentry: {str(e)}"


@dashboard_bp.route('/api/debug/mensaje/<message_code>', methods=['GET'])
@handle_api_errors
def debug_mensaje(message_code):
    """Endpoint para probar mensajes"""
    language = request.args.get('language', 'es')
    formato  = request.args.get('formato', 'json')
    if formato == 'texto':
        return MessageService.get_error_message(message_code, language_code=language)
    return api_response(message_code, language_code=language)
