import logging
from functools import wraps

from flask import session, request, redirect, json

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.responses import api_response

logger = logging.getLogger(LOGGER_NAME)


# ── Decoradores de autenticación ──────────────────────────────────────────────

def require_login(roles=None):
    """
    Decorador de autenticación.

    Sin argumentos → solo verifica que haya sesión activa.
    Con roles=['admin', 'cajero'] → verifica que el usuario tenga uno de esos roles
    o que su rol tenga los permisos equivalentes en la tabla `roles`.

    Nota: 'admin_restaurante' nunca obtiene acceso de admin aunque tenga
    el permiso 'admin_panel' (misma restricción que cajero).

    En fase 2, cuando los blueprints estén activos, actualizar url_for a
    'auth.mostrar_login' y 'auth.mostrar_local'.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return api_response('A004', http_status=401)
                return redirect('/login')

            if roles:
                user_role = session.get('user_role')

                # Rol está directamente en la lista permitida
                if user_role in roles:
                    return func(*args, **kwargs)

                # Verificar permisos en tabla roles
                try:
                    connection = get_db_connection()
                    if connection:
                        cursor = get_db_cursor(connection)
                        cursor.execute(
                            "SELECT permisos FROM roles WHERE id = %s AND activo = TRUE",
                            (user_role,)
                        )
                        rol_data = cursor.fetchone()
                        cursor.close()
                        connection.close()

                        if rol_data:
                            permisos = rol_data['permisos']
                            if isinstance(permisos, str):
                                permisos = json.loads(permisos)

                            es_admin_restaurante = (user_role == 'admin_restaurante')

                            if 'admin' in roles and 'admin_panel' in permisos and not es_admin_restaurante:
                                return func(*args, **kwargs)

                            if 'cajero' in roles and 'ver' in permisos:
                                return func(*args, **kwargs)

                            if 'admin_restaurante' in roles and ('ver' in permisos or 'reportes' in permisos):
                                return func(*args, **kwargs)

                except Exception as e:
                    logger.error(f"Error verificando permisos en require_login: {e}")

                return api_response('E004', http_status=403)

            return func(*args, **kwargs)
        return wrapper
    return decorator


def require_permission(permission):
    """
    Decorador que verifica un permiso específico desde la columna
    JSON `permisos` de la tabla `roles`.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return api_response('E003', http_status=401)
                return redirect('/login')

            user_role = session.get('user_role')

            try:
                connection = get_db_connection()
                if not connection:
                    return api_response('E006', http_status=500)

                cursor = get_db_cursor(connection)
                cursor.execute(
                    "SELECT permisos FROM roles WHERE id = %s AND activo = TRUE",
                    (user_role,)
                )
                rol = cursor.fetchone()
                cursor.close()
                connection.close()

                if not rol:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return api_response('E004', http_status=403)
                    return redirect('/login')

                permisos = rol['permisos']
                if isinstance(permisos, str):
                    permisos = json.loads(permisos)

                if permission not in permisos:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return api_response('E004', http_status=403,
                                            data={'message': f'No tienes permiso: {permission}'})
                    return redirect('/local')

            except Exception as e:
                logger.error(f"Error verificando permiso '{permission}': {e}")
                return api_response('E001', http_status=500)

            return func(*args, **kwargs)
        return wrapper
    return decorator


# ── Utilidad de permisos ──────────────────────────────────────────────────────

def get_user_permissions() -> list:
    """
    Retorna la lista de permisos del usuario actual según la tabla `roles`.
    Retorna [] si no hay sesión o si falla la consulta.
    """
    try:
        user_role = session.get('user_role')
        if not user_role:
            return []

        connection = get_db_connection()
        if not connection:
            return []

        cursor = get_db_cursor(connection)
        cursor.execute(
            "SELECT permisos FROM roles WHERE id = %s AND activo = TRUE",
            (user_role,)
        )
        rol = cursor.fetchone()
        cursor.close()
        connection.close()

        if not rol:
            return []

        permisos = rol['permisos']
        if isinstance(permisos, str):
            permisos = json.loads(permisos)

        return permisos or []

    except Exception as e:
        logger.error(f"Error obteniendo permisos del usuario: {e}")
        return []


# ── Vocabulario de permisos (fuente única para backend y UI) ──────────────────

# Acceso de lectura por sección/dominio. Permiso = 'ver_<seccion>'.
SECCIONES = [
    'dashboard', 'usuarios', 'maquinas', 'paquetes',
    'locales', 'liquidaciones', 'logs', 'mensajes', 'socios',
]

# Acciones globales (transversales a todas las secciones).
ACCIONES = ['ver', 'crear', 'editar', 'eliminar', 'reportes', 'configurar']

# Permiso master para entrar al panel admin.
PERMISO_ADMIN = 'admin_panel'

# Acción exigida según el método HTTP (None = solo se exige la sección).
_ACTION_BY_METHOD = {
    'GET': None, 'HEAD': None, 'OPTIONS': None,
    'POST': 'crear', 'PUT': 'editar', 'PATCH': 'editar', 'DELETE': 'eliminar',
}


def require_admin_access(section=None, action='auto'):
    """
    Decorador RBAC para endpoints del panel admin (reemplaza a
    require_login(['admin']) cuando se quiere control por sección y acción).

    Flujo:
      1. Exige sesión activa.
      2. El rol 'admin' hace bypass total (acceso completo).
      3. 'admin_restaurante' nunca entra al admin (regla histórica, igual que cajero).
      4. Exige el permiso master 'admin_panel'.
      5. Si se indica `section` (ej. 'usuarios'), exige 'ver_<section>'.
      6. Exige la acción:
         - action='auto' (default): se infiere del método HTTP
           (POST→crear, PUT/PATCH→editar, DELETE→eliminar, GET→ninguna).
         - action=None: no exige acción (solo sección).
         - action='<permiso>': exige ese permiso explícito (ej. 'configurar'
           para POSTs que son acciones de control y no creación de registros).
    """
    def _denegar(message=None):
        # API (XHR) → 403 JSON; navegación de página → redirect a /local
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            data = {'message': message} if message else None
            return api_response('E004', http_status=403, data=data)
        return redirect('/local')

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('logged_in'):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return api_response('A004', http_status=401)
                return redirect('/login')

            user_role = session.get('user_role')

            # admin: acceso total, sin más comprobaciones
            if user_role == 'admin':
                return func(*args, **kwargs)

            # admin_restaurante: equivalente a cajero, nunca admin
            if user_role == 'admin_restaurante':
                return _denegar()

            permisos = get_user_permissions()

            if PERMISO_ADMIN not in permisos:
                return _denegar()

            if section and f'ver_{section}' not in permisos:
                return _denegar(f'Sin acceso a la sección: {section}')

            req_action = _ACTION_BY_METHOD.get(request.method) if action == 'auto' else action
            if req_action and req_action not in permisos:
                return _denegar(f'Sin permiso para: {req_action}')

            return func(*args, **kwargs)
        return wrapper
    return decorator
