"""
test_auth.py — Smoke tests del flujo de autenticación.

Cubre:
- GET /login      → renderiza la página
- POST /login     → credenciales inválidas (usuario no existe)
- POST /login     → campos vacíos
- GET /local      → sin sesión → redirige a /login
- GET /logout     → limpia sesión y redirige a /login
"""
import json
from unittest.mock import patch, MagicMock

from tests.conftest import make_mock_cursor, make_mock_connection


# ── GET /login ────────────────────────────────────────────────────────────────

def test_login_page_loads(client):
    """La página de login devuelve 200 y contiene el formulario."""
    response = client.get('/login')
    assert response.status_code == 200
    assert b'login' in response.data.lower() or b'ingresar' in response.data.lower()




# ── POST /login — validación de campos ───────────────────────────────────────

def test_login_missing_fields_returns_400(client):
    """Login sin nombre ni contraseña devuelve 400."""
    response = client.post(
        '/login',
        data=json.dumps({}),
        content_type='application/json',
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['valido'] is False


def test_login_empty_nombre_returns_400(client):
    """Login con nombre vacío devuelve 400."""
    response = client.post(
        '/login',
        data=json.dumps({'nombre': '', 'password': 'algo'}),
        content_type='application/json',
    )
    assert response.status_code == 400


# ── POST /login — usuario no existe ──────────────────────────────────────────

def test_login_user_not_found_returns_401(client):
    """
    Si el usuario no existe en BD (fetchone → None), el login devuelve 401.
    Mockeamos get_db_connection en el namespace del blueprint auth.
    """
    cursor = make_mock_cursor(fetchone_result=None)
    conn   = make_mock_connection(cursor)

    with patch('blueprints.auth.routes.get_db_connection', return_value=conn), \
         patch('blueprints.auth.routes.get_db_cursor', return_value=cursor):

        response = client.post(
            '/login',
            data=json.dumps({'nombre': 'USUARIO_INEXISTENTE', 'password': 'wrong'}),
            content_type='application/json',
        )

    assert response.status_code == 401
    data = json.loads(response.data)
    assert data['valido'] is False


# ── POST /login — contraseña incorrecta ──────────────────────────────────────

def test_login_wrong_password_returns_401(client):
    """
    Usuario existe pero contraseña no coincide → 401.
    Usamos un hash real para que check_password_hash falle con la contraseña enviada.
    """
    from werkzeug.security import generate_password_hash

    fake_user = {
        'id':            1,
        'name':          'ADMIN',
        'password_hash': generate_password_hash('password_correcta'),
        'password':      None,
        'role':          'admin',
        'isActive':      1,
        'local':         'El Mekatiadero',
        'location_id':   1,
    }
    cursor = make_mock_cursor(fetchone_result=fake_user)
    conn   = make_mock_connection(cursor)

    with patch('blueprints.auth.routes.get_db_connection', return_value=conn), \
         patch('blueprints.auth.routes.get_db_cursor', return_value=cursor):

        response = client.post(
            '/login',
            data=json.dumps({'nombre': 'ADMIN', 'password': 'password_incorrecta'}),
            content_type='application/json',
        )

    assert response.status_code == 401
    data = json.loads(response.data)
    assert data['valido'] is False


# ── Rutas protegidas sin sesión ───────────────────────────────────────────────

def test_local_without_session_redirects_to_login(client):
    """GET /local sin sesión → redirige a /login."""
    response = client.get('/local')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_package_without_session_redirects(client):
    """GET /package sin sesión → redirige a /login."""
    response = client.get('/package')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_sales_without_session_redirects(client):
    """GET /sales sin sesión → redirige a /login."""
    response = client.get('/sales')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


# ── GET /logout ───────────────────────────────────────────────────────────────

def test_logout_redirects_to_login(auth_client):
    """GET /logout con sesión activa → limpia sesión y redirige a /login."""
    response = auth_client.get('/logout')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_after_logout_session_is_cleared(app, auth_client):
    """Después del logout la sesión queda limpia."""
    auth_client.get('/logout')
    # Verificar que /local ya no es accesible
    response = auth_client.get('/local')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']
