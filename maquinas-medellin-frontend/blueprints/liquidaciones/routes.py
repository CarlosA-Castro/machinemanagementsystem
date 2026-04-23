import logging
import traceback
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.location_scope import apply_location_filter, apply_location_name_filter
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

liquidaciones_bp = Blueprint('liquidaciones', __name__)

ADMIN_PERCENTAGE_DEFAULT = 25.0
RESTAURANT_PERCENTAGE_DEFAULT = 35.0


# ─── Helpers básicos ──────────────────────────────────────────────────────────

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
    fecha_inicio = _parse_date(
        request.args.get('fechaInicio') or request.args.get('fecha_inicio'), today
    )
    fecha_fin = _parse_date(
        request.args.get('fechaFin') or request.args.get('fecha_fin'), today
    )
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
    return fecha_inicio, fecha_fin


def _get_previous_period(fecha_inicio, fecha_fin):
    delta = (fecha_fin - fecha_inicio).days + 1
    fecha_fin_previa = fecha_inicio - timedelta(days=1)
    fecha_inicio_previa = fecha_fin_previa - timedelta(days=delta - 1)
    return fecha_inicio_previa, fecha_fin_previa


def _table_exists(cursor, table_name):
    cursor.execute('SHOW TABLES LIKE %s', (table_name,))
    return cursor.fetchone() is not None


def _pct_change(actual, previous):
    actual = _to_float(actual)
    previous = _to_float(previous)
    if previous <= 0:
        return 100.0 if actual > 0 else 0.0
    return round(((actual - previous) / previous) * 100, 2)


def _has_admin_col(cursor):
    """True si porcentaje_admin ya existe en maquinaporcentajerestaurante (V40+)."""
    try:
        cursor.execute(
            """SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME   = 'maquinaporcentajerestaurante'
                 AND COLUMN_NAME  = 'porcentaje_admin'"""
        )
        row = cursor.fetchone()
        return bool(row and row['cnt'])
    except Exception:
        return False


def _admin_expr(tiene_admin_col):
    """Expresión SQL para porcentaje_admin según BD."""
    if tiene_admin_col:
        return 'COALESCE(mpr.porcentaje_admin, 25.00)'
    return '25.00'


def _tp_location_cond():
    """Filtro manual por tp.location_id para queries con subqueries internas."""
    active_id = session.get('active_location_id')
    can_view_all = session.get('can_view_all_locations', False)
    if can_view_all and active_id is None:
        return '', []
    if active_id:
        return 'AND tp.location_id = %s', [active_id]
    return '', []


def _current_closure_scope():
    active_id = session.get('active_location_id')
    active_name = session.get('active_location_name')
    can_view_all = session.get('can_view_all_locations', False)

    if active_id:
        return active_id, active_name or 'Local actual'
    if can_view_all:
        return None, 'Todos los locales'
    return None, session.get('user_local', 'Local actual')


def _find_existing_cierre(cursor, local_id, fecha_inicio, fecha_fin):
    if not _table_exists(cursor, 'cierre_liquidacion'):
        return None

    cursor.execute(
        """
        SELECT
            cl.id,
            cl.local_id,
            COALESCE(loc.name, 'Todos los locales') AS local_nombre,
            cl.fecha_inicio,
            cl.fecha_fin,
            cl.creado_el
        FROM cierre_liquidacion cl
        LEFT JOIN location loc ON loc.id = cl.local_id
        WHERE cl.fecha_inicio = %s
          AND cl.fecha_fin = %s
          AND IFNULL(cl.local_id, 0) = IFNULL(%s, 0)
        ORDER BY cl.creado_el DESC
        LIMIT 1
        """,
        (fecha_inicio, fecha_fin, local_id),
    )
    existing = cursor.fetchone()
    if not existing:
        return None

    return {
        'id': int(existing['id']),
        'local_id': existing['local_id'],
        'local_nombre': existing['local_nombre'],
        'fecha_inicio': str(existing['fecha_inicio']),
        'fecha_fin': str(existing['fecha_fin']),
        'creado_el': str(existing['creado_el']),
    }


# ─── Helpers de consulta ──────────────────────────────────────────────────────

def _fetch_package_summary(cursor, fecha_inicio, fecha_fin):
    """Resumen de paquetes vendidos en el período, con turnos usados y restantes."""
    sql, params = apply_location_filter(
        """
        SELECT
            tp.id AS paquete_id,
            tp.name AS paquete_nombre,
            tp.turns AS turnos_por_paquete,
            tp.price AS precio_unitario,
            COUNT(DISTINCT qh.qr_code) AS paquetes_vendidos,
            COUNT(DISTINCT qh.qr_code) * COALESCE(MAX(tp.price), 0) AS ingresos_totales
        FROM qrhistory qh
        JOIN qrcode qr ON qr.code = qh.qr_code
        JOIN turnpackage tp ON qr.turnPackageId = tp.id
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
          AND qh.es_venta_real = TRUE
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
        GROUP BY tp.id, tp.name, tp.turns, tp.price
        ORDER BY paquetes_vendidos DESC
        """,
        [fecha_inicio, fecha_fin],
        column='location_id', table_alias='tp',
    )
    cursor.execute(sql, params)
    rows = cursor.fetchall()

    # Turnos realmente jugados por tipo de paquete:
    # se cuentan los registros de turnusage de los QR vendidos en el período.
    cursor.execute(
        """
        SELECT qr.turnPackageId AS paquete_id, COUNT(tu.id) AS turnos_jugados
        FROM turnusage tu
        JOIN qrcode qr ON tu.qrCodeId = qr.id
        WHERE qr.code IN (
            SELECT qh2.qr_code FROM qrhistory qh2
            WHERE DATE(qh2.fecha_hora) BETWEEN %s AND %s
              AND qh2.es_venta_real = TRUE
        )
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
        GROUP BY qr.turnPackageId
        """,
        (fecha_inicio, fecha_fin),
    )
    plays_by_pkg = {r['paquete_id']: int(r['turnos_jugados']) for r in cursor.fetchall()}

    paquetes = []
    for row in rows:
        turnos_por_paquete = int(row['turnos_por_paquete'] or 0)
        paquetes_vendidos  = int(row['paquetes_vendidos'] or 0)
        turnos_totales     = paquetes_vendidos * turnos_por_paquete
        turnos_usados      = plays_by_pkg.get(int(row['paquete_id']), 0)
        turnos_restantes   = max(turnos_totales - turnos_usados, 0)
        ingresos           = _to_float(row['ingresos_totales'])
        precio_turno       = ingresos / turnos_totales if turnos_totales > 0 else 0.0

        paquetes.append({
            'paquete_id':           row['paquete_id'],
            'paquete_nombre':       row['paquete_nombre'],
            'precio_unitario':      _to_float(row['precio_unitario']),
            'paquetes_vendidos':    paquetes_vendidos,
            'turnos_por_paquete':   turnos_por_paquete,
            'turnos_totales':       turnos_totales,
            'turnos_usados':        turnos_usados,
            'turnos_restantes':     turnos_restantes,
            'ingresos_totales':     ingresos,
            'valor_turnos_restantes': round(turnos_restantes * precio_turno, 2),
        })

    return paquetes


