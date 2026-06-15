import logging
from datetime import datetime, date

from flask import Blueprint, request, jsonify, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_admin_access
from utils.helpers import parse_json_col
from utils.responses import handle_api_errors
from utils.timezone import get_colombia_time
from utils.location_scope import get_active_location

logger = logging.getLogger(LOGGER_NAME)

campaigns_bp = Blueprint('campaigns', __name__)


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDAD COMPARTIDA — importable desde qr/routes.py
# ─────────────────────────────────────────────────────────────────────────────

def get_active_campaign_for_package(package_id, location_id, cursor):
    """
    Dada una venta (package_id + location_id) en este instante, devuelve
    el efecto de la campaña de mayor prioridad que aplique, o None.

    Retorna dict:
        {
            campaign_id, campaign_name, rule_id, rule_type,
            original_turns, final_turns,
            original_price, final_price,
            savings_pct   (float, para mostrar en UI)
        }
    O None si no hay campaña activa.
    """
    now_col  = get_colombia_time()
    now_date = now_col.date()
    now_time = now_col.time()
    now_dow  = now_col.weekday()   # 0=lun … 6=dom

    # 1. Obtener precio y turnos base del paquete
    cursor.execute(
        "SELECT turns, price FROM turnpackage WHERE id = %s", (package_id,)
    )
    pkg = cursor.fetchone()
    if not pkg:
        return None
    base_turns = int(pkg['turns'])
    base_price = float(pkg['price'])

    # 2. Buscar campañas activas que apliquen a este local y fecha/hora
    cursor.execute("""
        SELECT c.id, c.name, c.schedule_type, c.schedule_config,
               c.date_from, c.date_to, c.time_from, c.time_to, c.priority,
               cr.id AS rule_id, cr.applies_to, cr.package_ids,
               cr.rule_type, cr.rule_value
        FROM campaign c
        JOIN campaign_rule cr ON cr.campaign_id = c.id
        WHERE c.is_active = 1
          AND (c.location_id IS NULL OR c.location_id = %s)
          AND (c.date_from IS NULL OR c.date_from <= %s)
          AND (c.date_to   IS NULL OR c.date_to   >= %s)
        ORDER BY c.priority DESC
    """, (location_id, now_date, now_date))

    rows = cursor.fetchall()
    if not rows:
        return None

    for row in rows:
        # ── Filtro de paquete ────────────────────────────────────────────────
        applies_to  = row['applies_to']
        pkg_ids_raw = row['package_ids']
        if applies_to == 'specific_packages':
            pkg_ids = parse_json_col(pkg_ids_raw, [])
            if package_id not in [int(x) for x in pkg_ids]:
                continue

        # ── Filtro de horario ────────────────────────────────────────────────
        stype  = row['schedule_type']
        passed = False

        if stype == 'once' or stype == 'flash':
            # Vigencia de fecha ya verificada en SQL; solo revisar hora si aplica
            if row['time_from'] and row['time_to']:
                passed = row['time_from'] <= now_time <= row['time_to']
            else:
                passed = True

        elif stype == 'recurring':
            cfg = parse_json_col(row['schedule_config'], {})
            days = cfg.get('days', list(range(7)))  # default: todos los días
            # Convertir días (0=dom…6=sáb en JS/Python weekend) al weekday de Python
            # En schedule_config guardamos: 0=lun,1=mar,...,6=dom (igual que Python weekday)
            if now_dow in [int(d) for d in days]:
                tf = cfg.get('time_from') or (str(row['time_from']) if row['time_from'] else None)
                tt = cfg.get('time_to')   or (str(row['time_to'])   if row['time_to']   else None)
                if tf and tt:
                    t_from = datetime.strptime(tf[:5], '%H:%M').time()
                    t_to   = datetime.strptime(tt[:5], '%H:%M').time()
                    passed = t_from <= now_time <= t_to
                else:
                    passed = True

        if not passed:
            continue

        # ── Aplicar regla ────────────────────────────────────────────────────
        rtype = row['rule_type']
        rval  = parse_json_col(row['rule_value'], {})

        final_turns = base_turns
        final_price = base_price

        if rtype == 'free':
            final_price = 0.0

        elif rtype == 'discount_pct':
            pct = float(rval.get('pct', 0))
            final_price = round(base_price * (1 - pct / 100), -2)   # redondear a 100

        elif rtype == 'discount_fixed':
            amount = float(rval.get('amount', 0))
            final_price = max(0.0, base_price - amount)

        elif rtype == 'fixed_price':
            final_price = float(rval.get('price', base_price))

        elif rtype == 'bonus_turns':
            bonus = int(rval.get('bonus', 0))
            final_turns = base_turns + bonus

        elif rtype == 'buy_x_get_y':
            # buy X turns, get Y turns — el precio no cambia, los turnos sí
            get_turns = int(rval.get('get', base_turns))
            final_turns = get_turns

        savings_pct = round((1 - final_price / base_price) * 100, 1) if base_price > 0 else 0

        return {
            'campaign_id':   row['id'],
            'campaign_name': row['name'],
            'rule_id':       row['rule_id'],
            'rule_type':     rtype,
            'rule_value':    rval,
            'original_turns': base_turns,
            'final_turns':    final_turns,
            'original_price': base_price,
            'final_price':    final_price,
            'savings_pct':    savings_pct,
        }

    return None


