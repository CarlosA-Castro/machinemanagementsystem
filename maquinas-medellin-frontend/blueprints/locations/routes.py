import logging
import traceback
from datetime import datetime, timedelta

import sentry_sdk
from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from blueprints.esp32.state import get_heartbeat_fields
from utils.auth import require_login
from utils.helpers import parse_json_col
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

locations_bp = Blueprint('locations', __name__)

ADMIN_PERCENTAGE_DEFAULT = 25.0
RESTAURANT_PERCENTAGE_DEFAULT = 35.0


def _to_float(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _parse_date(value, fallback):
    if not value:
        return fallback
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), '%Y-%m-%d').date()


def _get_dashboard_period():
    today = get_colombia_time().date()
    default_start = today - timedelta(days=29)
    fecha_inicio = _parse_date(
        request.args.get('fechaInicio') or request.args.get('fecha_inicio'),
        default_start,
    )
    fecha_fin = _parse_date(
        request.args.get('fechaFin') or request.args.get('fecha_fin'),
        today,
    )
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
    return fecha_inicio, fecha_fin


def _table_exists(cursor, table_name):
    cursor.execute('SHOW TABLES LIKE %s', (table_name,))
    return cursor.fetchone() is not None


def _has_admin_col(cursor):
    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'maquinaporcentajerestaurante'
              AND COLUMN_NAME = 'porcentaje_admin'
            """
        )
        row = cursor.fetchone()
        return bool(row and row['cnt'])
    except Exception:
        return False


def _admin_expr(tiene_admin_col):
    if tiene_admin_col:
        return 'COALESCE(mpr.porcentaje_admin, 25.00)'
    return '25.00'


@locations_bp.route('/api/locales', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_locales():
    """Obtener todos los locales con estadísticas"""
    connection = None
    cursor = None
    try:
        logger.info("=== OBTENIENDO LOCALES ===")

        connection = get_db_connection()
        if not connection:
            logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                l.id, l.name, l.address, l.city, l.status,
                l.telefono, l.horario, l.notas
            FROM location l
            ORDER BY l.name
        """)

        locales = cursor.fetchall()
        logger.info(f"Locales encontrados: {len(locales)}")

        if not locales:
            return jsonify([])

        locales_con_estadisticas = []
        for local in locales:
            cursor.execute("""
                SELECT
                    COUNT(m.id) as maquinas_count,
                    SUM(CASE WHEN m.status = 'activa' THEN 1 ELSE 0 END) as maquinas_activas
                FROM machine m
                WHERE m.location_id = %s
            """, (local['id'],))

            stats = cursor.fetchone()

            locales_con_estadisticas.append({
                'id': local['id'],
                'name': local['name'],
                'address': local.get('address', ''),
                'city': local.get('city', ''),
                'status': local.get('status', 'activo'),
                'telefono': local.get('telefono', ''),
                'horario': local.get('horario', ''),
                'notas': local.get('notas', ''),
                'maquinas_count': stats['maquinas_count'] if stats else 0,
                'maquinas_activas': stats['maquinas_activas'] if stats else 0,
            })

        logger.info("Locales procesados exitosamente")
        return jsonify(locales_con_estadisticas)

    except Exception as e:
        logger.error(f"Error obteniendo locales: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales/dashboard-fase4', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_dashboard_locales_fase4():
    """Consolida KPIs operativos, técnicos y de rentabilidad para gestión multi-local."""
    connection = None
    cursor = None
    try:
        fecha_inicio, fecha_fin = _get_dashboard_period()

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute(
            """
            SELECT
                l.id,
                l.name,
                l.address,
                l.city,
                l.status,
                l.telefono,
                l.horario,
                l.notas,
                COUNT(m.id) AS maquinas_count,
                SUM(CASE WHEN m.status = 'activa' THEN 1 ELSE 0 END) AS maquinas_activas,
                SUM(CASE WHEN m.status = 'mantenimiento' THEN 1 ELSE 0 END) AS maquinas_mantenimiento,
                SUM(CASE WHEN m.status = 'inactiva' THEN 1 ELSE 0 END) AS maquinas_inactivas
            FROM location l
            LEFT JOIN machine m ON m.location_id = l.id
            GROUP BY l.id, l.name, l.address, l.city, l.status, l.telefono, l.horario, l.notas
            ORDER BY l.name
            """
        )
        locales_rows = cursor.fetchall()

        dashboard_locales = []
        locales_index = {}
        for row in locales_rows:
            item = {
                'id': row['id'],
                'name': row['name'],
                'address': row.get('address', ''),
                'city': row.get('city', ''),
                'status': row.get('status', 'activo'),
                'telefono': row.get('telefono', ''),
                'horario': row.get('horario', ''),
                'notas': row.get('notas', ''),
                'maquinas_count': int(row.get('maquinas_count') or 0),
                'maquinas_activas': int(row.get('maquinas_activas') or 0),
                'maquinas_mantenimiento': int(row.get('maquinas_mantenimiento') or 0),
                'maquinas_inactivas': int(row.get('maquinas_inactivas') or 0),
                'esp32_online': 0,
                'esp32_offline': 0,
                'alertas_criticas': 0,
                'alertas_advertencia': 0,
                'estaciones_mantenimiento': 0,
                'fallas_periodo': 0,
                'turnos_devueltos_periodo': 0,
                'ingresos_periodo': 0.0,
                'utilidad_periodo': 0.0,
                'costos_periodo': 0.0,
                'margen_utilidad': 0.0,
                'top_machine_name': None,
                'top_machine_profit': 0.0,
                'rentabilidad_source': 'sin_datos',
                'health_status': 'stable',
            }
            dashboard_locales.append(item)
            locales_index[item['id']] = item

        try:
            cursor.execute(
                """
                SELECT
                    m.id,
                    m.name,
                    m.status,
                    m.location_id,
                    l.name AS location_name,
                    m.stations_in_maintenance,
                    mt.machine_subtype
                FROM machine m
                LEFT JOIN location l ON m.location_id = l.id
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                ORDER BY m.name
                """
            )
        except Exception:
            cursor.execute(
                """
                SELECT
                    m.id,
                    m.name,
                    m.status,
                    m.location_id,
                    l.name AS location_name,
                    NULL AS stations_in_maintenance,
                    mt.machine_subtype
                FROM machine m
                LEFT JOIN location l ON m.location_id = l.id
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                ORDER BY m.name
                """
            )

        machines_rows = cursor.fetchall()
        machine_index = {}
        technical_alerts = []

        for machine in machines_rows:
            location_id = machine.get('location_id')
            if location_id not in locales_index:
                continue

            local = locales_index[location_id]
            heartbeat = get_heartbeat_fields(machine['id'])
            stations_in_maintenance = parse_json_col(machine.get('stations_in_maintenance'), [])
            stations_count = len(stations_in_maintenance)
            is_online = bool(heartbeat.get('esp32_online'))

            machine_data = {
                'id': machine['id'],
                'name': machine['name'],
                'status': machine['status'],
                'location_id': location_id,
                'location_name': machine.get('location_name') or local['name'],
                'esp32_online': is_online,
                'esp32_wifi': bool(heartbeat.get('esp32_wifi')),
                'esp32_server': bool(heartbeat.get('esp32_server')),
                'esp32_rssi': int(heartbeat.get('esp32_rssi') or 0),
                'stations_in_maintenance': stations_in_maintenance,
                'stations_in_maintenance_count': stations_count,
            }
            machine_index[machine['id']] = machine_data

            if is_online:
                local['esp32_online'] += 1
            else:
                local['esp32_offline'] += 1
                local['alertas_criticas'] += 1
                technical_alerts.append({
                    'severity': 'critical',
                    'kind': 'heartbeat_offline',
                    'location_id': location_id,
                    'location_name': local['name'],
                    'machine_id': machine['id'],
                    'machine_name': machine['name'],
                    'title': 'Máquina sin heartbeat',
                    'description': f"{machine['name']} dejó de reportar al panel técnico.",
                    'metric': 'ESP32 offline',
                })

            if stations_count:
                local['estaciones_mantenimiento'] += stations_count
                local['alertas_advertencia'] += 1
                technical_alerts.append({
                    'severity': 'warning',
                    'kind': 'station_maintenance',
                    'location_id': location_id,
                    'location_name': local['name'],
                    'machine_id': machine['id'],
                    'machine_name': machine['name'],
                    'title': 'Estaciones en mantenimiento',
                    'description': (
                        f"{machine['name']} tiene {stations_count} estación(es) fuera de servicio."
                    ),
                    'metric': f'{stations_count} estación(es)',
                })

            if machine.get('status') == 'mantenimiento' and not stations_count:
                local['alertas_advertencia'] += 1
                technical_alerts.append({
                    'severity': 'warning',
                    'kind': 'machine_maintenance',
                    'location_id': location_id,
                    'location_name': local['name'],
                    'machine_id': machine['id'],
                    'machine_name': machine['name'],
                    'title': 'Máquina en mantenimiento',
                    'description': f"{machine['name']} está marcada en mantenimiento general.",
                    'metric': 'Mantenimiento',
                })

        cursor.execute(
            """
            SELECT
                mf.machine_id,
                m.location_id,
                COUNT(*) AS fallas_periodo,
                COALESCE(SUM(mf.turnos_devueltos), 0) AS turnos_devueltos_periodo,
                MAX(mf.reported_at) AS ultima_falla
            FROM machinefailures mf
            JOIN machine m ON mf.machine_id = m.id
            WHERE DATE(mf.reported_at) BETWEEN %s AND %s
            GROUP BY mf.machine_id, m.location_id
            ORDER BY fallas_periodo DESC, ultima_falla DESC
            """,
            (fecha_inicio, fecha_fin),
        )
        failure_rows = cursor.fetchall()
        failure_alerts = []

        for row in failure_rows:
            location_id = row.get('location_id')
            if location_id not in locales_index:
                continue

            local = locales_index[location_id]
            machine = machine_index.get(row['machine_id'], {})
            fallas_periodo = int(row.get('fallas_periodo') or 0)
            turnos_devueltos = int(row.get('turnos_devueltos_periodo') or 0)

            local['fallas_periodo'] += fallas_periodo
            local['turnos_devueltos_periodo'] += turnos_devueltos

            if fallas_periodo > 0:
                severity = 'critical' if fallas_periodo >= 3 else 'warning'
                if severity == 'critical':
                    local['alertas_criticas'] += 1
                else:
                    local['alertas_advertencia'] += 1

                failure_alerts.append({
                    'severity': severity,
                    'kind': 'failure_rate',
                    'location_id': location_id,
                    'location_name': local['name'],
                    'machine_id': row['machine_id'],
                    'machine_name': machine.get('name') or f'Máquina {row["machine_id"]}',
                    'title': 'Fallas recurrentes en el período',
                    'description': (
                        f"{machine.get('name') or ('Máquina ' + str(row['machine_id']))} registró "
                        f"{fallas_periodo} falla(s) y {turnos_devueltos} turno(s) devuelto(s)."
                    ),
                    'metric': f'{fallas_periodo} falla(s)',
                })

        tiene_admin_col = _has_admin_col(cursor)
        admin_expr = _admin_expr(tiene_admin_col)

        cursor.execute(
            f"""
            SELECT
                m.id AS maquina_id,
                m.name AS maquina_nombre,
                m.location_id,
                COALESCE(loc.name, 'Sin local') AS location_name,
                COUNT(tu.id) AS turnos_usados,
                COALESCE(SUM(
                    CASE
                        WHEN tp.price IS NOT NULL AND COALESCE(tp.turns, 0) > 0
                        THEN tp.price / tp.turns
                        ELSE COALESCE(m.valor_por_turno, 3000.00)
                    END
                ), 0) AS ingresos_estimados,
                COALESCE(mpr.porcentaje_restaurante, {RESTAURANT_PERCENTAGE_DEFAULT}) AS pct_restaurante,
                {admin_expr} AS pct_admin
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            LEFT JOIN qrcode qr ON tu.qrCodeId = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN location loc ON m.location_id = loc.id
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
            WHERE DATE(tu.usedAt) BETWEEN %s AND %s
            GROUP BY
                m.id, m.name, m.location_id, loc.name,
                mpr.porcentaje_restaurante, {admin_expr}
            ORDER BY ingresos_estimados DESC
            """,
            (fecha_inicio, fecha_fin),
        )
        estimated_profit_rows = cursor.fetchall()
        profitability_by_machine = {}

        for row in estimated_profit_rows:
            ingresos = _to_float(row.get('ingresos_estimados'))
            pct_rest = _to_float(row.get('pct_restaurante'), RESTAURANT_PERCENTAGE_DEFAULT)
            pct_admin = _to_float(row.get('pct_admin'), ADMIN_PERCENTAGE_DEFAULT)
            utilidad = ingresos - (ingresos * pct_rest / 100) - (ingresos * pct_admin / 100)
            profitability_by_machine[row['maquina_id']] = {
                'machine_id': row['maquina_id'],
                'machine_name': row['maquina_nombre'],
                'location_id': row['location_id'],
                'location_name': row['location_name'],
                'turnos_usados': int(row.get('turnos_usados') or 0),
                'ingreso_bruto': round(ingresos, 2),
                'costos_operativos': 0.0,
                'utilidad_operativa': round(utilidad, 2),
                'margin_pct': round((utilidad / ingresos * 100), 2) if ingresos > 0 else 0.0,
                'source': 'estimada_por_uso',
            }

        if _table_exists(cursor, 'liquidaciones'):
            cursor.execute(
                f"""
                SELECT
                    liq.maquina_id,
                    m.name AS maquina_nombre,
                    m.location_id,
                    COALESCE(loc.name, 'Sin local') AS location_name,
                    SUM(COALESCE(liq.turnos_retirados, 0)) AS turnos_retirados,
                    SUM(COALESCE(liq.turnos_retirados, 0) * COALESCE(liq.valor_por_turno, 0)) AS ingreso_bruto,
                    SUM(COALESCE(liq.costos_operativos, 0)) AS costos_operativos,
                    SUM(
                        (COALESCE(liq.turnos_retirados, 0) * COALESCE(liq.valor_por_turno, 0))
                        - (
                            (COALESCE(liq.turnos_retirados, 0) * COALESCE(liq.valor_por_turno, 0))
                            * COALESCE(liq.porcentaje_restaurante, {RESTAURANT_PERCENTAGE_DEFAULT}) / 100
                        )
                        - (
                            (COALESCE(liq.turnos_retirados, 0) * COALESCE(liq.valor_por_turno, 0))
                            * {ADMIN_PERCENTAGE_DEFAULT} / 100
                        )
                        - COALESCE(liq.costos_operativos, 0)
                    ) AS utilidad_operativa,
                    COUNT(*) AS liquidaciones_registradas
                FROM liquidaciones liq
                JOIN machine m ON liq.maquina_id = m.id
                LEFT JOIN location loc ON m.location_id = loc.id
                WHERE liq.fecha BETWEEN %s AND %s
                GROUP BY liq.maquina_id, m.name, m.location_id, loc.name
                ORDER BY utilidad_operativa DESC
                """,
                (fecha_inicio, fecha_fin),
            )

            for row in cursor.fetchall():
                ingresos = _to_float(row.get('ingreso_bruto'))
                utilidad = _to_float(row.get('utilidad_operativa'))
                profitability_by_machine[row['maquina_id']] = {
                    'machine_id': row['maquina_id'],
                    'machine_name': row['maquina_nombre'],
                    'location_id': row['location_id'],
                    'location_name': row['location_name'],
                    'turnos_usados': int(row.get('turnos_retirados') or 0),
                    'ingreso_bruto': round(ingresos, 2),
                    'costos_operativos': round(_to_float(row.get('costos_operativos')), 2),
                    'utilidad_operativa': round(utilidad, 2),
                    'margin_pct': round((utilidad / ingresos * 100), 2) if ingresos > 0 else 0.0,
                    'source': 'manual',
                    'liquidaciones_registradas': int(row.get('liquidaciones_registradas') or 0),
                }

        profitability_locations = {}
        for machine in profitability_by_machine.values():
            location_id = machine.get('location_id')
            if location_id not in locales_index:
                continue

            local_profit = profitability_locations.setdefault(location_id, {
                'location_id': location_id,
                'location_name': machine['location_name'],
                'ingreso_bruto': 0.0,
                'costos_operativos': 0.0,
                'utilidad_operativa': 0.0,
                'machine_count': 0,
                'source_breakdown': {'manual': 0, 'estimada_por_uso': 0},
            })

            local_profit['ingreso_bruto'] += _to_float(machine.get('ingreso_bruto'))
            local_profit['costos_operativos'] += _to_float(machine.get('costos_operativos'))
            local_profit['utilidad_operativa'] += _to_float(machine.get('utilidad_operativa'))
            local_profit['machine_count'] += 1
            local_profit['source_breakdown'][machine.get('source', 'estimada_por_uso')] += 1

            local = locales_index[location_id]
            local['ingresos_periodo'] += _to_float(machine.get('ingreso_bruto'))
            local['utilidad_periodo'] += _to_float(machine.get('utilidad_operativa'))
            local['costos_periodo'] += _to_float(machine.get('costos_operativos'))

            if _to_float(machine.get('utilidad_operativa')) > _to_float(local.get('top_machine_profit')):
                local['top_machine_profit'] = round(_to_float(machine.get('utilidad_operativa')), 2)
                local['top_machine_name'] = machine.get('machine_name')

        for local in dashboard_locales:
            ingresos = _to_float(local['ingresos_periodo'])
            utilidad = _to_float(local['utilidad_periodo'])
            local['ingresos_periodo'] = round(ingresos, 2)
            local['utilidad_periodo'] = round(utilidad, 2)
            local['costos_periodo'] = round(_to_float(local['costos_periodo']), 2)
            local['margen_utilidad'] = round((utilidad / ingresos * 100), 2) if ingresos > 0 else 0.0
            local['rentabilidad_source'] = (
                'mixta'
                if local['top_machine_name'] and local['ingresos_periodo'] > 0
                else 'sin_datos'
            )

            if local['alertas_criticas'] > 0 or local['esp32_offline'] > 0:
                local['health_status'] = 'critical'
            elif local['alertas_advertencia'] > 0 or local['fallas_periodo'] > 0:
                local['health_status'] = 'warning'
            elif local['maquinas_count'] == 0:
                local['health_status'] = 'empty'
            else:
                local['health_status'] = 'healthy'

        profit_locations_list = []
        for local_profit in profitability_locations.values():
            ingreso_bruto = round(_to_float(local_profit['ingreso_bruto']), 2)
            utilidad = round(_to_float(local_profit['utilidad_operativa']), 2)
            costos = round(_to_float(local_profit['costos_operativos']), 2)
            manual_count = local_profit['source_breakdown'].get('manual', 0)
            estimated_count = local_profit['source_breakdown'].get('estimada_por_uso', 0)
            source = (
                'mixta' if manual_count and estimated_count
                else 'manual' if manual_count
                else 'estimada_por_uso'
            )
            profit_locations_list.append({
                'location_id': local_profit['location_id'],
                'location_name': local_profit['location_name'],
                'ingreso_bruto': ingreso_bruto,
                'costos_operativos': costos,
                'utilidad_operativa': utilidad,
                'machine_count': local_profit['machine_count'],
                'margin_pct': round((utilidad / ingreso_bruto * 100), 2) if ingreso_bruto > 0 else 0.0,
                'source': source,
            })

            if local_profit['location_id'] in locales_index:
                locales_index[local_profit['location_id']]['rentabilidad_source'] = source

        technical_alerts.extend(failure_alerts[:8])
        severity_order = {'critical': 0, 'warning': 1, 'info': 2}
        technical_alerts = sorted(
            technical_alerts,
            key=lambda item: (
                severity_order.get(item.get('severity'), 9),
                item.get('location_name', ''),
                item.get('machine_name', ''),
            ),
        )[:12]

        top_machines = sorted(
            profitability_by_machine.values(),
            key=lambda item: item.get('utilidad_operativa', 0),
            reverse=True,
        )[:8]
        underperforming_machines = sorted(
            profitability_by_machine.values(),
            key=lambda item: item.get('utilidad_operativa', 0),
        )[:5]
        top_locations = sorted(
            profit_locations_list,
            key=lambda item: item.get('utilidad_operativa', 0),
            reverse=True,
        )

        summary = {
            'total_locales': len(dashboard_locales),
            'locales_activos': sum(1 for item in dashboard_locales if item['status'] == 'activo'),
            'locales_en_riesgo': sum(
                1 for item in dashboard_locales if item['health_status'] in ('critical', 'warning')
            ),
            'maquinas_totales': sum(item['maquinas_count'] for item in dashboard_locales),
            'maquinas_activas': sum(item['maquinas_activas'] for item in dashboard_locales),
            'esp32_online': sum(item['esp32_online'] for item in dashboard_locales),
            'esp32_offline': sum(item['esp32_offline'] for item in dashboard_locales),
            'fallas_periodo': sum(item['fallas_periodo'] for item in dashboard_locales),
            'turnos_devueltos_periodo': sum(
                item['turnos_devueltos_periodo'] for item in dashboard_locales
            ),
            'estaciones_mantenimiento': sum(
                item['estaciones_mantenimiento'] for item in dashboard_locales
            ),
            'ingresos_periodo': round(
                sum(_to_float(item['ingresos_periodo']) for item in dashboard_locales), 2
            ),
            'utilidad_periodo': round(
                sum(_to_float(item['utilidad_periodo']) for item in dashboard_locales), 2
            ),
        }

        return jsonify({
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
                'label': f'{fecha_inicio.isoformat()} a {fecha_fin.isoformat()}',
            },
            'summary': summary,
            'locales': dashboard_locales,
            'technical_alerts': technical_alerts,
            'profitability': {
                'top_locations': top_locations[:6],
                'top_machines': top_machines,
                'underperforming_machines': underperforming_machines,
            },
        })

    except Exception as e:
        logger.error(f"Error obteniendo dashboard Fase 4 de locales: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@locations_bp.route('/api/locales/<int:local_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_local(local_id):
    """Obtener un local específico"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM location WHERE id = %s", (local_id,))
        local = cursor.fetchone()

        if not local:
            return api_response('E002', http_status=404, data={'local_id': local_id})

        return jsonify({
            'id': local['id'],
            'name': local['name'],
            'address': local.get('address', ''),
            'city': local.get('city', ''),
            'status': local.get('status', 'activo'),
            'telefono': local.get('telefono', ''),
            'horario': local.get('horario', ''),
            'notas': local.get('notas', ''),
        })

    except Exception as e:
        logger.error(f"Error obteniendo local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'address', 'city'])
def crear_local():
    """Crear un nuevo local"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name     = data['name']
        address  = data['address']
        city     = data['city']
        status   = data.get('status', 'activo')
        telefono = data.get('telefono', '')
        horario  = data.get('horario', '')
        notas    = data.get('notas', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM location WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Local ya existe'})

        cursor.execute("""
            INSERT INTO location (name, address, city, status, telefono, horario, notas)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (name, address, city, status, telefono, horario, notas))

        connection.commit()

        logger.info(f"Local creado: {name} en {city}")

        return api_response('S002', status='success', data={'local_id': cursor.lastrowid})

    except Exception as e:
        logger.error(f"Error creando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales/<int:local_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'address', 'city'])
def actualizar_local(local_id):
    """Actualizar un local existente"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        name     = data['name']
        address  = data['address']
        city     = data['city']
        status   = data.get('status')
        telefono = data.get('telefono', '')
        horario  = data.get('horario', '')
        notas    = data.get('notas', '')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM location WHERE id = %s", (local_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': local_id})

        cursor.execute("SELECT id FROM location WHERE name = %s AND id != %s", (name, local_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de local ya existe'})

        cursor.execute("""
            UPDATE location
            SET name = %s, address = %s, city = %s, status = %s,
                telefono = %s, horario = %s, notas = %s
            WHERE id = %s
        """, (name, address, city, status, telefono, horario, notas, local_id))

        connection.commit()

        logger.info(f"Local actualizado: {name} (ID: {local_id})")

        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()


@locations_bp.route('/api/locales/<int:local_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_local(local_id):
    """Eliminar un local"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM location WHERE id = %s", (local_id,))
        local = cursor.fetchone()
        if not local:
            return api_response('E002', http_status=404, data={'local_id': local_id})

        cursor.execute(
            "SELECT COUNT(*) as maquinas_count FROM machine WHERE location_id = %s", (local_id,)
        )
        maquinas_count = cursor.fetchone()['maquinas_count']

        if maquinas_count > 0:
            return api_response(
                'W005',
                status='warning',
                http_status=400,
                data={
                    'message': f'Local tiene {maquinas_count} máquinas asignadas',
                    'maquinas_count': maquinas_count,
                }
            )

        cursor.execute("DELETE FROM location WHERE id = %s", (local_id,))
        connection.commit()

        logger.info(f"Local eliminado: {local['name']} (ID: {local_id})")

        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:  cursor.close()
        if connection: connection.close()
