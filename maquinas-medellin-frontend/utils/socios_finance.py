"""
Capa financiera de socios — fórmulas espejo de liquidaciones.

Distribución 3-way (mismos defaults que liquidaciones/routes.py):
  ingreso_bruto
  └── negocio   = ingreso_bruto × pct_rest  / 100   (local / restaurante, def. 35%)
  └── admin     = ingreso_bruto × pct_admin / 100   (Inversiones Arcade, def. 25%)
  └── utilidad  = ingreso_bruto − negocio − admin   → se reparte entre inversores
        └── participacion = utilidad × (porcentaje_inversion / 100)

Fuente canónica de ingresos: turnusage → tp.price / tp.turns por turno jugado.
Idéntico a _fetch_usage_summary en liquidaciones/routes.py.
"""

import logging
from datetime import date, timedelta

from config import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

_ADMIN_PCT_DEFAULT      = 25.0
_RESTAURANT_PCT_DEFAULT = 35.0


# ─── Utilitarios internos ──────────────────────────────────────────────────────

def _to_float(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


def _has_admin_col(cursor):
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


def _admin_sql(tiene_admin_col):
    if tiene_admin_col:
        return 'COALESCE(mpr.porcentaje_admin, 25.00)'
    return '25.00'


def _utilidad_socio(ingreso_bruto, pct_rest, pct_admin, pct_inversion):
    """Participación neta del socio sobre el ingreso bruto de una máquina."""
    utilidad = ingreso_bruto * (100 - pct_rest - pct_admin) / 100
    return round(utilidad * pct_inversion / 100, 2)


# ─── Query base por período ────────────────────────────────────────────────────
# Agrupa por (máquina, local) y calcula ingreso_bruto via precio-por-turno.
# Cada registro de turnusage aporta tp.price / tp.turns al ingreso de esa máquina.

_BASE_SQL = """
    SELECT
        m.id                                                    AS maquina_id,
        m.name                                                  AS maquina_nombre,
        m.type                                                  AS maquina_tipo,
        COALESCE(l.id,   0)                                     AS local_id,
        COALESCE(l.name, 'Sin local')                           AS local_nombre,
        COALESCE(mpr.porcentaje_restaurante, {rest})            AS pct_rest,
        {admin_expr}                                            AS pct_admin,
        i.porcentaje_inversion                                  AS pct_inversion,
        COALESCE(SUM(
            tp.price / NULLIF(tp.turns, 0)
        ), 0)                                                   AS ingreso_bruto,
        COUNT(tu.id)                                            AS turnos_jugados
    FROM qrhistory qh
    JOIN qrcode       qr  ON qr.code          = qh.qr_code
    JOIN turnpackage  tp  ON qr.turnPackageId = tp.id
    JOIN turnusage    tu  ON tu.qrCodeId      = qr.id
    JOIN machine       m  ON tu.machineId     = m.id
    JOIN inversiones   i  ON i.maquina_id    = m.id
    LEFT JOIN location l  ON m.location_id   = l.id
    LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
    WHERE qh.es_venta_real = TRUE
      AND qr.turnPackageId IS NOT NULL
      AND qr.turnPackageId != 1
      AND DATE(qh.fecha_hora) BETWEEN %s AND %s
      AND i.socio_id = %s
      AND i.estado   = 'activa'
    GROUP BY m.id, m.name, m.type,
             l.id, l.name,
             mpr.porcentaje_restaurante, {admin_group},
             i.porcentaje_inversion
"""


def _build_sql(tiene_admin_col):
    admin_expr  = _admin_sql(tiene_admin_col)
    admin_group = 'mpr.porcentaje_admin' if tiene_admin_col else "'25.00'"
    return _BASE_SQL.format(
        rest=_RESTAURANT_PCT_DEFAULT,
        admin_expr=admin_expr,
        admin_group=admin_group,
    )


def _run_base(cursor, socio_id, fecha_inicio, fecha_fin):
    """Ejecuta la query base y devuelve filas enriquecidas con participación."""
    tiene_admin = _has_admin_col(cursor)
    sql = _build_sql(tiene_admin)
    cursor.execute(sql, (fecha_inicio, fecha_fin, socio_id))
    rows = []
    for r in cursor.fetchall():
        ingreso_bruto = _to_float(r['ingreso_bruto'])
        pct_rest      = _to_float(r['pct_rest'],      _RESTAURANT_PCT_DEFAULT)
        pct_admin     = _to_float(r['pct_admin'],     _ADMIN_PCT_DEFAULT)
        pct_inv       = _to_float(r['pct_inversion'], 0)
        negocio       = round(ingreso_bruto * pct_rest  / 100, 2)
        admin_monto   = round(ingreso_bruto * pct_admin / 100, 2)
        utilidad      = round(ingreso_bruto - negocio - admin_monto, 2)
        participacion = round(utilidad * pct_inv / 100, 2)
        rows.append({
            'maquina_id':       r['maquina_id'],
            'maquina_nombre':   r['maquina_nombre'],
            'maquina_tipo':     r['maquina_tipo'],
            'local_id':         r['local_id'],
            'local_nombre':     r['local_nombre'],
            'pct_rest':         round(pct_rest, 2),
            'pct_admin':        round(pct_admin, 2),
            'pct_inversion':    round(pct_inv, 2),
            'ingreso_bruto':    ingreso_bruto,
            'negocio':          negocio,
            'admin':            admin_monto,
            'utilidad':         utilidad,
            'participacion':    participacion,
            'turnos_jugados':   int(r['turnos_jugados'] or 0),
        })
    return rows


# ─── API pública del helper ────────────────────────────────────────────────────

def calcular_utilidad_socio(cursor, socio_id, fecha_inicio, fecha_fin):
    """
    Resumen global del socio en el período.
    Devuelve totales y desglose por máquina.
    """
    filas = _run_base(cursor, socio_id, fecha_inicio, fecha_fin)
    total_bruto    = sum(f['ingreso_bruto'] for f in filas)
    total_negocio  = sum(f['negocio']       for f in filas)
    total_admin    = sum(f['admin']         for f in filas)
    total_utilidad = sum(f['utilidad']      for f in filas)
    total_particip = sum(f['participacion'] for f in filas)

    return {
        'fecha_inicio':      str(fecha_inicio),
        'fecha_fin':         str(fecha_fin),
        'ingreso_bruto':     round(total_bruto,    2),
        'negocio':           round(total_negocio,  2),
        'admin':             round(total_admin,    2),
        'utilidad_pool':     round(total_utilidad, 2),
        'participacion':     round(total_particip, 2),
        'maquinas':          len(filas),
        'detalle_maquinas':  filas,
    }


def calcular_detalle_por_maquina(cursor, socio_id, fecha_inicio, fecha_fin):
    """Lista de máquinas con ingreso, split y participación del socio."""
    return _run_base(cursor, socio_id, fecha_inicio, fecha_fin)


def calcular_detalle_por_local(cursor, socio_id, fecha_inicio, fecha_fin):
    """Agrupa las máquinas por local y totaliza la participación."""
    filas = _run_base(cursor, socio_id, fecha_inicio, fecha_fin)
    locales = {}
    for f in filas:
        lid = f['local_id']
        if lid not in locales:
            locales[lid] = {
                'local_id':      lid,
                'local_nombre':  f['local_nombre'],
                'ingreso_bruto': 0.0,
                'utilidad':      0.0,
                'participacion': 0.0,
                'maquinas':      [],
            }
        locales[lid]['ingreso_bruto'] += f['ingreso_bruto']
        locales[lid]['utilidad']      += f['utilidad']
        locales[lid]['participacion'] += f['participacion']
        locales[lid]['maquinas'].append(f)

    for loc in locales.values():
        loc['ingreso_bruto'] = round(loc['ingreso_bruto'], 2)
        loc['utilidad']      = round(loc['utilidad'],      2)
        loc['participacion'] = round(loc['participacion'], 2)

    return list(locales.values())


def calcular_evolucion_mensual(cursor, socio_id, meses=6):
    """
    Devuelve un array de los últimos `meses` meses con la participación mensual.
    Formato: [{'periodo': '2026-03', 'label': 'Mar 2026', 'participacion': 12345.0}, ...]
    """
    tiene_admin = _has_admin_col(cursor)
    admin_expr  = _admin_sql(tiene_admin)
    admin_group = 'mpr.porcentaje_admin' if tiene_admin else "'25.00'"

    cursor.execute(
        f"""
        SELECT
            DATE_FORMAT(qh.fecha_hora, '%Y-%m')  AS periodo,
            DATE_FORMAT(qh.fecha_hora, '%b %Y')  AS label,
            COALESCE(mpr.porcentaje_restaurante, {_RESTAURANT_PCT_DEFAULT}) AS pct_rest,
            {admin_expr}                          AS pct_admin,
            i.porcentaje_inversion                AS pct_inv,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) AS ingreso_bruto
        FROM qrhistory qh
        JOIN qrcode       qr  ON qr.code          = qh.qr_code
        JOIN turnpackage  tp  ON qr.turnPackageId = tp.id
        JOIN turnusage    tu  ON tu.qrCodeId      = qr.id
        JOIN machine       m  ON tu.machineId     = m.id
        JOIN inversiones   i  ON i.maquina_id    = m.id
        LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
        WHERE qh.es_venta_real = TRUE
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
          AND DATE(qh.fecha_hora) >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)
          AND i.socio_id = %s
          AND i.estado   = 'activa'
        GROUP BY DATE_FORMAT(qh.fecha_hora, '%Y-%m'),
                 DATE_FORMAT(qh.fecha_hora, '%b %Y'),
                 mpr.porcentaje_restaurante, {admin_group},
                 i.porcentaje_inversion
        ORDER BY periodo ASC
        """,
        (meses, socio_id),
    )
    raw = cursor.fetchall()

    # Agrupa por período sumando participaciones de distintas máquinas
    agrupado = {}
    for r in raw:
        p   = r['periodo']
        ib  = _to_float(r['ingreso_bruto'])
        pr  = _to_float(r['pct_rest'],  _RESTAURANT_PCT_DEFAULT)
        pa  = _to_float(r['pct_admin'], _ADMIN_PCT_DEFAULT)
        pi  = _to_float(r['pct_inv'],   0)
        util = ib * (100 - pr - pa) / 100
        part = util * pi / 100
        if p not in agrupado:
            agrupado[p] = {'periodo': p, 'label': r['label'], 'participacion': 0.0}
        agrupado[p]['participacion'] = round(agrupado[p]['participacion'] + part, 2)

    return list(agrupado.values())


def calcular_roi(cursor, socio_id):
    """
    ROI histórico: participacion_acumulada / monto_invertido × 100.
    Si no hay inversión registrada, devuelve 0.
    """
    tiene_admin = _has_admin_col(cursor)
    admin_expr  = _admin_sql(tiene_admin)
    admin_group = 'mpr.porcentaje_admin' if tiene_admin else "'25.00'"

    # Monto total invertido activo
    cursor.execute(
        """
        SELECT COALESCE(SUM(monto_inicial), 0) AS monto_total
        FROM inversiones
        WHERE socio_id = %s AND estado = 'activa'
        """,
        (socio_id,),
    )
    row = cursor.fetchone()
    monto_total = _to_float(row['monto_total'] if row else 0)
    if monto_total <= 0:
        return 0.0

    # Participación acumulada histórica
    cursor.execute(
        f"""
        SELECT
            COALESCE(mpr.porcentaje_restaurante, {_RESTAURANT_PCT_DEFAULT}) AS pct_rest,
            {admin_expr}                          AS pct_admin,
            i.porcentaje_inversion                AS pct_inv,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0) AS ingreso_bruto
        FROM qrhistory qh
        JOIN qrcode       qr  ON qr.code          = qh.qr_code
        JOIN turnpackage  tp  ON qr.turnPackageId = tp.id
        JOIN turnusage    tu  ON tu.qrCodeId      = qr.id
        JOIN machine       m  ON tu.machineId     = m.id
        JOIN inversiones   i  ON i.maquina_id    = m.id
        LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
        WHERE qh.es_venta_real = TRUE
          AND qr.turnPackageId IS NOT NULL
          AND qr.turnPackageId != 1
          AND i.socio_id = %s
          AND i.estado   = 'activa'
        GROUP BY mpr.porcentaje_restaurante, {admin_group}, i.porcentaje_inversion
        """,
        (socio_id,),
    )
    participacion_total = 0.0
    for r in cursor.fetchall():
        ib  = _to_float(r['ingreso_bruto'])
        pr  = _to_float(r['pct_rest'],  _RESTAURANT_PCT_DEFAULT)
        pa  = _to_float(r['pct_admin'], _ADMIN_PCT_DEFAULT)
        pi  = _to_float(r['pct_inv'],   0)
        util = ib * (100 - pr - pa) / 100
        participacion_total += util * pi / 100

    return round(participacion_total / monto_total * 100, 2)


def calcular_resumen_todos_socios(cursor, fecha_inicio, fecha_fin):
    """
    Para gestionsocios.html: devuelve todos los socios activos con su
    participación real en el período.  Útil para el resumen de admin.
    """
    tiene_admin = _has_admin_col(cursor)
    admin_expr  = _admin_sql(tiene_admin)
    admin_group = 'mpr.porcentaje_admin' if tiene_admin else "'25.00'"

    cursor.execute(
        f"""
        SELECT
            s.id                                                    AS socio_id,
            s.nombre                                                AS socio_nombre,
            s.codigo_socio,
            s.estado,
            COALESCE(l.id,   0)                                     AS local_id,
            COALESCE(l.name, 'Sin local')                           AS local_nombre,
            COALESCE(SUM(i.monto_inicial), 0)                       AS inversion_total,
            COALESCE(mpr.porcentaje_restaurante, {_RESTAURANT_PCT_DEFAULT}) AS pct_rest,
            {admin_expr}                                            AS pct_admin,
            i.porcentaje_inversion                                  AS pct_inv,
            COALESCE(SUM(tp.price / NULLIF(tp.turns, 0)), 0)        AS ingreso_bruto
        FROM socios s
        JOIN inversiones i ON i.socio_id = s.id AND i.estado = 'activa'
        JOIN machine       m  ON i.maquina_id    = m.id
        LEFT JOIN location l  ON m.location_id   = l.id
        LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
        LEFT JOIN turnusage    tu  ON tu.machineId      = m.id
        LEFT JOIN qrcode       qr  ON tu.qrCodeId      = qr.id
        LEFT JOIN turnpackage  tp  ON qr.turnPackageId = tp.id
        LEFT JOIN qrhistory    qh  ON qh.qr_code       = qr.code
                                   AND qh.es_venta_real = TRUE
                                   AND qr.turnPackageId IS NOT NULL
                                   AND qr.turnPackageId != 1
                                   AND DATE(qh.fecha_hora) BETWEEN %s AND %s
        GROUP BY s.id, s.nombre, s.codigo_socio, s.estado,
                 l.id, l.name,
                 mpr.porcentaje_restaurante, {admin_group},
                 i.porcentaje_inversion
        ORDER BY s.nombre
        """,
        (fecha_inicio, fecha_fin),
    )

    socios = {}
    for r in cursor.fetchall():
        sid = r['socio_id']
        if sid not in socios:
            socios[sid] = {
                'socio_id':       sid,
                'socio_nombre':   r['socio_nombre'],
                'codigo_socio':   r['codigo_socio'],
                'estado':         r['estado'],
                'inversion_total': 0.0,
                'participacion':  0.0,
                'locales':        set(),
            }
        ib   = _to_float(r['ingreso_bruto'])
        pr   = _to_float(r['pct_rest'],  _RESTAURANT_PCT_DEFAULT)
        pa   = _to_float(r['pct_admin'], _ADMIN_PCT_DEFAULT)
        pi   = _to_float(r['pct_inv'],   0)
        util = ib * (100 - pr - pa) / 100
        part = util * pi / 100
        socios[sid]['participacion']  = round(socios[sid]['participacion'] + part, 2)
        socios[sid]['inversion_total'] = round(
            socios[sid]['inversion_total'] + _to_float(r['inversion_total']), 2
        )
        if r['local_nombre'] != 'Sin local':
            socios[sid]['locales'].add(r['local_nombre'])

    result = []
    for s in socios.values():
        s['locales'] = sorted(s['locales'])
        result.append(s)
    return result