def record_redemption(cursor, result, qr_code, user_id, location_id, package_id=None):
    """Registra en campaign_redemption para analítica."""
    try:
        cursor.execute("""
            INSERT INTO campaign_redemption
                (campaign_id, campaign_rule_id, package_id, qr_code,
                 user_id, location_id,
                 original_turns, final_turns, original_price, final_price)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            result['campaign_id'], result['rule_id'],
            package_id, qr_code, user_id, location_id,
            result['original_turns'], result['final_turns'],
            result['original_price'], result['final_price'],
        ))
    except Exception as e:
        logger.warning(f"[campaign] No se pudo registrar redemption: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CRUD ADMIN — Campañas
# ─────────────────────────────────────────────────────────────────────────────

@campaigns_bp.route('/api/admin/campaigns', methods=['GET'])
@require_admin_access('paquetes')
@handle_api_errors
def list_campaigns():
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db'}), 500
    cur = get_db_cursor(conn)

    active_id, _ = get_active_location()
    if active_id is not None:
        cur.execute("""
            SELECT c.*, l.name AS location_name,
                   COUNT(cr.id) AS redemptions
            FROM campaign c
            LEFT JOIN location l ON c.location_id = l.id
            LEFT JOIN campaign_redemption cr ON cr.campaign_id = c.id
            WHERE c.location_id = %s OR c.location_id IS NULL
            GROUP BY c.id ORDER BY c.priority DESC, c.created_at DESC
        """, (active_id,))
    else:
        cur.execute("""
            SELECT c.*, l.name AS location_name,
                   COUNT(cr.id) AS redemptions
            FROM campaign c
            LEFT JOIN location l ON c.location_id = l.id
            LEFT JOIN campaign_redemption cr ON cr.campaign_id = c.id
            GROUP BY c.id ORDER BY c.priority DESC, c.created_at DESC
        """)

    campaigns = []
    for row in cur.fetchall():
        d = dict(row)
        d['schedule_config'] = parse_json_col(d.get('schedule_config'), {})
        for k in ('date_from', 'date_to'):
            if d.get(k):
                d[k] = str(d[k])
        for k in ('time_from', 'time_to'):
            if d.get(k) is not None:
                d[k] = str(d[k])[:5]
        for k in ('created_at', 'updated_at'):
            if d.get(k):
                d[k] = d[k].isoformat()
        # Reglas
        cur.execute("""
            SELECT id, applies_to, package_ids, rule_type, rule_value
            FROM campaign_rule WHERE campaign_id = %s
        """, (d['id'],))
        rules = []
        for r in cur.fetchall():
            rd = dict(r)
            rd['package_ids'] = parse_json_col(rd.get('package_ids'), [])
            rd['rule_value']  = parse_json_col(rd.get('rule_value'),  {})
            rules.append(rd)
        d['rules'] = rules
        campaigns.append(d)

    cur.close(); conn.close()
    return jsonify({'status': 'success', 'data': campaigns})


@campaigns_bp.route('/api/admin/campaigns/activas-ahora', methods=['GET'])
@require_admin_access('paquetes')
@handle_api_errors
def campaigns_activas_ahora():
    """Devuelve campañas que están activas en este momento (para banner en UI)."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'data': []}), 200
    cur = get_db_cursor(conn)

    now_col  = get_colombia_time()
    now_date = now_col.date()
    now_time = now_col.time()
    now_dow  = now_col.weekday()

    active_id, _ = get_active_location()

    cur.execute("""
        SELECT c.id, c.name, c.schedule_type, c.schedule_config,
               c.time_from, c.time_to, c.date_to, c.priority,
               l.name AS location_name,
               cr.rule_type, cr.rule_value
        FROM campaign c
        JOIN campaign_rule cr ON cr.campaign_id = c.id
        LEFT JOIN location l  ON c.location_id  = l.id
        WHERE c.is_active = 1
          AND (c.location_id IS NULL OR c.location_id = %s OR %s IS NULL)
          AND (c.date_from IS NULL OR c.date_from <= %s)
          AND (c.date_to   IS NULL OR c.date_to   >= %s)
        ORDER BY c.priority DESC
    """, (active_id, active_id, now_date, now_date))

    activas = []
    seen = set()
    for row in cur.fetchall():
        if row['id'] in seen:
            continue
        stype = row['schedule_type']
        ok    = False

        if stype in ('once', 'flash'):
            tf, tt = row['time_from'], row['time_to']
            ok = (tf is None) or (tf <= now_time <= tt)

        elif stype == 'recurring':
            cfg  = parse_json_col(row['schedule_config'], {})
            days = [int(d) for d in cfg.get('days', range(7))]
            if now_dow in days:
                tf_s = cfg.get('time_from') or (str(row['time_from'])[:5] if row['time_from'] else None)
                tt_s = cfg.get('time_to')   or (str(row['time_to'])[:5]   if row['time_to']   else None)
                if tf_s and tt_s:
                    ok = datetime.strptime(tf_s[:5], '%H:%M').time() <= now_time <= datetime.strptime(tt_s[:5], '%H:%M').time()
                else:
                    ok = True

        if ok:
            seen.add(row['id'])
            rval = parse_json_col(row['rule_value'], {})
            activas.append({
                'id':            row['id'],
                'name':          row['name'],
                'schedule_type': stype,
                'location_name': row['location_name'] or 'Todos los locales',
                'rule_type':     row['rule_type'],
                'rule_value':    rval,
                'date_to':       str(row['date_to']) if row['date_to'] else None,
                'time_to':       str(row['time_to'])[:5] if row['time_to'] else None,
            })

    cur.close(); conn.close()
    return jsonify({'status': 'success', 'data': activas})


@campaigns_bp.route('/api/admin/campaigns', methods=['POST'])
@require_admin_access('paquetes')
@handle_api_errors
def create_campaign():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name requerido'}), 400
    rule = data.get('rule')
    if not rule or not rule.get('rule_type'):
        return jsonify({'error': 'rule.rule_type requerido'}), 400

    import json as _json
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db'}), 500
    cur = get_db_cursor(conn)

    try:
        cur.execute("""
            INSERT INTO campaign
                (name, description, location_id, schedule_type, schedule_config,
                 date_from, date_to, time_from, time_to, priority, is_active, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            name,
            data.get('description') or None,
            data.get('location_id') or None,
            data.get('schedule_type', 'once'),
            _json.dumps(data.get('schedule_config') or {}),
            data.get('date_from') or None,
            data.get('date_to')   or None,
            data.get('time_from') or None,
            data.get('time_to')   or None,
            int(data.get('priority', 0)),
            1 if data.get('is_active', True) else 0,
            session.get('user_name', 'admin'),
        ))
        cid = cur.lastrowid

        pkg_ids = rule.get('package_ids') or []
        cur.execute("""
            INSERT INTO campaign_rule
                (campaign_id, applies_to, package_ids, rule_type, rule_value)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            cid,
            rule.get('applies_to', 'all_packages'),
            _json.dumps(pkg_ids) if pkg_ids else None,
            rule['rule_type'],
            _json.dumps(rule.get('rule_value') or {}),
        ))

        conn.commit()
        logger.info(f"[campaign] Creada '{name}' id={cid} por {session.get('user_name')}")
        return jsonify({'status': 'success', 'id': cid}), 201

    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()


