"""
utils/location_scope.py
Gestión del contexto de local activo por sesión.

Roles con acceso global (pueden cambiar y ver todos los locales):
    - admin

Roles con local fijo (solo ven su local asignado):
    - cajero
    - admin_restaurante

Roles excluidos de este sistema (flujo propio):
    - socio
"""

import logging
from flask import session, jsonify, abort

from config import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# Roles que pueden seleccionar / cambiar de local
ROLES_GLOBAL = {'admin'}

# Roles que requieren local asignado obligatorio
ROLES_FIJO = {'cajero', 'admin_restaurante'}


# ── Construcción del contexto ─────────────────────────────────────────────────

def build_user_location_context(usuario: dict, cursor) -> dict:
    """
    Construye el dict de contexto de local para un usuario recién autenticado.
    Se guarda completo en sesión justo después del login.

    Retorna el contexto; no escribe en sesión (eso lo hace el caller).
    """
    role = usuario.get('role', '')
    assigned_id = usuario.get('location_id')  # puede ser None

    can_switch = role in ROLES_GLOBAL
    can_view_all = role in ROLES_GLOBAL

    # Resolver nombre del local asignado
    assigned_name = None
    if assigned_id:
        try:
            cursor.execute("SELECT name FROM location WHERE id = %s", (assigned_id,))
            row = cursor.fetchone()
            if row:
                assigned_name = row['name']
        except Exception as e:
            logger.error(f"Error resolviendo nombre de local asignado: {e}")

    # Roles globales: cargar lista de todos los locales activos
    allowed_ids = []
    if can_switch:
        try:
            cursor.execute("SELECT id, name FROM location WHERE status = 'activo' ORDER BY name")
            rows = cursor.fetchall()
            allowed_ids = [r['id'] for r in rows]
        except Exception as e:
            logger.error(f"Error cargando locales disponibles para admin: {e}")
    elif assigned_id:
        allowed_ids = [assigned_id]

    # Para roles fijos: active = assigned desde el inicio
    # Para roles globales: active queda None hasta que elijan en el selector
    if can_switch:
        active_id = None
        active_name = None
    else:
        active_id = assigned_id
        active_name = assigned_name

    return {
        'assigned_location_id':   assigned_id,
        'assigned_location_name': assigned_name,
        'active_location_id':     active_id,
        'active_location_name':   active_name,
        'allowed_location_ids':   allowed_ids,
        'can_switch_location':    can_switch,
        'can_view_all_locations': can_view_all,
    }


def save_location_context_to_session(context: dict):
    """Persiste el contexto de local en la sesión Flask."""
    for key, value in context.items():
        session[key] = value
    session.modified = True


# ── Lectura de sesión ─────────────────────────────────────────────────────────

def get_active_location() -> tuple:
    """
    Retorna (active_location_id, active_location_name) de la sesión.
    Puede ser (None, None) si el admin aún no eligió local.
    """
    return (
        session.get('active_location_id'),
        session.get('active_location_name'),
    )


def user_can_switch_location() -> bool:
    return bool(session.get('can_switch_location'))


def user_can_view_all() -> bool:
    return bool(session.get('can_view_all_locations'))


def get_location_context_for_frontend() -> dict:
    """Dict limpio para enviar al frontend (API o template)."""
    return {
        'active_location_id':     session.get('active_location_id'),
        'active_location_name':   session.get('active_location_name'),
        'assigned_location_id':   session.get('assigned_location_id'),
        'assigned_location_name': session.get('assigned_location_name'),
        'can_switch_location':    session.get('can_switch_location', False),
        'can_view_all_locations': session.get('can_view_all_locations', False),
    }


# ── Validación y enforcement ──────────────────────────────────────────────────

def enforce_location_scope(requested_location_id=None):
    """
    Valida que el location_id pedido esté dentro del alcance del usuario.
    Llama abort(403) si no tiene permiso.

    Si requested_location_id es None no hace nada (el caller usará el activo).
    """
    if requested_location_id is None:
        return

    can_switch = session.get('can_switch_location', False)
    allowed = session.get('allowed_location_ids', [])

    if can_switch:
        # Admin: cualquier local es válido siempre que exista en allowed
        if allowed and requested_location_id not in allowed:
            abort(403)
    else:
        # Rol fijo: solo puede pedir su local asignado
        assigned = session.get('assigned_location_id')
        if requested_location_id != assigned:
            abort(403)


def apply_location_filter(base_sql: str, params: list, column: str = 'location_id',
                          table_alias: str = '') -> tuple:
    """
    Agrega un filtro WHERE/AND {column} = {active_location_id} a una query.

    - Si el usuario tiene can_view_all y active_location_id es None
      (modo "todos los locales") → no agrega filtro.
    - Para roles fijos → siempre filtra por su local.

    Retorna (sql_modificado, params_modificados).
    """
    active_id = session.get('active_location_id')
    can_view_all = session.get('can_view_all_locations', False)

    # Modo "ver todos": no filtrar
    if can_view_all and active_id is None:
        return base_sql, params

    if active_id is None:
        # Sin local activo y sin permiso global → no debería pasar, pero
        # por seguridad filtramos con un valor imposible
        logger.warning("apply_location_filter: active_location_id es None sin permiso global")
        active_id = -1

    col = f"{table_alias}.{column}" if table_alias else column
    connector = 'AND' if 'WHERE' in base_sql.upper() else 'WHERE'
    sql = f"{base_sql.rstrip()} {connector} {col} = %s"
    return sql, params + [active_id]


# ── Actualización de local activo ─────────────────────────────────────────────

def set_active_location(location_id: int | None, location_name: str | None):
    """
    Actualiza el local activo en sesión.
    location_id=None significa modo "todos los locales" (solo para can_view_all).
    """
    session['active_location_id'] = location_id
    session['active_location_name'] = location_name
    session.modified = True
