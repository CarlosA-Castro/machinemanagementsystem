import logging
import traceback
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.location_scope import apply_location_name_filter
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

liquidaciones_bp = Blueprint('liquidaciones', __name__)

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


def _get_period():
    today = get_colombia_time().date()
    fecha_inicio = _parse_date(request.args.get('fechaInicio') or request.args.get('fecha_inicio'), today)
    fecha_fin = _parse_date(request.args.get('fechaFin') or request.args.get('fecha_fin'), today)
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
    return fecha_inicio, fecha_fin


def _get_previous_period(fecha_inicio, fecha_fin):
    delta = (fecha_fin - fecha_inicio).days + 1
    fecha_fin_previa = fecha_inicio - timedelta(days=1)
    fecha_inicio_previa = fecha_fin_previa - timedelta(days=delta - 1)
    return fecha_inicio_previa, fecha_fin_previa


def _table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _fetch_package_summary(cursor, fecha_inicio, fecha_fin):
    cursor.execute(
        """
        SELECT
            tp.id as paquete_id,
            tp.name as paquete_nombre,
            tp.turns as turnos_por_paquete,
            COUNT(DISTINCT qh.qr_code) as paquetes_vendidos,
            COALESCE(SUM(tp.price), 0) as ingresos_totales,
            COALESCE(SUM(ut.turns_remaining), 0) as turnos_restantes
        FROM qrhistory qh
        JOIN qrcode qr ON qr.code = qh.qr_code
        JOIN turnpackage tp ON qr.turnPackageId = tp.id
        LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
          AND qh.es_venta_real = TRUE
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
        GROUP BY tp.id, tp.name, tp.turns
        ORDER BY ingresos_totales DESC
        """,
        (fecha_inicio, fecha_fin),
    )

    paquetes = []
    for row in cursor.fetchall():
        turnos_por_paquete = int(row['turnos_por_paquete'] or 0)
        paquetes_vendidos = int(row['paquetes_vendidos'] or 0)
        turnos_totales = paquetes_vendidos * turnos_por_paquete
        turnos_restantes = int(row['turnos_restantes'] or 0)
        turnos_usados = max(turnos_totales - turnos_restantes, 0)
        precio_promedio_turno = (
            _to_float(row['ingresos_totales']) / turnos_totales
            if turnos_totales > 0
            else 0.0
        )

        paquetes.append(
            {
                'paquete_id': row['paquete_id'],
                'paquete_nombre': row['paquete_nombre'],
                'paquetes_vendidos': paquetes_vendidos,
                'turnos_por_paquete': turnos_por_paquete,
                'turnos_totales': turnos_totales,
                'turnos_usados': turnos_usados,
                'turnos_restantes': turnos_restantes,
                'ingresos_totales': _to_float(row['ingresos_totales']),
                'valor_turnos_restantes': round(turnos_restantes * precio_promedio_turno, 2),
            }
        )

    return paquetes