@campaigns_bp.route('/api/admin/campaigns/<int:cid>', methods=['PUT'])
@require_admin_access('paquetes')
@handle_api_errors
def update_campaign(cid):
    import json as _json
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db'}), 500
    cur = get_db_cursor(conn)

    try:
        cur.execute("SELECT id FROM campaign WHERE id = %s", (cid,))
        if not cur.fetchone():
            return jsonify({'error': 'Campaña no encontrada'}), 404

        cur.execute("""
            UPDATE campaign SET
                name            = COALESCE(%s, name),
                description     = %s,
                location_id     = %s,
                schedule_type   = COALESCE(%s, schedule_type),
                schedule_config = COALESCE(%s, schedule_config),
                date_from       = %s,
                date_to         = %s,
                time_from       = %s,
                time_to         = %s,
                priority        = COALESCE(%s, priority),
                is_active       = COALESCE(%s, is_active)
            WHERE id = %s
        """, (
            data.get('name') or None,
            data.get('description'),
            data.get('location_id') or None,
            data.get('schedule_type') or None,
            _json.dumps(data['schedule_config']) if data.get('schedule_config') is not None else None,
            data.get('date_from') or None,
            data.get('date_to')   or None,
            data.get('time_from') or None,
            data.get('time_to')   or None,
            data.get('priority'),
            data.get('is_active'),
            cid,
        ))

        rule = data.get('rule')
        if rule:
            cur.execute("DELETE FROM campaign_rule WHERE campaign_id = %s", (cid,))
            pkg_ids = rule.get('package_ids') or []
            cur.execute("""
                INSERT INTO campaign_rule
                    (campaign_id, applies_to, package_ids, rule_type, rule_value)
                VALUES (%s,%s,%s,%s,%s)
            """, (
                cid,
                rule.get('applies_to', 'all_packages'),
                _json.dumps(pkg_ids) if pkg_ids else None,
                rule['rule_type'],
                _json.dumps(rule.get('rule_value') or {}),
            ))

        conn.commit()
        return jsonify({'status': 'success'})

    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


