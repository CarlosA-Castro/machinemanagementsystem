"""
conftest.py — Fixtures compartidas para la suite de tests.

Estrategia:
- La app se crea una sola vez por sesión de tests (scope='session').
- CSRF y rate limiting deshabilitados en tests.
- El heartbeat monitor no arranca (factory.py lo verifica con TESTING=True).
- La BD no se conecta: cada test que necesite BD mockeará get_db_connection
  en el namespace del blueprint correspondiente.
"""
import sys
import os

# Asegurar que el directorio raíz de la app esté en el path de Python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from factory import create_app


@pytest.fixture(scope='session')
def app():
    """App Flask configurada para tests — sin BD real, sin CSRF, sin rate limit."""
    application = create_app()
    application.config.update({
        'TESTING':          True,
        'WTF_CSRF_ENABLED': False,   # flask-wtf no valida token en tests
        'RATELIMIT_ENABLED': False,  # flask-limiter deshabilitado en tests
        'SECRET_KEY':       'test-secret-key-not-for-production',
    })
    return application


@pytest.fixture
def client(app):
    """Cliente HTTP de Flask para hacer requests en tests."""
    return app.test_client()


@pytest.fixture
def auth_client(app, client):
    """
    Cliente con sesión de admin activa.
    Usa session_transaction() para inyectar la sesión directamente,
    sin necesidad de pasar por el flujo real de login.
    """
    with client.session_transaction() as sess:
        sess['logged_in']           = True
        sess['user_id']             = 1
        sess['user_name']           = 'ADMIN_TEST'
        sess['user_role']           = 'admin'
        sess['user_local']          = 'El Mekatiadero'
        sess['active_location_id']  = None   # modo "todos los locales"
        sess['can_switch_location'] = True
    return client


# ── Helpers de mock DB ────────────────────────────────────────────────────────

def make_mock_cursor(fetchone_result=None, fetchall_result=None):
    """Construye un mock de cursor de BD con respuestas predefinidas."""
    from unittest.mock import MagicMock
    cursor = MagicMock()
    cursor.fetchone.return_value  = fetchone_result
    cursor.fetchall.return_value  = fetchall_result or []
    cursor.execute.return_value   = None
    cursor.close.return_value     = None
    cursor.rowcount               = 0
    return cursor


def make_mock_connection(cursor):
    """Construye un mock de conexión de BD que devuelve el cursor dado."""
    from unittest.mock import MagicMock
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.commit.return_value  = None
    conn.close.return_value   = None
    return conn