def _fetch_turnos_summary(cursor, fecha_inicio, fecha_fin, paquetes):
    """Totales consolidados de turnos del período."""
    turnos_equiv    = sum(p['turnos_totales']   for p in paquetes)
    turnos_usados   = sum(p['turnos_usados']    for p in paquetes)
    turnos_rest     = sum(p['turnos_restantes'] for p in paquetes)
    valor_restantes = sum(p['valor_turnos_restantes'] for p in paquetes)
    return {
        'equivalentes':   turnos_equiv,
        'usados':         turnos_usados,
        'restantes':      turnos_rest,
        'valor_restantes': round(valor_restantes, 2),
    }


def _fetch_top3_maquinas(cursor, fecha_inicio, fecha_fin):
    """Top 3 máquinas por turnos jugados en el período."""
    sql, params = apply_location_filter(
        """
        SELECT m.name AS maquina_nombre, COUNT(tu.id) AS turnos_jugados
        FROM turnusage tu
        JOIN machine m ON tu.machineId = m.id
        JOIN qrcode  qr ON tu.qrCodeId = qr.id
        JOIN qrhistory qh ON qh.qr_code = qr.code
        JOIN turnpackage tp ON qr.turnPackageId = tp.id
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
          AND qh.es_venta_real = TRUE
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
        GROUP BY m.id, m.name
        ORDER BY turnos_jugados DESC
        LIMIT 3
        """,
        [fecha_inicio, fecha_fin],
        column='location_id', table_alias='tp',
    )
    cursor.execute(sql, params)
    return [
        {'nombre': r['maquina_nombre'], 'turnos_jugados': int(r['turnos_jugados'])}
        for r in cursor.fetchall()
    ]