@campaigns_bp.route('/api/admin/campaigns/<int:cid>', methods=['DELETE'])
@require_admin_access('paquetes')
@handle_api_errors
def delete_campaign(cid):
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db'}), 500
    cur = get_db_cursor(conn)
    try:
        cur.execute("DELETE FROM campaign WHERE id = %s", (cid,))
        if cur.rowcount == 0:
            return jsonify({'error': 'No encontrada'}), 404
        conn.commit()
        return jsonify({'status': 'success'})
    finally:
        cur.close(); conn.close()


@campaigns_bp.route('/api/admin/campaigns/<int:cid>/toggle', methods=['POST'])
@require_admin_access('paquetes')
@handle_api_errors
def toggle_campaign(cid):
    """Activa o desactiva una campaña instantáneamente."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db'}), 500
    cur = get_db_cursor(conn)
    try:
        cur.execute(
            "UPDATE campaign SET is_active = NOT is_active WHERE id = %s", (cid,)
        )
        if cur.rowcount == 0:
            return jsonify({'error': 'No encontrada'}), 404
        cur.execute("SELECT is_active FROM campaign WHERE id = %s", (cid,))
        new_state = bool(cur.fetchone()['is_active'])
        conn.commit()
        return jsonify({'status': 'success', 'is_active': new_state})
    finally:
        cur.close(); conn.close()


@campaigns_bp.route('/api/admin/campaigns/<int:cid>/flash', methods=['POST'])
@require_admin_access('paquetes')
@handle_api_errors
def flash_campaign(cid):
    """
    Activa la campaña ahora mismo durante N horas (por defecto 2).
    Cambia schedule_type=flash, date_from/to=hoy, time_from=ahora, time_to=ahora+N.
    """
    import json as _json
    data  = request.get_json(silent=True) or {}
    hours = float(data.get('hours', 2))

    now    = get_colombia_time()
    t_from = now.strftime('%H:%M')
    from datetime import timedelta
    t_to   = (now + timedelta(hours=hours)).strftime('%H:%M')
    today  = now.date()

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db'}), 500
    cur = get_db_cursor(conn)
    try:
        cur.execute("""
            UPDATE campaign
            SET schedule_type = 'flash',
                date_from = %s, date_to = %s,
                time_from = %s, time_to = %s,
                is_active = 1
            WHERE id = %s
        """, (today, today, t_from, t_to, cid))
        if cur.rowcount == 0:
            return jsonify({'error': 'No encontrada'}), 404
        conn.commit()
        logger.info(f"[campaign] Flash activado cid={cid} {t_from}–{t_to} por {session.get('user_name')}")
        return jsonify({'status': 'success', 'time_from': t_from, 'time_to': t_to})
    finally:
        cur.close(); conn.close()


@campaigns_bp.route('/api/admin/campaigns/stats', methods=['GET'])
@require_admin_access('paquetes')
@handle_api_errors
def campaign_stats():
    """Analítica: total redimido, ahorro total, top campañas."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'data': {}}), 200
    cur = get_db_cursor(conn)

    active_id, _ = get_active_location()
    loc_filter = "AND cr.location_id = %s" if active_id else ""
    params = [active_id] if active_id else []

    cur.execute(f"""
        SELECT
            c.id, c.name,
            COUNT(cr.id)      AS total_redenciones,
            SUM(cr.savings)   AS ahorro_total,
            SUM(cr.final_price) AS ingresos_con_descuento
        FROM campaign c
        LEFT JOIN campaign_redemption cr ON cr.campaign_id = c.id
        WHERE 1=1 {loc_filter}
        GROUP BY c.id, c.name
        ORDER BY total_redenciones DESC
        LIMIT 10
    """, params)

    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    return jsonify({'status': 'success', 'data': rows})
