"""
test_liquidaciones.py — Smoke tests del módulo de liquidaciones.

Cubre:
- Todos los endpoints protegidos devuelven 401/redirect sin sesión
- Con sesión activa, los endpoints responden (aunque BD esté mockeada)

Los endpoints de liquidaciones usan @require_login().
Sin sesión + header XHR → 401.
Sin sesión + request normal → 302 redirect a /login.
"""
import json
from unittest.mock import patch

from tests.conftest import make_mock_cursor, make_mock_connection

# Header que activa la respuesta 401 (en vez de redirect) en require_login
XHR = {'X-Requested-With': 'XMLHttpRequest'}


# ── Sin sesión — todos deben negar el acceso ──────────────────────────────────

def test_calcular_requires_login(client):
    """POST /api/liquidaciones/calcular sin sesión → 401."""
    response = client.post(
        '/api/liquidaciones/calcular',
        data=json.dumps({}),
        content_type='application/json',
        headers=XHR,
    )
    assert response.status_code == 401


def test_ventas_liquidadas_requires_login(client):
    """GET /api/ventas-liquidadas sin sesión → 401."""
    response = client.get('/api/ventas-liquidadas', headers=XHR)
    assert response.status_code == 401


def test_gastos_get_requires_login(client):
    """GET /api/liquidaciones/gastos sin sesión → 401."""
    response = client.get('/api/liquidaciones/gastos', headers=XHR)
    assert response.status_code == 401


def test_gastos_post_requires_login(client):
    """POST /api/liquidaciones/gastos sin sesión → 401."""
    response = client.post(
        '/api/liquidaciones/gastos',
        data=json.dumps({}),
        content_type='application/json',
        headers=XHR,
    )
    assert response.status_code == 401


def test_cerrar_requires_login(client):
    """POST /api/liquidaciones/cerrar sin sesión → 401."""
    response = client.post(
        '/api/liquidaciones/cerrar',
        data=json.dumps({}),
        content_type='application/json',
        headers=XHR,
    )
    assert response.status_code == 401


def test_historial_requires_login(client):
    """GET /api/liquidaciones/historial sin sesión → 401."""
    response = client.get('/api/liquidaciones/historial', headers=XHR)
    assert response.status_code == 401


def test_maquinas_catalogo_requires_login(client):
    """GET /api/liquidaciones/maquinas/catalogo sin sesión → 401."""
    response = client.get('/api/liquidaciones/maquinas/catalogo', headers=XHR)
    assert response.status_code == 401


# ── Con sesión activa — los endpoints deben responder ────────────────────────

def test_historial_with_session_responds(auth_client):
    """
    GET /api/liquidaciones/historial con sesión activa devuelve 200.
    Mockeamos la BD para devolver una lista vacía.
    """
    cursor = make_mock_cursor(fetchall_result=[])
    conn   = make_mock_connection(cursor)

    with patch('blueprints.liquidaciones.routes.get_db_connection', return_value=conn), \
         patch('blueprints.liquidaciones.routes.get_db_cursor', return_value=cursor):

        response = auth_client.get('/api/liquidaciones/historial', headers=XHR)

    # 200 o cualquier código de éxito (no 401/403)
    assert response.status_code < 400


def test_maquinas_catalogo_with_session_responds(auth_client):
    """
    GET /api/liquidaciones/maquinas/catalogo con sesión activa devuelve 200.
    """
    cursor = make_mock_cursor(fetchall_result=[])
    conn   = make_mock_connection(cursor)

    with patch('blueprints.liquidaciones.routes.get_db_connection', return_value=conn), \
         patch('blueprints.liquidaciones.routes.get_db_cursor', return_value=cursor):

        response = auth_client.get('/api/liquidaciones/maquinas/catalogo', headers=XHR)

    assert response.status_code < 400