def _fetch_usage_summary(cursor, fecha_inicio, fecha_fin):
    cursor.execute(
        """
        SELECT
            m.id as maquina_id,
            m.name as maquina_nombre,
            m.type as tipo_maquina,
            COALESCE(mpr.porcentaje_restaurante, %s) as porcentaje_restaurante,
            COUNT(tu.id) as turnos_usados,
            COUNT(DISTINCT qr.id) as qrs_utilizados,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) as ingresos_estimados
        FROM turnusage tu
        JOIN machine m ON tu.machineId = m.id
        JOIN qrcode qr ON tu.qrCodeId = qr.id
        LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
        LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
        WHERE DATE(tu.usedAt) BETWEEN %s AND %s
        GROUP BY m.id, m.name, m.type, mpr.porcentaje_restaurante
        ORDER BY ingresos_estimados DESC
        """,
        (RESTAURANT_PERCENTAGE_DEFAULT, fecha_inicio, fecha_fin),
    )

    resumen = {}
    for row in cursor.fetchall():
        resumen[row['maquina_id']] = {
            'maquina_id': row['maquina_id'],
            'maquina_nombre': row['maquina_nombre'],
            'tipo_maquina': row['tipo_maquina'],
            'porcentaje_restaurante': _to_float(row['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT),
            'turnos_usados': int(row['turnos_usados'] or 0),
            'qrs_utilizados': int(row['qrs_utilizados'] or 0),
            'ingresos_estimados': _to_float(row['ingresos_estimados']),
        }

    cursor.execute(
        """
        SELECT
            tu.machineId as maquina_id,
            tp.id as paquete_id,
            tp.name as paquete_nombre,
            COALESCE(tp.turns, 0) as turnos_por_paquete,
            COUNT(tu.id) as turnos_usados,
            COUNT(DISTINCT qr.id) as qrs_utilizados,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) as ingresos_estimados
        FROM turnusage tu
        JOIN qrcode qr ON tu.qrCodeId = qr.id
        LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
        WHERE DATE(tu.usedAt) BETWEEN %s AND %s
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
        GROUP BY tu.machineId, tp.id, tp.name, tp.turns
        ORDER BY ingresos_estimados DESC
        """,
        (fecha_inicio, fecha_fin),
    )

    paquetes_por_maquina = {}
    for row in cursor.fetchall():
        paquetes_por_maquina.setdefault(row['maquina_id'], []).append(
            {
                'paquete_id': row['paquete_id'],
                'paquete_nombre': row['paquete_nombre'],
                'turnos_por_paquete': int(row['turnos_por_paquete'] or 0),
                'turnos_usados': int(row['turnos_usados'] or 0),
                'qrs_utilizados': int(row['qrs_utilizados'] or 0),
                'ingresos_estimados': _to_float(row['ingresos_estimados']),
            }
        )

    return resumen, paquetes_por_maquina


def _fetch_machine_liquidations(cursor, fecha_inicio, fecha_fin, limit=None):
    if not _table_exists(cursor, 'liquidaciones'):
        return []

    query = """
        SELECT
            l.id,
            l.fecha,
            l.maquina_id,
            l.turnos_retirados,
            l.valor_por_turno,
            l.costos_operativos,
            l.porcentaje_restaurante,
            l.observaciones,
            l.usuario_id,
            l.creado_el,
            m.name as maquina_nombre,
            m.type as tipo_maquina
        FROM liquidaciones l
        JOIN machine m ON l.maquina_id = m.id
        WHERE l.fecha BETWEEN %s AND %s
        ORDER BY l.fecha DESC, l.creado_el DESC
    """

    params = [fecha_inicio, fecha_fin]
    if limit:
        query += " LIMIT %s"
        params.append(limit)

    cursor.execute(query, tuple(params))
    rows = []
    for row in cursor.fetchall():
        turnos = int(row['turnos_retirados'] or 0)
        valor_turno = _to_float(row['valor_por_turno'], 0)
        costos = _to_float(row['costos_operativos'], 0)
        porcentaje_restaurante = _to_float(row['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT)
        ingreso_bruto = turnos * valor_turno
        negocio = ingreso_bruto * porcentaje_restaurante / 100
        administracion = ingreso_bruto * ADMIN_PERCENTAGE_DEFAULT / 100
        utilidad_operativa = ingreso_bruto - negocio - administracion - costos

        rows.append(
            {
                'id': row['id'],
                'fecha': row['fecha'].isoformat() if row['fecha'] else None,
                'maquina_id': row['maquina_id'],
                'maquina_nombre': row['maquina_nombre'],
                'tipo_maquina': row['tipo_maquina'],
                'turnos_retirados': turnos,
                'valor_por_turno': valor_turno,
                'costos_operativos': costos,
                'porcentaje_restaurante': porcentaje_restaurante,
                'ingreso_bruto': round(ingreso_bruto, 2),
                'negocio': round(negocio, 2),
                'administracion': round(administracion, 2),
                'utilidad_operativa': round(utilidad_operativa, 2),
                'observaciones': row['observaciones'],
                'usuario_id': row['usuario_id'],
                'creado_el': row['creado_el'].isoformat() if row['creado_el'] else None,
            }
        )

    return rows


def _group_liquidations_by_machine(rows):
    grouped = {}
    for row in rows:
        machine = grouped.setdefault(
            row['maquina_id'],
            {
                'maquina_id': row['maquina_id'],
                'maquina_nombre': row['maquina_nombre'],
                'tipo_maquina': row['tipo_maquina'],
                'liquidaciones_registradas': 0,
                'turnos_retirados': 0,
                'ingreso_manual_total': 0.0,
                'costos_operativos': 0.0,
                'utilidad_operativa': 0.0,
            },
        )
        machine['liquidaciones_registradas'] += 1
        machine['turnos_retirados'] += row['turnos_retirados']
        machine['ingreso_manual_total'] += row['ingreso_bruto']
        machine['costos_operativos'] += row['costos_operativos']
        machine['utilidad_operativa'] += row['utilidad_operativa']
    return grouped


def _pct_change(actual, previous):
    actual = _to_float(actual)
    previous = _to_float(previous)
    if previous <= 0:
        return 100.0 if actual > 0 else 0.0
    return round(((actual - previous) / previous) * 100, 2)


def _build_machine_summary(cursor, fecha_inicio, fecha_fin):
    fecha_inicio_previa, fecha_fin_previa = _get_previous_period(fecha_inicio, fecha_fin)
    resumen_actual, paquetes_actuales = _fetch_usage_summary(cursor, fecha_inicio, fecha_fin)
    resumen_previo, _ = _fetch_usage_summary(cursor, fecha_inicio_previa, fecha_fin_previa)

    liquidaciones_actuales = _fetch_machine_liquidations(cursor, fecha_inicio, fecha_fin)
    liquidaciones_previas = _fetch_machine_liquidations(cursor, fecha_inicio_previa, fecha_fin_previa)
    manual_actual = _group_liquidations_by_machine(liquidaciones_actuales)
    manual_previo = _group_liquidations_by_machine(liquidaciones_previas)

    machine_ids = set(resumen_actual) | set(manual_actual)
    resumen_maquinas = {}

    for machine_id in machine_ids:
        actual = resumen_actual.get(machine_id, {})
        previo = resumen_previo.get(machine_id, {})
        manual_data = manual_actual.get(machine_id, {})
        manual_prev = manual_previo.get(machine_id, {})

        nombre = actual.get('maquina_nombre') or manual_data.get('maquina_nombre') or f'Máquina #{machine_id}'
        tipo = actual.get('tipo_maquina') or manual_data.get('tipo_maquina') or 'general'
        porcentaje_restaurante = actual.get('porcentaje_restaurante', RESTAURANT_PERCENTAGE_DEFAULT)
        ingreso_base = manual_data.get('ingreso_manual_total') or actual.get('ingresos_estimados', 0.0)
        ingreso_previo = manual_prev.get('ingreso_manual_total') or previo.get('ingresos_estimados', 0.0)
        ingresos_restaurante = ingreso_base * porcentaje_restaurante / 100
        ingresos_proveedor = ingreso_base - ingresos_restaurante

        resumen_maquinas[nombre] = {
            'maquina_id': machine_id,
            'maquina_nombre': nombre,
            'tipo_maquina': tipo,
            'ventas_realizadas': int(actual.get('qrs_utilizados', 0)),
            'paquetes_vendidos': int(actual.get('qrs_utilizados', 0)),
            'turnos_usados': int(actual.get('turnos_usados', 0)),
            'turnos_retirados': int(manual_data.get('turnos_retirados', 0)),
            'ingresos_estimados': round(_to_float(actual.get('ingresos_estimados')), 2),
            'ingresos_totales': round(_to_float(ingreso_base), 2),
            'porcentaje_restaurante': round(_to_float(porcentaje_restaurante), 2),
            'ingresos_restaurante': round(_to_float(ingresos_restaurante), 2),
            'ingresos_proveedor': round(_to_float(ingresos_proveedor), 2),
            'costos_operativos': round(_to_float(manual_data.get('costos_operativos')), 2),
            'utilidad_operativa': round(_to_float(manual_data.get('utilidad_operativa', ingresos_proveedor)), 2),
            'liquidaciones_registradas': int(manual_data.get('liquidaciones_registradas', 0)),
            'fuente_ingresos': 'manual' if manual_data.get('ingreso_manual_total') else 'estimada_por_uso',
            'rendimiento_porcentual': _pct_change(ingreso_base, ingreso_previo),
            'paquetes': paquetes_actuales.get(machine_id, []),
        }

    return resumen_maquinas, liquidaciones_actuales, liquidaciones_previas


def _build_propietarios_distribution(cursor, fecha_inicio, fecha_fin):
    if not (_table_exists(cursor, 'maquinapropietario') and _table_exists(cursor, 'propietarios')):
        return {}

    cursor.execute(
        """
        SELECT
            p.id as propietario_id,
            p.nombre as propietario_nombre,
            m.id as maquina_id,
            m.name as maquina_nombre,
            mp.porcentaje_propiedad,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) as ingresos_estimados,
            COUNT(tu.id) as turnos_usados
        FROM maquinapropietario mp
        JOIN propietarios p ON mp.propietario_id = p.id
        JOIN machine m ON mp.maquina_id = m.id
        LEFT JOIN turnusage tu ON tu.machineId = m.id AND DATE(tu.usedAt) BETWEEN %s AND %s
        LEFT JOIN qrcode qr ON tu.qrCodeId = qr.id
        LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
        GROUP BY p.id, p.nombre, m.id, m.name, mp.porcentaje_propiedad
        ORDER BY ingresos_estimados DESC
        """,
        (fecha_inicio, fecha_fin),
    )

    distribucion = {}
    for row in cursor.fetchall():
        propietario = distribucion.setdefault(
            row['propietario_nombre'],
            {
                'propietario_id': row['propietario_id'],
                'total_ingresos': 0.0,
                'ventas_asociadas': 0,
                'detalles_maquinas': [],
            },
        )
        ingreso_prop = _to_float(row['ingresos_estimados']) * _to_float(row['porcentaje_propiedad']) / 100
        propietario['total_ingresos'] += ingreso_prop
        propietario['ventas_asociadas'] += int(row['turnos_usados'] or 0)
        propietario['detalles_maquinas'].append(
            {
                'maquina_id': row['maquina_id'],
                'maquina_nombre': row['maquina_nombre'],
                'porcentaje_propiedad': _to_float(row['porcentaje_propiedad']),
                'monto_propietario': round(ingreso_prop, 2),
            }
        )

    for propietario in distribucion.values():
        propietario['total_ingresos'] = round(propietario['total_ingresos'], 2)

    return distribucion


def _build_investor_liquidation(cursor, resumen_maquinas, fecha_inicio, fecha_fin):
    if not (_table_exists(cursor, 'inversiones') and _table_exists(cursor, 'socios')):
        return []

    cursor.execute(
        """
        SELECT
            i.id as inversion_id,
            i.socio_id,
            s.nombre as socio_nombre,
            i.maquina_id,
            i.porcentaje_inversion,
            i.monto_inicial,
            i.fecha_inicio,
            i.fecha_fin,
            m.name as maquina_nombre
        FROM inversiones i
        JOIN socios s ON i.socio_id = s.id
        JOIN machine m ON i.maquina_id = m.id
        WHERE i.estado = 'activa'
          AND i.fecha_inicio <= %s
          AND (i.fecha_fin IS NULL OR i.fecha_fin >= %s)
        ORDER BY s.nombre, m.name
        """,
        (fecha_fin, fecha_inicio),
    )

    inversionistas = {}
    for row in cursor.fetchall():
        maquina = next(
            (item for item in resumen_maquinas.values() if item['maquina_id'] == row['maquina_id']),
            None,
        )
        if not maquina:
            continue

        porcentaje_inversion = _to_float(row['porcentaje_inversion'])
        ingreso_total = _to_float(maquina['ingresos_totales'])
        porcentaje_negocio = _to_float(maquina['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT)
        utilidad_base = ingreso_total * (1 - porcentaje_negocio / 100 - ADMIN_PERCENTAGE_DEFAULT / 100)
        utilidad_base -= _to_float(maquina['costos_operativos'])
        utilidad_participacion = utilidad_base * porcentaje_inversion / 100
        ingreso_participacion = ingreso_total * porcentaje_inversion / 100

        inversionista = inversionistas.setdefault(
            row['socio_id'],
            {
                'socio_id': row['socio_id'],
                'socio_nombre': row['socio_nombre'],
                'maquinas': [],
                'maquinas_poseidas': 0,
                'ingreso_total': 0.0,
                'utilidad_total': 0.0,
                'turnos_usados': 0.0,
                'gastos_mantenimiento_estimados': 0.0,
                'porcentaje_participacion_total': 0.0,
                'rendimiento_porcentual': 0.0,
                'paquetes_top': [],
            },
        )

        machine_packages = []
        for package_item in maquina.get('paquetes', []):
            machine_packages.append(
                {
                    'paquete_nombre': package_item['paquete_nombre'],
                    'turnos_usados': package_item['turnos_usados'],
                    'ingresos_estimados': round(package_item['ingresos_estimados'], 2),
                }
            )

        inversionista['maquinas'].append(
            {
                'inversion_id': row['inversion_id'],
                'maquina_id': row['maquina_id'],
                'maquina_nombre': row['maquina_nombre'],
                'porcentaje_inversion': porcentaje_inversion,
                'ingreso_total_maquina': round(ingreso_total, 2),
                'ingreso_participacion': round(ingreso_participacion, 2),
                'utilidad_participacion': round(utilidad_participacion, 2),
                'porcentaje_negocio': porcentaje_negocio,
                'porcentaje_administracion': ADMIN_PERCENTAGE_DEFAULT,
                'turnos_usados': maquina['turnos_usados'],
                'costos_operativos': round(_to_float(maquina['costos_operativos']), 2),
                'rendimiento_porcentual': round(_to_float(maquina['rendimiento_porcentual']), 2),
                'paquetes': machine_packages,
                'fuente_ingresos': maquina['fuente_ingresos'],
            }
        )
        inversionista['maquinas_poseidas'] += 1
        inversionista['ingreso_total'] += ingreso_participacion
        inversionista['utilidad_total'] += utilidad_participacion
        inversionista['turnos_usados'] += maquina['turnos_usados'] * porcentaje_inversion / 100
        inversionista['gastos_mantenimiento_estimados'] += _to_float(maquina['costos_operativos']) * porcentaje_inversion / 100
        inversionista['porcentaje_participacion_total'] += porcentaje_inversion

    resultado = []
    for inversionista in inversionistas.values():
        paquetes_counter = Counter()
        rendimiento_total = 0.0

        for maquina in inversionista['maquinas']:
            rendimiento_total += _to_float(maquina['rendimiento_porcentual'])
            for paquete in maquina['paquetes']:
                paquetes_counter[paquete['paquete_nombre']] += paquete['turnos_usados']

        inversionista['paquetes_top'] = [
            {'paquete_nombre': nombre, 'turnos_usados': turnos}
            for nombre, turnos in paquetes_counter.most_common(3)
        ]
        inversionista['ingreso_total'] = round(inversionista['ingreso_total'], 2)
        inversionista['utilidad_total'] = round(inversionista['utilidad_total'], 2)
        inversionista['turnos_usados'] = round(inversionista['turnos_usados'], 2)
        inversionista['gastos_mantenimiento_estimados'] = round(
            inversionista['gastos_mantenimiento_estimados'], 2
        )
        inversionista['rendimiento_porcentual'] = round(
            rendimiento_total / max(inversionista['maquinas_poseidas'], 1),
            2,
        )
        resultado.append(inversionista)

    resultado.sort(key=lambda item: item['utilidad_total'], reverse=True)
    return resultado


def _build_period_comparison(cursor, fecha_inicio, fecha_fin):
    fecha_inicio_previa, fecha_fin_previa = _get_previous_period(fecha_inicio, fecha_fin)

    def _period_totals(start_date, end_date):
        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as total_ingresos
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qh.es_venta_real = TRUE
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
            """,
            (start_date, end_date),
        )
        row = cursor.fetchone() or {}
        return {
            'ventas': int(row.get('total_ventas') or 0),
            'ingresos': _to_float(row.get('total_ingresos')),
        }

    actual = _period_totals(fecha_inicio, fecha_fin)
    previo = _period_totals(fecha_inicio_previa, fecha_fin_previa)

    return {
        'periodo_actual': {
            'fecha_inicio': fecha_inicio.isoformat(),
            'fecha_fin': fecha_fin.isoformat(),
            **actual,
        },
        'periodo_previo': {
            'fecha_inicio': fecha_inicio_previa.isoformat(),
            'fecha_fin': fecha_fin_previa.isoformat(),
            **previo,
        },
        'variacion_ingresos_pct': _pct_change(actual['ingresos'], previo['ingresos']),
        'variacion_ventas_pct': _pct_change(actual['ventas'], previo['ventas']),
    }


@liquidaciones_bp.route('/api/ventas-liquidadas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_ventas_liquidadas():
    """Obtener ventas liquidadas con distribución real."""
    connection = None
    cursor = None
    try:
        logger.info("=== INICIANDO OBTENER VENTAS LIQUIDADAS ===")

        fecha_inicio, fecha_fin = _get_period()
        pagina = int(request.args.get('pagina', 1))
        por_pagina = int(request.args.get('porPagina', 50))
        offset = (pagina - 1) * por_pagina

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        tiene_porcentaje = _table_exists(cursor, 'maquinaporcentajerestaurante')
        tiene_propietarios = _table_exists(cursor, 'maquinapropietario') and _table_exists(cursor, 'propietarios')

        count_sql, count_params = apply_location_name_filter(
            """
            SELECT COUNT(*) as total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            """,
            [fecha_inicio, fecha_fin],
            column='local',
            table_alias='qh',
        )
        cursor.execute(count_sql, count_params)
        total = (cursor.fetchone() or {}).get('total', 0)

        if total == 0:
            return jsonify(
                {
                    'datos': [],
                    'totalRegistros': 0,
                    'totalIngresos': 0,
                    'gananciaTotal': 0,
                    'gananciaProveedor': 0,
                    'gananciaRestaurante': 0,
                    'paginaActual': pagina,
                    'totalPaginas': 1,
                    'mensaje': 'No hay ventas registradas en el período seleccionado',
                }
            )

        if tiene_porcentaje and tiene_propietarios:
            query, params = apply_location_name_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) as fecha,
                    qh.qr_code,
                    qh.user_name as vendedor,
                    tp.name as paquete_nombre,
                    tp.turns as turnos_usados,
                    tp.price as precio_unitario,
                    1 as cantidad_paquetes,
                    tp.price as ingresos_totales,
                    COALESCE(m.name, 'Máquina no especificada') as maquina_nombre,
                    COALESCE(mpr.porcentaje_restaurante, %s) as porcentaje_restaurante,
                    (tp.price * COALESCE(mpr.porcentaje_restaurante, %s) / 100) as ingresos_restaurante,
                    (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, %s)) / 100) as ingresos_proveedor,
                    (tp.price * 0.30) as ingresos_30_porciento,
                    (tp.price * 0.35) as ingresos_35_porciento,
                    COALESCE(p.nombre, 'Propietario general') as propietario,
                    COALESCE(mp.porcentaje_propiedad, 100.00) as porcentaje_propiedad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                LEFT JOIN turnusage tu ON qr.id = tu.qrCodeId
                LEFT JOIN machine m ON tu.machineId = m.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN maquinapropietario mp ON m.id = mp.maquina_id
                LEFT JOIN propietarios p ON mp.propietario_id = p.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                LIMIT %s OFFSET %s
                """,
                [
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    fecha_inicio,
                    fecha_fin,
                    por_pagina,
                    offset,
                ],
                column='local',
                table_alias='qh',
            )
        else:
            query, params = apply_location_name_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) as fecha,
                    qh.qr_code,
                    qh.user_name as vendedor,
                    tp.name as paquete_nombre,
                    tp.turns as turnos_usados,
                    tp.price as precio_unitario,
                    1 as cantidad_paquetes,
                    tp.price as ingresos_totales,
                    'Máquina no especificada' as maquina_nombre,
                    %s as porcentaje_restaurante,
                    (tp.price * %s / 100) as ingresos_restaurante,
                    (tp.price * (100 - %s) / 100) as ingresos_proveedor,
                    (tp.price * 0.30) as ingresos_30_porciento,
                    (tp.price * 0.35) as ingresos_35_porciento,
                    'Propietario general' as propietario,
                    100.00 as porcentaje_propiedad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                LIMIT %s OFFSET %s
                """,
                [
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    fecha_inicio,
                    fecha_fin,
                    por_pagina,
                    offset,
                ],
                column='local',
                table_alias='qh',
            )

        cursor.execute(query, params)
        ventas = cursor.fetchall()

        total_ingresos = sum(_to_float(v['ingresos_totales']) for v in ventas)
        total_restaurante = sum(_to_float(v['ingresos_restaurante']) for v in ventas)
        total_proveedor = sum(_to_float(v['ingresos_proveedor']) for v in ventas)

        return jsonify(
            {
                'datos': ventas,
                'totalRegistros': total,
                'totalIngresos': round(total_ingresos, 2),
                'gananciaTotal': round(total_ingresos, 2),
                'gananciaProveedor': round(total_proveedor, 2),
                'gananciaRestaurante': round(total_restaurante, 2),
                'paginaActual': pagina,
                'totalPaginas': (total + por_pagina - 1) // por_pagina,
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo ventas liquidadas: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/calcular', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def calcular_liquidacion():
    """Calcular liquidación detallada por período."""
    connection = None
    cursor = None
    try:
        data = request.get_json() or {}
        fecha_inicio = _parse_date(data.get('fecha_inicio'), get_colombia_time().date())
        fecha_fin = _parse_date(data.get('fecha_fin'), get_colombia_time().date())
        if fecha_inicio > fecha_fin:
            fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        tiene_porcentaje = _table_exists(cursor, 'maquinaporcentajerestaurante')
        tiene_propietarios = _table_exists(cursor, 'maquinapropietario')
        tiene_tabla_propietarios = _table_exists(cursor, 'propietarios')

        periodo_sql, periodo_params = apply_location_name_filter(
            """
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as total_ingresos,
                COUNT(DISTINCT tu.machineId) as maquinas_utilizadas
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON qr.id = tu.qrCodeId
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            """,
            [fecha_inicio, fecha_fin],
            column='local',
            table_alias='qh',
        )
        cursor.execute(periodo_sql, periodo_params)
        periodo = cursor.fetchone() or {}
        total_ingresos = _to_float(periodo.get('total_ingresos'))

        paquetes_resumen = _fetch_package_summary(cursor, fecha_inicio, fecha_fin)
        resumen_maquinas, liquidaciones_maquinas, _ = _build_machine_summary(cursor, fecha_inicio, fecha_fin)
        distribucion_propietarios = (
            _build_propietarios_distribution(cursor, fecha_inicio, fecha_fin)
            if tiene_propietarios and tiene_tabla_propietarios
            else {}
        )
        inversionistas = _build_investor_liquidation(cursor, resumen_maquinas, fecha_inicio, fecha_fin)
        comparativos = _build_period_comparison(cursor, fecha_inicio, fecha_fin)

        if resumen_maquinas:
            total_restaurante = sum(_to_float(item['ingresos_restaurante']) for item in resumen_maquinas.values())
            total_proveedor = sum(_to_float(item['ingresos_proveedor']) for item in resumen_maquinas.values())
        else:
            total_restaurante = total_ingresos * (RESTAURANT_PERCENTAGE_DEFAULT / 100)
            total_proveedor = total_ingresos - total_restaurante

        datos_tabla = []
        if tiene_porcentaje and tiene_propietarios and tiene_tabla_propietarios:
            datos_sql, datos_params = apply_location_name_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) as fecha,
                    qh.qr_code,
                    tp.name as paquete_nombre,
                    tp.turns as turnos_usados,
                    COALESCE(m.name, 'No especificada') as maquina_nombre,
                    tp.price as ingresos_totales,
                    COALESCE(mpr.porcentaje_restaurante, %s) as porcentaje_restaurante,
                    (tp.price * COALESCE(mpr.porcentaje_restaurante, %s) / 100) as ingresos_restaurante,
                    (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, %s)) / 100) as ingresos_proveedor,
                    COALESCE(p.nombre, 'No asignado') as propietario
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                LEFT JOIN turnusage tu ON qr.id = tu.qrCodeId
                LEFT JOIN machine m ON tu.machineId = m.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN maquinapropietario mp ON m.id = mp.maquina_id
                LEFT JOIN propietarios p ON mp.propietario_id = p.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                """,
                [
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    fecha_inicio,
                    fecha_fin,
                ],
                column='local',
                table_alias='qh',
            )
        else:
            datos_sql, datos_params = apply_location_name_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) as fecha,
                    qh.qr_code,
                    tp.name as paquete_nombre,
                    tp.turns as turnos_usados,
                    'No especificada' as maquina_nombre,
                    tp.price as ingresos_totales,
                    %s as porcentaje_restaurante,
                    (tp.price * %s / 100) as ingresos_restaurante,
                    (tp.price * (100 - %s) / 100) as ingresos_proveedor,
                    'No asignado' as propietario
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                """,
                [
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    RESTAURANT_PERCENTAGE_DEFAULT,
                    fecha_inicio,
                    fecha_fin,
                ],
                column='local',
                table_alias='qh',
            )
        cursor.execute(datos_sql, datos_params)
        datos_tabla = cursor.fetchall()

        return jsonify(
            {
                'success': True,
                'configuracion': {
                    'porcentaje_negocio_default': RESTAURANT_PERCENTAGE_DEFAULT,
                    'porcentaje_administracion_default': ADMIN_PERCENTAGE_DEFAULT,
                    'usa_porcentajes_personalizados': tiene_porcentaje,
                },
                'periodo': {
                    'fecha_inicio': fecha_inicio.isoformat(),
                    'fecha_fin': fecha_fin.isoformat(),
                    'total_ventas': int(periodo.get('total_ventas') or 0),
                    'total_ingresos': round(total_ingresos, 2),
                    'total_restaurante': round(total_restaurante, 2),
                    'total_proveedor': round(total_proveedor, 2),
                    'maquinas_utilizadas': int(periodo.get('maquinas_utilizadas') or 0),
                },
                'comparativos': comparativos,
                'distribucion_propietarios': distribucion_propietarios,
                'resumen_maquinas': resumen_maquinas,
                'datos_tabla': datos_tabla,
                'paquetes_resumen': paquetes_resumen,
                'inversionistas': inversionistas,
                'ultimas_liquidaciones_maquinas': liquidaciones_maquinas[:5],
                'totales': {
                    'ingresos_totales': round(total_ingresos, 2),
                    'ganancia_restaurante': round(total_restaurante, 2),
                    'ganancia_proveedores': round(total_proveedor, 2),
                },
            }
        )
    except Exception as e:
        logger.error(f"Error calculando liquidación: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/maquinas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_liquidaciones_maquinas():
    connection = None
    cursor = None
    try:
        fecha_inicio, fecha_fin = _get_period()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        liquidaciones = _fetch_machine_liquidations(cursor, fecha_inicio, fecha_fin)

        total_ingreso = sum(item['ingreso_bruto'] for item in liquidaciones)
        total_costos = sum(item['costos_operativos'] for item in liquidaciones)
        total_utilidad = sum(item['utilidad_operativa'] for item in liquidaciones)

        return jsonify(
            {
                'datos': liquidaciones,
                'totalRegistros': len(liquidaciones),
                'totales': {
                    'ingreso_bruto': round(total_ingreso, 2),
                    'costos_operativos': round(total_costos, 2),
                    'utilidad_operativa': round(total_utilidad, 2),
                },
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo liquidaciones de máquinas: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/maquinas/catalogo', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_catalogo_maquinas_liquidacion():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT
                m.id,
                m.name,
                m.type,
                COALESCE(m.valor_por_turno, 3000.00) as valor_por_turno,
                COALESCE(mpr.porcentaje_restaurante, %s) as porcentaje_restaurante
            FROM machine m
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
            ORDER BY m.name ASC
            """,
            (RESTAURANT_PERCENTAGE_DEFAULT,),
        )
        maquinas = cursor.fetchall()
        return jsonify({'datos': maquinas, 'totalRegistros': len(maquinas)})
    except Exception as e:
        logger.error(f"Error obteniendo catálogo de máquinas: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/maquinas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['fecha', 'maquina_id', 'turnos_retirados'])
def registrar_liquidacion_maquina():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        if not _table_exists(cursor, 'liquidaciones'):
            return api_response(
                'E002',
                http_status=404,
                data={'message': 'La tabla liquidaciones no está disponible en esta base de datos'},
            )

        cursor.execute(
            """
            SELECT
                m.id,
                m.name,
                COALESCE(m.valor_por_turno, 3000.00) as valor_por_turno,
                COALESCE(mpr.porcentaje_restaurante, %s) as porcentaje_restaurante
            FROM machine m
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
            WHERE m.id = %s
            """,
            (RESTAURANT_PERCENTAGE_DEFAULT, data['maquina_id']),
        )
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': data['maquina_id']})

        valor_por_turno = _to_float(data.get('valor_por_turno'), _to_float(maquina['valor_por_turno'], 3000.0))
        porcentaje_restaurante = _to_float(
            data.get('porcentaje_restaurante'),
            _to_float(maquina['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT),
        )

        cursor.execute(
            """
            INSERT INTO liquidaciones (
                fecha,
                maquina_id,
                turnos_retirados,
                valor_por_turno,
                costos_operativos,
                porcentaje_restaurante,
                observaciones,
                usuario_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                data['fecha'],
                data['maquina_id'],
                int(data['turnos_retirados']),
                valor_por_turno,
                _to_float(data.get('costos_operativos')),
                porcentaje_restaurante,
                (data.get('observaciones') or '').strip() or None,
                session.get('user_id'),
            ),
        )
        liquidacion_id = cursor.lastrowid
        connection.commit()

        return jsonify(
            {
                'success': True,
                'liquidacion_id': liquidacion_id,
                'message': f'Ingreso de {maquina["name"]} registrado correctamente',
            }
        )
    except Exception as e:
        logger.error(f"Error registrando liquidación de máquina: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/verificar-tablas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def verificar_tablas_liquidaciones():
    """Verificar qué tablas existen para liquidaciones."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        tablas_requeridas = [
            'maquinaporcentajerestaurante',
            'maquinapropietario',
            'propietarios',
            'liquidaciones',
            'inversiones',
            'socios',
            'userturns',
        ]

        resultados = {}
        for tabla in tablas_requeridas:
            existe = _table_exists(cursor, tabla)
            resultados[tabla] = existe
            if existe:
                cursor.execute(f"DESCRIBE {tabla}")
                resultados[f'{tabla}_columnas'] = [col['Field'] for col in cursor.fetchall()]

        return jsonify(
            {
                'tablas': resultados,
                'recomendaciones': [
                    'Configurar porcentajes por máquina en maquinaporcentajerestaurante'
                    if resultados.get('maquinaporcentajerestaurante')
                    else 'Falta maquinaporcentajerestaurante para reparto por negocio',
                    'Usar socios + inversiones para liquidación del inversionista'
                    if resultados.get('socios') and resultados.get('inversiones')
                    else 'Faltan socios/inversiones para liquidación de inversionistas',
                    'Registrar cierres en tabla liquidaciones para ingreso real por máquina'
                    if resultados.get('liquidaciones')
                    else 'La tabla liquidaciones no está disponible para cierres manuales',
                ],
            }
        )
    except Exception as e:
        logger.error(f"Error verificando tablas: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