def _fetch_usage_summary(cursor, fecha_inicio, fecha_fin, tiene_admin_col=False):
    """Resumen de uso por máquina basado en turnusage."""
    admin_col = _admin_expr(tiene_admin_col)
    cursor.execute(
        f"""
        SELECT
            m.id AS maquina_id,
            m.name AS maquina_nombre,
            m.type AS tipo_maquina,
            COALESCE(mpr.porcentaje_restaurante, %s) AS porcentaje_restaurante,
            {admin_col} AS porcentaje_admin,
            COUNT(tu.id) AS turnos_usados,
            COUNT(DISTINCT qr.id) AS qrs_utilizados,
            COALESCE(SUM(
                CASE WHEN tp.price IS NOT NULL AND COALESCE(tp.turns, 0) > 0
                     THEN tp.price / tp.turns
                     ELSE COALESCE(m.valor_por_turno, 3000.00)
                END
            ), 0) AS ingresos_estimados
        FROM turnusage tu
        JOIN machine m ON tu.machineId = m.id
        JOIN qrcode  qr ON tu.qrCodeId = qr.id
        LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
        LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
        WHERE DATE(tu.usedAt) BETWEEN %s AND %s
        GROUP BY m.id, m.name, m.type, mpr.porcentaje_restaurante, {admin_col}
        ORDER BY ingresos_estimados DESC
        """,
        (RESTAURANT_PERCENTAGE_DEFAULT, fecha_inicio, fecha_fin),
    )

    resumen = {}
    for row in cursor.fetchall():
        resumen[row['maquina_id']] = {
            'maquina_id':           row['maquina_id'],
            'maquina_nombre':       row['maquina_nombre'],
            'tipo_maquina':         row['tipo_maquina'],
            'porcentaje_restaurante': _to_float(row['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT),
            'porcentaje_admin':     _to_float(row['porcentaje_admin'], ADMIN_PERCENTAGE_DEFAULT),
            'turnos_usados':        int(row['turnos_usados'] or 0),
            'qrs_utilizados':       int(row['qrs_utilizados'] or 0),
            'ingresos_estimados':   _to_float(row['ingresos_estimados']),
        }

    # Paquetes usados por máquina
    cursor.execute(
        """
        SELECT
            tu.machineId AS maquina_id,
            tp.id AS paquete_id,
            tp.name AS paquete_nombre,
            COALESCE(tp.turns, 0) AS turnos_por_paquete,
            COUNT(tu.id) AS turnos_usados,
            COUNT(DISTINCT qr.id) AS qrs_utilizados,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) AS ingresos_estimados
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
        paquetes_por_maquina.setdefault(row['maquina_id'], []).append({
            'paquete_id':         row['paquete_id'],
            'paquete_nombre':     row['paquete_nombre'],
            'turnos_por_paquete': int(row['turnos_por_paquete'] or 0),
            'turnos_usados':      int(row['turnos_usados'] or 0),
            'qrs_utilizados':     int(row['qrs_utilizados'] or 0),
            'ingresos_estimados': _to_float(row['ingresos_estimados']),
        })

    return resumen, paquetes_por_maquina


def _fetch_machine_liquidations(cursor, fecha_inicio, fecha_fin, limit=None):
    if not _table_exists(cursor, 'liquidaciones'):
        return []

    query = """
        SELECT
            l.id, l.fecha, l.maquina_id, l.turnos_retirados, l.valor_por_turno,
            l.costos_operativos, l.porcentaje_restaurante, l.observaciones,
            l.usuario_id, l.creado_el,
            m.name AS maquina_nombre, m.type AS tipo_maquina
        FROM liquidaciones l
        JOIN machine m ON l.maquina_id = m.id
        WHERE l.fecha BETWEEN %s AND %s
        ORDER BY l.fecha DESC, l.creado_el DESC
    """
    params = [fecha_inicio, fecha_fin]
    if limit:
        query += ' LIMIT %s'
        params.append(limit)

    cursor.execute(query, tuple(params))
    rows = []
    for row in cursor.fetchall():
        turnos          = int(row['turnos_retirados'] or 0)
        valor_turno     = _to_float(row['valor_por_turno'])
        costos          = _to_float(row['costos_operativos'])
        pct_rest        = _to_float(row['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT)
        ingreso_bruto   = turnos * valor_turno
        negocio         = ingreso_bruto * pct_rest / 100
        administracion  = ingreso_bruto * ADMIN_PERCENTAGE_DEFAULT / 100
        utilidad_op     = ingreso_bruto - negocio - administracion - costos

        rows.append({
            'id':                    row['id'],
            'fecha':                 row['fecha'].isoformat() if row['fecha'] else None,
            'maquina_id':            row['maquina_id'],
            'maquina_nombre':        row['maquina_nombre'],
            'tipo_maquina':          row['tipo_maquina'],
            'turnos_retirados':      turnos,
            'valor_por_turno':       valor_turno,
            'costos_operativos':     costos,
            'porcentaje_restaurante': pct_rest,
            'ingreso_bruto':         round(ingreso_bruto, 2),
            'negocio':               round(negocio, 2),
            'administracion':        round(administracion, 2),
            'utilidad_operativa':    round(utilidad_op, 2),
            'observaciones':         row['observaciones'],
            'usuario_id':            row['usuario_id'],
            'creado_el':             row['creado_el'].isoformat() if row['creado_el'] else None,
        })

    return rows


def _group_liquidations_by_machine(rows):
    grouped = {}
    for row in rows:
        machine = grouped.setdefault(row['maquina_id'], {
            'maquina_id':               row['maquina_id'],
            'maquina_nombre':           row['maquina_nombre'],
            'tipo_maquina':             row['tipo_maquina'],
            'liquidaciones_registradas': 0,
            'turnos_retirados':         0,
            'ingreso_manual_total':     0.0,
            'costos_operativos':        0.0,
            'utilidad_operativa':       0.0,
        })
        machine['liquidaciones_registradas'] += 1
        machine['turnos_retirados']          += row['turnos_retirados']
        machine['ingreso_manual_total']      += row['ingreso_bruto']
        machine['costos_operativos']         += row['costos_operativos']
        machine['utilidad_operativa']        += row['utilidad_operativa']
    return grouped


def _build_machine_summary(cursor, fecha_inicio, fecha_fin, tiene_admin_col=False):
    fecha_ini_prev, fecha_fin_prev = _get_previous_period(fecha_inicio, fecha_fin)
    resumen_actual, paq_actuales  = _fetch_usage_summary(cursor, fecha_inicio, fecha_fin, tiene_admin_col)
    resumen_previo, _             = _fetch_usage_summary(cursor, fecha_ini_prev, fecha_fin_prev, tiene_admin_col)

    liq_actuales = _fetch_machine_liquidations(cursor, fecha_inicio, fecha_fin)
    liq_previas  = _fetch_machine_liquidations(cursor, fecha_ini_prev, fecha_fin_prev)
    manual_act   = _group_liquidations_by_machine(liq_actuales)
    manual_prev  = _group_liquidations_by_machine(liq_previas)

    machine_ids = set(resumen_actual) | set(manual_act)
    resumen_maquinas = {}

    for mid in machine_ids:
        actual     = resumen_actual.get(mid, {})
        previo     = resumen_previo.get(mid, {})
        manual_d   = manual_act.get(mid, {})
        manual_p   = manual_prev.get(mid, {})

        nombre     = actual.get('maquina_nombre') or manual_d.get('maquina_nombre') or f'Máquina #{mid}'
        tipo       = actual.get('tipo_maquina')   or manual_d.get('tipo_maquina')   or 'general'
        pct_rest   = actual.get('porcentaje_restaurante', RESTAURANT_PERCENTAGE_DEFAULT)
        pct_adm    = actual.get('porcentaje_admin', ADMIN_PERCENTAGE_DEFAULT)
        ingreso_b  = manual_d.get('ingreso_manual_total') or actual.get('ingresos_estimados', 0.0)
        ingreso_p  = manual_p.get('ingreso_manual_total') or previo.get('ingresos_estimados', 0.0)
        ing_rest   = ingreso_b * pct_rest / 100
        ing_adm    = ingreso_b * pct_adm  / 100
        ing_util   = ingreso_b - ing_rest - ing_adm

        resumen_maquinas[nombre] = {
            'maquina_id':              mid,
            'maquina_nombre':          nombre,
            'tipo_maquina':            tipo,
            'ventas_realizadas':       int(actual.get('qrs_utilizados', 0)),
            'paquetes_vendidos':       int(actual.get('qrs_utilizados', 0)),
            'turnos_usados':           int(actual.get('turnos_usados', 0)),
            'turnos_retirados':        int(manual_d.get('turnos_retirados', 0)),
            'ingresos_estimados':      round(_to_float(actual.get('ingresos_estimados')), 2),
            'ingresos_totales':        round(_to_float(ingreso_b), 2),
            'porcentaje_restaurante':  round(_to_float(pct_rest), 2),
            'porcentaje_admin':        round(_to_float(pct_adm), 2),
            'porcentaje_utilidad':     round(100 - _to_float(pct_rest) - _to_float(pct_adm), 2),
            'ingresos_restaurante':    round(_to_float(ing_rest), 2),
            'ingresos_admin':          round(_to_float(ing_adm), 2),
            'ingresos_utilidad':       round(_to_float(ing_util), 2),
            'ingresos_proveedor':      round(_to_float(ing_util), 2),
            'costos_operativos':       round(_to_float(manual_d.get('costos_operativos')), 2),
            'utilidad_operativa':      round(_to_float(manual_d.get('utilidad_operativa', ing_util)), 2),
            'liquidaciones_registradas': int(manual_d.get('liquidaciones_registradas', 0)),
            'fuente_ingresos':         'manual' if manual_d.get('ingreso_manual_total') else 'estimada_por_uso',
            'rendimiento_porcentual':  _pct_change(ingreso_b, ingreso_p),
            'paquetes':                paq_actuales.get(mid, []),
        }

    return resumen_maquinas, liq_actuales, liq_previas


def _build_propietarios_distribution(cursor, fecha_inicio, fecha_fin):
    if not (_table_exists(cursor, 'maquinapropietario') and _table_exists(cursor, 'propietarios')):
        return {}

    cursor.execute(
        """
        SELECT
            p.id AS propietario_id,
            p.nombre AS propietario_nombre,
            m.id AS maquina_id,
            m.name AS maquina_nombre,
            mp.porcentaje_propiedad,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) AS ingresos_estimados,
            COUNT(tu.id) AS turnos_usados
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
        prop = distribucion.setdefault(row['propietario_nombre'], {
            'propietario_id': row['propietario_id'],
            'total_ingresos': 0.0,
            'ventas_asociadas': 0,
            'detalles_maquinas': [],
        })
        ingreso_prop = _to_float(row['ingresos_estimados']) * _to_float(row['porcentaje_propiedad']) / 100
        prop['total_ingresos'] += ingreso_prop
        prop['ventas_asociadas'] += int(row['turnos_usados'] or 0)
        prop['detalles_maquinas'].append({
            'maquina_id':          row['maquina_id'],
            'maquina_nombre':      row['maquina_nombre'],
            'porcentaje_propiedad': _to_float(row['porcentaje_propiedad']),
            'monto_propietario':   round(ingreso_prop, 2),
        })

    for prop in distribucion.values():
        prop['total_ingresos'] = round(prop['total_ingresos'], 2)

    return distribucion


def _build_investor_liquidation(cursor, resumen_maquinas, fecha_inicio, fecha_fin):
    """
    Liquidación por inversionista usando maquinapropietario + propietarios.
    Calcula participación sobre la utilidad (100 - pct_negocio - pct_admin) de cada máquina.
    """
    if not (_table_exists(cursor, 'maquinapropietario') and _table_exists(cursor, 'propietarios')):
        return []

    tiene_admin = _has_admin_col(cursor)
    admin_expr  = _admin_expr(tiene_admin)

    sql, params = apply_location_filter(
        f"""
        SELECT
            p.id AS propietario_id,
            p.nombre AS propietario_nombre,
            m.id AS maquina_id,
            m.name AS maquina_nombre,
            mp.porcentaje_propiedad,
            tp.id AS paquete_id,
            tp.name AS paquete_nombre,
            tp.price AS precio_unitario,
            tp.turns AS turnos_paquete,
            COUNT(tu.id) AS turnos_jugados,
            COUNT(tu.id) * COALESCE(tp.price / NULLIF(tp.turns, 0), 0) AS total_paquete,
            COALESCE(mpr.porcentaje_restaurante, 35.00) AS pct_negocio,
            {admin_expr} AS pct_admin
        FROM qrhistory qh
        JOIN qrcode      qr ON qr.code          = qh.qr_code
        JOIN turnpackage  tp ON qr.turnPackageId = tp.id
        JOIN turnusage    tu ON tu.qrCodeId      = qr.id
        JOIN machine       m ON tu.machineId     = m.id
        JOIN maquinapropietario mp ON mp.maquina_id   = m.id
        JOIN propietarios        p ON p.id             = mp.propietario_id
        LEFT JOIN maquinaporcentajerestaurante mpr ON mpr.maquina_id = m.id
        WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
          AND qh.es_venta_real = TRUE
        GROUP BY p.id, p.nombre, m.id, m.name, mp.porcentaje_propiedad,
                 tp.id, tp.name, tp.price, tp.turns,
                 mpr.porcentaje_restaurante, {admin_expr}
        ORDER BY p.nombre, m.name, turnos_jugados DESC
        """,
        [fecha_inicio, fecha_fin],
        column='location_id', table_alias='tp',
    )
    cursor.execute(sql, params)

    inv_map = {}
    for r in cursor.fetchall():
        pid = r['propietario_id']
        mid = r['maquina_id']
        if pid not in inv_map:
            inv_map[pid] = {'nombre': r['propietario_nombre'], 'maquinas': {}}
        if mid not in inv_map[pid]['maquinas']:
            inv_map[pid]['maquinas'][mid] = {
                'nombre':               r['maquina_nombre'],
                'porcentaje_propiedad': _to_float(r['porcentaje_propiedad']),
                'pct_negocio':          _to_float(r['pct_negocio']),
                'pct_admin':            _to_float(r['pct_admin']),
                'paquetes':             [],
                'ingresos_maquina':     0.0,
            }
        maq = inv_map[pid]['maquinas'][mid]
        total_p = _to_float(r['total_paquete'])
        turnos_pkg = int(r['turnos_paquete'] or 1)
        precio_unit = _to_float(r['precio_unitario'])
        maq['paquetes'].append({
            'paquete_id':        r['paquete_id'],
            'paquete_nombre':    r['paquete_nombre'],
            'precio_unitario':   precio_unit,
            'turnos_paquete':    turnos_pkg,
            'precio_por_turno':  round(precio_unit / turnos_pkg, 2),
            'turnos_jugados':    int(r['turnos_jugados'] or 0),
            'total':             total_p,
        })
        maq['ingresos_maquina'] += total_p

    resultado = []
    for pid, inv in inv_map.items():
        total_part   = 0.0
        maquinas_list = []
        for mid, maq in inv['maquinas'].items():
            pct_util     = 100 - maq['pct_negocio'] - maq['pct_admin']
            utilidad_maq = maq['ingresos_maquina'] * pct_util / 100
            participacion = utilidad_maq * maq['porcentaje_propiedad'] / 100
            total_part   += participacion

            maquina_resumen = next(
                (item for item in resumen_maquinas.values() if item['maquina_id'] == mid),
                {},
            )
            maquinas_list.append({
                'maquina_id':           mid,
                'nombre':               maq['nombre'],
                'porcentaje_propiedad': maq['porcentaje_propiedad'],
                'pct_negocio':          maq['pct_negocio'],
                'pct_admin':            maq['pct_admin'],
                'pct_utilidad':         round(pct_util, 2),
                'ingresos_maquina':     round(maq['ingresos_maquina'], 2),
                'utilidad_maquina':     round(utilidad_maq, 2),
                'ingreso_participacion': round(participacion, 2),
                'turnos_usados':        maquina_resumen.get('turnos_usados', 0),
                'rendimiento_porcentual': maquina_resumen.get('rendimiento_porcentual', 0),
                'fuente_ingresos':      maquina_resumen.get('fuente_ingresos', 'estimada_por_uso'),
                'paquetes':             maq['paquetes'],
            })

        resultado.append({
            'propietario_id':      pid,
            'nombre':              inv['nombre'],
            'maquinas':            maquinas_list,
            'maquinas_poseidas':   len(maquinas_list),
            'total_participacion': round(total_part, 2),
        })

    resultado.sort(key=lambda x: x['total_participacion'], reverse=True)
    return resultado


def _build_period_comparison(cursor, fecha_inicio, fecha_fin):
    fecha_ini_prev, fecha_fin_prev = _get_previous_period(fecha_inicio, fecha_fin)

    def _period_totals(start, end):
        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT qh.qr_code) AS total_ventas,
                COALESCE(SUM(tp.price), 0) AS total_ingresos
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qh.es_venta_real = TRUE
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
            """,
            (start, end),
        )
        row = cursor.fetchone() or {}
        return {
            'ventas':   int(row.get('total_ventas') or 0),
            'ingresos': _to_float(row.get('total_ingresos')),
        }

    actual = _period_totals(fecha_inicio, fecha_fin)
    previo = _period_totals(fecha_ini_prev, fecha_fin_prev)

    return {
        'periodo_actual': {
            'fecha_inicio': fecha_inicio.isoformat(),
            'fecha_fin':    fecha_fin.isoformat(),
            **actual,
        },
        'periodo_previo': {
            'fecha_inicio': fecha_ini_prev.isoformat(),
            'fecha_fin':    fecha_fin_prev.isoformat(),
            **previo,
        },
        'variacion_ingresos_pct': _pct_change(actual['ingresos'], previo['ingresos']),
        'variacion_ventas_pct':   _pct_change(actual['ventas'],   previo['ventas']),
    }


def _fetch_historial_cierres(cursor, limite=5):
    if not _table_exists(cursor, 'cierre_liquidacion'):
        return []
    active_id, _, can_view_all = (
        session.get('active_location_id'),
        session.get('active_location_name'),
        session.get('can_view_all_locations', False),
    )

    if can_view_all and active_id is None:
        cursor.execute(
            """
            SELECT cl.id, cl.local_id, COALESCE(loc.name, 'Todos los locales') AS local_nombre,
                   cl.fecha_inicio, cl.fecha_fin, cl.total_ingresos, cl.total_negocio,
                   cl.total_admin, cl.total_utilidad, cl.pct_negocio, cl.pct_admin, cl.creado_el
            FROM cierre_liquidacion cl
            LEFT JOIN location loc ON loc.id = cl.local_id
            ORDER BY cl.creado_el DESC
            LIMIT %s
            """,
            (limite,),
        )
    else:
        cursor.execute(
            """
            SELECT cl.id, cl.local_id, COALESCE(loc.name, 'Todos los locales') AS local_nombre,
                   cl.fecha_inicio, cl.fecha_fin, cl.total_ingresos, cl.total_negocio,
                   cl.total_admin, cl.total_utilidad, cl.pct_negocio, cl.pct_admin, cl.creado_el
            FROM cierre_liquidacion cl
            LEFT JOIN location loc ON loc.id = cl.local_id
            WHERE IFNULL(cl.local_id, 0) = IFNULL(%s, 0)
            ORDER BY cl.creado_el DESC
            LIMIT %s
            """,
            (active_id, limite),
        )
    return [
        {
            'id':             r['id'],
            'local_id':       r.get('local_id'),
            'local_nombre':   r.get('local_nombre') or 'Todos los locales',
            'fecha_inicio':   str(r['fecha_inicio']),
            'fecha_fin':      str(r['fecha_fin']),
            'total_ingresos': float(r['total_ingresos']),
            'total_negocio':  float(r['total_negocio']),
            'total_admin':    float(r['total_admin']),
            'total_utilidad': float(r['total_utilidad']),
            'pct_negocio':    float(r['pct_negocio']),
            'pct_admin':      float(r['pct_admin']),
            'creado_el':      str(r['creado_el']),
        }
        for r in cursor.fetchall()
    ]


def _fetch_gastos_periodo(cursor, fecha_inicio, fecha_fin):
    if not _table_exists(cursor, 'gastos_liquidacion'):
        return []
    try:
        cursor.execute(
            """SELECT id, concepto, monto
               FROM gastos_liquidacion
               ORDER BY id DESC
               LIMIT 100""",
        )
        return [
            {
                'id':       g['id'],
                'concepto': g['concepto'],
                'monto':    float(g['monto']),
            }
            for g in cursor.fetchall()
        ]
    except Exception as e:
        logger.warning(f"Error obteniendo gastos: {e}")
        return []


# ─── Calcular liquidación ──────────────────────────────────────────────────────

@liquidaciones_bp.route('/api/liquidaciones/calcular', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def calcular_liquidacion():
    """Calcular liquidación detallada: modelo 3-way negocio / admin / utilidad."""
    connection = None
    cursor = None
    try:
        data         = request.get_json() or {}
        fecha_inicio = _parse_date(data.get('fecha_inicio'), get_colombia_time().date())
        fecha_fin    = _parse_date(data.get('fecha_fin'),    get_colombia_time().date())
        if fecha_inicio > fecha_fin:
            fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        local_id, local_nombre = _current_closure_scope()
        cierre_existente = _find_existing_cierre(cursor, local_id, fecha_inicio, fecha_fin)

        tiene_porcentaje       = _table_exists(cursor, 'maquinaporcentajerestaurante')
        tiene_propietarios     = _table_exists(cursor, 'maquinapropietario')
        tiene_tabla_propietarios = _table_exists(cursor, 'propietarios')
        tiene_admin            = _has_admin_col(cursor) if tiene_porcentaje else False

        # Resumen de paquetes vendidos (fuente canónica de ingresos — sin JOIN a turnusage)
        paquetes_resumen = _fetch_package_summary(cursor, fecha_inicio, fecha_fin)
        turnos_summary   = _fetch_turnos_summary(cursor, fecha_inicio, fecha_fin, paquetes_resumen)
        # total_ingresos derivado de paquetes para evitar multiplicación por filas de turnusage
        total_ingresos = sum(p['ingresos_totales'] for p in paquetes_resumen)
        top3_maquinas    = _fetch_top3_maquinas(cursor, fecha_inicio, fecha_fin)

        # Resumen por máquina, comparativos, propietarios
        resumen_maquinas, liquidaciones_maquinas, _ = _build_machine_summary(
            cursor, fecha_inicio, fecha_fin, tiene_admin
        )
        distribucion_propietarios = (
            _build_propietarios_distribution(cursor, fecha_inicio, fecha_fin)
            if tiene_propietarios and tiene_tabla_propietarios
            else {}
        )
        inversionistas = _build_investor_liquidation(cursor, resumen_maquinas, fecha_inicio, fecha_fin)
        comparativos   = _build_period_comparison(cursor, fecha_inicio, fecha_fin)

        # Porcentajes globales promedio para el resumen
        if resumen_maquinas:
            total_negocio_v  = sum(_to_float(m['ingresos_restaurante']) for m in resumen_maquinas.values())
            total_admin_v    = sum(_to_float(m['ingresos_admin'])       for m in resumen_maquinas.values())
            total_utilidad_v = sum(_to_float(m['ingresos_utilidad'])    for m in resumen_maquinas.values())
        else:
            total_negocio_v  = total_ingresos * RESTAURANT_PERCENTAGE_DEFAULT / 100
            total_admin_v    = total_ingresos * ADMIN_PERCENTAGE_DEFAULT / 100
            total_utilidad_v = total_ingresos - total_negocio_v - total_admin_v

        pct_negocio  = RESTAURANT_PERCENTAGE_DEFAULT
        pct_admin    = ADMIN_PERCENTAGE_DEFAULT
        if tiene_porcentaje:
            admin_col = 'COALESCE(porcentaje_admin, 25.00)' if tiene_admin else '25.00'
            cursor.execute(
                f"""SELECT AVG(COALESCE(porcentaje_restaurante, 35.00)) AS avg_neg,
                           AVG({admin_col}) AS avg_adm
                    FROM maquinaporcentajerestaurante"""
            )
            row_pct = cursor.fetchone()
            if row_pct and row_pct['avg_neg']:
                pct_negocio = float(row_pct['avg_neg'])
                pct_admin   = float(row_pct['avg_adm'])

        distribucion = {
            'total_ingresos': total_ingresos,
            'negocio':  {'pct': round(pct_negocio, 2),           'monto': round(total_negocio_v, 2)},
            'admin':    {'pct': round(pct_admin, 2),             'monto': round(total_admin_v, 2)},
            'utilidad': {'pct': round(100 - pct_negocio - pct_admin, 2), 'monto': round(total_utilidad_v, 2)},
        }

        # Tabla detallada
        if tiene_porcentaje and tiene_propietarios and tiene_tabla_propietarios:
            loc_cond, loc_params = _tp_location_cond()
            datos_sql = f"""
                SELECT
                    DATE(qh.fecha_hora) AS fecha,
                    qh.user_name AS vendedor,
                    qh.qr_code,
                    tp.name AS paquete_nombre,
                    tp.turns AS turnos_usados,
                    COALESCE(m.name, 'No especificada') AS maquina_nombre,
                    tp.price AS ingresos_totales,
                    COALESCE(mpr.porcentaje_restaurante, %s) AS porcentaje_restaurante,
                    (tp.price * COALESCE(mpr.porcentaje_restaurante, %s) / 100) AS ingresos_restaurante,
                    (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, %s)) / 100) AS ingresos_proveedor,
                    COALESCE(p.nombre, 'No asignado') AS propietario
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                LEFT JOIN (SELECT qrCodeId, MIN(machineId) AS machineId FROM turnusage GROUP BY qrCodeId) tu ON qr.id = tu.qrCodeId
                LEFT JOIN machine m ON tu.machineId = m.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN maquinapropietario mp ON m.id = mp.maquina_id
                LEFT JOIN propietarios p ON mp.propietario_id = p.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                  {loc_cond}
                ORDER BY qh.fecha_hora DESC
                """
            datos_params = [RESTAURANT_PERCENTAGE_DEFAULT] * 3 + [fecha_inicio, fecha_fin] + loc_params
        else:
            datos_sql, datos_params = apply_location_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) AS fecha,
                    qh.user_name AS vendedor,
                    qh.qr_code,
                    tp.name AS paquete_nombre,
                    tp.turns AS turnos_usados,
                    'No especificada' AS maquina_nombre,
                    tp.price AS ingresos_totales,
                    %s AS porcentaje_restaurante,
                    (tp.price * %s / 100) AS ingresos_restaurante,
                    (tp.price * (100 - %s) / 100) AS ingresos_proveedor,
                    'No asignado' AS propietario
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                """,
                [RESTAURANT_PERCENTAGE_DEFAULT] * 3 + [fecha_inicio, fecha_fin],
                column='location_id', table_alias='tp',
            )
        cursor.execute(datos_sql, datos_params)
        datos_tabla = [
            {
                'fecha':                  str(row['fecha'])[:10] if row.get('fecha') else None,
                'vendedor':               str(row.get('vendedor') or ''),
                'paquete_nombre':         str(row.get('paquete_nombre') or ''),
                'ingresos_totales':       float(row.get('ingresos_totales') or 0),
                'porcentaje_restaurante': float(row.get('porcentaje_restaurante') or 0),
                'ingresos_restaurante':   float(row.get('ingresos_restaurante') or 0),
            }
            for row in cursor.fetchall()
        ]

        historial = _fetch_historial_cierres(cursor, limite=5)
        gastos    = _fetch_gastos_periodo(cursor, fecha_inicio, fecha_fin)

        return jsonify({
            'success': True,
            'configuracion': {
                'porcentaje_negocio_default':      RESTAURANT_PERCENTAGE_DEFAULT,
                'porcentaje_administracion_default': ADMIN_PERCENTAGE_DEFAULT,
                'usa_porcentajes_personalizados':  tiene_porcentaje,
                'tiene_columna_admin':             tiene_admin,
            },
            'periodo': {
                'fecha_inicio':      fecha_inicio.isoformat(),
                'fecha_fin':         fecha_fin.isoformat(),
                'local_id':          local_id,
                'local_nombre':      local_nombre,
                'total_ventas':      sum(p['paquetes_vendidos'] for p in paquetes_resumen),
                'total_ingresos':    round(total_ingresos, 2),
                'maquinas_utilizadas': len(resumen_maquinas),
            },
            'distribucion':           distribucion,
            'comparativos':           comparativos,
            'paquetes_resumen':       paquetes_resumen,
            'paquete_mas_vendido':    paquetes_resumen[0] if paquetes_resumen else None,
            'top3_maquinas':          top3_maquinas,
            'turnos':                 turnos_summary,
            'distribucion_propietarios': distribucion_propietarios,
            'resumen_maquinas':       resumen_maquinas,
            'inversionistas':         inversionistas,
            'datos_tabla':            datos_tabla,
            'historial':              historial,
            'cierre_existente':       cierre_existente,
            'gastos':                 gastos,
            'ultimas_liquidaciones_maquinas': liquidaciones_maquinas[:5],
            'totales': {
                'ingresos_totales':   round(total_ingresos, 2),
                'ganancia_negocio':   round(total_negocio_v, 2),
                'ganancia_admin':     round(total_admin_v, 2),
                'ganancia_utilidad':  round(total_utilidad_v, 2),
                'ganancia_restaurante': round(total_negocio_v, 2),
                'ganancia_proveedores': round(total_utilidad_v, 2),
            },
        })

    except Exception as e:
        logger.error(f"Error calculando liquidación: {e}", exc_info=True)
        logger.error(traceback.format_exc())
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Ventas liquidadas (listado paginado) ─────────────────────────────────────

@liquidaciones_bp.route('/api/ventas-liquidadas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_ventas_liquidadas():
    """Listado paginado de transacciones con distribución 3-way."""
    connection = None
    cursor = None
    try:
        logger.info('=== INICIANDO OBTENER VENTAS LIQUIDADAS ===')
        fecha_inicio, fecha_fin = _get_period()
        pagina    = int(request.args.get('pagina',    1))
        por_pagina = int(request.args.get('porPagina', 50))
        offset    = (pagina - 1) * por_pagina

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        tiene_porcentaje  = _table_exists(cursor, 'maquinaporcentajerestaurante')
        tiene_propietarios = (
            _table_exists(cursor, 'maquinapropietario') and _table_exists(cursor, 'propietarios')
        )
        tiene_admin = _has_admin_col(cursor) if tiene_porcentaje else False
        admin_expr  = _admin_expr(tiene_admin)

        count_sql, count_params = apply_location_filter(
            """SELECT COUNT(*) AS total
               FROM qrhistory qh
               JOIN qrcode qr ON qr.code = qh.qr_code
               JOIN turnpackage tp ON qr.turnPackageId = tp.id
               WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                 AND qr.turnPackageId IS NOT NULL
                 AND qr.turnPackageId != 1
                 AND qh.es_venta_real = TRUE""",
            [fecha_inicio, fecha_fin],
            column='location_id', table_alias='tp',
        )
        cursor.execute(count_sql, count_params)
        total = (cursor.fetchone() or {}).get('total', 0)

        if total == 0:
            return jsonify({
                'datos': [], 'totalRegistros': 0,
                'totalIngresos': 0, 'gananciaTotal': 0,
                'gananciaNegocio': 0, 'gananciaAdmin': 0, 'gananciaUtilidad': 0,
                'gananciaProveedor': 0, 'gananciaRestaurante': 0,
                'paginaActual': pagina, 'totalPaginas': 1,
                'mensaje': 'No hay ventas registradas en el período seleccionado',
            })

        mpr_join = ('LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id'
                    if tiene_porcentaje else '')
        mp_join  = (('LEFT JOIN maquinapropietario mp ON m.id = mp.maquina_id '
                     'LEFT JOIN propietarios p ON mp.propietario_id = p.id')
                    if tiene_propietarios else '')

        loc_cond_v, loc_params_v = _tp_location_cond()
        query = f"""
            SELECT
                DATE(qh.fecha_hora) AS fecha,
                qh.qr_code,
                qh.user_name AS vendedor,
                tp.name AS paquete_nombre,
                tp.turns AS turnos_paquete,
                tp.price AS precio_unitario,
                COALESCE(m.name, 'Máquina no especificada') AS maquina_nombre,
                COALESCE(mpr.porcentaje_restaurante, {RESTAURANT_PERCENTAGE_DEFAULT}) AS pct_negocio,
                {admin_expr} AS pct_admin,
                (tp.price * COALESCE(mpr.porcentaje_restaurante, {RESTAURANT_PERCENTAGE_DEFAULT}) / 100) AS monto_negocio,
                (tp.price * {admin_expr} / 100) AS monto_admin,
                (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, {RESTAURANT_PERCENTAGE_DEFAULT}) - {admin_expr}) / 100) AS monto_utilidad,
                COALESCE(p.nombre, 'No asignado') AS propietario,
                COALESCE(mp.porcentaje_propiedad, 0) AS porcentaje_propiedad
            FROM qrhistory qh
            JOIN qrcode      qr ON qr.code          = qh.qr_code
            JOIN turnpackage  tp ON qr.turnPackageId = tp.id
            LEFT JOIN (SELECT qrCodeId, MIN(machineId) AS machineId FROM turnusage GROUP BY qrCodeId) tu ON qr.id = tu.qrCodeId
            LEFT JOIN machine    m ON tu.machineId   = m.id
            {mpr_join}
            {mp_join}
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
              {loc_cond_v}
            ORDER BY qh.fecha_hora DESC
            LIMIT %s OFFSET %s
            """
        params = [fecha_inicio, fecha_fin] + loc_params_v + [por_pagina, offset]
        cursor.execute(query, params)
        ventas = cursor.fetchall()

        total_ingresos  = sum(_to_float(v['precio_unitario']) for v in ventas)
        total_negocio   = sum(_to_float(v['monto_negocio'])   for v in ventas)
        total_admin_v   = sum(_to_float(v['monto_admin'])     for v in ventas)
        total_utilidad  = sum(_to_float(v['monto_utilidad'])  for v in ventas)

        return jsonify({
            'datos':            ventas,
            'totalRegistros':   total,
            'totalIngresos':    round(total_ingresos, 2),
            'gananciaTotal':    round(total_ingresos, 2),
            'gananciaNegocio':  round(total_negocio, 2),
            'gananciaAdmin':    round(total_admin_v, 2),
            'gananciaUtilidad': round(total_utilidad, 2),
            'gananciaRestaurante': round(total_negocio, 2),
            'gananciaProveedor':   round(total_utilidad, 2),
            'paginaActual':     pagina,
            'totalPaginas':     (total + por_pagina - 1) // por_pagina,
        })

    except Exception as e:
        logger.error(f'Error obteniendo ventas liquidadas: {e}', exc_info=True)
        logger.error(traceback.format_exc())
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Gastos ───────────────────────────────────────────────────────────────────

@liquidaciones_bp.route('/api/liquidaciones/gastos', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_gastos():
    """Listar gastos informativos del período."""
    connection = None
    cursor = None
    try:
        fecha_inicio, fecha_fin = _get_period()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        gastos = _fetch_gastos_periodo(cursor, fecha_inicio, fecha_fin)
        return jsonify({'gastos': gastos, 'total': sum(g['monto'] for g in gastos)})

    except Exception as e:
        logger.error(f'Error obteniendo gastos: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@liquidaciones_bp.route('/api/liquidaciones/gastos', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def registrar_gasto():
    """Registrar un gasto informativo."""
    connection = None
    cursor = None
    try:
        data      = request.get_json() or {}
        concepto  = (data.get('concepto') or '').strip()
        monto     = _to_float(data.get('monto'))

        if not concepto or monto <= 0:
            return api_response('E002', http_status=400)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute(
            """INSERT INTO gastos_liquidacion (concepto, monto, usuario_id)
               VALUES (%s, %s, %s)""",
            (concepto, monto, session.get('user_id')),
        )
        connection.commit()
        return jsonify({'success': True, 'id': cursor.lastrowid})

    except Exception as e:
        logger.error(f'Error registrando gasto: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@liquidaciones_bp.route('/api/liquidaciones/gastos/<int:gasto_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_gasto(gasto_id):
    """Eliminar un gasto informativo."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute('DELETE FROM gastos_liquidacion WHERE id = %s', (gasto_id,))
        connection.commit()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error eliminando gasto: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Cerrar liquidación ───────────────────────────────────────────────────────

@liquidaciones_bp.route('/api/liquidaciones/cerrar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def cerrar_liquidacion():
    """Guardar cierre oficial de liquidación en cierre_liquidacion."""
    connection = None
    cursor = None
    try:
        data           = request.get_json() or {}
        fecha_inicio   = _parse_date(data.get('fecha_inicio'), get_colombia_time().date()).isoformat()
        fecha_fin      = _parse_date(data.get('fecha_fin'),    get_colombia_time().date()).isoformat()
        if fecha_inicio > fecha_fin:
            fecha_inicio, fecha_fin = fecha_fin, fecha_inicio
        total_ingresos = _to_float(data.get('total_ingresos'))
        total_negocio  = _to_float(data.get('total_negocio'))
        total_admin    = _to_float(data.get('total_admin'))
        total_utilidad = _to_float(data.get('total_utilidad'))
        pct_negocio    = _to_float(data.get('pct_negocio'),   RESTAURANT_PERCENTAGE_DEFAULT)
        pct_admin      = _to_float(data.get('pct_admin'),     ADMIN_PERCENTAGE_DEFAULT)
        observaciones  = (data.get('observaciones') or '').strip() or None
        local_id       = data.get('local_id')
        if local_id in ('', None):
            local_id = session.get('active_location_id')

        if not fecha_inicio or not fecha_fin:
            return api_response('E002', http_status=400)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        existing_close = _find_existing_cierre(cursor, local_id, fecha_inicio, fecha_fin)
        if existing_close:
            return jsonify({
                'success': False,
                'message': (
                    f"Este periodo ya fue cerrado para {existing_close['local_nombre']} "
                    f"({existing_close['fecha_inicio']} -> {existing_close['fecha_fin']})."
                ),
                'existing_close': existing_close,
            }), 409

        cursor.execute(
            """INSERT INTO cierre_liquidacion
               (local_id, fecha_inicio, fecha_fin, total_ingresos, total_negocio,
                total_admin, total_utilidad, pct_negocio, pct_admin, observaciones, usuario_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (local_id, fecha_inicio, fecha_fin, total_ingresos, total_negocio,
             total_admin, total_utilidad, pct_negocio, pct_admin,
             observaciones, session.get('user_id')),
        )
        connection.commit()
        cierre_id = cursor.lastrowid
        logger.info(f'Cierre liquidación registrado: id={cierre_id}, {fecha_inicio}→{fecha_fin}')
        return jsonify({'success': True, 'id': cierre_id})

    except Exception as e:
        if connection:
            connection.rollback()
        if 'Ya existe un cierre de liquidacion' in str(e):
            existing_close = None
            if cursor:
                try:
                    existing_close = _find_existing_cierre(cursor, local_id, fecha_inicio, fecha_fin)
                except Exception:
                    existing_close = None
            return jsonify({
                'success': False,
                'message': 'Este periodo ya tiene un cierre oficial registrado.',
                'existing_close': existing_close,
            }), 409
        logger.error(f'Error cerrando liquidación: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Historial ────────────────────────────────────────────────────────────────

@liquidaciones_bp.route('/api/liquidaciones/historial', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_historial():
    """Últimas N liquidaciones cerradas para gráfica e histórico."""
    connection = None
    cursor = None
    try:
        limite = min(int(request.args.get('limite', 10)), 50)
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        historial = _fetch_historial_cierres(cursor, limite)
        return jsonify({'historial': historial})

    except Exception as e:
        logger.error(f'Error obteniendo historial: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Liquidaciones por máquina (cierres manuales existentes) ──────────────────

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
        total_ingreso  = sum(item['ingreso_bruto']      for item in liquidaciones)
        total_costos   = sum(item['costos_operativos']  for item in liquidaciones)
        total_utilidad = sum(item['utilidad_operativa'] for item in liquidaciones)

        return jsonify({
            'datos':          liquidaciones,
            'totalRegistros': len(liquidaciones),
            'totales': {
                'ingreso_bruto':     round(total_ingreso,  2),
                'costos_operativos': round(total_costos,   2),
                'utilidad_operativa': round(total_utilidad, 2),
            },
        })
    except Exception as e:
        logger.error(f'Error obteniendo liquidaciones de máquinas: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


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

        tiene_admin = _has_admin_col(cursor)
        admin_col   = 'COALESCE(mpr.porcentaje_admin, 25.00)' if tiene_admin else '25.00'

        cursor.execute(
            f"""
            SELECT
                m.id, m.name, m.type,
                COALESCE(m.valor_por_turno, 3000.00) AS valor_por_turno,
                COALESCE(mpr.porcentaje_restaurante, %s) AS porcentaje_restaurante,
                {admin_col} AS porcentaje_admin
            FROM machine m
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
            ORDER BY m.name ASC
            """,
            (RESTAURANT_PERCENTAGE_DEFAULT,),
        )
        maquinas = cursor.fetchall()
        return jsonify({
            'datos': [
                {
                    'id':                   int(m['id']),
                    'name':                 m['name'],
                    'type':                 m['type'] or '',
                    'valor_por_turno':      float(m['valor_por_turno'] or 0),
                    'porcentaje_restaurante': float(m['porcentaje_restaurante'] or RESTAURANT_PERCENTAGE_DEFAULT),
                    'porcentaje_admin':     float(m['porcentaje_admin'] or ADMIN_PERCENTAGE_DEFAULT),
                }
                for m in maquinas
            ],
            'totalRegistros': len(maquinas),
        })

    except Exception as e:
        logger.error(f'Error obteniendo catálogo de máquinas: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


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
            return api_response('E002', http_status=404,
                                data={'message': 'La tabla liquidaciones no está disponible'})

        cursor.execute(
            """SELECT m.id, m.name,
                      COALESCE(m.valor_por_turno, 3000.00) AS valor_por_turno,
                      COALESCE(mpr.porcentaje_restaurante, %s) AS porcentaje_restaurante
               FROM machine m
               LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
               WHERE m.id = %s""",
            (RESTAURANT_PERCENTAGE_DEFAULT, data['maquina_id']),
        )
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': data['maquina_id']})

        valor_turno = _to_float(data.get('valor_por_turno'), _to_float(maquina['valor_por_turno'], 3000.0))
        pct_rest    = _to_float(data.get('porcentaje_restaurante'),
                                _to_float(maquina['porcentaje_restaurante'], RESTAURANT_PERCENTAGE_DEFAULT))

        cursor.execute(
            """INSERT INTO liquidaciones
               (fecha, maquina_id, turnos_retirados, valor_por_turno,
                costos_operativos, porcentaje_restaurante, observaciones, usuario_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                data['fecha'], data['maquina_id'],
                int(data['turnos_retirados']), valor_turno,
                _to_float(data.get('costos_operativos')), pct_rest,
                (data.get('observaciones') or '').strip() or None,
                session.get('user_id'),
            ),
        )
        liquidacion_id = cursor.lastrowid
        connection.commit()

        return jsonify({
            'success': True,
            'liquidacion_id': liquidacion_id,
            'message': f'Ingreso de {maquina["name"]} registrado correctamente',
        })

    except Exception as e:
        logger.error(f'Error registrando liquidación de máquina: {e}', exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Configurar porcentajes por máquina ──────────────────────────────────────

@liquidaciones_bp.route('/api/liquidaciones/maquinas/<int:maquina_id>/porcentajes', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
def actualizar_porcentajes_maquina(maquina_id):
    """Guardar porcentaje_restaurante y porcentaje_admin para una máquina."""
    connection = None
    cursor = None
    try:
        data        = request.get_json() or {}
        pct_negocio = _to_float(data.get('porcentaje_restaurante'), RESTAURANT_PERCENTAGE_DEFAULT)
        pct_admin   = _to_float(data.get('porcentaje_admin'),       ADMIN_PERCENTAGE_DEFAULT)

        if pct_negocio < 0 or pct_admin < 0 or (pct_negocio + pct_admin) >= 100:
            return api_response('E002', http_status=400,
                                data={'message': 'Los porcentajes deben ser positivos y sumar menos de 100'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute('SELECT id FROM machine WHERE id = %s', (maquina_id,))
        if not cursor.fetchone():
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        tiene_admin = _has_admin_col(cursor)

        if tiene_admin:
            cursor.execute(
                """INSERT INTO maquinaporcentajerestaurante (maquina_id, porcentaje_restaurante, porcentaje_admin)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       porcentaje_restaurante = VALUES(porcentaje_restaurante),
                       porcentaje_admin       = VALUES(porcentaje_admin)""",
                (maquina_id, pct_negocio, pct_admin),
            )
        else:
            cursor.execute(
                """INSERT INTO maquinaporcentajerestaurante (maquina_id, porcentaje_restaurante)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE porcentaje_restaurante = VALUES(porcentaje_restaurante)""",
                (maquina_id, pct_negocio),
            )

        connection.commit()
        logger.info(f'Porcentajes actualizados: maquina_id={maquina_id}, negocio={pct_negocio}, admin={pct_admin}')
        return jsonify({
            'success': True,
            'porcentaje_restaurante': pct_negocio,
            'porcentaje_admin': pct_admin,
            'porcentaje_utilidad': round(100 - pct_negocio - pct_admin, 2),
        })

    except Exception as e:
        logger.error(f'Error actualizando porcentajes: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ─── Verificar tablas ─────────────────────────────────────────────────────────

@liquidaciones_bp.route('/api/liquidaciones/verificar-tablas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def verificar_tablas_liquidaciones():
    """Diagnóstico de tablas y columnas del módulo de liquidaciones."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        tablas_requeridas = [
            'maquinaporcentajerestaurante', 'maquinapropietario', 'propietarios',
            'liquidaciones', 'gastos_liquidacion', 'cierre_liquidacion',
        ]
        resultados = {}
        for tabla in tablas_requeridas:
            existe = _table_exists(cursor, tabla)
            resultados[tabla] = existe
            if existe:
                cursor.execute(f'DESCRIBE {tabla}')
                resultados[f'{tabla}_columnas'] = [col['Field'] for col in cursor.fetchall()]

        tiene_admin = _has_admin_col(cursor) if resultados.get('maquinaporcentajerestaurante') else False

        conteos = {}
        for t in ['maquinaporcentajerestaurante', 'maquinapropietario', 'propietarios',
                  'gastos_liquidacion', 'cierre_liquidacion']:
            if resultados.get(t):
                cursor.execute(f'SELECT COUNT(*) AS cnt FROM {t}')
                conteos[t] = (cursor.fetchone() or {}).get('cnt', 0)

        return jsonify({
            'tablas':          resultados,
            'tiene_admin_col': tiene_admin,
            'conteos':         conteos,
        })

    except Exception as e:
        logger.error(f'Error verificando tablas: {e}', exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
